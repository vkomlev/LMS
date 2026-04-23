# API управления заданиями и результатами учеников

**Версия:** 1.0  
**Дата обновления:** 2026-02-16  
**Базовый URL:** `http://localhost:8000/api/v1`  
**Swagger UI:** `http://localhost:8000/docs`

---

## Содержание

1. [Общая информация](#общая-информация)
2. [Эндпойнты заданий (Tasks)](#эндпойнты-заданий-tasks)
3. [Типы заданий и структура данных](#типы-заданий-и-структура-данных)
4. [Эндпойнты попыток (Attempts)](#эндпойнты-попыток-attempts)
5. [Эндпойнты результатов заданий (Task Results)](#эндпойнты-результатов-заданий-task-results)
6. [CRUD операции с результатами](#crud-операции-с-результатами)
7. [Эндпойнты статистики](#эндпойнты-статистики)
8. [Схемы данных](#схемы-данных)
9. [Примеры использования](#примеры-использования)
10. [Коды ошибок](#коды-ошибок)

---

## Общая информация

### Аутентификация

Все эндпойнты требуют API ключ, передаваемый через query-параметр:

```
?api_key=your-api-key
```

**Пример:**
```
GET /api/v1/attempts/1?api_key=bot-key-1
```

### Пагинация

Эндпойнты, возвращающие списки, поддерживают пагинацию через query-параметры:
- `limit` (int, 1-1000) - количество записей на странице (по умолчанию: 100)
- `offset` (int, ≥0) - смещение для пагинации (по умолчанию: 0)
- `skip` (int, ≥0) - альтернативное название для offset (используется в CRUD эндпойнтах)

### Формат ответов

Все ответы возвращаются в формате JSON с кодировкой UTF-8.

---

## Эндпойнты заданий (Tasks)

Задание (Task) представляет собой вопрос или задачу, которую должен выполнить ученик. Задание содержит формулировку вопроса, варианты ответов (для задач с выбором), правила проверки и оценивания.

### Типы заданий

Система поддерживает следующие типы заданий:

- **SC (Single Choice)** - Выбор одного варианта ответа из нескольких
- **MC (Multiple Choice)** - Выбор нескольких вариантов ответа
- **SA (Short Answer)** - Краткий текстовый или числовой ответ
- **SA_COM (Short Answer with Comments)** - Краткий ответ с комментариями
- **TA (Text Answer)** - Развернутый текстовый ответ (требует ручной проверки)

---

### GET /tasks/{task_id}

Получить задание по ID.

**Параметры:**
- `task_id` (path, int) - ID задания

**Ответ (200 OK):**
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
    "scoring_mode": "all_or_nothing",
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

**Ошибки:**
- `404` - Задание не найдено

---

### GET /tasks/

Получить список всех заданий с пагинацией.

**Параметры:**
- `skip` (query, int, default: 0) - Смещение для пагинации
- `limit` (query, int, default: 100) - Количество записей на странице

**Ответ (200 OK):**
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
    "total": 150,
    "limit": 100,
    "offset": 0
  }
}
```

---

### POST /tasks/

Создать новое задание.

**Тело запроса:**
```json
{
  "external_uid": "TASK-SC-001",
  "course_id": 1,
  "difficulty_id": 3,
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
    "scoring_mode": "all_or_nothing",
    "correct_options": ["A"],
    "penalties": {
      "wrong_answer": 0,
      "missing_answer": 0,
      "extra_wrong_mc": 0
    }
  },
  "max_score": 10
}
```

**Ответ (201 Created):**
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
- `400` - Ошибка валидации данных задания
- `404` - Курс или уровень сложности не найден
- `422` - Ошибка валидации запроса

---

### GET /tasks/by-course/{course_id}

Получить список заданий курса с фильтрацией и пагинацией.

**Параметры:**
- `course_id` (path, int) - ID курса
- `difficulty_id` (query, int, опциональный) - Фильтр по уровню сложности
- `limit` (query, int, default: 100) - Максимум записей на странице (1-1000)
- `offset` (query, int, default: 0) - Смещение для пагинации

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
    "max_score": 10
  }
]
```

**Ошибки:**
- `404` - Курс не найден

**Пример запроса:**
```bash
curl "http://localhost:8000/api/v1/tasks/by-course/1?api_key=bot-key-1&difficulty_id=3&limit=20&offset=0"
```

---

### GET /tasks/by-external/{external_uid}

Получить задание по внешнему идентификатору.

**Параметры:**
- `external_uid` (path, string) - Внешний идентификатор задания

**Ответ (200 OK):**
```json
{
  "id": 1,
  "external_uid": "TASK-SC-001",
  "task_content": {
    "type": "SC",
    "stem": "Что такое переменная в Python?",
    "options": [...]
  },
  "solution_rules": {
    "max_score": 10,
    "correct_options": ["A"]
  },
  "course_id": 1,
  "difficulty_id": 3,
  "max_score": 10
}
```

**Ошибки:**
- `404` - Задание не найдено

**Примечания:**
- `external_uid` используется для интеграции с внешними системами
- Позволяет получать задания по устойчивому идентификатору, не зависящему от внутреннего ID

---

### POST /tasks/bulk-upsert

Массовый upsert заданий по `external_uid`. Если задание с таким `external_uid` существует, оно обновляется, иначе создается новое.

**Тело запроса:**
```json
{
  "items": [
    {
      "external_uid": "TASK-SC-001",
      "course_id": 1,
      "difficulty_id": 3,
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
      "max_score": 10
    },
    {
      "external_uid": "TASK-MC-002",
      "course_id": 1,
      "difficulty_id": 3,
      "task_content": {
        "type": "MC",
        "stem": "Какие из перечисленных способов создают пустой список в Python?",
        "options": [
          {"id": "A", "text": "list()", "is_active": true},
          {"id": "B", "text": "[]", "is_active": true},
          {"id": "C", "text": "[1, 2, 3]", "is_active": true}
        ]
      },
      "solution_rules": {
        "max_score": 15,
        "correct_options": ["A", "B"],
        "scoring_mode": "partial"
      },
      "max_score": 15
    }
  ]
}
```

**Ответ (200 OK):**
```json
{
  "results": [
    {
      "external_uid": "TASK-SC-001",
      "action": "created",
      "id": 1
    },
    {
      "external_uid": "TASK-MC-002",
      "action": "updated",
      "id": 2
    }
  ]
}
```

**Поля ответа:**
- `results` (array) - Список результатов обработки:
  - `external_uid` (string) - Внешний идентификатор задания
  - `action` (string) - Действие: `"created"` (создано) или `"updated"` (обновлено)
  - `id` (int) - ID созданного или обновленного задания

**Ошибки:**
- `400` - Ошибка валидации данных заданий
- `422` - Ошибка валидации запроса (неверный формат JSON)

**Примечания:**
- Позволяет существенно ускорить импорт заданий (одно HTTP-обращение вместо сотен)
- Все задания валидируются перед сохранением
- Если хотя бы одно задание невалидно, возвращается ошибка 400 с описанием проблемы

**Пример запроса:**
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
      }
    ]
  }'
```

---

### POST /tasks/validate

Предварительная валидация задания перед импортом. Не сохраняет задание в БД, только проверяет его корректность.

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

**Параметры запроса:**
- `task_content` (object, обязательный) - Содержимое задания (см. раздел "Типы заданий")
- `solution_rules` (object, опциональный) - Правила проверки и оценивания
- `course_code` (string, опциональный) - Код курса для проверки существования
- `course_id` (int, опциональный) - ID курса (альтернатива course_code)
- `difficulty_code` (string, опциональный) - Код уровня сложности
- `difficulty_id` (int, опциональный) - ID уровня сложности (альтернатива difficulty_code)
- `external_uid` (string, опциональный) - Внешний идентификатор задания

**Ответ (200 OK) - Валидное задание:**
```json
{
  "is_valid": true,
  "errors": []
}
```

**Ответ (200 OK) - Невалидное задание:**
```json
{
  "is_valid": false,
  "errors": [
    "course_code not provided",
    "Validation error: Для задач типа SC должен быть указан ровно один правильный вариант. Указано: 2"
  ]
}
```

**Поля ответа:**
- `is_valid` (bool) - Флаг валидности задания
- `errors` (array[string]) - Список ошибок валидации (пустой, если задание валидно)

**Проверяемые условия:**
- Структура `task_content` (наличие обязательных полей, соответствие типу)
- Структура `solution_rules` (наличие `max_score`, соответствие `correct_options` вариантам ответа)
- Существование курса (если указан `course_code` или `course_id`)
- Существование уровня сложности (если указан `difficulty_code` или `difficulty_id`)
- Для SC: ровно один правильный вариант
- Для MC: минимум один правильный вариант
- Соответствие ID вариантов в `correct_options` и `options[].id`

**Пример запроса:**
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

---

### POST /tasks/find-by-external

Массовое получение заданий по списку внешних идентификаторов.

**Тело запроса:**
```json
{
  "external_uids": ["TASK-SC-001", "TASK-MC-002", "TASK-NOT-FOUND"]
}
```

**Параметры запроса:**
- `external_uids` (array[string], обязательный) - Список внешних идентификаторов заданий

**Ответ (200 OK):**
```json
{
  "items": [
    {
      "external_uid": "TASK-SC-001",
      "id": 1
    },
    {
      "external_uid": "TASK-MC-002",
      "id": 2
    }
  ],
  "not_found": ["TASK-NOT-FOUND"]
}
```

**Поля ответа:**
- `items` (array) - Список найденных заданий:
  - `external_uid` (string) - Внешний идентификатор
  - `id` (int) - Внутренний ID задания
- `not_found` (array[string]) - Список `external_uid`, которые не были найдены

**Примечания:**
- Возвращает только существующие задания
- Если часть UID отсутствует, они попадают в `not_found`
- Полезно для проверки существования заданий перед импортом

---

### PATCH /tasks/{task_id}

Частичное обновление задания.

**Параметры:**
- `task_id` (path, int) - ID задания

**Тело запроса:**
```json
{
  "task_content": {
    "type": "SC",
    "stem": "Обновленная формулировка вопроса",
    "options": [...]
  }
}
```

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
- `400` - Ошибка валидации данных
- `404` - Задание не найдено

---

### DELETE /tasks/{task_id}

Удалить задание.

**Параметры:**
- `task_id` (path, int) - ID задания

**Ответ (204 No Content):**
Тело ответа отсутствует.

**Ошибки:**
- `404` - Задание не найдено

**Примечания:**
- При удалении задания также удаляются все связанные результаты (`task_results`) из-за каскадного удаления

---

### GET /tasks/search

Поиск заданий по содержимому.

**Параметры:**
- `q` (query, string, обязательный) - Поисковый запрос (минимум 2 символа)
- `course_id` (query, int, опциональный) - Фильтр по курсу
- `limit` (query, int, default: 20) - Максимум результатов (1-200)
- `offset` (query, int, default: 0) - Смещение для пагинации

**Ответ (200 OK):**
```json
[
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
]
```

**Поиск выполняется по:**
- `task_content.stem` (формулировка вопроса)
- `task_content.title` (название задания, если указано)
- `external_uid` (внешний идентификатор)

**Примечания:**
- Поиск регистронезависимый (case-insensitive)
- Использует оператор `ILIKE` для поиска подстроки
- Результаты сортируются по ID задания

**Пример запроса:**
```bash
curl "http://localhost:8000/api/v1/tasks/search?q=переменная&course_id=1&limit=20&api_key=bot-key-1"
```

---

### POST /tasks/import/google-sheets

Импорт заданий из Google Sheets таблицы. Поддерживает массовый импорт заданий различных типов.

**Тело запроса:**
```json
{
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit",
  "sheet_name": "Лист1",
  "course_code": "PY",
  "difficulty_code": "NORMAL",
  "dry_run": false,
  "column_mapping": {
    "external_uid": "ID",
    "type": "Тип",
    "stem": "Вопрос",
    "options": "Варианты",
    "correct_answer": "Правильный ответ",
    "course_uid": "Курс"
  }
}
```

**Параметры запроса:**
- `spreadsheet_url` (string, обязательный) - URL Google Sheets таблицы или spreadsheet_id
- `sheet_name` (string, опциональный) - Название листа (по умолчанию: "Лист1")
- `course_code` (string, опциональный) - Код курса (если курс один на весь импорт)
- `course_id` (int, опциональный) - ID курса (альтернатива `course_code`)
- `difficulty_code` (string, опциональный) - Код уровня сложности (обязателен, если не указан `difficulty_id`)
- `difficulty_id` (int, опциональный) - ID уровня сложности (альтернатива `difficulty_code`)
- `dry_run` (bool, опциональный) - Режим проверки без сохранения (по умолчанию: `false`)
- `column_mapping` (object, опциональный) - Маппинг колонок таблицы на поля задания

**Курс для каждой строки (подкурсы в одном файле):**
- Добавьте в таблицу колонку `course_uid` (courses.course_uid) и заполняйте её для каждой строки.
- Если `course_uid` заполнен в каждой строке, `course_code/course_id` в запросе можно не указывать.

**Ответ (200 OK):**
```json
{
  "imported": 10,
  "updated": 0,
  "errors": [],
  "total_rows": 10
}
```

**Ответ с ошибками:**
```json
{
  "imported": 8,
  "updated": 0,
  "errors": [
    {
      "row_index": 3,
      "external_uid": "TASK-SC-003",
      "error": "Validation error: Для задач типа SC должен быть указан ровно один правильный вариант. Указано: 2"
    },
    {
      "row_index": 5,
      "external_uid": null,
      "error": "Ошибка парсинга: external_uid не указан"
    }
  ],
  "total_rows": 10
}
```

**Поля ответа:**
- `imported` (int) - Количество созданных заданий
- `updated` (int) - Количество обновленных заданий
- `errors` (array) - Список ошибок:
  - `row_index` (int) - Номер строки в таблице (начиная с 1, без учета заголовка)
  - `external_uid` (string|null) - Внешний идентификатор задания (если удалось извлечь)
  - `error` (string) - Текст ошибки
- `total_rows` (int) - Общее количество обработанных строк (без учета заголовка)

**Требования к таблице:**
- Первая строка должна содержать заголовки колонок
- Обязательные колонки: `external_uid` (или `ID`), `type` (или `Тип`), `stem` (или `Вопрос`), `correct_answer` (или `Правильный ответ`)
- Для SC/MC обязательно указать `options` (или `Варианты`)
- Для SA/TA можно указать дополнительные параметры

**Стандартный маппинг колонок:**
- `external_uid` / `ID` / `код` → `external_uid`
- `type` / `Тип` / `task_type` → `type`
- `stem` / `question` / `Вопрос` / `задача` → `stem`
- `options` / `варианты` / `answers` → `options`
- `correct_answer` / `correct` / `правильный` / `ответ` → `correct_answer`
- `max_score` / `score` / `балл` / `баллы` → `max_score`

**Процесс импорта:**
1. Извлекает `spreadsheet_id` из URL
2. Читает данные из указанного листа через Google Sheets API
3. Парсит каждую строку данных в структуру задания
4. Валидирует данные (структура, ссылочная целостность)
5. Импортирует задания через `bulk_upsert` (создает новые или обновляет существующие по `external_uid`)
6. Возвращает детальный отчет с результатами

**Ошибки:**
- `400` - Неверные параметры запроса (не указан курс или уровень сложности)
- `403` - Неверный или отсутствующий API ключ
- `404` - Курс или уровень сложности не найден
- `500` - Ошибка при чтении Google Sheets или обработке данных

**Примечания:**
- Импорт продолжается даже при ошибках в отдельных строках
- Все ошибки возвращаются в массиве `errors` с указанием номера строки
- Частичный успех: некоторые задания могут быть импортированы, другие - нет
- Рекомендуется использовать `dry_run: true` для предварительной проверки данных
- Service Account должен иметь доступ к таблице

**Пример запроса:**
```bash
curl -X POST "http://localhost:8000/api/v1/tasks/import/google-sheets?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit",
    "sheet_name": "Лист1",
    "course_code": "PY",
    "difficulty_code": "NORMAL",
    "dry_run": false
  }'
```

**Подробная документация:** см. [Документация API импорта задач](./import-api-documentation.md)

---

## Типы заданий и структура данных

### Структура задания

Задание состоит из двух основных частей:

1. **task_content** - То, что видит ученик (формулировка, варианты ответа, подсказки)
2. **solution_rules** - Правила проверки и оценивания (правильные ответы, баллы, штрафы)

---

### SC (Single Choice) - Выбор одного варианта

Задание с выбором одного правильного варианта из нескольких.

**task_content:**
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

**solution_rules:**
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

**Требования:**
- Минимум 2 варианта ответа в `options`
- Ровно один правильный вариант в `correct_options`
- ID вариантов должны быть уникальными

---

### MC (Multiple Choice) - Выбор нескольких вариантов

Задание с выбором нескольких правильных вариантов.

**task_content:**
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

**solution_rules (с частичным оцениванием):**
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

**Требования:**
- Минимум 2 варианта ответа
- Минимум один правильный вариант в `correct_options`
- Можно использовать `partial_rules` для частичного оценивания

---

### SA (Short Answer) - Краткий ответ

Задание с кратким текстовым или числовым ответом.

**task_content:**
```json
{
  "type": "SA",
  "stem": "Сколько элементов в списке [1, 2, 3, 4, 5]?",
  "prompt": "Введите число",
  "tags": ["python", "lists", "len"]
}
```

**solution_rules:**
```json
{
  "max_score": 10,
  "scoring_mode": "all_or_nothing",
  "auto_check": true,
  "short_answer": {
    "normalization": ["trim", "lower"],
    "accepted_answers": [
      {
        "value": "5",
        "score": 10
      },
      {
        "value": "пять",
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

**Параметры short_answer:**
- `normalization` (array[string]) - Список шагов нормализации: `"trim"`, `"lower"`, `"collapse_spaces"`
- `accepted_answers` (array) - Список допустимых ответов с баллами
- `use_regex` (bool) - Использовать регулярные выражения для проверки
- `regex` (string, опциональный) - Регулярное выражение (если `use_regex = true`)

---

### TA (Text Answer) - Развернутый ответ

Задание с развернутым текстовым ответом, требующее ручной проверки.

**task_content:**
```json
{
  "type": "TA",
  "stem": "Объясните разницу между методами append() и extend() для списков в Python.",
  "prompt": "Приведите примеры использования каждого метода",
  "tags": ["python", "lists", "methods"]
}
```

**solution_rules:**
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

**Параметры text_answer:**
- `auto_check` (bool) - Возможность автопроверки (обычно `false`)
- `rubric` (array) - Набор критериев оценивания для ручной проверки:
  - `id` (string) - Устойчивый ID критерия
  - `title` (string) - Название критерия
  - `max_score` (int) - Максимальный балл по критерию

---

### Общие поля task_content

Все типы заданий поддерживают следующие поля:

- `type` (string, обязательный) - Тип задания: `SC`, `MC`, `SA`, `SA_COM`, `TA`
- `code` (string, опциональный) - Внутренний код задания
- `title` (string, опциональный) - Краткое название задания
- `stem` (string, обязательный) - Основная формулировка вопроса/задачи
- `prompt` (string, опциональный) - Дополнительное пояснение или подсказка
- `media` (object, опциональный) - Мультимедийные материалы:
  - `image_url` (string) - URL изображения
  - `audio_url` (string) - URL аудио
  - `video_url` (string) - URL видео
- `tags` (array[string], опциональный) - Список тегов
- `options` (array, опциональный) - Варианты ответа (обязательно для SC/MC)

---

### Общие поля solution_rules

Все типы заданий поддерживают следующие поля:

- `max_score` (int, обязательный) - Полный балл за задачу (должен совпадать с `tasks.max_score`)
- `scoring_mode` (string) - Режим оценивания:
  - `"all_or_nothing"` - Все или ничего (по умолчанию)
  - `"partial"` - Частичное оценивание
  - `"custom"` - Кастомные правила (требует `custom_scoring_config`)
- `auto_check` (bool) - Можно ли выполнить полную проверку автоматически (по умолчанию: `true`)
- `manual_review_required` (bool) - Требуется ли обязательная ручная дооценка (по умолчанию: `false`)
- `penalties` (object) - Правила штрафов:
  - `wrong_answer` (int) - Штраф за неверный ответ (по умолчанию: 0)
  - `missing_answer` (int) - Штраф за отсутствие ответа (по умолчанию: 0)
  - `extra_wrong_mc` (int) - Штраф за каждый лишний неверный вариант в MC (по умолчанию: 0)

---

## Эндпойнты попыток (Attempts)

Попытка (Attempt) представляет собой сессию выполнения набора заданий учеником. Попытка может быть привязана к курсу и содержать метаданные (например, таймлимит).

### POST /attempts

Создать новую попытку прохождения теста/набора задач.

**Тело запроса:**
```json
{
  "user_id": 10,
  "course_id": 1,
  "source_system": "web",
  "meta": {
    "time_limit": 3600,
    "task_ids": [1, 2, 3],
    "title": "Контрольная работа по Python"
  }
}
```

**Параметры запроса:**
- `user_id` (int, обязательный) - ID пользователя, который проходит попытку
- `course_id` (int, опциональный) - ID курса, если попытка привязана к конкретному курсу
- `source_system` (string, опциональный) - Источник создания попытки (по умолчанию: "lms"). Возможные значения: "lms", "web", "tg_bot", "import" и т.п.
- `meta` (object, опциональный) - Произвольные метаданные:
  - `time_limit` (int) - Лимит времени на выполнение в секундах
  - `task_ids` (array[int]) - Список ID задач, включенных в попытку
  - `title` (string) - Название попытки/контрольной работы
  - Любые другие поля

**Ответ (201 Created):**
```json
{
  "id": 1,
  "user_id": 10,
  "course_id": 1,
  "source_system": "web",
  "created_at": "2026-02-16T12:00:00Z",
  "finished_at": null,
  "meta": {
    "time_limit": 3600,
    "task_ids": [1, 2, 3],
    "title": "Контрольная работа по Python"
  }
}
```

**Ошибки:**
- `400` - Ошибка валидации данных запроса
- `403` - Неверный или отсутствующий API ключ
- `404` - Пользователь или курс не найден

**Пример запроса:**
```bash
curl -X POST "http://localhost:8000/api/v1/attempts?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 10,
    "course_id": 1,
    "source_system": "web",
    "meta": {
      "time_limit": 3600
    }
  }'
```

---

### GET /attempts/{attempt_id}

Получить попытку с результатами по задачам.

**Параметры:**
- `attempt_id` (path, int) - ID попытки

**Ответ (200 OK):**
```json
{
  "attempt": {
    "id": 1,
    "user_id": 10,
    "course_id": 1,
    "source_system": "web",
    "created_at": "2026-02-16T12:00:00Z",
    "finished_at": null,
    "meta": {
      "time_limit": 3600,
      "task_ids": [1, 2, 3]
    }
  },
  "results": [
    {
      "task_id": 1,
      "score": 10,
      "max_score": 10,
      "is_correct": true,
      "answer_json": {
        "type": "SC",
        "response": {
          "selected_option_ids": ["A"]
        }
      }
    },
    {
      "task_id": 2,
      "score": 5,
      "max_score": 10,
      "is_correct": false,
      "answer_json": {
        "type": "MC",
        "response": {
          "selected_option_ids": ["A", "B"]
        }
      }
    }
  ],
  "total_score": 15,
  "total_max_score": 20
}
```

**Поля ответа:**
- `attempt` - Метаданные попытки
- `results` - Список результатов по задачам в рамках попытки:
  - `task_id` (int) - ID задачи
  - `score` (int) - Набранный балл
  - `max_score` (int) - Максимальный балл
  - `is_correct` (bool|null) - Флаг правильности ответа (null для задач с ручной проверкой)
  - `answer_json` (object|null) - Сохранённый ответ ученика по задаче
- `total_score` (int) - Суммарный набранный балл по всем задачам попытки
- `total_max_score` (int) - Суммарный максимальный балл по всем задачам попытки

**Инвариант Learning API (start-or-get-attempt):** для попытки, полученной через `POST /learning/tasks/{task_id}/start-or-get-attempt`, backend гарантирует: `attempt.meta` — объект (не null), `attempt.meta.task_ids` — массив int[], содержащий как минимум этот `task_id`. При повторном вызове по тому же заданию дубликаты в `task_ids` не добавляются. Пустой или битый `meta`/`task_ids` восстанавливается при вызове start-or-get-attempt.

**Ошибки:**
- `404` - Попытка не найдена

---

### GET /attempts/by-user/{user_id}

Получить список попыток пользователя.

**Параметры:**
- `user_id` (path, int) - ID пользователя
- `course_id` (query, int, опциональный) - Фильтр по курсу
- `limit` (query, int, default: 100) - Максимум записей на странице (1-1000)
- `offset` (query, int, default: 0) - Смещение для пагинации

**Ответ (200 OK):**
```json
[
  {
    "id": 1,
    "user_id": 10,
    "course_id": 1,
    "source_system": "web",
    "created_at": "2026-02-16T12:00:00Z",
    "finished_at": "2026-02-16T13:00:00Z",
    "meta": {
      "time_limit": 3600
    }
  },
  {
    "id": 2,
    "user_id": 10,
    "course_id": 1,
    "source_system": "web",
    "created_at": "2026-02-16T14:00:00Z",
    "finished_at": null,
    "meta": {}
  }
]
```

**Примечания:**
- Результаты сортируются по дате создания (от новых к старым)
- Если `finished_at` равен `null`, попытка еще не завершена
- Опциональный параметр `course_id` позволяет фильтровать попытки по курсу

**Ошибки:**
- `404` - Пользователь не найден

**Пример запроса:**
```bash
curl "http://localhost:8000/api/v1/attempts/by-user/10?api_key=bot-key-1&course_id=1&limit=20&offset=0"
```

---

### POST /attempts/{attempt_id}/answers

Отправить ответы по задачам внутри попытки. Ответы автоматически проверяются и сохраняются в `task_results`.

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
    },
    {
      "external_uid": "TASK-MC-001",
      "answer": {
        "type": "MC",
        "response": {
          "selected_option_ids": ["A", "B"]
        }
      }
    },
    {
      "task_id": 3,
      "answer": {
        "type": "SA",
        "response": {
          "value": "42"
        }
      }
    }
  ]
}
```

**Параметры запроса:**
- `items` (array, обязательный) - Список ответов по задачам:
  - `task_id` (int, опциональный) - ID задачи в БД. Обязателен, если не указан `external_uid`
  - `external_uid` (string, опциональный) - Внешний устойчивый ID задачи. Обязателен, если не указан `task_id`
  - `answer` (object, обязательный) - Ответ ученика на задачу:
    - `type` (string, обязательный) - Тип задачи: `SC`, `MC`, `SA`, `SA_COM`, `TA`. Должен совпадать с типом задачи
    - `response` (object, обязательный) - Структура ответа в зависимости от типа:
      - Для `SC`/`MC`: `{"selected_option_ids": ["A", "B"]}`
      - Для `SA`/`SA_COM`: `{"value": "текст ответа"}`
      - Для `TA`: `{"text": "развернутый ответ"}`

**Ответ (200 OK):**
```json
{
  "attempt_id": 1,
  "results": [
    {
      "task_id": 1,
      "check_result": {
        "is_correct": true,
        "score": 10,
        "max_score": 10,
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
    },
    {
      "task_id": 2,
      "check_result": {
        "is_correct": false,
        "score": 5,
        "max_score": 10,
        "details": {
          "correct_options": ["A", "B"],
          "user_options": ["A", "B"],
          "matched_short_answer": null,
          "rubric_scores": null
        },
        "feedback": {
          "general": "Частично правильно",
          "by_option": {}
        }
      }
    }
  ],
  "total_score_delta": 15,
  "total_max_score_delta": 20
}
```

**Поля ответа:**
- `attempt_id` (int) - ID попытки
- `results` (array) - Результаты проверки по каждой задаче:
  - `task_id` (int) - ID задачи
  - `check_result` (object) - Результат проверки:
    - `is_correct` (bool|null) - Флаг правильности ответа
    - `score` (int) - Набранный балл
    - `max_score` (int) - Максимальный балл
    - `details` (object) - Расширенная информация о проверке
    - `feedback` (object) - Текстовая обратная связь для ученика
- `total_score_delta` (int) - Суммарный набранный балл только по этим присланным ответам
- `total_max_score_delta` (int) - Суммарный максимальный балл только по этим присланным ответам

**Ошибки:**
- `400` - Попытка уже завершена, истекло время, список ответов пуст, задача не найдена, тип ответа не совпадает с типом задачи
- `404` - Попытка не найдена
- `422` - Ошибка валидации запроса (неверный формат JSON)

**Примечания:**
- Ответы автоматически проверяются через `CheckingService`
- Результаты сохраняются в таблицу `task_results`
- Если попытка уже завершена (`finished_at` не null), отправка ответов невозможна
- Если в `meta` попытки указан `time_limit`, проверяется, не истекло ли время
- Можно отправлять ответы по частям (не все сразу)

**Пример запроса:**
```bash
curl -X POST "http://localhost:8000/api/v1/attempts/1/answers?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
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
  }'
```

---

### POST /attempts/{attempt_id}/finish

Завершить попытку и вернуть агрегированные результаты.

**Параметры:**
- `attempt_id` (path, int) - ID попытки

**Ответ (200 OK):**
```json
{
  "attempt": {
    "id": 1,
    "user_id": 10,
    "course_id": 1,
    "source_system": "web",
    "created_at": "2026-02-16T12:00:00Z",
    "finished_at": "2026-02-16T13:00:00Z",
    "meta": {
      "time_limit": 3600
    }
  },
  "results": [
    {
      "task_id": 1,
      "score": 10,
      "max_score": 10,
      "is_correct": true,
      "answer_json": {
        "type": "SC",
        "response": {
          "selected_option_ids": ["A"]
        }
      }
    }
  ],
  "total_score": 10,
  "total_max_score": 10
}
```

**Поля ответа:**
- Аналогичны `GET /attempts/{attempt_id}`, но `finished_at` теперь установлен

**Ошибки:**
- `404` - Попытка не найдена

**Примечания:**
- Устанавливает `finished_at` в текущее время
- После завершения попытки нельзя отправлять новые ответы
- Возвращает полную картину попытки со всеми результатами

---

### POST /attempts/{attempt_id}/cancel (Learning Engine V1, этап 3.5)

Аннулировать активную попытку (без завершения по ответам). Идемпотентно.

**Параметры:**
- `attempt_id` (path, int) - ID попытки

**Тело запроса (опционально):**
```json
{
  "reason": "user_exit_to_main_menu"
}
```
Можно отправить пустое тело или не передавать body.

**Ответ (200 OK):**
```json
{
  "attempt_id": 1,
  "status": "cancelled",
  "cancelled_at": "2026-02-26T12:00:00Z",
  "already_cancelled": false
}
```
- `already_cancelled: true` — попытка уже была отменена (повторный вызов).

**Ошибки:**
- `404` - Попытка не найдена
- `409` - Попытка уже завершена; отменять можно только активную попытку

**Влияние на task_results и статистику:**
- Отменённая попытка не считается активной и не возвращается в `POST /learning/tasks/{task_id}/start-or-get-attempt`.
- В агрегатах по пользователю/курсу/задаче и в «последней завершённой попытке» учитываются только попытки с `finished_at` и без `cancelled_at`.

---

## Эндпойнты результатов заданий (Task Results)

Результат задания (Task Result) представляет собой результат выполнения конкретной задачи учеником в рамках попытки.

### GET /task-results/by-user/{user_id}

Получить список результатов выполнения заданий пользователя.

**Параметры:**
- `user_id` (path, int) - ID пользователя
- `limit` (query, int, default: 100) - Максимум записей на странице (1-1000)
- `offset` (query, int, default: 0) - Смещение для пагинации

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
    "submitted_at": "2026-02-16T12:00:00Z",
    "received_at": "2026-02-16T12:00:00Z",
    "checked_at": "2026-02-16T12:00:05Z",
    "checked_by": null,
    "count_retry": 0,
    "metrics": {},
    "answer_json": {
      "type": "SC",
      "response": {
        "selected_option_ids": ["A"]
      }
    },
    "source_system": "web"
  },
  {
    "id": 2,
    "attempt_id": 1,
    "task_id": 2,
    "user_id": 10,
    "score": 5,
    "max_score": 10,
    "is_correct": false,
    "submitted_at": "2026-02-16T12:05:00Z",
    "received_at": "2026-02-16T12:00:00Z",
    "checked_at": "2026-02-16T12:05:05Z",
    "checked_by": null,
    "count_retry": 1,
    "metrics": {},
    "answer_json": {
      "type": "MC",
      "response": {
        "selected_option_ids": ["A", "B"]
      }
    },
    "source_system": "web"
  }
]
```

**Поля ответа:**
- `id` (int) - ID результата
- `attempt_id` (int|null) - ID попытки (если результат привязан к попытке)
- `task_id` (int) - ID задачи
- `user_id` (int) - ID пользователя
- `score` (int) - Набранный балл
- `max_score` (int|null) - Максимальный балл за задачу на момент проверки
- `is_correct` (bool|null) - Флаг правильности ответа (null для задач с ручной проверкой)
- `submitted_at` (datetime) - Время сдачи ответа
- `received_at` (datetime) - Время начала выполнения (когда начали)
- `checked_at` (datetime|null) - Время проверки (null для непроверенных)
- `checked_by` (int|null) - ID пользователя, выполнившего проверку (null для автоматической проверки)
- `count_retry` (int) - Количество попыток
- `metrics` (object) - Метрики качества ответа (произвольный JSON)
- `answer_json` (object|null) - Исходный ответ ученика
- `source_system` (string) - Источник системы, записавшей результат

**Ошибки:**
- `404` - Пользователь не найден

**Пример запроса:**
```bash
curl "http://localhost:8000/api/v1/task-results/by-user/10?api_key=bot-key-1&limit=50&offset=0"
```

---

### GET /task-results/by-task/{task_id}

Получить список результатов выполнения конкретной задачи.

**Параметры:**
- `task_id` (path, int) - ID задачи
- `limit` (query, int, default: 100) - Максимум записей на странице (1-1000)
- `offset` (query, int, default: 0) - Смещение для пагинации

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
    "submitted_at": "2026-02-16T12:00:00Z",
    "received_at": "2026-02-16T12:00:00Z",
    "checked_at": "2026-02-16T12:00:05Z",
    "checked_by": null,
    "count_retry": 0,
    "metrics": {},
    "answer_json": {
      "type": "SC",
      "response": {
        "selected_option_ids": ["A"]
      }
    },
    "source_system": "web"
  }
]
```

**Ошибки:**
- `404` - Задача не найдена

**Примечания:**
- Полезно для анализа результатов конкретной задачи
- Можно использовать для статистики по задаче

---

### GET /task-results/by-attempt/{attempt_id}

Получить список результатов выполнения заданий в рамках конкретной попытки.

**Параметры:**
- `attempt_id` (path, int) - ID попытки
- `limit` (query, int, default: 100) - Максимум записей на странице (1-1000)
- `offset` (query, int, default: 0) - Смещение для пагинации

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
    "submitted_at": "2026-02-16T12:00:00Z",
    "received_at": "2026-02-16T12:00:00Z",
    "checked_at": "2026-02-16T12:00:05Z",
    "checked_by": null,
    "count_retry": 0,
    "metrics": {},
    "answer_json": {
      "type": "SC",
      "response": {
        "selected_option_ids": ["A"]
      }
    },
    "source_system": "web"
  }
]
```

**Ошибки:**
- `404` - Попытка не найдена

**Примечания:**
- Возвращает все результаты по задачам в рамках одной попытки
- Полезно для отображения результатов контрольной работы или теста

---

### POST /task-results/{result_id}/manual-check

Выполнить ручную дооценку результата выполнения задачи.

Позволяет преподавателю или администратору изменить оценку, установленную автоматической проверкой, или проверить задачу, требующую ручной проверки.

**Параметры:**
- `result_id` (path, int) - ID результата для проверки

**Тело запроса:**
```json
{
  "score": 8,
  "checked_by": 2,
  "lock_token": "claim-token-from-claim-next",
  "is_correct": false,
  "metrics": {
    "comment": "Частично верно. Не учтена важная деталь.",
    "rubric_scores": {
      "content": 5,
      "style": 2,
      "grammar": 1
    }
  }
}
```

**Параметры запроса:**
- `score` (int, обязательный) - Новый балл (должен быть ≥ 0 и ≤ max_score)
- `checked_by` (int, обязательный) - ID пользователя, выполняющего проверку
- `is_correct` (bool, опциональный) - Флаг правильности ответа
- `metrics` (object, опциональный) - Дополнительные метрики (комментарии, рубрики и т.п.)

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
  "submitted_at": "2026-02-16T12:00:00Z",
  "received_at": "2026-02-16T12:00:00Z",
  "checked_at": "2026-02-16T13:00:00Z",
  "checked_by": 2,
  "count_retry": 0,
  "metrics": {
    "comment": "Частично верно. Не учтена важная деталь.",
    "rubric_scores": {
      "content": 5,
      "style": 2,
      "grammar": 1
    }
  },
  "answer_json": {
    "type": "TA",
    "response": {
      "text": "Развернутый ответ ученика..."
    }
  },
  "source_system": "web"
}
```

**Ошибки:**
- `400` - Неверные параметры запроса:
  - `score` не указан
  - `checked_by` не указан
  - `score` превышает `max_score`
  - `score` отрицательный
- `404` - Результат не найден
- `409` - Токен блокировки невалиден или просрочен (если передан `lock_token`)

**Примечания:**
- Автоматически устанавливает `checked_at` в текущее время
- Поле `checked_by` позволяет отслеживать, кто выполнил проверку
- Поле `metrics` может содержать произвольные данные (комментарии, рубрики и т.п.)

**Пример запроса:**
```bash
curl -X POST "http://localhost:8000/api/v1/task-results/1/manual-check?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "score": 8,
    "checked_by": 2,
    "is_correct": false,
    "metrics": {
      "comment": "Частично верно"
    }
  }'
```

---

## CRUD операции с результатами

Помимо специализированных эндпойнтов, доступны стандартные CRUD операции для работы с результатами заданий.

### GET /task-results/

Получить список всех результатов с пагинацией.

**Параметры:**
- `skip` (query, int, default: 0) - Смещение для пагинации
- `limit` (query, int, default: 100) - Количество записей на странице

**Ответ (200 OK):**
```json
{
  "items": [
    {
      "id": 1,
      "attempt_id": 1,
      "task_id": 1,
      "user_id": 10,
      "score": 10,
      "max_score": 10,
      "is_correct": true,
      "submitted_at": "2026-02-16T12:00:00Z",
      "received_at": "2026-02-16T12:00:00Z",
      "checked_at": "2026-02-16T12:00:05Z",
      "checked_by": null,
      "count_retry": 0,
      "metrics": {},
      "answer_json": null,
      "source_system": "web"
    }
  ],
  "meta": {
    "total": 150,
    "limit": 100,
    "offset": 0
  }
}
```

---

### GET /task-results/{result_id}

Получить результат по ID.

**Параметры:**
- `result_id` (path, int) - ID результата

**Ответ (200 OK):**
```json
{
  "id": 1,
  "attempt_id": 1,
  "task_id": 1,
  "user_id": 10,
  "score": 10,
  "max_score": 10,
  "is_correct": true,
  "submitted_at": "2026-02-16T12:00:00Z",
  "received_at": "2026-02-16T12:00:00Z",
  "checked_at": "2026-02-16T12:00:05Z",
  "checked_by": null,
  "count_retry": 0,
  "metrics": {},
  "answer_json": {
    "type": "SC",
    "response": {
      "selected_option_ids": ["A"]
    }
  },
  "source_system": "web"
}
```

**Ошибки:**
- `404` - Результат не найден

---

### POST /task-results/

Создать новый результат вручную (обычно результаты создаются автоматически при отправке ответов).

**Тело запроса:**
```json
{
  "score": 10,
  "user_id": 10,
  "task_id": 1,
  "attempt_id": 1,
  "max_score": 10,
  "is_correct": true,
  "answer_json": {
    "type": "SC",
    "response": {
      "selected_option_ids": ["A"]
    }
  },
  "source_system": "web",
  "metrics": {},
  "count_retry": 0
}
```

**Ответ (201 Created):**
```json
{
  "id": 1,
  "attempt_id": 1,
  "task_id": 1,
  "user_id": 10,
  "score": 10,
  "max_score": 10,
  "is_correct": true,
  "submitted_at": "2026-02-16T12:00:00Z",
  "received_at": "2026-02-16T12:00:00Z",
  "checked_at": null,
  "checked_by": null,
  "count_retry": 0,
  "metrics": {},
  "answer_json": {
    "type": "SC",
    "response": {
      "selected_option_ids": ["A"]
    }
  },
  "source_system": "web"
}
```

---

### PUT /task-results/{result_id}

Полное обновление результата.

**Параметры:**
- `result_id` (path, int) - ID результата

**Тело запроса:**
```json
{
  "score": 8,
  "metrics": {
    "comment": "Обновленный комментарий"
  },
  "is_correct": false,
  "checked_by": 2,
  "checked_at": "2026-02-16T13:00:00Z"
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
  "submitted_at": "2026-02-16T12:00:00Z",
  "received_at": "2026-02-16T12:00:00Z",
  "checked_at": "2026-02-16T13:00:00Z",
  "checked_by": 2,
  "count_retry": 0,
  "metrics": {
    "comment": "Обновленный комментарий"
  },
  "answer_json": null,
  "source_system": "web"
}
```

---

### PATCH /task-results/{result_id}

Частичное обновление результата.

**Параметры:**
- `result_id` (path, int) - ID результата

**Тело запроса:**
```json
{
  "score": 9
}
```

**Ответ (200 OK):**
```json
{
  "id": 1,
  "attempt_id": 1,
  "task_id": 1,
  "user_id": 10,
  "score": 9,
  "max_score": 10,
  "is_correct": true,
  "submitted_at": "2026-02-16T12:00:00Z",
  "received_at": "2026-02-16T12:00:00Z",
  "checked_at": "2026-02-16T12:00:05Z",
  "checked_by": null,
  "count_retry": 0,
  "metrics": {},
  "answer_json": null,
  "source_system": "web"
}
```

---

### DELETE /task-results/{result_id}

Удалить результат.

**Параметры:**
- `result_id` (path, int) - ID результата

**Ответ (204 No Content):**
Тело ответа отсутствует.

**Ошибки:**
- `404` - Результат не найден

---

## Эндпойнты статистики

### GET /task-results/stats/by-task/{task_id}

Получить статистику по задаче.

**Параметры:**
- `task_id` (path, int) - ID задачи

**Ответ (200 OK):**
```json
{
  "task_id": 1,
  "total_attempts": 50,
  "average_score": 7.5,
  "correct_percentage": 60.0,
  "min_score": 0,
  "max_score": 10,
  "score_distribution": {
    "0": 5,
    "5": 10,
    "10": 35
  },
  "hints_used_count": 12,
  "used_text_hints_count": 8,
  "used_video_hints_count": 4
}
```

**Поля ответа:**
- `task_id` (int) - ID задачи
- `total_attempts` (int) - Общее количество попыток выполнения задачи
- `hints_used_count`, `used_text_hints_count`, `used_video_hints_count` (int) — этап 3.6: число событий открытия подсказок
- `average_score` (float) - Средний балл
- `correct_percentage` (float) - Процент правильных ответов
- `min_score` (int) - Минимальный балл
- `max_score` (int) - Максимальный балл
- `score_distribution` (object) - Распределение баллов (ключ - балл, значение - количество)

**Ошибки:**
- `404` - Задача не найдена

**Пример запроса:**
```bash
curl "http://localhost:8000/api/v1/task-results/stats/by-task/1?api_key=bot-key-1"
```

---

### GET /task-results/stats/by-course/{course_id}

Получить статистику по курсу.

**Параметры:**
- `course_id` (path, int) - ID курса

**Ответ (200 OK):**
```json
{
  "course_id": 1,
  "total_attempts": 200,
  "average_score": 8.2,
  "correct_percentage": 65.0,
  "tasks_count": 10
}
```

**Поля ответа:**
- `course_id` (int) - ID курса
- `total_attempts` (int) - Общее количество попыток по всем задачам курса
- `average_score` (float) - Средний балл по всем задачам
- `correct_percentage` (float) - Процент правильных ответов
- `tasks_count` (int) - Количество задач в курсе

**Ошибки:**
- `404` - Курс не найден

---

### GET /task-results/stats/by-user/{user_id}

Получить статистику по пользователю.

**Параметры:**
- `user_id` (path, int) - ID пользователя

**Ответ (200 OK):**
```json
{
  "user_id": 10,
  "total_attempts": 30,
  "average_score": 7.8,
  "correct_percentage": 70.0,
  "total_score": 234,
  "total_max_score": 300,
  "completion_percentage": 78.0,
  "hints_used_count": 5,
  "used_text_hints_count": 3,
  "used_video_hints_count": 2
}
```

**Поля ответа:**
- `user_id` (int) - ID пользователя
- `total_attempts` (int) - Общее количество попыток пользователя
- `average_score` (float) - Средний балл
- `correct_percentage` (float) - Процент правильных ответов
- `total_score` (int) - Сумма всех баллов пользователя
- `total_max_score` (int) - Сумма максимальных баллов по всем задачам
- `completion_percentage` (float) - Процент выполнения (total_score / total_max_score * 100)
- `hints_used_count`, `used_text_hints_count`, `used_video_hints_count` (int) — этап 3.6: число открытий подсказок пользователем

**Ошибки:**
- `404` - Пользователь не найден

**Learning API, этап 3.6 — фиксация открытия подсказки:** `POST /learning/tasks/{task_id}/hint-events` (body: `student_id`, `attempt_id`, `hint_type`, `hint_index`, `action`, `source`). Идемпотентно в окне дедупа. События учитываются в полях `hints_used_count`, `used_text_hints_count`, `used_video_hints_count` в stats by-task, by-user, by-course. См. [api-reference.md](api-reference.md), [tz-learning-engine-stage3-6-hint-events.md](tz-learning-engine-stage3-6-hint-events.md).

---

### GET /task-results/by-pending-review

Получить результаты заданий, требующих ручной проверки.

**Параметры:**
- `course_id` (query, int, опциональный) - Фильтр по курсу
- `user_id` (query, int, опциональный) - Фильтр по пользователю
- `limit` (query, int, default: 50) - Максимум записей на странице (1-1000)
- `offset` (query, int, default: 0) - Смещение для пагинации

**Ответ (200 OK):**
```json
[
  {
    "id": 1,
    "attempt_id": 1,
    "task_id": 1,
    "user_id": 10,
    "score": 0,
    "max_score": 20,
    "is_correct": null,
    "submitted_at": "2026-02-16T12:00:00Z",
    "received_at": "2026-02-16T12:00:00Z",
    "checked_at": null,
    "checked_by": null,
    "count_retry": 0,
    "metrics": {},
    "answer_json": {
      "type": "TA",
      "response": {
        "text": "Развернутый ответ ученика..."
      }
    },
    "source_system": "web"
  }
]
```

**Поля ответа:**
- Аналогичны `GET /task-results/by-user/{user_id}`, но фильтруются только результаты, требующие проверки

**Критерии отбора:**
- `checked_at = null` (результат еще не проверен)
- ИЛИ задание имеет `solution_rules.manual_review_required = true`

**Примечания:**
- Результаты сортируются по дате отправки (от новых к старым)
- Полезно для преподавателей и методистов для получения списка заданий на проверку
- Можно фильтровать по курсу или пользователю для более узкого поиска

**Пример запроса:**
```bash
curl "http://localhost:8000/api/v1/task-results/pending-review?course_id=1&limit=50&api_key=bot-key-1"
```

---

## Схемы данных

### AttemptCreate

Схема создания попытки.

```json
{
  "user_id": 10,
  "course_id": 1,
  "source_system": "web",
  "meta": {
    "time_limit": 3600,
    "task_ids": [1, 2, 3]
  }
}
```

**Поля:**
- `user_id` (int, обязательный) - ID пользователя
- `course_id` (int, опциональный) - ID курса
- `source_system` (string, опциональный, default: "lms") - Источник создания попытки
- `meta` (object, опциональный) - Произвольные метаданные

---

### AttemptRead

Схема чтения попытки.

```json
{
  "id": 1,
  "user_id": 10,
  "course_id": 1,
  "created_at": "2026-02-16T12:00:00Z",
  "finished_at": null,
  "source_system": "web",
  "meta": {}
}
```

**Поля:**
- `id` (int) - ID попытки
- `user_id` (int) - ID пользователя
- `course_id` (int|null) - ID курса
- `created_at` (datetime|null) - Время создания
- `finished_at` (datetime|null) - Время завершения (null, если не завершена)
- `source_system` (string|null) - Источник создания попытки
- `meta` (object|null) - Метаданные

---

### AttemptAnswersRequest

Схема запроса на отправку ответов.

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

**Поля:**
- `items` (array, обязательный) - Список ответов:
  - `task_id` (int|null) - ID задачи (обязателен, если не указан `external_uid`)
  - `external_uid` (string|null) - Внешний ID задачи (обязателен, если не указан `task_id`)
  - `answer` (object, обязательный) - Ответ ученика:
    - `type` (string) - Тип задачи: `SC`, `MC`, `SA`, `SA_COM`, `TA`
    - `response` (object) - Структура ответа в зависимости от типа

---

### TaskResultRead

Схема чтения результата задания.

```json
{
  "id": 1,
  "attempt_id": 1,
  "task_id": 1,
  "user_id": 10,
  "score": 10,
  "max_score": 10,
  "is_correct": true,
  "submitted_at": "2026-02-16T12:00:00Z",
  "received_at": "2026-02-16T12:00:00Z",
  "checked_at": "2026-02-16T12:00:05Z",
  "checked_by": null,
  "count_retry": 0,
  "metrics": {},
  "answer_json": {
    "type": "SC",
    "response": {
      "selected_option_ids": ["A"]
    }
  },
  "source_system": "web"
}
```

**Поля:**
- `id` (int) - ID результата
- `attempt_id` (int|null) - ID попытки
- `task_id` (int) - ID задачи
- `user_id` (int) - ID пользователя
- `score` (int) - Набранный балл
- `max_score` (int|null) - Максимальный балл
- `is_correct` (bool|null) - Флаг правильности ответа
- `submitted_at` (datetime) - Время сдачи
- `received_at` (datetime) - Время начала выполнения
- `checked_at` (datetime|null) - Время проверки
- `checked_by` (int|null) - ID проверяющего
- `count_retry` (int) - Количество попыток
- `metrics` (object|null) - Метрики качества ответа
- `answer_json` (object|null) - Исходный ответ ученика
- `source_system` (string) - Источник системы

---

### StudentAnswer

Схема ответа ученика.

**Для SC (Single Choice):**
```json
{
  "type": "SC",
  "response": {
    "selected_option_ids": ["A"]
  }
}
```

**Для MC (Multiple Choice):**
```json
{
  "type": "MC",
  "response": {
    "selected_option_ids": ["A", "B"]
  }
}
```

**Для SA (Short Answer):**
```json
{
  "type": "SA",
  "response": {
    "value": "42"
  }
}
```

**Для TA (Text Answer):**
```json
{
  "type": "TA",
  "response": {
    "text": "Развернутый ответ ученика..."
  }
}
```

---

## Примеры использования

### Работа с заданиями

#### 1. Создание задания SC

```bash
curl -X POST "http://localhost:8000/api/v1/tasks?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "external_uid": "TASK-SC-001",
    "course_id": 1,
    "difficulty_id": 3,
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
      "correct_options": ["A"]
    },
    "max_score": 10
  }'
```

#### 2. Пакетная загрузка заданий

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
        "external_uid": "TASK-MC-002",
        "course_id": 1,
        "difficulty_id": 3,
        "task_content": {
          "type": "MC",
          "stem": "Какие способы создают пустой список?",
          "options": [
            {"id": "A", "text": "list()", "is_active": true},
            {"id": "B", "text": "[]", "is_active": true}
          ]
        },
        "solution_rules": {
          "max_score": 15,
          "correct_options": ["A", "B"]
        },
        "max_score": 15
      }
    ]
  }'
```

**Ответ:**
```json
{
  "results": [
    {"external_uid": "TASK-SC-001", "action": "created", "id": 1},
    {"external_uid": "TASK-MC-002", "action": "created", "id": 2}
  ]
}
```

#### 3. Валидация задания перед импортом

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

**Ответ:**
```json
{
  "is_valid": true,
  "errors": []
}
```

#### 4. Получение заданий курса

```bash
curl "http://localhost:8000/api/v1/tasks/by-course/1?api_key=bot-key-1&difficulty_id=3&limit=20"
```

#### 5. Импорт из Google Sheets

```bash
curl -X POST "http://localhost:8000/api/v1/tasks/import/google-sheets?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit",
    "sheet_name": "Лист1",
    "course_code": "PY",
    "difficulty_code": "NORMAL",
    "dry_run": false
  }'
```

**Ответ:**
```json
{
  "imported": 10,
  "updated": 0,
  "errors": [],
  "total_rows": 10
}
```

---

### Полный цикл работы с попыткой

#### 1. Создание попытки

```bash
curl -X POST "http://localhost:8000/api/v1/attempts?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 10,
    "course_id": 1,
    "source_system": "web",
    "meta": {
      "time_limit": 3600,
      "title": "Контрольная работа по Python"
    }
  }'
```

**Ответ:**
```json
{
  "id": 1,
  "user_id": 10,
  "course_id": 1,
  "source_system": "web",
  "created_at": "2026-02-16T12:00:00Z",
  "finished_at": null,
  "meta": {
    "time_limit": 3600,
    "title": "Контрольная работа по Python"
  }
}
```

#### 2. Отправка ответов

```bash
curl -X POST "http://localhost:8000/api/v1/attempts/1/answers?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "task_id": 1,
        "answer": {
          "type": "SC",
          "response": {
            "selected_option_ids": ["A"]
          }
        }
      },
      {
        "task_id": 2,
        "answer": {
          "type": "MC",
          "response": {
            "selected_option_ids": ["A", "B"]
          }
        }
      }
    ]
  }'
```

**Ответ:**
```json
{
  "attempt_id": 1,
  "results": [
    {
      "task_id": 1,
      "check_result": {
        "is_correct": true,
        "score": 10,
        "max_score": 10,
        "details": {
          "correct_options": ["A"],
          "user_options": ["A"]
        },
        "feedback": {
          "general": "Правильно!"
        }
      }
    },
    {
      "task_id": 2,
      "check_result": {
        "is_correct": false,
        "score": 5,
        "max_score": 10,
        "details": {
          "correct_options": ["A", "B"],
          "user_options": ["A", "B"]
        },
        "feedback": {
          "general": "Частично правильно"
        }
      }
    }
  ],
  "total_score_delta": 15,
  "total_max_score_delta": 20
}
```

#### 3. Завершение попытки

```bash
curl -X POST "http://localhost:8000/api/v1/attempts/1/finish?api_key=bot-key-1"
```

**Ответ:**
```json
{
  "attempt": {
    "id": 1,
    "user_id": 10,
    "course_id": 1,
    "source_system": "web",
    "created_at": "2026-02-16T12:00:00Z",
    "finished_at": "2026-02-16T13:00:00Z",
    "meta": {
      "time_limit": 3600,
      "title": "Контрольная работа по Python"
    }
  },
  "results": [
    {
      "task_id": 1,
      "score": 10,
      "max_score": 10,
      "is_correct": true,
      "answer_json": {
        "type": "SC",
        "response": {
          "selected_option_ids": ["A"]
        }
      }
    },
    {
      "task_id": 2,
      "score": 5,
      "max_score": 10,
      "is_correct": false,
      "answer_json": {
        "type": "MC",
        "response": {
          "selected_option_ids": ["A", "B"]
        }
      }
    }
  ],
  "total_score": 15,
  "total_max_score": 20
}
```

#### 4. Получение результатов пользователя

```bash
curl "http://localhost:8000/api/v1/task-results/by-user/10?api_key=bot-key-1&limit=20"
```

#### 5. Ручная проверка результата

```bash
curl -X POST "http://localhost:8000/api/v1/task-results/1/manual-check?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "score": 8,
    "checked_by": 2,
    "is_correct": false,
    "metrics": {
      "comment": "Частично верно. Не учтена важная деталь."
    }
  }'
```

---

## Коды ошибок

### 400 Bad Request

Ошибка валидации данных:

```json
{
  "detail": "Попытка уже завершена. Нельзя отправлять ответы в завершенную попытку."
}
```

Или:

```json
{
  "detail": "Время на выполнение истекло"
}
```

Или:

```json
{
  "detail": "score (15) не может быть больше max_score (10)"
}
```

---

### 403 Forbidden

Неверный или отсутствующий API ключ:

```json
{
  "detail": "Invalid or missing API Key"
}
```

---

### 404 Not Found

Ресурс не найден:

```json
{
  "detail": "Попытка с ID 999 не найдена"
}
```

Или:

```json
{
  "detail": "Результат с ID 999 не найден"
}
```

---

### 422 Unprocessable Entity

Ошибка валидации запроса (неверный формат JSON):

```json
{
  "detail": [
    {
      "loc": ["body", "items", 0, "answer", "type"],
      "msg": "value is not a valid enumeration member; permitted: 'SC', 'MC', 'SA', 'SA_COM', 'TA'",
      "type": "type_error.enum"
    }
  ]
}
```

---

### 500 Internal Server Error

Внутренняя ошибка сервера:

```json
{
  "detail": "Internal server error"
}
```

---

## Дополнительные ресурсы

- [Полная документация API Quiz системы](./api-reference.md) - Все эндпойнты для работы с задачами, проверкой, попытками и результатами
- [Примеры использования API Quiz системы](./api-examples.md) - Практические примеры работы с задачами, проверкой и статистикой
- [Документация импорта задач из Google Sheets](./import-api-documentation.md) - Полное руководство по импорту задач из Google Sheets
- [Краткая шпаргалка по импорту](./import-quick-start.md) - Быстрый старт по импорту задач
- [Ответы на вопросы разработчиков](./faq-developers-answers.md) - Краткие ответы на частые вопросы по API
- [Swagger UI](http://localhost:8000/docs) - Интерактивная документация API
- [Форматы JSONB полей](./api-examples.md#форматы-jsonb-полей) - Описание структуры TaskContent и SolutionRules

---

## Часто задаваемые вопросы (FAQ)

### 1. Как определяется набор заданий для попытки?

**Ответ:** Набор заданий для попытки определяется **на стороне клиента** (ТГ бота или веб-приложения). 

**Вариант A (рекомендуемый):** Задания передаются в `meta.task_ids` при создании попытки:

```json
{
  "user_id": 10,
  "course_id": 1,
  "source_system": "web",
  "meta": {
    "time_limit": 3600,
    "task_ids": [1, 2, 3, 4, 5]
  }
}
```

**Вариант B:** Можно получить все задания курса через `GET /tasks/by-course/{course_id}` и выбрать нужные на клиенте.

**Важно:**
- Бэкенд **не формирует** список заданий автоматически
- Задания можно отправлять по частям (не все сразу)
- Можно отправлять ответы по заданиям, которые не были указаны в `meta.task_ids` (если они существуют в системе)

**Рекомендация:** Используйте `meta.task_ids` для явного указания набора заданий попытки. Это позволяет:
- Контролировать, какие задания включены в попытку
- Отслеживать прогресс выполнения
- Ограничивать набор заданий для конкретной попытки

---

### 2. Как обрабатываются таймлимиты?

**Ответ:** Таймлимиты обрабатываются **на бэкенде** при отправке ответов.

**Механизм работы:**
1. При создании попытки можно указать `meta.time_limit` (в секундах)
2. При каждой отправке ответов бэкенд проверяет, не истекло ли время
3. Если время истекло, бэкенд возвращает ошибку `400 Bad Request` с сообщением "Время на выполнение истекло"

**Пример:**
```json
{
  "user_id": 10,
  "course_id": 1,
  "meta": {
    "time_limit": 3600  // 1 час в секундах
  }
}
```

**Рекомендации для фронтенда/ТГ бота:**
- ✅ **Рекомендуется** отслеживать время на клиенте и показывать таймер
- ✅ Можно блокировать отправку ответов после истечения времени (для лучшего UX)
- ⚠️ **Но бэкенд все равно проверит** время и отклонит запрос, если время истекло
- ✅ После истечения времени попытку можно завершить через `POST /attempts/{attempt_id}/finish`

**Важно:**
- Проверка времени происходит **при каждой отправке ответов**, а не при создании попытки
- Если `time_limit` не указан, ограничений по времени нет
- После завершения попытки (`finished_at` установлен) отправка ответов невозможна независимо от времени

---

### 3. Для каких типов заданий требуется ручная проверка?

**Ответ:** Ручная проверка требуется для заданий, у которых в `solution_rules` установлено:
- `auto_check: false` (автопроверка невозможна)
- `manual_review_required: true` (требуется обязательная ручная дооценка)

**Типы заданий:**
- **TA (Text Answer)** - всегда требуют ручной проверки (`auto_check: false`)
- **SC/MC/SA** - могут требовать ручной проверки, если `manual_review_required: true`

**Как определить задания, требующие проверки:**

Используйте эндпойнт `GET /task-results/by-pending-review` для получения списка результатов, требующих ручной проверки:

```
GET /task-results/by-pending-review?course_id=1&limit=50
```

Этот эндпойнт возвращает результаты, где `checked_at = null` (еще не проверены).

**Альтернативные способы:**

1. **Получить результаты по задаче** и фильтровать:
   ```
   GET /task-results/by-task/{task_id}
   ```
   Затем фильтровать результаты, где `checked_at = null`

2. **Получить все результаты** и фильтровать:
   ```
   GET /task-results/?skip=0&limit=1000
   ```
   Фильтровать по `checked_at = null`

**Workflow ручной проверки:**
1. Преподаватель/методист получает список результатов, требующих проверки
2. Выбирает результат для проверки
3. Вызывает `POST /task-results/{result_id}/manual-check` с новым баллом и комментариями
4. Результат обновляется с `checked_at` и `checked_by`

---

### 4. Какие дополнительные фильтры нужны для статистики?

**Текущее состояние:** Эндпойнты статистики не поддерживают фильтры по дате, типу задания, уровню сложности и т.д.

**Доступные эндпойнты:**
- `GET /task-results/stats/by-task/{task_id}` - статистика по задаче
- `GET /task-results/stats/by-course/{course_id}` - статистика по курсу
- `GET /task-results/stats/by-user/{user_id}` - статистика по пользователю

**Рекомендуемые фильтры (планируются к добавлению):**
- По дате (за период): `date_from`, `date_to`
- По уровню сложности: `difficulty_id`
- По типу задания: `task_type` (SC, MC, SA, TA)
- По статусу проверки: `checked_by` (null для автоматической, ID для ручной)

**Временное решение:**
Можно получить результаты через `GET /task-results/by-task/{task_id}` или `GET /task-results/by-user/{user_id}` и рассчитать статистику на клиенте с нужными фильтрами.

**Рекомендация:** Добавить параметры фильтрации в эндпойнты статистики (см. раздел "Планируемые улучшения").

**Примечание:** В настоящее время фильтры не поддерживаются, но можно получить результаты через `GET /task-results/by-task/{task_id}` или `GET /task-results/by-user/{user_id}` и рассчитать статистику на клиенте с нужными фильтрами.

---

### 5. Какие форматы таблиц поддерживаются для импорта из Google Sheets?

**Ответ:** Поддерживаются таблицы Google Sheets с заголовками в первой строке.

**Обязательные колонки:**
- `external_uid` (или `ID`, `код`) - Внешний идентификатор задания
- `type` (или `Тип`) - Тип задания: SC, MC, SA, SA_COM, TA
- `stem` (или `Вопрос`, `question`) - Формулировка вопроса
- `correct_answer` (или `Правильный ответ`) - Правильный ответ

**Опциональные колонки:**
- `options` (или `Варианты`) - Варианты ответа для SC/MC
- `max_score` (или `Балл`) - Максимальный балл
- `code` - Внутренний код задания
- `title` - Название задания
- `prompt` - Подсказка
- `accepted_answers` - Допустимые ответы для SA

**Пример таблицы:**

| external_uid | type | stem | options | correct_answer | max_score |
|--------------|------|------|---------|----------------|-----------|
| TASK-SC-001 | SC | Что такое переменная? | A: Область памяти \| B: Функция | A | 10 |
| TASK-MC-002 | MC | Какие способы создают список? | A: list() \| B: [] \| C: [1,2,3] | A,B | 15 |
| TASK-SA-003 | SA | Сколько элементов в [1,2,3]? | | 3 | 5 |

**Формат options:**
```
ID: Текст варианта | ID: Текст варианта
```

**Формат correct_answer:**
- SC: `A`
- MC: `A,B` или `A, B`
- SA: `3` или `3 | три`

**Подробная документация:** см. [Документация импорта из Google Sheets](./import-api-documentation.md)

**Обработка ошибок:**
- Импорт продолжается даже при ошибках в отдельных строках
- Все ошибки возвращаются в массиве `errors` с указанием номера строки
- Используйте `dry_run: true` для предварительной проверки

---

### 6. Есть ли эндпойнт для поиска заданий по тексту?

**Ответ:** Да, доступен эндпойнт `GET /tasks/search` для поиска заданий по содержимому.

**Использование:**
```
GET /tasks/search?q=переменная&course_id=1&limit=20
```

**Поиск выполняется по:**
- `task_content.stem` (формулировка вопроса)
- `task_content.title` (название задания, если указано)
- `external_uid` (внешний идентификатор)

**Параметры:**
- `q` (string, обязательный) - Поисковый запрос (минимум 2 символа)
- `course_id` (int, опциональный) - Фильтр по курсу
- `limit` (int, опциональный) - Максимум результатов (1-200, по умолчанию: 20)
- `offset` (int, опциональный) - Смещение для пагинации

**Другие доступные эндпойнты:**
- `GET /tasks/by-course/{course_id}` - получение заданий курса
- `GET /tasks/by-external/{external_uid}` - получение задания по внешнему ID
- `GET /tasks/{task_id}` - получение задания по ID
- `GET /tasks/` - список всех заданий с пагинацией

---

## Планируемые улучшения

### 1. Фильтры в статистике

**Планируется добавить параметры фильтрации:**
- `date_from` (datetime) - Начало периода
- `date_to` (datetime) - Конец периода
- `difficulty_id` (int) - Фильтр по уровню сложности
- `task_type` (string) - Фильтр по типу задания (SC, MC, SA, TA)
- `checked_by` (int|null) - Фильтр по статусу проверки

**Пример:**
```
GET /task-results/stats/by-course/1?date_from=2026-01-01&date_to=2026-02-01&task_type=SC
```

---

## Изменения в версии 1.0

### Основные возможности:

#### Задания (Tasks):
- ✅ CRUD операции с заданиями
- ✅ Получение заданий по курсу, по внешнему идентификатору
- ✅ Пакетная загрузка заданий (bulk-upsert)
- ✅ Предварительная валидация заданий
- ✅ Импорт заданий из Google Sheets
- ✅ Поддержка различных типов задач (SC, MC, SA, SA_COM, TA)
- ✅ Детальное описание структуры данных для каждого типа задания

#### Попытки (Attempts):
- ✅ Создание и управление попытками выполнения заданий
- ✅ Отправка ответов по задачам с автоматической проверкой
- ✅ Завершение попыток
- ✅ Валидация попыток при отправке ответов
- ✅ Поддержка таймлимитов для попыток

#### Результаты (Task Results):
- ✅ Получение результатов по пользователю, задаче или попытке
- ✅ Ручная дооценка результатов преподавателем
- ✅ CRUD операции с результатами заданий
- ✅ Статистика по задачам, курсам и пользователям
- ✅ Получение результатов, требующих ручной проверки (`GET /task-results/pending-review`)

#### Поиск и фильтрация:
- ✅ Поиск заданий по содержимому (`GET /tasks/search`)
- ✅ Получение результатов, требующих проверки (`GET /task-results/pending-review`)
