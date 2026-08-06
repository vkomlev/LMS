"""tsk-303 Фаза 1: инварианты схемы лестницы помощи (уровни 2-3).

Миграция `tsk303_help_ladder` не просто добавляет колонки — она меняет два
ограничения БД, и оба легко сломать незаметно:

1. `help_requests_request_type_check` стоит в базе с этапа 3.8, но в
   `__table_args__` модели его НЕТ. Забыть пересоздать ограничение — значит
   получить рабочий Python-код, который падает на INSERT только в рантайме,
   когда ученик впервые запросит индивидуальный разбор.
2. `ck_help_requests_webinar_link_type` намеренно сформулирован так, что NULL
   разрешён ВСЕГДА. Это не небрежность: TTL вебинар-ссылки реализуется
   обнулением при закрытии заявки (фаза 3), и более «строгая» формулировка
   вида «ссылка обязана быть у individual_review» заблокировала бы штатное
   закрытие. Тест фиксирует это как требование, а не как случайность.

Плюс проверяется то, ради чего история возвратов сделана таблицей, а не
счётчиком на заявке: агрегируемость по конкретному преподавателю (KPI).

Тесты идут внутри общей откатываемой транзакции (savepoint'ы для нарушений
ограничений), поэтому за собой ничего не чистят и в
`SELF_MANAGED_CONNECTION_MODULES` не попадают.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_TASK_CONTENT = '{"type":"SC","stem":"2+2?","options":[{"id":"a","text":"3"},{"id":"b","text":"4"}]}'
_SOLUTION_RULES = '{"max_score":1,"correct_options":["b"]}'


@pytest_asyncio.fixture(scope="function")
async def ladder(db: AsyncSession) -> dict[str, int]:
    """Минимальный набор строк для FK заявки: курс, задание, ученик, 2 учителя."""
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES ('tsk303 курс лестницы', 'self_guided') RETURNING id"
            )
        )
    ).scalar_one()

    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    assert difficulty_id is not None, "нет difficulties — задание не создать"

    task_id = (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, "
                "difficulty_id, external_uid) VALUES "
                "(CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid) RETURNING id"
            ),
            {
                "tc": _TASK_CONTENT,
                "sr": _SOLUTION_RULES,
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"tsk303-{uuid.uuid4().hex[:12]}",
            },
        )
    ).scalar_one()

    async def new_user(name: str) -> int:
        return (
            await db.execute(
                text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"), {"n": name}
            )
        ).scalar_one()

    return {
        "course": course_id,
        "task": task_id,
        "student": await new_user("tsk303 ученик"),
        "teacher_a": await new_user("tsk303 учитель А"),
        "teacher_b": await new_user("tsk303 учитель Б"),
    }


async def _insert_request(
    db: AsyncSession,
    ladder: dict[str, int],
    *,
    request_type: str = "manual_help",
    webinar_link: str | None = None,
    status: str = "open",
) -> int:
    """Создать заявку помощи напрямую в БД (проверяем ограничения, не сервис)."""
    return (
        await db.execute(
            text(
                "INSERT INTO help_requests (status, student_id, task_id, course_id, "
                "request_type, webinar_link, auto_created, context_json, priority, "
                "created_at, updated_at) "
                "VALUES (:st, :s, :t, :c, :rt, :wl, false, '{}'::jsonb, 100, now(), now()) "
                "RETURNING id"
            ),
            {
                "st": status,
                "s": ladder["student"],
                "t": ladder["task"],
                "c": ladder["course"],
                "rt": request_type,
                "wl": webinar_link,
            },
        )
    ).scalar_one()


async def test_individual_review_type_accepted(db: AsyncSession, ladder):
    """Новый класс заявки проходит CHECK — иначе уровень 2 не создать вовсе."""
    request_id = await _insert_request(db, ladder, request_type="individual_review")
    stored = (
        await db.execute(
            text("SELECT request_type FROM help_requests WHERE id = :id"), {"id": request_id}
        )
    ).scalar_one()
    assert stored == "individual_review"


async def test_unknown_request_type_still_rejected(db: AsyncSession, ladder):
    """Ограничение расширено, а не снято: произвольный класс по-прежнему не пройдёт."""
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await _insert_request(db, ladder, request_type="whatever_new")


async def test_webinar_link_rejected_for_other_request_types(db: AsyncSession, ladder):
    """Ссылка на разбор у заявки не того класса — ученику её никто не покажет.

    Молча осевшая ссылка выглядела бы как «преподаватель ответил», хотя
    в интерфейсе ученика она недостижима. Ограничение ловит это в БД.
    """
    for wrong_type in ("manual_help", "blocked_limit"):
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await _insert_request(
                    db, ladder, request_type=wrong_type, webinar_link="https://meet.example/room",
                )


async def test_blank_webinar_link_rejected(db: AsyncSession, ladder):
    """Пустая ссылка — тот же дефект, что и её отсутствие, но выглядит ответом.

    Профилактика класса из реестра ошибок проекта (`docs/ai/ERRORS.md`,
    2026-07-22 tsk-363: пустая строка вместо NULL уронила прод). Ссылку вводит
    руками преподаватель; `''` или пробелы прошли бы проверку «не NULL», ученик
    получил бы кнопку «Перейти к разбору» в пустоту, а заявка выглядела бы
    отвеченной. Реестр требует подстраховки на уровне схемы, не только в
    сервисе, — поэтому проверка здесь, а не (только) в фазе 3.
    """
    for blank in ("", "   ", "\t\n"):
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await _insert_request(
                    db, ladder, request_type="individual_review", webinar_link=blank,
                )


async def test_webinar_link_allowed_for_individual_review(db: AsyncSession, ladder):
    """Штатный путь уровня 2: ссылка живёт на заявке индивидуального разбора."""
    request_id = await _insert_request(
        db, ladder, request_type="individual_review", webinar_link="https://meet.example/room",
    )
    stored = (
        await db.execute(
            text("SELECT webinar_link FROM help_requests WHERE id = :id"), {"id": request_id}
        )
    ).scalar_one()
    assert stored == "https://meet.example/room"


async def test_closing_request_may_clear_webinar_link(db: AsyncSession, ladder):
    """TTL ссылки: закрытие заявки обнуляет её, и ограничение этому не мешает.

    Ровно тот путь, ради которого CHECK написан как «NULL разрешён всегда».
    Оценка разбора при этом сохраняется — она и есть история, в отличие от
    ссылки на уже несуществующую комнату.
    """
    request_id = await _insert_request(
        db, ladder, request_type="individual_review", webinar_link="https://meet.example/room",
    )
    await db.execute(
        text(
            "UPDATE help_requests SET webinar_link = NULL, review_understood = true, "
            "status = 'closed', closed_at = now() WHERE id = :id"
        ),
        {"id": request_id},
    )
    row = (
        await db.execute(
            text(
                "SELECT webinar_link, review_understood, request_type "
                "FROM help_requests WHERE id = :id"
            ),
            {"id": request_id},
        )
    ).fetchone()
    assert row[0] is None, "ссылка обязана обнуляться при закрытии (TTL заявки)"
    assert row[1] is True, "оценка разбора переживает обнуление ссылки"
    assert row[2] == "individual_review", "класс заявки при этом не меняется"


async def test_escalation_marker_defaults_to_null(db: AsyncSession, ladder):
    """Свежая заявка не эскалирована — отметка уровня 3 пустая."""
    request_id = await _insert_request(db, ladder)
    row = (
        await db.execute(
            text(
                "SELECT escalated_to_methodist_at, review_understood, webinar_link "
                "FROM help_requests WHERE id = :id"
            ),
            {"id": request_id},
        )
    ).fetchone()
    assert row == (None, None, None), "поля уровней 2-3 не должны заполняться сами"


async def test_reopens_are_aggregable_per_teacher(db: AsyncSession, ladder):
    """То, ради чего история возвратов — таблица, а не счётчик на заявке.

    Возврат начисляется тому, чей ответ не помог. Это не всегда назначенный
    преподаватель: к заявке по ACL может ответить и методист, и преподаватель
    по связи с учеником. Счётчик на строке заявки такие случаи не различает.
    """
    request_id = await _insert_request(db, ladder)
    second_request_id = await _insert_request(db, ladder)

    for req, teacher_key in (
        (request_id, "teacher_a"),
        (request_id, "teacher_a"),
        (second_request_id, "teacher_b"),
    ):
        await db.execute(
            text(
                "INSERT INTO help_request_reopens (request_id, teacher_id) VALUES (:r, :t)"
            ),
            {"r": req, "t": ladder[teacher_key]},
        )

    rows = (
        await db.execute(
            text(
                "SELECT teacher_id, COUNT(*) AS cnt FROM help_request_reopens "
                "WHERE teacher_id = ANY(:ids) GROUP BY teacher_id"
            ),
            {"ids": [ladder["teacher_a"], ladder["teacher_b"]]},
        )
    ).fetchall()
    per_teacher = {row[0]: row[1] for row in rows}
    assert per_teacher == {ladder["teacher_a"]: 2, ladder["teacher_b"]: 1}


async def test_reopens_die_with_their_request(db: AsyncSession, ladder):
    """История возврата привязана к заявке: удалили заявку — строки не осиротели."""
    request_id = await _insert_request(db, ladder)
    await db.execute(
        text("INSERT INTO help_request_reopens (request_id, teacher_id) VALUES (:r, :t)"),
        {"r": request_id, "t": ladder["teacher_a"]},
    )
    await db.execute(text("DELETE FROM help_requests WHERE id = :id"), {"id": request_id})
    left = (
        await db.execute(
            text("SELECT COUNT(*) FROM help_request_reopens WHERE request_id = :r"),
            {"r": request_id},
        )
    ).scalar_one()
    assert left == 0, "ON DELETE CASCADE обязан унести историю возвратов вместе с заявкой"


async def test_reopen_survives_teacher_removal(db: AsyncSession, ladder):
    """Учётку преподавателя удалили — факт возврата остаётся, автор обнуляется.

    Возврат — часть истории заявки, а не собственность учётной записи; терять
    строку целиком (CASCADE) значило бы задним числом исправлять статистику.
    """
    request_id = await _insert_request(db, ladder)
    await db.execute(
        text("INSERT INTO help_request_reopens (request_id, teacher_id) VALUES (:r, :t)"),
        {"r": request_id, "t": ladder["teacher_a"]},
    )
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": ladder["teacher_a"]})
    row = (
        await db.execute(
            text(
                "SELECT COUNT(*), COUNT(teacher_id) FROM help_request_reopens "
                "WHERE request_id = :r"
            ),
            {"r": request_id},
        )
    ).fetchone()
    assert row[0] == 1, "строка возврата обязана пережить удаление учётки"
    assert row[1] == 0, "автор обнуляется (ON DELETE SET NULL), а не тянет строку за собой"
