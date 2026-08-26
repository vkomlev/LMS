"""tsk-301: сторожевой тест точек расхода ИИ (пробел П13 контракта).

**Зачем нужен именно механический страж.** В tsk-572 правило применили в двух
клиентах из трёх — SSE-буферизацию поставили в вебе и в боте, забыли в третьем
месте, и ученик получил порченый текст. Ошибка этого класса не падает ни тестом,
ни исключением: код работает, просто в одном месте правило не действует.
Дисциплина «не забыть» уже проверена и уже проиграла, поэтому список точек
расхода сверяется автоматически.

Страж находит точки **сканированием исходников**, а не по памяти: любой новый
вызов модели или новая постановка работы в очередь оценки обязаны появиться в
реестре `AI_SPEND_POINTS` вместе с указанием, какая возможность их гейтит.
Забыли внести — тест краснеет и называет файл.

Реестр допускает запись «гейт стоит выше по потоку»: так устроена фоновая
оценка кода — она сама зовёт модель, но работа попадает к ней только через уже
прогейченный вход в `attempts.py`. Ссылка на этот вход обязательна, иначе
формулировка «где-то выше проверяют» стала бы способом обойти сам страж.

Третий вид записи — СИСТЕМНЫЙ расход, который порождает не ученик, а сама
служба (tsk-678: еженедельная проверка боевых цепочек моделей). Двери прав тут
нет по существу: гейтить нечего и некого, — но и расход не должен оставаться
без выключателя. Такая точка называет свой рубильник, и страж проверяет, что он
действительно есть в конфиге, в `.env.example` и читается в самом файле.
Рубильник намеренно НЕ разрешён точке с возможностью ученика: иначе он стал бы
дешёвым способом обойти дверь прав.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

#: Модуль общего LLM-транспорта. Его собственные файлы — реализация, а не точка
#: расхода: гейт внутри транспорта стоял бы ПОСЛЕ решения потратить деньги.
_TRANSPORT_DIR = APP_ROOT / "services" / "llm"

#: Вызов модели: `stream(...)` / `complete(...)` из общего клиента.
_LLM_CALL_RX = re.compile(r"\b(?:await\s+|async\s+for\s+\w+\s+in\s+)?(stream|complete)\s*\(")
#: Импорт из транспорта — и однострочный, и многострочный в скобках. Первая
#: редакция стража читала только однострочный и потому не видела
#: `code_review_service` (там импорт в скобках) — страж, который не видит
#: половину точек, хуже отсутствующего: он создаёт ложное спокойствие.
_LLM_IMPORT_RX = re.compile(
    r"from\s+app\.services\.llm\s+import\s+(?:\(([^)]*)\)|([^\n(]+))", re.DOTALL
)

#: Постановка работы в очередь оценки кода — расход, отложенный во времени.
#: Гейт обязан стоять ДО неё: иначе обещание Demo «токены не расходуем»
#: нарушается молча, работа уже помечена и тик её заберёт.
_ENQUEUE_RX = re.compile(r"""["']status["']\s*:\s*["']pending["']""")


@dataclass(frozen=True)
class SpendPoint:
    """Точка расхода и то, чем она гейтится."""

    #: Возможность ученика из `GATED_CAPABILITIES`. `None` — расход СИСТЕМНЫЙ:
    #: его порождает не ученик, а сама служба, и дверь прав тут не при чём.
    capability: Optional[str] = None
    #: Гейт стоит в этом же файле.
    gated_here: bool = False
    #: Гейт стоит выше по потоку — путь до места, где он реально вызывается.
    gated_upstream: Optional[str] = None
    #: Гейт — рубильник оператора: имя переменной окружения, которой расход
    #: включается и выключается. Только для системного расхода (tsk-678).
    gated_by_toggle: Optional[str] = None
    #: Проводка выполнена (Фаза 3). Пока False, содержательной проверки гейта
    #: в файле нет — но сам факт существования точки уже под надзором.
    wired: bool = False

    def __post_init__(self) -> None:
        if not self.gated_here and not self.gated_upstream and not self.gated_by_toggle:
            raise AssertionError(
                "точка расхода обязана указать, где стоит её гейт"
            )
        # Рубильник закрывает ТОЛЬКО системный расход. Иначе он стал бы вторым,
        # более дешёвым способом «загейтить» ученическую возможность мимо двери
        # прав: тариф её перечисляет, а на деле она у всех, пока переменная
        # включена. Это ровно та дыра, ради которой страж и написан.
        if self.gated_by_toggle and self.capability:
            raise AssertionError(
                "рубильник не заменяет дверь прав: у точки с возможностью "
                f"{self.capability!r} гейт обязан стоять здесь или выше по потоку"
            )
        if not self.capability and not self.gated_by_toggle:
            raise AssertionError(
                "точка без возможности ученика обязана быть системной и назвать "
                "свой рубильник"
            )


#: Реестр. Ключ — путь относительно корня репозитория.
AI_SPEND_POINTS: dict[str, SpendPoint] = {
    "app/api/v1/ai_tutor.py": SpendPoint(
        capability="ai_tutor", gated_here=True, wired=True
    ),
    "app/api/v1/attempts.py": SpendPoint(
        capability="code_review", gated_here=True, wired=True
    ),
    "app/services/code_review_service.py": SpendPoint(
        capability="code_review", gated_upstream="app/api/v1/attempts.py", wired=True
    ),
    # Фоновый тик сам модель не зовёт, но разбирает очередь пометок и тем
    # ЗАПУСКАЕТ расход. Найден стражем, а не глазами: в первой редакции реестра
    # его не было — ровно тот пропуск, ради которого страж и написан.
    "app/services/code_review_cron_service.py": SpendPoint(
        capability="code_review", gated_upstream="app/api/v1/attempts.py", wired=True
    ),
    # tsk-646: разбор развёрнутых текстовых работ. Гейт — тот же самый и в том
    # же месте: работа попадает сюда только через прогейченный вход в
    # `attempts.py`. Возможность намеренно НЕ заведена новая — иначе у тарифа
    # появилось бы право, которого никто не покупал, а расход остался бы тем же.
    "app/services/text_authorship_service.py": SpendPoint(
        capability="code_review", gated_upstream="app/api/v1/attempts.py", wired=True
    ),
    # tsk-658: раскладка развёрнутого ответа по рубрике задания. Гейт тот же и
    # там же — работа доходит сюда только через прогейченный вход `attempts.py`,
    # и новой возможности намеренно не заводится по тому же доводу, что в
    # tsk-646.
    #
    # Что ЗДЕСЬ ново и о чём нельзя молчать: на текстовой работе с критериями
    # это ВТОРОЙ вызов модели вместо одного. Совмещать его с признаком авторства
    # нельзя — оси обязаны считаться независимо (tsk-646), — поэтому расход на
    # такую сдачу примерно удваивается. Порог тот же, что у соседей: работы
    # короче `MIN_TEXT_CHARS` и задания без критериев к модели не идут вовсе.
    "app/services/rubric_review_service.py": SpendPoint(
        capability="code_review", gated_upstream="app/api/v1/attempts.py", wired=True
    ),
    # tsk-678: еженедельная проверка боевых цепочек моделей. ПЕРВЫЙ системный
    # расход в реестре, и потому единственный с рубильником вместо двери прав.
    #
    # Возможности намеренно нет, и это не пропуск. Расход порождает не ученик, а
    # планировщик: работа не приходит сюда ни из `attempts.py`, ни откуда-либо
    # ещё — образцы синтетические, лежат в самом файле (`PROBE_WORKS`). Записать
    # сюда `gated_upstream="app/api/v1/attempts.py"` по образцу соседей было бы
    # неправдой в реестре — а «где-то выше проверяют» без реальной ссылки и есть
    # тот обход, от которого страж заведён.
    #
    # Гейт настоящий: `LLM_CHAIN_CHECK_ENABLED` читается в `start_scheduler()`,
    # выключенный — планировщик не поднимается вовсе. Умолчание `true`, и это
    # осознанно: расход ограничен сверху самой природой прохода — восемь вызовов
    # в неделю на процесс, порядка полуцента, без зависимости от числа учеников.
    "app/services/llm_chain_check_cron_service.py": SpendPoint(
        gated_by_toggle="LLM_CHAIN_CHECK_ENABLED", wired=True
    ),
}


def _rel(path: Path) -> str:
    return path.relative_to(APP_ROOT.parent).as_posix()


def _discover() -> set[str]:
    """Найти все точки расхода ИИ в `app/` сканированием исходников."""
    found: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        if _TRANSPORT_DIR in path.parents or path == _TRANSPORT_DIR:
            continue
        source = path.read_text(encoding="utf-8")

        imported = set()
        for match in _LLM_IMPORT_RX.finditer(source):
            names = match.group(1) or match.group(2) or ""
            imported.update(name.strip() for name in names.split(","))
        if {"stream", "complete"} & imported and _LLM_CALL_RX.search(source):
            found.add(_rel(path))

        if _ENQUEUE_RX.search(source) and "code_review" in source:
            found.add(_rel(path))
    return found


def test_no_unregistered_ai_spend_point() -> None:
    """Каждая точка расхода ИИ значится в реестре с указанием своего гейта.

    Это главная проверка: она ловит НОВУЮ точку, добавленную без гейта, —
    ровно тот класс, из-за которого в tsk-572 правило применили не везде.
    """
    unregistered = _discover() - set(AI_SPEND_POINTS)
    assert not unregistered, (
        "новые точки расхода ИИ без записи в реестре: "
        + ", ".join(sorted(unregistered))
        + ". Внесите их в AI_SPEND_POINTS и укажите, какая возможность их гейтит."
    )


def test_registry_has_no_phantom_points() -> None:
    """В реестре нет записей о точках, которых больше нет.

    Мёртвая запись опаснее отсутствующей: она создаёт впечатление, что место
    под надзором, и прячет за собой пустоту.
    """
    phantom = set(AI_SPEND_POINTS) - _discover()
    assert not phantom, (
        "в реестре числятся несуществующие точки расхода: " + ", ".join(sorted(phantom))
    )


@pytest.mark.parametrize(
    "rel_path",
    sorted(p for p, point in AI_SPEND_POINTS.items() if point.gated_upstream),
)
def test_upstream_gate_target_exists(rel_path: str) -> None:
    """Ссылка «гейт выше по потоку» ведёт в реальный зарегистрированный файл."""
    upstream = AI_SPEND_POINTS[rel_path].gated_upstream
    assert upstream in AI_SPEND_POINTS, (
        f"{rel_path}: ссылка на вышестоящий гейт {upstream!r} не найдена в реестре"
    )
    assert AI_SPEND_POINTS[upstream].gated_here, (
        f"{rel_path}: {upstream} сам не гейтит — цепочка «выше по потоку» разорвана"
    )


@pytest.mark.parametrize(
    "rel_path",
    sorted(
        p for p, point in AI_SPEND_POINTS.items() if point.wired and point.gated_here
    ),
)
def test_wired_point_calls_the_door(rel_path: str) -> None:
    """Проведённая точка действительно зовёт единую дверь, а не свою проверку.

    Только для точек с гейтом на месте: те, что гейтятся выше по потоку, дверь
    сами не зовут — за них это проверяет `test_upstream_gate_target_exists`.
    """
    source = (APP_ROOT.parent / rel_path).read_text(encoding="utf-8")
    assert "entitlements_service" in source, (
        f"{rel_path}: точка помечена проведённой, но единую дверь не зовёт"
    )


@pytest.mark.parametrize(
    "rel_path",
    sorted(p for p, point in AI_SPEND_POINTS.items() if point.wired and point.gated_here),
)
def test_gate_goes_through_should_block(rel_path: str) -> None:
    """Решение применяется через `should_block`, а не чтением `.allowed`.

    Режимы выката и журнал наблюдения живут именно в `should_block`. Точка,
    которая сверится с `decision.allowed` напрямую, обойдёт и `shadow`, и
    `guests`: фаза наблюдения окажется пустой, а понять это будет не по чему —
    отказов нет, значит «всё хорошо».
    """
    source = (APP_ROOT.parent / rel_path).read_text(encoding="utf-8")
    assert "should_block" in source, (
        f"{rel_path}: решение применяется мимо should_block — режимы выката не сработают"
    )
    assert ".allowed" not in source, (
        f"{rel_path}: прямое чтение decision.allowed обходит режимы выката; "
        f"используйте should_block"
    )


@pytest.mark.parametrize(
    "rel_path",
    sorted(p for p, point in AI_SPEND_POINTS.items() if point.gated_by_toggle),
)
def test_toggle_gate_is_really_a_gate(rel_path: str) -> None:
    """Рубильник назван в реестре — значит, он существует и его правда читают.

    Иначе слово «рубильник» стало бы декларацией: точку внесли, тест позеленел,
    а выключить расход нечем. Проверяем три вещи разом — что настройка объявлена
    в конфиге, что оператор может её найти (`.env.example`) и что сам файл
    расхода к ней обращается. Настройка, до которой оператор не догадается, —
    это половина гейта.
    """
    toggle = AI_SPEND_POINTS[rel_path].gated_by_toggle
    assert toggle is not None
    repo_root = APP_ROOT.parent

    config = (APP_ROOT / "core" / "config.py").read_text(encoding="utf-8")
    assert f'"{toggle}"' in config, f"{rel_path}: {toggle} не объявлен в config.py"

    env_example = (repo_root / ".env.example").read_text(encoding="utf-8")
    assert f"{toggle}=" in env_example, (
        f"{rel_path}: {toggle} не описан в .env.example — оператор о нём не узнает"
    )

    source = (repo_root / rel_path).read_text(encoding="utf-8")
    assert toggle.lower() in source, (
        f"{rel_path}: настройка {toggle} не читается — расход не выключить"
    )


@pytest.mark.parametrize("rel_path", sorted(AI_SPEND_POINTS))
def test_every_point_is_wired(rel_path: str) -> None:
    """Все точки проведены. Приёмочный критерий Фазы 3.

    Отдельным тестом, а не полем в реестре: `wired=False` — это состояние
    «ещё не сделано», и оно обязано быть видимым как красный тест, а не как
    тихо пропущенная параметризация.
    """
    assert AI_SPEND_POINTS[rel_path].wired, (
        f"{rel_path}: точка расхода не проведена через дверь прав"
    )


# ─────────── Точки принуждения, не связанные с расходом токенов ─────────────
#
# Пробел П13 говорит про «одну функцию на 3 точки», а расход ИИ — только две из
# них. Третья, эскалация преподавателю, тратит не токены, а время человека, и
# сканером вызовов модели она не ловится. Без отдельного надзора здесь могла бы
# появиться вторая дорога к преподавателю мимо тарифа — и заметить это было бы
# нечем.

#: Создание РУЧНОЙ заявки преподавателю. Авто-заявка `blocked_limit` намеренно
#: не в списке: её создаёт система, и гейтить её нельзя.
_MANUAL_HELP_RX = re.compile(r"\bget_or_create_help_request\s*\(")

ENFORCEMENT_POINTS: dict[str, str] = {
    "app/api/v1/learning.py": "teacher_escalation",
}


def _discover_manual_help() -> set[str]:
    found: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        # Объявление функции — не её вызов.
        if "def get_or_create_help_request" in source:
            continue
        if _MANUAL_HELP_RX.search(source):
            found.add(_rel(path))
    return found


def test_no_ungated_manual_help_path() -> None:
    """Любая дорога к ручной заявке преподавателю значится в реестре."""
    unregistered = _discover_manual_help() - set(ENFORCEMENT_POINTS)
    assert not unregistered, (
        "ручная заявка преподавателю создаётся мимо надзора: "
        + ", ".join(sorted(unregistered))
    )


@pytest.mark.parametrize("rel_path", sorted(ENFORCEMENT_POINTS))
def test_enforcement_point_uses_the_door(rel_path: str) -> None:
    source = (APP_ROOT.parent / rel_path).read_text(encoding="utf-8")
    assert "entitlements_service" in source, f"{rel_path}: дверь прав не зовётся"
    assert "should_block" in source, (
        f"{rel_path}: решение применяется мимо should_block — режимы выката не сработают"
    )


def test_capabilities_are_known() -> None:
    """Возможности в реестре совпадают с теми, что умеет дверь."""
    from app.services.entitlements_service import GATED_CAPABILITIES

    # Системные точки возможности не объявляют — у них рубильник, и его честность
    # проверяет `test_toggle_gate_is_really_a_gate`.
    declared = {p.capability for p in AI_SPEND_POINTS.values() if p.capability} | set(
        ENFORCEMENT_POINTS.values()
    )
    unknown = declared - set(GATED_CAPABILITIES)
    assert not unknown, f"в реестре неизвестные возможности: {sorted(unknown)}"


def test_all_gated_capabilities_have_a_point() -> None:
    """У каждой возможности, которую умеет дверь, есть хотя бы одна точка.

    Возможность без точки принуждения — обещание, которое никто не выполняет:
    тариф её перечисляет, а на деле она доступна всем.
    """
    from app.services.entitlements_service import GATED_CAPABILITIES

    covered = {p.capability for p in AI_SPEND_POINTS.values() if p.capability} | set(
        ENFORCEMENT_POINTS.values()
    )
    missing = set(GATED_CAPABILITIES) - covered
    assert not missing, f"возможности без точки принуждения: {sorted(missing)}"
