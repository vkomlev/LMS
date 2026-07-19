# Review — tsk-298 Фаза 3-Ⅲ (LMS): переписка открыта cookie + фикс ученического разрыва

**Дата:** 2026-07-19 · **Задача:** tsk-298 Фаза 3-Ⅲ (последняя область паритета) · **Скилл:** `/fastapi-api-developer`

## Цель
Открыть веб-порталу core-переписку (inbox/by-user/send/mark-read) с ACL «участник диалога».
**Побочно чинит пред-существующий разрыв:** ученическая переписка `/me/messages` (Y-5.2) на
проде отдавала 403 (эндпоинты были сервис-only), а `inbox`-хук ещё и не слал `user_id` (422).

## Changed Files
| Файл | Суть |
|---|---|
| `app/api/v1/messages_extra.py` | 4 core-эндпоинта `get_db`→`get_current_user`+`get_bare_db`+ACL: `send` (отправитель=сам, `can_message`), `by-user` (own-data гейт + новый `peer_id`), `inbox` (own-data; `user_id` **опционален** → cookie видит свой; чинит ученический хук), `mark-read/by-sender` (own-data). Остальные messages (bulk/admin/forward/attachments) — остаются service. |
| `app/services/messages_service.py` | `can_message` (связь teacher↔student / methodist-admin) + `peer_id`-фильтр 1:1 в `get_messages_for_user`. |
| `docs/openapi.json` | Регенерирован (171; `inbox.user_id` опционален, `by-user.peer_id` добавлен). |

## Ключевое (security)
- **Own-data гейт:** inbox/by-user/mark-read — только `user_id == current_user` (или service). Нельзя читать чужую переписку.
- **send без подмены:** `sender_id` принудительно = current_user (403 при попытке чужого); получатель обязан быть участником (`can_message`) — защита от рассылки.
- **Backward compat:** сервисный токен (TG-бот) → bypass; `inbox` service обязан слать `user_id`. Регрессия `test_inbox_service_y4` зелёная.
- **Фикс ученической переписки:** те же 4 эндпоинта теперь работают по cookie → `/me/messages` оживёт (был двойной разрыв: 403 + 422).

## Validation
| Критерий | Итог |
|---|---|
| inbox/by-user/mark-read own-data (self→200, чужой→403, service→200) | ✅ |
| inbox без user_id (cookie→свой) | ✅ |
| send: linked→201, посторонний→403, подмена sender→403, service→201 | ✅ |
| peer_id 1:1-фильтр | ✅ |
| Регрессия `test_inbox_service_y4` (бот) | ✅ 6 |
| bandit | ✅ 0 новых (`can_message` — text() + binds, без f-string) |

Тесты: `tests/test_messages_cookie_tsk298.py` (10).

## Risks / Follow-ups
- Прочие messages-эндпоинты (thread/{id}, senders, forward, to-students/to-teachers, attachments) — остаются service-only; при необходимости откроются отдельно.
- Cross-project: auth 4 эндпоинтов + фикс ученической переписки → CHANGELOG.
- **Завершает Фазу 3 → tsk-298 (портал преподавателя, полный паритет) готов.**
