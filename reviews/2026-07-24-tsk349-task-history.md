# tsk-349 — История выполнения задания: попытки, комментарии, помощь, подсказки (ученику и учителю)

**Дата:** 2026-07-24 · **Профиль:** `/fastapi-api-developer` (LMS) + `/executor-pro`/`/gstack` (SPW)
**Data Impact:** read (2 новых read-only эндпоинта, миграций нет)

## Контекст и цель

Ни ученик, ни учитель не видели полную историю по конкретному заданию — только
агрегаты. Задача: детальная карточка «всё по одному заданию у одного ученика».
- **Ученик** (экран задания): свои попытки, комментарии учителя, свои обращения за помощью.
- **Учитель** (клик по заданию в карточке ученика): вся история + диалог помощи +
  полное условие + **правильный ответ / правило проверки** (раньше учителю негде было увидеть).
- **Строго:** эталон и правило проверки — только учителю (класс answer-in-stem, tsk-254).

## Грунт переиспользован (не строил заново)

- ACL портала — `manual_progress_service.can_edit_progress` (tsk-297), тот же, что у правки прогресса.
- Диалог заявок помощи — паттерн `help_requests` + `help_request_replies` (tsk-336/303).
- Подсказки — `learning_events_service.get_hint_open_counts(user_id=, task_id=)`.
- Эталон — `checking_service.build_solution_rules` + `solution_rules.short_answer.accepted_answers`
  (для TBL_COM эталон строкой + `task_content.table.columns`, tsk-366).
- Ученический ACL задания — `tasks_acl_service.assert_task_access` (Y-4 post-S5).

## Реализация

### Backend (LMS)
- **Схема** `app/schemas/task_history.py` — `TaskHistoryResponse` (task/attempts/help_requests/
  hints/solution). Всегда-присутствующие поля обязательны (не-optional клиентский тип).
- **Сервис** `app/services/task_history_service.py::build_task_history(include_solution)` —
  батч без N+1 (≈5 запросов независимо от числа попыток). Блок `solution` собирается
  **только при `include_solution=True`** — структурное разграничение, не фильтрация на выходе.
- **Роутер учителя** `app/api/v1/teacher_task_history.py` —
  `GET /teacher/students/{student_id}/tasks/{task_id}/history`, гейт роли + `can_edit_progress`,
  курс задания резолвится ДО сборки данных.
- **Эндпоинт ученика** `app/api/v1/me.py` — `GET /me/tasks/{task_id}/history`, self-only
  (`user_id=current_user.id`) + `assert_task_access`, `include_solution=False`.
- Роутер зарегистрирован в `app/api/main.py`; openapi регенерирован (178 эндпоинтов).

### Frontend (SPW)
- `lib/task-history/use-task-history.ts` — хуки `useStudentTaskHistory` / `useMyTaskHistory`.
- `components/task-history/TaskHistoryCard.tsx` — переиспользуемая карточка (попытки, диалог
  помощи, подсказки, блок эталона). Эталон рендерится только `variant="teacher"` И при `solution!=null`.
- `TaskHistorySheet.tsx` (учитель, боковая панель, ленивая загрузка) + `MyTaskHistorySection.tsx`
  (ученик, секция на экране задания).
- Клик по заголовку задания в `StudentProgress` открывает Sheet; секция подключена в экран задания.

## Разграничение видимости (ключевой инвариант)

| | Ученик (`/me/...`) | Учитель (`/teacher/...`) |
|---|---|---|
| Свои попытки + комментарии | ✅ | ✅ (ученика) |
| Заявки помощи + диалог | ✅ (свои) | ✅ |
| Подсказки | ✅ | ✅ |
| Условие задания | ✅ | ✅ |
| **Эталон / правило проверки (`solution`)** | ❌ `null` всегда | ✅ |

Защита в глубину: сервер не собирает `solution` в ученической ветке; фронт-карточка
дополнительно гейтит блок по `variant` (тест: даже если `solution` просочится в payload,
`variant="student"` его не рендерит).

## Гейты

- **LMS pytest:** 922 passed, 10 skipped (полная сюита). Новый `tests/test_task_history_tsk349.py`
  — 8 тестов: учитель видит всё+эталон, methodist bypass, чужой учитель→403, 404, ученик видит своё
  без эталона, self-scoped (чужой ученик — пусто), задание вне курсов→403, сервис-гейт solution.
- **SPW:** tsc 0, eslint 0 ошибок, vitest 504 passed (новый `task-history-card.test.tsx` 6 тестов
  + `student-progress.test.tsx` +1 на открытие карточки).
- **Cross-project mirror:** обновлены `contracts/lms-api.md`, `CHANGELOG.md`, `STATE.md` в ContentBackbone.
- **openapi.json** регенерирован в том же изменении.

## Риски / follow-ups

- Задание с `course_id = NULL` (legacy) даёт **404** на учительском эндпоинте (ACL требует курс).
  Безвредно: такие задания не попадают в дерево прогресса и не достижимы кликом. При реализации
  tsk-353 (поиск) — учесть, если поиск сможет находить null-курсовые задания.
- Живой прод-прогон под обеими ролями — обязателен после деплоя (см. tsk-349, гейт живой проверки).
