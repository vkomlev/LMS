"""ИИ-наставник ученика (tsk-572 этап 2).

Публичное: сборка промпта без эталона и сервис сессий диалога.
Транспорт к модели — `app.services.llm` (этап 1), тут только педагогика и данные.
"""
from app.services.ai_tutor.prompt import (
    STUDENT_DATA_CLOSE,
    STUDENT_DATA_OPEN,
    TutorMode,
    TutorTaskView,
    build_context_block,
    build_opening_user_message,
    build_system_prompt,
    pick_mode,
)

__all__ = [
    "TutorTaskView",
    "TutorMode",
    "pick_mode",
    "build_system_prompt",
    "build_context_block",
    "build_opening_user_message",
    "STUDENT_DATA_OPEN",
    "STUDENT_DATA_CLOSE",
]
