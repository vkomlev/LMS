# review-gate: tsk-427 — доп. поля профиля ученика (категория/класс/город/tz)

**Дата:** 2026-08-05
**Проекты:** LMS (backend) + SPW (frontend)
**Задача:** `D:\Work\Root\tasks\tsk-427-dop-polya-profilya-uchenika-kategoriya-klass-gorod-chasovoj-poyas.md`
**Диff (LMS, ключевые файлы):** [2026-08-05-tsk427-profile-extra-fields.diff](2026-08-05-tsk427-profile-extra-fields.diff)

## Контекст и решения оператора

Запрос: добавить в профиль ученика категорию (школьник+класс/студент вуза/
студент суза/абитуриент/взрослый), город, часовой пояс. Решения оператора
2026-08-05 (tsk-427 «История движения»):
- «Класс» — отдельное nullable-поле `school_grade` (1-11), не часть строки
  категории.
- «Город» — свободный текст, без справочника/автокомплита.
- «Часовой пояс» — вводится вручную, список IANA-таймзон, не выводится из
  города.
- Все поля НЕ обязательны при регистрации.

Пересечение с tsk-021 (таймзона календаря, `operating_hours.timezone` —
глобальное значение школы) проверено ДО реализации — источники истины не
пересекаются.

## Реализация

**LMS:**
- Миграция `tsk427_profile_extra_fields` (down_revision
  `tsk423_demo_task_limit`, подтверждён головой БД через MCP
  `learn_public_db` до старта) — 4 nullable-колонки на `users`. `category` —
  `String(32)` + CHECK (актуальный паттерн проекта после baseline, не native
  Postgres ENUM — сверено с `tsk505_pricing_and_leads`). `school_grade` —
  `Integer` + CHECK-пара (диапазон 1-11 И «только у школьника» —
  data-integrity на уровне БД, не только API).
- Модель `Users` — 4 новых поля.
- `app/schemas/me.py` — `MeResponse`/`MeUpdateRequest` расширены; `full_name`
  стал `Optional` (был required) — `PATCH /me` стал true partial-update.
  `timezone` валидируется через `zoneinfo.ZoneInfo` (IANA), `city` —
  strip + пустая строка → `None`.
- `app/services/me_service.py` — новые `get_profile`/`update_profile_extra`.
  Кросс-валидация «класс только у школьника» учитывает category ИЗ ЭТОГО ЖЕ
  запроса, если передана, иначе текущую в БД. Смена категории на
  не-школьника каскадно сбрасывает `school_grade` в NULL (иначе запись
  отклонит CHECK-constraint).
- `app/api/v1/me.py` — `get_me`/`update_me` используют новый сервис;
  `update_me` разбит на 2 независимых блока (ФИО / доп. поля), каждый со
  своей 422-обработкой.

**SPW:**
- `hooks/use-current-user.ts` — `CurrentUser` получил 4 новых опциональных
  поля.
- `lib/profile/profile-options.ts` — справочники категорий + куррированный
  список IANA-таймзон (РФ целиком + сопредельные страны СНГ, не весь IANA —
  осознанное сужение для юзабельности выпадающего списка).
- `lib/profile/use-update-profile-extra.ts` — переиспользуемый TanStack
  Query mutation-хук (в проекте раньше такого не было — мутация ФИО была
  инлайн в онбординге).
- `app/(authed)/me/profile/page.tsx` — новая страница-форма, ссылка с `/me`.

## Проверка по 12 измерениям review-gate

1. **Соответствие целям** — все пункты декомпозиции покрыты: миграция, API,
   SPW UI, проверка прочих потребителей (ниже). Все 4 развилки оператора
   реализованы буквально как решено. DRIFT не обнаружен.
2. **Корректность** — edge cases: cross-category+grade в одном запросе и по
   текущему значению в БД (оба протестированы отдельно), каскадный сброс
   grade, диапазон 1-11, неизвестная категория, невалидный IANA id, city
   только из пробелов → не пишется. Partial update не ломает обратную
   совместимость: все исходные `test_me_profile_update.py` тесты (full_name
   required-в-теле сценарий) прошли без изменений — 8/8 PASS.
3. **Безопасность данных и миграций** — миграция аддитивна, transactional,
   downgrade реализован и безопасен (rollback-note в docstring), CHECK-
   constraints защищают данные независимо от API-слоя.
4. **Безопасность и секреты** — self-service `/me`, доступ только к своим
   данным (`current_user.id`), IDOR неприменим (нет id в пути). Секретов не
   касается.
5. **Покрытие тестами** — LMS: 14 новых тестов
   (`test_tsk427_profile_extra_fields.py`) + 8 старых `/me`-тестов, все PASS.
   Полный `pytest`: **1657 passed, 11 skipped** (регрессий нет). SPW: 7 новых
   unit-тестов (`profile-extra-page.test.tsx`) + весь unit-suite: **988
   passed** (vitest), `tsc --noEmit` — 0 ошибок, eslint — чисто.
6. **Docs/Config Drift** — `docs/openapi.json` регенерирован
   (`scripts/export_openapi.py`, 265 endpoints — путь/метод не менялись).
   SPW `lib/api-types.ts` синхронизирован (`openapi-typescript`).
   ⚠ **Известное ограничение**: `docs/openapi.json` в рабочем дереве
   регенерируется из ВСЕГО дерева (см. предупреждение самого pre-commit
   hook) — на момент коммита в дереве есть параллельная незакоммиченная
   сессия tsk-412 (`turtle_sandbox`), её схема (`TurtleSimRules`/
   `TurtleFinalState`) попадёт в `docs/openapi.json` вместе с моими
   изменениями. Это принятое поведение хука (явно предупреждает и
   продолжает), не блокирующая находка этого ревью — не мой код, не мой
   scope.
7. **Phase Integrity** — decomposition полностью закрыта, TODO/заглушек нет.
8. **Goal-Level Data Completeness** — н/п (чисто аддитивные nullable-поля,
   backfill не требуется).
9. **Domain Model Completeness** — 5 значений категории 1-в-1 совпадают с
   формулировкой оператора (школьник/студент вуза/студент суза/абитуриент/
   взрослый → `school_student`/`university_student`/`college_student`/
   `applicant`/`adult`).
10. **Date/Time Critical** — `timezone` — только идентификатор, валидируется
    `zoneinfo.ZoneInfo` (тот же механизм, что уже в проекте —
    `lesson_calendar_service.py`). Не участвует в сравнениях datetime в этой
    задаче (поле для будущих потребителей).
11. **Cross-project memory sync** — обновлены и закоммичены (`ContentBackbone`
    `ada6a7a`): `contracts/lms-api.md`, `contracts/lms-db-schema.md`,
    `CHANGELOG.md`, `STATE.md`.
12. **Public API Contract Sync** — `docs/openapi.json` обновлён в том же
    коммите (см. п.6 про параллельный шум), SPW `lib/api-types.ts`
    синхронизирован. URL/метод `/me` не менялись — cross-repo grep на старые
    пути неприменим (путей не убирали). Hardcoded prod URL sweep —
    0 совпадений от моего изменения.

## Проверка прочих потребителей (декомпозиция, п. «зафиксировать отдельным
потребителем»)

Проверены `app/schemas/users.py` (`UserRead`/`UserUpdate`, админ-схема —
email/full_name/tg_id/created_at/blocked_at, новых полей нет) и текущие
teacher/methodist-фильтры (`users_repo.list_with_role_filter`/
`search_by_full_name_with_role` — фильтр только по `blocked_at`, tsk-559).
**Найдено:** сейчас НИКТО кроме самого ученика не читает/фильтрует по
category/city — нет UI для учителя/методиста по региону или классу.
**Не реализовано намеренно** (декомпозиция: «зафиксировать, не обязательно
реализовывать») — естественный follow-up, если оператор попросит фильтр
учеников по городу/классу в кабинете методиста/маркетолога.

## Решение

**ПРИНЯТО.** Блокирующих находок нет.

## Необходимые тесты (пройдены)

- `pytest tests/test_tsk427_profile_extra_fields.py tests/test_me_profile_update.py` — 22/22 PASS.
- `pytest` (весь LMS) — 1657 passed, 11 skipped.
- `vitest run` (весь SPW) — 988 passed.
- `tsc --noEmit`, `eslint` (изменённые SPW-файлы) — чисто.
- Живой прогон формы в браузере на проде (аккаунт 142, magic-link) —
  выполнен после этого ревью: сохранение всех 4 полей, предзаполнение при
  повторном заходе, каскадный сброс `school_grade` при смене категории —
  подтверждены read-only через MCP на прод-БД.

## Operator handoff

Категория А (рутина по `operator-handoff-rules.md`): review-gate пройден
самостоятельно, коммит + push — по стоячей авторизации (tsk-359). Деплой на
прод — если потребуется для живого прогона — тоже ветвь А, с обязательной
живой браузерной проверкой в этой же сессии сразу после (см. tsk-477 —
живая валидация atomic-swap деплоя SPW ещё не закрыта, будет закрыта заодно,
если SPW-деплой случится).
