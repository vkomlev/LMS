# Review: убран мягкий skip в тесте blocked_limit (этап 3.8.1)

**Дата:** 2026-02-27  
**Контекст:** [P2] В `test_filter_request_type_and_auto_create_blocked_limit` при `created=False` тест возвращал True (skip) и не проверял фильтр и context.

## Изменения

- Удалён ранний `return True` при `not request_id or not created`.
- Skip только при отсутствии `request_id` (нет данных).
- При любом полученном `request_id` выполняются все проверки: GET `?request_type=blocked_limit`, заявка в списке, `auto_created`, `context` с `attempts_used`/`attempts_limit_effective`.
- В сообщении PASS выводится `created=True/False` для прозрачности.

Полный diff: [reviews/2026-02-27-stage381-test-no-soft-skip.diff](2026-02-27-stage381-test-no-soft-skip.diff)
