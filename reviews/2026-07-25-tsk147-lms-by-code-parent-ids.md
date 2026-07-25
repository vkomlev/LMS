# tsk-147 — LMS `by-code` отдаёт `parent_course_ids` (единый способ читать иерархию)

## Контекст

Задача трекера `D:\Work\Root\tasks\tsk-147-lms-by-code-otdaet-parent-course-ids.md`
(создана 2026-07-03 по наблюдению при сверке tsk-146): `GET /api/v1/courses/by-code/{code}`
возвращал `parent_course_ids: []` даже для вложенных курсов (проверено на
`wp:python-podrostki-tema-9-turtle` id=892).

## Ключевая находка: код уже исправлен параллельной задачей (tsk-261), до старта этой сессии

Параллельная задача **tsk-261** («QA-приёмка Python для подростков + ОГЭ», находка **A6**
независимого pre-deploy ревью) обнаружила ровно тот же дефект самостоятельно и уже
исправила его **2026-07-17**, коммит LMS `bdc6868` («fix: отдавай реальные
parent_course_ids в by-code — проверка «корень» была фиктивной (tsk-261)»), задеплоено
в тот же день. tsk-261 закрыта 2026-07-24.

Причина дефекта: `CoursesService.get_by_course_uid` шёл через `repo.get_by_keys` без
`selectinload(Courses.parent_courses)`. `Courses.parent_course_ids` — property
(`app/models/courses.py:102-122`), при незагруженной связи молча отдаёт `[]` вместо
ошибки/lazy-load — потребитель, различающий корень/подкурс по этому полю, считал
корнем ЛЮБОЙ курс.

Фикс (`app/services/courses_service.py::get_by_course_uid`, строки ~119-124):
```python
stmt = (
    select(Courses)
    .options(selectinload(Courses.parent_courses))
    .where(Courses.course_uid == course_uid)
)
```
— тот же паттерн, что уже применялся в `by-id` (`app/repos/base.py::get`, спецкейс для
модели `Courses`, строки 33-38) и в `/courses/{id}/children`
(`app/repos/courses_repo.py::get_children`, строка 41). Это ровно **вариант 1** из
развилки tsk-147 («by-code гидрирует parent_course_ids так же, как by-id»).

Регресс-тест уже существует и добавлен вместе с фиксом:
`tests/test_tsk261_by_code_parent_ids.py` (2 теста: подкурс сообщает родителя, корень —
пустой список).

## Развилка — решение и замер (сегодня, 2026-07-25)

**Решение:** вариант 1 (гидрация в коде) — уже реализован, дополнительная работа не
требуется. Подтверждено, что стоимость разумна и это НЕ N+1:

- `selectinload(Courses.parent_courses)` — ровно ОДИН дополнительный SQL-запрос
  (JOIN по `course_parents` с фильтром по `course_id`), не рекурсивный, не зависит от
  глубины иерархии и числа родителей узла.
- **Замер на проде (read-only, `learn_prod_db` MCP):** рекурсивный CTE по всему графу
  курсов — максимальная глубина **3 уровня**, всего узлов с учётом путей — 731.
- **Живой smoke на самом связанном узле графа** (id=1247 `wp:inf-5-practice-algorithms`,
  5 родителей, глубина 3):
  ```
  GET /courses/by-code/wp:inf-5-practice-algorithms
  → {"id":1247,...,"parent_course_ids":[886,887,888,889,890],...}
  time_total = 0.007007s
  ```
- **Живой smoke на курсе из исходной жалобы tsk-147** (id=892
  `wp:python-podrostki-tema-9-turtle`):
  ```
  GET /courses/by-code/wp:python-podrostki-tema-9-turtle
  → {"id":892,...,"parent_course_ids":[823],...}
  ```
  (раньше был `[]` — дефект больше не воспроизводится).

Вывод: гидрация на by-code стоит ровно столько же, сколько уже стоит на by-id
(единственный join-запрос), латентность на самом тяжёлом узле реального графа — единицы
миллисекунд. Документировать «читать иерархию только через by-id» не требуется — оба
пути эквивалентны и уже единообразны.

## CB-клиент и сверки tsk-146

Проверено агентом (read-only разведка в `D:\Work\ContentBackbone`):

- `monolith/lms_client` (`LMSClient.get_course_by_code`) вызывается в
  `lms_publish/lesson_publisher.py`, `lms_publish/run.py`, `subsystem_c/runner.py`,
  `content_lint/audit.py`, `scripts/tsk308_phase2_reexport.py` — везде только для
  резолва `id`/`access_level`, ни один вызов не читал `parent_course_ids` из ответа
  by-code и не содержал workaround-обхода бага (лишнего запроса by-id ради родителей).
- Единственное упоминание бага — заметка в
  `skills/core/methodist/references/lms-wp-export.md:22-23`, советовавшая читать
  иерархию только через by-id/`/children`. Заметка была актуальна до фикса tsk-261,
  сейчас устарела — **обновлена** в этой сессии (см. «Изменённые файлы»).
- Скрипты сверки графа флагмана tsk-146 жили не в ContentBackbone, а в
  `CreateCourses/courses/ai-predprinimatel/exports/lms-restructure/` и были удалены в
  tsk-237 (дубль-двойник, вне охвата этой задачи); сама сверка tsk-146 использовала
  by-id, а не by-code — консистентности с текущим by-code это не нарушало и раньше.
- Скрипты, читающие иерархию курсов в CB напрямую, используют `/courses/{id}/children`
  (`scripts/import_oge_sdamgia_variants.py:181-192`,
  `scripts/tsk174_reparent_sections.py:43`) — не затронуты.

**Вывод:** правок в коде CB не потребовалось — CB никогда не зависел от бага. Обновлена
только устаревшая документная заметка.

## Изменённые файлы

### LMS
Нет изменений кода — фикс уже задеплоен под tsk-261 (`bdc6868`, 2026-07-17). Этот
review-артефакт документирует верификацию и закрытие развилки tsk-147.

### ContentBackbone (commit `de30455`, запушен в `origin/main`)
- `docs/cross-project/contracts/lms-api.md` — добавлена секция
  «`parent_course_ids` hydration fix (tsk-147/tsk-261)» с деталями фикса и замера.
- `docs/cross-project/CHANGELOG.md` — новая запись в начале файла (2026-07-25).
- `skills/core/methodist/references/lms-wp-export.md` — заметка про баг by-code
  заменена на актуальную (by-code можно читать наравне с by-id).

## Validation Commands

```powershell
cd D:\Work\LMS
.venv\Scripts\python.exe -m pytest tests/test_tsk261_by_code_parent_ids.py -v
# => 2 passed
```

```bash
ssh lms-spw-vds "sudo -u app bash -lc 'cd /opt/lms && git log -1 --oneline'"
# => e9953ae (главная ветка на проде включает bdc6868 как предка)

# smoke на курсе из исходной жалобы tsk-147
curl http://127.0.0.1:8000/api/v1/courses/by-code/wp:python-podrostki-tema-9-turtle -H "X-API-Key: $KEY"
# => parent_course_ids:[823] (было [])

# smoke на самом глубоком/связанном узле графа
curl http://127.0.0.1:8000/api/v1/courses/by-code/wp:inf-5-practice-algorithms -H "X-API-Key: $KEY"
# => parent_course_ids:[886,887,888,889,890], 7мс
```

## DB Findings

- `learn_prod_db` (read-only, MCP): рекурсивный обход графа `course_parents` —
  максимальная глубина 3 уровня, 731 узел с учётом путей (некоторые узлы под
  несколькими родителями считаются по разным путям).
- Самый связанный узел — id=1247 (5 родителей одновременно), депта 3 — использован
  для замера стоимости гидрации в худшем реальном случае.

## Риски / Follow-ups

- Риска нет — код уже в проде, регресс-тесты существуют и проходят, живой smoke
  подтверждает корректное поведение на исходном курсе жалобы и на самом сложном узле
  графа.
- tsk-147 можно закрывать как **superseded/done via tsk-261** — код-часть развилки
  была решена независимо до начала этой сессии; работа сессии свелась к верификации
  (тесты + прод smoke + DB-замер) и синхронизации cross-project документации CB,
  которая по факту оставалась консистентной.
