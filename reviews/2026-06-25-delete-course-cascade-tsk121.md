# DELETE /api/v1/courses/{id} — 500 → 409/cascade (tsk-121)

**Дата:** 2026-06-25
**Задача:** tsk-121 (всплыло при отладке публикатора tsk-120)
**Файлы:** `app/services/courses_service.py`, `app/api/v1/courses_extra.py`, `tests/test_delete_course_cascade_tsk121.py`

## Контекст / проблема

`DELETE /api/v1/courses/{id}` отдавал **500 Internal Server Error**, если у курса
были подкурсы, материалы или зависимости. Демо-курсы публикатора (683–686, 688)
с подкурсами было невозможно удалить через API.

## Причина

Курсы используют generic CRUD-роутер (`create_crud_router`, `app/api/v1/crud.py`),
чей `delete_item` зовёт `BaseService.delete` → `repo.delete` → `await db.delete(course)`.
В async SQLAlchemy `db.delete()` инициирует обработку relationships (lazy-load
коллекций материалов/заданий/подкурсов для дисассоциации), а FK `social_posts.course_id`
имеет `ON DELETE NO ACTION`. Ошибка всплывала необработанной → generic `Exception`
handler отдавал 500. БД-каскады (`ondelete=CASCADE`) на materials/tasks/course_parents/
course_dependencies/teacher_courses/user_courses при этом ORM-путём не использовались.

## Решение (продуктовое)

Удаление учебного курса со связями должно быть осознанным → **по умолчанию отказ**,
с явным opt-in на каскад:

- Выделенный `DELETE /api/v1/courses/{course_id}` в `courses_extra.py`. Роутер
  `courses_extra` включается в `main.py` **до** generic CRUD-роутера, поэтому
  перехватывает маршрут (generic-delete для курсов становится теневым).
- **`cascade=false` (по умолчанию):** при наличии связей (подкурсы, материалы,
  задания, зависимости, соц-посты, привязанные преподаватели/студенты) →
  `DomainError` **409** с `payload.relations` (счётчики). Курс не трогается.
- **`cascade=true`:** удаление через Core `delete(Courses)` — БД-каскад убирает
  связанные строки; соц-посты (FK NO ACTION, nullable) предварительно отвязываются
  (`course_id = NULL`). **Подкурсы не удаляются**, лишь отвязываются (становятся
  корневыми) — намеренно, рекурсивное удаление поддерева вне охвата.
- Курс **без связей** удаляется одинаково в обоих режимах (204, как и раньше).
- Любая остаточная `IntegrityError/DBAPIError` от БД оборачивается в `DomainError` 409
  (никаких 500).

## Изменения

- `CoursesService.count_relations(db, course_id)` — счётчики связей (Core `func.count`).
- `CoursesService.delete_course(db, course_id, *, cascade)` — доменная логика
  отказа/каскада + обёртка ошибок в `DomainError`.
- `delete_course_endpoint` в `courses_extra.py` — `?cascade`, 204/404/409, OpenAPI-доки.

## Валидация

- `tests/test_delete_course_cascade_tsk121.py` — **4 passed**:
  - связи без cascade → 409 (с `relations.children=1`, `materials=1`), курс жив;
  - cascade=true → 204, курс+материал удалены, подкурс жив и отвязан;
  - курс без связей → 204;
  - несуществующий курс → 404.
- Реальные данные: демо-курсы 683, 684, 685, 686, 688 удалены через
  `delete_course(cascade=True)` (dogfood сервиса). Проверка: 0 осиротевших
  courses/materials/tasks/course_parents.

## Риски / follow-ups

- В OpenAPI остаётся теневой generic `DELETE /courses/{id}` (первым матчится наш) —
  косметика, на поведение не влияет.
- Рекурсивное удаление поддерева подкурсов не реализовано (по дизайну — unlink).
  Если понадобится «удалить ветку целиком» — отдельная задача.
- Cross-project контракт обновлён: `ContentBackbone/docs/cross-project/contracts/lms-api.md`
  + `CHANGELOG.md`.
