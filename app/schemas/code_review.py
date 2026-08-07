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
    language: Optional[str] = Field(None, examples=["Python", "C++ (Arduino)"])
    code_quality: Optional[CodeReviewQuality] = None
    ai_authorship: Optional[CodeReviewAuthorship] = None
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
            "Модель сочла стиль похожим на код от ИИ. Это эвристика и повод "
            "присмотреться, а не вывод о списывании"
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

    return CodeReviewBadge(
        status=str(status),
        score=score,
        ai_suspected=(verdict == "ai_likely"),
        degraded=bool(raw.get("degraded")),
    )
