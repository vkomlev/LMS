# Быстрый старт - Импорт задач из Google Sheets

Краткая инструкция для фронтенд-разработчиков.

---

## Минимальный пример

```javascript
// 1. Импорт задач
const response = await fetch(
  'http://localhost:8000/api/v1/tasks/import/google-sheets?api_key=bot-key-1',
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      spreadsheet_url: 'https://docs.google.com/spreadsheets/d/YOUR_ID/edit',
      sheet_name: 'Tasks',
      course_code: 'PY',
      difficulty_code: 'NORMAL',
      dry_run: false,
    }),
  }
);

const result = await response.json();
console.log(`Импортировано: ${result.imported}, Ошибок: ${result.errors.length}`);
```

---

## Обязательные поля запроса

- `spreadsheet_url` - URL таблицы или spreadsheet_id
- `difficulty_code` ИЛИ `difficulty_id` - уровень сложности

### Курс для заданий

- **Либо** `course_code` ИЛИ `course_id` - один курс на весь импорт
- **Либо** добавьте в таблицу колонку `course_uid` и заполняйте курс для каждой строки (курс на строке)

## Опциональные поля

- `sheet_name` - название листа (по умолчанию "Лист1")
- По умолчанию лист берётся из `GSHEETS_WORKSHEET_NAME` (теперь **`Tasks`**), если `sheet_name` не указан.
- `column_mapping` - кастомный маппинг колонок
- `dry_run` - режим проверки без сохранения (по умолчанию false)

---

## Формат ответа

```typescript
{
  imported: number;      // Импортировано задач
  updated: number;       // Обновлено задач
  errors: Array<{       // Ошибки
    row_index: number;
    external_uid?: string;
    error: string;
  }>;
  total_rows: number;   // Всего обработано строк
}
```

---

## Обязательные колонки в таблице

1. `external_uid` - уникальный ID задачи
2. `type` - тип задачи (SC, MC, SA, SA_COM, TA)
3. `stem` - формулировка вопроса
4. `correct_answer` - правильный ответ

## Формат вариантов ответа (для SC/MC)

```
A: Вариант 1 | B: Вариант 2 | C: Вариант 3
```

## Формат правильного ответа

- SC: `A`
- MC: `A,B,C`
- SA: `8` или `8 | 28`

---

## Полная документация

См. [import-api-documentation.md](./import-api-documentation.md)
