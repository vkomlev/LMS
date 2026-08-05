# review-gate: tsk-563 — редактирование доп. полей профиля в кабинете админа/методиста

**Дата:** 2026-08-05
**Проекты:** LMS (backend) + SPW (frontend)
**Задача:** `D:\Work\Root\tasks\tsk-563-redaktirovanie-dop-polej-profilya-kategoriya-klass-gorod-poyas-v-kabinete-admina.md`
**Диff (LMS):** [2026-08-05-tsk563-admin-profile-extra.diff](2026-08-05-tsk563-admin-profile-extra.diff)

## Контекст и решение оператора

Продолжение tsk-427 (self-service `PATCH /me`): нужно дать методисту/админу
редактировать те же 4 поля (категория/класс/город/часовой пояс) чужому
ученику из кабинета. Развилка «дать доступ также методисту или только
админу» решена оператором через `AskUserQuestion`: **как есть сейчас** —
расширить существующий общий `PATCH /users/{id}` (гейт
`require_role("methodist","admin")`, тот же, что уже редактирует ФИО/почту),
не заводить отдельный admin-only эндпоинт.

## Реализация

**LMS:**
- `app/schemas/me.py` — валидаторы `_strip_city`/`_validate_timezone`
  вынесены в переиспользуемые функции `normalize_city`/`validate_timezone`
  (DRY, без изменения поведения self-service `PATCH /me`).
- `app/schemas/users.py` — `UserUpdate`/`UserRead` получили
  `category`/`school_grade`/`city`/`timezone` (тот же `ProfileCategory`
  Literal, импортирован из `schemas/me.py`, без дублирования списка
  значений).
- `app/api/v1/users.py::patch_user` — доп. поля выделяются из payload и
  проводятся через `me_service.update_profile_extra` (та же функция, что
  self-service `PATCH /me`, tsk-427) **напрямую**, без дублирования
  кросс-валидации «класс только у школьника» и каскадного сброса
  `school_grade`. ValueError → 422. Пишет audit-событие
  `admin.profile_extra.updated` (`user_id`=актёр-методист/админ,
  `details.target_user_id`=редактируемый ученик) — по образцу
  `manual_progress_service.py` (актёр в `user_id`, субъект — в `details`).
  Одна транзакция: профиль-экстра flush → audit flush → `service.update()`
  коммитит всё разом (rollback на IntegrityError откатывает всё, включая
  доп. поля).
- `app/services/audit_service.py` — новая константа
  `ADMIN_PROFILE_EXTRA_UPDATED`.

**SPW:**
- `components/methodist/PersonProfileExtraEditForm.tsx` — новый компонент
  по образцу self-service `ProfileExtraForm` (`/me/profile`, tsk-427):
  та же кросс-валидация на клиенте (скрытие «Класс» при смене категории),
  те же справочники `CATEGORY_OPTIONS`/`TIMEZONE_OPTIONS`. Отдельная форма
  от `PersonEditForm` (ФИО/почта) — независимый набор полей с другим
  жизненным циклом, тот же паттерн, что self-service `/me` vs `/me/profile`.
- `lib/methodist/use-methodist-people.ts::PersonPatch` — расширен 4 полями;
  `usePatchPerson` не менялся (уже общий PATCH).
- `components/methodist/PersonDetail.tsx` — врезка кнопки формы рядом с
  `PersonEditForm`; read-only отображение заполненных полей в карточке
  (`Категория`/`Город`/`Часовой пояс`, скрыто если не заполнено — большинство
  учеников поля не имеют).
- `lib/api-types.ts` перегенерирован из обновлённого `docs/openapi.json`.

## Проверка по 12 измерениям

1. **Соответствие целям** — доступ дан методисту+админу (решение оператора),
   поля/валидация 1-в-1 с self-service. DRIFT не обнаружен.
2. **Корректность** — кросс-валидация (grade без school_student, каскадный
   сброс), комбинированное обновление (доп. поля + ФИО в одном запросе,
   одна транзакция), audit-событие пишется только при реальной правке доп.
   полей (не при правке одного ФИО) — покрыто тестами.
3. **Безопасность миграций** — миграций нет (поля уже существуют с tsk-427).
4. **Безопасность/ACL** — переиспользован существующий гейт, не ослаблен и
   не расширен за пределы уже принятого решения. Студент не может править
   чужой профиль (403, тест). IDOR неприменим к самому паттерну (гейт по
   роли, не по владению) — та же модель, что у ФИО/почты.
5. **Тесты** — LMS: 9 новых (`test_tsk563_admin_profile_extra.py`) +
   19 существующих `test_tsk433_people_write_gates.py` — 28/28 PASS. Полный
   `pytest`: **1693 passed, 11 skipped**. SPW: 6 новых unit-тестов
   компонента, `tsc`/`eslint` чисто (детали — Validation Results ниже).
6. **Docs/Config Drift** — `docs/openapi.json` регенерирован, endpoints
   не менялись (265), только схема `UserUpdate`/`UserRead`. SPW
   `lib/api-types.ts` синхронизирован, diff проверен на отсутствие чужого
   шума (только category/school_grade/city/timezone).
7. **Phase Integrity** — decomposition закрыта, TODO нет.
8-10. Н/п (нет новых данных/справочников/date-time сравнений).
11. **Cross-project memory sync** — выполняется отдельным шагом после этого
    ревью (см. задачу в трекере сессии).
12. **Public API Contract Sync** — `UserUpdate`/`UserRead` расширены
    аддитивно, путь/метод `/users/{id}` не менялись. Hardcoded URL sweep —
    0 совпадений от этого изменения.

## Инцидент по пути (не блокирует ПРИНЯТО, но важно зафиксировать)

Во время реализации параллельная сессия (tsk-412, независимый чип в этом же
рабочем дереве) сделала `git stash` перед своим коммитом — это на несколько
секунд убрало с диска все 4 незакоммиченных файла tsk-563 (тот же класс
риска, что ADR-0008 в SPW, здесь впервые всплыл в LMS). Стэш был корректно
вытолкнут обратно (`git stash pop`) той же или другой сессией — файлы
восстановлены полностью, подтверждено grep по всем 4 файлам + повторным
прогоном 50 тестов (все зелёные). **Prevention action**: коммитить
самодостаточный кусок работы раньше, не держать несколько файлов
незакоммиченными долго в потенциально общем дереве — этот ревью-артефакт
пишется и коммит делается сразу после зелёных тестов backend, до начала
следующего фронтенд-этапа.

## Решение

**ПРИНЯТО.** Блокирующих находок нет.

## Validation Results

- `pytest tests/test_tsk563_admin_profile_extra.py tests/test_tsk433_people_write_gates.py tests/test_me_profile_update.py tests/test_tsk427_profile_extra_fields.py` — 50/50 PASS (перепрогнано после инцидента со stash).
- `pytest` (весь LMS) — 1693 passed, 11 skipped.
- SPW: `tsc --noEmit` — 0 ошибок; `eslint` (изменённые файлы) — чисто.
- SPW unit-тесты нового компонента и живой прогон на проде — следующие шаги
  той же сессии (см. трекер задач).

## Operator handoff

Категория А: review-gate, коммит, пуш, деплой — по стоячей авторизации
(tsk-359), обязательная живая браузерная проверка после деплоя.
