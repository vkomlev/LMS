# tsk-1005: история выдачи и исполнения ДЗ на карточке ученика (кабинет методиста)

## Контекст

Оператор 19.09: в кабинете методиста на карточке ученика нужна история
выдачи и исполнения ДЗ — для тюнинга движка выдачи (tsk-741). Данные
(`homework_assignment`/`homework_item`) существуют с tsk-741, витрины не
было: `GET .../homework` отдаёт только действующую выдачу.

## Изменённые файлы (LMS)

- `app/services/homework_service.py` — новая `get_history` (вся история,
  включая отменённые, новые сверху); общий хвост `_to_homework_dict`
  вынесен из `get_current`, чтобы подсчёт `done`/`planned_minutes`/
  `is_overdue` не разъехался между действующей выдачей и её записью в истории
- `app/schemas/homework.py` — `HomeworkHistoryItemRead` (= `HomeworkRead` +
  `cancelled_at`), `HomeworkHistoryResponse`
- `app/api/v1/homework.py` — новый
  `GET /teacher/students/{student_id}/homework/history`, тот же ACL
  (`require_role("teacher","methodist","admin")` + `ensure_can_edit_progress`),
  что у соседних ручек ДЗ в этом же файле
- `docs/openapi.json` — регенерирован
- `tests/test_tsk1005_homework_history.py` — новый (8 тестов: сервисный
  слой + HTTP-эндпоинт с ACL)

## Изменённые файлы (SPW, отдельный коммит)

- `lib/api-types.ts` — регенерирован
- `lib/teacher/use-student-homework.ts` — `useStudentHomeworkHistory`
- `components/methodist/PersonDetail.tsx` — секция «История домашних
  заданий» (`HomeworkHistoryBlock`/`HomeworkHistoryEntryCard`); пункты-
  задания кликабельны и открывают `TaskHistorySheet` (tsk-349, тот же
  компонент, что у `StudentProgress.tsx` учителя) — новый формат не заводился
- `tests/unit/methodist-people.test.tsx` — 5 новых тестов + заглушка
  `/homework/history` в общем моке карточки

## Отдельное решение (AskUserQuestion)

Пункт декомпозиции «поправить `ProgressLink`» — НЕ сделан осознанно.
Разведка нашла: сама зона `/teacher/*` на сервере
(`app/(teacher)/layout.tsx:41`) редиректит любого без личной роли `teacher`
на `/me/continue`. Ссылка в `PersonDetail.tsx` уже корректно отражает эту
границу (подтверждено существующим тестом
`tests/unit/methodist-people.test.tsx` — «методисту без роли преподавателя
ссылку на прогресс не показываем»). Оператор подтвердил: оставить как есть,
секция истории ДЗ методисту доступна и без этой ссылки.

## Валидация

- LMS: `pytest tests/test_tsk1005_homework_history.py
  tests/test_tsk741_homework.py tests/test_homework_minutes_tsk867.py
  tests/test_tsk798_program_scope.py` — **124 passed**
- SPW: `vitest run tests/unit/methodist-people.test.tsx
  tests/unit/methodist-people-edit.test.tsx
  tests/unit/methodist-write-first.test.tsx
  tests/unit/methodist-design-canon.test.tsx
  tests/unit/student-progress.test.tsx` — **91 passed**
- SPW: `tsc --noEmit -p .` — без ошибок
- Hardcoded URL guard: 0 совпадений

## Живая проверка

На dev-стенде маршрут `/methodist/people/{id}` уткнулся в пред-существующий
(не относится к этой задаче) баг Next.js dev-rewrite: `GET
/api/v1/users/{id}/roles/` (trailing slash) на dev-сервере отвечает
редиректом на абсолютный upstream-URL вместо прозрачного прокси, браузер
блокирует его CSP `connect-src 'self'`. На проде эти пути обслуживает nginx
напрямую (см. комментарий в `next.config.ts` — Next-rewrite там даже не
задействован), поэтому живая проверка сделана сразу на проде — см. историю
задачи в Root-трекере.

## Rollback

`git revert` коммита LMS + коммита SPW; `openapi.json`/`api-types.ts`
регенерировать заново после отката.
