"""Сессии разговора с ИИ-наставником (tsk-572 этап 2).

Держит состояние диалога и правила его жизни: один открытый разговор на пару
«ученик + задание», мягкий предел ходов, TTL для брошенных сессий.

Транспорт к модели здесь не вызывается — этим занимается роутер, чтобы поток
шёл ученику по мере генерации, а не копился в сервисе.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_tutor.prompt import (
    TutorMode,
    TutorTaskView,
    build_opening_user_message,
    build_system_prompt,
    pick_mode,
)
from app.services.llm import LLMMessage

logger = logging.getLogger(__name__)

# Мягкий предел: не обрыв, а момент, когда наставник начинает предлагать
# преподавателя. Жёстко резать нельзя — ученик бросит на середине мысли.
SOFT_TURN_LIMIT = 10
# Жёсткий потолок: защита от бесконечного диалога и от расхода на одного ученика.
HARD_TURN_LIMIT = 25
# Через сколько часов без активности разговор считается брошенным.
SESSION_TTL_HOURS = 24


@dataclass
class TutorSession:
    id: int
    student_id: int
    task_id: int
    mode: TutorMode
    turns: int
    status: str
    task_stem_snapshot: str
    student_answer_snapshot: Optional[str]

    @property
    def soft_limit_reached(self) -> bool:
        return self.turns >= SOFT_TURN_LIMIT

    @property
    def hard_limit_reached(self) -> bool:
        return self.turns >= HARD_TURN_LIMIT


async def _load_task_view(db: AsyncSession, task_id: int) -> tuple[TutorTaskView, int | None]:
    """Достать безопасный вид задания.

    SQL перечисляет колонки поимённо и `solution_rules` среди них НЕТ — эталон
    не должен доехать даже до памяти процесса, где его мог бы подхватить
    следующий рефакторинг.
    """
    row = (await db.execute(text("""
        SELECT t.id, t.task_content, t.course_id, c.title AS course_title
        FROM tasks t LEFT JOIN courses c ON c.id = t.course_id
        WHERE t.id = :tid AND t.is_active
    """), {"tid": task_id})).mappings().first()
    if row is None:
        raise ValueError(f"задание {task_id} не найдено или неактивно")

    content = row["task_content"] if isinstance(row["task_content"], dict) else {}

    class _Shim:
        id = row["id"]
        task_content = content

    return TutorTaskView.from_task(_Shim, course_title=row["course_title"]), row["course_id"]


async def _last_student_answer(db: AsyncSession, student_id: int, task_id: int) -> Optional[str]:
    """Последний ответ ученика по заданию — то, с чем он пришёл.

    Берём только реальные ученические сдачи (`spw_web`): ручная простановка
    преподавателя (`manual_teacher`) — это его отметка, а не текст ученика, и
    подсовывать её наставнику как «твой ответ» бессмысленно.

    **Ответ лежит ВЛОЖЕННО (tsk-666).** Клиент сохраняет сдачу как
    `{"type": ..., "response": {"value": ..., "comment": ...}, ...}`, а разбор
    искал ключи на верхнем уровне — там их нет никогда. Итог по проду: снимок
    ответа пуст у **27 сессий из 27**, то есть наставник не видел ответа ученика
    ни разу за всё время работы контура. Три молчаливых последствия:

    1. Наставник не знает, ЧТО ученик ответил, и вынужден спрашивать.
    2. `pick_mode` считает `has_student_code=False` всегда — режим `debug`
       («разберём твой код») недостижим в принципе: на проде 15 `concept`,
       8 `thin`, 4 `mission` и **ноль** `debug`.
    3. Первая реплика всегда идёт по ветке «ученик ещё НЕ отправлял ответ»
       («где ты застрял?») вместо «как ты рассуждал» — даже сразу после
       неверной сдачи, ради которой наставник и открылся.

    Плоская форма оставлена запасным путём: старые строки и другие клиенты.
    """
    row = (await db.execute(text("""
        SELECT tr.answer_json
        FROM task_results tr
        WHERE tr.user_id = :uid AND tr.task_id = :tid
          AND (tr.source_system = 'spw_web' OR tr.source_system IS NULL)
        ORDER BY tr.submitted_at DESC, tr.id DESC
        LIMIT 1
    """), {"uid": student_id, "tid": task_id})).first()
    if not row or row[0] is None:
        return None
    payload = row[0]
    if not isinstance(payload, dict):
        return str(payload)

    response = payload.get("response")
    parts: list[str] = []
    if isinstance(response, dict):
        for key in ("value", "text", "code"):
            if response.get(key):
                parts.append(str(response[key]))
                break
        # Комментарий — это рассуждение ученика своими словами. Для наставника
        # он ценнее самого ответа: там видно, ГДЕ сломалась мысль.
        if response.get("comment"):
            parts.append(f"Пояснение ученика: {response['comment']}")
    else:
        for key in ("answer", "value", "text", "code"):
            if payload.get(key):
                parts.append(str(payload[key]))
                break

    return "\n".join(parts) or None


async def get_or_create(
    db: AsyncSession, *, student_id: int, task_id: int
) -> tuple[TutorSession, bool]:
    """Найти открытый разговор или начать новый. Второе значение — «создан ли».

    Уникальный частичный индекс в БД гарантирует единственность открытой сессии
    на пару: без него ученик открыл бы вторую вкладку и обнулил счётчик ходов.
    """
    # Протухший разговор закрываем ЗДЕСЬ, а не надеемся на уборщика: `expire_stale`
    # написан и покрыт тестом, но в рабочем коде его не зовёт никто (tsk-659 —
    # проверено grep'ом по всему репозиторию). То есть срок жизни разговора
    # существовал только на бумаге, и ученик, вернувшийся к заданию через неделю,
    # попадал во вчерашний разговор с чужим контекстом — ровно то, что оговорка
    # ниже обещала не допустить.
    #
    # Ленивое истечение чинит именно ученический вред и не требует планировщика.
    # Уборщик для отчётности (закрыть брошенное у тех, кто НЕ вернулся) остаётся
    # отдельной задачей — на цифру «сколько разговоров доведено до конца» он
    # влияет, на путь ученика нет.
    await db.execute(text("""
        UPDATE ai_tutor_session
        SET status = 'expired', closed_at = now()
        WHERE student_id = :uid AND task_id = :tid AND status = 'open'
          AND last_activity_at < now() - make_interval(hours => :ttl)
    """), {"uid": student_id, "tid": task_id, "ttl": SESSION_TTL_HOURS})

    row = (await db.execute(text("""
        SELECT id, student_id, task_id, mode, turns, status,
               task_stem_snapshot, student_answer_snapshot
        FROM ai_tutor_session
        WHERE student_id = :uid AND task_id = :tid AND status = 'open'
    """), {"uid": student_id, "tid": task_id})).mappings().first()
    if row:
        return TutorSession(**dict(row)), False

    view, course_id = await _load_task_view(db, task_id)
    answer = await _last_student_answer(db, student_id, task_id)
    mode = pick_mode(view, has_student_code=_looks_like_code(answer))

    created = (await db.execute(text("""
        INSERT INTO ai_tutor_session
            (student_id, task_id, course_id, mode, status,
             task_stem_snapshot, student_answer_snapshot)
        VALUES (:uid, :tid, :cid, :mode, 'open', :stem, :answer)
        RETURNING id, student_id, task_id, mode, turns, status,
                  task_stem_snapshot, student_answer_snapshot
    """), {
        "uid": student_id, "tid": task_id, "cid": course_id, "mode": mode,
        "stem": view.stem, "answer": answer,
    })).mappings().first()

    session = TutorSession(**dict(created))
    system_prompt = build_system_prompt(view, mode, student_answer=answer)
    await add_message(db, session.id, "system", system_prompt)
    await db.commit()
    logger.info(
        "ai_tutor: начата сессия id=%s student=%s task=%s режим=%s",
        session.id, student_id, task_id, mode,
    )
    return session, True


def _looks_like_code(answer: Optional[str]) -> bool:
    """Похоже ли на код — грубо, по характерным признакам Python."""
    if not answer:
        return False
    markers = ("def ", "for ", "while ", "print(", "input(", "if ", "=", ":")
    hits = sum(1 for m in markers if m in answer)
    return "\n" in answer and hits >= 2


async def add_message(
    db: AsyncSession, session_id: int, role: str, content: str,
    *, model: str | None = None, truncated: bool = False,
) -> None:
    await db.execute(text("""
        INSERT INTO ai_tutor_message (session_id, role, content, model, truncated)
        VALUES (:sid, :role, :content, :model, :truncated)
    """), {"sid": session_id, "role": role, "content": content,
           "model": model, "truncated": truncated})


async def note_turn(
    db: AsyncSession,
    session_id: int,
    *,
    model: str | None,
    guard_hit: dict | None = None,
) -> None:
    """Записать в сессию, КТО отвечал и срабатывал ли страж (tsk-748).

    До 31.08 `meta` был пуст у всех 56 сессий, и разбор инцидента упирался в
    вопрос без ответа: цепочка наставника перебирает четыре модели, последняя из
    них мини-модель, а какая ответила — нигде. Модель по каждому сообщению
    пишется в `ai_tutor_message.model` и раньше, но чтобы ответить «кто вёл ЭТОТ
    разговор», приходилось собирать её по репликам; сводка на сессии отвечает
    сразу и переживает чистку сообщений.

    Слияние делается в одном запросе поверх текущего значения: параллельная
    вкладка ученика не должна затирать чужой ход. `jsonb_typeof` здесь не
    перестраховка — JSON-null в jsonb это не SQL NULL, и `COALESCE` его
    пропускает, после чего `||` роняет запись.
    """
    if not model:
        return
    await db.execute(text("""
        WITH cur AS (
            SELECT id,
                   CASE WHEN jsonb_typeof(meta) = 'object' THEN meta ELSE '{}'::jsonb END AS base
            FROM ai_tutor_session WHERE id = :sid
        )
        UPDATE ai_tutor_session s
        SET meta = cur.base
            || jsonb_build_object(
                 'last_model', CAST(:model AS text),
                 'models', COALESCE(cur.base -> 'models', '{}'::jsonb)
                     || jsonb_build_object(
                          CAST(:model AS text),
                          COALESCE(CAST(cur.base -> 'models' ->> :model AS int), 0) + 1
                        )
               )
            || CASE WHEN CAST(:hit AS jsonb) IS NULL THEN '{}'::jsonb
                    ELSE jsonb_build_object(
                        'guard_hits',
                        COALESCE(cur.base -> 'guard_hits', '[]'::jsonb)
                            || jsonb_build_array(CAST(:hit AS jsonb))
                    ) END
        FROM cur WHERE s.id = cur.id
    """), {
        "sid": session_id, "model": model,
        "hit": json.dumps(guard_hit, ensure_ascii=False) if guard_hit else None,
    })


async def history(db: AsyncSession, session_id: int) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT role, content, created_at, truncated
        FROM ai_tutor_message WHERE session_id = :sid ORDER BY id
    """), {"sid": session_id})).mappings().all()
    return [dict(r) for r in rows]


async def build_llm_messages(
    db: AsyncSession, session: TutorSession, new_student_text: str | None
) -> list[LLMMessage]:
    """Собрать переписку для модели.

    Системная инструкция пересобирается на каждом ходе (а не берётся из БД):
    у неё появляется отметка о мягком пределе, когда разговор затянулся.
    Историю при этом берём из БД — она и есть память разговора.
    """
    view, _ = await _load_task_view(db, session.task_id)
    system = build_system_prompt(
        view, session.mode,
        student_answer=session.student_answer_snapshot,
        soft_limit=session.soft_limit_reached,
    )
    messages: list[LLMMessage] = [LLMMessage(role="system", content=system)]

    rows = await history(db, session.id)
    student_turns = [r for r in rows if r["role"] in ("student", "tutor")]
    if not student_turns:
        messages.append(LLMMessage(
            role="user",
            content=build_opening_user_message(view, session.student_answer_snapshot),
        ))
    else:
        for r in student_turns:
            messages.append(LLMMessage(
                role="user" if r["role"] == "student" else "assistant",
                content=r["content"],
            ))
    if new_student_text:
        from app.services.ai_tutor.prompt import STUDENT_DATA_CLOSE, STUDENT_DATA_OPEN
        # Реплика ученика тоже идёт как данные: правила промпта прямо говорят,
        # что содержимое этих меток — не команды.
        messages.append(LLMMessage(
            role="user",
            content=f"{STUDENT_DATA_OPEN}\n{new_student_text.strip()}\n{STUDENT_DATA_CLOSE}",
        ))
    return messages


async def bump_turn(db: AsyncSession, session_id: int) -> None:
    await db.execute(text("""
        UPDATE ai_tutor_session
        SET turns = turns + 1, last_activity_at = now()
        WHERE id = :sid
    """), {"sid": session_id})


async def close(db: AsyncSession, session_id: int, status: str = "closed") -> None:
    await db.execute(text("""
        UPDATE ai_tutor_session
        SET status = :st, closed_at = now()
        WHERE id = :sid AND status = 'open'
    """), {"sid": session_id, "st": status})


async def expire_stale(db: AsyncSession, ttl_hours: int = SESSION_TTL_HOURS) -> int:
    """Закрыть брошенные разговоры.

    Без этого открытая сессия висит вечно и блокирует уникальный индекс: ученик,
    вернувшийся к заданию через неделю, попал бы в старый разговор с чужим
    контекстом вместо нового.
    """
    res = await db.execute(text("""
        UPDATE ai_tutor_session
        SET status = 'expired', closed_at = now()
        WHERE status = 'open'
          AND last_activity_at < now() - make_interval(hours => :h)
        RETURNING id
    """), {"h": ttl_hours})
    ids = [r[0] for r in res.fetchall()]
    if ids:
        await db.commit()
        logger.info("ai_tutor: закрыто брошенных сессий: %s", len(ids))
    return len(ids)
