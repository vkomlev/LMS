# Примеры использования API Quiz системы

**Версия:** 2.0  
**Дата обновления:** 2026-01-17  
**Базовый URL:** `http://localhost:8000/api/v1`  
**Swagger UI:** `http://localhost:8000/docs`

> 📖 **Полная документация:** См. [API Reference](./api-reference.md) для полного списка всех эндпойнтов.

---

## Содержание

1. [Форматы JSONB полей](#форматы-jsonb-полей)
2. [Эндпойнты задач](#эндпойнты-задач)
3. [Эндпойнты проверки](#эндпойнты-проверки)
4. [Эндпойнты попыток](#эндпойнты-попыток)
5. [Эндпойнты результатов](#эндпойнты-результатов)
6. [Эндпойнты статистики](#эндпойнты-статистики)
7. [Эндпойнты импорта](#эндпойнты-импорта)
8. [Примеры ошибок](#примеры-ошибок)

---

## Форматы JSONB полей

### TaskContent (task_content)

Структура поля `task_content` в таблице `tasks`. Описывает то, что видит ученик.

#### Пример для SC (Single Choice)

```json
{
  "type": "SC",
  "code": "PY-VAR-001",
  "title": "Переменные Python",
  "stem": "Что такое переменная в Python?",
  "prompt": "Переменная хранит значение, которое можно изменять",
  "options": [
    {
      "id": "A",
      "text": "Именованная область памяти для хранения данных",
      "explanation": "Правильно! Переменная действительно хранит данные в памяти.",
      "is_active": true
    },
    {
      "id": "B",
      "text": "Функция для вывода данных",
      "explanation": "Неверно. Функция print() используется для вывода, а не переменная.",
      "is_active": true
    },
    {
      "id": "C",
      "text": "Тип данных",
      "explanation": "Неверно. Тип данных - это int, str, list и т.д., а не переменная.",
      "is_active": true
    }
  ],
  "tags": ["python", "variables", "basics"],
  "media": {
    "image_url": "https://example.com/image.png"
  }
}
```

#### Пример для MC (Multiple Choice)

```json
{
  "type": "MC",
  "stem": "Какие из перечисленных способов создают пустой список в Python?",
  "options": [
    {
      "id": "A",
      "text": "list()",
      "is_active": true
    },
    {
      "id": "B",
      "text": "[]",
      "is_active": true
    },
    {
      "id": "C",
      "text": "[1, 2, 3]",
      "is_active": true
    },
    {
      "id": "D",
      "text": "list(range(3))",
      "is_active": true
    }
  ],
  "tags": ["python", "lists"]
}
```

#### Пример для SA (Short Answer)

```json
{
  "type": "SA",
  "stem": "Сколько элементов в списке [1, 2, 3, 4, 5]?",
  "prompt": "Введите число",
  "tags": ["python", "lists", "len"]
}
```

#### Пример для TA (Text Answer)

```json
{
  "type": "TA",
  "stem": "Объясните разницу между методами append() и extend() для списков в Python.",
  "prompt": "Приведите примеры использования каждого метода",
  "tags": ["python", "lists", "methods"]
}
```

### SolutionRules (solution_rules)

Структура поля `solution_rules` в таблице `tasks`. Описывает, как задача проверяется и как начисляются баллы.

#### Пример для SC

```json
{
  "max_score": 10,
  "scoring_mode": "all_or_nothing",
  "auto_check": true,
  "manual_review_required": false,
  "correct_options": ["A"],
  "penalties": {
    "wrong_answer": 0,
    "missing_answer": 0,
    "extra_wrong_mc": 0
  }
}
```

#### Пример для MC с частичным оцениванием

```json
{
  "max_score": 15,
  "scoring_mode": "partial",
  "auto_check": true,
  "correct_options": ["A", "B"],
  "partial_rules": [
    {
      "selected": ["A"],
      "score": 8
    },
    {
      "selected": ["B"],
      "score": 7
    },
    {
      "selected": ["A", "B"],
      "score": 15
    }
  ],
  "penalties": {
    "wrong_answer": 0,
    "missing_answer": 0,
    "extra_wrong_mc": 2
  }
}
```

#### Пример для SA

```json
{
  "max_score": 10,
  "scoring_mode": "all_or_nothing",
  "auto_check": true,
  "short_answer": {
    "normalization": ["trim", "lower"],
    "accepted_answers": [
      {
        "value": "8",
        "score": 10
      },
      {
        "value": "восемь",
        "score": 10
      }
    ],
    "use_regex": false
  },
  "penalties": {
    "wrong_answer": 0,
    "missing_answer": 0,
    "extra_wrong_mc": 0
  }
}
```

#### Пример для TA

```json
{
  "max_score": 20,
  "scoring_mode": "all_or_nothing",
  "auto_check": false,
  "manual_review_required": true,
  "text_answer": {
    "auto_check": false,
    "rubric": [
      {
        "id": "content",
        "title": "Содержание",
        "max_score": 10
      },
      {
        "id": "style",
        "title": "Стиль изложения",
        "max_score": 5
      },
      {
        "id": "grammar",
        "title": "Грамматика",
        "max_score": 5
      }
    ]
  },
  "penalties": {
    "wrong_answer": 0,
    "missing_answer": 0,
    "extra_wrong_mc": 0
  }
}
```

---

## Эндпойнты задач

### POST /api/v1/tasks

Создание новой задачи.

#### Запрос

```bash
curl -X POST "http://localhost:8000/api/v1/tasks?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "task_content": {
      "type": "SC",
      "stem": "Что такое переменная в Python?",
      "options": [
        {
          "id": "A",
          "text": "Именованная область памяти для хранения данных",
          "is_active": true
        },
        {
          "id": "B",
          "text": "Функция для вывода данных",
          "is_active": true
        }
      ]
    },
    "solution_rules": {
      "max_score": 10,
      "correct_options": ["A"]
    },
    "course_id": 1,
    "difficulty_id": 3,
    "max_score": 10,
    "external_uid": "TASK-SC-001"
  }'
```

#### Ответ (201 Created)

```json
{
  "id": 1,
  "external_uid": "TASK-SC-001",
  "task_content": {
    "type": "SC",
    "stem": "Что такое переменная в Python?",
    "options": [
      {
        "id": "A",
        "text": "Именованная область памяти для хранения данных",
        "is_active": true
      },
      {
        "id": "B",
        "text": "Функция для вывода данных",
        "is_active": true
      }
    ]
  },
  "solution_rules": {
    "max_score": 10,
    "correct_options": ["A"],
    "penalties": {
      "wrong_answer": 0,
      "missing_answer": 0,
      "extra_wrong_mc": 0
    }
  },
  "course_id": 1,
  "difficulty_id": 3,
  "max_score": 10
}
```

#### Ошибки

**400 Bad Request** - Ошибка валидации данных:
```json
{
  "error": "domain_error",
  "detail": "Ошибка валидации данных задачи: Для задач типа SC должен быть указан ровно один правильный вариант. Указано: 2"
}
```

**404 Not Found** - Курс или уровень сложности не найден:
```json
{
  "error": "domain_error",
  "detail": "Курс с ID 999 не найден"
}
```

### GET /api/v1/tasks/by-external/{external_uid}

Получение задачи по внешнему идентификатору.

#### Запрос

```bash
curl "http://localhost:8000/api/v1/tasks/by-external/TASK-SC-001?api_key=bot-key-1"
```

#### Ответ (200 OK)

```json
{
  "id": 1,
  "external_uid": "TASK-SC-001",
  "task_content": {
    "type": "SC",
    "stem": "Что такое переменная в Python?",
    "options": [...]
  },
  "solution_rules": {...},
  "course_id": 1,
  "difficulty_id": 3,
  "max_score": 10
}
```

#### Ошибки

**404 Not Found** - Задача не найдена:
```json
{
  "error": "domain_error",
  "detail": "Задача с указанным external_uid не найдена",
  "payload": {
    "external_uid": "TASK-NOT-FOUND"
  }
}
```

### POST /api/v1/tasks/validate

Предварительная валидация задачи перед импортом.

#### Запрос

```bash
curl -X POST "http://localhost:8000/api/v1/tasks/validate?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
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
  }'
```

#### Ответ (200 OK) - Валидная задача

```json
{
  "is_valid": true,
  "errors": []
}
```

#### Ответ (200 OK) - Невалидная задача

```json
{
  "is_valid": false,
  "errors": [
    "course_code not provided",
    "Validation error: Для задач типа SC должен быть указан ровно один правильный вариант. Указано: 2"
  ]
}
```

### POST /api/v1/tasks/bulk-upsert

Массовый upsert задач.

#### Запрос

```bash
curl -X POST "http://localhost:8000/api/v1/tasks/bulk-upsert?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "external_uid": "TASK-SC-001",
        "course_id": 1,
        "difficulty_id": 3,
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
        "max_score": 10
      },
      {
        "external_uid": "TASK-SC-002",
        "course_id": 1,
        "difficulty_id": 3,
        "task_content": {
          "type": "SC",
          "stem": "Какой оператор для целочисленного деления?",
          "options": [
            {"id": "A", "text": "/", "is_active": true},
            {"id": "B", "text": "//", "is_active": true}
          ]
        },
        "solution_rules": {
          "max_score": 10,
          "correct_options": ["B"]
        },
        "max_score": 10
      }
    ]
  }'
```

#### Ответ (200 OK)

```json
{
  "results": [
    {
      "external_uid": "TASK-SC-001",
      "action": "created",
      "id": 1
    },
    {
      "external_uid": "TASK-SC-002",
      "action": "updated",
      "id": 2
    }
  ]
}
```

### GET /api/v1/tasks/by-course/{course_id}

Получение задач по курсу.

#### Запрос

```bash
curl "http://localhost:8000/api/v1/tasks/by-course/1?api_key=bot-key-1&skip=0&limit=10"
```

#### Ответ (200 OK)

```json
{
  "items": [
    {
      "id": 1,
      "external_uid": "TASK-SC-001",
      "task_content": {...},
      "solution_rules": {...},
      "course_id": 1,
      "difficulty_id": 3,
      "max_score": 10
    }
  ],
  "meta": {
    "total": 25,
    "limit": 10,
    "offset": 0
  }
}
```

---

## Эндпойнты проверки

### POST /api/v1/check/task

Проверка одной задачи (stateless).

#### Запрос

```bash
curl -X POST "http://localhost:8000/api/v1/check/task?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "task_content": {
      "type": "SC",
      "stem": "Что такое переменная в Python?",
      "options": [
        {
          "id": "A",
          "text": "Именованная область памяти для хранения данных",
          "explanation": "Правильно!",
          "is_active": true
        },
        {
          "id": "B",
          "text": "Функция для вывода данных",
          "explanation": "Неверно.",
          "is_active": true
        }
      ]
    },
    "solution_rules": {
      "max_score": 10,
      "correct_options": ["A"],
      "penalties": {
        "wrong_answer": 0,
        "missing_answer": 0,
        "extra_wrong_mc": 0
      }
    },
    "answer": {
      "type": "SC",
      "selected_options": ["A"]
    }
  }'
```

#### Ответ (200 OK) - Правильный ответ

```json
{
  "score": 10,
  "max_score": 10,
  "is_correct": true,
  "feedback": [
    {
      "type": "correct",
      "message": "Правильно! Переменная действительно хранит данные в памяти."
    }
  ]
}
```

#### Ответ (200 OK) - Неправильный ответ

```json
{
  "score": 0,
  "max_score": 10,
  "is_correct": false,
  "feedback": [
    {
      "type": "incorrect",
      "message": "Неверно. Функция print() используется для вывода, а не переменная."
    }
  ]
}
```

#### Ответ (200 OK) - Отсутствующий ответ (с штрафом)

```json
{
  "score": 0,
  "max_score": 10,
  "is_correct": false,
  "feedback": [
    {
      "type": "missing",
      "message": "Ответ не предоставлен"
    }
  ]
}
```

### POST /api/v1/check/tasks-batch

Проверка набора задач (stateless).

#### Запрос

```bash
curl -X POST "http://localhost:8000/api/v1/check/tasks-batch?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "task_content": {...},
        "solution_rules": {...},
        "answer": {
          "type": "SC",
          "selected_options": ["A"]
        }
      },
      {
        "task_content": {...},
        "solution_rules": {...},
        "answer": {
          "type": "MC",
          "selected_options": ["A", "B"]
        }
      }
    ]
  }'
```

#### Ответ (200 OK)

```json
{
  "results": [
    {
      "index": 0,
      "result": {
        "score": 10,
        "max_score": 10,
        "is_correct": true,
        "feedback": []
      }
    },
    {
      "index": 1,
      "result": {
        "score": 15,
        "max_score": 20,
        "is_correct": false,
        "feedback": []
      }
    }
  ]
}
```

---

## Эндпойнты попыток

### POST /api/v1/attempts

Создание новой попытки.

#### Запрос

```bash
curl -X POST "http://localhost:8000/api/v1/attempts?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 1,
    "course_id": 1,
    "source_system": "web",
    "meta": {
      "time_limit": 3600,
      "task_ids": [1, 2, 3]
    }
  }'
```

#### Ответ (201 Created)

```json
{
  "id": 1,
  "user_id": 1,
  "course_id": 1,
  "source_system": "web",
  "created_at": "2026-01-17T12:00:00Z",
  "finished_at": null,
  "meta": {
    "time_limit": 3600,
    "task_ids": [1, 2, 3]
  }
}
```

### POST /api/v1/attempts/{attempt_id}/answers

Отправка ответов по задачам в рамках попытки.

#### Запрос

```bash
curl -X POST "http://localhost:8000/api/v1/attempts/1/answers?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "answers": [
      {
        "task_id": 1,
        "answer": {
          "type": "SC",
          "selected_options": ["A"]
        }
      },
      {
        "task_id": 2,
        "answer": {
          "type": "MC",
          "selected_options": ["A", "B"]
        }
      }
    ]
  }'
```

#### Ответ (200 OK)

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

#### Ошибки

**400 Bad Request** - Попытка уже завершена:
```json
{
  "detail": "Попытка уже завершена. Нельзя отправлять ответы в завершенную попытку."
}
```

**400 Bad Request** - Истекло время:
```json
{
  "detail": "Время на выполнение истекло"
}
```

**404 Not Found** - Попытка не найдена:
```json
{
  "detail": "Попытка с ID 999 не найдена"
}
```

### GET /api/v1/attempts/by-user/{user_id}

Получение попыток пользователя.

#### Запрос

```bash
curl "http://localhost:8000/api/v1/attempts/by-user/1?api_key=bot-key-1&skip=0&limit=10"
```

#### Ответ (200 OK)

```json
{
  "items": [
    {
      "id": 1,
      "user_id": 1,
      "course_id": 1,
      "source_system": "web",
      "created_at": "2026-01-17T12:00:00Z",
      "finished_at": null,
      "meta": {...}
    }
  ],
  "meta": {
    "total": 5,
    "limit": 10,
    "offset": 0
  }
}
```

---

## Примеры ошибок

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

- [Полная документация API](./api-reference.md) - Полный список всех эндпойнтов
- [API управления заданиями и результатами учеников](./assignments-and-results-api.md) - Подробная документация по эндпойнтам попыток, результатов заданий, ручной проверке и статистике
- [Документация импорта из Google Sheets](./import-api-documentation.md) - Подробное руководство по импорту
- [Краткая шпаргалка по импорту](./import-quick-start.md) - Быстрый старт
- [Swagger UI](http://localhost:8000/docs) - Интерактивная документация API

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
