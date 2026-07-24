# tsk-403 — Диагностика периодических 502 фоновому опросу teacher-бота

**Дата:** 2026-07-24
**Скилл:** /fastapi-api-developer
**Тип:** диагностика по реальным логам прода (read-only), фикс не применялся

## Симптом

teacher-бот TG_LMS фоновым опросом (`python-httpx`) обращается к LMS API
`GET /api/v1/users/?role=teacher` и ~1–2 раза в сутки получает **502 Bad Gateway**.
Обнаружено плановой проверкой tsk-277 (24.07.2026).

## Первопричина (подтверждена логами, не кодом)

**502 = окно перезапуска однопроцессного uvicorn при деплое/рестарте `lms.service`.**

Доказательная цепочка (хост `lms-spw-vds`, 5.42.102.20):

1. **nginx access.log** — за сутки ровно 2 строки 502, обе `23/Jul/2026:08:53:39`,
   клиент `72.56.247.22` (это хост tg-lms-vds, teacher-поллер), эндпоинт
   `/api/v1/users/?role=teacher`, agent `python-httpx/0.28.1`. Сегодня — 0.
   Совпадает с симптомом «1–2 раза в сутки».

2. **nginx error.log.1** — в тот же момент:
   ```
   2026/07/23 08:53:39 [error] connect() failed (111: Unknown error)
   while connecting to upstream, client: 72.56.247.22,
   upstream: "http://127.0.0.1:8000/api/v1/users/..."
   ```
   `111` = ECONNREFUSED: на порту 8000 никто не слушал (не таймаут, не «no live upstreams»).

3. **journalctl -u lms.service** — в ту же секунду `08:53:39`:
   `Deactivated successfully` → `Stopped` → `Started`. Ключевое —
   **«Deactivated successfully»** (чистая остановка), а не «Failed»/«killed by signal».
   Таких чистых перезапусков — несколько в день (21-го: 10:50/10:58/14:12;
   23-го: 05:43/07:55/08:53/13:37; 24-го: 05:36/05:52) — по числу деплоев/рестартов.

4. **/var/log/lms/app.log** — вокруг рестарта чистое
   `Shutting down` → `Finished server process` → `Application startup complete`.
   Ни трейсбека, ни ошибки.

5. **journalctl -k** — OOM/`killed process` отсутствуют.

### Механизм

`lms.service` запущен одним процессом без воркеров:
```
ExecStart=.../uvicorn app.api.main:app --host 127.0.0.1 --port 8000
Type=simple, Restart=on-failure, RestartSec=5
```
При каждом `systemctl restart` (деплой) старый процесс отпускает сокет :8000,
новый ещё не забиндил — окно ~1–2 сек, когда nginx получает `connect refused` → 502.
Частый опрос teacher-поллера (интервал 30 сек) иногда попадает ровно в это окно.

### Что первопричиной НЕ является

Крах приложения, OOM, таймаут gunicorn/uvicorn, нехватка воркеров, медленный
эндпоинт, перегрузка БД — по логам исключены. Строки `SSL_do_handshake() failed
(bad key share)` в error.log — интернет-сканеры на 443, к 502 отношения не имеют.

## Реальный ущерб (низкий)

Поллер устойчив: `poll_pending_reviews` ловит `APIClientError` (обёртка 502) →
`logger.warning` → `sleep(interval)` → следующий тик через 30 сек
(`src/bots/teacher/poller.py:324`). Краха и потери данных нет; ущерб =
одна строка warning в логе + задержка обнаружения новых проверок ≤30 сек.
На стороне клиента (`src/common/api_client.py:_request`) ретрая на транзиентные 5xx
сейчас НЕТ — 502 сразу пробрасывается как `APIClientError(status_code=502)`.

## Предлагаемые правки (минимальные, порядок по цене/риску)

### A. Клиентский мягкий ретрай в TG_LMS (низкий риск, без прод-изменения LMS)
В `AsyncAPIClient._request` (или в поллерах) — 1 повтор на транзиентных 5xx
(502/503/504) с короткой задержкой (0.5–1 сек). Полностью прячет редкое окно
рестарта от логики ботов и убирает шум в логе. Это и есть «Слой 1»-подход из задачи.

### B. Zero-downtime рестарт на стороне LMS (настоящее лечение, прод-деплой)
systemd socket activation: `lms.socket` держит слушающий сокет :8000, отдаёт fd
uvicorn'у (`--fd`); во время рестарта соединения встают в очередь, а не отклоняются.
Тогда 502 при деплое исчезают в принципе. Больше по объёму, трогает прод-деплой →
только с согласованием оператора.

### Рекомендация
**A сейчас** (дёшево, снимает наблюдаемый симптом), **B — как долгосрочное
улучшение деплоя** при желании убрать 502 у источника. C = A+B.

## Проверочные команды (read-only, воспроизведение)
```bash
ssh lms-spw-vds 'grep " 502 " /var/log/nginx/access.log*'
ssh lms-spw-vds 'grep -E "connect\(\) failed|upstream" /var/log/nginx/error.log*'
ssh lms-spw-vds 'journalctl -u lms.service --since "-3 days" | grep -E "Started|Stopped|Deactivated|Failed"'
```

## Применено (решение оператора: C = A + B)

### A — клиентский ретрай (TG_LMS, `src/common/api_client.py`)
Мягкий повтор в `AsyncAPIClient._request`: 1 повтор с задержкой 0.5 c на
транзиентных 5xx (502/503/504) и `httpx.RequestError` **только для идемпотентных**
методов (GET/HEAD/OPTIONS). POST/PUT/DELETE не повторяются (не задваивать записи).
Терминальные ошибки по-прежнему оборачиваются в `APIClientError` — контракт сохранён.
Проверено 5 юнит-сценариями на `httpx.MockTransport`: GET 502→200 повтор+успех;
POST 502 без повтора; 422 без повтора; постоянный 502 GET останавливается на лимите;
ConnectError GET повтор+обёртка. Все прошли.

### B — zero-downtime рестарт (прод LMS, systemd socket activation)
Добавлены на прод `lms.socket` (держит `127.0.0.1:8000`) + drop-in
`lms.service.d/socket.conf` (uvicorn слушает унаследованный fd: `--fd 3`).
Базовый `lms.service` не изменён. Версионировано в репо: `deploy/vps/lms.socket`,
`deploy/vps/lms.service.d/socket.conf`, инструкция в `deploy/vps/README.md`.
deploy.sh/rollback.sh не менялись — оба делают `systemctl restart lms` (сервис),
сокет переживает рестарт.

**Живая проверка (24.07.2026 12:15 UTC):** 240 запросов `/health` через nginx
каждые 50 мс во время `systemctl restart lms.service` → **240/240 = 200**, ноль 502.
Процесс идёт с `--fd 3`, health 200 локально и через nginx. До правки то же окно
рестарта давало connect refused → 502.

### Откат B (если понадобится)
```bash
ssh lms-spw-vds 'rm /etc/systemd/system/lms.socket \
  /etc/systemd/system/lms.service.d/socket.conf; \
  systemctl daemon-reload; systemctl disable --now lms.socket; \
  systemctl restart lms'
```

## Риски / follow-up
- Правка A — на стороне TG_LMS (не LMS); ретрай ставить только на идемпотентные
  GET-поллеры или на весь `_request` с ограничением на безопасные методы/коды.
- Правка B меняет прод-механику запуска — требует теста рестарта и отката.
- api_key светится в query string в логах nginx (видно в access.log) — отдельный
  мелкий security-follow-up, вне scope tsk-403.
