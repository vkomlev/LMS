# Тестовый файл для импорта заданий

## Файл: `tasks_import_test.xlsx` (или `tasks_import_test__new.xlsx`)

Этот файл содержит разнообразные тестовые задания для проверки функционала импорта из Google Sheets.

Если `tasks_import_test.xlsx` был открыт в Excel и заблокирован, генератор сохранит новый файл как `tasks_import_test__new.xlsx`.

## Содержимое

Файл содержит **19 заданий** различных типов:

- **SC (Single Choice)**: 4 задания - выбор одного правильного ответа
- **MC (Multiple Choice)**: 4 задания - выбор нескольких правильных ответов
- **SA (Short Answer)**: 5 заданий - короткий текстовый ответ
- **SA_COM (Short Answer with Comments)**: 2 задания - короткий ответ с комментариями
- **TA (Text Answer)**: 4 задания - развернутый ответ (требует ручной проверки)

## Структура файла

### Обязательные колонки:
- `external_uid` - уникальный идентификатор задания
- `course_uid` - код курса **для строки** (courses.course_uid). Позволяет импортировать задания в разные подкурсы в одной таблице
- `type` - тип задания (SC, MC, SA, SA_COM, TA)
- `stem` - формулировка вопроса
- `correct_answer` - правильный ответ

### Опциональные колонки:
- `options` - варианты ответа (для SC/MC)
- `max_score` - максимальный балл
- `code` - внутренний код задания
- `title` - название задания
- `prompt` - подсказка для ученика
- `input_link` - ссылка на входные данные
- `accepted_answers` - допустимые варианты ответа (для SA/SA_COM)

## Использование

### 1. Загрузите файл в Google Sheets

1. Откройте Google Sheets
2. Создайте новую таблицу
3. Импортируйте файл `tasks_import_test.xlsx` или скопируйте данные вручную
4. Убедитесь, что лист называется "Tasks" (или укажите другое название)

### 2. Настройте доступ для Service Account

1. Откройте таблицу в Google Sheets
2. Нажмите "Настройки доступа" (Share)
3. Добавьте email из Service Account (указан в JSON-файле, поле `client_email`)
4. Дайте права "Читатель" (Viewer)

### 3. Выполните импорт через API

```bash
curl -X POST "http://localhost:8000/api/v1/tasks/import/google-sheets?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID/edit",
    "sheet_name": "Tasks",
    "difficulty_code": "NORMAL",
    "dry_run": false
  }'
```

Примечание: если колонка `course_uid` заполнена в каждой строке, `course_code/course_id` в запросе можно не указывать.

### 4. Проверьте результаты

```bash
# Проверка импортированных заданий
curl "http://localhost:8000/api/v1/tasks/by-course/1?api_key=bot-key-1"

# Проверка конкретного задания
curl "http://localhost:8000/api/v1/tasks/by-external/TEST-SC-001?api_key=bot-key-1"
```

## Регенерация файла

Для обновления тестовых данных запустите:

```bash
python tests/generate_tasks_import_xlsx.py
```

Файл будет создан в корне проекта как `tasks_import_test.xlsx`. Если файл занят (открыт в Excel) — как `tasks_import_test__new.xlsx`.

## Примечания

- Все `external_uid` начинаются с префикса `TEST-` для удобства идентификации тестовых данных
- Задания TA (Text Answer) требуют ручной проверки - поле `correct_answer` содержит "Нет правильного ответа"
- Для заданий SA/SA_COM в колонке `accepted_answers` указаны альтернативные варианты ответов через `|`
- Формат `options` для SC/MC: `A: Текст | B: Текст | C: Текст`
- Формат `correct_answer` для MC: `A,B,C` (несколько вариантов через запятую)

## Тестирование через Telegram бот

После импорта заданий можно протестировать их через Telegram бот:

1. Создайте попытку (attempt) с заданиями из импортированного курса
2. Отправьте ответы на задания разных типов
3. Проверьте автоматическую проверку для SC, MC, SA, SA_COM
4. Проверьте ручную проверку для TA заданий через эндпойнт `/task-results/by-pending-review`
