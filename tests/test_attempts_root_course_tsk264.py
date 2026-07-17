"""
Попытки по паре «курс + задание» (tsk-264): счёт попыток в границах корня.

Граф курсов переиспользует узлы (из 645 узлов прода — 24, до 5 родителей).
Раньше `attempts_used` считался по заданию независимо от пути, и ученик,
исчерпавший попытки в курсе X, приходил в курс Y через тот же узел с уже
мёртвым заданием (жалоба приёмки tsk-261 A7).

Проверяем на НАСТОЯЩЕЙ БД (не на моках — граф, рекурсивные CTE и запись
попытки живут в PostgreSQL, мок их не воспроизводит):
- переиспользуемый узел под двумя корнями → попытки не пересекаются;
- обычный узел → счёт как раньше (регресс);
- прогресс (PASSED) остаётся ОБЩИМ для обоих корней — решение оператора;
- заявленный клиентом чужой корень отвергается (иначе лимит обходится);
- попытка с пустым корнем (путь неизвестен) не расходует лимит.

Фикстура графа:
    root_a (1)          root_b (2)
        \\               /
         reused_node (общий узел с заданием)
    root_c → plain_node (обычный узел, один родитель)
"""
import os
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.services.attempts_service import AttemptsService
from app.services.learning_engine_service import (
    DEFAULT_MAX_ATTEMPTS,
    LearningEngineService,
)
from app.utils.exceptions import DomainError

settings = Settings()
engine_svc = LearningEngineService()
attempts_svc = AttemptsService()


@pytest_asyncio.fixture(scope="function")
async def graph():
    """Учебный граф с переиспользуемым узлом + студент. Полная уборка за собой."""
    engine = create_async_engine(settings.database_url)
    ids: dict[str, int] = {}
    async with AsyncSession(engine, expire_on_commit=False) as s:
        try:
            async def new_course(title: str) -> int:
                return (
                    await s.execute(
                        text(
                            "INSERT INTO courses (title, access_level) "
                            "VALUES (:t, 'self_guided') RETURNING id"
                        ),
                        {"t": title},
                    )
                ).scalar()

            ids["root_a"] = await new_course("tsk264 корень A")
            ids["root_b"] = await new_course("tsk264 корень B")
            ids["root_c"] = await new_course("tsk264 корень C")
            ids["reused"] = await new_course("tsk264 переиспользуемый узел")
            ids["plain"] = await new_course("tsk264 обычный узел")

            # Переиспользуемый узел висит под двумя корнями — как на проде.
            for parent in ("root_a", "root_b"):
                await s.execute(
                    text(
                        "INSERT INTO course_parents (course_id, parent_course_id) "
                        "VALUES (:c, :p)"
                    ),
                    {"c": ids["reused"], "p": ids[parent]},
                )
            await s.execute(
                text(
                    "INSERT INTO course_parents (course_id, parent_course_id) "
                    "VALUES (:c, :p)"
                ),
                {"c": ids["plain"], "p": ids["root_c"]},
            )

            difficulty_id = (
                await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
            ).scalar()
            assert difficulty_id is not None, "нет difficulties — граф не собрать"

            async def new_task(course_id: int, uid: str) -> int:
                return (
                    await s.execute(
                        text(
                            "INSERT INTO tasks (task_content, course_id, difficulty_id, "
                            "external_uid) VALUES "
                            "(CAST(:tc AS jsonb), :cid, :did, :uid) RETURNING id"
                        ),
                        {
                            "tc": '{"type": "SA", "question": "tsk264"}',
                            "cid": course_id,
                            "did": difficulty_id,
                            "uid": uid,
                        },
                    )
                ).scalar()

            ids["task_reused"] = await new_task(ids["reused"], "tsk264-reused")
            ids["task_plain"] = await new_task(ids["plain"], "tsk264-plain")
            # Квиз на переиспользуемом узле — у него ответ один навсегда.
            ids["task_quiz"] = (
                await s.execute(
                    text(
                        "INSERT INTO tasks (task_content, course_id, difficulty_id, "
                        "external_uid) VALUES (CAST(:tc AS jsonb), :cid, :did, :uid) "
                        "RETURNING id"
                    ),
                    {
                        "tc": '{"type": "SC_Qw", "question": "tsk264 quiz"}',
                        "cid": ids["reused"],
                        "did": difficulty_id,
                        "uid": "tsk264-quiz",
                    },
                )
            ).scalar()

            ids["user"] = (
                await s.execute(
                    text(
                        "INSERT INTO users (full_name) VALUES "
                        "('tsk264 тестовый ученик') RETURNING id"
                    )
                )
            ).scalar()
            # Ученик записан на оба корня сразу — как в жалобе: прошёл A, пришёл в B.
            for r in ("root_a", "root_b", "root_c"):
                await s.execute(
                    text(
                        "INSERT INTO user_courses (user_id, course_id, is_active) "
                        "VALUES (:u, :c, true)"
                    ),
                    {"u": ids["user"], "c": ids[r]},
                )
            await s.commit()
            yield ids, s
        finally:
            await s.rollback()
            await s.execute(
                text("DELETE FROM task_results WHERE user_id = :u"), {"u": ids.get("user", -1)}
            )
            await s.execute(
                text("DELETE FROM attempts WHERE user_id = :u"), {"u": ids.get("user", -1)}
            )
            await s.execute(
                text("DELETE FROM user_courses WHERE user_id = :u"), {"u": ids.get("user", -1)}
            )
            await s.execute(
                text("DELETE FROM tasks WHERE id = ANY(:t)"),
                {"t": [ids[k] for k in ("task_reused", "task_plain", "task_quiz") if k in ids]},
            )
            await s.execute(
                text("DELETE FROM course_parents WHERE course_id = ANY(:c)"),
                {"c": [ids[k] for k in ("reused", "plain") if k in ids]},
            )
            await s.execute(
                text("DELETE FROM users WHERE id = :u"), {"u": ids.get("user", -1)}
            )
            await s.execute(
                text("DELETE FROM courses WHERE id = ANY(:c)"),
                {"c": list(ids[k] for k in ids if k != "user" and not k.startswith("task"))},
            )
            await s.commit()
            await engine.dispose()


async def _burn_attempts(
    s: AsyncSession, user_id: int, task_id: int, course_id: int,
    root_course_id: int | None, count: int,
) -> None:
    """Записать `count` неверных ответов по заданию в контексте корня."""
    for _ in range(count):
        attempt = await attempts_svc.create_attempt(
            s,
            user_id=user_id,
            course_id=course_id,
            root_course_id=root_course_id,
            source_system="test_tsk264",
        )
        await s.execute(
            text(
                "INSERT INTO task_results (user_id, task_id, attempt_id, score, "
                "max_score, is_correct, submitted_at) VALUES "
                "(:u, :t, :a, 0, 10, false, now())"
            ),
            {"u": user_id, "t": task_id, "a": attempt.id},
        )
    await s.commit()


async def test_reused_node_attempts_do_not_cross_roots(graph):
    """Исчерпал попытки в курсе A → в курсе B задание живо и попытки свежие."""
    ids, s = graph
    await _burn_attempts(
        s, ids["user"], ids["task_reused"], ids["reused"],
        ids["root_a"], DEFAULT_MAX_ATTEMPTS,
    )

    in_a = await engine_svc.compute_task_state(
        s, ids["user"], ids["task_reused"], root_course_id=ids["root_a"]
    )
    assert in_a.attempts_used == DEFAULT_MAX_ATTEMPTS
    assert in_a.state == "BLOCKED_LIMIT", "в своём курсе лимит обязан работать"

    in_b = await engine_svc.compute_task_state(
        s, ids["user"], ids["task_reused"], root_course_id=ids["root_b"]
    )
    assert in_b.attempts_used == 0, (
        "попытки курса A просочились в курс B — это и есть жалоба tsk-261 A7"
    )
    assert in_b.state != "BLOCKED_LIMIT", "в новом курсе задание не должно быть заблокировано"


async def test_passed_progress_stays_shared_between_roots(graph):
    """Прогресс общий: пройденное в A остаётся пройденным в B (решение оператора)."""
    ids, s = graph
    attempt = await attempts_svc.create_attempt(
        s, user_id=ids["user"], course_id=ids["reused"],
        root_course_id=ids["root_a"], source_system="test_tsk264",
    )
    await s.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "is_correct, submitted_at) VALUES (:u, :t, :a, 10, 10, true, now())"
        ),
        {"u": ids["user"], "t": ids["task_reused"], "a": attempt.id},
    )
    await s.commit()

    in_b = await engine_svc.compute_task_state(
        s, ids["user"], ids["task_reused"], root_course_id=ids["root_b"]
    )
    assert in_b.state == "PASSED", (
        "решённое в курсе A обязано остаться решённым в курсе B — "
        "перерешивать известное ученик не должен"
    )


async def test_plain_node_regression_unchanged(graph):
    """Обычный узел (один родитель): счёт попыток как раньше."""
    ids, s = graph
    await _burn_attempts(
        s, ids["user"], ids["task_plain"], ids["plain"],
        ids["root_c"], DEFAULT_MAX_ATTEMPTS,
    )
    state = await engine_svc.compute_task_state(
        s, ids["user"], ids["task_plain"], root_course_id=ids["root_c"]
    )
    assert state.attempts_used == DEFAULT_MAX_ATTEMPTS
    assert state.state == "BLOCKED_LIMIT", "регресс: обычный узел обязан блокироваться"


async def test_resolve_root_picks_single_active_root(graph):
    """Узел под одним активным корнем → корень восстанавливается без подсказки клиента."""
    ids, s = graph
    root = await engine_svc.resolve_attempt_root(
        s, student_id=ids["user"], course_id=ids["plain"]
    )
    assert root == ids["root_c"]


async def test_resolve_root_ambiguous_returns_none(graph):
    """Узел под двумя активными корнями без контекста → None, гадать нельзя."""
    ids, s = graph
    root = await engine_svc.resolve_attempt_root(
        s, student_id=ids["user"], course_id=ids["reused"]
    )
    assert root is None


async def test_resolve_root_rejects_foreign_root(graph):
    """Заявленный корень, не содержащий узел, отвергается — иначе лимит обходится."""
    ids, s = graph
    with pytest.raises(DomainError):
        await engine_svc.resolve_attempt_root(
            s,
            student_id=ids["user"],
            course_id=ids["plain"],
            requested_root_course_id=ids["root_a"],
        )


async def test_requested_root_accepted_when_contains_node(graph):
    """Клиент передал корень, чьё дерево содержит узел → он и берётся."""
    ids, s = graph
    root = await engine_svc.resolve_attempt_root(
        s,
        student_id=ids["user"],
        course_id=ids["reused"],
        requested_root_course_id=ids["root_b"],
    )
    assert root == ids["root_b"]


async def test_null_root_attempts_do_not_consume_limit(graph):
    """Попытка с неизвестным путём не расходует лимит ни в одном корне.

    Это осознанное поведение для старых записей, где корень восстановить нечем
    (на проде таких 7, все у 2 учеников). Оно описано в карточке tsk-264,
    а не замолчано: ученик получает свежие попытки — сторона ошибки выбрана
    в пользу ученика, а не блокировки.
    """
    ids, s = graph
    await _burn_attempts(
        s, ids["user"], ids["task_reused"], ids["reused"],
        None, DEFAULT_MAX_ATTEMPTS,
    )
    for root_key in ("root_a", "root_b"):
        state = await engine_svc.compute_task_state(
            s, ids["user"], ids["task_reused"], root_course_id=ids[root_key]
        )
        assert state.attempts_used == 0, f"попытка без пути не должна считаться в {root_key}"


async def test_quiz_stays_blocked_across_roots(graph):
    """Квиз считается ОБЩЕ, а не по курсам: у него ответ один навсегда.

    Находка ревью tsk-264: submit отклоняет повтор квиза глобально (409,
    QUIZ_TASK_TYPES в attempts.py — задваивать scale_scores нельзя). Если бы счёт
    квиза стал по-курсовым, в соседнем курсе ученику показали бы «попытка есть»,
    он нажал бы «ответить» и получил отказ сервера.
    """
    ids, s = graph
    # Ответ ниже порога в курсе A — единственная попытка квиза израсходована.
    await _burn_attempts(
        s, ids["user"], ids["task_quiz"], ids["reused"], ids["root_a"], 1,
    )
    in_b = await engine_svc.compute_task_state(
        s, ids["user"], ids["task_quiz"], root_course_id=ids["root_b"]
    )
    assert in_b.attempts_used == 1, "счёт квиза обязан быть общим для всех курсов"
    assert in_b.state == "BLOCKED_LIMIT", (
        "иначе SPW предложит ответить повторно, а сервер вернёт 409"
    )


async def test_legacy_call_without_root_counts_globally(graph):
    """Вызов без корня — прежнее поведение: счёт по всем попыткам задания."""
    ids, s = graph
    await _burn_attempts(
        s, ids["user"], ids["task_reused"], ids["reused"],
        ids["root_a"], 2,
    )
    state = await engine_svc.compute_task_state(s, ids["user"], ids["task_reused"])
    assert state.attempts_used == 2
