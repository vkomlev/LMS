# tsk-110 — Аудит LMS: media/attachments (2026-06-04)

**Skill:** db-check · **БД:** learn (public) · **MCP:** learn_public_db  
**Задача:** tsk-110 · **Этап:** 1 из 13 (свежий срез по источникам)

---

## Целевая БД

- Сервер: localhost:5432, база `learn`, схема `public`
- Таблица: `tasks` (столбцы `external_uid`, `task_content jsonb`)
- Доступ: MCP read-only

---

## Общий состав (всего задач по источникам)

| Бакет | Всего | Шаблон external_uid |
|---|---|---|
| other (ручные/нераспознанные) | 1 287 | нет `ext:` и `wp_nav:` |
| wp_nav | 1 080 | `wp_nav:*` |
| ext_other (kompege 137, d4 100, yandex 7) | 244 | `ext:*` без polyakov/sdamgia |
| sdamgia | 186 | `ext:*:sdamgia:*` |
| polyakov | 57 | `ext:*:polyakov:*` или `ext:polyakov:*` |
| no_external_uid | 2 | NULL |
| **Итого** | **2 856** | |

---

## Дефект 1 — [IMAGE REMOVED] в тексте задачи

| Бакет | Кол-во задач | Вариант плейсхолдера |
|---|---|---|
| sdamgia | **183 / 186** | `[IMAGE REMOVED: no_src]` |
| polyakov | **5 / 57** | `[IMAGE REMOVED: None]` |
| wp_nav | 0 | — |
| ext_other | 0 | — |

### Sdamgia — детали

- В `stem` сохраняется **вся страница** (header + nav + left_column + task body).
- `[IMAGE REMOVED: no_src]` — это **декоративные иконки** header/nav
  (`logo.svg`, `inf.png`, кнопки соцсетей), у которых нет `src` после прохождения
  через `url_filter.py`.
- **Настоящие изображения задания** (`get_file?id=...`) присутствуют как абсолютные
  URL в HTML и НЕ теряются фильтром — но в `stem_images` не занесены.
- Все 183 задачи с `[IMAGE REMOVED]` одновременно содержат `get_file?id=...` в stem.
- Нужно: выбирать только тело задачи (`.prob_maindiv`/`.pbody`), а `get_file?id=...`
  переносить в `stem_images`.

### Polyakov — детали

Уникальные topicId с `[IMAGE REMOVED]`:

| topicId | Версий в БД | Тип дефекта |
|---|---|---|
| 4406 | 5 | `[IMAGE REMOVED: None]` + файл 3-40.xls |
| 7613 | 2 | `[IMAGE REMOVED: None]` + файл 3-148.xls |
| 7442 | 2 | `[IMAGE REMOVED: None]` (только схема графа) |

Причина: JS `changeImageFilePath` на сайте kpolyakov.spb.ru меняет `img[src]` в
runtime; статический HTTP-парсер получает пустой `src` → `[IMAGE REMOVED: None]`.

---

## Дефект 2 — Файл упомянут в тексте, нет attached_file_paths

Критерий: `stem` содержит «файл» / `.xls` / `.xlsx` И `attached_file_paths` пустой.

| Бакет | Задач |
|---|---|
| wp_nav | **312** |
| other (ручные) | 193 *(скорее всего не дефект CB)* |
| sdamgia | **70** |
| ext_other | **55** (kompege 27, d4 26, yandex 2) |
| polyakov | **13** |

Polyakov — уникальные topicId с файлом без attachment:
`4406`, `7613`, `6757`, `2807`, `2380` (по 2-5 версий каждый).

---

## Дефект 3 — Тихая потеря изображений в wp_nav

| Состояние `stem_images` | Кол-во |
|---|---|
| `[]` (пустой массив, ключ есть) | **1 080 / 1 080** |
| Непустой массив | 0 |
| Ключ отсутствует | 0 |

Из 1 080 задач `wp_nav` — **40** содержат в тексте markdown-изображения формата
`![...](https://...)` (внешние Yandex/другие CDN URL).
Остальные 1 040 — в тексте нет картинок вовсе, потеря неверифицирована.

Вывод: для 40 задач `stem_images` должен содержать URL из markdown, но он пустой.
Для остальных требуется проверка исходного WP-контента на CB-стороне.

---

## Состояние attached_file_paths (заполнен)

- `attached_file_paths` непустой — только у **132 задач** бакета `other` (ручные/импорт).
- Ни у одной задачи с `external_uid` (`polyakov`, `sdamgia`, `wp_nav`, `ext_other`)
  `attached_file_paths` **не заполнен**.
- `stem_images` непустой — у 0 внешних задач.

Вывод: passthrough CB→LMS для этих полей **не работает** ни для одного внешнего источника.

---

## Сводная таблица дефектов (приоритет исправления)

| Приоритет | Бакет | Кол-во задач | Тип дефекта | Root cause |
|---|---|---|---|---|
| P0 | sdamgia | 183 | `[IMAGE REMOVED]` + нет stem_images | Вся страница в stem, нет body-selector |
| P0 | sdamgia | 70 | Файл упомянут, нет attachment | Не реализован download `get_file?id=` |
| P0 | polyakov | 5 | `[IMAGE REMOVED]` + нет attachment | JS render (changeImageFilePath) |
| P1 | polyakov | 13 | Файл упомянут, нет attachment | Relative URL (3-40.xls) не резолвится |
| P1 | wp_nav | 40 | Тихая потеря (md-images есть, stem_images пуст) | CB не извлекает URL из markdown |
| P2 | wp_nav | 312 | Файл упомянут, нет attachment | Нет CB downloader |
| P2 | ext_other | 55 | Файл упомянут, нет attachment | Нет CB downloader |

---

## Инварианты

- ✅ Схема `tasks` содержит `task_content jsonb` с полями `stem_images`, `attached_file_paths`, `has_attached_file`
- ✅ LMS passthrough работает для `other` (132 задачи с непустым `attached_file_paths`)
- ❌ Ни одна внешняя задача (polyakov/sdamgia/wp_nav/ext_other) не имеет заполненного `stem_images`
- ❌ Ни одна внешняя задача не имеет заполненного `attached_file_paths`
- ⚠️ Все 1 080 `wp_nav` задач имеют `stem_images: []` — ключ есть, значение пустое

---

## SQL для воспроизведения

```sql
-- Разбивка по бакетам
SELECT
  CASE
    WHEN external_uid LIKE 'ext:polyakov%' OR external_uid LIKE 'ext:%:polyakov:%' THEN 'polyakov'
    WHEN external_uid LIKE 'ext:%:sdamgia:%' OR external_uid LIKE 'ext:sdamgia%' THEN 'sdamgia'
    WHEN external_uid LIKE 'wp_nav:%' THEN 'wp_nav'
    WHEN external_uid LIKE 'ext:%' THEN 'ext_other'
    ELSE 'other'
  END AS source_bucket,
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE (task_content->>'stem') ILIKE '%[IMAGE REMOVED%') AS image_removed,
  COUNT(*) FILTER (WHERE
    ((task_content->>'stem') ILIKE '%файл%' OR (task_content->>'stem') ILIKE '%.xls%')
    AND (task_content->'attached_file_paths' IS NULL OR task_content->'attached_file_paths' = '[]'::jsonb)
  ) AS file_mention_no_attach,
  COUNT(*) FILTER (WHERE
    task_content ? 'stem_images' AND task_content->'stem_images' = '[]'::jsonb
  ) AS stem_images_empty_array
FROM tasks
GROUP BY 1
ORDER BY total DESC;
```

---

## Риски и рекомендации

1. **sdamgia**: главный приоритет — исправить selector тела задачи (`.prob_maindiv`) и
   добавить сбор `get_file?id=...` в `stem_images` через CAS downloader.
2. **polyakov**: нужен Playwright re-fetch для topicId с `[IMAGE REMOVED]`; отдельно —
   relative URL resolve для `*.xls` ссылок.
3. **wp_nav**: разобрать 40 задач с md-images (извлечь URL в `stem_images`);
   для attachment — нужна CB-логика скачивания файлов.
4. **Блокер**: LMS не подтверждает `/media/<sha>` маршрут — до live re-import обязателен
   HTTP smoke (AC-4 из спека).
5. **Re-import**: 57 + 186 + 1080 = 1323 внешние задачи потенциально затронуты;
   update-only по `external_uid`, backup обязателен.

---

*Следующий этап: `change-plan-architect` — ADR-0040 (CAS URL контракт, LMS serving).*
