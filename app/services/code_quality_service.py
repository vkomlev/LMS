# app/services/code_quality_service.py
"""
Сервис статического анализа качества/стиля кода ученика (tsk-302, направление 1).

Оценивает СТИЛЬ уже принятого кода (магические числа, сложность, длина/число
аргументов функций, читаемость имён) — не корректность (её проверяет
turtle-песочница, tsk-412, через сравнение трассы рисунка).

Видимость результата — ТОЛЬКО teacher/methodist/admin (решение оператора,
tsk-302, 2026-08-06): функция намеренно не встраивается в `CheckResult`,
который эхо-возвращается ученику в ответе `POST /attempts/{id}/answers`
(`AttemptAnswerResult.check_result`). Вызывающая сторона (`app/api/v1/attempts.py`)
кладёт результат напрямую в `metrics` при записи `task_results` — это поле
уже отдаётся только через методист/teacher-эндпоинты
(`GET /task-results/detail/by-user/{user_id}`, `stats/by-task`, `stats/by-course`).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def analyze_student_code_quality(code: str, *, timeout_sec: float = 5.0) -> Optional[Dict[str, Any]]:
    """
    Прогоняет код ученика через статический анализ (pylint/radon) в изоляции
    песочницы turtle_sandbox (tsk-412) и возвращает JSON-совместимый отчёт для
    `task_results.metrics`.

    Синхронная блокирующая функция (subprocess) — вызывающая сторона обязана
    звать через `asyncio.to_thread`, как и `run_student_code`.

    Args:
        code: Исходный код ученика (`answer.response.value`).
        timeout_sec: Таймаут анализа в изолированном процессе.

    Returns:
        None при пустом коде; иначе словарь с отчётом либо `{"error": ..., "message": ...}`
        при сбое анализа (таймаут/авария процесса) — сбой анализа не бросает исключение,
        приём ответа ученика не должен падать из-за побочной метрики.
    """
    if not code.strip():
        return None

    from app.services.turtle_sandbox.executor import run_code_quality_check

    result = run_code_quality_check(code, timeout_sec=timeout_sec)
    if not result.ok:
        logger.info(
            "code_quality: анализ не выполнен (error=%s): %s",
            result.error, result.message,
        )
        return {"error": result.error, "message": result.message}
    return result.report
