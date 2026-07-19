# Review — tsk-298 Фаза 3-Ⅰ (LMS): workload + ростер открыты cookie-преподавателю

**Дата:** 2026-07-19 · **Задача:** tsk-298 Фаза 3 (паритет), подфаза Ⅰ · **Скилл:** `/fastapi-api-developer`

## Цель
Открыть веб-порталу преподавателя (по cookie) два read-only эндпоинта, которые были
сервис-ключ-only (`get_db`, только TG-бот): нагрузка + ростер учеников. Первый шаг Фазы 3
(паритет с ботом), выбран как самый простой/низкорисковый (read-only).

## Changed Files
| Файл | Суть |
|---|---|
| `app/api/v1/teacher_workload.py` | `GET /teacher/workload`: `get_db` → `get_current_user`+`get_bare_db` + identity-гейт (`id==teacher_id` или service). |
| `app/api/v1/student_teacher_links.py` | `GET /users/{teacher_id}/students` (ТОЛЬКО этот хендлер): то же. Остальные (link CRUD) остаются service/admin. |
| `docs/openapi.json` | Регенерирован (171; путей не добавлено — сменились auth+403 на 2 эндпоинтах). |

## Ключевое
- **Backward compat:** сервисный токен (TG-бот через `?api_key=`/X-API-Key) проходит `get_current_user` как `is_service` → identity-гейт bypass. Бот не сломан (тесты подтверждают).
- **ACL:** identity-гейт по `teacher_id`; данные ростера сами scoped через `student_teacher_links`; workload — через `HELP_REQUESTS_ACL_SQL`/`REVIEW_ACL_SQL` в сервисе.
- Открыт ТОЛЬКО ростер-эндпоинт; link-CRUD (`POST/DELETE /users/.../teachers`) остались service/admin — не расширял поверхность.

## Validation
| Критерий | Итог |
|---|---|
| workload/ростер по cookie своего teacher_id → 200 | ✅ |
| по cookie чужого teacher_id → 403 | ✅ |
| по сервисному ключу (бот) → 200 (backward compat) | ✅ |
| ростер отдаёт связанных учеников | ✅ |
| Регрессия (next-modes, reviews-pending) | ✅ 21 passed |
| openapi обновлён | ✅ (без новых путей) |
| bandit | ✅ 0 issues |

Тесты: `tests/test_teacher_workload_roster_cookie_tsk298.py` (6).

## Risks / Follow-ups
- **Фаза 3-Ⅱ:** help-requests (список/ответ/закрыть/override) — тот же enabler-паттерн + решение по blocked_limit.
- **Фаза 3-Ⅲ:** messages (весь роутер) — сложный ACL «участник диалога» + разбор возможного разрыва ученической переписки (тоже сервис-only).
- Cross-project: auth-модель 2 эндпоинтов изменилась → mirror `lms-api.md` + CHANGELOG.
