# Review — tsk-298 Фаза 2a (LMS enablers для веб-очереди проверки)

**Дата:** 2026-07-19
**Задача:** tsk-298, Фаза 2a — backend-enabler'ы под teacher-портал (очередь проверки в SPW)
**Скилл:** `/fastapi-api-developer` → `/executor-pro`

## Цель

Веб-очередь проверки требует того, чего claim-контракт (заточен под бота) не даёт.
3 аддитивных enabler'а:
1. **teacher-scoped список очереди** (у бота — только claim-next по одной; вебу нужен список).
2. **`attempt_id` в claim-item** (веб строит URL вложений `/attempts/{attempt_id}/attachments/{id}`).
3. **расширение ACL скачивания вложения** на препода-ревьюера (сейчас — только владелец-ученик / service-key; препод по cookie получал 403).

## Changed Files

| Файл | Суть |
|---|---|
| `app/schemas/teacher_next_modes.py` | `ReviewClaimItem.attempt_id` (аддитивно) + новые `PendingReviewItem` / `PendingReviewListResponse`. |
| `app/services/teacher_queue_service.py` | `attempt_id` в item у `claim_next_review` + `claim_review_by_id` (RETURNING `tr.attempt_id`). Новая `list_pending_reviews()` (тот же предикат `mandatory_review_sql`+`REVIEW_ACL_SQL`, read-only, лёгкий item без `answer_json`, флаг `is_claimed`, FIFO, пагинация). Новая `teacher_can_review_attempt()` (ACL: REVIEW_ACL хотя бы на одну задачу попытки). |
| `app/api/v1/teacher_reviews.py` | `GET /teacher/reviews/pending` (teacher_id + course_id?/limit/offset, identity-гейт, ACL-scope в запросе). |
| `app/api/v1/attempts.py` | `download_attempt_attachment`: сверх владельца/сервиса — препод, авторизованный на проверку работы попытки (`teacher_can_review_attempt`). |
| `docs/openapi.json` | Регенерирован (171 endpoint, +`/teacher/reviews/pending`, `ReviewClaimItem.attempt_id`, PendingReview-схемы). |

## DB Findings (MCP, read-only)

- `task_results.attempt_id` (integer) существует → безопасно выводить в RETURNING/item.
- `attempts` — course-level (`user_id`, `course_id`), БЕЗ `task_id` → ACL вложения резолвится через `task_results JOIN tasks WHERE attempt_id=...` + `REVIEW_ACL_SQL`.
- `attempts` минимально требует только `user_id` (остальное — defaults).

## Validation Results

| Критерий | Статус |
|---|---|
| `GET /teacher/reviews/pending` отдаёт очередь + attempt_id + is_claimed | ✅ (тест) |
| 403 при чужом teacher_id | ✅ |
| `attempt_id` в claim-next item | ✅ |
| ACL вложения расширен на препода (не-препод → 403, препод → ACL пройден) | ✅ (endpoint + service-helper) |
| Регрессия | ✅ 19 (review alignment/next-modes) + 21 (help-requests/attachments/requires-attachment) passed |
| openapi обновлён | ✅ (171; diff — только мой путь + схемы, без удалений) |
| bandit | ⚠ 2 B608 (Low confidence) — тот же безопасный f-string-SQL паттерн, что 12× в этом файле (фрагменты — модульные литералы хелперов, значения через bind); новый класс риска не введён |

Тесты: `tests/test_teacher_reviews_pending_tsk298.py` (6).

## Команды валидации
```bash
.venv/Scripts/python.exe -m pytest tests/test_teacher_reviews_pending_tsk298.py tests/test_review_queue_alignment_tsk247.py -q
.venv/Scripts/python.exe scripts/export_openapi.py
```

## Risks / Follow-ups

- **Security (расширение ACL вложения):** препод теперь скачивает вложение чужого ученика, ЕСЛИ авторизован на проверку этой работы (REVIEW_ACL: teacher на course-tree ИЛИ methodist). Точечно, покрыто негатив-тестом (посторонний → 403). Это осознанное расширение по решению оператора (полный MVP + вложения).
- **`by-pending-review` (task_results_extra) без teacher-ACL** — существующий эндпоинт; новый `/teacher/reviews/pending` его не трогает, отдаёт свою teacher-scoped очередь. Разведён.
- **Cross-project:** новый endpoint + изменённый `ReviewClaimItem` + ACL вложения → обновить mirror `lms-api.md` + CHANGELOG (ContentBackbone).
- **Фаза 2b:** SPW-очередь (список → claim → stem+ответ+вложения → grade/regrade) поверх этих enabler'ов.
