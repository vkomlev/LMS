# Спека: кабинет родителя (tsk-478)

**Дата:** 2026-08-01 · **Задача:** [[tsk-478]] (P1) · зависимость [[tsk-494]] (закрыта, задеплоена)
**Skill:** `/spec-writer`

## Цель

Родитель входит своим email/TG и видит read-only периодный дашборд ОДНОГО
привязанного ученика (данные — готовый `GET /students/{id}/dashboard` из
tsk-494), без доступа к остальному функционалу портала.

## Границы

**Входит:**
- Миграция: роль `parent` (строка в `roles`).
- Миграция: таблица `parent_student_links` (M2M, по образцу
  `student_teacher_links`).
- Backend: CRUD-эндпоинты связки (создание/снятие) — доступны ТОЛЬКО
  teacher/methodist/admin/service, НЕ самому родителю. Создание связки
  идемпотентно выдаёт роль `parent`, если её ещё нет (см. «Гочта» ниже).
- Backend: гейт `GET /students/{id}/dashboard` расширяется — родитель с ролью
  `parent` и записью в `parent_student_links` видит СВОЕГО ученика.
- SPW: роут-группа `app/(parent)/parent/*`, одна страница — дашборд.
- Живая проверка на проде: тестовая связка, вход под родителем, подтверждение
  read-only и видимости только своего ребёнка.

**НЕ входит (явно исключено):**
- Многодетность / переключатель ребёнка в шапке (оператор подтвердил
  повторно 2026-08-01 — отложено).
- Новый auth-механизм — только существующий magic-link/TG.
- Endpoint создания учётной записи "с нуля" для родителя — используется
  существующий self-service magic-link (первый вход по email автоматически
  создаёт `Users` + auto-assign роли `student`, см. «Гочта» ниже).
- Любые write-действия для роли `parent` (продление попыток и т.п.) — не
  создаются вообще, не гейтятся постфактум.
- Отдельный `/design-consultation` — переиспользуется `SPW/DESIGN.md`
  (оператор подтвердил 2026-08-01).

## Найденное при разведке (важно для реализации)

1. **Роль `parent` НЕ требует нового API назначения ролей.** Уже существует
   `POST/DELETE /users/{user_id}/roles/{role_id}` (`app/api/v1/user_roles.py`,
   гейт `require_role("admin")`) + справочник `GET /roles/catalog`. Новая
   роль — это только миграция-seed строки в `roles`, назначение — этим же
   существующим эндпоинтом.
2. **Гочта (auto-assign при первом входе):** `get_or_create_user_by_email`
   (`app/services/auth/magic_link_service.py:167`) при СОЗДАНИИ нового
   пользователя в ТОЙ ЖЕ транзакции вызывает `ensure_student_role` — родитель,
   впервые входящий по email (ещё не заведённый оператором), автоматически
   получит роль `student`. Та же логика для TG (`tg_init_service.py`). Чтобы
   не заставлять оператора руками снимать `student` каждый раз: эндпоинт
   создания `parent_student_links` (см. ниже) идемпотентно добавляет роль
   `parent` пользователю (переиспользует `user_roles_service.add_role`),
   если её ещё нет. Роль `student` НЕ снимается автоматически (может
   оказаться легитимной у того же человека в другом контексте) — операционная
   инструкция для оператора: если родитель НЕ должен быть виден как ученик,
   снять `student` вручную тем же существующим эндпоинтом (`DELETE
   /users/{id}/roles/{role_id}`) через кабинет администратора.
3. **ACL дашборда — НЕ через `ensure_can_edit_progress`.** Эта функция
   используется по всему сервису и для настоящего РЕДАКТИРОВАНИЯ прогресса
   (не только для этого read-only дашборда) — добавление туда роли `parent`
   создало бы риск, что где-то ещё `can_edit_progress` начнёт неявно пускать
   родителя к мутациям. Вместо этого — отдельная композитная проверка ТОЛЬКО
   в `app/api/v1/student_dashboard.py`:
   `ensure_can_edit_progress(...)` (сервис/admin/methodist/teacher) **ИЛИ**
   `parent_student_links_service.is_linked(parent_id=current_user.id,
   student_id=student_id)` при роли `parent`. Так родительский доступ
   физически не может просочиться в write-пути.
4. **Образец M2M** — `t_student_teacher_links`
   (`app/models/association_tables.py:49`), `StudentTeacherLinksRepository`,
   `StudentTeacherLinksService`, `app/api/v1/student_teacher_links.py`.
   `parent_student_links` — тот же паттерн 1:1 (PK составной
   `parent_id`+`student_id`, `linked_at`, FK CASCADE на `users`).

## Ограничения

- Технические: Python 3.10+/FastAPI/SQLAlchemy async (LMS), Next.js/TS (SPW),
  переиспользование существующих auth/ACL примитивов — новых auth-механизмов
  не создавать.
- Безопасность: IDOR-критично — родитель `parent_id=X` не должен видеть
  ученика `Y`, если явной строки в `parent_student_links` нет. Обязателен
  негативный тест.
- Минимизация данных: уже заложена в API tsk-494 (нет `solution_rules`, нет
  текста заявок помощи) — UI родителя ничего дополнительно не фильтрует и
  не запрашивает.
- Срок: публично обещано оператором 2026-07-30, 1-2 недели — не затягивать
  исследование сверх необходимого.

## План

1. Alembic-миграция: `INSERT INTO roles (name) VALUES ('parent')` (по образцу
   существующих ролей — проверить точный механизм seed, например
   `20241231_235959_baseline_pre_alembic_schema.py` или отдельная data-миграция
   для новых ролей, если таковая уже есть в истории — не дублировать паттерн,
   если он есть).
2. Alembic-миграция: таблица `parent_student_links` (PK составной
   `parent_id`+`student_id`, `linked_at timestamptz default now()`, FK
   CASCADE на `users.id` на оба столбца) + модель в
   `app/models/association_tables.py`.
3. `app/repos/parent_student_links_repository.py` +
   `app/services/parent_student_links_service.py` — методы `list_children`,
   `list_parents`, `is_linked`, `add_link` (+ идемпотентный auto-assign роли
   `parent`), `remove_link`. Прямая копия паттерна
   `StudentTeacherLinksRepository`/`Service`.
4. `app/api/v1/parent_student_links.py` — `GET
   /users/{student_id}/parents`, `POST/DELETE
   /users/{student_id}/parents/{parent_id}` — гейт
   `require_role("methodist","admin")` по образцу `_PEOPLE_WRITE_GATE`
   (`student_teacher_links.py`). Регистрация роутера в `app/api/main.py`.
5. `app/api/v1/student_dashboard.py` — расширить гейт композитной проверкой
   (п.3 разведки выше). Новый тест: parent, привязанный к student A, видит A
   (200) и НЕ видит B (403).
6. SPW: `app/(parent)/parent/dashboard/page.tsx` (или аналог, по факту
   структуры проекта) — потребляет `GET /students/{id}/dashboard`, тот же
   `hasRole()`/`getServerMe()` паттерн защиты роута, что `(teacher)`/
   `(admin)`/`(methodist)`. Дизайн — по `SPW/DESIGN.md`, без нового
   дизайн-прохода.
7. Тесты backend: создание/снятие связки (только methodist/admin, не сам
   родитель — 403), auto-assign роли `parent` при создании связки
   (идемпотентно — повторный вызов не дублирует роль), IDOR-тест дашборда
   (чужой родитель → 403), позитивный тест (свой ученик → 200, тот же формат
   ответа, что у teacher/methodist).
8. Живая проверка на проде: тестовая связка родитель↔ученик (реальные ID),
   вход под тестовым родителем (magic-link), подтверждение в браузере — виден
   только свой ребёнок, нет кнопок действий, нет `solution_rules`/текста
   заявок в сетевых ответах.

## Критерии готовности

- [ ] Alembic head включает миграции роли `parent` и `parent_student_links`,
      `alembic upgrade`/`downgrade` оба проходят на dev.
- [ ] `POST /users/{student_id}/parents/{parent_id}` под methodist/admin —
      201/204, повторный вызов идемпотентен, роль `parent` назначена.
- [ ] Та же операция под ролью `parent`/`teacher`/без роли — 403.
- [ ] `GET /students/{id}/dashboard` под ролью `parent`, привязанным к этому
      `id` — 200, форма ответа идентична существующей (tsk-494).
- [ ] Тот же запрос для НЕпривязанного `id` — 403 (IDOR-тест обязателен).
- [ ] Полный прогон pytest LMS зелёный (регресс на `student_teacher_links`/
      `user_roles` не допускается).
- [ ] SPW: `/parent/dashboard` открывается под ролью `parent`, редиректит
      неавторизованных/чужеролевых.
- [ ] Живая проверка на проде (браузер, тестовая связка) — read-only
      подтверждено визуально, сетевые ответы без запрещённых полей.
- [ ] `/review-gate` — ПРИНЯТО.
- [ ] Cross-project контракты (ContentBackbone) обновлены при изменении
      публичного API.

## Риски

| Риск | Мера снижения |
|---|---|
| Auto-assign `student` роли при первом входе родителя маскирует его как ученика в списках | Явная операционная заметка оператору (см. «Гочта» п.2), не блокирует MVP; можно вынести в follow-up автоматическую очистку |
| Смешение ACL дашборда с `can_edit_progress` создаёт скрытый write-доступ | Композитная проверка изолирована в роуте дашборда, НЕ трогает `can_edit_progress` (п.3 разведки) |
| SPW-паттерн `(parent)` скопирован неточно (визуальная калька вместо паттерна защиты) | Явное указание в декомпозиции — только паттерн role-guard, не интерфейс |
| Срок поджимает — риск урезать живую проверку | Живая проверка — обязательный критерий готовности, не опциональный |

## Распределение по skills

| Шаг | Под-задача | Skill-исполнитель | Ревью |
|---|---|---|---|
| 1-2 | Миграции роли + `parent_student_links` | `/fastapi-api-developer` + `/db-check` | `/review-gate` |
| 3-4 | Repo/service/API связки | `/fastapi-api-developer` | `/review-gate` |
| 5 | Расширение гейта дашборда | `/fastapi-api-developer` | `/review-gate` (IDOR — обязательная проверка) |
| 6 | SPW роут-группа + страница | `/executor-pro` | `/review-gate` |
| 7 | Тесты backend | `/fastapi-api-developer` | — |
| 8 | Живая проверка на проде | `/fastapi-api-developer` (deploy) + живой браузер под тестовым родителем | оператор подтверждает визуально при необходимости |

## Чеклист исполнения

- [ ] Миграции применены на dev, `alembic downgrade -1` откатывается чисто.
- [ ] Полный pytest зелёный.
- [ ] OpenAPI регенерирован.
- [ ] Cross-project docs (ContentBackbone) обновлены.
- [ ] Задеплоено на прод, живая проверка выполнена.
- [ ] Root-трекер tsk-478 закрыт, коммиты во всех репозиториях запушены.
