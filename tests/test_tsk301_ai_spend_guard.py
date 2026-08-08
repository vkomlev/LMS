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

    capability: str
    #: Гейт стоит в этом же файле.
    gated_here: bool = False
    #: Гейт стоит выше по потоку — путь до места, где он реально вызывается.
    gated_upstream: Optional[str] = None
    #: Проводка выполнена (Фаза 3). Пока False, содержательной проверки гейта
    #: в файле нет — но сам факт существования точки уже под надзором.
    wired: bool = False

    def __post_init__(self) -> None:
        if not self.gated_here and not self.gated_upstream:
            raise AssertionError(
                "точка расхода обязана указать, где стоит её гейт"
            )


#: Реестр. Ключ — путь относительно корня репозитория.
AI_SPEND_POINTS: dict[str, SpendPoint] = {
    "app/api/v1/ai_tutor.py": SpendPoint(capability="ai_tutor", gated_here=True),
    "app/api/v1/attempts.py": SpendPoint(capability="code_review", gated_here=True),
    "app/services/code_review_service.py": SpendPoint(
        capability="code_review", gated_upstream="app/api/v1/attempts.py"
    ),
    # Фоновый тик сам модель не зовёт, но разбирает очередь пометок и тем
    # ЗАПУСКАЕТ расход. Найден стражем, а не глазами: в первой редакции реестра
    # его не было — ровно тот пропуск, ради которого страж и написан.
    "app/services/code_review_cron_service.py": SpendPoint(
        capability="code_review", gated_upstream="app/api/v1/attempts.py"
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
    sorted(p for p, point in AI_SPEND_POINTS.items() if point.wired),
)
def test_wired_point_calls_the_door(rel_path: str) -> None:
    """Проведённая точка действительно зовёт единую дверь, а не свою проверку.

    Пока проводка не сделана (Фаза 3), параметризация пуста и тест не запускается.
    Как только `wired=True` — проверка становится содержательной.
    """
    source = (APP_ROOT.parent / rel_path).read_text(encoding="utf-8")
    assert "entitlements_service" in source, (
        f"{rel_path}: точка помечена проведённой, но единую дверь не зовёт"
    )


def test_capabilities_are_known() -> None:
    """Возможности в реестре совпадают с теми, что умеет дверь."""
    from app.services.entitlements_service import GATED_CAPABILITIES

    unknown = {p.capability for p in AI_SPEND_POINTS.values()} - set(GATED_CAPABILITIES)
    assert not unknown, f"в реестре неизвестные возможности: {sorted(unknown)}"
