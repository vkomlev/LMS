# tsk-566 — экранирование ILIKE спецсимволов в `list_by_course`

## Контекст

`GET /courses/{course_id}/materials?q=...` (список материалов курса в кабинете
методиста) искал по `title`/`external_uid` через `Materials.title.ilike(pattern)`
без экранирования `%`/`_`. Тот же класс бага, что tsk-565 закрыл для
`GET /tasks/search` и `GET /materials/search` (buквальный `%`/`_` в запросе
срабатывал как wildcard, decoy-запись без спецсимвола подмешивалась в выдачу).
Не безопасность/инъекция — SQLAlchemy параметризует значение, чисто ложные
срабатывания поиска. Приоритет P3 (UX-квирк).

Задача в кросс-проектном трекере: `tsk-566`
(`D:\Work\Root\tasks\tsk-566-lms-materials-list-by-course-ekranirovat-ilike-spetssimvoly.md`).

## Изменения

- [app/repos/materials_repo.py](../app/repos/materials_repo.py) — `list_by_course`:
  `pattern = f"%{escape_ilike(q.strip())}%"` + `.ilike(pattern, escape='\\')` на
  обоих условиях, по образцу уже принятого решения в соседнем методе
  `search_materials` этого же файла (tsk-565). Хелпер `app/utils/ilike.py::escape_ilike`
  уже существовал — переиспользован без изменений.
- [tests/test_tsk566_ilike_escape_list_by_course.py](../tests/test_tsk566_ilike_escape_list_by_course.py) —
  regression-тест по образцу `tests/test_tsk565_ilike_escape.py`: буквальный `%`
  в query находит только точное совпадение (decoy без `%` не подмешивается) +
  контрольный тест на отсутствие регресса для обычного запроса без спецсимволов.

## Validation Commands

```
.venv/Scripts/python.exe -m pytest tests/test_tsk566_ilike_escape_list_by_course.py -v
.venv/Scripts/python.exe -m pytest tests/ -q
```

## Результат

- Новый тест: 2 passed.
- Полный набор: **1716 passed, 11 skipped**, 0 failed (654s).

## Risks / Follow-ups

Нет. Изменение локально к одному методу, поведение по остальным веткам
(`is_active`, `type_filter`, сортировка, пагинация) не тронуто. Паттерн
идентичен уже смерженному и отревьюенному tsk-565.
