# Review S1 — M11 миграция `courses.is_public_demo`

**Дата:** 2026-05-02
**Tech-spec:** [tech-spec-Y5-guest-embed-v1.md §6.1](../../ContentBackbone/docs/tech-specs/tech-spec-Y5-guest-embed-v1.md)
**Stage:** S1 (foundation для S2/S3/S6)
**Исполнитель:** /executor-pro
**Ревью:** /db-check (MCP) → /pr-review (этот файл) → /review-gate (финал в S8)

## Контекст

Phase Y-5 открывает guest-mode SPW: анонимный посетитель решает 1+ задач из demo-курсов без регистрации. M11 — единственная миграция фазы; добавляет флаг `courses.is_public_demo` для пометки публичных курсов. Параметризация (1 demo-курс на acceptance) — через ручной UPDATE оператором (S6).

Альтернативы рассмотрены и отклонены:
- Отдельная таблица `public_demo_courses(course_id PK)` — лишний JOIN при каждом ACL check, без выигрыша.
- Колонка `visibility ENUM('private','demo','public')` — преждевременная абстракция; в обозримом будущем третий уровень не нужен.

## Изменения

| Файл | Изменение |
|---|---|
| `app/db/migrations/versions/20260502_010000_M11_courses_is_public_demo.py` | NEW: ADD COLUMN `is_public_demo BOOLEAN NOT NULL DEFAULT FALSE` + partial INDEX `WHERE is_public_demo=TRUE` |
| `app/models/courses.py` | ADD ORM field `is_public_demo: Mapped[bool]` (server_default `false`) |
| `app/schemas/courses.py` | ADD `is_public_demo: bool` в `CourseRead` (default False) |

## Validation Commands

```bash
cd d:/Work/LMS
alembic upgrade head        # применяет M11 (был head=m10, стал m11)
alembic current             # m11_courses_is_public_demo (head)
alembic downgrade -1        # удаляет колонку и индекс
alembic upgrade head        # повторно применяет — roundtrip зелёный
```

Все четыре команды отработали без ошибок (см. `reviews/evidence/2026-05-02-y5-s1-alembic-roundtrip.txt`, генерируется на финальном gate).

## DB Findings (через MCP `learn_public_db`)

- `information_schema.columns`: `courses.is_public_demo` — `boolean`, `is_nullable='NO'`, `column_default='false'`. ✓
- `pg_indexes`: `idx_courses_is_public_demo` создан как partial btree `WHERE (is_public_demo = true)`. ✓
- Распределение данных: всего 161 row в `courses`, `is_public_demo=true` — 0. Все existing курсы остались private — backward-compat 100%. ✓
- Никаких изменений в `course_topics`, `tasks`, `guest_session`, `guest_attempt` — изменение точечное.

## Risks / Follow-ups

| ID | Описание | Митигация |
|---|---|---|
| G7 (из ТЗ) | M11 ломает existing course-queries | Не реализовался: NOT NULL DEFAULT FALSE добавляет колонку без data-impact на existing SELECT'ы; Pydantic `CourseRead` получил default → старые consumer'ы (если читают через `from_attributes=True`) не падают |
| — | Будущая UI-toggle для оператора | Отложено в post-MVP; в Y-5 — manual SQL UPDATE (см. S6) |

## Acceptance (§16 ТЗ)

- [x] M11 файл создан, имя `20260502_010000_M11_courses_is_public_demo.py`
- [x] `alembic upgrade head` зелёный
- [x] `alembic downgrade -1 && alembic upgrade head` зелёный (roundtrip)
- [x] `\d courses` показывает column `is_public_demo BOOLEAN NOT NULL DEFAULT false` (verified via information_schema)
- [x] Partial index `idx_courses_is_public_demo` существует (verified via pg_indexes)
- [x] ORM `Course.is_public_demo` доступен (server_default + nullable=False)

## PR-review статус

**PASS.** Миграция минимальна, идемпотентна, обратимо безопасна. Готова к интеграции; финальный `/review-gate` пройдёт в S8 после закрытия S2/S3/S6/S7.
