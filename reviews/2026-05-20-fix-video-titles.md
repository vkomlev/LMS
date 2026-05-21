# tsk-004 Этап 1.2 — восстановление заголовков видеоматериалов

**Дата:** 2026-05-20
**Скилл:** `/db-check`
**Связано:** [tsk-004 «Порядок в LMS»](https://github.com/vkomlev/work-root/blob/master/tasks/tsk-004-poryadok-v-lms.md) — продолжение после Этапа 1.1.

## Контекст

В `Learn.public.materials` 223 видеоматериала имели заголовок просто «Видеоурок» (пример: id 458, 459, 465). Дефект пришёл из WP-импорта в ContentBackbone — `content_hub.material.metadata.title` тоже хранит «Видеоурок» (т.е. деградация на импортере, а не в LMS).

## Найденный источник правильного title

`content_hub.source_item.body` хранит исходный HTML страниц курсов с victor-komlev.ru. Каждое VK-видео там оформлено как:

```html
<li>☝️ <a href="https://vk.com/video-53400615_456239780" target="_blank" rel="noopener">Знакомство с IDLE Python. Первая программа</a></li>
```

URL ровно совпадает с `materials.content.sources[0].url`, текст в `<a>` — правильный заголовок. **VK API не требуется.** Альтернатива через VK API была проверена и отклонена: `VK_ACCESS_TOKEN` в `D:/Work/VK_Importer/.env` истёк, `refresh_token` flow требует валидного `device_id` (operator-handoff не нужен — нашли автономный путь).

## Маппинг

- `materials.external_uid = 'wp:mat:<author>:<course-slug>:<position>'` (223/223 распарсились).
- По `<author>:<slug>` находим `content_hub.source_item.global_uid = 'wp:course:<author>:<slug>'`.
- Все 33 нужных `source_item` нашлись (33/33).

## Стратегия в скрипте

Один проход, две ветки:
1. **220 материалов с URL** — regex по `<a href="...video<VK_ID>...">…</a>` в body → новый title. Обновляем только `title`.
2. **3 материала без URL** (id 631-633, `navigator-po-zadaniyu-26-ege`) — `content.sources=[]`. Парсим весь HTML страницы курса в порядке появления VK-ссылок, сопоставляем по `order_position` из `external_uid`. Обновляем `title` И `content.sources` (добавляем URL).

Обновление идемпотентно: `WHERE title='Видеоурок'`. Повторный запуск не сломает уже починенные строки.

## Применено

```
=== Fix video titles — APPLY (COMMIT) ===
Кандидаты: 223
Курсов-источников: 33
Загружено source_item: 33 (из 33 запрошенных)

Найдено новых заголовков (URL есть):     220
Дополнение URL+title из HTML (URL пуст): 3
Пустой URL И ничего в HTML:              0
Без source_item:                         0
URL есть, в HTML нет:                    0

После UPDATE: всё ещё с title='Видеоурок': 0
```

## Верификация

Через независимый MCP read-only канал (`mcp__learn_public_db__query`):
- `still_bad_videourok = 0` (было 223)
- `video_total = 319` без изменений
- `video_with_url = 319` (стало = total; 3 пустых sources заполнены)
- Спот-проверки оператора:
  - id 458 → «Инструкции, переменные, input()»
  - id 459 → «Переменные разных типов»
  - id 465 → «Ошибки в Python»
  - id 631 → «Инструкция по работе с файлами на ЕГЭ»
  - id 632 → «Как правильно указать путь до файла…»
  - id 633 → «Работа с файлами в задании 26»

## Артефакты

- [scripts/fix_video_titles.py](../scripts/fix_video_titles.py) — переиспользуемый, dry-run по умолчанию
- [reviews/2026-05-20-fix-video-titles.diff](2026-05-20-fix-video-titles.diff)

## Risks / Follow-ups

- **Root cause не исправлен:** WP-импортер в ContentBackbone берёт title='Видеоурок' из метаданных WP вместо текста `<a>` рядом с VK-URL. На следующем прогоне импорта дефект вернётся. Согласовано с оператором — отдельный этап tsk-004 (фикс в репозитории ContentBackbone).
- Скрипт **идемпотентен** (`WHERE title='Видеоурок'`), поэтому можно безопасно гонять его как временный workaround после каждого импорта до починки самого импортера.
- Скрипт читает из двух схем одной БД (`public` + `content_hub`). Подключение — через `app.db.session.async_session_factory` (search_path=public; cross-schema JOIN указывает схему явно).
