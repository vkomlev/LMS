# Ревью tsk-996, фаза D: якорь задания банка на материал прозрачного хозяина

**Ревьюер:** независимый techlead (отдельный агент), 2026-10-20
**Объём:** LMS — миграция `tsk996_bank_anchor`, `node_sequence`, 6 мест обхода, `tasks_service`, API `GET /tasks/{id}/anchor-materials`, тесты; SPW — `TaskAnchorSelect`, хук `useTaskAnchorMaterials`, unit-тест.

## Вердикт: ПРИНЯТО

BLOCKER — нет. MAJOR — нет.

## Проверено ревьюером

- `tests/test_tsk996_bank_anchor.py` — 22 passed; тесты фаз A–C и tsk-1198 — зелёные (кроме существующего флака `*-dependencies`, см. NIT-1).
- SPW: `methodist-task-anchor-tsk996.test.tsx` 9 passed, `tsc --noEmit` чисто.
- Одна голова alembic: `tsk996_task_anchor → tsk1384_help_stuck_reason → tsk996_bank_anchor`.
- Соответствие пунктам 1–5 постановки; курсор next-item (`host_alt` с якорем, шаг HEAD хозяина, признак `has_anchors` шире правила `anchor_of` — задания не теряются, повторов нет); plpgsql (рекурсии нет, триггер пересчёта `order_number` не пересекается, каскад удаления курса — ранний выход); число запросов на узел не выросло.

## Находки и что с ними сделано

| # | Уровень | Находка | Решение |
|---|---|---|---|
| MINOR-1 | MINOR | Подкурс, прозрачный для двух родителей (ошибка разметки): сервис/список принимали любого хозяина, порядок узла — только первого | **Исправлено:** `anchor_course_ids` и bulk-upsert берут только первого хозяина (тот же порядок, что `transparent_links`); тест `test_two_transparent_parents_only_first_host`. Триггер-страж допускает любого прозрачного родителя (мягче сервиса, не опаснее); grace в таком дереве может поставить задание к второму хозяину — follow-up: частичный UNIQUE `course_parents(course_id) WHERE is_transparent` после проверки прода |
| MINOR-2 | MINOR | Теоретическая взаимоблокировка PATCH якоря против снятия прозрачности той же пары (обратный порядок блокировок) | Не исправлено: редкий класс, тот же уже есть у переезда материала в фазе B; follow-up |
| MINOR-3 | MINOR | Гонка «проверка сервиса прошла → страж отказал» давала 500 | **Исправлено:** PATCH ловит отказ стража (`tsk-996:` в ошибке базы) и отвечает 422 |
| MINOR-4 | MINOR | Подпись группы «свой раздел» у задания банка не объясняла, что якорь на свой материал в уроке не действует | **Исправлено:** «Раздел этого задания — действует, когда раздел открыт сам по себе» |
| NIT-1 | NIT | Флак `test_output_identical_with_switch_on_and_off[*-dependencies]`: `_BLOCKED_COURSES_SQL` без `ORDER BY` | Существующий дефект main (воспроизведён на чистом main); не в объёме задачи — follow-up |
| NIT-2 | NIT | `key={g.label}` у `optgroup` | **Исправлено:** ключ по курсу |
| NIT-3 | NIT | В ключе кеша списка вариантов нет курса задания | **Исправлено:** `courseId` в `queryKey` |
| NIT-4 | NIT | Переезд задания обнуляет якорь даже если он допустим в новом курсе | Сознательно, как фаза B; отражено в ADR |
| NIT-5 | NIT | Нет теста ролей у нового GET | Уже есть: 401/403/404 в `test_api_anchor_materials_lists_host` |

## Follow-up

1. Cross-project память (ContentBackbone `contracts/lms-api.md`, `contracts/lms-db-schema.md` — head `tsk996_bank_anchor`, `contracts/spw.md`, `CHANGELOG.md`) — в облаке репозитория CB нет, обновить на машине оператора.
2. Перед выкатом на копии базы: `alembic downgrade tsk1384_help_stuck_reason` → `upgrade head`.
3. На проде проверить, что нет подкурсов с двумя прозрачными родителями: `SELECT course_id FROM course_parents WHERE is_transparent GROUP BY 1 HAVING count(*) > 1`.
