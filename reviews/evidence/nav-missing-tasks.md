# Реестр заданий навигатора, отсутствующих в LMS

Обновлён 2026-06-07 после исправления nav_parser (теперь ищет по task_content->>'source_task_id').
Все 78 предыдущих записей (курсы 140, 148, 138, 155) были ложными MISS:
задания присутствуют в LMS в wp_nav-обёртке с известным source_kind + source_task_id.

| Задание | course_id | Раздел | diff_id | Источник | task_id | URL | Добавлено |
|---------|-----------|--------|---------|----------|---------|-----|----------|
| 9 | 160 | Средние | 3 | yandex | c01534c6-0b3e-4da7-9d99-6c8d759babaf:9 | https://education.yandex.ru/ege/variants/c01534c6-0b3e-4da7-9d99-6c8d759babaf/task/9 | 2026-06-07 |
| 5 | 156 | Средние | 3 | yandex | c01534c6-0b3e-4da7-9d99-6c8d759babaf:5 | https://education.yandex.ru/ege/variants/c01534c6-0b3e-4da7-9d99-6c8d759babaf/task/5 | 2026-06-07 |
| 8 | 159 | Средние | 3 | yandex | 31d08c52-c86e-4487-b7e4-6f7435f63344:8 | https://education.yandex.ru/ege/variants/31d08c52-c86e-4487-b7e4-6f7435f63344/task/8?utm_term=kege | 2026-06-07 |
