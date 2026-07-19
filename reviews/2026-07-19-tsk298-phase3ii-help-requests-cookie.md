# Review — tsk-298 Фаза 3-Ⅱ (LMS): help-requests + override открыты cookie-преподавателю

**Дата:** 2026-07-19 · **Задача:** tsk-298 Фаза 3-Ⅱ · **Скилл:** `/fastapi-api-developer`

## Цель
Открыть веб-порталу запросы помощи (список/карточка/ответ/закрыть/освободить/claim-next) +
переопределение лимита попыток (для blocked_limit). Были сервис-ключ-only.

## Changed Files
| Файл | Суть |
|---|---|
| `app/api/v1/teacher_help_requests.py` | Все 6 эндпоинтов: `get_db`→`get_current_user`+`get_bare_db` + identity-гейт (teacher_id/closed_by). ACL уже был в сервисе (`can_access_help_request`/`HELP_REQUESTS_ACL_SQL`). |
| `app/api/v1/teacher_learning.py` | `POST /teacher/task-limits/override`: cookie + identity (updated_by) + **новый ACL** `teacher_can_override_limit` (закрыт старый TODO — write без ACL). |
| `app/services/teacher_queue_service.py` | `teacher_can_override_limit` (teacher на course-tree задачи ИЛИ свой ученик ИЛИ methodist/admin). |
| `docs/openapi.json` | Регенерирован (171; путей не добавлено). |

## Ключевое
- **Backward compat:** сервисный токен бота (`?api_key=`/X-API-Key) → `is_service` → identity+ACL bypass. Бот не сломан (регрессия stage38 зелёная).
- **Override получил ACL:** ранее write без проверки (TODO). Теперь non-service обязан быть updated_by=self И авторизован на задачу ученика — иначе 403.
- **blocked_limit reply/close** backend по-прежнему разрешает (это UI-политика); веб (Фаза 3-Ⅱ SPW) зеркалит бот (R4): для blocked_limit показывает только override.

## Validation
| Критерий | Итог |
|---|---|
| help-requests list: cookie self→200, чужой→403, service→200 | ✅ |
| override: methodist→200, чужой updated_by→403, teacher без ACL→403, linked-student→200, service→200 | ✅ |
| Регрессия stage38 (бот) | ✅ |
| openapi | ✅ (без новых путей) |
| bandit | ⚠ 2 B608 (Low, тот же безопасный file-wide f-string паттерн: модульные литералы + binds) |

Тесты: `tests/test_teacher_help_requests_override_cookie_tsk298.py` (9).

## Risks / Follow-ups
- Фаза 3-Ⅲ: messages (весь роутер, ACL «участник диалога») + разбор ученической переписки.
- Cross-project: auth-модель help-requests+override → CHANGELOG.
