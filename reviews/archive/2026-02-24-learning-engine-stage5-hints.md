# Review: Learning Engine этап 5 — Hints

**Дата:** 2026-02-24

**Контекст:** Реализация этапа 5 по ТЗ: отдача в API задач полей hints_text, hints_video, has_hints из task_content в едином backward-compatible формате.

**Изменения:**
- **app/schemas/tasks.py**: функция **extract_hints_from_task_content(task_content)** — извлекает и нормализует массивы (только строки), возвращает (hints_text, hints_video, has_hints); при отсутствии/невалидном типе — [], [], False. В **TaskRead** добавлены поля **hints_text**, **hints_video**, **has_hints** (default_factory/list, False) и **model_validator(mode="after")**, заполняющий их из task_content при model_validate.
- **tests/test_hints_stage5.py**: тесты extract_hints (пустые, валидные, нормализация), TaskRead с hints и без.
- **docs/hints-stage5.md**: контракт, правила извлечения, целевые эндпоинты, примеры.
- **docs/learning-engine-v1-implementation-plan.md**: этап 5 отмечен как **done**.

Все ответы задач (GET по id/external_uid, list by-course, search), использующие TaskRead, автоматически получают заполненные hints за счёт валидатора. Дополнительных правок в эндпоинтах не требуется.

Полный diff (схемы): [2026-02-24-learning-engine-stage5-hints.diff](./2026-02-24-learning-engine-stage5-hints.diff)
