# tsk-031 (хвост) — UI-кнопка «Назначить курс»: review-gate

## Контекст

tsk-031 «Доп курсы для закрепления» держалась на одном открытом пункте декомпозиции:
UI-кнопка ручного назначения курса в портале преподавателя (backend готов с 2026-06-24).

Перед UI-работой — обязательная read-only разведка правил на проде (см. tsk-031):
подтверждено, что автоназначение по правилам (требование 1, ADR-0002) реально работает,
не заглушка. 6 активных правил соответствуют требованиям оператора (SC-пробный → вводный
курс трека; провал вводного → курс повторения), дважды реально сработали
(`assignment_event` id 45/46, зачисление подтверждено в `user_courses`, ученик 142,
2026-06-28/29). Побочная находка: одно ручное тестовое назначение (id 47, курс
`wp:informatika-5-11`) сейчас отсутствует в `user_courses` — похоже на тестовую отписку
после ручной проверки через curl, не относится к механизму назначения, не чинилось (вне
скоупа, не блокер).

## Изменения

### LMS backend

- `app/api/v1/teacher_assignments.py`: новый read-only эндпоинт
  `GET /teacher/courses/search?q=&limit=` — поиск курса по `title`/`course_uid` для
  UI-селектора. Гейт `require_role("teacher","methodist","admin")` (cookie-сессия учителя
  из браузера — существующий `GET /courses/search` в `courses_extra.py` требует
  X-API-Key и недоступен из SPW). Переиспользует `courses_service.search_text`
  (параметризованный ILIKE, тот же паттерн, что и у `/courses/search`).
- `docs/openapi.json` регенерирован (`scripts/export_openapi.py`) — diff чистый, только
  новый эндпоинт.
- `docs/ai/adr/0002-course-assignment-trigger-rules.md`: добавлен §6 (новый эндпоинт),
  follow-up «UI-кнопка в SPW» отмечен закрытым, статус ADR обновлён.
- Тесты (`tests/test_assignment_rules_tsk031.py`, +3): поиск по title (роль teacher через
  Bearer-сессию), поиск по `course_uid` (сервисный токен), 403 без роли (роль `customer`).
  Найден и обойдён footgun теста: `get_current_user` self-heal для role-less пользователя
  пишет `audit_event` (append-only, `ON DELETE SET NULL` бьётся об её же UPDATE/DELETE-
  триггер) — cleanup ломался бы `DELETE FROM users`; тест явно назначает роль `customer`
  вместо role-less пользователя, избегая self-heal.

### SPW frontend

- `components/teacher/CourseAssignButton.tsx` (новый) — кнопка «Назначить курс»,
  дебаунс-поиск (паттерн `TaskSearchBox`, tsk-353) → выбор → подтверждение.
  Идемпотентность видна на UI: `already_enrolled=true` → «уже назначен», не ошибка.
  ACL клиентом не проверяется заранее (тот же паттерн, что у остальных мутаций
  `StudentProgress`) — 403 сервера показывается как inline feedback.
- `lib/teacher/use-course-assignment.ts` (новый) — `useCourseSearch` + `useAssignCourse`.
- `components/teacher/StudentProgress.tsx` — кнопка встроена рядом с `TaskSearchBox`.
- `lib/api-types.ts` регенерирован (`openapi-typescript` из обновлённого openapi.json).
- Тесты: `tests/unit/course-assign-button.test.tsx` (новый, 8 сценариев) +
  `tests/unit/student-progress.test.tsx` (мок нового хука, чтобы встроенная кнопка не
  лезла в сеть в существующих тестах).

### Cross-project memory (ContentBackbone)

- `docs/cross-project/contracts/lms-api.md` — добавлен `GET /teacher/courses/search`.
- `docs/cross-project/CHANGELOG.md` — запись в начале.
- `docs/cross-project/STATE.md` — секции LMS и SPW обновлены.

## Валидация

- LMS pytest (teacher/assignment подмножество): **102 passed** (включая новые 3).
- LMS pytest (файл tsk-031 целиком): **17 passed**.
- SPW vitest (полный набор): **531 passed**.
- SPW `tsc --noEmit`: чисто.
- `docs/openapi.json` / `lib/api-types.ts` diff: только новый эндпоинт, без посторонних
  правок (проверено — общий рабочий каталог с параллельными сессиями).

## Review-gate: ПРИНЯТО

12 измерений пройдены (детали — в ответе ревью). Cross-project memory (измерение 11) и
Public API Contract Sync (измерение 12) закрыты в этой же сессии — без них решение было
бы автоматическим ОТКЛОНЕНО.

## Risks / Follow-ups

- Живой прогон на проде под ролью учителя — после этого ревью, перед закрытием задачи.
  **Выполнено**: назначение курса 142 → «Вводная информатика» прошло (`assignment_event`
  id 51, `assigned_by=2`), повтор корректно показал «уже назначен», без дубля в
  `user_courses`/`assignment_event` (проверено прямым запросом к прод-БД).
- `assignment_event` id 47 (несовпадение с `user_courses`) — не чинилось, вне скоупа,
  зафиксировано как наблюдение.

## Hotfix (тот же день): поиск отдавал подкурсы

Оператор поймал сразу после деплоя на живом прогоне: `GET /teacher/courses/search`
искал по всему графу курсов через общий `search_text`, включая подкурсы
(`course_parents`). Подкурс не открывается ученику вне родительского курса — назначать
его отдельно нельзя.

**Фикс:** `courses_service.search_root_courses` — тот же `outerjoin(course_parents) ...
WHERE course_id IS NULL` фильтр, что и в уже проверенном `get_root_courses`.
`GET /courses/search` (`courses_extra.py`) не менялся — ищет по всему графу, как раньше
(его потребители — не эта задача). Regression-тест `test_course_search_excludes_subcourse`
(root+sub курсы с общим маркером в title, проверка что в выдаче только root).

Прошёл повторный `/review-gate` (диф отдельно проверен на корректность SQL-фильтра,
отсутствие регрессии в `/courses/search`, достаточность теста, отсутствие инъекции —
экранирование скопировано из `BaseRepository.search_text`). Задеплоено (LMS `26405e3`).
Живая проверка: подкурс «5.1. Социальная информатика и информационное общество»
(`wp:inf-11-g5-t1`), ранее видимый в выдаче по запросу «информатика», больше не
появляется — только корневые курсы (`wp:inf-5`…`wp:inf-11`, `wp:vvodnaya-informatika` и т.д.).
