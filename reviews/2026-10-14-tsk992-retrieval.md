# tsk-992 (П3): повторение по следам ошибок — Фаза B

Контекст: постановка `docs/briefs/2026-10-14-p3-p5-retrieval-help-gate-final-check.md`, шаги 6–12.
Решения оператора Р2 (что и когда повторять) и Р3 (блок над программой курса).

- Миграция `tsk992_task_review_schedule` (down: `tsk1335_pd_consents`; перестроена после того, как tsk-1335 попал в main): таблица `task_review_schedule`, колонка `courses.retrieval_config`.
- Рубильник `GET/PUT /courses/{id}/retrieval` (методист/админ), по умолчанию выключен.
- Планировщик в приёме ответа (`attempts.py`, блок 2.4c'), нестрогий.
- `GET /learning/courses/{id}/review-due`, `POST /learning/courses/{id}/review/{task_id}/answer` (тонкий эндпоинт, `source_system='retrieval'`).
- Исключение `'retrieval'` из статуса задания, счёта попыток и «пройдено» — помощник `learning_path_results_filter`.

Дифф: `reviews/2026-10-14-tsk992-retrieval.diff`. Ревью: `reviews/2026-10-14-tsk992-retrieval-review.md`.
