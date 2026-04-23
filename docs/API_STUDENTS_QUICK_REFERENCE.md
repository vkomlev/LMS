# Быстрая справка: API для управления студентами

**Версия API:** v1  
**Базовый URL:** `http://localhost:8000/api/v1`  
**Аутентификация:** `?api_key=bot-key-1` (обязательно для всех запросов)

## 📋 Сводная таблица эндпойнтов

### Список и поиск пользователей

| Метод | URL | Параметры | Описание |
|-------|-----|-----------|----------|
| `GET` | `/users/` | `skip`, `limit`, `sort_by`, `order`, `role` | Список с сортировкой и фильтрацией |
| `GET` | `/users/search` | `q`, `limit`, `offset` | Поиск по имени |
| `GET` | `/users/{id}` | - | Информация о пользователе |
| `GET` | `/users/by-tg/{tg_id}` | - | ID по Telegram ID |

### Связь студент-преподаватель

| Метод | URL | Параметры | Описание |
|-------|-----|-----------|----------|
| `GET` | `/users/{student_id}/teachers` | - | Список преподавателей студента |
| `GET` | `/users/{teacher_id}/students` | - | Список студентов преподавателя |
| `POST` | `/users/{student_id}/teachers/{teacher_id}` | - | Привязать преподавателя |
| `DELETE` | `/users/{student_id}/teachers/{teacher_id}` | - | Отвязать преподавателя |

### Связь студент-курс

| Метод | URL | Параметры | Описание |
|-------|-----|-----------|----------|
| `POST` | `/user-courses/` | `user_id`, `course_id`, `order_number` | Привязать к курсу |
| `DELETE` | `/user-courses/{user_id}/{course_id}` | - | Отвязать от курса |

### Редактирование пользователей

| Метод | URL | Параметры | Описание |
|-------|-----|-----------|----------|
| `PATCH` | `/users/{id}` | `email`, `full_name`, `tg_id` | Частичное обновление |
| `PUT` | `/users/{id}` | `email`, `full_name`, `tg_id` | Полное обновление |
| `POST` | `/users/` | `email`, `full_name`, `tg_id` | Создать пользователя |
| `DELETE` | `/users/{id}` | - | Удалить пользователя |

---

## 🎯 Типичные сценарии использования

### 1. Получить список всех студентов, отсортированных по ФИО

```bash
GET /api/v1/users/?role=student&sort_by=full_name&order=asc&api_key=bot-key-1
```

### 2. Найти студента по имени

```bash
GET /api/v1/users/search?q=Иван&api_key=bot-key-1
```

### 3. Привязать студента к преподавателю

```bash
POST /api/v1/users/13/teachers/16?api_key=bot-key-1
```

### 4. Привязать студента к курсу

```bash
POST /api/v1/user-courses/?api_key=bot-key-1
Content-Type: application/json

{
  "user_id": 13,
  "course_id": 1,
  "order_number": null
}
```

### 5. Обновить имя студента

```bash
PATCH /api/v1/users/13?api_key=bot-key-1
Content-Type: application/json

{
  "full_name": "Новое Имя"
}
```

### 6. Получить список преподавателей студента

```bash
GET /api/v1/users/13/teachers?api_key=bot-key-1
```

### 7. Отвязать студента от курса

```bash
DELETE /api/v1/user-courses/13/1?api_key=bot-key-1
```

---

## 📊 Параметры GET /api/v1/users/

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `skip` | `int` | `0` | Смещение (пагинация) |
| `limit` | `int` | `100` | Лимит (1-1000) |
| `sort_by` | `enum` | `full_name` | Поле: `full_name`, `email`, `created_at` |
| `order` | `enum` | `asc` | Направление: `asc`, `desc` |
| `role` | `string` | `null` | Фильтр по роли: `student`, `teacher` и т.д. |

---

## 🔑 Коды ответов

| Код | Описание |
|-----|----------|
| `200` | Успешно (GET, PUT, PATCH) |
| `201` | Создано (POST) |
| `204` | Нет содержимого (DELETE, POST для связей) |
| `400` | Некорректный запрос (дубликат и т.д.) |
| `403` | Неверный API ключ |
| `404` | Не найдено |
| `422` | Ошибка валидации |

---

## 📚 Полная документация

Подробная документация со всеми примерами, схемами данных и FAQ доступна в:
- **Markdown:** `docs/API_STUDENTS_MANAGEMENT.md`
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

---

**Дата создания:** 26 января 2026
