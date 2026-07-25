# tsk-409 (LMS): ACL-фикс скачивания вложения в истории задания

## Контекст

tsk-409: оператор попросил явную кнопку «показать решение» на карточке истории
задания (портал преподавателя, `TaskHistoryCard`) — оформленный ответ +
комментарий учителя + прикреплённый файл.

## Разведка (главный вывод)

`task_history_service.py` уже отдаёт и комментарий учителя (`tr.metrics->>'comment'`),
и ответ ученика (`tr.answer_json`) — оба уже рендерятся в `TaskHistoryCard.tsx`
(не сырым JSON-дампом, а через `AnswerPreview`). Единственный реальный пробел —
**файл вложения**: он физически уже лежит в том же `answer_json`
(`response.meta.attachments[]`, тот же формат, что кладёт клиент при
`POST /attempts/{id}/attachments`), но карточка истории никогда его не
извлекала и не показывала. Существующий парсер `lib/teacher/attachments.ts`
(`extractAttachments`, tsk-298 Фаза 2b) уже умеет это делать — используется в
`ReviewGradePanel.tsx`. **Backend-схему менять не пришлось** — вся правка
только в SPW (см. парный SPW-артефакт `2026-07-26-tsk409-task-history-solution-button.md`).

## Найденный побочный дефект (ACL mismatch)

При добавлении ссылки на вложение в карточку истории обнаружился реальный
пробел авторизации:

- Карточка истории гейтится `manual_progress_service.can_edit_progress`:
  учитель проходит, если ученик закреплён за ним через `student_teacher_links`
  **ИЛИ** курс задания попадает под его `teacher_course_acl`.
- Скачивание вложения (`GET /attempts/{id}/attachments/{id}`) гейтится
  `teacher_queue_service.teacher_can_review_attempt` → `REVIEW_ACL_SQL`, которая
  проверяет **только** `teacher_course_acl` **ИЛИ** роль `methodist` — без
  ветки `student_teacher_links`.

Итог: преподаватель, закреплённый за учеником напрямую (без course-tree ACL),
уже мог открыть карточку истории (видел бы новую кнопку/ссылку на файл), но
получал бы 403 при попытке скачать сам файл. Это пре-существующий разрыв
между двумя ACL-функциями (tsk-298 vs tsk-297/349), ранее не проявлявшийся,
потому что вложения были доступны только из очереди проверки (там ACL и
so-и-нужный охват совпадали). Моя фича впервые делает эту ссылку видимой из
пути с более широким ACL — фикс необходим, иначе кнопка вела бы часть
преподавателей в 403.

## Изменения

`app/services/teacher_queue_service.py` — `teacher_can_review_attempt`:
добавлена ветка `EXISTS (... student_teacher_links ...)`, теми же bind-параметрами
(`:teacher_id`), в том же стиле, что и у `can_edit_progress`. `REVIEW_ACL_SQL`
(используется claim-next/pending-list) не тронут — расширение только у этой
одной функции, чтобы не менять состав очереди проверки для других экранов.

## Тесты

`tests/test_teacher_reviews_pending_tsk298.py`:
- новый `test_attachment_acl_allows_directly_linked_teacher` — teacher без
  course-tree ACL: `False` до привязки `student_teacher_links`, `True` после.
- существующий `test_attachment_acl_service_helper` (methodist bypass /
  посторонний → False) не тронут, остаётся зелёным.

## Validation Commands

```
.venv/Scripts/python.exe -m pytest tests/test_teacher_reviews_pending_tsk298.py -q
.venv/Scripts/python.exe -m pytest tests/test_task_history_tsk349.py tests/test_attempt_attachments.py -q
.venv/Scripts/python.exe -m pytest tests/ -q
```

Результат: 7/7, 15/15, полный набор — 962 passed, 10 skipped, 0 failed.

## DB Findings

Read-only sanity через `learn_prod_db` MCP — найдена реальная попытка с
одновременно комментарием и вложением (для живого прогона):
`task_result_id=2257, user_id=4497, task_id=7458, attempt_id=547, course_id=1253`.
Никаких записей в БД не выполнялось.

## Живая проверка (после деплоя)

Задеплоено на прод (`d08a9b3`, `sudo -u app deploy.sh`, `/health` → `{"status":"ok"}`).
Под учётной записью id=2 (`victor.komlev@mail.ru`, teacher/admin/methodist) открыта
карточка истории `task_result_id=2257` (задача 7458, ученик 4497, попытка 547) —
раскрыты ответ («4»), вложение (`docx, 11 КБ`) и комментарий («Отлично! Принято!»)
одновременно. Прямой `fetch` на `/api/v1/attempts/547/attachments/547_...` из
авторизованной сессии → `200`, `content-length: 11377` (совпадает с «11 КБ» в UI).
Живой прогон подтверждает: и фикс ACL, и SPW-кнопка работают на реальных данных.

## Risks / Follow-ups

- ACL-расширение read-only и точечное (одна функция, один OR-branch) —
  не меняет состав очереди проверки/pending-list для других поверхностей.
- `require_role("teacher","methodist","admin")` на самом эндпоинте истории
  шире, чем `REVIEW_ACL_SQL`/`teacher_can_review_attempt` (роль `admin` не
  бай-пасится ни там, ни там) — предсуществующий (до этой задачи) разрыв,
  не в скоупе tsk-409, отдельная находка ниже.
