# Правки bulk-upsert по ручному ревью (P1/P2)

**Дата:** 2026-03-23  
**Контекст:** Ответ на findings: неатомарный batch, отсутствие per-item validation errors (422 на весь запрос), N+1 курсов, пробелы в тестах.

## Изменения поведения

1. **Транзакция записи:** все `create`/`update` в фазе БД с `commit=False`, один `await db.commit()` в конце; при любом исключении — `rollback`, все строки batch-записи с `status=error`, `errors[]` с пояснением, `processed=0`.
2. **Валидация:** `MaterialsBulkUpsertRequest.items` — `List[Dict[str, Any]]`; Pydantic только проверяет «массив объектов». Поля и `content` по `type` валидируются в сервисе через `MaterialsBulkUpsertItem.model_validate` → per-item `error` / `error_type=validation` без обрыва всего запроса.
3. **Курсы:** `CoursesRepository.filter_existing_ids` — один `SELECT id FROM courses WHERE id IN (...)`.
4. **BaseRepository:** `create`/`update` с опциональным `commit: bool = True` (по умолчанию без изменений для остального кода).

## Тесты

- Смешанный batch: одна валидная + одна с неверным `content`.
- Атомарность: `patch` на второй `MaterialsRepository.create` → rollback, в БД 0 строк по обоим `external_uid`.

## Diff

См. `reviews/2026-03-23-bulk-upsert-atomic-per-item-validation.diff`
