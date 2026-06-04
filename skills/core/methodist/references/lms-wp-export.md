# LMS-импорт и WP-публикация (две проекции курса)

Источник истины для `methodist` по выгрузке. Подключается в Шаге 0, применяется
в Шаге 6. Содержит закэшированные факты схемы LMS (`d:\Work\LMS`) и структуры
сайта victor-komlev.ru — чтобы skill не переисследовал их каждый прогон. При
явном расхождении с актуальным кодом LMS / сайтом — перепроверить и обновить
этот файл (followup), не выдумывать.

План методиста — **слой нормализации**: `docs/v2` остаётся источником, не
переписывается; план в `curriculum/` несёт **две проекции** одного курса.

## Часть 1. LMS-проекция (подтверждено кодом d:\Work\LMS)

### 1.1. Иерархия
Одна модель `Courses`, иерархия — граф `course_parents` (many-to-many,
произвольная глубина, порядок `order_number` управляется триггером БД — не
задавать). «Лист» = курс без детей. «Подкурс 1-го уровня» = прямой ребёнок.
Зависимости — `course_dependencies` (`course_id` требует `required_course_id`).
Идемпотентность: курсы — по `course_uid`, задания — по `external_uid`,
материалы — по `(course_id, external_uid)`, сложность — `difficulties.uid/code`.

### 1.2. Артефакты выгрузки (генерировать ОБА)

**A. JSON под bulk-API (основной).**
- Курсы: список `CourseCreate` — `title`, `course_uid`, `description`,
  `access_level` (`self_guided|auto_check|manual_check|group_sessions|personal_teacher`),
  `is_required`, иерархия через `parent_course_uid`, зависимости —
  `required_courses_uid`.
- Задания: массив `TaskUpsertItem` под `POST /api/v1/tasks/bulk-upsert`:
```json
{
  "external_uid": "PY-EGE-T1-03-SC",
  "course_uid": "PY-EGE-T1",
  "difficulty": "EASY",
  "max_score": 10,
  "task_content": {
    "type": "SC",
    "stem": "Что выведет print(2**3)?",
    "options": [
      {"id": "A", "text": "6", "is_active": true},
      {"id": "B", "text": "8", "is_active": true}
    ]
  },
  "solution_rules": { "max_score": 10, "correct_options": ["B"], "scoring_mode": "all_or_nothing" }
}
```
  Для `SA/SA_COM` — `solution_rules.short_answer.accepted_answers`; для `TA` —
  `solution_rules.text_answer.rubric` (список `{id,title,max_score}`).
- Материалы: `course_uid`, `external_uid`, `title`, `type`
  (`text|video|audio|image|link|pdf|office_document|script|document`),
  `content` (для text → `{text, format: markdown}`), `order_position`.

**B. Google Sheets раскладка (дополнительно, для ручного импорта).**
- Лист `Courses`: `course_uid | title | access_level | description |
  parent_course_uid | order_number | required_courses_uid | is_required`
- Лист `Tasks`: `external_uid | type | stem | options ("A: .. | B: .. [пояснение]") |
  correct_answer ("A" / "A,B") | accepted_answers ("вар:10 | вар2:5") |
  max_score | course_uid | difficulty_code | code | title | prompt`
- Лист `Materials`: `course_uid | external_uid | title | type | url |
  description | caption | order_position | is_active`

### 1.3. Difficulty (фиксированный справочник)
`theory/THEORY(1)`, `easy/EASY(2)`, `normal/NORMAL(3)`, `hard/HARD(4)`,
`project/PROJECT(5)`. Маппинг бакетов — см. `assignment-rules.md` § 2.

## Часть 2. WP-проекция — модель «Навигатор» (подтверждено на victor-komlev.ru)

Сайт = WordPress + Elementor, **LMS-плагина нет**. «Курс» = ручная разметка:
посты + страница-навигатор. Зрелая модель (по образцу ЕГЭ-курса):

**L1 — страница-навигатор (оглавление курса)**
- H1: `«<Курс>. Навигатор по курсу.»`
- slug: `/<kurs>-navigator-po-kursu/`
- линейный пронумерованный список разделов (анкор + кнопка «Перейти»)
- финальный CTA «Следующий урок» → первый раздел

**L2 — страница темы/раздела** — slug `/<kurs>-tema-{N}-{slug}/`, фикс. порядок
блоков:
1. H1 `«<Курс>. Тема N. <название>»`
2. **Краткий план раздела** (маркированный список целей)
3. 📖 **Текстовые уроки** (ссылки на L3-посты, допустим deep-link `#anchor`)
4. 👀 **Видеоуроки**
5. ❓ **Контрольные вопросы** (SC/SA из заданий)
6. 💻 **Задания** (практика — сюда задания с критериями)
7. **Итоги раздела**
8. Нижняя навигация: **«Назад» / «Вперёд» / «На главную»** + счётчик
   **«Раздел N из M»** (M фиксируется в плане заранее, заполняется вручную)

**L3 — пост-урок** (обычный WP-пост `/{slug}/`): заголовок → теория →
примеры кода → скриншоты → «Задание» → опц. CTA «Изучить с наставником» →
видео. Якоря `#section` на ключевые секции (для deep-link из L2).

**Обязательно учесть:** нет авто-навигации/прогресса — порядок и общее M
зафиксировать в плане; breadcrumbs курса через `/shkolnye-programmy/...` (не
блоговый рубрикатор); single-page (мини-курс) — только для коротких узких тем;
плоский «лендинг+посты» без возврата к программе — не использовать для новых.

## Часть 3. Таблица соответствия LMS-узел ↔ WP-страница

| План (узел) | LMS | WP |
|---|---|---|
| Корневой курс | `Courses` root, `course_uid` | страница-навигатор L1 |
| Подкурс 1-го уровня (Фаза/Модуль) | child `Courses` + `course_parents` | раздел в навигаторе + L2 страница темы |
| Лист (тема/урок) | child `Courses` (без детей) | L2-блок 📖 + L3 пост-урок |
| Материал | `materials` (text→markdown) | контент L3-поста |
| Задание | `tasks` (task_content/solution_rules) | L2-блок 💻 / ❓ |
| Зависимость прохождения | `course_dependencies` | порядок «Раздел N из M» + Назад/Вперёд |

Один узел плана → одновременно строка LMS-выгрузки и страница WP. `course_uid` /
`external_uid` — общий ключ обеих проекций (стабильный, идемпотентный).
