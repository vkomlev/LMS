# Полная документация API Quiz системы

**Версия:** 2.0  
**Дата обновления:** 2026-01-17  
**Базовый URL:** `http://localhost:8000/api/v1`  
**Swagger UI:** `http://localhost:8000/docs`

---

## Содержание

1. [Общая информация](#общая-информация)
2. [Аутентификация](#аутентификация)
3. [Эндпойнты задач](#эндпойнты-задач)
4. [Эндпойнты проверки](#эндпойнты-проверки)
5. [Эндпойнты попыток](#эндпойнты-попыток)
6. [Эндпойнты результатов](#эндпойнты-результатов)
7. [Эндпойнты статистики](#эндпойнты-статистики)
8. [Эндпойнты материалов](#эндпойнты-материалов)
9. [Эндпойнты импорта](#эндпойнты-импорта)
10. [Коды ошибок](#коды-ошибок)

---

## Общая информация

### Формат ответов

Все ответы возвращаются в формате JSON с кодировкой UTF-8.

### Пагинация

Эндпойнты, возвращающие списки, поддерживают пагинацию через query-параметры:
- `limit` (int, 1-1000) - количество записей на странице
- `offset` (int, ≥0) - смещение для пагинации

### Версионирование

API использует версионирование через префикс пути: `/api/v1/`

---

## Аутентификация

Все эндпойнты требуют API ключ, передаваемый через query-параметр:

```
?api_key=your-api-key
```

**Пример:**
```
GET /api/v1/tasks/1?api_key=bot-key-1
```

**Ошибка аутентификации (403):**
```json
{
  "detail": "Invalid or missing API Key"
}
```

---

## Эндпойнты задач

### GET /tasks/by-course/{course_id}

Получить список задач курса с фильтрацией и пагинацией.

**Параметры:**
- `course_id` (path, int) - ID курса
- `difficulty_id` (query, int, optional) - Фильтр по уровню сложности
- `limit` (query, int, default: 100) - Максимум записей на странице
- `offset` (query, int, default: 0) - Смещение

**Ответ (200 OK):**
```json
[
  {
    "id": 1,
    "external_uid": "TASK-SC-001",
    "task_content": {
      "type": "SC",
      "stem": "Что такое переменная в Python?",
      "options": [
        {"id": "A", "text": "Область памяти", "is_active": true},
        {"id": "B", "text": "Функция", "is_active": true}
      ]
    },
    "solution_rules": {
      "max_score": 10,
      "correct_options": ["A"]
    },
    "course_id": 1,
    "difficulty_id": 3,
    "max_score": 10
  }
]
```

**Ошибки:**
- `404` - Курс не найден

---

### GET /tasks/by-external/{external_uid}

Получить задачу по внешнему идентификатору.

**Параметры:**
- `external_uid` (path, string) - Внешний идентификатор задачи

**Ответ (200 OK):**
```json
{
  "id": 1,
  "external_uid": "TASK-SC-001",
  "task_content": {...},
  "solution_rules": {...},
  "course_id": 1,
  "difficulty_id": 3,
  "max_score": 10
}
```

**Ошибки:**
- `404` - Задача не найдена

---

### POST /tasks/validate

Предварительная валидация задачи перед импортом.

**Тело запроса:**
```json
{
  "task_content": {
    "type": "SC",
    "stem": "Что такое переменная?",
    "options": [
      {"id": "A", "text": "Область памяти", "is_active": true},
      {"id": "B", "text": "Функция", "is_active": true}
    ]
  },
  "solution_rules": {
    "max_score": 10,
    "correct_options": ["A"]
  },
  "course_code": "PY",
  "difficulty_code": "NORMAL",
  "external_uid": "TASK-SC-001"
}
```

**Ответ (200 OK):**
```json
{
  "is_valid": true,
  "errors": []
}
```

или

```json
{
  "is_valid": false,
  "errors": [
    "course_code not provided",
    "Validation error: Для задач типа SC должен быть указан ровно один правильный вариант. Указано: 2"
  ]
}
```

---

### POST /tasks/bulk-upsert

Массовый upsert задач по external_uid.

**Тело запроса:**
```json
{
  "items": [
    {
      "external_uid": "TASK-SC-001",
      "course_id": 1,
      "difficulty_id": 3,
      "task_content": {...},
      "solution_rules": {...},
      "max_score": 10
    }
  ]
}
```

**Ответ (200 OK):**
```json
{
  "results": [
    {"external_uid": "TASK-SC-001", "action": "created", "id": 1},
    {"external_uid": "TASK-SC-002", "action": "updated", "id": 2}
  ]
}
```

**Ошибки:**
- `400` - Ошибка валидации данных задач
- `422` - Ошибка валидации запроса

---

### POST /tasks/find-by-external

Массовое получение задач по списку external_uid.

**Тело запроса:**
```json
{
  "external_uids": ["TASK-SC-001", "TASK-SC-002"]
}
```

**Ответ (200 OK):**
```json
{
  "items": [
    {"id": 1, "external_uid": "TASK-SC-001", ...},
    {"id": 2, "external_uid": "TASK-SC-002", ...}
  ],
  "not_found": []
}
```

---

## Эндпойнты проверки

### POST /check/task

Stateless-проверка одной задачи без сохранения в БД.

**Тело запроса:**
```json
{
  "task_content": {
    "type": "SC",
    "stem": "Что такое переменная?",
    "options": [
      {"id": "A", "text": "Область памяти", "is_active": true},
      {"id": "B", "text": "Функция", "is_active": true}
    ]
  },
  "solution_rules": {
    "max_score": 10,
    "scoring_mode": "all_or_nothing",
    "correct_options": ["A"],
    "penalties": {
      "wrong_answer": 0,
      "missing_answer": 0,
      "extra_wrong_mc": 0
    }
  },
  "answer": {
    "type": "SC",
    "response": {
      "selected_option_ids": ["A"]
    }
  }
}
```

**Ответ (200 OK):**
```json
{
  "score": 10,
  "max_score": 10,
  "is_correct": true,
  "details": {
    "correct_options": ["A"],
    "user_options": ["A"],
    "matched_short_answer": null,
    "rubric_scores": null
  },
  "feedback": {
    "general": "Правильно!",
    "by_option": {
      "A": "Правильно! Переменная действительно хранит данные в памяти."
    }
  }
}
```

**Ошибки:**
- `400` - Ошибка валидации данных задачи или ответа
- `422` - Ошибка валидации запроса

---

### POST /check/batch

Массовая проверка нескольких задач.

**Тело запроса:**
```json
{
  "items": [
    {
      "task_content": {...},
      "solution_rules": {...},
      "answer": {...}
    }
  ]
}
```

**Ответ (200 OK):**
```json
{
  "results": [
    {
      "score": 10,
      "max_score": 10,
      "is_correct": true,
      "details": {...},
      "feedback": {...}
    }
  ]
}
```

---

## Эндпойнты попыток

### GET /attempts/by-user/{user_id}

Получить список попыток пользователя.

**Параметры:**
- `user_id` (path, int) - ID пользователя
- `course_id` (query, int, optional) - Фильтр по курсу
- `limit` (query, int, default: 100) - Максимум записей
- `offset` (query, int, default: 0) - Смещение

**Ответ (200 OK):**
```json
[
  {
    "id": 1,
    "user_id": 10,
    "course_id": 1,
    "source_system": "web",
    "created_at": "2026-01-17T12:00:00Z",
    "finished_at": null,
    "meta": {}
  }
]
```

---

### POST /attempts

Создать новую попытку прохождения теста.

**Тело запроса:**
```json
{
  "user_id": 10,
  "course_id": 1,
  "source_system": "web",
  "meta": {
    "time_limit": 3600
  }
}
```

**Ответ (201 Created):**
```json
{
  "id": 1,
  "user_id": 10,
  "course_id": 1,
  "source_system": "web",
  "created_at": "2026-01-17T12:00:00Z",
  "finished_at": null,
  "meta": {"time_limit": 3600}
}
```

---

### POST /attempts/{attempt_id}/answers

Отправить ответы по задачам внутри попытки.

**Параметры:**
- `attempt_id` (path, int) - ID попытки

**Тело запроса:**
```json
{
  "items": [
    {
      "task_id": 1,
      "answer": {
        "type": "SC",
        "response": {
          "selected_option_ids": ["A"]
        }
      }
    }
  ]
}
```

**Ответ (200 OK):**
```json
{
  "attempt_id": 1,
  "total_score": 25,
  "max_score": 30,
  "results": [
    {
      "task_id": 1,
      "score": 10,
      "max_score": 10,
      "is_correct": true
    },
    {
      "task_id": 2,
      "score": 15,
      "max_score": 20,
      "is_correct": false
    }
  ]
}
```

**Ошибки:**
- `400` - Попытка уже завершена или истекло время
- `404` - Попытка не найдена
- `422` - Ошибка валидации запроса

---

## Эндпойнты результатов

### GET /task-results/by-user/{user_id}

Получить результаты пользователя.

**Параметры:**
- `user_id` (path, int) - ID пользователя
- `limit` (query, int, default: 100) - Максимум записей
- `offset` (query, int, default: 0) - Смещение

**Ответ (200 OK):**
```json
[
  {
    "id": 1,
    "attempt_id": 1,
    "task_id": 1,
    "user_id": 10,
    "score": 10,
    "max_score": 10,
    "is_correct": true,
    "submitted_at": "2026-01-17T12:00:00Z",
    "metrics": {},
    "feedback": []
  }
]
```

---

### GET /task-results/by-task/{task_id}

Получить результаты по задаче.

**Параметры:**
- `task_id` (path, int) - ID задачи
- `limit` (query, int, default: 100) - Максимум записей
- `offset` (query, int, default: 0) - Смещение

**Ответ (200 OK):**
```json
[
  {
    "id": 1,
    "attempt_id": 1,
    "task_id": 1,
    "user_id": 10,
    "score": 10,
    "max_score": 10,
    "is_correct": true,
    "submitted_at": "2026-01-17T12:00:00Z"
  }
]
```

**Ошибки:**
- `404` - Задача не найдена

---

### GET /task-results/by-attempt/{attempt_id}

Получить результаты по попытке.

**Параметры:**
- `attempt_id` (path, int) - ID попытки
- `limit` (query, int, default: 100) - Максимум записей
- `offset` (query, int, default: 0) - Смещение

**Ответ (200 OK):**
```json
[
  {
    "id": 1,
    "attempt_id": 1,
    "task_id": 1,
    "user_id": 10,
    "score": 10,
    "max_score": 10,
    "is_correct": true,
    "submitted_at": "2026-01-17T12:00:00Z"
  }
]
```

**Ошибки:**
- `404` - Попытка не найдена

---

### POST /task-results/{result_id}/manual-check

Ручная дооценка результата задачи.

**Параметры:**
- `result_id` (path, int) - ID результата

**Тело запроса:**
```json
{
  "score": 8,
  "checked_by": 2,
  "is_correct": false,
  "metrics": {
    "comment": "Частично верно"
  }
}
```

**Ответ (200 OK):**
```json
{
  "id": 1,
  "attempt_id": 1,
  "task_id": 1,
  "user_id": 10,
  "score": 8,
  "max_score": 10,
  "is_correct": false,
  "checked_at": "2026-01-17T13:00:00Z",
  "checked_by": 2,
  "metrics": {
    "comment": "Частично верно"
  }
}
```

**Ошибки:**
- `400` - Неверные параметры запроса (например, score > max_score)
- `404` - Результат не найден

---

## Эндпойнты статистики

### GET /task-results/stats/by-task/{task_id}

Статистика по задаче.

**Параметры:**
- `task_id` (path, int) - ID задачи

**Ответ (200 OK):**
```json
{
  "task_id": 1,
  "total_attempts": 10,
  "average_score": 7.5,
  "correct_percentage": 60.0,
  "min_score": 0,
  "max_score": 10,
  "score_distribution": {
    "0": 2,
    "5": 2,
    "10": 6
  }
}
```

**Ошибки:**
- `404` - Задача не найдена

---

### GET /task-results/stats/by-course/{course_id}

Статистика по курсу.

**Параметры:**
- `course_id` (path, int) - ID курса

**Ответ (200 OK):**
```json
{
  "course_id": 1,
  "total_attempts": 50,
  "average_score": 75.5,
  "correct_percentage": 65.0,
  "tasks_count": 28
}
```

**Ошибки:**
- `404` - Курс не найден

---

### GET /task-results/stats/by-user/{user_id}

Статистика по пользователю.

**Параметры:**
- `user_id` (path, int) - ID пользователя

**Ответ (200 OK):**
```json
{
  "user_id": 10,
  "total_attempts": 5,
  "average_score": 8.0,
  "correct_percentage": 80.0,
  "total_score": 40,
  "total_max_score": 50,
  "completion_percentage": 80.0
}
```

**Ошибки:**
- `404` - Пользователь не найден

---

## Эндпойнты материалов

API учебных материалов курса: CRUD, список по курсу, изменение порядка, перемещение, массовое обновление активности, копирование в другой курс, статистика, импорт из Google Sheets.

**Полная документация:** [API учебных материалов](materials-api.md)

Основные эндпойнты:

- **GET** `/courses/{course_id}/materials` — список материалов курса (фильтры: is_active, type; сортировка: order_position, title, created_at; пагинация skip/limit)
- **POST** `/materials` — создание материала
- **GET** `/materials/{id}` — получение материала
- **PATCH** `/materials/{id}` — обновление материала
- **DELETE** `/materials/{id}` — удаление материала
- **POST** `/courses/{course_id}/materials/reorder` — изменить порядок материалов
- **POST** `/materials/{material_id}/move` — переместить материал (позиция или другой курс)
- **POST** `/courses/{course_id}/materials/bulk-update` — массовое обновление is_active
- **POST** `/materials/{material_id}/copy` — копировать материал в другой курс
- **GET** `/courses/{course_id}/materials/stats` — статистика по материалам курса
- **POST** `/materials/import/google-sheets` — импорт материалов из Google Таблицы (многокурсовой; dry_run поддерживается)

Типы материалов: `text`, `video`, `audio`, `image`, `link`, `pdf`, `office_document`. Структура поля `content` зависит от типа — см. [materials-api.md](materials-api.md).

---

## Эндпойнты импорта

### POST /tasks/import/google-sheets

Импорт задач из Google Sheets.

**Тело запроса:**
```json
{
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit",
  "sheet_name": "Лист1",
  "course_code": "PY",
  "difficulty_code": "NORMAL",
  "dry_run": false,
  "column_mapping": {
    "ID": "external_uid",
    "Тип": "type",
    "Вопрос": "stem"
  }
}
```

**Ответ (200 OK):**
```json
{
  "imported": 10,
  "updated": 0,
  "errors": [],
  "total_rows": 10
}
```

или с ошибками:

```json
{
  "imported": 8,
  "updated": 0,
  "errors": [
    {
      "row": 3,
      "message": "Ошибка валидации: Для задач типа SC должен быть указан ровно один правильный вариант"
    }
  ],
  "total_rows": 10
}
```

**Ошибки:**
- `400` - Неверные параметры запроса
- `403` - Неверный или отсутствующий API ключ
- `404` - Курс или уровень сложности не найден
- `500` - Ошибка при чтении Google Sheets

**Подробная документация:** [Импорт из Google Sheets](./import-api-documentation.md)

---

### POST /materials/import/google-sheets

Импорт учебных материалов из Google Таблицы. Многокурсовой импорт: курс для каждой строки задаётся полем `course_uid` в таблице. Upsert по паре (course_id, external_uid). Поддерживается `dry_run`.

**Тело запроса:** `spreadsheet_url`, `sheet_name` (по умолчанию "Materials"), `dry_run`, `column_mapping` (опционально).

**Подробная документация:** [API учебных материалов — Импорт из Google Sheets](materials-api.md#импорт-из-google-sheets)

---

## Коды ошибок

### 400 Bad Request

Ошибка валидации данных:

```json
{
  "error": "domain_error",
  "detail": "Ошибка валидации данных задачи: Для задач типа SC должен быть указан ровно один правильный вариант. Указано: 2"
}
```

### 403 Forbidden

Неверный или отсутствующий API ключ:

```json
{
  "detail": "Invalid or missing API Key"
}
```

### 404 Not Found

Ресурс не найден:

```json
{
  "error": "domain_error",
  "detail": "Задача с указанным external_uid не найдена",
  "payload": {
    "external_uid": "TASK-NOT-FOUND"
  }
}
```

### 422 Unprocessable Entity

Ошибка валидации запроса (неверный формат JSON):

```json
{
  "detail": [
    {
      "loc": ["body", "task_content", "type"],
      "msg": "value is not a valid enumeration member; permitted: 'SC', 'MC', 'SA', 'SA_COM', 'TA'",
      "type": "type_error.enum"
    }
  ]
}
```

### 500 Internal Server Error

Внутренняя ошибка сервера:

```json
{
  "detail": "Internal server error"
}
```

---

## Дополнительные ресурсы

- [Примеры использования API](./api-examples.md) - Подробные примеры запросов и ответов
- [Документация импорта из Google Sheets](./import-api-documentation.md) - Полное руководство по импорту
- [Краткая шпаргалка по импорту](./import-quick-start.md) - Быстрый старт
- [Swagger UI](http://localhost:8000/docs) - Интерактивная документация API
- [Форматы JSONB полей](./api-examples.md#форматы-jsonb-полей) - Описание структуры TaskContent и SolutionRules

---

## Изменения в версии 2.0

### Новые эндпойнты:
- ✅ `GET /tasks/by-course/{course_id}` - Фильтрация задач по курсу
- ✅ `GET /attempts/by-user/{user_id}` - Получение попыток пользователя
- ✅ `GET /task-results/by-user/{user_id}` - Результаты пользователя
- ✅ `GET /task-results/by-task/{task_id}` - Результаты по задаче
- ✅ `GET /task-results/by-attempt/{attempt_id}` - Результаты по попытке
- ✅ `POST /task-results/{result_id}/manual-check` - Ручная дооценка
- ✅ `GET /task-results/stats/by-task/{task_id}` - Статистика по задаче
- ✅ `GET /task-results/stats/by-course/{course_id}` - Статистика по курсу
- ✅ `GET /task-results/stats/by-user/{user_id}` - Статистика по пользователю
- ✅ `POST /tasks/import/google-sheets` - Импорт из Google Sheets

### Улучшения:
- ✅ Валидация JSONB полей (TaskContent, SolutionRules)
- ✅ Поддержка custom scoring mode
- ✅ Применение штрафов (penalties)
- ✅ Генерация обратной связи (feedback)
- ✅ Валидация попыток при отправке ответов
- ✅ Поддержка таймлимитов для попыток
