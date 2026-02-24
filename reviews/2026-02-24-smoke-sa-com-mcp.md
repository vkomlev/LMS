# Smoke-тесты контракта SA_COM/SA+COM (CURL + MCP + логи)

**Дата:** 2026-02-24

## Правила

- **Smoke-testing:** CURL для API, валидация по БД (MCP PostgreSQL) и логам `logs/app.log`.
- **Database:** данные для тестов берутся из БД через MCP (read-only).

## Тестовые данные (MCP)

- Незавершённая попытка: `SELECT id FROM attempts WHERE finished_at IS NULL` → **attempt_id=1**.
- Задача типа SA_COM: `SELECT id, task_content->>'type' FROM tasks WHERE task_content->>'type' = 'SA_COM'` → **task_id=30** (course_id=10).

## Результаты проверок

| Сценарий | Ожидание | Факт | Примечание |
|----------|----------|------|------------|
| SA_COM + response.value | 200 | **200** | Ответ: attempt_id, results, total_score_delta. В БД создана запись в task_results с answer_json.type=SA_COM, response.value=42. |
| SA+COM алиас + response.value | 200 | 422 на текущем процессе | Схема исправлена: `AnswerTypeInput = Union[TaskType, Literal["SA+COM"]]` + model_validator нормализует в SA_COM. Парсинг в Python проходит. После перезапуска приложения ожидается 200. |
| Неверный тип (SA_PLUS_COM) | 422 | **422** | detail с literal_error по полю type. |

## Логи

- В `logs/app.log`: при 422 для `/api/v1/attempts/.../answers` пишется `Validation error at ...` и доп. лог с контекстом по answer/type (observability).
- Успешные POST 200: INSERT в task_results с answer_json в формате канонического контракта.

## БД (MCP)

После успешного POST с SA_COM проверено:

```sql
SELECT attempt_id, task_id, score, max_score, answer_json
FROM task_results WHERE attempt_id = 1 ORDER BY task_id;
```

Результат: записи с `answer_json.type = "SA_COM"`, `answer_json.response.value = "42"`.

## Правка схемы для SA+COM

В `app/schemas/checking.py`:

- Тип поля `type`: `AnswerTypeInput = Union[TaskType, Literal["SA+COM"]]`.
- `model_validator(mode="after")` нормализует "SA+COM" → "SA_COM" и пишет deprecation в лог.

Полный diff: [reviews/2026-02-24-smoke-sa-com-mcp.diff](2026-02-24-smoke-sa-com-mcp.diff)
