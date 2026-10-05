"""tsk-1249: индивидуальный курс ученику из кабинета методиста.

Правило оператора (tsk-1245): индивидуальный курс (повторение, доработка)
закрывает прохождение основных курсов ученика, пока не пройден целиком.

Индивидуальный курс — обычный корневой курс. Он либо собирается из готовых
тем банка (они становятся его подкурсами, задания не копируются), либо
берётся готовый. Замок — точечная зависимость
`course_dependencies(основной_корень -> индивидуальный, auto_assign=false)`
(tsk-231, фаза 6): блокирует только тех, кому индивидуальный курс доступен.
Снимается сам по COMPLETED, досрочно — отключением записи ученика.

Всё в одной транзакции и одним коммитом: репозитории проекта коммитят сами,
поэтому здесь прямой SQL — иначе сбой на середине оставил бы курс без замка
или замок без курса.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import alumni_enrollment_guard, course_activity_service
from app.services.learning_engine_service import LearningEngineService

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Индивидуальный курс повторения. Пока он не пройден, основные курсы закрыты. "
    "В каждой теме сначала теория, потом задания. Решай сам: на занятии разберём "
    "твой код устно."
)


@dataclass
class IndividualCourse:
    """Индивидуальный курс ученика с замками."""

    course_id: int
    title: str
    course_uid: str | None
    is_active: bool
    state: str
    locked_root_ids: list[int]
    locked_root_titles: list[str]
    topic_ids: list[int]
    topic_titles: list[str]


async def _student_roots(db: AsyncSession, user_id: int) -> list[int]:
    """Активные корни ученика в порядке его плана."""
    rows = await db.execute(
        text(
            "SELECT course_id FROM user_courses WHERE user_id=:u AND is_active "
            "ORDER BY order_number"
        ),
        {"u": user_id},
    )
    return [int(r[0]) for r in rows]


async def _assert_student(db: AsyncSession, user_id: int) -> None:
    """404, если ученика нет."""
    found = await db.execute(text("SELECT 1 FROM users WHERE id=:u"), {"u": user_id})
    if found.scalar() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Пользователь {user_id} не найден")


async def _create_from_topics(
    db: AsyncSession, user_id: int, title: str, topic_ids: list[int]
) -> int:
    """Создать корень и подключить темы подкурсами по порядку."""
    if not topic_ids or len(set(topic_ids)) != len(topic_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужен список тем без повторов")
    found = await db.execute(
        text("SELECT count(*) FROM courses WHERE id = ANY(:ids)"), {"ids": topic_ids}
    )
    if found.scalar() != len(topic_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Среди тем есть несуществующий курс")
    # Тема становится главой нового курса. Курс, на который кто-то записан
    # (ученик или преподаватель), главой быть не может: записывают только на
    # верхний уровень, а для основного курса ученика это ещё и замок без
    # выхода — «88 требует курс, внутри которого лежит 88».
    enrolled = (
        await db.execute(
            text(
                "SELECT c.id, c.title FROM courses c WHERE c.id = ANY(:ids) AND ("
                " EXISTS (SELECT 1 FROM user_courses u WHERE u.course_id = c.id) OR"
                " EXISTS (SELECT 1 FROM teacher_courses t WHERE t.course_id = c.id))"
            ),
            {"ids": topic_ids},
        )
    ).all()
    if enrolled:
        names = ", ".join(f"«{r[1]}»" for r in enrolled)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Темой нельзя взять курс, на который уже записаны люди: {names}. "
            "Выберите главы внутри него.",
        )
    await course_activity_service.assert_courses_active(
        db, topic_ids, action="сборка индивидуального курса"
    )
    uid = f"ind:user{user_id}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    course_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level, description, course_uid) "
                    "VALUES (:t, 'self_guided', :d, :uid) RETURNING id"
                ),
                {"t": title.strip(), "d": _DESCRIPTION, "uid": uid},
            )
        ).scalar_one()
    )
    for n, topic_id in enumerate(topic_ids, start=1):
        await db.execute(
            text(
                "INSERT INTO course_parents (course_id, parent_course_id, order_number, is_transparent) "
                "VALUES (:c, :p, :n, false)"
            ),
            {"c": topic_id, "p": course_id, "n": n},
        )
    return course_id


async def _enroll(db: AsyncSession, user_id: int, course_id: int) -> None:
    """Записать на курс (или вернуть отключённую запись)."""
    has_parents = await db.execute(
        text("SELECT 1 FROM course_parents WHERE course_id=:c LIMIT 1"), {"c": course_id}
    )
    if has_parents.scalar():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Выдать можно только курс верхнего уровня, этот вложен в другой. "
            "Соберите индивидуальный курс из него как из темы.",
        )
    existing = (
        await db.execute(
            text("SELECT is_active FROM user_courses WHERE user_id=:u AND course_id=:c"),
            {"u": user_id, "c": course_id},
        )
    ).first()
    if existing is not None and existing[0]:
        return
    # Новая или возвращаемая запись — одни и те же проверки: снятую выдачу нельзя
    # вернуть выпускнику или на выключенный курс.
    await course_activity_service.assert_courses_active(
        db, [course_id], action="выдача индивидуального курса"
    )
    await alumni_enrollment_guard.assert_not_alumni(
        db, user_id, action="выдача индивидуального курса"
    )
    if existing is not None:
        await db.execute(
            text("UPDATE user_courses SET is_active=true WHERE user_id=:u AND course_id=:c"),
            {"u": user_id, "c": course_id},
        )
        return
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
        {"u": user_id, "c": course_id},
    )


async def _assert_lock_hits_only_student(
    db: AsyncSession, user_id: int, course_id: int, locks: list[int]
) -> None:
    """409, если новый замок закроет основной курс и другим держателям курса.

    Связка `основной -> индивидуальный` общая для всех: она закрывает основной
    курс каждому, у кого есть и тот, и другой. У собранного курса держатель
    один, а у готового общего (мини-курс tsk-231 на нескольких учеников) новая
    связка молча закрыла бы основной курс и остальным. Уже существующие связки
    не проверяются — их поставили намеренно.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT r.id, r.title, count(DISTINCT other.user_id)
                FROM courses r
                JOIN user_courses other ON other.course_id = :c AND other.is_active
                                       AND other.user_id <> :u
                JOIN user_courses main ON main.user_id = other.user_id
                                      AND main.course_id = r.id AND main.is_active
                WHERE r.id = ANY(:roots)
                  AND NOT EXISTS (SELECT 1 FROM course_dependencies d
                                  WHERE d.course_id = r.id AND d.required_course_id = :c)
                GROUP BY r.id, r.title
                """
            ),
            {"c": course_id, "u": user_id, "roots": locks},
        )
    ).all()
    if rows:
        detail = ", ".join(f"«{r[1]}» ещё у {r[2]} чел." for r in rows)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Этот курс выдан и другим ученикам — новый замок закрыл бы им: "
            f"{detail}. Соберите отдельный курс из тех же тем.",
        )


async def issue(
    db: AsyncSession,
    user_id: int,
    *,
    course_id: int | None,
    title: str | None,
    topic_ids: list[int] | None,
    lock_root_ids: list[int] | None,
) -> int:
    """Выдать индивидуальный курс ученику и закрыть его основные курсы.

    :param course_id: готовый корневой курс; иначе собирается из `topic_ids`.
    :param title: название нового курса (при сборке).
    :param topic_ids: темы банка по порядку (при сборке).
    :param lock_root_ids: какие корни закрыть; None — все активные корни ученика.
    :return: id индивидуального курса.
    """
    await _assert_student(db, user_id)
    if (course_id is None) == (not topic_ids):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Укажите либо готовый курс, либо темы для сборки — что-то одно",
        )
    if course_id is None and not (title or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Для нового курса нужно название")
    try:
        roots_before = await _student_roots(db, user_id)
        if course_id is None:
            course_id = await _create_from_topics(db, user_id, title or "", topic_ids or [])
        await _enroll(db, user_id, course_id)
        wanted = [r for r in (lock_root_ids if lock_root_ids is not None else roots_before) if r != course_id]
        ever = set(
            (
                await db.execute(
                    text("SELECT course_id FROM user_courses WHERE user_id=:u"), {"u": user_id}
                )
            ).scalars().all()
        )
        foreign = set(wanted) - ever
        if foreign:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Ученик не записан на курсы {sorted(foreign)} — закрыть их нельзя",
            )
        # Отключённую запись пропускаем молча: закрывать там нечего, а кабинет
        # видит записи ученика без признака активности.
        locks = [r for r in wanted if r in roots_before]
        if not locks:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "У ученика нет основных курсов, которые можно закрыть",
            )
        await _assert_lock_hits_only_student(db, user_id, course_id, locks)
        for root in locks:
            await db.execute(
                text(
                    "INSERT INTO course_dependencies (course_id, required_course_id, auto_assign) "
                    "VALUES (:c, :r, false) ON CONFLICT (course_id, required_course_id) DO NOTHING"
                ),
                {"c": root, "r": course_id},
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    logger.info(
        "tsk-1249: ученику %s выдан индивидуальный курс %s, закрыты %s",
        user_id, course_id, locks,
    )
    return course_id


async def list_for_student(db: AsyncSession, user_id: int) -> list[IndividualCourse]:
    """Индивидуальные курсы ученика: его курсы, которые точечно закрывают его корни."""
    await _assert_student(db, user_id)
    rows = (
        await db.execute(
            text(
                """
                SELECT c.id, c.title, c.course_uid, uc.is_active,
                       array_agg(DISTINCT cd.course_id) AS roots
                FROM user_courses uc
                JOIN courses c ON c.id = uc.course_id
                JOIN course_dependencies cd
                  ON cd.required_course_id = uc.course_id AND NOT cd.auto_assign
                JOIN user_courses mine
                  ON mine.user_id = uc.user_id AND mine.course_id = cd.course_id
                 AND mine.is_active
                WHERE uc.user_id = :u
                GROUP BY c.id, c.title, c.course_uid, uc.is_active, uc.order_number
                ORDER BY uc.order_number
                """
            ),
            {"u": user_id},
        )
    ).all()
    engine = LearningEngineService()
    result: list[IndividualCourse] = []
    for cid, title, uid, active, roots in rows:
        root_ids = sorted(int(r) for r in roots)
        names = await _titles(db, root_ids)
        topics = (
            await db.execute(
                text(
                    "SELECT c.id, c.title FROM course_parents p JOIN courses c ON c.id=p.course_id "
                    "WHERE p.parent_course_id=:c ORDER BY p.order_number"
                ),
                {"c": cid},
            )
        ).all()
        state = await engine.compute_course_state(db, user_id, cid, update_state_table=False)
        result.append(
            IndividualCourse(
                course_id=int(cid), title=title, course_uid=uid, is_active=bool(active),
                state=state.state, locked_root_ids=root_ids,
                locked_root_titles=[names[r] for r in root_ids],
                topic_ids=[int(t[0]) for t in topics], topic_titles=[t[1] for t in topics],
            )
        )
    return result


async def _titles(db: AsyncSession, ids: list[int]) -> dict[int, str]:
    """Названия курсов по id."""
    rows = await db.execute(
        text("SELECT id, title FROM courses WHERE id = ANY(:ids)"), {"ids": ids}
    )
    return {int(r[0]): r[1] for r in rows}


async def withdraw(db: AsyncSession, user_id: int, course_id: int) -> None:
    """Снять индивидуальный курс: отключить запись — замки перестают действовать.

    Строки зависимостей остаются: точечная связка без держателей никого не
    блокирует, а повторная выдача вернёт её в силу без пересоздания.
    """
    try:
        res = await db.execute(
            text(
                "UPDATE user_courses SET is_active=false "
                "WHERE user_id=:u AND course_id=:c AND is_active"
            ),
            {"u": user_id, "c": course_id},
        )
        if res.rowcount == 0:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Активной выдачи этого курса у ученика нет"
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    logger.info("tsk-1249: у ученика %s снят индивидуальный курс %s", user_id, course_id)
