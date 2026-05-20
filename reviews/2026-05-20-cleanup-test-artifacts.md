# Cleanup test artifacts — Learn.public

**Дата:** 2026-05-20
**Скилл:** `/db-check`
**Стратегия:** A (снести только тестовых users, сохранить контент и 3 реальных)
**Результат:** COMMIT, верификация через независимый MCP-канал прошла.

## Контекст

В `Learn.public` накопились артефакты ранних фаз Y-1...Y-6 (test-users, guest-sessions, audit-noise). Курсы/материалы/задачи и активность 3 реальных users (id 2 victor.komlev@mail.ru, id 3 sade_2005@list.ru, id 142 victor.v.komlev@gmail.com) должны сохраниться.

## Критерии «тестовый»

- `users.id NOT IN (2, 3, 142)` — 2241 учётка (email `@example.com` / `@example.test`, full_name `inbox-test-*`, `y[0-9]-*`, `V1`/`No-Email User`/`Pre-existing`/`VK User`, тестовые TG-id длиной >11)
- `guest_session` / `guest_attempt` — целиком (отдельная подсистема, все записи синтетические)
- `magic_link` без email или с email тестового user
- `audit_event` с user_id NULL или НЕ из реальных

## Технические находки

1. **Триггер `audit_event_no_modify`** (BEFORE DELETE/UPDATE → `RAISE EXCEPTION 'audit_event is append-only'`) блокирует не только прямой DELETE, но и SET NULL по FK от `users`. Решение — `ALTER TABLE audit_event DISABLE TRIGGER` в рамках транзакции, ENABLE до коммита.
2. **`notifications.modified_by`** имеет FK с правилом `NO ACTION` (в отличие от `user_id` → CASCADE). До удаления тестовых users — занулить `modified_by` у затронутых notifications (4 шт.).
3. Каскад от `DELETE FROM users` штатно подбирает: user_roles, identity_link, user_session, notifications.user_id, attempts (+task_results), learning_events, help_requests (+replies), teacher_courses, user_courses, student_*, access_requests, social_posts, user_achievements.

## Удалено

| Таблица | До | После | Δ |
|---|---:|---:|---:|
| users | 2244 | 3 | -2241 |
| user_roles | 1858 | 8 | -1850 |
| identity_link | 329 | 9 | -320 |
| audit_event | 273 | 5 | -268 |
| user_session | 309 | 184 | -125 |
| notifications | 138 | 44 | -94 |
| magic_link | 205 | 113 | -92 |
| guest_session | 14 | 0 | -14 |
| teacher_courses | 7 | 1 | -6 |
| task_results | 68 | 64 | -4 |
| guest_attempt | 4 | 0 | -4 |

## Не тронуто

- `courses=161`, `materials=664`, `tasks=567`, `course_parents=158` (WP + PY импорт)
- `roles=6`, `difficulties=5`, `alembic_version=1` (справочники)
- Активность реальных users 2/3/142: `attempts=149`, `learning_events=167`, `student_material_progress=193`, `help_requests=17`, `messages=9`, `user_courses=2`, `student_course_state=1`

## Артефакты

- [scripts/cleanup_test_artifacts.py](../scripts/cleanup_test_artifacts.py) — переиспользуемый, dry-run по умолчанию (`--apply` для COMMIT)
- [reviews/2026-05-20-cleanup-test-artifacts.diff](2026-05-20-cleanup-test-artifacts.diff)

## Верификация

Независимый MCP read-only канал (`mcp__learn_public_db__query`) после COMMIT подтвердил: реальные users живы (3), контент сохранён (161/664/567), тестовые таблицы соответствуют ожидаемым counts. Триггер `audit_event_no_modify` возвращён в `tgenabled='O'`.

## Risks / Follow-ups

- **Sequences не сброшены** — следующий `INSERT INTO users` получит id > 2244. Если требуется реструктуризация id — отдельная задача (потребует `setval` + проверка зависимостей).
- **id у новых тестов снова могут пересекаться** — стоит закрепить prefix `y%-test` и периодически чистить тем же скриптом (cron / pre-release).
- **5 оставшихся audit_event** — это события реальных users 2/3/142; не трогали по плану.
- **3 student_task_limit_override и 5 access_requests** — все привязаны к реальным users или к ролям, не тестовые.
