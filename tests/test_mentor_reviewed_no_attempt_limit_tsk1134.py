"""
Лимит попыток не действует на задания, которые проверяет наставник (tsk-1134).

Итоговый проект курса «Аналитик данных» (SA_COM, manual_review_required=true)
сдаётся в три этапа, правило оценки обещает «доработка — сдаёте снова». При
лимите 3 ученик после двух возвратов оказывался заблокирован (409 на отправке,
BLOCKED_LIMIT в оглавлении). Решение оператора 2026-09-26: для заданий с ручной
проверкой лимит не применяется вовсе; автоматические задания — без изменений.

Проверяем на НАСТОЯЩЕЙ БД — те же пути, что читает приём ответа и оглавление:
- compute_task_state и compute_task_states_batch: наставническое задание после
  3 возвратов — FAILED (не BLOCKED_LIMIT), attempts_unlimited=True;
- автоматическое задание после 3 неверных — по-прежнему BLOCKED_LIMIT (регресс);
- статус оглавления (_compute_syllabus_task_status) — тот же вывод.
"""
import os
import sys
import uuid
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
    is_mentor_reviewed,
)
from app.services.me_service import _compute_syllabus_task_status

settings = Settings()
engine_svc = LearningEngineService()
attempts_svc = AttemptsService()


@pytest.mark.parametrize(
    "task_type, mrr, expected",
    [
        ("TA", None, True),
        ("SA_COM", "true", True),
        ("SA_COM", True, True),
        ("TBL_COM", "true", True),
        ("SA", "true", True),
        ("SA_COM", "false", False),
        ("SA_COM", None, False),
        ("SA", None, False),
        ("SC", "true", False),
        ("SC_Qw", "true", False),
        (None, None, False),
    ],
)
def test_is_mentor_reviewed(task_type, mrr, expected) -> None:
    """Признак «проверяет наставник» — по типу и manual_review_required."""
    assert is_mentor_reviewed(task_type, mrr) is expected


def _syllabus_row(task_type: str, mrr: bool, used: int) -> dict:
    """Строка оглавления с последним неверным ответом (возврат на доработку)."""
    return {
        "last_submitted_at": "2026-09-26",
        "last_is_correct": False,
        "last_checked_at": "2026-09-26",
        "last_score": 0,
        "last_max_score": 10,
        "attempts_used": used,
        "attempts_limit_effective": DEFAULT_MAX_ATTEMPTS,
        "has_open_attempt": True,
        "task_type": task_type,
        "manual_review_required": mrr,
    }


def test_syllabus_status_mentor_reviewed_never_blocked() -> None:
    """Оглавление: проект после 3 возвратов — failed, автозадание — blocked_limit."""
    assert _compute_syllabus_task_status(_syllabus_row("SA_COM", True, 5)) == "failed"
    assert _compute_syllabus_task_status(_syllabus_row("TA", False, 5)) == "failed"
    assert _compute_syllabus_task_status(_syllabus_row("SA", False, 3)) == "blocked_limit"


@pytest_asyncio.fixture(scope="function")
async def course():
    """Курс с проектом (наставник) и автозаданием + студент. Уборка за собой."""
    engine = create_async_engine(settings.database_url)
    ids: dict[str, int] = {}
    async with AsyncSession(engine, expire_on_commit=False) as s:
        try:
            ids["course"] = (
                await s.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES ('tsk1134 курс', 'self_guided') RETURNING id"
                    )
                )
            ).scalar()
            difficulty_id = (
                await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
            ).scalar()
            assert difficulty_id is not None, "нет difficulties — курс не собрать"
            run_uid = uuid.uuid4().hex[:12]

            async def new_task(tc: str, sr: str, uid: str) -> int:
                return (
                    await s.execute(
                        text(
                            "INSERT INTO tasks (task_content, solution_rules, course_id, "
                            "difficulty_id, external_uid) VALUES (CAST(:tc AS jsonb), "
                            "CAST(:sr AS jsonb), :cid, :did, :uid) RETURNING id"
                        ),
                        {"tc": tc, "sr": sr, "cid": ids["course"],
                         "did": difficulty_id, "uid": uid},
                    )
                ).scalar()

            ids["task_project"] = await new_task(
                '{"type": "SA_COM", "question": "tsk1134 проект"}',
                '{"manual_review_required": true}',
                f"tsk1134-project-{run_uid}",
            )
            ids["task_auto"] = await new_task(
                '{"type": "SA", "question": "tsk1134 авто"}',
                '{"correct_answer": {"value": "42"}}',
                f"tsk1134-auto-{run_uid}",
            )
            ids["user"] = (
                await s.execute(
                    text("INSERT INTO users (full_name) VALUES ('tsk1134 ученик') RETURNING id")
                )
            ).scalar()
            await s.execute(
                text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
                {"u": ids["user"], "c": ids["course"]},
            )
            await s.commit()
            yield ids, s
        finally:
            await s.rollback()
            u = ids.get("user", -1)
            for tbl in ("task_results", "attempts", "user_courses"):
                await s.execute(text(f"DELETE FROM {tbl} WHERE user_id = :u"), {"u": u})
            await s.execute(
                text("DELETE FROM tasks WHERE id = ANY(:t)"),
                {"t": [ids[k] for k in ("task_project", "task_auto") if k in ids]},
            )
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": u})
            await s.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids.get("course", -1)})
            await s.commit()
            await engine.dispose()


async def _return_for_rework(s: AsyncSession, ids: dict, task_id: int, count: int) -> None:
    """Записать `count` отклонённых работ (возврат на доработку / неверный ответ)."""
    for _ in range(count):
        attempt = await attempts_svc.create_attempt(
            s, user_id=ids["user"], course_id=ids["course"],
            root_course_id=ids["course"], source_system="test_tsk1134",
        )
        await s.execute(
            text(
                "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
                "is_correct, submitted_at, checked_at) VALUES "
                "(:u, :t, :a, 0, 10, false, now(), now())"
            ),
            {"u": ids["user"], "t": task_id, "a": attempt.id},
        )
    await s.commit()


async def test_project_not_blocked_after_rework_returns(course):
    """Проект: 4 возврата на доработку — сдавать снова можно, лимит не действует."""
    ids, s = course
    await _return_for_rework(s, ids, ids["task_project"], DEFAULT_MAX_ATTEMPTS + 1)

    st = await engine_svc.compute_task_state(
        s, ids["user"], ids["task_project"], root_course_id=ids["course"]
    )
    assert st.attempts_used == DEFAULT_MAX_ATTEMPTS + 1
    assert st.state == "FAILED", "наставническое задание не должно блокироваться лимитом"
    assert st.attempts_unlimited is True

    batch = await engine_svc.compute_task_states_batch(
        s, ids["user"], [ids["task_project"]], root_course_id=ids["course"]
    )
    assert batch[ids["task_project"]].state == "FAILED"
    assert batch[ids["task_project"]].attempts_unlimited is True


async def test_auto_task_still_blocked_by_limit(course):
    """Регресс: автоматическое задание после 3 неверных — BLOCKED_LIMIT, как раньше."""
    ids, s = course
    await _return_for_rework(s, ids, ids["task_auto"], DEFAULT_MAX_ATTEMPTS)

    st = await engine_svc.compute_task_state(
        s, ids["user"], ids["task_auto"], root_course_id=ids["course"]
    )
    assert st.state == "BLOCKED_LIMIT"
    assert st.attempts_unlimited is False

    batch = await engine_svc.compute_task_states_batch(
        s, ids["user"], [ids["task_auto"]], root_course_id=ids["course"]
    )
    assert batch[ids["task_auto"]].state == "BLOCKED_LIMIT"
