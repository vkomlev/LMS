# tsk-1004: вложение к ОТВЕТУ преподавателя на заявку помощи

## Контекст

Оператор 19.09: у ученика в запросе помощи есть вложение (tsk-943), у ответа
преподавателя — не было вовсе. Инфраструктура (namespace `attachment_storage.HELP_REQUESTS`,
поля `messages.attachment_url`/`attachment_id`) уже существовала — реализация точечная.

## Изменённые файлы (LMS)

- `app/schemas/teacher_help_requests.py` — `HelpRequestReplyRequest.attachment_id`,
  `HelpRequestReplyItem.attachment_id`/`attachment_url`
- `app/schemas/learning_api.py` — `StudentHelpReplyItem.message_id`/`attachment_url`
- `app/services/help_requests_service.py` — `reply_help_request` принимает
  `attachment_id` и пишет `messages.attachment_id`; новая
  `get_help_request_reply_attachment`; JOIN `messages` в `get_help_request_detail`
  и `get_student_help_request`
- `app/api/v1/teacher_help_requests.py` — новый
  `GET /{request_id}/replies/{message_id}/attachment`; `attachment_id` прокинут в reply
- `app/api/v1/learning.py` — новый
  `GET /help-requests/{request_id}/replies/{message_id}/attachment`
- `docs/openapi.json` — регенерирован
- `tests/test_tsk1004_help_request_reply_attachment.py` — новый (3 теста)

## Изменённые файлы (SPW, отдельный коммит)

- `lib/api-types.ts` — регенерирован
- `lib/teacher/use-help-requests.ts` — `useReplyHelpRequest` принимает `attachmentId`
- `components/teacher/HelpRequestInlineReply.tsx` — поле загрузки файла (по
  образцу `HintPanel.tsx`)
- `components/teacher/HelpRequestPanel.tsx` — ссылка на вложение в `history[]`
- `components/task/HelpLadderPanel.tsx` — ссылка на вложение в `lastReply`
- 4 тестовых файла — новые/дополненные проверки + моки нового хука

## Дизайн-решение

Upload переиспользован как есть (`POST /learning/help-requests/attachments`,
без ролевого гейта) — не заведён отдельный teacher-эндпоинт. `attachment_url`
НЕ хранится в БД (только `attachment_id`) — вычисляется на чтении под
конкретную аудиторию: у вопроса и ответа разные download-эндпоинты
(`/teacher/help-requests/...` и `/learning/help-requests/...`).

## Валидация

- LMS: `pytest tests/test_tsk1004_help_request_reply_attachment.py
  tests/test_tsk943_help_request_attachments.py tests/test_help_ladder_teacher_tsk303.py
  tests/test_help_ladder_endpoints_tsk303.py tests/test_teacher_help_requests_stage38.py
  tests/test_learning_api_routes.py tests/test_tsk592_help_claim_visibility.py
  tests/test_help_requests_pending_count_tsk348.py` — **73 passed**
- SPW: `vitest run tests/unit/help-ladder-panel.test.tsx tests/unit/help-request-panel.test.tsx
  tests/unit/student-progress.test.tsx tests/unit/feedback-and-kpi.test.tsx` — **105 passed**
- SPW: `tsc --noEmit -p .` — без ошибок
- Hardcoded URL guard: 0 совпадений в изменённых файлах
- Живая проверка на dev-стенде (localhost:3000+8000): преподаватель
  (user_id=2) прикрепил `tsk1004-live-check.png` и ответил на реальную
  заявку (request_id=38542, message_id=9678); в БД подтверждён
  `messages.attachment_id = "2_10c7fafef7eb4144becf343e9a40698e_tsk1004-live-check.png"`;
  ученик (user_id=142) увидел ссылку «Вложение преподавателя»
  (`/api/v1/learning/help-requests/38542/replies/9678/attachment`) и скачал
  файл — 200, `image/png`, 68 байт (совпадает с загруженным).

## Cross-project

`D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` +
`CHANGELOG.md` обновлены и закоммичены (`f882415`).

## Review-gate

ПРИНЯТО — блокирующих находок нет. Non-blocking: скачиваемое имя файла у
вложения ответа — сырой ключ хранилища (`messages` не хранит
`attachment_filename`), тот же класс ограничения уже есть у общего
`/messages/{id}/attachment`, не регрессия этой задачи.

## Rollback

`git revert` коммита LMS + коммита SPW; `openapi.json`/`api-types.ts`
регенерировать заново после отката. Cross-project коммит в ContentBackbone
откатить отдельно при необходимости.
