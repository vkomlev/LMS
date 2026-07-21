# tsk-356: восстановление битого stem у 50 заданий (JS-заглушка / шапка сайта вместо условия)

Побочная находка tsk-354. `task_content->>'stem'` у части заданий содержал служебный
текст сайта-источника вместо условия задачи.

## Итоговый охват (после полного пересчёта, шире исходной заметки в 40 шт.)

| Группа | Кол-во | external_uid | Способ восстановления |
|---|---|---|---|
| kompege JS-заглушка | 10 | `ext:calib:kompege:20260525:*` | Скопировать `stem` из твина `ext:d4:kompege:20260602:*` (тот же числовой ID kompege) — уже в БД, курс 142, 0 attempts/results на битых |
| sdamgia — шапка портала | 40 | `wp_nav:*` (курсы 138-165) | Живой ре-скрейп: обычный HTTP GET + парсинг `div.pbody` (JS не требуется, метод проверен). 4 из 40 (id 3760,3792,3794,3796, курс 155) имеют task_results — фикс не трогает результаты, только текст условия |
| yandex SPA-каркас | 1 | `wp_nav:7:017606ca` (id=3759) | Требует authenticated API (метод tsk-100) или Claude in Chrome — НЕ включено в этот apply, отдельный шаг |

**Итого в этом apply: 50 заданий** (10 + 40). Задание 3759 (yandex) — отдельно.

## Метод (sdamgia)

`curl`-эквивалент (`urllib.request`, UA заголовок) → decode UTF-8 → найти `class="pbody"`,
извлечь родительский `<div>` с балансировкой вложенных тегов → снять внешний div,
обернуть в `<html><body>...</body></html>` (формат подтверждён на примере рабочего
`ext:d4:sdamgia` задания). Все 40/40 успешно, 2 содержат `<img>` с формулами (уже абсолютные
URL на `ege.sdamgia.ru`, без правки путей).

## Проверка перед записью

- Триггер `trg_set_task_order_position` (BEFORE UPDATE) — реагирует только при изменении
  `order_position`; правка `task_content` его не затрагивает — подтверждено чтением функции.
- `task_content` — `NOT NULL jsonb`, ограничений на `stem` нет.
- Бэкап старых `stem` — `reviews/2026-07-21-tsk356-old-stems-backup.json` перед apply.

## SQL применения (в транзакции, DBCHECK_OK=1)

```sql
BEGIN;
-- 10 kompege: копия stem твина
UPDATE tasks t SET task_content = jsonb_set(t.task_content, '{stem}', to_jsonb(g.stem))
FROM (VALUES
  (2947,2084),(2948,2088),(2949,2093),(2950,2092),(2951,2091),
  (2952,2089),(2953,2090),(2954,2086),(2955,2085),(2956,2087)
) AS pair(broken_id, good_id)
JOIN tasks gt ON gt.id = pair.good_id
CROSS JOIN LATERAL (SELECT gt.task_content->>'stem' AS stem) g
WHERE t.id = pair.broken_id;

-- 40 sdamgia: новый stem из скрипта (параметризованно, см. apply-скрипт)
-- UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', to_jsonb($1::text)) WHERE id = $2;

-- верификация
SELECT id, left(task_content->>'stem', 60) FROM tasks WHERE id IN (2947,2948,...,3802);
COMMIT;
```

Скрипт: [scripts/fix_broken_scrape_stem_tsk356.py](../scripts/fix_broken_scrape_stem_tsk356.py)
(реализует то же самое через SQLAlchemy/asyncpg, с встроенной верификацией и rollback при провале).

## Инцидент при выполнении: локальный `.env` ≠ прод

Первый запуск `--apply` (без ssh, локально из `D:\Work\LMS`) отрапортовал `COMMIT — изменения
сохранены`, но независимая проверка через MCP `learn_prod_db` показала **50 из 50 всё ещё
битых**. Причина: локальный `.env` → `DATABASE_URL=postgresql+asyncpg://postgres:***@localhost:5432/Learn`
(dev-БД), а не прод (`5.42.107.253`). Скрипт реально закоммитил в **локальную dev-базу**,
а не в прод — прод остался нетронутым, никакого риска не было, но и фикса тоже не было.
Установленный паттерн (см. `scripts/reorder_courses_by_difficulty_tsk345.py`) — такие
one-off скрипты запускаются **на самом сервере** (`ssh lms-spw-vds`, `/opt/lms`, `.env`
там уже смотрит на прод), не локально. Пересобрано верно:

```
scp scripts/fix_broken_scrape_stem_tsk356.py lms-spw-vds:/tmp/
scp reviews/2026-07-21-tsk356-sdamgia-extracted.json lms-spw-vds:/tmp/
ssh lms-spw-vds "cp /tmp/... /opt/lms/scripts/... && cp /tmp/... /opt/lms/reviews/... && chown app:app ..."
ssh -tt lms-spw-vds "sudo -u app bash -c 'cd /opt/lms && PYTHONIOENCODING=utf-8 DBCHECK_OK=1 ./venv/bin/python scripts/fix_broken_scrape_stem_tsk356.py --apply'"
```

## Итог apply (реальный прод, 2026-07-21)

- `kompege UPDATE rowcount = 10` (ожидалось 10)
- `sdamgia UPDATE rowcount = 40` (ожидалось 40)
- Встроенная верификация скрипта: `0 из 50 всё ещё содержат служебный текст — OK`
- **Независимая верификация через MCP `learn_prod_db`** (отдельный read-only канал от
  канала записи): `still_broken = 0, checked = 50` — подтверждено.
- Образцы после фикса: id 2948 → `<p>Определите количество цифр с числовым значением,
  превышающим 9, в 27-ричной записи числа...` (реальное условие); id 3760 → `<html><body><p
  class="left_margin">Для ко­ди­ро­ва­ния не­ко­то­рой по­сле­до­ва­тель­но­сти...`
  (реальное условие sdamgia).
- Побочный эффект: тот же фикс по ошибке (см. инцидент выше) применился и к локальной
  dev-БД (`localhost/Learn`) — оставлено как есть (безвредно, приводит dev в консистентность
  с прод для тех же 50 заданий), не откачивалось.

## Не входит в этот apply

Задание id=3759 (`wp_nav:7:017606ca`, курс 158, source `education.yandex.ru/ege/training/7/task/1`)
— страница SPA, обычный HTTP не отдаёт условие (проверено WebFetch: только каркас
приложения). Нужен authenticated API (метод tsk-100, `/api/v5/gpttr get_task_by_id`) или
Claude in Chrome под учёткой Виктора — отдельный follow-up, 0 attempts/results, риска нет.
