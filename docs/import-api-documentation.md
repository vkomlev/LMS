# Документация API импорта задач из Google Sheets

**Версия:** 1.0  
**Дата:** 2026-01-17  
**Базовый URL:** `http://localhost:8000/api/v1`

---

## Содержание

1. [Обзор](#обзор)
2. [Требования к файлу импорта](#требования-к-файлу-импорта)
3. [Порядок импорта](#порядок-импорта)
4. [API Эндпойнты](#api-эндпойнты)
5. [Примеры использования](#примеры-использования)
6. [Обработка ошибок](#обработка-ошибок)
7. [Часто задаваемые вопросы](#часто-задаваемые-вопросы)

---

## Обзор

API импорта задач из Google Sheets позволяет массово импортировать задачи в систему LMS из таблиц Google Sheets. Импорт поддерживает все типы задач: SC (Single Choice), MC (Multiple Choice), SA (Short Answer), SA_COM (Short Answer with Comments), TA (Text Answer).

### Основные возможности

- ✅ Массовый импорт задач из Google Sheets
- ✅ Автоматическая валидация данных
- ✅ Поддержка всех типов задач
- ✅ Режим dry_run для проверки без сохранения
- ✅ Детальный отчет об ошибках
- ✅ Автоматический маппинг колонок
- ✅ Поддержка кастомного маппинга колонок

---

## Требования к файлу импорта

### Формат таблицы

Таблица должна быть в формате Google Sheets с заголовками в первой строке и данными задач в последующих строках.

### Обязательные колонки

| Колонка | Описание | Тип данных | Пример |
|---------|----------|------------|--------|
| `external_uid` | Внешний уникальный идентификатор задачи | Строка | `TASK-SC-001` |
| `type` | Тип задачи | Строка | `SC`, `MC`, `SA`, `SA_COM`, `TA` |
| `stem` | Формулировка вопроса/задачи | Строка | `Что такое переменная в Python?` |
| `correct_answer` | Правильный ответ | Строка | `A` или `A,B` или `8` |

### Опциональные колонки

| Колонка | Описание | Тип данных | Пример | Когда используется |
|---------|----------|------------|--------|-------------------|
| `options` | Варианты ответа | Строка | `A: Вариант 1 \| B: Вариант 2` | Для SC, MC |
| `max_score` | Максимальный балл | Число | `10` | Для всех типов |
| `code` | Внутренний код задачи | Строка | `PY-VAR-001` | Опционально |
| `title` | Название задачи | Строка | `Переменные Python` | Опционально |
| `prompt` | Подсказка для ученика | Строка | `Переменная хранит значение` | Опционально |
| `input_link` | Ссылка на входные данные | URL | `https://example.com/data` | Опционально |
| `accepted_answers` | Допустимые варианты ответа | Строка | `8 \| 28 \| двадцать восемь` | Для SA, SA_COM |

### Формат данных в колонках

#### Колонка `type`

Допустимые значения:
- `SC` - Single Choice (одиночный выбор)
- `MC` - Multiple Choice (множественный выбор)
- `SA` - Short Answer (короткий ответ)
- `SA_COM` - Short Answer with Comments (короткий ответ с комментариями)
- `TA` - Text Answer (развернутый ответ)

#### Колонка `options` (для SC/MC)

Формат: `ID: Текст варианта | ID: Текст варианта`

**Пример:**
```
A: Именованная область памяти для хранения данных | B: Функция для вывода данных | C: Тип данных | D: Оператор
```

**Правила:**
- Каждый вариант отделяется символом `|`
- Формат: `ID: Текст`
- ID должен быть уникальным в рамках задачи (обычно A, B, C, D...)
- Минимум 2 варианта для SC, минимум 2 для MC

#### Колонка `correct_answer`

**Для SC (Single Choice):**
- Один вариант: `A`

**Для MC (Multiple Choice):**
- Несколько вариантов через запятую: `A,B,C` или `A, B, C`

**Для SA/SA_COM (Short Answer):**
- Точное значение: `8`
- Или несколько допустимых через `|`: `8 | 28 | двадцать восемь`

**Для TA (Text Answer):**
- Можно оставить пустым или указать `Нет правильного ответа` (требует ручной проверки)

#### Колонка `accepted_answers` (для SA/SA_COM)

Формат: `вариант1 | вариант2 | вариант3`

**Пример:**
```
8 | 28 | двадцать восемь
```

**С баллами:**
```
вариант1:10 | вариант2:5 | вариант3:3
```

#### Колонка `max_score`

- Целое число
- Если не указано, используется значение по умолчанию:
  - Для SA/SA_COM: `10` (из настройки `DEFAULT_POINTS_SHORT_ANSWER`)
  - Для остальных типов: `10`

### Пример структуры таблицы

| external_uid | type | stem | options | correct_answer | max_score | code | title | prompt | input_link |
|--------------|------|------|---------|----------------|-----------|------|-------|--------|------------|
| TASK-SC-001 | SC | Что такое переменная? | A: Область памяти \| B: Функция | A | 10 | PY-VAR-001 | Переменные | Переменная хранит значение | |
| TASK-MC-001 | MC | Выберите неизменяемые типы | A: list \| B: tuple \| C: str | B,C | 15 | PY-IMMUT-001 | Неизменяемые типы | | |
| TASK-SA-001 | SA | Сколько байт в int? | | 8 | 10 | PY-INT-001 | Размер int | | |

### Автоматический маппинг колонок

Если `column_mapping` не указан в запросе, система автоматически определяет колонки по стандартным названиям:

| Название колонки (регистронезависимо) | Маппится на поле |
|--------------------------------------|------------------|
| `external_uid`, `uid`, `id`, `код` | `external_uid` |
| `type`, `тип`, `task_type` | `type` |
| `stem`, `question`, `вопрос`, `задача` | `stem` |
| `options`, `варианты`, `answers` | `options` |
| `correct_answer`, `correct`, `правильный`, `ответ` | `correct_answer` |
| `max_score`, `score`, `балл`, `баллы` | `max_score` |
| `code`, `код` | `code` |
| `title`, `название` | `title` |
| `prompt`, `подсказка` | `prompt` |
| `input_link`, `ссылка`, `link` | `input_link` |
| `accepted_answers`, `принятые` | `accepted_answers` |

---

## Порядок импорта

### Шаг 1: Подготовка Google Sheets таблицы

1. Создайте новую таблицу в Google Sheets
2. Заполните первую строку заголовками колонок
3. Заполните данные задач начиная со второй строки
4. Убедитесь, что:
   - Все обязательные колонки заполнены
   - Формат данных соответствует требованиям
   - `external_uid` уникальны для каждой задачи

### Шаг 2: Настройка доступа

1. Получите Service Account JSON-файл из Google Cloud Console
2. Разместите файл в папке `secrets/` проекта
3. Предоставьте Service Account доступ к таблице Google Sheets:
   - Откройте таблицу
   - Нажмите "Настройки доступа" (Share)
   - Добавьте email из Service Account (например, `qsm-importer@gscapi-390409.iam.gserviceaccount.com`)
   - Дайте права "Читатель" (Viewer)

### Шаг 3: Получение идентификаторов

1. **Spreadsheet ID:**
   - Из URL: `https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit`
   - Или скопируйте весь URL

2. **Название листа:**
   - Обычно `Лист1` (Sheet1) по умолчанию
   - Или укажите точное название листа

3. **Коды курса и сложности:**
   - Получите через эндпойнт `GET /api/v1/meta/tasks`
   - Или используйте существующие коды из БД

### Шаг 4: Тестовый импорт (dry_run)

Рекомендуется сначала выполнить импорт в режиме `dry_run: true` для проверки данных:

```json
{
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/YOUR_ID/edit",
  "sheet_name": "Лист1",
  "course_code": "PY",
  "difficulty_code": "NORMAL",
  "dry_run": true
}
```

Проверьте:
- Количество обработанных строк (`total_rows`)
- Количество ошибок (`errors`)
- Детали ошибок (если есть)

### Шаг 5: Реальный импорт

После успешной проверки выполните реальный импорт:

```json
{
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/YOUR_ID/edit",
  "sheet_name": "Лист1",
  "course_code": "PY",
  "difficulty_code": "NORMAL",
  "dry_run": false
}
```

### Шаг 6: Проверка результатов

1. Проверьте ответ API:
   - `imported` - количество импортированных задач
   - `updated` - количество обновленных задач
   - `errors` - список ошибок (если есть)

2. Проверьте задачи в БД:
   - Используйте `GET /api/v1/tasks/by-external/{external_uid}`
   - Или `GET /api/v1/tasks/by-course/{course_id}`

---

## API Эндпойнты

### POST /api/v1/tasks/import/google-sheets

Импортирует задачи из Google Sheets.

#### Аутентификация

Требуется API ключ в query-параметре:
```
?api_key=your-api-key
```

#### Запрос

**URL:** `POST /api/v1/tasks/import/google-sheets?api_key={api_key}`

**Content-Type:** `application/json`

**Тело запроса:**

```typescript
{
  // Обязательные поля
  spreadsheet_url: string;  // URL таблицы или spreadsheet_id
  
  // Опциональные поля
  sheet_name?: string;      // Название листа (по умолчанию из настроек или "Лист1")
  column_mapping?: {        // Кастомный маппинг колонок
    [column_name: string]: string;  // название_колонки -> поле_задачи
  };
  
  // Курс и сложность (один из вариантов обязателен)
  course_code?: string;     // Код курса (courses.course_uid)
  course_id?: number;        // ID курса (если указан, используется вместо course_code)
  difficulty_code?: string;  // Код сложности (difficulties.code)
  difficulty_id?: number;    // ID сложности (если указан, используется вместо difficulty_code)
  
  // Режим работы
  dry_run?: boolean;        // true = только валидация, false = реальный импорт (по умолчанию false)
}
```

#### Примеры запросов

**Минимальный запрос:**
```json
{
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit",
  "course_code": "PY",
  "difficulty_code": "NORMAL"
}
```

**С указанием листа:**
```json
{
  "spreadsheet_url": "1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk",
  "sheet_name": "Лист1",
  "course_code": "PY",
  "difficulty_code": "NORMAL",
  "dry_run": true
}
```

**С кастомным маппингом:**
```json
{
  "spreadsheet_url": "1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk",
  "column_mapping": {
    "external_uid": "ID",
    "type": "Тип",
    "stem": "Вопрос",
    "options": "Варианты",
    "correct_answer": "Правильный ответ"
  },
  "course_code": "PY",
  "difficulty_code": "NORMAL"
}
```

#### Ответ

**Успешный ответ (200 OK):**

```typescript
{
  imported: number;         // Количество успешно импортированных задач
  updated: number;          // Количество обновленных задач (если external_uid уже существует)
  errors: Array<{           // Список ошибок
    row_index: number;      // Номер строки в таблице (начиная с 1, не считая заголовок)
    external_uid?: string;  // external_uid задачи (если удалось извлечь)
    error: string;          // Текст ошибки
  }>;
  total_rows: number;       // Общее количество обработанных строк (без заголовка)
}
```

**Пример успешного ответа:**
```json
{
  "imported": 10,
  "updated": 0,
  "errors": [],
  "total_rows": 10
}
```

**Пример ответа с ошибками:**
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
      "row_index": 7,
      "external_uid": null,
      "error": "Ошибка парсинга: Обязательное поле 'external_uid' (колонка 'external_uid') пустое"
    }
  ],
  "total_rows": 10
}
```

#### Коды ошибок

| Код | Описание | Причина |
|-----|----------|---------|
| 400 | Bad Request | Неверные параметры запроса (отсутствует course_id/course_code или difficulty_id/difficulty_code) |
| 403 | Forbidden | Неверный или отсутствующий API ключ |
| 404 | Not Found | Курс или уровень сложности не найден |
| 500 | Internal Server Error | Ошибка при чтении Google Sheets или обработке данных |

#### Типичные ошибки

**400 Bad Request:**
```json
{
  "detail": "Необходимо указать course_id или course_code"
}
```

**404 Not Found:**
```json
{
  "detail": "Курс с кодом 'INVALID-CODE' не найден"
}
```

**500 Internal Server Error:**
```json
{
  "detail": "Ошибка при чтении Google Sheet: <HttpError 403 when requesting ... returned \"The caller does not have permission\">"
```

---

## Примеры использования

### Пример 1: Базовый импорт

```javascript
const response = await fetch(
  'http://localhost:8000/api/v1/tasks/import/google-sheets?api_key=bot-key-1',
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      spreadsheet_url: 'https://docs.google.com/spreadsheets/d/1NbsaFMkDWGqzGTSi9Y1lG4THj8fiFty6u7CL9NLx8xk/edit',
      sheet_name: 'Лист1',
      course_code: 'PY',
      difficulty_code: 'NORMAL',
      dry_run: false,
    }),
  }
);

const result = await response.json();
console.log(`Импортировано: ${result.imported}, Ошибок: ${result.errors.length}`);
```

### Пример 2: Импорт с обработкой ошибок

```javascript
async function importTasks(spreadsheetUrl, courseCode, difficultyCode) {
  try {
    const response = await fetch(
      `http://localhost:8000/api/v1/tasks/import/google-sheets?api_key=bot-key-1`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          spreadsheet_url: spreadsheetUrl,
          course_code: courseCode,
          difficulty_code: difficultyCode,
          dry_run: false,
        }),
      }
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    const result = await response.json();

    if (result.errors.length > 0) {
      console.warn('Импорт завершен с ошибками:');
      result.errors.forEach((error) => {
        console.warn(`  Строка ${error.row_index}: ${error.error}`);
      });
    }

    return {
      success: true,
      imported: result.imported,
      updated: result.updated,
      errors: result.errors,
    };
  } catch (error) {
    console.error('Ошибка импорта:', error.message);
    return {
      success: false,
      error: error.message,
    };
  }
}
```

### Пример 3: Предварительная проверка (dry_run)

```javascript
async function validateImport(spreadsheetUrl, courseCode, difficultyCode) {
  const response = await fetch(
    `http://localhost:8000/api/v1/tasks/import/google-sheets?api_key=bot-key-1`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        spreadsheet_url: spreadsheetUrl,
        course_code: courseCode,
        difficulty_code: difficultyCode,
        dry_run: true, // Только валидация
      }),
    }
  );

  const result = await response.json();

  if (result.errors.length === 0) {
    console.log(`✅ Все ${result.total_rows} задач прошли валидацию`);
    return true;
  } else {
    console.log(`❌ Найдено ${result.errors.length} ошибок из ${result.total_rows} задач`);
    result.errors.forEach((error) => {
      console.log(`  Строка ${error.row_index}: ${error.error}`);
    });
    return false;
  }
}
```

---

## Обработка ошибок

### Типы ошибок

1. **Ошибки валидации данных:**
   - Неверный формат типа задачи
   - Отсутствуют обязательные поля
   - Неверный формат вариантов ответа
   - Несоответствие правильных ответов вариантам

2. **Ошибки доступа:**
   - Service Account не имеет доступа к таблице
   - Неверный spreadsheet_id
   - Лист не найден

3. **Ошибки ссылочной целостности:**
   - Курс не найден
   - Уровень сложности не найден

### Рекомендации по обработке

1. **Всегда используйте dry_run для первого импорта**
2. **Проверяйте массив `errors` в ответе**
3. **Логируйте ошибки для отладки**
4. **Предоставляйте пользователю понятные сообщения об ошибках**

---

## Часто задаваемые вопросы

### Q: Как получить список доступных курсов и уровней сложности?

**A:** Используйте эндпойнт `GET /api/v1/meta/tasks`:

```javascript
const response = await fetch('http://localhost:8000/api/v1/meta/tasks?api_key=bot-key-1');
const meta = await response.json();
console.log('Курсы:', meta.courses);
console.log('Уровни сложности:', meta.difficulties);
```

**Пример ответа:**
```json
{
  "difficulties": [
    { "id": 1, "code": "THEORY", "name_ru": "Теория" },
    { "id": 3, "code": "NORMAL", "name_ru": "Средняя" }
  ],
  "courses": [
    { "id": 1, "course_uid": "PY", "title": "Основы Python" }
  ],
  "task_types": ["SC", "MC", "SA", "SA_COM", "TA"],
  "version": 1
}
```

### Q: Можно ли импортировать задачи без указания course_code?

**A:** Нет, необходимо указать либо `course_code`, либо `course_id`. Аналогично для `difficulty_code`/`difficulty_id`.

### Q: Что происходит, если external_uid уже существует?

**A:** Задача будет обновлена (в ответе `updated` увеличится на 1, `imported` останется без изменений). Обновляются все поля: `task_content`, `solution_rules`, `max_score`, `course_id`, `difficulty_id`.

### Q: Как проверить, существует ли задача с определенным external_uid?

**A:** Используйте эндпойнт `GET /api/v1/tasks/by-external/{external_uid}`:

```javascript
const response = await fetch(
  `http://localhost:8000/api/v1/tasks/by-external/TASK-SC-001?api_key=bot-key-1`
);
if (response.status === 200) {
  const task = await response.json();
  console.log('Задача найдена:', task.id);
} else if (response.status === 404) {
  console.log('Задача не найдена');
}
```

### Q: Можно ли импортировать задачи разных типов в одном запросе?

**A:** Да, в одной таблице могут быть задачи разных типов (SC, MC, SA, SA_COM, TA).

### Q: Как указать кастомные названия колонок?

**A:** Используйте параметр `column_mapping`:

```json
{
  "column_mapping": {
    "ID задачи": "external_uid",
    "Тип": "type",
    "Вопрос": "stem"
  }
}
```

### Q: Что делать, если Service Account не имеет доступа к таблице?

**A:** 
1. Откройте таблицу в Google Sheets
2. Нажмите "Настройки доступа" (Share)
3. Добавьте email из Service Account (указан в JSON-файле, поле `client_email`)
4. Дайте права "Читатель" (Viewer)

### Q: Можно ли импортировать задачи без вариантов ответа для SC/MC?

**A:** Нет, для SC и MC обязательно указать минимум 2 варианта в колонке `options`.

### Q: Какой формат правильного ответа для MC?

**A:** Несколько вариантов через запятую: `A,B,C` или `A, B, C` (пробелы не важны).

### Q: Что означает dry_run?

**A:** Режим проверки без сохранения. Все данные валидируются, но не сохраняются в БД. Полезно для проверки перед реальным импортом.

---

## Дополнительные ресурсы

- [Примеры тестовых данных](../docs/test-google-sheets-content.md)
- [Результаты smoke tests](../docs/smoke-tests-gsheets-results.md)
- [Миграция функциональности](../docs/gsheets-import-migration.md)

---

**Версия документа:** 1.0  
**Последнее обновление:** 2026-01-17
