# review-gate: tsk-235 — окно благодати на ротацию refresh-токена

**Решение: ПРИНЯТО**

## Контекст

Задача `tsk-235` (P1, security-critical, auth). Полная диагностика — в
`D:\Work\Root\tasks\tsk-235-spw-proshel-material-ne-udalos-sohranit.md`
(первопричина найдена ранее, не переисследовалась). Симптом: SPW «Прошёл
материал» → «Не удалось сохранить». Первопричина — гонка ротации
refresh-токена между двумя вкладками SPW, делящими одну refresh-cookie:
вкладка A обновляется первой (токен X отозван, выдан Y), вкладка B шлёт тот
же X → мгновенный 401 без окна благодати.

Решение оператора (принято 2026-07-24, зафиксировано в задаче): окно
благодати на ротацию — отозванный < N сек назад токен с известным
преемником не даёт 401, а возвращает ТУ ЖЕ пару токенов преемника
(идемпотентно). Replay после окна — отзыв всей цепочки сессий (детект кражи
не ослаблен).

## Изменения

- `app/db/migrations/versions/20260724_010000_tsk235_session_replaced_by.py`
  — `user_session.replaced_by_session_id` (self-ref UUID FK, nullable, ON
  DELETE SET NULL) + partial index.
- `app/models/user_session.py` — поле в ORM-модели.
- `app/services/auth/session_service.py` — `refresh_session`:
  - `.with_for_update()` на строке сессии — сериализует подлинно
    одновременные запросы (не только последовательные повторы).
  - Redis-кэш (`session_refresh_grace:*`, TTL 25с) идемпотентной пары
    токенов преемника — по паттерну `link_token_service` (fail-open при
    недоступности Redis).
  - Окно благодати 20 сек (обоснование в коде).
  - Replay вне окна → `revoke_all_sessions` + явный `db.commit()` внутри
    сервиса (см. «Находки в процессе» ниже — без этого коммита отзыв
    откатывался бы вместе с 401-транзакцией).
  - Revoked без `replaced_by_session_id` (обычный logout) → поведение не
    изменилось, цепочка не трогается.
- `app/api/v1/auth/session.py` — роутер передаёт Redis-клиент (паттерн
  `link_token.py`).
- `tests/test_session_refresh_grace_window_tsk235.py` — 4 юнит-теста логики
  веток (общая транзакция, последовательные вызовы).
- `tests/test_session_refresh_http_race_tsk235.py` — 1 тест подлинной HTTP-
  параллельности (5 конкурентных запросов, отдельные соединения к БД,
  `no_tx_isolation`, по образцу `test_attempts_limit_race_tsk273.py`).
- `tests/conftest.py` — регистрация нового `no_tx_isolation`-модуля в
  `SELF_MANAGED_CONNECTION_MODULES`.

## Находки в процессе (самостоятельно найдены и исправлены до этого отчёта)

1. **[LOGIC, было бы S1]** Первая версия `refresh_session` возвращала
   `None` из ветки chain-revoke ДО commit — роутер коммитит только на
   успешном пути, а при 401 просто `raise HTTPException` без commit.
   Отзыв всей цепочки (security-фикс) молча откатывался бы при закрытии
   сессии. Исправлено: явный `await db.commit()` внутри сервиса перед
   `return None` в этой ветке. Причина, почему не осталось незамеченным —
   не было теста на это до ручного прослеживания потока управления;
   `test_replay_after_grace_window_revokes_chain` покрывает результат, но
   не сам механизм коммита напрямую.
2. **[LOGIC, было бы S2]** Первая версия закрывала гонку только для
   ПОСЛЕДОВАТЕЛЬНЫХ повторных запросов (второй приходит после commit
   первого). Подлинно одновременные запросы читали `revoked_at IS NULL` до
   commit друг друга и оба уходили в ветку ротации — цепочка размножалась
   бы (ровно то, что задача требовала не допустить). Обнаружено при
   проектировании HTTP-теста на реальной параллельности; закрыто
   `.with_for_update()`. Намеренно проверено: тест
   `test_concurrent_http_refresh_all_succeed_one_successor` красный без
   `with_for_update()`, зелёный с ним (revert-and-verify).
3. **[LOGIC, S3]** Ветка «токен ещё активен» первой версии обрабатывала
   `refresh_expires_at IS NULL` как «не истёк» (implicit allow) — расходится
   с исходным SQL-фильтром `refresh_expires_at > now()`, где NULL исключал
   строку. `create_session` всегда проставляет это поле (NULL в проде не
   встречается), но семантика восстановлена для точного соответствия
   прежнему поведению.

## Проверка по 12 измерениям

1. **Соответствие целям** — все пункты решения оператора покрыты: окно
   благодати, идемпотентный повтор без размножения цепочки, детект кражи
   сохранён, поведение logout не тронуто. DRIFT нет.
2. **Корректность** — edge cases (unknown token, expired, revoked без
   преемника, revoked с преемником в/вне окна, NULL `refresh_expires_at`,
   подлинная и последовательная гонка) покрыты тестами.
3. **DB/миграции** — nullable колонка, self-ref FK `ON DELETE SET NULL`,
   partial index. `upgrade`/`downgrade` прогнаны на dev (round-trip чист).
   Прод: `user_session` — 410 строк (read-only проверено), миграция
   тривиальна, без риска блокировок.
4. **Безопасность/секреты** — секретов не добавлено; Redis URL — из
   существующих `Settings`; JSON-пейлоад кэша не содержит user-controlled
   строк, кроме уже-случайных токенов; IDOR — не применимо (нет
   user-supplied ID, только hash lookup).
5. **Тесты** — без моков на критичном пути: реальный Postgres (dev) +
   реальный Redis (localhost:6379/2, по образцу
   `test_y5_guest_endpoints.py`) + подлинная HTTP-конкурентность (не только
   общая транзакция теста). 18/18 новых+смежных тестов зелёные; полный
   набор — 937 passed + 1 (guard-тест, был красным до регистрации нового
   `no_tx_isolation`-модуля, затем зелёным) = 938 passed, 10 skipped, 0
   failed.
6. **Docs/Config drift** — не применимо (внутренняя логика, не API-форма).
7. **Phase integrity** — scope staged-набора ограничен tsk-235 (проверено
   `git status`: прочие untracked файлы в репозитории — из несвязанных
   параллельных задач, не затронуты).
8–9. **Data/domain completeness** — не применимо.
10. **Date/Time** — все сравнения через существующий tz-aware `_now()`;
    `_now() - old.revoked_at` — оба aware (`TIMESTAMPTZ` колонка), проверено
    эмпирически в тестах.
11. **Cross-project memory sync** — `ContentBackbone/docs/cross-project/
    contracts/lms-db-schema.md` (секция `user_session`) + `CHANGELOG.md` +
    `STATE.md` обновлены и закоммичены (`06e8c3e` в ContentBackbone) ДО
    этого отчёта.
12. **Public API Contract Sync** — `/auth/session/refresh`: URL/метод/
    request/response schema/коды не менялись — только внутреннее поведение
    при повторном refresh уже отозванным токеном. OpenAPI-бэксинк не
    требуется. Hardcoded prod-URL sweep (`grep` по `app/services/`,
    `app/core/`) — чисто (совпадения — только pre-existing dev-fallback в
    `config.py`, не из этого диффа).

## Operator handoff

- Деплой на прод + живой прогон гонки — ветвь А по стоячей авторизации
  оператора (operator-handoff-rules, `tsk-359`): код готов, review-gate
  чист, задача явно требует деплоя (это НЕ откладывается на оператора) —
  выполняется в этой же сессии, следом за этим отчётом.

## Итог

Блокирующих находок нет. Все находки, поднятые в процессе самостоятельного
ревью, исправлены до этого отчёта (не осталось «отложенных на потом»).
**ПРИНЯТО.**
