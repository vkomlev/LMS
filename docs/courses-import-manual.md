# Мануал по импорту курсов из Google Sheets

Подробное руководство по использованию эндпойнта импорта курсов из Google Sheets таблиц.

## Содержание

1. [Обзор](#обзор)
2. [Подготовка Google Sheets таблицы](#подготовка-google-sheets-таблицы)
3. [Настройка доступа](#настройка-доступа)
4. [Формат данных](#формат-данных)
5. [Использование API](#использование-api)
6. [Обработка ошибок](#обработка-ошибок)
7. [Примеры использования](#примеры-использования)
8. [Часто задаваемые вопросы](#часто-задаваемые-вопросы)

---

## Обзор

Эндпойнт `POST /api/v1/courses/import/google-sheets` позволяет массово импортировать курсы из Google Sheets таблицы в систему LMS.

### Основные возможности

- ✅ **Массовый импорт** - импорт множества курсов одной операцией
- ✅ **Upsert по course_uid** - автоматическое создание новых или обновление существующих курсов
- ✅ **Иерархия курсов** - поддержка родительских курсов через `parent_course_uid`
- ✅ **Зависимости курсов** - автоматическое создание зависимостей через `required_courses_uid`
- ✅ **Валидация данных** - проверка данных перед импортом
- ✅ **Dry Run режим** - предварительная проверка без сохранения в БД
- ✅ **Частичный успех** - импорт продолжается даже при ошибках в отдельных строках

### Процесс импорта

1. Извлечение `spreadsheet_id` из URL
2. Чтение данных из указанного листа через Google Sheets API
3. Парсинг каждой строки данных в структуру курса
4. Валидация данных (структура, ссылочная целостность)
5. Импорт курсов через `bulk_upsert` (создание новых или обновление существующих)
6. Обработка зависимостей между курсами
7. Возврат детального отчета с результатами

---

## Подготовка Google Sheets таблицы

### Структура таблицы

Таблица должна содержать следующие колонки (в указанном порядке):

| Колонка | Тип | Обязательность | Описание |
|---------|-----|----------------|----------|
| `course_uid` | string | ✅ Обязательно | Уникальный код курса (ключ для upsert) |
| `title` | string | ✅ Обязательно | Название курса |
| `description` | string | ⚪ Опционально | Описание курса |
| `access_level` | enum | ✅ Обязательно | Уровень доступа (см. ниже) |
| `parent_course_uid` | string | ⚪ Опционально | Код родительского курса (пусто = корневой) |
| `order_number` | int | ⚪ Опционально | Порядковый номер подкурса внутри родительского курса (используется только если указан `parent_course_uid`). Если не указан, устанавливается автоматически триггером БД. |
| `required_courses_uid` | string | ⚪ Опционально | Список кодов зависимых курсов через запятую |
| `is_required` | bool | ⚪ Опционально | Обязательный ли курс (true/false, по умолчанию false) |

### Уровни доступа (access_level)

Допустимые значения (строго):
- `self_guided` - самостоятельное обучение
- `auto_check` - автоматическая проверка
- `manual_check` - ручная проверка
- `group_sessions` - групповые занятия
- `personal_teacher` - персональный преподаватель

### Пример таблицы

| course_uid | title | description | access_level | parent_course_uid | order_number | required_courses_uid | is_required |
|------------|-------|-------------|--------------|-------------------|--------------|---------------------|-------------|
| COURSE-PY-01 | Основы Python | Введение в Python | self_guided | | | | false |
| COURSE-PY-02 | Python: Продвинутый уровень | Генераторы, декораторы | auto_check | COURSE-PY-01 | 1 | COURSE-PY-01 | false |
| COURSE-PY-03 | Python: ООП | Объектно-ориентированное программирование | auto_check | COURSE-PY-01 | 2 | COURSE-PY-02 | false |
| COURSE-DS-01 | Введение в Data Science | Основы работы с данными | manual_check | | | COURSE-PY-01,COURSE-MATH-01 | false |
| COURSE-MATH-01 | Математика для программистов | Основы математики | auto_check | | | | true |

### Правила валидации

1. **course_uid** должен быть уникальным в таблице и в системе
2. **parent_course_uid**, если указан, должен ссылаться на существующий `course_uid` (в этом же файле или уже в БД)
3. **order_number**: целое положительное число. Используется только если указан `parent_course_uid`. Если не указан, порядковый номер устанавливается автоматически триггером БД. При указании `order_number` триггер БД автоматически пересчитает порядковые номера остальных подкурсов родителя.
4. **required_courses_uid**: каждая ссылка должна существовать; self-dependency запрещен
5. Пустые строки/пробелы в списках зависимостей игнорируются (`"A, B"` допустимо)
6. **is_required**: допустимые значения - `true`, `false`, `1`, `0`, `yes`, `no`, `да`, `нет` (регистр не важен)

---

## Настройка доступа

### Шаг 1: Создание Service Account

1. Откройте [Google Cloud Console](https://console.cloud.google.com/)
2. Создайте проект или выберите существующий
3. Включите Google Sheets API
4. Создайте Service Account:
   - IAM & Admin → Service Accounts → Create Service Account
   - Укажите имя и описание
   - Создайте ключ (JSON) и скачайте файл

### Шаг 2: Настройка доступа к таблице

1. Откройте вашу Google Sheets таблицу
2. Нажмите "Настройки доступа" (Share)
3. Добавьте email Service Account (из JSON файла, поле `client_email`)
4. Установите права: **"Редактор"** или **"Читатель"**

### Шаг 3: Настройка переменных окружения

Добавьте в файл `.env`:

```env
GSHEETS_SERVICE_ACCOUNT_JSON=secrets/gscapi-390409-4a9cf4824d2c.json
```

Где `secrets/gscapi-390409-4a9cf4824d2c.json` - путь к JSON файлу с credentials Service Account.

---

## Формат данных

### Обязательные поля

#### `course_uid` (string)
- Уникальный код курса
- Используется как ключ для upsert (если курс существует - обновляется, иначе создается)
- Примеры: `COURSE-PY-01`, `PY-BASICS`, `MATH-101`

#### `title` (string)
- Название курса
- Примеры: `Основы Python`, `Математика для программистов`

#### `access_level` (enum)
- Уровень доступа к курсу
- Допустимые значения: `self_guided`, `auto_check`, `manual_check`, `group_sessions`, `personal_teacher`

### Опциональные поля

#### `description` (string)
- Описание курса
- Может быть пустым
- Пример: `Введение в Python: переменные, типы данных, условия, циклы`

#### `parent_course_uid` (string)
- Код родительского курса для создания иерархии
- Если пусто или `null` - курс становится корневым
- Должен ссылаться на существующий `course_uid` (в этом же файле или уже в БД)
- Пример: `COURSE-PY-01`

#### `order_number` (int, опционально)
- Порядковый номер подкурса внутри родительского курса
- Используется только если указан `parent_course_uid`
- Должно быть целым положительным числом (≥ 1)
- Если не указан, порядковый номер устанавливается автоматически триггером БД
- При указании `order_number` триггер БД автоматически пересчитает порядковые номера остальных подкурсов родителя
- Пример: `1`, `2`, `3`

#### `required_courses_uid` (string)
- Список кодов зависимых курсов через запятую
- Каждая ссылка должна существовать
- Self-dependency запрещен
- Пустые строки/пробелы игнорируются
- Примеры:
  - `COURSE-PY-01`
  - `COURSE-PY-01,COURSE-MATH-01`
  - `COURSE-PY-01, COURSE-MATH-01` (пробелы допустимы)

#### `is_required` (bool)
- Обязательный ли курс
- Допустимые значения: `true`, `false`, `1`, `0`, `yes`, `no`, `да`, `нет` (регистр не важен)
- По умолчанию: `false`
- Примеры: `true`, `false`, `TRUE`, `False`, `1`, `0`

---

## Использование API

### Базовый запрос

```bash
curl -X POST "http://localhost:8000/api/v1/courses/import/google-sheets?api_key=YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit",
    "sheet_name": "Courses",
    "dry_run": false
  }'
```

### Параметры запроса

#### `spreadsheet_url` (string, required)
- URL таблицы Google Sheets или только `spreadsheet_id`
- Примеры:
  - `https://docs.google.com/spreadsheets/d/185yB39jP8IF_SGJTpWMRXPHYYXF6FZz6Pji70O8Krhc/edit`
  - `185yB39jP8IF_SGJTpWMRXPHYYXF6FZz6Pji70O8Krhc`

#### `sheet_name` (string, optional)
- Название листа в таблице
- По умолчанию: `"Courses"`
- Примеры: `"Courses"`, `"Курсы"`, `"Sheet1"`

#### `column_mapping` (dict, optional)
- Кастомный маппинг колонок таблицы на поля курса
- Если не указан, используется автоматический маппинг по стандартным названиям
- Формат: `{"название_колонки_в_таблице": "поле_курса"}`
- Пример:
```json
{
  "column_mapping": {
    "Код": "course_uid",
    "Название": "title",
    "Описание": "description",
    "Уровень доступа": "access_level",
    "Родитель": "parent_course_uid",
    "Зависимости": "required_courses_uid",
    "Обязательный": "is_required"
  }
}
```

#### `dry_run` (bool, optional)
- Режим проверки без сохранения
- По умолчанию: `false`
- Если `true`: данные валидируются, но не сохраняются в БД
- Рекомендуется использовать для предварительной проверки перед реальным импортом

### Ответ API

#### Успешный ответ (200)

```json
{
  "imported": 10,
  "updated": 0,
  "errors": [],
  "total_rows": 10
}
```

Поля:
- `imported` (int): количество созданных курсов
- `updated` (int): количество обновленных курсов
- `errors` (array): список ошибок (пустой массив = нет ошибок)
- `total_rows` (int): общее количество обработанных строк данных (без заголовка)

#### Ответ с ошибками (200)

```json
{
  "imported": 8,
  "updated": 0,
  "errors": [
    {
      "row_index": 3,
      "course_uid": "COURSE-PY-03",
      "error": "Родительский курс с course_uid 'COURSE-PY-99' не найден"
    },
    {
      "row_index": 5,
      "course_uid": null,
      "error": "Обязательное поле 'course_uid' (колонка 'course_uid') пустое"
    }
  ],
  "total_rows": 10
}
```

**Важно:** Импорт возвращает статус 200 даже при наличии ошибок в отдельных строках. Проверяйте массив `errors` для выявления проблем.

---

## Обработка ошибок

### Типы ошибок

#### Ошибки валидации данных

- **Пустое обязательное поле**: `"Обязательное поле 'course_uid' (колонка 'course_uid') пустое"`
- **Неподдерживаемый access_level**: `"Неподдерживаемый уровень доступа: invalid_value. Допустимые значения: ..."`
- **Родительский курс не найден**: `"Родительский курс с course_uid 'COURSE-PY-99' не найден"`
- **Зависимый курс не найден**: зависимость пропускается (не добавляется в errors, но не создается)

#### Ошибки доступа

- **403 Forbidden**: Service Account не имеет доступа к таблице
- **404 Not Found**: таблица или лист не найдены
- **500 Internal Server Error**: ошибка при чтении Google Sheets API

### Стратегия обработки ошибок

1. **Частичный успех**: импорт продолжается даже при ошибках в отдельных строках
2. **Детальная информация**: каждая ошибка содержит номер строки и описание проблемы
3. **Валидация перед импортом**: используйте `dry_run: true` для предварительной проверки

### Рекомендации

1. **Всегда используйте dry_run** перед реальным импортом
2. **Проверяйте массив errors** в ответе
3. **Исправляйте ошибки** в таблице и повторяйте импорт
4. **Проверяйте логи** приложения (`logs/app.log`) для детальной диагностики

---

## Примеры использования

### Пример 1: Dry Run (предварительная проверка)

```bash
curl -X POST "http://localhost:8000/api/v1/courses/import/google-sheets?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/185yB39jP8IF_SGJTpWMRXPHYYXF6FZz6Pji70O8Krhc/edit",
    "sheet_name": "Courses",
    "dry_run": true
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

### Пример 2: Реальный импорт

```bash
curl -X POST "http://localhost:8000/api/v1/courses/import/google-sheets?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "spreadsheet_url": "185yB39jP8IF_SGJTpWMRXPHYYXF6FZz6Pji70O8Krhc",
    "sheet_name": "Courses",
    "dry_run": false
  }'
```

### Пример 3: Импорт с кастомным маппингом колонок

```bash
curl -X POST "http://localhost:8000/api/v1/courses/import/google-sheets?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "spreadsheet_url": "185yB39jP8IF_SGJTpWMRXPHYYXF6FZz6Pji70O8Krhc",
    "sheet_name": "Курсы",
    "column_mapping": {
      "Код": "course_uid",
      "Название": "title",
      "Описание": "description",
      "Уровень доступа": "access_level",
      "Родитель": "parent_course_uid",
      "Зависимости": "required_courses_uid",
      "Обязательный": "is_required"
    },
    "dry_run": false
  }'
```

### Пример 4: Проверка через PowerShell

```powershell
$apiKey = "bot-key-1"
$body = @{
    spreadsheet_url = "https://docs.google.com/spreadsheets/d/185yB39jP8IF_SGJTpWMRXPHYYXF6FZz6Pji70O8Krhc/edit"
    sheet_name = "Courses"
    dry_run = $false
} | ConvertTo-Json -Depth 10

$headers = @{"Content-Type" = "application/json"}

$response = Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/courses/import/google-sheets?api_key=$apiKey" `
    -Method POST `
    -Headers $headers `
    -Body $body

$response | ConvertTo-Json -Depth 10
```

---

## Часто задаваемые вопросы

### Q: Как проверить, что импорт прошел успешно?

A: Проверьте ответ API:
- `errors` должен быть пустым массивом
- `imported + updated` должно равняться `total_rows`
- Проверьте данные в БД через API или напрямую

### Q: Что делать, если родительский курс не найден?

A: Убедитесь, что:
1. Родительский курс импортирован раньше (в этой же таблице выше)
2. Или родительский курс уже существует в БД
3. `parent_course_uid` указан правильно (без опечаток)

### Q: Можно ли обновить существующие курсы?

A: Да, импорт работает как **upsert**:
- Если курс с таким `course_uid` существует - он обновляется
- Если не существует - создается новый
- При повторном импорте `updated` будет показывать количество обновленных курсов

### Q: Как обрабатываются зависимости между курсами?

A: Зависимости обрабатываются **после** импорта всех курсов:
- Если зависимый курс не найден - зависимость пропускается (не создается)
- Self-dependency запрещен автоматически
- Зависимости создаются только для успешно импортированных курсов

### Q: Что делать, если Service Account не имеет доступа?

A: 
1. Откройте таблицу Google Sheets
2. Нажмите "Настройки доступа" (Share)
3. Добавьте email Service Account (из JSON файла)
4. Установите права: "Редактор" или "Читатель"

### Q: Можно ли импортировать курсы без course_uid?

A: Нет, `course_uid` обязателен, так как используется как ключ для upsert. Курсы без `course_uid` будут пропущены с ошибкой.

### Q: Как работает автоматический маппинг колонок?

A: Система ищет колонки по стандартным названиям (регистр не важен):
- `course_uid`, `uid`, `id`, `код`, `course code` → `course_uid`
- `title`, `название`, `name` → `title`
- `description`, `описание`, `desc` → `description`
- `access_level`, `access level`, `уровень доступа`, `тип доступа` → `access_level`
- `parent_course_uid`, `parent`, `родитель`, `parent course` → `parent_course_uid`
- `order_number`, `order number`, `порядковый номер`, `порядок`, `order` → `order_number`
- `required_courses_uid`, `required courses`, `зависимости`, `dependencies` → `required_courses_uid`
- `is_required`, `required`, `обязательный`, `mandatory` → `is_required`

### Q: Что происходит с пустыми строками в таблице?

A: Пустые строки (где все ячейки пустые) автоматически пропускаются при импорте.

---

## Дополнительные ресурсы

- [API документация курсов](courses-api.md) - полная документация всех эндпойнтов курсов
- [Результаты тестирования](test-courses-import-results.md) - результаты тестирования импорта
- [Swagger UI](http://localhost:8000/docs) - интерактивная документация API

---

**Последнее обновление:** 21.01.2026
