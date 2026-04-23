# Review: правки по замечаниям этапа 3.8 (teacher help-requests)

**Дата:** 2026-02-27

**Контекст:** Закрытие замечаний P1 (500→422, 403→404) и P2 (скипы не маскируют покрытие).

**Изменения:**

1. **[P1] 500 при невалидном `status` → 422**  
   В `app/api/v1/teacher_help_requests.py` параметр списка переименован в `status_filter` с `alias="status"`, чтобы не перекрывать импорт `fastapi.status`. При значении не из `("open","closed","all")` возвращается `422`.

2. **[P1] close несуществующей заявки → 404, а не 403**  
   В роутере `help_request_close` сначала вызывается `help_request_exists(db, request_id)`; при `False` — `404`. Затем проверка ACL и вызов `close_help_request`. В `app/services/help_requests_service.py` добавлена функция `help_request_exists(db, request_id)`.

3. **[P2] Скипы критичных сценариев не считаются проходом**  
   В `tests/test_teacher_help_requests_stage38.py` при скипе из-за отсутствия данных возвращается `False` вместо `True`: ACL 403 (чужой teacher); в **test_reply_creates_message_and_dedupe** — обе ветки «нет открытой заявки» и «нет teacher»; close_after_reply. Итог «Пройдено: X/6» перестаёт маскировать непрогнанные сценарии.

4. **Тест request-help и learning_events**  
   Проверка события `help_requested` переведена с «последние 2 события по student_id» на проверку по `event_id` заявки: `SELECT event_type FROM learning_events WHERE id = :event_id` и ожидание `help_requested`.

Полный diff: [2026-02-27-teacher-help-requests-stage38-review-fixes.diff](2026-02-27-teacher-help-requests-stage38-review-fixes.diff)
