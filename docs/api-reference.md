# Полная документация API Quiz системы

**Версия:** 2.0  
**Дата обновления:** 2026-01-17  
**Базовый URL:** `http://localhost:8000/api/v1`  
**Swagger UI:** `http://localhost:8000/docs`

---

## Содержание

1. [Общая информация](#общая-информация)
2. [Аутентификация](#аутентификация)
3. [Роли и управление ими](#роли-и-управление-ими)
4. [Эндпойнты задач](#эндпойнты-задач)
5. [Эндпойнты проверки](#эндпойнты-проверки)
6. [Эндпойнты попыток](#эндпойнты-попыток)
7. [Эндпойнты результатов](#эндпойнты-результатов)
8. [Learning API (Learning Engine V1)](#learning-api-learning-engine-v1)
9. [Эндпойнты статистики](#эндпойнты-статистики)
10. [Эндпойнты материалов](#эндпойнты-материалов)
11. [Эндпойнты импорта](#эндпойнты-импорта)
12. [Коды ошибок](#коды-ошибок)

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

## Роли и управление ими

Справочник ролей, назначение ролей пользователям, фильтрация по ролям и заявки на доступ к ролям описаны в отдельном контракте:

**[Контракт: Роли и управление ими через API](roles-and-api-contract.md)**

В нём закреплены неизменяемые ID ролей по контракту с ТГ ботом (1=admin, 2=methodist, 3=teacher, 4=student, 5=marketer, 6=customer), правила добавления новых ролей и перечень эндпоинтов: `/roles/`, `/users/{user_id}/roles/`, фильтр `role` в `/users/` и `/users/search`, `/access_requests/` (в т.ч. `role_id`).

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
    "max_score": 10,
    "hints_text": [],
    "hints_video": [],
    "has_hints": false
  }
]
```

В ответах задач (TaskRead) присутствуют поля подсказок из `task_content` (Learning Engine V1, этап 5): `hints_text`, `hints_video`, `has_hints`. См. [assignments-and-results-api.md](assignments-and-results-api.md), [hints-stage5.md](hints-stage5.md).

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
    "meta": {"task_ids": [123]},
    "time_expired": false,
    "attempts_used": null,
    "attempts_limit_effective": null,
    "last_based_status": null
  }
]
```

Поля `time_expired`, `attempts_used`, `attempts_limit_effective`, `last_based_status` (Learning Engine V1, этап 4) — см. [assignments-and-results-api.md](assignments-and-results-api.md), [attempts-integration-stage4.md](attempts-integration-stage4.md).

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
- Для задач с комментарием (SA_COM): в `response` допускается поле `comment` (string | null), опционально; на проверку/баллы не влияет.

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
      "task_id": 2,
      "answer": {
        "type": "SA_COM",
        "response": {
          "value": "основной ответ",
          "comment": "комментарий ученика"
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
  "total_score_delta": 25,
  "total_max_score_delta": 30,
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

### POST /attempts/{attempt_id}/cancel (Learning Engine V1, этап 3.5)

Аннулировать активную попытку. Идемпотентно: повторный вызов возвращает `200` и `already_cancelled: true` без изменения данных.

**Параметры:**
- `attempt_id` (path, int) - ID попытки

**Тело запроса (опционально):**
```json
{
  "reason": "user_exit_to_main_menu"
}
```
Можно отправить пустое тело `{}` или не передавать body.

**Ответ (200 OK):**
```json
{
  "attempt_id": 1,
  "status": "cancelled",
  "cancelled_at": "2026-02-26T12:00:00Z",
  "already_cancelled": false
}
```
- `already_cancelled: true` — попытка уже была отменена ранее (идемпотентный ответ).

**Ошибки:**
- `404` - Попытка не найдена
- `409` - Попытка уже завершена (`finished_at` задан); отменять можно только активную попытку

**Поведение:**
- Отменённая попытка не считается активной: не возвращается в `POST /learning/tasks/{task_id}/start-or-get-attempt`.
- В статистике и в «последней попытке» по задаче/курсу отменённые попытки не учитываются (учитываются только с `finished_at` и без `cancelled_at`).

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

## Learning API (Learning Engine V1)

Эндпоинты маршрутизации и состояний Learning Engine (этап 3). Консолидированное описание: [assignments-and-results-api.md](assignments-and-results-api.md). Примеры и smoke: [smoke-learning-api.md](smoke-learning-api.md).

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/learning/next-item?student_id=` | Следующий шаг: material \| task \| none \| blocked_dependency \| blocked_limit. |
| POST | `/learning/materials/{material_id}/complete` | Отметить материал пройденным (body: `student_id`). |
| POST | `/learning/tasks/{task_id}/start-or-get-attempt` | Начать или получить текущую попытку по задаче. Гарантия: в ответе `GET /attempts/{id}` поле `attempt.meta.task_ids` (int[]) содержит как минимум этот `task_id`; при пустом/битом `meta` backend восстанавливает его при вызове. |
| GET | `/learning/tasks/{task_id}/state?student_id=` | Состояние задания: OPEN \| IN_PROGRESS \| PASSED \| FAILED \| BLOCKED_LIMIT. |
| POST | `/learning/tasks/{task_id}/request-help` | Запрос помощи (body: `student_id`, `message`). |
| POST | `/learning/tasks/{task_id}/hint-events` | Фиксация открытия подсказки (этап 3.6). Body: `student_id`, `attempt_id`, `hint_type`, `hint_index`, `action`, `source`. Идемпотентно в окне дедупа. |
| POST | `/teacher/task-limits/override` | Переопределение лимита попыток (body: `student_id`, `task_id`, `max_attempts_override`, `updated_by`). |
| GET | `/teacher/help-requests?teacher_id=&status=open\|closed\|all&request_type=manual_help\|blocked_limit\|all&limit=&offset=` | Список заявок на помощь (этап 3.8/3.8.1). Поля: request_type, auto_created, context. Фильтр request_type — по типу заявки. ACL: назначенный teacher, student_teacher_links, teacher_courses или роль methodist. |
| GET | `/teacher/help-requests/{request_id}?teacher_id=` | Карточка заявки (поля списка + message, closed_at, closed_by, resolution_comment, history). В списке и карточке: request_type, auto_created, context (этап 3.8.1). |
| POST | `/teacher/help-requests/{request_id}/close` | Закрыть заявку (body: `closed_by`, `resolution_comment`). Идемпотентно. |
| POST | `/teacher/help-requests/{request_id}/reply` | Ответить студенту (body: `teacher_id`, `message`, `close_after_reply`, `idempotency_key`). Создаёт сообщение в messages, опционально закрывает заявку. |

Ответ `POST /learning/tasks/{task_id}/request-help` с этапа 3.8 может содержать опциональное поле `request_id` (ID заявки в help_requests). С этапа 3.8.1 при `GET /learning/next-item` или `GET /learning/tasks/{task_id}/state` с типом/состоянием `blocked_limit`/`BLOCKED_LIMIT` заявка на помощь создаётся автоматически (request_type=blocked_limit, auto_created=true, context с attempts_used/attempts_limit_effective). Список заявок можно фильтровать по `request_type=manual_help|blocked_limit|all`. Smoke-сценарий заявок: [smoke-learning-engine-stage3-8-help-requests.md](smoke-learning-engine-stage3-8-help-requests.md).

Все запросы требуют `api_key` в query. Ответы содержат поля, описанные в этапных документах (state: `attempts_used`, `attempts_limit_effective`, `last_attempt_id` и т.д.).

### POST /learning/tasks/{task_id}/hint-events (этап 3.6)

Фиксация открытия подсказки (text/video) для аналитики. Идемпотентно: повтор в окне дедупа (5 мин) возвращает тот же `event_id` и `deduplicated: true`.

**Тело запроса:**
```json
{
  "student_id": 2,
  "attempt_id": 47,
  "hint_type": "text",
  "hint_index": 0,
  "action": "open",
  "source": "student_execute"
}
```

**Ответ (200 OK):**
```json
{
  "ok": true,
  "deduplicated": false,
  "event_id": 123
}
```

**Ошибки:** `404` — задание/студент/попытка не найдены; `409` — попытка не принадлежит студенту или не в контексте задания/курса.

---

## Эндпойнты статистики

**Learning Engine V1, этап 6:** основной статус и прогресс считаются по **последней завершённой попытке** (last-attempt). Поля `average_score`, `total_attempts`, `total_score`, `total_max_score`, `min_score`, `max_score` остаются дополнительными (по всем попыткам). Подробнее: [last-attempt-statistics-stage6.md](last-attempt-statistics-stage6.md).

### GET /task-results/stats/by-task/{task_id}

Статистика по задаче. Основные показатели: `progress_percent`, `passed_tasks_count`, `failed_tasks_count` (по last-attempt).

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
  "score_distribution": {},
  "progress_percent": 70.0,
  "passed_tasks_count": 7,
  "failed_tasks_count": 3,
  "last_passed_count": 7,
  "last_failed_count": 3,
  "hints_used_count": 12,
  "used_text_hints_count": 8,
  "used_video_hints_count": 4
}
```

**Поля этапа 3.6:** `hints_used_count`, `used_text_hints_count`, `used_video_hints_count` — число событий открытия подсказок (по `learning_events` с `event_type='hint_open'`).

**Ошибки:**
- `404` - Задача не найдена

---

### GET /task-results/stats/by-course/{course_id}

Статистика по курсу. Основные показатели: `progress_percent`, `passed_tasks_count`, `failed_tasks_count` (по last-attempt).

**Параметры:**
- `course_id` (path, int) - ID курса

**Ответ (200 OK):**
```json
{
  "course_id": 1,
  "total_attempts": 50,
  "average_score": 75.5,
  "correct_percentage": 65.0,
  "tasks_count": 28,
  "progress_percent": 65.0,
  "passed_tasks_count": 120,
  "failed_tasks_count": 65,
  "hints_used_count": 45,
  "used_text_hints_count": 30,
  "used_video_hints_count": 15
}
```

**Поля этапа 3.6:** `hints_used_count`, `used_text_hints_count`, `used_video_hints_count` — агрегат по задачам курса.

**Ошибки:**
- `404` - Курс не найден

---

### GET /task-results/stats/by-user/{user_id}

Статистика по пользователю. Основной прогресс: `progress_percent`, `passed_tasks_count`, `failed_tasks_count`, `current_score`, `current_ratio`, `last_score`, `last_max_score`, `last_ratio` (по last-attempt).

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
  "completion_percentage": 80.0,
  "progress_percent": 75.0,
  "passed_tasks_count": 3,
  "failed_tasks_count": 1,
  "current_score": 24,
  "current_ratio": 0.8,
  "last_score": 24,
  "last_max_score": 30,
  "last_ratio": 0.8,
  "hints_used_count": 5,
  "used_text_hints_count": 3,
  "used_video_hints_count": 2
}
```

**Поля этапа 3.6:** `hints_used_count`, `used_text_hints_count`, `used_video_hints_count` — число открытий подсказок пользователем.

**Ошибки:**
- `404` - Пользователь не найден

---

## Эндпойнты материалов

API учебных материалов курса: CRUD, список по курсу, изменение порядка, перемещение, массовое обновление активности, копирование в другой курс, статистика, импорт из Google Sheets.

**Полная документация:** [API учебных материалов](materials-api.md)

Основные эндпойнты:

- **GET** `/materials/search` — поиск материалов по title и external_uid (параметр q обязателен; course_id опционально — по всем курсам или по одному)
- **GET** `/courses/{course_id}/materials` — список материалов курса (параметр q — поиск по title/external_uid в рамках курса; фильтры: is_active, type; сортировка: order_position, title, created_at; пагинация skip/limit)
- **POST** `/materials` — создание материала
- **GET** `/materials/{id}` — получение материала
- **PATCH** `/materials/{id}` — обновление материала (при изменении content передаётся полный объект content для типа)
- **DELETE** `/materials/{id}` — удаление материала
- **POST** `/courses/{course_id}/materials/reorder` — изменить порядок материалов
- **POST** `/materials/{material_id}/move` — переместить материал (new_order_position опционально при переносе в другой курс — тогда в конец; course_id опционально — при null только смена позиции)
- **POST** `/courses/{course_id}/materials/bulk-update` — массовое обновление is_active
- **POST** `/materials/{material_id}/copy` — копировать материал в другой курс
- **GET** `/courses/{course_id}/materials/stats` — статистика по материалам курса
- **POST** `/materials/upload` — загрузить файл для контента материала (multipart; возвращает url для content.sources[0].url или content.url)
- **GET** `/materials/files/{file_id}` — скачать загруженный файл материала
- **POST** `/materials/import/google-sheets` — импорт материалов из Google Таблицы (многокурсовой; dry_run поддерживается)

Типы материалов: `text`, `video`, `audio`, `image`, `link`, `pdf`, `office_document`, `script`, `document`. Структура поля `content` зависит от типа — см. [materials-api.md](materials-api.md).

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
- [API управления заданиями и результатами учеников](./assignments-and-results-api.md) - Подробная документация по эндпойнтам попыток, результатов заданий, ручной проверке и статистике
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
