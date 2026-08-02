# tsk-089: kpolyakov-картинки HARD-блока ЕГЭ — фикс 404 (вариант B, CAS)

## Контекст

tsk-089 (создана 2026-05-24 из pilot tsk-004 Phase 6.6, зависла до 2026-08-03).
Операторская сверка 2026-08-03 показала актуальный масштаб: **19 активных
заданий**, **9 уникальных файлов** (все `.gif`), сосредоточены в HARD-блоке ЕГЭ
(курсы 1379/1380/1381/1396/1398, родитель 1378 → 112 «ЕГЭ по информатике»).
`task_content->>'stem'` содержал `<img src="NNNN.gif">` — голое имя файла без
хоста, гарантированный 404 в SPW.

Выбранный вариант — **B (CAS)**: скачать в content-addressed хранилище
(sha256) + прод-S3, переиспользуя инфраструктуру tsk-520/526/536. Вариант A
(hotlink на kpolyakov) отклонён operator'ом заранее как противоположность
того, от чего только что явно ушли в tsk-520.

## Находка: путь картинок у kpolyakov — не тот, что в постановке задачи

Изначальная постановка задачи (и первая попытка) исходила из
`https://kpolyakov.spb.ru/cms/files/<имя>` — тот же базовый путь, что и для
файлов-приложений (`<a href>`). Оба варианта (`cms/files/<имя>` и
`cms/files/ege-proc/<имя>` и т.п.) дали **404**.

Причина — на странице `kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=<id>`
`<img src>` и `<a href>` резолвятся через **два разных** скрытых поля:

```html
<input type="hidden" name="imagePath" id="imagePath" value="../../cms/images/"/>
<input type="hidden" name="filePath" id="filePath" value="../../cms/files/"/>
```

```js
function changeImageFilePath(html) {
  var imagePath = document.getElementById('imagePath');
  html = html.replace(/img src="([^:])/g, 'img src="' + imagePath.value + '$1');
  ...
  var filePath = document.getElementById('filePath');
  html = html.replace(/a href="([^:])/g, 'a href="' + filePath.value + '$1');
  ...
}
```

Правильный абсолютный путь картинок — **`https://kpolyakov.spb.ru/cms/images/<имя>`**
(проверено на всех 9 файлах — HTTP 200, `Content-Type: image/gif`). `cms/files/`
остаётся верным только для файлов-приложений (`.xls` через `<a href>`).

Источник `topicId` для каждого задания — поле `task_content->>'source_url'`
(`https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=NNNN`),
записанное оригинальным импортом `wp_nav_import.py` (tsk-103) — в
ContentBackbone `content_hub` эти задачи не индексировались (импорт шёл мимо
CB pipeline, отдельным одноразовым `wp_nav`-путём), поэтому `source_item`/
`content_hub.task` пусты для этих 19 id.

## Изменения

| Файл | Суть |
|---|---|
| `ContentBackbone/scripts/tsk089_kpolyakov_hard_images.py` | Шаг 1: скачивает 9 gif через `download_to_cas` (уже с фиксом ContentType из tsk-526/536) в CAS + прод-S3, верифицирует публичную доступность через `/api/v1/media/<sha>`. Dry-run по умолчанию, `--apply` для реальной записи. |
| `LMS/scripts/tsk089_hard_block_stem_update.py` | Шаг 2: `/db-check` протокол — читает 19 задач `FOR UPDATE` в транзакции, заменяет `src="NNNN.gif"` на `src="/api/v1/media/<sha_ext>"`, dry-run откатывает, `--apply` коммитит + верифицирует в той же транзакции. |
| `ContentBackbone/reviews/tsk089-hard-block-images/images_plan.json` | План шага 1: filename → sha_ext, public_ok=true для всех 9. |

Новый SPW/LMS endpoint не понадобился — `GET /api/v1/media/{sha_ext}`
(ADR-0040/0047) уже существует и уже отдаёт `.gif` из allowlist через 307 на
прод-S3.

## Mapping filename → sha_ext

| filename | sha_ext | размер |
|---|---|---|
| 5436.gif | 944ad1f560ed5ee6069e26b96198eb8313327b91b79565ac15acda3fad73d28c.gif | 7407 |
| 5516.gif | 5e531e31cc6a63feead54596af00014a7f45acb05cc45e4276add6a48882bdd8.gif | 3439 |
| 5863.gif | fae3cda27efd387ead4a3ca43ca11dd26fd36a4593d526d85d2af6c7ef066872.gif | 3984 |
| 5927.gif | c830dda076109208b8a8cb11d6d1f689a4da74f0b9599fde2b6c5fdec282afb2.gif | 1828 |
| 6180.gif | c43a9a40a3240609fa9ba09c50f9cc311114b1dd8787a6392ca68493c170f49d.gif | 3152 |
| 6294.gif | ed27803342b15645ace43e09514511a9f9bce6a521734d1e9b689034f1cf44bd.gif | 2887 |
| 7613.gif | 63f7ef1f38bf018832e234be839c867b053def74fd1b2e18c794f76e9890c780.gif | 17539 |
| 7901.gif | f77ff0287687e5c0ba82be2710d2fefab137fa57519d5078f18267cb6156520e.gif | 2208 |
| 7902.gif | 02a4c853e3f45539409f1ca1f6dee14dd687e348d0dc15f0bd971fc56577e506.gif | 2193 |

19 задач: 4299, 4337, 4342, 4344, 4345, 4351, 4378, 4384, 4419, 4456, 4457,
4458, 4459, 4475, 4476, 4477, 4478, 4493, 4494 (курсы 1379/1380/1381/1396/1398).

## DB Findings (MCP)

- Подтверждающий SQL до правки (regex по `stem`) дал ровно 19 строк — совпало
  с операторским списком 1:1.
- После `--apply`: независимая проверка через `mcp__learn_prod_db` (отдельный
  read-only запрос вне скрипта, что писал) — все 19 строк содержат
  `/api/v1/media/`, ни одного голого `src="NNNN.gif"` не осталось.
- Внутритранзакционная верификация в самом скрипте (после `UPDATE`, до
  `COMMIT`) — тот же результат.

## Validation Results

| Критерий | Результат |
|---|---|
| AC-1 (вариант выбран) | PASS — B, зафиксировано в tsk-089 |
| AC-2 (implementation) | PASS — CAS-скрипты, endpoint уже был |
| AC-3 (regression pilot) | PASS — id 4299 и 4342 открыты живьём в SPW прод (`claude-in-chrome`), картинка рендерится (не битая иконка) |
| AC-4 (масштаб) | PASS — 19/9, не 1040; disk space тривиален (~44 КБ суммарно) |
| `pytest -q` | 1534 passed, 11 skipped, 0 failed (код не менялся, один эталонный прогон) |

## Live Regression (скриншоты)

- id 4299 (`/courses/lms%3Atsk347%3Ahard%3A140/task/wp_nav%3A1%3Aa8f2a9a9`) —
  граф дорог отрендерен корректно.
- id 4342 (`/courses/lms%3Atsk347%3Ahard%3A149/task/wp_nav%3A22%3A279d6b62`) —
  таблица процессов отрендерена корректно.

## Risks / Follow-ups

- Изменения — чисто данные (`tasks.task_content`), код LMS/SPW/CB не менялся →
  **деплоя на прод не было**, оба скрипта писали напрямую в прод-БД
  (`5.42.107.253/learn`) и прод-S3 тем же паттерном, что tsk526/536.
- `wp_nav`-импорт (tsk-103) не пишет в `content_hub` ContentBackbone — при
  повторении подобной задачи для других `wp_nav:*` заданий источник
  (`task_content->>'source_url'`) остаётся единственной зацепкой на
  оригинальный URL; `content_hub.source_item`/`task` для них пусты.
- `cms/images/` vs `cms/files/` — стоит зафиксировать в
  `ContentBackbone/docs/ai/ege-import-playbook.md` (сейчас там только
  `changeImageFilePath` упомянут применительно к файлам-приложениям, не к
  картинкам) — дельта не входила в scope tsk-089, оставляю как follow-up.
