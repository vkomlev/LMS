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
| 25 | 152 | Средние | 3 | yandex | c01534c6-0b3e-4da7-9d99-6c8d759babaf:25 | https://education.yandex.ru/ege/variants/c01534c6-0b3e-4da7-9d99-6c8d759babaf/task/25 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 2653 | https://kompege.ru/task?id=2653 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 2580 | https://kompege.ru/task?id=2580 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 23570 | https://kompege.ru/task?id=23570 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 22567 | https://kompege.ru/task?id=22567 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 21588 | https://kompege.ru/task?id=21588 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 18228 | https://kompege.ru/task?id=18228 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 14733 | https://kompege.ru/task?id=14733 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 11940 | https://kompege.ru/task?id=11940 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 10072 | https://kompege.ru/task?id=10072 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 9847 | https://kompege.ru/task?id=9847 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 9554 | https://kompege.ru/task?id=9554 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 9171 | https://kompege.ru/task?id=9171 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 8616 | https://kompege.ru/task?id=8616 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 8569 | https://kompege.ru/task?id=8569 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 8279 | https://kompege.ru/task?id=8279 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 8168 | https://kompege.ru/task?id=8168 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 7986 | https://kompege.ru/task?id=7986 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 7756 | https://kompege.ru/task?id=7756 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 7690 | https://kompege.ru/task?id=7690 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 6277 | https://kompege.ru/task?id=6277 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 6071 | https://kompege.ru/task?id=6071 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 5988 | https://kompege.ru/task?id=5988 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 5890 | https://kompege.ru/task?id=5890 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 5245 | https://kompege.ru/task?id=5245 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 4938 | https://kompege.ru/task?id=4938 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 3902 | https://kompege.ru/task?id=3902 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 3745 | https://kompege.ru/task?id=3745 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 3377 | https://kompege.ru/task?id=3377 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 3096 | https://kompege.ru/task?id=3096 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 3088 | https://kompege.ru/task?id=3088 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 2132 | https://kompege.ru/task?id=2132 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 1763 | https://kompege.ru/task?id=1763 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 1379 | https://kompege.ru/task?id=1379 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 1354 | https://kompege.ru/task?id=1354 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 1066 | https://kompege.ru/task?id=1066 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 936 | https://kompege.ru/task?id=936 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 889 | https://kompege.ru/task?id=889 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 838 | https://kompege.ru/task?id=838 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 813 | https://kompege.ru/task?id=813 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 441 | https://kompege.ru/task?id=441 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | kompege | 316 | https://kompege.ru/task?id=316 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 8200 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=8200 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 8117 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=8117 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 8116 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=8116 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 8106 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=8106 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 8105 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=8105 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 8104 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=8104 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 8103 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=8103 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 8102 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=8102 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 8073 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=8073 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 6793 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=6793 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | polyakov | 6167 | https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=6167 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | yandex | 2be758c4-f82d-4eaa-a128-1f87e21fe349 | https://education.yandex.ru/ege/inf/task/2be758c4-f82d-4eaa-a128-1f87e21fe349 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | yandex | 2fde956d-cb4f-40b7-8699-5a1fdfbbe9a8 | https://education.yandex.ru/ege/inf/task/2fde956d-cb4f-40b7-8699-5a1fdfbbe9a8 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | yandex | 3de03ee8-893c-4125-81f6-165fac65e9d5 | https://education.yandex.ru/ege/inf/task/3de03ee8-893c-4125-81f6-165fac65e9d5 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | yandex | ac96584e-64cf-4491-acfb-fb19cd19ec3e | https://education.yandex.ru/ege/inf/task/ac96584e-64cf-4491-acfb-fb19cd19ec3e | 2026-06-08 |
| 26 | 153 | Сложные | 4 | yandex | ce678701-0e62-409b-82b7-a5e1946f31c2 | https://education.yandex.ru/ege/inf/task/ce678701-0e62-409b-82b7-a5e1946f31c2 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | yandex | c5c6b768-42bb-4590-8b90-2317266cc1f2 | https://education.yandex.ru/ege/inf/task/c5c6b768-42bb-4590-8b90-2317266cc1f2 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | yandex | 136654a3-23d8-4dd7-8f6f-3134292c33b3 | https://education.yandex.ru/ege/inf/task/136654a3-23d8-4dd7-8f6f-3134292c33b3 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | yandex | d8ad24ec-8e40-4a31-a8a2-2a25640339a3 | https://education.yandex.ru/ege/inf/task/d8ad24ec-8e40-4a31-a8a2-2a25640339a3 | 2026-06-08 |
| 26 | 153 | Сложные | 4 | yandex | 15e49bb8-3179-465b-baaa-e11efdd21bf0 | https://education.yandex.ru/ege/inf/task/15e49bb8-3179-465b-baaa-e11efdd21bf0 | 2026-06-08 |
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
| 26 | 153 | Задания для тренировки | задачи перенесены в `tasks`; отсутствующие сложные задачи зафиксированы в основном реестре | `materials.id=411`, inactive |

## Исключения материалов навигатора

Эти пункты не являются missing-заданиями и не должны создаваться как материалы текущего курса.

| Задание | course_id | Пункт навигатора | Причина |
|---------|-----------|------------------|---------|
| 12 | 163 | `Разбор типовых заданий` → `zadanie-11-ege.../#razbor-zadaniy` | Кросс-ссылка на страницу задания 11; `nav_parser` игнорирует материалы `zadanie-N`, если `N` не совпадает с текущим заданием |
