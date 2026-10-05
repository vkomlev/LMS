"""Черновики текстовых подсказок из ответов преподавателей (tsk-1220).

**Зачем.** За год преподаватели ответили на заявки помощи 158 раз текстом по 122
заданиям (прод, 05.10). Объяснение, которое помогло одному ученику, обычно
помогает и следующему, но остаётся в переписке. Здесь оно превращается в
подсказку к заданию.

**Почему не напрямую.** Живые ответы — это часто готовый код ученика с
исправленной строкой или прямой ответ («убери class, введи 5 int»). Ученику
в виде подсказки такое отдавать нельзя (tsk-855: подсказка называет приём, а
не ответ; tsk-1180: без готового кода решения). Поэтому цепочка такая:
модель обобщает приём → линтер ищет утечку ответа → человек вычитывает →
только после подтверждения текст ДОПИСЫВАЕТСЯ в `hints_text`.

**Повторная генерация.** Черновик помнит, из каких ответов собран
(`source_reply_ids`). Задание попадает в кандидаты снова только при появлении
ответа, которого нет ни в одном черновике — включая пропущенные моделью и
заблокированные линтером. Отдельного файла «с прошлого прогона» не нужно.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_hint_drafts import TaskHintDrafts
from app.models.tasks import Tasks
from app.services.grading_criteria_draft import clean_stem
from app.services.llm import client as llm_client
from app.services.llm.contracts import Budget, LLMMessage

logger = logging.getLogger(__name__)

LLM_PURPOSE = "hint_draft"

#: Короче — это «ок», «смотри выше», «исправил»: обобщать нечего.
MIN_REPLY_LENGTH = 40

#: Подсказка длиннее — уже разбор, а не подсказка.
HINT_MAX_LENGTH = 600

#: Сколько ответов по одному заданию отдаём модели.
REPLIES_LIMIT = 8
REPLY_LIMIT_CHARS = 1500

#: Ответы, которые подсказкой не станут: любая ссылка (Телемост, видео, диск —
#: тип `reply_kind` размечен догадкой и пропускает ссылки, вставленные иначе),
#: отсылка к видео без ссылки и служебные тестовые ответы живых прогонов
#: («tsk-348 follow-up», «можно игнорировать») — на проде такие есть.
EXCLUDE_REPLY_RE = (
    r"(https?://|telemost|tsk-[0-9]+|можно игнорировать|живой тест"
    r"|(смотри|посмотри|см\.)\s+(видео|разбор|запись))"
)

_ELIGIBLE_REPLIES_SQL = f"""
    SELECT r.id AS reply_id, h.task_id, r.body, h.message AS question, r.created_at
      FROM help_request_replies r
      JOIN help_requests h ON h.id = r.request_id
      JOIN tasks t ON t.id = h.task_id
     WHERE t.is_active
       AND r.reply_kind = 'text'
       AND length(btrim(r.body)) >= {MIN_REPLY_LENGTH}
       AND r.body !~* :exclude
"""


class HintDraftError(RuntimeError):
    """Черновик составить не удалось."""


@dataclass(frozen=True)
class SourceReply:
    """Ответ преподавателя, из которого собирается подсказка."""

    reply_id: int
    question: str
    body: str


@dataclass
class GeneratedHint:
    """Итог генерации: подсказка либо обоснованный пропуск."""

    task_id: int
    source_reply_ids: list[int]
    skip: bool
    text: Optional[str]
    reason: Optional[str]
    model: str
    lint_flags: list[str] = field(default_factory=list)
    accepted_answers: list[str] = field(default_factory=list)


# ── отбор ──────────────────────────────────────────────────────────────────


async def candidates(db: AsyncSession, *, limit: int = 50) -> list[dict[str, Any]]:
    """Задания, у которых есть подходящие ответы, ещё не вошедшие ни в один черновик.

    :param db: сессия.
    :param limit: сколько заданий вернуть.
    :returns: `[{task_id, new_reply_ids, replies_total}]`, сначала задания с
        большим числом новых ответов.
    """
    rows = (
        await db.execute(
            text(
                f"""
                WITH e AS ({_ELIGIBLE_REPLIES_SQL})
                SELECT e.task_id,
                       array_agg(e.reply_id ORDER BY e.reply_id) FILTER (
                         WHERE NOT EXISTS (
                           SELECT 1 FROM task_hint_drafts d
                            WHERE d.task_id = e.task_id AND e.reply_id = ANY(d.source_reply_ids)
                         )
                       ) AS new_ids,
                       count(*) AS total
                  FROM e
                 GROUP BY e.task_id
                """
            ),
            {"exclude": EXCLUDE_REPLY_RE},
        )
    ).all()
    out = [
        {"task_id": r.task_id, "new_reply_ids": list(r.new_ids), "replies_total": int(r.total)}
        for r in rows
        if r.new_ids
    ]
    out.sort(key=lambda x: (-len(x["new_reply_ids"]), x["task_id"]))
    return out[:limit]


async def source_replies(db: AsyncSession, task_id: int) -> list[SourceReply]:
    """Все подходящие ответы по заданию — модель видит их вместе.

    Вместе, а не только новые: приём, который преподаватели объясняли трижды,
    и есть то, что стоит вынести в подсказку.
    """
    rows = (
        await db.execute(
            text(_ELIGIBLE_REPLIES_SQL + " AND h.task_id = :task_id ORDER BY r.created_at DESC"),
            {"exclude": EXCLUDE_REPLY_RE, "task_id": task_id},
        )
    ).all()
    return [
        SourceReply(reply_id=int(r.reply_id), question=(r.question or "").strip(), body=r.body.strip())
        for r in rows
    ]


# ── генерация ──────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """Ты методист онлайн-школы программирования и информатики.
Тебе дают задание и ответы преподавателей ученикам, которые просили помощи с этим
заданием. Твоя работа — понять, какой ПРИЁМ или ход рассуждения помог ученикам, и
записать его как короткую подсказку для следующего ученика.

Подсказку читает ученик, который ещё НЕ решил задание. Она должна подтолкнуть,
а не решить за него.

Жёсткие запреты (нарушил хоть один — подсказка негодна):
1. Нельзя писать ответ задания, итоговое число, итоговую строку или их часть.
2. Нельзя писать код решения или исправленную программу ученика — ни целиком,
   ни строкой. Можно назвать функцию или конструкцию словами («округли функцией
   round с нужным числом знаков»), но не писать её с аргументами из задания.
3. Нельзя упоминать конкретного ученика, его код, его ошибку («у тебя return
   внутри цикла»). Перепиши в общем виде: «проверь, не стоит ли return внутри
   цикла — тогда функция завершится на первом шаге».
4. Не повторяй уже существующие подсказки задания. Если они есть, твоя
   подсказка — следующая ступень, «если не помогло»: глубже или с другой стороны.

Когда подсказку писать НЕ надо (верни skip=true и причину):
- ответы преподавателей — только готовое решение или исправленный код, и общий
  приём из них не выделить;
- ответы про оформление конкретной сдачи, а не про решение (формат поля ответа
  тоже можно превратить в подсказку, если ошибку совершали несколько учеников);
- приём уже полностью сказан в существующих подсказках.

Форма: 1–3 предложения, на «ты», простыми словами, тон как у существующих
подсказок. Без приветствий, без «преподаватель советует». Простой текст:
без LaTeX ($...$), без markdown и без обратных кавычек.

Ответь ТОЛЬКО JSON-объектом:
{"skip": false, "hint": "текст подсказки", "reason": null}
или
{"skip": true, "hint": null, "reason": "почему подсказки нет"}
"""


def _existing_hints(content: dict[str, Any]) -> list[str]:
    """Текущие текстовые подсказки задания."""
    raw = content.get("hints_text")
    return [h for h in raw if isinstance(h, str) and h.strip()] if isinstance(raw, list) else []


def build_messages(
    *, content: dict[str, Any], replies: list[SourceReply]
) -> list[LLMMessage]:
    """Собрать промпт. Отдельно, чтобы проверить тестом."""
    hints = _existing_hints(content)
    hints_block = "\n".join(f"- {h}" for h in hints) if hints else "(подсказок нет)"
    replies_block = "\n\n".join(
        f"Ответ {i}.\nВопрос ученика: {(r.question or '—')[:400]}\n"
        f"Ответ преподавателя: {r.body[:REPLY_LIMIT_CHARS]}"
        for i, r in enumerate(replies[:REPLIES_LIMIT], start=1)
    )
    user = (
        f"Тип задания: {content.get('type') or '—'}\n"
        f"Название: {content.get('title') or '—'}\n\n"
        f"Условие:\n{clean_stem(content.get('stem'))}\n\n"
        f"Существующие подсказки:\n{hints_block}\n\n"
        f"Ответы преподавателей:\n{replies_block}\n"
    )
    return [LLMMessage(role="system", content=_SYSTEM_PROMPT), LLMMessage(role="user", content=user)]


def _parse(text_: str) -> dict[str, Any]:
    """Разобрать JSON ответа модели, терпя обёртку ```json."""
    try:
        payload = json.loads(text_)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text_, re.S)
        if not match:
            raise HintDraftError("модель вернула не JSON")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise HintDraftError(f"ответ модели не разбирается: {exc}") from exc
    if not isinstance(payload, dict):
        raise HintDraftError("модель вернула не объект")
    return payload


def accepted_answers(solution_rules: Any) -> list[str]:
    """Эталонные ответы задания (короткий ответ) — для проверки утечки."""
    if not isinstance(solution_rules, dict):
        return []
    sa = solution_rules.get("short_answer")
    items = sa.get("accepted_answers") if isinstance(sa, dict) else None
    out: list[str] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, (str, int, float)) and str(value).strip():
            out.append(str(value).strip())
    return out


_CODE_LINE_RE = re.compile(
    r"(```|^\s*(def|for|while|if|elif|import|from|return|class)\b.*[:(]"
    r"|^\s*\w+\s*=\s*\S|\b(print|input)\s*\([^)\s]|=\s*[A-ZА-Я]{2,}\()",
    re.M,
)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def local_lint(hint: str, answers: list[str]) -> list[str]:
    """Грубые проверки утечки на стороне LMS.

    Тонкую проверку по словоформам делает content-lint `hint-leaks-answer` в
    ContentBackbone — он обязательный фильтр перед очередью. Здесь то, что
    дёшево поймать сразу: код в подсказке и эталон целиком.

    :returns: список меток; пустой — нарушений не найдено.
    """
    flags: list[str] = []
    if _CODE_LINE_RE.search(hint):
        flags.append("code-in-hint")
    norm_hint = _norm(hint)
    for ans in answers:
        a = _norm(ans)
        if not a:
            continue
        # Короткий эталон («5», «да») — только отдельным словом, иначе «5»
        # найдётся в любом «1.5».
        if len(a) <= 3:
            if re.search(rf"(?<![\w.,]){re.escape(a)}(?![\w.,])", norm_hint):
                flags.append("answer-in-hint")
                break
        elif a in norm_hint:
            flags.append("answer-in-hint")
            break
    if len(hint) > HINT_MAX_LENGTH:
        flags.append("too-long")
    return flags


async def generate(db: AsyncSession, task_id: int, *, model: Optional[str] = None) -> GeneratedHint:
    """Составить черновик подсказки по одному заданию. В базу не пишет.

    :raises HintDraftError: нет задания, нет подходящих ответов, сбой модели.
    """
    task = await db.scalar(select(Tasks).where(Tasks.id == task_id))
    if task is None:
        raise HintDraftError("задание не найдено")
    replies = await source_replies(db, task_id)
    if not replies:
        raise HintDraftError("у задания нет текстовых ответов преподавателей, пригодных для подсказки")
    content = task.task_content if isinstance(task.task_content, dict) else {}
    answers = accepted_answers(task.solution_rules)

    try:
        result = await llm_client.complete(
            build_messages(content=content, replies=replies),
            model=model,
            temperature=0.0,
            max_tokens=700,
            purpose=LLM_PURPOSE,
            budget=Budget.BATCH,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 — таксономия ошибок клиента шире
        logger.warning("tsk-1220: подсказка для задания %s не составлена: %s", task_id, exc)
        raise HintDraftError(f"вызов модели не удался: {exc}") from exc

    payload = _parse(result.text)
    ids = sorted(r.reply_id for r in replies)
    hint_raw = payload.get("hint")
    hint = " ".join(hint_raw.split()) if isinstance(hint_raw, str) else ""
    reason_raw = payload.get("reason")
    reason = reason_raw.strip() if isinstance(reason_raw, str) and reason_raw.strip() else None
    if payload.get("skip") is True or not hint:
        return GeneratedHint(
            task_id=task_id, source_reply_ids=ids, skip=True, text=None,
            reason=reason or "модель не дала подсказки", model=result.model,
            accepted_answers=answers,
        )
    return GeneratedHint(
        task_id=task_id, source_reply_ids=ids, skip=False, text=hint, reason=None,
        model=result.model, lint_flags=local_lint(hint, answers), accepted_answers=answers,
    )


# ── запись и вычитка ───────────────────────────────────────────────────────


async def store(
    db: AsyncSession,
    *,
    task_id: int,
    status: str,
    text_: Optional[str],
    source_reply_ids: list[int],
    model: Optional[str],
    note: Optional[str],
    lint_flags: list[str],
) -> TaskHintDrafts:
    """Записать итог генерации: черновик в очередь, пропуск или блокировку.

    :raises HintDraftError: статус вне `draft|skipped|blocked`, пустой текст у
        черновика.
    """
    if status not in ("draft", "skipped", "blocked"):
        raise HintDraftError("записать можно только draft, skipped или blocked")
    clean = " ".join((text_ or "").split()) or None
    if status == "draft" and not clean:
        raise HintDraftError("у черновика пустой текст")
    if status == "draft" and lint_flags:
        # Обязательный фильтр до очереди: с метками утечки черновик в очередь не идёт.
        raise HintDraftError(f"черновик с метками линтера в очередь не ставится: {lint_flags}")
    row = TaskHintDrafts(
        task_id=task_id,
        text=clean,
        source_reply_ids=sorted(set(source_reply_ids)),
        status=status,
        model=model,
        note=note,
        lint_flags=lint_flags,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def review(
    db: AsyncSession,
    *,
    draft_id: int,
    reviewer_id: int,
    action: str,
    text_: Optional[str] = None,
) -> tuple[TaskHintDrafts, list[str]]:
    """Правка, подтверждение или отклонение черновика человеком.

    `approve` дописывает текст в КОНЕЦ `hints_text` под блокировкой строки
    задания: существующие подсказки не трогаются, повтор того же текста не
    дублируется. `content_provenance` не ставится — по той же причине, что в
    `PATCH /tasks/{id}/hints` (tsk-808): иначе замёрзнет условие задания.

    :returns: черновик и итоговый `hints_text` задания.
    :raises HintDraftError: черновика нет или он уже не в очереди.
    """
    draft = await db.scalar(
        select(TaskHintDrafts).where(TaskHintDrafts.id == draft_id).with_for_update()
    )
    if draft is None:
        raise HintDraftError("черновик не найден")
    if draft.status != "draft":
        raise HintDraftError(f"черновик уже обработан: {draft.status}")
    if text_ is not None:
        clean = " ".join(text_.split())
        if not clean:
            raise HintDraftError("пустой текст подсказки")
        draft.text = clean

    task = await db.scalar(select(Tasks).where(Tasks.id == draft.task_id).with_for_update())
    content = dict(task.task_content) if task and isinstance(task.task_content, dict) else {}
    hints = _existing_hints(content)

    now = datetime.now(timezone.utc)
    if action == "approve":
        if task is None:
            raise HintDraftError("задание удалено")
        if draft.text not in hints:
            hints = [*hints, draft.text]
        content["hints_text"] = hints
        content["has_hints"] = bool(hints) or bool(content.get("hints_video"))
        task.task_content = content
        draft.status = "approved"
    elif action == "reject":
        draft.status = "rejected"
    elif action != "save":
        raise HintDraftError("действие должно быть save, approve или reject")
    if action != "save":
        draft.reviewed_by = reviewer_id
        draft.reviewed_at = now
    await db.commit()
    await db.refresh(draft)
    logger.info(
        "tsk-1220: черновик %s задания %s — %s (пользователь %s)",
        draft.id, draft.task_id, action, reviewer_id,
    )
    return draft, hints


async def queue(
    db: AsyncSession, *, status: str = "draft", limit: int = 20, offset: int = 0
) -> tuple[int, list[dict[str, Any]]]:
    """Очередь вычитки: черновик рядом с условием, подсказками и исходными ответами."""
    total = int(
        await db.scalar(
            text("SELECT count(*) FROM task_hint_drafts WHERE status = :s"), {"s": status}
        )
        or 0
    )
    drafts = (
        await db.scalars(
            select(TaskHintDrafts)
            .where(TaskHintDrafts.status == status)
            .order_by(TaskHintDrafts.created_at, TaskHintDrafts.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items: list[dict[str, Any]] = []
    for d in drafts:
        row = (
            await db.execute(
                text(
                    "SELECT t.task_content, t.solution_rules, t.course_id, c.title AS course_title "
                    "FROM tasks t LEFT JOIN courses c ON c.id = t.course_id WHERE t.id = :id"
                ),
                {"id": d.task_id},
            )
        ).first()
        content = row.task_content if row and isinstance(row.task_content, dict) else {}
        srcs = (
            await db.execute(
                text(
                    "SELECT r.id, r.body, h.message FROM help_request_replies r "
                    "JOIN help_requests h ON h.id = r.request_id "
                    "WHERE r.id = ANY(:ids) ORDER BY r.created_at"
                ),
                {"ids": list(d.source_reply_ids)},
            )
        ).all()
        items.append(
            {
                "id": d.id,
                "task_id": d.task_id,
                "course_id": row.course_id if row else None,
                "course_title": row.course_title if row else None,
                "task_type": content.get("type"),
                "title": content.get("title"),
                "stem": clean_stem(content.get("stem")),
                "existing_hints": _existing_hints(content),
                "accepted_answers": accepted_answers(row.solution_rules if row else None),
                "text": d.text,
                "status": d.status,
                "model": d.model,
                "note": d.note,
                "created_at": d.created_at,
                "sources": [
                    {"reply_id": s.id, "question": s.message, "body": s.body} for s in srcs
                ],
            }
        )
    return total, items
