# tsk-431 (Календарь LMS Фаза 4): GET /students/{student_id}/lesson-reminders/pending

## Контекст

Оператор подтвердил взятие в работу Фазы 4 tsk-021/tsk-431 (TG-дублирование
напоминаний о занятии, `kind='lesson_reminder'`, tsk-429). Разведка со стороны
TG_LMS-сессии показала, что исходное допущение задачи («TG_LMS уже умеет
читать LMS `Notifications` inbox без нового эндпоинта») — **неверно**:

1. `NotificationsService`/`api_client.list_notifications()` в TG_LMS ходит в
   legacy `GET /api/v1/notifications/` (`app/schemas/notifications.py`) —
   схема `{id, content, modified_by, modified_at}`, без `kind/user_id/payload`.
   Читает ту же физическую таблицу `notifications`, но без фильтрации по
   получателю и без inbox-полей M8-миграции.
2. `GET /me/notifications` (Y-4) — `Depends(require_authenticated)` явно
   отвергает сервисный токен (`app/api/deps.py:126-127`: `if
   current_user.is_service: raise 403`).
3. `GET /methodist/escalations/pending` (Y-6, ближайший «похожий» прецедент)
   — service-key bypass ЕСТЬ, но SQL фильтрует `WHERE user_id =
   current_user.id`, а под сервисным токеном `get_current_user` возвращает
   `CurrentUser(id=0, is_service=True)` (`app/api/deps.py:114-117`). Значит
   под сервисным ключом этот эндпоинт **всегда** возвращает пустой список,
   независимо от того, чьи эскалации запрашивались — он спроектирован под
   per-user session/Bearer auth (methodist сам логинится), а не под «один
   бот-ключ читает за многих разных пользователей по очереди». Использовать
   его как образец для НОВЫХ service-key-эндпоинтов нельзя.

Правильный прецедент — `GET /teacher/reviews/pending-count` /
`GET /teacher/help-requests/pending-count`: явный `teacher_id` в query +
`current_user.id == teacher_id OR is_service`. Тот же приём применён здесь,
но с identity-гейтом в стиле `messages_extra.py::get_messages_for_user`
(`if not current_user.is_service and current_user.id != user_id: 403`).

## Реализация

Новый файл `app/api/v1/student_lesson_reminders.py`:
- `GET /api/v1/students/{student_id}/lesson-reminders/pending?since=&limit=`
- Auth: `Depends(get_current_user)` (НЕ `require_authenticated`, НЕ
  role-гейт) + явная проверка `current_user.is_service OR current_user.id
  == student_id` иначе 403.
- `kind` жёстко зафиксирован на `'lesson_reminder'` на уровне SQL — эндпоинт
  называется по задаче, не общий inbox-proxy.
- Ответ — тот же формат, что у `EscalationListResponse`
  (`{items: [{id, created_at, kind, title, payload, read_at}], count}`).
- `read_at` не проставляется этим путём (read-only) — намеренно: пометка
  read'ом из TG погасила бы напоминание в SPW-баннере тоже.

Регистрация роутера — `app/api/main.py` (после `teacher_lesson_occurrences_router`).

## DB Findings

Read-only. Таблица `notifications` (M8-расширенная, `app/models/notifications.py`)
уже содержит `lesson_reminder`-строки от `lesson_attendance_cron_service.py`
(tsk-429) — новых миграций не требуется, только новый способ их прочитать.

## Validation Results

- `tests/test_tsk431_student_lesson_reminders.py` (2 теста): ACL
  (401/403/self-200/service-key-200) + kind-фильтр/чужой-ученик-фильтр/since/limit — **PASS**.
- Полный `pytest` LMS — см. отдельную запись в артефакте TG_LMS-сессии /
  CHANGELOG (прогонялся параллельно с TG_LMS-стороной в этой же сессии).
- `python scripts/export_openapi.py` — 273 → 274 эндпоинта, диф схемы —
  только новый путь (`git diff docs/openapi.json`), без побочных изменений.

## API contract guard (fastapi-api-developer Шаг 4.5)

1. **Hardcoded URLs** — 0 совпадений (эндпоинт не строит URL, только SQL).
2. **IDOR sweep** — `{student_id}` в пути + `current_user.is_service OR
   current_user.id == student_id` иначе 403 + негативный тест
   (`test_lesson_reminders_pending_acl`, ветка «другой ученик → 403»). PASS.
3. **Spec backsync** — `docs/openapi.json` regenerated в этом коммите;
   `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` +
   `CHANGELOG.md` + `STATE.md` обновлены в этой же сессии (см. cross-project
   commit в ContentBackbone).
4. **Schema vs OpenAPI** — ответ теста повторяет именованную схему
   `StudentLessonReminderPendingResponse` (envelope `{items, count}`, не
   голый list).

## Risks / Follow-ups

- Deep-link в TG-сообщении ведёт на страницу `/lessons` (SPW), не на
  конкретное занятие — на момент реализации в SPW нет per-occurrence
  маршрута. Не блокирует exit-criteria tsk-431 (страница показывает список
  занятий ученика, включая упомянутое).
- Живая проверка (реальный TG-аккаунт с привязанным занятием) — выполняется
  в TG_LMS-сессии/артефакте, не здесь (эндпоинт — read-only API-слой).
