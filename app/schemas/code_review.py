# app/schemas/code_review.py
"""
Компактный вид машинной оценки кода для списков (tsk-302).

Полный отчёт (`task_results.code_review`) содержит замечания, обоснование
вердикта и разбор линтера — это уместно на экране проверки одной работы. Но в
ленте активности сотня событий, а в прогрессе ученика — десятки заданий: гонять
туда полный JSON значит раздувать ответ ради данных, которые всё равно не
поместятся в строку списка.

Поэтому списки получают три поля, из которых рисуется значок: готовность,
балл и признак «есть повод присмотреться». Захочет преподаватель подробностей —
откроет карточку задания, там отчёт целиком.

Ученику этот вид, как и полный отчёт, не показывается: обе поверхности
(лента, прогресс) доступны только преподавателю и методисту.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class CodeReviewQuality(BaseModel):
    """Чистота кода глазами модели."""

    model_config = ConfigDict(extra="allow")

    score: Optional[int] = Field(None, description="Чистота кода, 0–10", examples=[7])
    notes: Optional[list[str]] = Field(
        None, description="Замечания человеческим языком, без номенклатуры линтера"
    )


class CodeReviewAuthorship(BaseModel):
    """Признак ИИ-авторства. Эвристика и повод присмотреться, а не вывод."""

    model_config = ConfigDict(extra="allow")

    verdict: Optional[str] = Field(
        None,
        description="ai_likely | ambiguous | student_likely",
        examples=["student_likely"],
    )
    reasoning: Optional[str] = Field(None, description="Почему модель так решила")


class TextPasteSignal(BaseModel):
    """
    Один механический след вставки текста из чужого окна (tsk-646).

    Отличается от `CodeReviewAuthorship` природой: это не мнение модели о слоге,
    а факт о символах в тексте, который преподаватель может проверить глазами.
    Поэтому у каждого следа есть кусок текста — иначе признак снова превратился
    бы в «похоже на ИИ» без оснований, то есть в то, с чего задача началась.
    """

    model_config = ConfigDict(extra="allow")

    code: Optional[str] = Field(
        None,
        description="Код следа: math_render_residue | latex_markup | markdown_residue",
        examples=["math_render_residue"],
    )
    label: Optional[str] = Field(None, description="Объяснение человеческим языком")
    evidence: Optional[str] = Field(None, description="Кусок текста, где след найден")


class UnseenConstruct(BaseModel):
    """
    Одна конструкция, которой не было в пройденных этим учеником материалах (tsk-864).

    Как и `TextPasteSignal`, это ФАКТ, а не мнение модели: конструкция найдена в
    коде разбором синтаксиса, а её отсутствие — сверкой с текстом материалов,
    которые ученик отметил пройденными. Поэтому у пометки есть строка кода:
    преподаватель проверяет её глазами, не веря нам на слово.
    """

    model_config = ConfigDict(extra="allow")

    code: Optional[str] = Field(
        None, description="Код конструкции: fstring | dunder | comprehension | …",
        examples=["fstring"],
    )
    label: Optional[str] = Field(
        None, description="Название человеческим языком", examples=['f-строка (`f"…"`)']
    )
    evidence: Optional[str] = Field(
        None,
        description="Строка кода ученика, где конструкция встретилась",
        examples=['print(f"Привет, {name}!")'],
    )


class UnseenConstructs(BaseModel):
    """
    Итог сверки кода с пройденными материалами (tsk-864).

    Секции нет вовсе, когда сверять не с чем: язык не Python, либо у ученика нет
    ни одной отметки о пройденном материале. Пустой `items` при этом значит
    обратное — сверили, и всё найденное в коде ученику уже объясняли. Разница
    важна: тишина «проверено» и тишина «не проверялось» — разные вещи, и
    показывать их одинаково значит вводить преподавателя в заблуждение.
    """

    model_config = ConfigDict(extra="allow")

    items: Optional[list[UnseenConstruct]] = Field(
        None, description="Конструкции, которых не было в пройденном. Пустой список — все пройдены"
    )
    materials_seen: Optional[int] = Field(
        None,
        description="По скольким отмеченным пройденными материалам сверяли",
        examples=[31],
    )


class CodeReviewLintMessage(BaseModel):
    """Одно замечание линтера."""

    model_config = ConfigDict(extra="allow")

    symbol: Optional[str] = Field(None, examples=["magic-value-comparison"])
    message: Optional[str] = None
    line: Optional[int] = None


class CodeReviewPylint(BaseModel):
    model_config = ConfigDict(extra="allow")

    score: Optional[float] = Field(None, description="Оценка pylint, 0–10", examples=[8.75])
    messages: Optional[list[CodeReviewLintMessage]] = None


class CodeReviewComplexity(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    complexity: Optional[int] = None


class CodeReviewRadon(BaseModel):
    model_config = ConfigDict(extra="allow")

    complexity: Optional[list[CodeReviewComplexity]] = None


class RubricReviewItem(BaseModel):
    """Один пункт рубрики и что по нему видно в ответе ученика (tsk-658)."""

    model_config = ConfigDict(extra="allow")

    id: Optional[str] = Field(None, description="ID критерия из `text_answer.rubric`")
    title: Optional[str] = Field(None, description="Формулировка критерия")
    max_score: Optional[int] = Field(
        None, description="Вес критерия. Пусто у критериев без баллов"
    )
    met: Optional[str] = Field(
        None,
        description=(
            "yes — критерий выполнен и подтверждён местом в ответе; no — этого нет "
            "либо сказано неверно; unclear — по тексту решить нельзя"
        ),
        examples=["yes", "no", "unclear"],
    )
    evidence: Optional[str] = Field(
        None, description="Цитата из ответа ученика либо объяснение, почему пункт не засчитан"
    )


class RubricReview(BaseModel):
    """
    Раскладка развёрнутого ответа по рубрике задания (tsk-658).

    Это ОПОРА для преподавателя, а не решение: зачёт по таким работам ставит
    человек. Балл здесь предложенный и сложен нашим кодом из выполненных
    пунктов — модель его не считает и не видит (довод tsk-605: на арифметике
    без эталона она ошибается заметно чаще).
    """

    model_config = ConfigDict(extra="allow")

    items: Optional[list[RubricReviewItem]] = Field(
        None, description="Пункты рубрики в том же порядке, что у задания"
    )
    suggested_score: Optional[int] = Field(
        None,
        description=(
            "Сумма баллов за пункты со значением yes. Предложение, а не оценка. "
            "Пусто, если у критериев нет весов"
        ),
        examples=[4, None],
    )
    max_score: Optional[int] = Field(
        None, description="Потолок рубрики — чтобы предложенный балл читался («4 из 6»)"
    )
    summary: Optional[str] = Field(
        None, description="1–2 предложения: что в работе есть, чего не хватает"
    )
    error: Optional[str] = Field(
        None, description="Разбор не выполнен: модель недоступна либо ответ не разобран"
    )


class CodeReviewStatic(BaseModel):
    """Разбор линтера — только для Python; для прочих языков секции нет."""

    model_config = ConfigDict(extra="allow")

    pylint: Optional[CodeReviewPylint] = None
    radon: Optional[CodeReviewRadon] = None


class CodeReviewReport(BaseModel):
    """
    Полный отчёт машинной оценки — то, что лежит в ``task_results.code_review``.

    Схема описана явно, а не отдана «просто словарём», чтобы клиент получал
    типы, а не приведения: без неё в SPW приходилось в каждом месте писать
    ``as CodeReview``, и опечатка в имени поля не ловилась ничем.

    ``extra="allow"`` намеренно: отчёт пишет фоновый тик, и появление в нём
    нового раздела не должно молча обрезать данные на выдаче.
    """

    model_config = ConfigDict(extra="allow")

    status: Optional[str] = Field(None, examples=["done", "pending", "failed", "skipped"])
    kind: Optional[str] = Field(
        None,
        description=(
            "Что именно разобрано: `code` — программа (tsk-302), `text` — развёрнутый "
            "письменный ответ (tsk-646). Пусто у отчётов, созданных до появления "
            "текстовой ветки: они все про код"
        ),
        examples=["code", "text"],
    )
    language: Optional[str] = Field(None, examples=["Python", "C++ (Arduino)"])
    code_quality: Optional[CodeReviewQuality] = None
    ai_authorship: Optional[CodeReviewAuthorship] = None
    signals: Optional[list[TextPasteSignal]] = Field(
        None,
        description=(
            "Механические следы вставки в текстовой работе (tsk-646). Считаются "
            "регулярками, без модели, и остаются доступны, даже когда модель не "
            "ответила. Пустой список — следов нет; это не значит «писал сам»"
        ),
    )
    unseen_constructs: Optional[UnseenConstructs] = Field(
        None,
        description=(
            "tsk-864: конструкции из кода, которых нет ни в одном материале, "
            "пройденном этим учеником. Считается у нас из данных, без модели, и "
            "остаётся доступной, даже когда модель не ответила. Повод посмотреть "
            "работу, а не вывод о списывании: конструкцию могли узнать и сами"
        ),
    )
    rubric_review: Optional[RubricReview] = Field(
        None,
        description=(
            "Раскладка развёрнутого ответа по рубрике задания (tsk-658). Есть только "
            "у текстовых работ, у которых задание несёт критерии. Опора для "
            "преподавателя: зачёт по таким работам ставит человек"
        ),
    )
    static: Optional[CodeReviewStatic] = Field(
        None, description="Разбор линтера (pylint/radon) — только для Python"
    )
    degraded: Optional[bool] = Field(
        None, description="Модель была недоступна, в отчёте только статический анализ"
    )
    error: Optional[str] = None
    reason: Optional[str] = None
    backfill: Optional[bool] = Field(
        None, description="Работа попала в очередь пересчётом задним числом"
    )


class CodeReviewBadge(BaseModel):
    """Машинная оценка кода в объёме, достаточном для значка в списке."""

    status: str = Field(
        ...,
        description="pending — оценка готовится; done — готова; failed — не вышла; skipped — оценивать нечего",
        examples=["done", "pending"],
    )
    score: Optional[int] = Field(
        None,
        description="Чистота кода, 0–10. None, если оценка ещё не готова или не удалась",
        examples=[8, None],
    )
    ai_suspected: bool = Field(
        False,
        description=(
            "Есть повод присмотреться: модель сочла стиль похожим на ИИ либо в "
            "тексте нашлись механические следы вставки. Эвристика, а не вывод "
            "о списывании"
        ),
    )
    has_unseen_constructs: bool = Field(
        False,
        description=(
            "tsk-864: в работе есть конструкция, которой не было в пройденных этим "
            "учеником материалах. ОТДЕЛЬНОЕ поле, а не часть `ai_suspected`: это "
            "факт о материалах, а не подозрение на нейросеть, и склеив их в один "
            "флаг, список подписал бы ученику обвинение, которого признак не несёт"
        ),
    )
    degraded: bool = Field(
        False,
        description="Оценка неполная: модель была недоступна, есть только разбор линтера",
    )


def build_code_review_badge(raw: Optional[Dict[str, Any]]) -> Optional[CodeReviewBadge]:
    """
    Сворачивает полный отчёт из БД в компактный вид.

    Возвращает None, если оценки нет вовсе — тогда клиент просто не рисует значок.
    Отчёты старого формата (до этапа 3, без `status`) тоже дают None: разбирать
    два формата ради горстки старых записей — цена без выгоды.

    :param raw: содержимое `task_results.code_review` (JSONB) либо None.
    """
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    if not status:
        return None

    quality = raw.get("code_quality") or {}
    score = quality.get("score")
    if not isinstance(score, int):
        # У деградированного отчёта балл лежит в разборе линтера: он по той же
        # десятибалльной шкале, но дробный — округляем, значку хватит целого.
        lint_score = ((raw.get("static") or {}).get("pylint") or {}).get("score")
        # Округляем «половину вверх», а не встроенным round(): тот в Python
        # банковский (round(8.5) == 8), а на клиенте то же сворачивание делает
        # Math.round (8.5 -> 9). Разойдись правила — значок в карточке и в
        # ленте показывал бы у одной работы разные числа.
        score = int(lint_score + 0.5) if isinstance(lint_score, (int, float)) else None

    verdict = (raw.get("ai_authorship") or {}).get("verdict")
    # Механический след зажигает значок наравне с вердиктом модели (tsk-646).
    # Иначе работа, где следы вставки нашлись, а модель промолчала, выглядела
    # бы в ленте чистой — при том, что след как раз ПРОВЕРЯЕМ, а вердикт нет.
    signals = raw.get("signals")
    # tsk-864: конструкция из непройденной темы поднимает СВОЙ флаг. Решение
    # оператора 09.09 — показывать её и в списках, но подмешивать в
    # `ai_suspected` нельзя: подпись того значка говорит «работа похожа на
    # сделанную нейросетью», а признак утверждает совсем другое — что
    # конструкцию этому ученику не объясняли. Ученик мог узнать её сам, и
    # склейка превратила бы факт в обвинение ровно там, где вся задача про
    # обратное. Список получает два разных значка и две разные подписи.
    #
    # `isinstance`, а не `or {}`: значок строится для КАЖДОЙ строки ленты, и
    # один отчёт неожиданной формы уронил бы весь список целиком.
    unseen = raw.get("unseen_constructs")
    unseen_items = unseen.get("items") if isinstance(unseen, dict) else None
    # Список, а не «что-нибудь непустое»: строка «нет» в `items` тоже
    # правдива по `bool`, и значок зажёгся бы на пустом месте.
    has_unseen = isinstance(unseen_items, list) and len(unseen_items) > 0

    return CodeReviewBadge(
        status=str(status),
        score=score,
        ai_suspected=(verdict == "ai_likely" or bool(signals)),
        has_unseen_constructs=has_unseen,
        degraded=bool(raw.get("degraded")),
    )
