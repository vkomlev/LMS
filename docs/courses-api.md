# Courses API (Swagger-ready documentation)

Документ описывает **все эндпойнты**, относящиеся к работе с курсами в LMS: CRUD курсов, иерархия, зависимости курсов и привязка пользователей (студентов) к курсам.

**⚠️ Важно (миграция 2026-01-24):** 
- Курс теперь может иметь **несколько родителей** (many-to-many отношение). Поле `parent_course_id` заменено на `parent_course_ids` (список ID).
- Добавлена поддержка **порядковых номеров** подкурсов внутри родительского курса через поле `order_number` в таблице `course_parents`.
- Порядковые номера автоматически управляются триггерами БД (см. `docs/database-triggers-contract.md`).
- Все примеры и схемы обновлены.

## Общие правила

- **Base URL**: `/api/v1`
- **Аутентификация**: обязательный query-параметр `api_key`.
  - При отсутствии/неверном ключе: **403** (`Invalid or missing API Key`)
- **Ошибки домена**: многие ошибки возвращаются как **DomainError** единым глобальным хэндлером:

```json
{
  "error": "domain_error",
  "detail": "Человекочитаемое сообщение",
  "payload": { "любые": "детали" }
}
```

## Схемы (ключевые)

### `CourseCreate`
- `title` (string, required): название курса
- `access_level` (enum, required): `self_guided | auto_check | manual_check | group_sessions | personal_teacher`
- `description` (string|null)
- `parent_course_ids` (int[]|null): список ID родительских курсов; `null` или `[]` = корневой курс
- `parent_courses` (ParentCourseWithOrder[]|null): список родительских курсов с порядковыми номерами
  - Если указано, имеет приоритет над `parent_course_ids`
  - Каждый элемент: `{"parent_course_id": int, "order_number": int|null}`
  - `order_number` - порядковый номер подкурса внутри родителя (если `null`, устанавливается автоматически триггером БД)
- `is_required` (bool, default=false)
- `course_uid` (string|null): внешний код курса (для импорта/интеграций)

**Важно:** 
- Курс может иметь несколько родителей (many-to-many). Передайте список ID: `[1, 2]` для курса с двумя родителями.
- Для указания порядкового номера используйте `parent_courses` вместо `parent_course_ids`.
- Порядковые номера автоматически управляются триггерами БД (см. `docs/database-triggers-contract.md`).

### `CourseUpdate`
Все поля опциональны; `parent_course_ids=[]` или `null` делает курс корневым (удаляет все связи с родителями).

- `parent_courses` (ParentCourseWithOrder[]|null): список родительских курсов с порядковыми номерами
  - Если указано, имеет приоритет над `parent_course_ids`
  - Каждый элемент: `{"parent_course_id": int, "order_number": int|null}`
  - `order_number` - порядковый номер подкурса внутри родителя (если `null`, устанавливается автоматически триггером БД)

### `CourseRead`
Отдаётся в ответах. Важно: 
- `course_uid` может быть `null` для старых курсов
- `parent_course_ids` (int[]): список ID родительских курсов; пустой список `[]` для корневых курсов

### `CourseMoveRequest`
- `new_parent_ids` (int[]|null): список ID новых родительских курсов; `null` или `[]` = сделать курс корневым
- `new_parent_courses` (ParentCourseWithOrder[]|null): список новых родительских курсов с порядковыми номерами
  - Если указано, имеет приоритет над `new_parent_ids`
  - Каждый элемент: `{"parent_course_id": int, "order_number": int|null}`
  - `order_number` - порядковый номер подкурса внутри родителя (если `null`, устанавливается автоматически триггером БД)
- `replace_parents` (bool, default=false): режим работы с родителями
  - `false` (по умолчанию) = добавить новые связи к существующим
  - `true` = заменить все существующие связи новыми

**Важно:** 
- Курс может быть перемещен к нескольким родителям одновременно.
- Для указания порядкового номера используйте `new_parent_courses` вместо `new_parent_ids`.
- Порядковые номера автоматически управляются триггерами БД (см. `docs/database-triggers-contract.md`).
- Поведение при перемещении зависит от параметра `replace_parents`.

### `UserCourseBulkCreate`
`course_ids` (int[]) список курсов для массовой привязки.

### `UserCourseReorderRequest`
`course_orders` — список `{ course_id, order_number }`.

---

## 1) Курсы — CRUD (`/courses`)

CRUD для курсов подключён через общий генератор роутов (`create_crud_router`) и доступен в swagger.

### `POST /courses/`
Создать курс.

### `GET /courses/`
Получить страницу курсов.

Параметры:
- `skip` (int, default=0)
- `limit` (int, default=100)

Ответ: `Page[CourseRead]` (items + meta).

### `GET /courses/{id}`
Получить курс по ID.

### `PUT /courses/{id}`
Полное обновление курса (контрактом допускает частичное по схеме).

### `PATCH /courses/{id}`
Частичное обновление курса.

**Тело:** `CourseUpdate`
- `parent_course_ids` (int[]|null): обновить список родительских курсов
  - `null` = не изменять текущие связи
  - `[]` = сделать курс корневым (удалить все связи с родителями)
  - `[1, 2]` = установить новых родителей (поведение зависит от `replace_parents`)
- `parent_courses` (ParentCourseWithOrder[]|null): список родительских курсов с порядковыми номерами
  - Если указано, имеет приоритет над `parent_course_ids`
  - Позволяет задать порядковый номер подкурса внутри каждого родителя
- `replace_parents` (bool, default=false): режим работы с родителями
  - `false` (по умолчанию) = добавить новые связи к существующим
  - `true` = заменить все существующие связи новыми

**Пример:**
```bash
# Обновить родительские курсы (order_number установится автоматически)
# Добавить новые связи к существующим (по умолчанию)
curl -X PATCH "http://localhost:8000/api/v1/courses/6?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"parent_course_ids": [1, 2]}'

# Заменить все существующие связи новыми
curl -X PATCH "http://localhost:8000/api/v1/courses/6?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"parent_course_ids": [1, 2], "replace_parents": true}'

# Обновить родительские курсы с указанием order_number
curl -X PATCH "http://localhost:8000/api/v1/courses/6?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "parent_courses": [
      {"parent_course_id": 1, "order_number": 1},
      {"parent_course_id": 2, "order_number": 2}
    ]
  }'

# Сделать курс корневым
curl -X PATCH "http://localhost:8000/api/v1/courses/6?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"parent_course_ids": []}'
```

### `DELETE /courses/{id}`
Удалить курс.

---

## 2) Курсы — иерархия/поиск (`/courses/*`) (`app/api/v1/courses_extra.py`)

### `GET /courses/search`
Поиск курсов по названию (title) или коду (course_uid).

**Параметры:**
- `q` (string, required, min_length=2) - поисковый запрос
- `limit` (int, default=20, max=200) - максимум результатов
- `offset` (int, default=0) - смещение

**Поиск:** регистронезависимый (ILIKE), ищет вхождение запроса в `title` или `course_uid`.

- **200**: `CourseRead[]`

**Пример:**
```bash
curl "http://localhost:8000/api/v1/courses/search?q=Python&limit=10&api_key=bot-key-1"
```

### `GET /courses/by-code/{code}`
Получить курс по `course_uid`.

- **200**: `CourseRead`
- **404**: DomainError `Курс с указанным кодом не найден`

### `GET /courses/access-levels` ⭐ ДЛЯ UI
Получить список всех уровней доступа с метаданными для отображения в UI.

**Использование:** Этот эндпойнт предназначен для получения списка всех доступных уровней доступа с их русскими названиями, короткими названиями для кнопок и описаниями. Используется в мастере создания курса для отображения выбора уровня доступа.

**Ответ:** `AccessLevelInfo[]`

Поля каждого элемента:
- `value` (string) - машинное значение уровня доступа (для API), например `"auto_check"`
- `display_name` (string) - полное русское название, например `"Автопроверяемый"`
- `short_name` (string) - короткое название для кнопок (до 15 символов), например `"Автопроверка"`
- `description` (string) - описание уровня доступа, например `"Задания проверяются автоматически системой"`

**Статусы:**
- **200**: `AccessLevelInfo[]` - список всех уровней доступа
- **403**: Invalid or missing API Key

**Пример запроса:**
```bash
curl "http://localhost:8000/api/v1/courses/access-levels?api_key=bot-key-1"
```

**Пример ответа:**
```json
[
  {
    "value": "self_guided",
    "display_name": "Самостоятельный",
    "short_name": "Самостоятельный",
    "description": "Студент изучает материал самостоятельно, без проверки"
  },
  {
    "value": "auto_check",
    "display_name": "Автопроверяемый",
    "short_name": "Автопроверка",
    "description": "Задания проверяются автоматически системой"
  },
  {
    "value": "manual_check",
    "display_name": "Ручная проверка",
    "short_name": "Ручная проверка",
    "description": "Задания проверяются преподавателем вручную"
  },
  {
    "value": "group_sessions",
    "display_name": "Групповые занятия",
    "short_name": "Групповой",
    "description": "Групповые занятия с преподавателем"
  },
  {
    "value": "personal_teacher",
    "display_name": "Персональный преподаватель",
    "short_name": "Персональный",
    "description": "Индивидуальные занятия с персональным преподавателем"
  }
]
```

**Рекомендации по использованию в UI:**
- Используйте `short_name` для текста на кнопках (оптимизировано для компактного отображения)
- Используйте `display_name` для заголовков и полных описаний
- Используйте `description` для tooltips и подсказок при наведении
- **ВАЖНО:** Располагайте кнопки в **2-3 ряда**, не в один ряд, чтобы надписи были читаемы
- Рекомендуемая компоновка: 2-3 кнопки в ряд, максимум 3 ряда

**Пример компоновки кнопок:**
```
[Самостоятельный] [Автопроверка]
[Ручная проверка]  [Групповой]
[Персональный]
```

Подробные рекомендации по UI см. в [docs/ui-access-levels-recommendations.md](ui-access-levels-recommendations.md).

### `GET /courses/roots`
Получить корневые курсы (курсы без родителей, где `parent_course_ids = []`).

- **200**: `CourseRead[]` - список корневых курсов

### `GET /courses/{course_id}/children`
Получить прямых детей курса с порядковыми номерами.

- **200**: `CourseWithOrderNumber[]` - список дочерних курсов с полем `order_number`
  - Каждый элемент содержит все поля `CourseRead` плюс `order_number` (порядковый номер подкурса внутри родительского курса)
  - Сортировка: по `order_number` (NULL в конце), затем по `id`
  - `order_number` может быть `null`, если порядковый номер не установлен (устанавливается автоматически триггером БД)

**Пример ответа:**
```json
[
  {
    "id": 2,
    "title": "Анализ данных",
    "access_level": "manual_check",
    "description": "Курс по анализу данных",
    "parent_course_ids": [1],
    "created_at": "2026-01-24T12:00:00Z",
    "is_required": false,
    "course_uid": null,
    "order_number": 1
  },
  {
    "id": 6,
    "title": "Test Course 1",
    "access_level": "self_guided",
    "description": "Test course",
    "parent_course_ids": [1],
    "created_at": "2026-01-24T12:00:00Z",
    "is_required": false,
    "course_uid": null,
    "order_number": 3
  }
]
```

**Примечание:** Порядковые номера автоматически управляются триггерами БД. См. `docs/database-triggers-contract.md`

### `GET /courses/{course_id}/tree`
Получить дерево курса с детьми всех уровней.

- **200**: `CourseTreeRead`
- **404**: DomainError `Курс не найден`

Примечание: если в БД нет детей — `children=[]`.

### `PATCH /courses/{course_id}/move`
Переместить курс в иерархии (изменить `parent_course_ids`).

**Тело:** `CourseMoveRequest`
- `new_parent_ids` (int[]|null): список ID новых родительских курсов
  - `null` или `[]` = сделать курс корневым (удалить все связи с родителями)
  - `[1, 2]` = установить несколько родителей одновременно (order_number установится автоматически)
- `new_parent_courses` (ParentCourseWithOrder[]|null): список новых родительских курсов с порядковыми номерами
  - Если указано, имеет приоритет над `new_parent_ids`
  - Позволяет задать порядковый номер подкурса внутри каждого родителя
- `replace_parents` (bool, default=false): режим работы с родителями
  - `false` (по умолчанию) = добавить новые связи к существующим
  - `true` = заменить все существующие связи новыми

**Важно:** Курс может иметь несколько родителей. Поведение при перемещении зависит от параметра `replace_parents`.

**Ошибки:**
- **404**: курс или один из новых родителей не найден
- **400**: попытка создать цикл в иерархии или самоссылку (валидация выполняется триггером БД)

**Пример запроса:**
```bash
# Добавить нового родителя к существующим (по умолчанию)
curl -X PATCH "http://localhost:8000/api/v1/courses/6/move?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"new_parent_ids": [1]}'

# Заменить всех родителей новыми
curl -X PATCH "http://localhost:8000/api/v1/courses/6/move?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"new_parent_ids": [1, 2], "replace_parents": true}'

# Переместить курс к нескольким родителям с указанием order_number
curl -X PATCH "http://localhost:8000/api/v1/courses/6/move?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "new_parent_courses": [
      {"parent_course_id": 1, "order_number": 1},
      {"parent_course_id": 2, "order_number": 1}
    ]
  }'

# Сделать курс корневым
curl -X PATCH "http://localhost:8000/api/v1/courses/6/move?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"new_parent_ids": []}'
```

### `PATCH /courses/{course_id}/parents/{parent_course_id}/order` ⭐ НОВЫЙ
Изменить порядковый номер подкурса у конкретного родительского курса.

**Параметры:**
- `course_id` (int, path) - ID дочернего курса
- `parent_course_id` (int, path) - ID родительского курса

**Тело:** `CourseParentOrderUpdate`
- `order_number` (int, required): новый порядковый номер подкурса внутри родительского курса
  - Если указан, триггер БД автоматически пересчитает порядковые номера остальных подкурсов
  - Порядковые номера уникальны в рамках одного родительского курса

**Правила:**
- Если `order_number` указан, триггер БД автоматически пересчитает порядковые номера остальных подкурсов родителя
- Порядковые номера уникальны в рамках одного родительского курса
- При изменении номера остальные подкурсы автоматически сдвигаются

**⚠️ ВАЖНО:** Пересчет `order_number` выполняется автоматически триггером БД. См. `docs/database-triggers-contract.md`

**Ошибки:**
- **404**: Курс, родительский курс или связь между ними не найдены
- **403**: Invalid or missing API Key

**Пример запроса:**
```bash
# Установить порядковый номер 1
curl -X PATCH "http://localhost:8000/api/v1/courses/6/parents/1/order?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"order_number": 1}'

# Установить порядковый номер 2
curl -X PATCH "http://localhost:8000/api/v1/courses/6/parents/1/order?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"order_number": 2}'
```

**Пример ответа:**
```json
{
  "id": 6,
  "title": "Подкурс",
  "access_level": "auto_check",
  "description": "Описание",
  "parent_course_ids": [1],
  "created_at": "2026-01-24T12:00:00Z",
  "is_required": false,
  "course_uid": null
}
```

### `GET /courses/{course_id}/users` ⭐ КРИТИЧНО
Получить список студентов (пользователей), привязанных к курсу.

**Параметры:**
- `course_id` (int, path) - ID курса
- `limit` (int, optional, default=100, max=1000) - максимум результатов на странице
- `offset` (int, optional, default=0) - смещение для пагинации

**Ответ:** `CourseUsersResponse`

Поля ответа:
- `course_id` (int) - ID курса
- `course_title` (string) - название курса
- `users` (List[UserCourseWithUser]) - список студентов с полной информацией о пользователях
  - Каждый элемент содержит: `user_id`, `course_id`, `added_at`, `order_number`, `user` (полная информация о пользователе)
- `total` (int) - общее количество студентов курса (для пагинации)

**Использование:**
- Для управления студентами курса со стороны методиста
- Для просмотра текущих студентов курса в боте
- Критически важно для полноценного функционала "управления студентами курса"

**Статусы:**
- **200**: `CourseUsersResponse` - успешно получен список студентов
- **404**: DomainError `Курс не найден` - курс с указанным ID не существует
- **403**: Invalid or missing API Key

**Пример запроса:**
```bash
curl "http://localhost:8000/api/v1/courses/2/users?limit=50&offset=0&api_key=bot-key-1"
```

**Пример ответа:**
```json
{
  "course_id": 2,
  "course_title": "Анализ данных",
  "total": 3,
  "users": [
    {
      "user_id": 4,
      "course_id": 2,
      "added_at": "2025-02-06T16:29:40.005259Z",
      "order_number": null,
      "user": {
        "id": 4,
        "email": "student@example.com",
        "full_name": "Иван Иванов",
        "tg_id": null,
        "created_at": "2025-02-06T11:42:52.667736Z"
      }
    }
  ]
}
```

---

## 3) Зависимости курсов (`/courses/{course_id}/dependencies/*`) (`app/api/v1/course_dependencies.py`)

### `GET /courses/{course_id}/dependencies/`
Список курсов, от которых зависит `course_id`.

- **200**: `CourseRead[]`

### `POST /courses/{course_id}/dependencies/{required_course_id}`
Добавить зависимость: `course_id` зависит от `required_course_id`.

- **204**: добавлено
- **404**: если курс/required_course не найдены
- **400**: если зависимость некорректна (например self-dependency)

### `DELETE /courses/{course_id}/dependencies/{required_course_id}`
Удалить зависимость.

- **204**: удалено

### `POST /courses/{course_id}/dependencies/bulk`
Массовое добавление зависимостей для курса.

**Параметры:**
- `course_id` (int, path) - ID курса, для которого добавляются зависимости

**Тело:** `CourseDependenciesBulkCreate`

Поля запроса:
- `required_course_ids` (int[], required, min_length=1) - список ID курсов-зависимостей для добавления

**Поведение:**
- Добавляет все зависимости из списка к указанному курсу
- Пропускает уже существующие зависимости (не создает дубликаты, используется `ON CONFLICT DO NOTHING`)
- Пропускает self-dependency автоматически (курс не может зависеть от самого себя)
- Пропускает несуществующие курсы (валидация выполняется перед добавлением)
- Возвращает список успешно добавленных зависимостей (только те, которые реально были добавлены)

**Статусы:**
- **201**: `CourseRead[]` - список успешно добавленных зависимостей
- **404**: Курс не найден (course_id не существует в БД)
- **400**: Пустой список зависимостей или некорректные данные
- **403**: Invalid or missing API Key

**Пример запроса:**
```bash
# PowerShell (правильный способ передачи JSON)
$json='{"required_course_ids":[7,8]}'
$json | curl.exe -s -S -X POST "http://localhost:8000/api/v1/courses/2/dependencies/bulk?api_key=bot-key-1" `
  -H "Content-Type: application/json" --data-binary '@-'

# Linux/Mac
curl -X POST "http://localhost:8000/api/v1/courses/2/dependencies/bulk?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"required_course_ids": [7, 8]}'
```

**Пример ответа:**
```json
[
  {
    "id": 7,
    "title": "Основы Python",
    "access_level": "self_guided",
    "description": "Введение в Python: переменные, типы данных, условия, циклы",
    "parent_course_ids": [],
    "created_at": "2026-01-21T11:46:41.947661Z",
    "is_required": false,
    "course_uid": "COURSE-PY-01"
  },
  {
    "id": 8,
    "title": "Математика для программистов",
    "access_level": "auto_check",
    "description": "Основы математики: алгебра, логика, теория множеств",
    "parent_course_ids": [],
    "created_at": "2026-01-21T11:46:42.037693Z",
    "is_required": true,
    "course_uid": "COURSE-MATH-01"
  }
]
```

**Примечания:**
- Если некоторые зависимости уже существуют, они будут пропущены, но ответ вернет только новые зависимости
- Если все зависимости уже существуют, вернется пустой массив `[]` (HTTP 201)
- Порядок в ответе соответствует порядку успешно добавленных зависимостей

---

## 4) Привязка пользователей к курсам — базовый CRUD (`/user-courses/*`) (`app/api/v1/user_courses.py`)

### `POST /user-courses/`
Создать связь user ↔ course.

Тело: `UserCourseCreate`

### `GET /user-courses/{user_id}/{course_id}`
Получить связь по составному ключу.

### `PUT /user-courses/{user_id}/{course_id}`
Обновить связь (обычно `order_number`).

### `DELETE /user-courses/{user_id}/{course_id}`
Удалить связь.

---

## 5) Привязка пользователей к курсам — “удобные” эндпойнты (`/users/{user_id}/courses*`) (`app/api/v1/user_courses_extra.py`)

### `GET /users/{user_id}/courses`
Получить список курсов пользователя с детальной информацией по курсу.

Query:
- `order_by_order` (bool, default=true)

Ответ: `UserCourseListResponse`

### `POST /users/{user_id}/courses/bulk`
Массовая привязка курсов к пользователю.

Тело: `UserCourseBulkCreate`

Ответ: `UserCourseRead[]`

Поведение:
- `order_number` выставляется триггером БД автоматически, если не передан явно
- уже существующие связи не дублируются

### `PATCH /users/{user_id}/courses/reorder`
Переупорядочить курсы пользователя.

Тело: `UserCourseReorderRequest`

Ответ: `UserCourseRead[]` (обновлённые связи)

---

## 6) Импорт курсов из Google Sheets (`/courses/import/google-sheets`) (`app/api/v1/courses_extra.py`)

### `POST /courses/import/google-sheets`

Массовый импорт курсов из Google Sheets таблицы в систему LMS.

**Поддерживаемые функции:**
- Иерархия курсов (parent_course_uid) - поддержка множественных родителей (список через запятую)
- Зависимости между курсами (required_courses_uid)
- Upsert по course_uid (если курс существует - обновляется, иначе создается)

**Тело запроса:** `GoogleSheetsImportRequest`

Поля:
- `spreadsheet_url` (string, required): URL таблицы Google Sheets или spreadsheet_id
- `sheet_name` (string, optional): название листа (по умолчанию "Courses")
- `column_mapping` (dict, optional): кастомный маппинг колонок
- `dry_run` (bool, default=false): режим проверки без сохранения

**Ответ:** `GoogleSheetsImportResponse`

Поля:
- `imported` (int): количество созданных курсов
- `updated` (int): количество обновленных курсов
- `errors` (GoogleSheetsImportError[]): список ошибок
- `total_rows` (int): общее количество обработанных строк

**Статусы:**
- **200**: импорт выполнен (возможно с ошибками в отдельных строках)
- **400**: неверные параметры запроса (пустая таблица, отсутствие заголовков)
- **403**: неверный или отсутствующий API ключ
- **500**: ошибка при чтении Google Sheets или обработке данных

**Требования к таблице:**
- Первая строка должна содержать заголовки колонок
- Обязательные колонки: `course_uid`, `title`, `access_level`
- Опциональные колонки: `description`, `parent_course_uid`, `order_number`, `required_courses_uid`, `is_required`
- `parent_course_uid` - один или несколько course_uid через запятую (например, 'COURSE-PY-01' или 'COURSE-PY-01,COURSE-MATH-01') для поддержки множественных родителей
- `order_number` - порядковый номер подкурса внутри родительского курса (целое положительное число). Используется только если указан `parent_course_uid`. Если не указан, порядковый номер устанавливается автоматически триггером БД.
- `required_courses_uid` - список course_uid через запятую (например, 'COURSE-PY-01,COURSE-MATH-01')

**Обработка ошибок:**
- Импорт продолжается даже при ошибках в отдельных строках
- Все ошибки возвращаются в массиве `errors` с указанием номера строки
- Частичный успех: некоторые курсы могут быть импортированы, другие - нет

**Пример запроса:**

```bash
curl -X POST "http://localhost:8000/api/v1/courses/import/google-sheets?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/185yB39jP8IF_SGJTpWMRXPHYYXF6FZz6Pji70O8Krhc/edit",
    "sheet_name": "Courses",
    "dry_run": false
  }'
```

**Пример ответа:**

```json
{
  "imported": 10,
  "updated": 0,
  "errors": [],
  "total_rows": 10
}
```

**Подробная документация:** см. [Мануал по импорту курсов](courses-import-manual.md)

---

## Swagger документация

Все эндпойнты курсов полностью документированы в Swagger UI:

- **Доступ:** `http://localhost:8000/docs` (Swagger UI) или `http://localhost:8000/redoc` (ReDoc)
- **Теги:** `courses`, `import`
- **Схемы:** все Pydantic схемы включают подробные описания и примеры
- **Примеры запросов:** каждый эндпойнт содержит примеры использования
- **Коды ответов:** все возможные статусы документированы с примерами

**Важно:** Swagger автоматически обновляется на основе Pydantic схем. Все изменения в схемах (включая миграцию на many-to-many для `parent_course_ids`) автоматически отражаются в Swagger UI.

**Ключевые схемы в Swagger:**
- `GoogleSheetsImportRequest` - запрос на импорт с примерами
- `GoogleSheetsImportResponse` - ответ с результатами импорта
- `GoogleSheetsImportError` - информация об ошибках импорта
- `CourseRead`, `CourseCreate`, `CourseUpdate` - основные схемы курсов
  - **Обновлено:** `parent_course_ids` теперь `List[int]` (поддержка множественных родителей)
  - **Новое:** `parent_courses` (List[ParentCourseWithOrder]) - поддержка указания порядковых номеров
- `ParentCourseWithOrder` - схема родительского курса с порядковым номером
  - `parent_course_id` (int) - ID родительского курса
  - `order_number` (int|null) - порядковый номер подкурса (автоматически устанавливается триггером БД, если null)
- `CourseMoveRequest` - схема для перемещения курса
  - **Обновлено:** `new_parent_ids` теперь `List[int]` (поддержка множественных родителей)
  - **Новое:** `new_parent_courses` (List[ParentCourseWithOrder]) - поддержка указания порядковых номеров
- `CourseParentOrderUpdate` - схема для изменения порядкового номера подкурса
  - `order_number` (int) - новый порядковый номер
- `CourseWithOrderNumber` - схема курса с порядковым номером (используется в GET /courses/{course_id}/children)
  - Расширяет `CourseRead`, добавляя поле `order_number` (int|null)
  - `order_number` - порядковый номер подкурса внутри родительского курса
- `CourseTreeRead`, `CourseReadWithChildren` - схемы для иерархии
- `CourseUsersResponse`, `UserCourseWithUser` - схемы для списка студентов курса (GET /courses/{course_id}/users)
- `CourseDependenciesBulkCreate` - схема для массового добавления зависимостей (POST /courses/{course_id}/dependencies/bulk)
- `UserRead` - схема пользователя (используется в `UserCourseWithUser`)

**Изменения в API:**
- **Миграция на many-to-many (2026-01-24):**
  - `parent_course_id` (int|null) → `parent_course_ids` (List[int])
  - `new_parent_id` (int|null) → `new_parent_ids` (List[int])
  - Курс теперь может иметь несколько родителей одновременно
- **Поддержка порядковых номеров (2026-01-24):**
  - Добавлено поле `parent_courses` в `CourseCreate` и `CourseUpdate`
  - Добавлено поле `new_parent_courses` в `CourseMoveRequest`
  - Добавлен эндпойнт `PATCH /courses/{id}/parents/{parent_id}/order` для изменения порядкового номера
  - Порядковые номера автоматически управляются триггерами БД (см. `docs/database-triggers-contract.md`)
- Все примеры в Swagger обновлены для отражения новой структуры

---

## Быстрые примеры запросов (curl)

```bash
# корневые курсы
curl "http://localhost:8000/api/v1/courses/roots?api_key=bot-key-1"

# дерево курса
curl "http://localhost:8000/api/v1/courses/1/tree?api_key=bot-key-1"

# получить детей курса (с order_number)
curl "http://localhost:8000/api/v1/courses/1/children?api_key=bot-key-1"

# переместить курс 6 под курс 1
curl -X PATCH "http://localhost:8000/api/v1/courses/6/move?api_key=bot-key-1" ^
  -H "Content-Type: application/json" ^
  -d "{\"new_parent_ids\": [1]}"

# переместить курс 6 к нескольким родителям с указанием order_number
curl -X PATCH "http://localhost:8000/api/v1/courses/6/move?api_key=bot-key-1" ^
  -H "Content-Type: application/json" ^
  -d "{\"new_parent_courses\": [{\"parent_course_id\": 1, \"order_number\": 1}, {\"parent_course_id\": 2, \"order_number\": 1}]}"

# изменить порядковый номер подкурса у родителя
curl -X PATCH "http://localhost:8000/api/v1/courses/6/parents/1/order?api_key=bot-key-1" ^
  -H "Content-Type: application/json" ^
  -d "{\"order_number\": 2}"

# получить курсы пользователя
curl "http://localhost:8000/api/v1/users/3/courses?api_key=bot-key-1"

# bulk assign
curl -X POST "http://localhost:8000/api/v1/users/3/courses/bulk?api_key=bot-key-1" ^
  -H "Content-Type: application/json" ^
  -d "{\"course_ids\":[1,2,6]}"

# reorder
curl -X PATCH "http://localhost:8000/api/v1/users/3/courses/reorder?api_key=bot-key-1" ^
  -H "Content-Type: application/json" ^
  -d "{\"course_orders\":[{\"course_id\":2,\"order_number\":1},{\"course_id\":1,\"order_number\":2}]}"

# импорт курсов из Google Sheets (dry run)
curl -X POST "http://localhost:8000/api/v1/courses/import/google-sheets?api_key=bot-key-1" ^
  -H "Content-Type: application/json" ^
  -d "{\"spreadsheet_url\":\"https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit\",\"sheet_name\":\"Courses\",\"dry_run\":true}"

# импорт курсов из Google Sheets (реальный импорт)
curl -X POST "http://localhost:8000/api/v1/courses/import/google-sheets?api_key=bot-key-1" ^
  -H "Content-Type: application/json" ^
  -d "{\"spreadsheet_url\":\"https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit\",\"sheet_name\":\"Courses\",\"dry_run\":false}"

# поиск курсов
curl "http://localhost:8000/api/v1/courses/search?q=Python&limit=10&api_key=bot-key-1"

# получить список студентов курса
curl "http://localhost:8000/api/v1/courses/1/users?limit=50&api_key=bot-key-1"

# массовое добавление зависимостей
curl -X POST "http://localhost:8000/api/v1/courses/1/dependencies/bulk?api_key=bot-key-1" ^
  -H "Content-Type: application/json" ^
  -d "{\"required_course_ids\":[2,3,4]}"
```

