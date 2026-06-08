# Реестр заданий навигатора, отсутствующих в LMS

Обновлён 2026-06-08 после исправления nav_parser (теперь ищет по task_content->>'source_task_id' и `ext:calib:yandex`).
Все 78 предыдущих записей (курсы 140, 148, 138, 155) были ложными MISS:
задания присутствуют в LMS в wp_nav-обёртке с известным source_kind + source_task_id.

| Задание | course_id | Раздел | diff_id | Источник | task_id | URL | Добавлено |
|---------|-----------|--------|---------|----------|---------|-----|----------|
| 9 | 160 | Средние | 3 | yandex | c01534c6-0b3e-4da7-9d99-6c8d759babaf:9 | https://education.yandex.ru/ege/variants/c01534c6-0b3e-4da7-9d99-6c8d759babaf/task/9 | 2026-06-07 |
| 5 | 156 | Средние | 3 | yandex | c01534c6-0b3e-4da7-9d99-6c8d759babaf:5 | https://education.yandex.ru/ege/variants/c01534c6-0b3e-4da7-9d99-6c8d759babaf/task/5 | 2026-06-07 |
| 8 | 159 | Средние | 3 | yandex | 31d08c52-c86e-4487-b7e4-6f7435f63344:8 | https://education.yandex.ru/ege/variants/31d08c52-c86e-4487-b7e4-6f7435f63344/task/8?utm_term=kege | 2026-06-07 |
| 13 | 139 | Простые | 2 | sdamgia | 7258 | https://inf-ege.sdamgia.ru/problem?id=7258 | 2026-06-08 |
| 13 | 139 | Простые | 2 | sdamgia | 2222 | https://inf-ege.sdamgia.ru/problem?id=2222 | 2026-06-08 |
| 13 | 139 | Простые | 2 | sdamgia | 2203 | https://inf-ege.sdamgia.ru/problem?id=2203 | 2026-06-08 |
| 13 | 139 | Простые | 2 | sdamgia | 2229 | https://inf-ege.sdamgia.ru/problem?id=2229 | 2026-06-08 |
| 13 | 139 | Простые | 2 | sdamgia | 2238 | https://inf-ege.sdamgia.ru/problem?id=2238 | 2026-06-08 |
| 13 | 139 | Простые | 2 | sdamgia | 3510 | https://inf-ege.sdamgia.ru/problem?id=3510 | 2026-06-08 |
| 13 | 139 | Простые | 2 | sdamgia | 4854 | https://inf-ege.sdamgia.ru/problem?id=4854 | 2026-06-08 |
| 13 | 139 | Сложные | 4 | polyakov | 7041 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=7041 | 2026-06-08 |
| 13 | 139 | Сложные | 4 | kompege | 21602 | https://kompege.ru/task?id=21602 | 2026-06-08 |
| 13 | 139 | Сложные | 4 | kompege | 16260 | https://kompege.ru/task?id=16260 | 2026-06-08 |
| 13 | 139 | Сложные | 4 | kompege | 12467 | https://kompege.ru/task?id=12467 | 2026-06-08 |
| 13 | 139 | Сложные | 4 | sdamgia | 60255 | https://inf-ege.sdamgia.ru/problem?id=60255 | 2026-06-08 |
| 13 | 139 | Сложные | 4 | sdamgia | 76116 | https://inf-ege.sdamgia.ru/problem?id=76116 | 2026-06-08 |
| 13 | 139 | Сложные | 4 | sdamgia | 9762 | https://inf-ege.sdamgia.ru/problem?id=9762 | 2026-06-08 |
| 13 | 139 | Сложные | 4 | sdamgia | 13488 | https://inf-ege.sdamgia.ru/problem?id=13488 | 2026-06-08 |

| 14 | 142 | Средние | 3 | yandex | c01534c6-0b3e-4da7-9d99-6c8d759babaf:14 | https://education.yandex.ru/ege/variants/c01534c6-0b3e-4da7-9d99-6c8d759babaf/task/14 | 2026-06-08 |

| 17 | 145 | Средние | 3 | yandex | c01534c6-0b3e-4da7-9d99-6c8d759babaf:17 | https://education.yandex.ru/ege/variants/c01534c6-0b3e-4da7-9d99-6c8d759babaf/task/17 | 2026-06-08 |

| 19 | 147 | Средние | 3 | yandex | 31d08c52-c86e-4487-b7e4-6f7435f63344:19 | https://education.yandex.ru/ege/variants/31d08c52-c86e-4487-b7e4-6f7435f63344/task/19 | 2026-06-08 |
| 23 | 150 | Средние | 3 | yandex | 31d08c52-c86e-4487-b7e4-6f7435f63344:23 | https://education.yandex.ru/ege/variants/31d08c52-c86e-4487-b7e4-6f7435f63344/task/23 | 2026-06-08 |
## Найдено в другом курсе, отсутствует в текущем курсе

Эти задания есть в LMS, но не в том курсе, где они указаны навигатором. Не считаем их
полностью отсутствующими в LMS, но фиксируем как пробел текущего курса.

| Задание | course_id | Раздел | diff_id | Источник | task_id | URL | Где найдено | Добавлено |
|---------|-----------|--------|---------|----------|---------|-----|-------------|----------|
| 16 | 144 | Сложные | 4 | polyakov | 7239 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=7239 | course_id=148, `ext:d4:polyakov:20260602:7239` | 2026-06-08 |

## Покрыто вводными заданиями, не восстанавливать как материалы

Эти пункты навигатора не являются missing-заданиями. Они намеренно погашены как материалы,
потому что их содержание преобразовано во вводные задания или контрольные вопросы LMS.

| Задание | course_id | Пункт навигатора | LMS-покрытие | Материал |
|---------|-----------|------------------|--------------|----------|
| 4 | 155 | Вопросы по разделу 1-5 | `lms:tsk109:c155:01-05` | `materials.id=419`, inactive |
| 4 | 155 | Мини практика 6-10 | `lms:tsk109:c155:06-10` | `materials.id=807`, inactive |
| 4 | 155 | Мини практика после заданий | `lms:tsk109:c155:01-10` | `materials.id=418`, inactive |
| 11 | 162 | Вопросы | `lms:c162:vvod:01-10` | `materials.id=371`, inactive |
| 11 | 162 | Мини-задания | `lms:c162:vvod:11-20` | `materials.id=372`, inactive |
| 13 | 139 | Вопросы | `lms:c139:vvod:01-08` | `materials.id=356`, inactive |
| 13 | 139 | Решаем на бумаге | `lms:c139:vvod:09-18` | `materials.id=357`, inactive |
| 13 | 139 | Мини-задания | `lms:c139:vvod:19-27` | материала нет; не восстанавливать |
| 14 | 142 | Вопросы | `lms:c142:vvod:01-11` | `materials.id=380`, inactive |
| 14 | 142 | Задания для подготовки | задачи перенесены в `tasks` | `materials.id=381`, inactive |
| 15 | 143 | Задания для подготовки | задачи перенесены в `tasks` | `materials.id=384`, inactive |
| 16 | 144 | Задания для подготовки | задачи перенесены в `tasks`; `materials.id=586` — дубль видео id=585 | `materials.id=386`, inactive |
| 17 | 145 | Задания для подготовки | `lms:c145:vvod:01-07`; `materials.id=592` — дубль видео id=590 | `materials.id=388`, inactive |
| 18 | 146 | Задания для подготовки | задачи перенесены в `tasks`; `materials.id=595` — дубль видео id=594 | `materials.id=390`, inactive |

## Исключения материалов навигатора

Эти пункты не являются missing-заданиями и не должны создаваться как материалы текущего курса.

| Задание | course_id | Пункт навигатора | Причина |
|---------|-----------|------------------|---------|
| 12 | 163 | `Разбор типовых заданий` → `zadanie-11-ege.../#razbor-zadaniy` | Кросс-ссылка на страницу задания 11; `nav_parser` игнорирует материалы `zadanie-N`, если `N` не совпадает с текущим заданием |
