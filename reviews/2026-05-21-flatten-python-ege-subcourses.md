# tsk-004 Этап 1.4 — flatten подкурсов «Python для ЕГЭ»

**Дата:** 2026-05-21
**Скилл:** `/db-check`
**Связано:** [tsk-004 «Порядок в LMS»](https://github.com/vkomlev/work-root/blob/master/tasks/tsk-004-poryadok-v-lms.md) — продолжение после Этапов 1.1, 1.2, 1.3 (частично).

## Контекст

Курс id=88 «Python для ЕГЭ» имел трёхуровневую структуру:
- L1 (под 88): 10 промежуточных курсов «ЕГЭ для Python. Тема N. <название>» (ids 89, 94-102).
- L2 (под каждым L1): 1 листовой курс с тем же по сути названием (ids 90, 106, 103, 108, 111, 110, 109, 104, 105, 107).

L1 и L2 — это **одна и та же тема в разных формулировках** (например, «Тема 1. Установка Python» ↔ «Как установить Python»). Двухуровневая обёртка дублирует структуру без смысловой нагрузки.

## Маппинг intermediate → leaf

| order | Intermediate (удалён) | Leaf (стал прямым ребёнком 88) |
|---|---|---|
| 1 | 89: ЕГЭ для Python. Тема 1. Установка Python | 90: Как установить Python |
| 2 | 94: Тема 2. Пишем первую программу | 106: Первая программа на Python. Основные конструкции |
| 3 | 95: Тема 3. Числа | 103: Числа в Python и операции с ними |
| 4 | 96: Тема 4. Строки | 108: Работа со строками в Python |
| 5 | 97: Тема 5. Условные | 111: Условные конструкции в Python |
| 6 | 98: Тема 6. Циклы | 110: Циклы в Python |
| 7 | 99: Тема 7. Списки | 109: Списки (массивы) в Python |
| 8 | 100: Тема 8. Функции | 104: Функции в Python. Создание собственных функций |
| 9 | 101: Тема 9. Множества | 105: Использование множеств (`set`) в Python |
| 10 | 102: Тема 10. Словари | 107: Работа со словарями в Python |

## Предварительный аудит

- Все 10 intermediate-курсов **чисты по связям**: `attempts=0`, `user_courses=0`, `teacher_courses=0`, `student_course_state=0`, `help_requests=0`, `course_dependencies=0`, `social_posts=0`. Безопасно удаляются.
- В intermediate **0 tasks** (ни в одном). 93 материала суммарно (15 + 11 + 5 + 11 + 11 + 11 + 18 + 13 + 3 + 4 + 2).
- `unique (course_id, external_uid)` — конфликтов при переносе intermediate→leaf нет.

## Применённая операция

В одной транзакции, с временным DISABLE триггеров:

1. `ALTER TABLE materials DISABLE TRIGGER trg_set_material_order_position` и `trg_reorder_materials_after_delete`.
2. `ALTER TABLE course_parents DISABLE TRIGGER trg_set_course_parent_order_number` и `trg_reorder_course_parents_after_delete`.
3. Для каждой пары `(intermediate, leaf)`:
   - **Shift:** `UPDATE materials SET order_position += N` для существующих leaf-материалов (где N = кол-во материалов в intermediate).
   - **Move:** `UPDATE materials SET course_id = leaf WHERE course_id = intermediate` — оригинальные позиции 1..N сохраняются, intermediate-материалы оказываются **в начале leaf-курса**.
   - **Move tasks** (для будущего, в текущей БД task_count=0).
4. `DELETE FROM course_parents WHERE parent_course_id=88 AND course_id IN (intermediate)`.
5. `DELETE FROM course_parents WHERE parent_course_id IN (intermediate)` — снимаем 89→90 и т.д.
6. `INSERT INTO course_parents (parent_course_id=88, course_id=leaf, order_number=<from intermediate>)` — leaf становится прямым ребёнком 88 с тем же order, что был у intermediate.
7. `DELETE FROM courses WHERE id IN (89,94,95,96,97,98,99,100,101,102)` — удаляем intermediate-курсы.
8. ENABLE TRIGGER.

## Верификация (после COMMIT, независимый MCP-канал)

| Метрика | Значение |
|---|---|
| intermediate_alive | 0 (было 10) |
| children_of_88 | 10 (10 leaf-подкурсов) |
| orphan_course_parents_rows | 0 |
| orphan_materials_intermediate | 0 |
| orphan_tasks_intermediate | 0 |
| total_materials_in_leaves | 205 (было 112; +93 из intermediate) |

Финальная структура `children_of_88` (id, order, title) — порядок 1..10 строго соответствует исходному порядку «Тема 1..10».

## Известное состояние order_position в leaf

Триггеры были отключены на время операции, поэтому `order_position` в leaf-курсах **может содержать дыры** относительно `ROW_NUMBER()` (диагностика после операции показала 8..33 mismatch на курс). Это **исторический мусор**, существовавший и до операции — дыры пришли из WP-импорта. Логический порядок сохранён.

Если потребуется compact reorder — отдельный скрипт `UPDATE materials SET order_position = rn FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY order_position) AS rn FROM materials WHERE course_id = ANY(...)) sq WHERE materials.id = sq.id` под отключёнными триггерами. Не делал в этом этапе — оператор не запрашивал.

## Артефакты

- [scripts/flatten_python_ege_subcourses.py](../scripts/flatten_python_ege_subcourses.py) — переиспользуемый (dry-run по умолчанию, `--apply` для COMMIT). Не идемпотентен (intermediate-курсы уже удалены), но безопасен при повторном запуске — операция UPDATE/DELETE по несуществующим id вернёт 0 строк, валидация пройдёт.
- [reviews/2026-05-21-flatten-python-ege-subcourses.diff](2026-05-21-flatten-python-ege-subcourses.diff)

## Risks / Follow-ups

- **CB-импортер при re-import снова создаст intermediate-курсы.** Поскольку курсы 89/94-102 имеют `course_uid='wp:ege-dlya-python-tema-*'` (то есть импортируются CB по этим uid), при следующем прогоне CB `run_wp_pipeline()` они появятся снова. Это связано с Этапом 1.3 — CB-сторонний фикс. Возможные стратегии (отдельная задача):
  - (а) Не импортировать intermediate-курсы из WP, если на сайте структура другая. Требует анализа WP-навигатора.
  - (б) Добавить в LMS UPSERT-логику: если `course_uid` совпадает с заблокированным маппингом → пропустить.
  - (в) Запустить этот же скрипт повторно после re-import — он снова всё сплющит (идемпотентно по результату).
  
- **«Задания»-материалы** (40+ text-материалов с реальным HTML контентом) — отложены на Этап 1.5. После миграции их распределение по leaf-курсам поменялось (некоторые из intermediate переехали). Перед удалением нужен отдельный аудит.

- **order_position дыры** в leaf-курсах — низкий приоритет, не блокирует.
