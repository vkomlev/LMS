# -*- coding: utf-8 -*-
"""
tsk-230: флаг SolutionRules.manual_review_required для SA/SA_COM.

Контракт (glossary): при manual_review_required=true задача НЕ получает
авто-вердикт — уходит в очередь ручной проверки (is_correct=None, score=0),
даже если ответ совпал бы с accepted_answers. При false — поведение прежнее
(авто-проверка по слову).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.checking import StudentAnswer, StudentResponse
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import TaskContent
from app.services.checking_service import CheckingService

_cs = CheckingService()


def _sa_com_task(manual_review_required: bool):
    tc = TaskContent.model_validate({"type": "SA_COM", "stem": "Ответь одним словом"})
    sr = SolutionRules.model_validate(
        {
            "max_score": 1,
            "manual_review_required": manual_review_required,
            "short_answer": {"accepted_answers": [{"value": "прод", "score": 1}]},
        }
    )
    return tc, sr


def test_sa_com_manual_required_defers_verdict():
    """manual_review_required=true: даже верный ответ → is_correct=None, score=0 (в очередь)."""
    tc, sr = _sa_com_task(manual_review_required=True)
    res = _cs.check_task(
        tc, sr, StudentAnswer(type="SA_COM", response=StudentResponse(value="прод"))
    )
    assert res.is_correct is None
    assert res.score == 0
    assert res.max_score == 1


def test_sa_com_manual_required_wrong_answer_still_deferred():
    """manual_review_required=true: неверный ответ тоже не заваливается авто, а ждёт проверки."""
    tc, sr = _sa_com_task(manual_review_required=True)
    res = _cs.check_task(
        tc, sr, StudentAnswer(type="SA_COM", response=StudentResponse(value="мимо"))
    )
    assert res.is_correct is None
    assert res.score == 0


def test_sa_com_default_auto_check_correct():
    """manual_review_required=false (default-поведение): верный ответ авто-зачитывается."""
    tc, sr = _sa_com_task(manual_review_required=False)
    res = _cs.check_task(
        tc, sr, StudentAnswer(type="SA_COM", response=StudentResponse(value="прод"))
    )
    assert res.is_correct is True
    assert res.score == 1


def test_sa_com_default_auto_check_wrong():
    """manual_review_required=false: неверный ответ авто-заваливается (как раньше)."""
    tc, sr = _sa_com_task(manual_review_required=False)
    res = _cs.check_task(
        tc, sr, StudentAnswer(type="SA_COM", response=StudentResponse(value="мимо"))
    )
    assert res.is_correct is False
    assert res.score == 0
