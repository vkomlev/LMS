# Реестр Ошибок Агентов

## 2026-03-03 — LE V1 stage 3.9: `due_at` str vs datetime (500 в help-request detail)

- Источник: реализация Cursor (fastapi-debugger), последующая проверка Codex.
- Симптом: `GET /api/v1/teacher/help-requests/{id}` возвращал 500.
- Ошибка: `TypeError: '<' not supported between instances of 'str' and 'datetime.datetime'`.

### Корневая причина

1. В `app/services/help_requests_service.py` для `due_at` использовался результат сырого SQL (`text(...)`), где в части окружений драйвер возвращал строку.
2. Код сравнивал `due_at < now` без нормализации типа.
3. В тестах не было сценария с `due_at` в строковом формате (контракт на тип не закреплён тестом).

### Почему ошибся Cursor

1. Предположение о стабильном типе `timestamptz` как `datetime` при чтении через `text(...)`.
2. Отсутствие защитной нормализации входного значения перед сравнением.
3. Неполный негативный тест-пакет для смешанных типов (datetime/string).

### Почему Codex не поймал ранее

1. Проверка фокусировалась на контракте/роутах и целевых P1/P2 этапа, без воспроизведения detail-сценария с проблемным `due_at`.
2. Не был выполнен отдельный аудит "сырой SQL + сравнение дат" по всем сервисам.
3. Не было runtime smoke именно на кейс `help_request_detail` с реальными данными, где `due_at` пришёл строкой.

### Что исправлено

1. Добавлена `_normalize_due_at(due_at)` в `help_requests_service`:
   - string -> `datetime.fromisoformat(...)` (+ timezone normalization),
   - naive datetime -> UTC-aware datetime.
2. В `list_help_requests` и `get_help_request_detail` сравнение делается только после нормализации.
3. В API-ответы передаётся нормализованный `due_at`.

### Остаточный риск

1. Похожие сравнения дат из `text(...)` остаются в:
   - `app/services/teacher_queue_service.py` (`due_at < now`),
   - местах с `claim_expires_at < now` при чтении через raw SQL.
2. При возврате строк в этих местах возможны аналогичные `TypeError`.

### Обязательные профилактические меры

1. Cursor (написание кода):
   - правило: перед сравнением дат из `text(...)` всегда нормализовать тип;
   - для timestamp-полей использовать общий helper нормализации (single source);
   - добавлять негативные тесты: date как строка, naive datetime, null.
2. Codex (проверка):
   - отдельный чек-лист ревью: "raw SQL -> типы -> сравнения datetime";
   - всегда делать минимум один runtime smoke на detail/list endpoint с датами;
   - требовать тест, который падает на старом коде и проходит на новом.

### Статус

- Статус: исправлено в `help_requests_service`, требует доработки по остаточным рискам.
- Связанные артефакты:
  - `reviews/2026-03-03-help-requests-due-at-str-datetime-fix.diff`
  - `reviews/2026-03-03-help-requests-due-at-str-datetime-fix.md`
