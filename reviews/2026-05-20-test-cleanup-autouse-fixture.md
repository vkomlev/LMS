# tsk-004 Этап 1.1 — autouse cleanup fixture + добор orphan'ов

**Дата:** 2026-05-20
**Скилл:** `/db-check` → `/claude-booster` (по запросу оператора)
**Связано:** [tsk-004 «Порядок в LMS»](https://github.com/vkomlev/work-root/blob/master/tasks/tsk-004-poryadok-v-lms.md), коммит первого этапа [LMS@29f5ead](https://github.com/vkomlev/LMS/commit/29f5ead)

## Контекст

После Этапа 1 (массовая очистка тестовых артефактов из `Learn.public`) оператор обнаружил, что в БД снова появились тестовые users. Расследование показало:
- 30 users `y6-{teacher|stud|meth|other|meth-acl}-...@example.com` (id 2348–2377) — один прогон `tests/test_y6_review_loop.py` (2026-05-20 11:17–11:19).
- 17 + 1 = 18 свежих `audit_event`, 1 orphan attempt — каскадные следы тестов.
- 5 materials и 20 tasks (id > baseline) — **легитимный WP-импорт контента**, не тесты.

## Корневая причина

В `tests/test_y6_review_loop.py:135-138` локальная функция `_cleanup` имела комментарий:

> users НЕ удаляем — audit_event имеет append-only trigger, и попытка cascade DELETE упадёт. Orphan users в test DB допустимы

Авторы не знали обхода `audit_event_no_modify`: `ALTER TABLE … DISABLE TRIGGER` в той же транзакции. Этот обход уже применён в `scripts/cleanup_test_artifacts.py` (Этап 1), но в тестах его не было. Паттерн распространился: 28 тест-файлов с `@example.com/.test` префиксами, многие коммитят без teardown.

Дополнительно: фикстура `db` в `tests/conftest.py` делает `rollback()` в teardown, но тесты внутри явно вызывают `await db.commit()` — поэтому rollback ни на что не влияет.

## Применённые правки

### 1. Session-scoped autouse cleanup-фикстура — `tests/conftest.py`

Добавлен `_cleanup_test_artifacts` (session-scope, autouse, sync с `asyncio.run` внутри). Логика:

**Setup:** запомнить `now()` PG-сервера (snapshot_ts).
**Teardown:**
- `UPDATE notifications SET modified_by = NULL` для test-users (FK `NO ACTION`)
- `ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_modify`
- `DELETE audit_event WHERE ts >= snapshot_ts AND (user_id IS NULL OR user_id NOT IN (2,3,142))`
- `DELETE magic_link WHERE created_at >= snapshot_ts AND (email IS NULL OR email ILIKE '%@example.%')`
- `DELETE guest_session WHERE created_at >= snapshot_ts` (CASCADE снимет `guest_attempt`)
- `DELETE users WHERE created_at >= snapshot_ts AND id NOT IN (2,3,142) AND (email IS NULL OR email ILIKE '%@example.%')`
  → каскад: user_roles, identity_link, user_session, attempts, task_results, learning_events, notifications, teacher_courses, user_courses, student_*, access_requests, help_requests + replies, messages.recipient, user_achievements, social_posts
- `ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_modify`

**Два слоя защиты от случайного удаления real users:**
1. Allow-list `_REAL_USER_IDS = (2, 3, 142)`
2. Фильтр email `ILIKE '%@example.%'` (real users имеют mail.ru/list.ru/gmail.com)

**Технические решения:**
- Snapshot — timestamp, а не `MAX(id)`: `guest_session.id` это UUID, `MAX(uuid)` не существует.
- Фикстура **sync** с `asyncio.run` внутри: pytest-asyncio 1.x не даёт прямо использовать `@pytest_asyncio.fixture(scope="session")` без явного session-scoped event loop. `asyncio.run` создаёт изолированный loop под snapshot/sweep.

### 2. Bug-fix в `scripts/cleanup_test_artifacts.py`

`DELETE FROM magic_link` использовал подзапрос `email IN (SELECT email FROM users WHERE id NOT IN real)`. После первого прогона test users снесены, подзапрос даёт пустоту, и `magic_link` с тестовыми email домена остаются навсегда. Добавлен прямой фильтр `email ILIKE '%@example.%'` (защита от sequence-эффекта повторного запуска).

После фикса дотерто 87 orphan magic_link.

### 3. Обновлён misleading-комментарий в `tests/test_y6_review_loop.py:_cleanup`

Старый текст «users НЕ удаляем — audit_event имеет append-only trigger» заменён на отсылку к session-scoped autouse фикстуре, которая берёт каскад на себя.

## Верификация

После полного прогона `pytest tests/test_y6_review_loop.py` фикстура сообщила:
```
test-artifacts sweep snapshot_ts: 2026-05-20 17:30:48.089701+00:00
test-artifacts sweep done: users=14 audit_event=9 magic_link=0 guest_session=0
```

Состояние БД после всех операций:
| Метрика | Значение |
|---|---|
| users total / orphan | 3 / 0 |
| audit_event orphan (NULL/test) | 0 |
| magic_link total / orphan | 26 / 0 |
| guest_session | 0 |
| courses / materials / tasks | 161 / 659 / 587 |

`materials=659`, `tasks=587` — стали ниже/выше baseline (664/567) из-за параллельного WP-импорта контента, который перетряс таблицу за время сессии. К тесту не относится.

## Failing test

`test_y6_escalation_cron_tick_idempotent` падает и без моих правок (проверено `git stash` + прогон). Это pre-existing regression, выходит за scope этого PR. Зафиксировать как follow-up — возможно связан с `5eb45f7 fix: Y-6 escalation — защита от non-object task_results.metrics`.

## Risks / Follow-ups

- **Скрипт snapshot_ts vs параллельная сессия:** если два разработчика одновременно гоняют pytest на одной dev-БД — sweep одной сессии заденет users другой. На dev приемлемо, для CI стоит держать изолированную БД на каждого runner'а.
- **Фикстура не зачищает state, накопленный до прогона:** users id 2348–2377 удалось снести только повторным запуском `cleanup_test_artifacts.py`. Это by design — фикстура snapshot'ит время старта. Для добивки исторического мусора есть отдельный скрипт.
- **WP-импорт контента в дев** — материалы/задачи появляются и исчезают между прогонами. Фикстура их не трогает (что правильно), но baseline `expected 664` в sanity-блоке скрипта может «дрожать». В рамках следующих этапов tsk-004 — заменить hardcoded baseline на проверку «materials > 0».
- **Failing test** — отдельная задача (см. выше).

## Артефакты

- [tests/conftest.py](../tests/conftest.py) — autouse session-scoped sweep
- [tests/test_y6_review_loop.py](../tests/test_y6_review_loop.py) — обновлён misleading-комментарий `_cleanup`
- [scripts/cleanup_test_artifacts.py](../scripts/cleanup_test_artifacts.py) — bug-fix в magic_link sweep
- [reviews/2026-05-20-test-cleanup-autouse-fixture.diff](2026-05-20-test-cleanup-autouse-fixture.diff)
