"""Контрольные точки внутри материала (tsk-998, П9).

Материал может нести 1–2 вопроса «проверь себя» прямо в тексте теории:
`materials.content.checkpoints = [{id, after_block, question, kind, options?,
answer, explanation?}]` (формат публикатора ContentBackbone; место вопроса в
HTML — пустая метка `<div data-cb-checkpoint="id">`).

Что здесь:
- проекция `content` для ученика: без `answer`/`explanation`, а при выключенном
  рубильнике `MATERIAL_CHECKPOINTS_ENABLED` — без `checkpoints` вообще;
- проверка ответа ученика и запись события `material_checkpoint` в
  `learning_events`.

Чего здесь НЕТ и быть не должно (решение постановки): ответ на точку не влияет
на прохождение материала (`student_material_progress`), статусы курса,
`task_results` и аналитику пробелов — это диагностика, а не оценка и не гейт.
Повторный ответ разрешён; перебор вариантов возможен, поэтому сигналом усвоения
служит ПЕРВАЯ попытка — она помечена в событии `first_attempt: true`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings

logger = logging.getLogger(__name__)

_settings = Settings()

#: Тип события в `learning_events`.
EVENT_TYPE = "material_checkpoint"
#: Потолок длины ответа ученика (ограничение постановки).
ANSWER_MAX_LEN = 200
#: Поля точки, которые ученику не отдаются.
_HIDDEN_FROM_STUDENT = ("answer", "explanation")

_SPACES_RE = re.compile(r"\s+")


def checkpoints_enabled() -> bool:
    """Включены ли контрольные точки (рубильник `MATERIAL_CHECKPOINTS_ENABLED`)."""
    return bool(getattr(_settings, "material_checkpoints_enabled", False))


def student_content(content: Any, *, enabled: Optional[bool] = None) -> Any:
    """Проекция `materials.content` для ученика.

    Без точек — тот же объект без изменений (материалы без П9 отдаются как
    раньше). С точками — копия: при включённом рубильнике у каждой точки сняты
    `answer` и `explanation`, при выключенном ключа `checkpoints` нет вовсе.
    Исходный словарь не меняется: он может быть атрибутом ORM-объекта, и правка
    на месте ушла бы в базу при ближайшем flush.
    """
    if not isinstance(content, dict) or "checkpoints" not in content:
        return content
    on = checkpoints_enabled() if enabled is None else enabled
    projected = {k: v for k, v in content.items() if k != "checkpoints"}
    if not on:
        return projected
    raw = content.get("checkpoints")
    projected["checkpoints"] = [
        {k: v for k, v in cp.items() if k not in _HIDDEN_FROM_STUDENT}
        for cp in (raw if isinstance(raw, list) else [])
        if isinstance(cp, dict)
    ]
    return projected


def find_checkpoint(content: Any, checkpoint_id: str) -> Optional[dict[str, Any]]:
    """Точка материала по id (из полного `content`, с ответом) или None."""
    if not isinstance(content, dict):
        return None
    for cp in content.get("checkpoints") or []:
        if isinstance(cp, dict) and str(cp.get("id")) == checkpoint_id:
            return cp
    return None


def normalize_short_answer(value: str) -> str:
    """Короткий ответ к виду для сверки: без регистра, «ё» как «е», пробелы схлопнуты.

    Конечная точка снимается («Список.» = «список»). Числа с запятой и точкой
    не сводятся: «0,5» и «0.5» — автор задаёт оба варианта, если нужны оба.
    """
    v = _SPACES_RE.sub(" ", str(value)).strip().casefold().replace("ё", "е")
    return v[:-1].rstrip() if v.endswith(".") else v


def is_correct(checkpoint: dict[str, Any], answer: str) -> bool:
    """Верен ли ответ ученика.

    `single` — id выбранного варианта совпадает с `answer` точки. `short` —
    нормализованный ответ совпадает с одним из допустимых.
    """
    kind = checkpoint.get("kind")
    expected = checkpoint.get("answer")
    if kind == "single":
        return isinstance(expected, str) and answer.strip() == expected
    if kind == "short":
        accepted = expected if isinstance(expected, list) else [expected]
        given = normalize_short_answer(answer)
        return any(
            isinstance(a, str) and given == normalize_short_answer(a) for a in accepted
        )
    return False


async def answer_checkpoint(
    db: AsyncSession,
    *,
    student_id: int,
    material_id: int,
    content: Any,
    checkpoint_id: str,
    answer: str,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Проверить ответ на точку и записать событие `material_checkpoint`.

    Гейты доступа к материалу (зачисление, оплата, активность) — забота
    вызывающего, здесь только рубильник и сама точка.

    Returns:
        (`{correct, explanation, first_attempt}`, None) или (None, код ошибки):
        `disabled` — рубильник выключен; `not_found` — у материала нет такой точки.
    """
    if not checkpoints_enabled():
        return None, "disabled"
    checkpoint = find_checkpoint(content, checkpoint_id)
    if checkpoint is None:
        return None, "not_found"

    answer = answer[:ANSWER_MAX_LEN]
    correct = is_correct(checkpoint, answer)

    # Первая попытка — сигнал усвоения (повтор после ответа «неверно» — уже
    # перебор). Блокировка по паре (ученик, материал) не даёт двум параллельным
    # первым ответам оба записаться как первые.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"{EVENT_TYPE}:{student_id}:{material_id}"},
    )
    prior = (
        await db.execute(
            text(
                """
                SELECT count(*) AS total
                FROM learning_events
                WHERE student_id = :sid
                  AND event_type = :et
                  AND payload->>'material_id' = :mid
                  AND payload->>'checkpoint_id' = :cid
                """
            ),
            {"sid": student_id, "et": EVENT_TYPE, "mid": str(material_id), "cid": checkpoint_id},
        )
    ).one()
    first_attempt = int(prior.total) == 0
    # tsk-1373: пояснение обычно содержит ответ. После первой ошибки его не
    # показываем — иначе «попробуй ещё раз» теряет смысл; показываем при верном
    # ответе и на любой не первой попытке (вторая ошибка, повтор после верного).
    reveal = correct or not first_attempt

    payload = {
        "material_id": material_id,
        "checkpoint_id": checkpoint_id,
        "correct": correct,
        "answer": answer,
        "first_attempt": first_attempt,
    }
    await db.execute(
        text(
            """
            INSERT INTO learning_events (student_id, event_type, payload, created_at)
            VALUES (:sid, :et, CAST(:payload AS jsonb), now())
            """
        ),
        {"sid": student_id, "et": EVENT_TYPE, "payload": json.dumps(payload, ensure_ascii=False)},
    )
    explanation = checkpoint.get("explanation")
    return {
        "correct": correct,
        "explanation": (
            explanation if reveal and isinstance(explanation, str) and explanation.strip() else None
        ),
        "first_attempt": first_attempt,
    }, None
