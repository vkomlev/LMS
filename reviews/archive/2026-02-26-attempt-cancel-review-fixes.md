# Правки по ревью: этап 3.5 (cancel) — answers/finish, статистика, HTTP-тест

**Дата:** 2026-02-26

**Контекст:** Устранение замечаний по этапу 3.5: блокировка answers/finish для отменённой попытки, исключение cancelled из всех статистических агрегатов, HTTP-тест «cancel → answers 400».

**Изменения:**

1. **POST /attempts/{id}/answers** (`app/api/v1/attempts.py`): добавлена проверка `attempt.cancelled_at is not None` — при отменённой попытке возвращается **400** с текстом «Попытка отменена. Нельзя отправлять ответы в отменённую попытку.»

2. **POST /attempts/{id}/finish** (`app/api/v1/attempts.py`): добавлена проверка `attempt.cancelled_at is not None` — при отменённой попытке возвращается **409** с текстом «Попытка отменена. Завершать можно только активную попытку.»

3. **Статистика** (`app/services/task_results_service.py`): во всех агрегатах учёт только попыток без отмены:
   - `get_stats_by_task`: `total_query` и `stats_query` — join с `Attempts` и условие `Attempts.cancelled_at.is_(None)`.
   - `get_stats_by_course`: `stats_query` — join с `Attempts`, `cancelled_at IS NULL`.
   - `get_stats_by_user`: `stats_query` — join с `Attempts`, `cancelled_at IS NULL`.
   Добавлен импорт `from app.models.attempts import Attempts`.

4. **Тесты** (`tests/test_attempt_cancel_stage35.py`):
   - Добавлен HTTP-тест `test_http_post_answers_after_cancel_returns_400`: создаётся попытка, отменяется, затем вызывается `POST /api/v1/attempts/{id}/answers` — ожидается **400** и наличие «отмен» в `detail`.
   - Исправлена ошибка в `test_cancel_active_returns_200_and_cancelled`: удалена лишняя строка `user_id = user_id.scalar()` (user_id уже int).

**Результат:** 7/7 тестов проходят.

**Полный diff:** [2026-02-26-attempt-cancel-review-fixes.diff](2026-02-26-attempt-cancel-review-fixes.diff)
