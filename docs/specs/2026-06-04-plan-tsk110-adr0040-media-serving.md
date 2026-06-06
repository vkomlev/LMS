# Изменение: ADR-0040 — CAS URL + LMS media serving (tsk-110 этап 2)

**Дата:** 2026-06-04  
**Статус:** Ready for execution  
**Задача:** tsk-110  
**Этап спека:** 2 из 13 (change-plan-architect)

---

## Целевая возможность

LMS умеет отдавать студенту медиафайлы и файлы-вложения внешних задач
(изображения из sdamgia/polyakov/wp_nav, .xls/.ods из polyakov), скачанные
ContentBackbone через CAS-хранилище с дедупликацией по sha256. CB кладёт
файлы в локальный CAS, пишет LMS-relative URL в `stem_images`/`attached_file_paths`,
LMS отдаёт их через защищённый endpoint.

---

## Текущее состояние

| Точка | Состояние |
|---|---|
| LMS `/media/` | **Нет** — ни StaticFiles, ни endpoint |
| LMS `FileResponse` | Есть для messages (`/api/v1/messages/{id}/attachment`) и materials (`/api/v1/materials/download/{file_id}`) — pattern проверен |
| LMS config | `messages_upload_dir`, `materials_upload_dir` — переменные среды, автосоздание директорий |
| CB CAS | Директория `data/media_store/` существует (упомянута в `migrate_to_cas.state.json`); нет помощника загрузки для внешних URL |
| CB payload | `attached_file_paths: list[str]` (ADR-0033), `stem_images: list[str]` — поля есть в schema, но не заполняются для web-задач |
| ADR head | CB: 0039 — следующий = **0040** |

---

## Карта влияния

```
ContentBackbone (CB)                    LMS
  downloader/cas.py  ──── writes ────▶  <CAS_MEDIA_ROOT>/ab/abc123.png
  adapter/builder.py ──── payload ───▶  task_content.stem_images["/api/v1/media/abc123.png"]
                                                 │
  bulk-upsert (POST /tasks/import) ◀─── import ─┘
                                                 │
  GET /api/v1/media/{sha256hex}  ◀── SPW/TG_LMS ─┘
```

Затронуты проекты: **CB** (downloader, adapter, exporter), **LMS** (новый endpoint + config),
**SPW** (потребляет stem_images — passthrough, без изменений), **TG_LMS** (аналогично).

---

## Пробелы и блокеры

| # | Пробел | Статус | Блокер? |
|---|---|---|---|
| G1 | LMS endpoint `/api/v1/media/{sha256hex}` не реализован | **ОТКРЫТ** | **ДА** — нельзя делать re-import до этого |
| G2 | `CAS_MEDIA_ROOT` не задан в `.env` LMS | **ОТКРЫТ** | ДА |
| G3 | CB CAS downloader для HTTP-ресурсов не существует | **ОТКРЫТ** | ДА |
| G4 | Форма URL в `stem_images` (LMS-absolute vs sha-only) не утверждена | **ОТКРЫТ** | ДА — блокирует G3 |
| G5 | CB `migrate_to_cas.state.json` — назначение неясно, возможен конфликт | Нужна проверка | Нет |
| G6 | Права доступа к `/api/v1/media/` — public или auth? | **ОТКРЫТ** | Нет (рекомендация ниже) |

---

## Допущения и открытые вопросы (закрытые ниже)

**Q1: Форма URL в payload `stem_images`?**  
→ **Решение:** LMS-absolute path `/api/v1/media/{sha256hex}` (с расширением: `abc123.png`).
Прямая совместимость с SPW/TG (`<img src=...>`), не требует дополнительного resolve на
клиенте. CB записывает полный путь — LMS конструирует `settings.base_url + path` при экспорте.

**Q2: Нужна ли аутентификация на `/api/v1/media/`?**  
→ **Решение:** Без авторизации (public). Stem-изображения уже отдаются в stem задачи
через guest-endpoint; закрывать их за auth = UX-потеря без выгоды. Имя файла —
sha256-хеш (64 hex), перебор непрактичен. Ограничение: только allowlisted content-type,
root-jail, 404 на missing.

**Q3: Структура CAS на диске?**  
→ **Решение:** `<CAS_MEDIA_ROOT>/<sha[:2]>/<sha>.<ext>` (шардинг по первым 2 символам хеша).
Пример: `data/media_store/ab/abc123def456.png`. Это стандартный git-style sharding,
дедупликация по содержимому, без риска path traversal (sha — только hex).

**Q4: Куда CB пишет, откуда LMS читает (shared path)?**  
→ **Решение:** Обе службы работают на одной машине (dev: Windows; prod: один VPS Y-7).
Единый путь через переменную среды:
- CB: `CAS_MEDIA_ROOT=D:\Work\ContentBackbone\data\media_store` (уже существует)
- LMS: `CAS_MEDIA_ROOT=D:\Work\ContentBackbone\data\media_store` (новая переменная)

При переносе на VPS — shared volume или S3-compatible. Отдельный ADR при Y-7.

**Q5: Нужен ли StaticFiles или достаточно FileResponse?**  
→ **Решение:** `FileResponse` через FastAPI endpoint — единообразно с существующим паттерном
(messages, materials), позволяет добавить content-type whitelist, 404, root-jail без лишней
прослойки. StaticFiles — ускорение для CDN (backlog, вне tsk-110 scope).

---

## Решение по дублированию

CB и LMS — разные кодовые базы с разными языками (Python+CLI и Python+FastAPI). CAS
downloader — в CB, endpoint — в LMS. Общая точка — только путь к директории через
`CAS_MEDIA_ROOT`. Дублирования нет.

---

## Этапы внедрения

### Фаза A — ADR-0040 + LMS media endpoint (блокер, ~1 день)

**Шаг A1.** Написать `docs/adr/0040-media-cas-url-lms-serving.md` в CB (оформить
принятые решения Q1-Q5 как ADR).

**Шаг A2.** В LMS:
- Добавить `CAS_MEDIA_ROOT` в `app/core/config.py` (Path, с `mkdir` аналогично uploads)
- Создать `app/api/v1/media.py` — endpoint `GET /api/v1/media/{sha_ext}`:
  - Разобрать `sha_ext` как `{sha256_hex}.{ext}` (regex: `[0-9a-f]{64}\.(png|jpg|jpeg|gif|webp|svg|pdf|txt|ods|odt|xlsx|xls|csv)`)
  - Path resolve: `settings.cas_media_root / sha256_hex[:2] / sha_ext`
  - Root-jail: убедиться `resolved.is_relative_to(settings.cas_media_root)` → 400 если нет
  - 404 если файл отсутствует
  - `FileResponse` с `media_type` из allowlisted типов по расширению
  - Нет auth — endpoint public
- Зарегистрировать router в `app/main.py`
- Написать 5+ тестов: ok, missing, traversal, wrong ext, wrong sha format

**Шаг A3.** Smoke: положить тестовый файл в `CAS_MEDIA_ROOT/ab/abc...png`, проверить
`curl http://localhost:8000/api/v1/media/abc...png` → HTTP 200.

**Предусловие:** нет  
**Проверка готовности (AC-4 из спека):** HTTP 200 для реального CAS-файла, HTTP 404 на
missing, HTTP 400 на traversal-попытку.

---

### Фаза B — CB CAS downloader (после A, ~1 день)

**Шаг B1.** Создать `monolith/external_tasks/media/cas_downloader.py`:
- `download_to_cas(url: str, cas_root: Path, session: httpx.AsyncClient) → str | None`
- Проверки: schema http/https, hostname в `URL_ALLOWLIST`, content-type в whitelist,
  max size (env `CAS_MAX_FILE_BYTES`, default 20 MB)
- Скачать → sha256 → shard-путь → `FileExistsError`-safe (идемпотентно)
- Возвращает `sha256hex.ext` или `None` на ошибку (с логом WARNING)
- Запрет path traversal: только sha256 filename без `/`

**Предусловие:** A3 PASS  
**Проверка готовности (AC-3 из спека):** повторный вызов с тем же URL → тот же sha,
файл не перезаписывается, content-type и max-size валидируются.

---

### Фаза C — URL resolve в normalizer (после B)

**Шаг C1.** В `url_filter.py`: перед проверкой hostname resolve относительный URL
к `source_url` через `urllib.parse.urljoin`. Тесты: relative `img[src]`, relative `a[href]`,
absolute (без изменений), dangerous scheme (javascript:, data:) → drop.

**Предусловие:** B  
**Проверка готовности (AC-2 из спека):** тесты покрывают 5+ кейсов; `polyakov 4406`
sample-парсинг больше не даёт `[IMAGE REMOVED: None]` для stat-html файлов с src.

---

### Фаза D — интеграция CAS в normalizer/adapter (после B+C)

**Шаг D1.** В `image_resolver.py` и аналоге для `a[href]`: после resolve URL — вызвать
`download_to_cas` для allowlisted ресурсов; записывать в `stem_images`/`attached_file_paths`
результирующий путь `/api/v1/media/{sha}.{ext}`.

**Шаг D2.** Убедиться, что adapter `builder.py` и XLSX exporter `lms_import_file.py`
прозрачно пропускают `stem_images`/`attached_file_paths`/`has_attached_file`
(уже реализовано в passthrough tsk-094/095 — верификация).

**Предусловие:** B+C  
**Проверка готовности:** unit-тест: моковый URL → CAS download → `stem_images` заполнен.

---

### Фаза E — source-specific rendering

**E1 (polyakov):** Playwright re-fetch для topicId с `[IMAGE REMOVED]`
(4406, 7613, 7442 — 3 уникальных топика, 5+2+2 версий в БД).
Используется `monolith/external_tasks/fetchers/` паттерн.

**E2 (sdamgia):** Уточнить селектор body задачи (`.prob_maindiv`/`.pbody`).
Playwright **не нужен** — изображения доступны как абсолютные URL в HTML
(confirmed: все 183 задачи имеют `get_file?id=...` в stem, CDN — `inf-ege.sdamgia.ru`).

**Предусловие:** C+D  
**Проверка:** AC-5 (polyakov 4406), AC-6 (sdamgia selector).

---

### Фаза F — pipeline и re-import (финал, требует A+B+C+D+E)

Dry-run, pilot 25-50 задач, `review-gate` PASS, полный batch.
Подробно — в плане `pipeline-operator` (этап 9-12 спека).

---

## Маршрутизация по skills

| Фаза | Под-задача | Исполнитель | Ревью / контроль | Примечания |
|---|---|---|---|---|
| A1 | ADR-0040 текст | `executor-lite` | `context-auditor` | Текстовый артефакт, минимальный контекст |
| A2-A3 | LMS media endpoint + тесты | `fastapi-api-developer` | `lms-fastapi-techlead-code-reviewer` | Security-критично: root-jail, traversal |
| B1 | CB CAS downloader | `executor-pro` | `techlead-code-reviewer` | Shared pipeline module, security |
| C1 | URL resolve fix | `executor-pro` | `techlead-code-reviewer` | Затрагивает все источники |
| D1-D2 | CAS интеграция в normalizer/adapter | `executor-pro` | `techlead-code-reviewer` | Контракт `ExternalParsedTask` + adapter |
| E1 | Polyakov Playwright re-fetch | `executor-pro` | `qa-fix` | Source-specific renderer |
| E2 | Sdamgia selector fix | `executor-pro` | `qa-fix` | body extraction |
| F | Dry-run + pilot + full batch | `pipeline-operator` | `review-gate` | review-gate PASS обязателен перед full batch |
| финал | Docs update | `context-auditor` + `project-docs` | оператор | Cross-project STATE/CHANGELOG |

**Cross-cutting skills:**
- `encoding-guard` — перед коммитом любых `.py` файлов с кириллицей в строках
- `db-check` — перед pilot и после full batch (AC-9, AC-10)
- `context-auditor` — после E (drift-check перед F)

---

## Plan проверки

| AC | Проверка | Когда |
|---|---|---|
| AC-4 | HTTP 200/404/400 для LMS `/api/v1/media/` | После A3 |
| AC-2 | Unit-тесты URL resolve (5+ кейсов) | После C1 |
| AC-3 | CAS idempotency: повторный download | После B1 |
| AC-5 | polyakov 4406: нет `[IMAGE REMOVED]`, stem_images непустой | После E1 |
| AC-6 | sdamgia: только body задачи, нет декоративных header-img | После E2 |
| AC-7 | wp_nav recovery report | После D (dry-run) |
| AC-9 | Pilot: нет дублей, 2-й прогон = 0 новых строк | После F pilot |
| AC-10 | Final SQL: нет восстановимых IMAGE REMOVED | После full batch |
| AC-11 | review-gate PASS | Перед full batch |

---

## Риски и меры снижения

| Риск | Вероятность | Митигация |
|---|---|---|
| Static serving открывает лишние файлы | Средняя | root-jail + sha-only path + 404 на missing, нет directory listing |
| CAS_MEDIA_ROOT shared — ошибка пути | Низкая | Проверять is_relative_to + smoke до re-import |
| sdamgia CDN недоступен при повторном fetch | Средняя | Checkpoint + cache raw HTML; не перезагружать что уже есть |
| polyakov Playwright медленный | Высокая | Throttling, checkpoint, обрабатывать только 3 уникальных topicId |
| Re-import затирает ручные правки | Средняя | update-only по external_uid + backup-export before apply |
| wp_nav: исходный URL недоступен | Высокая | Audit report, восстанавливать только с доказуемым source URL |

---

## Критерии Go/No-Go

**Go (переход к Фазе F):**
- AC-4 PASS (LMS media endpoint)
- AC-3 PASS (CAS idempotency)
- AC-5 PASS (polyakov pilot)
- AC-6 PASS (sdamgia selector)
- review-gate PASS

**No-Go (стоп):**
- LMS endpoint даёт path traversal
- CAS downloader записывает вне `cas_media_root`
- pilot создаёт дубли по external_uid

---

## Решение по UX-сложности

Медиафайлы отдаются как статический контент по публичному URL. Никакого
нового UX (экраны, диалоги, кнопки) нет. SPW/TG_LMS потребляют `stem_images`
как `<img src>` — без изменений клиентского кода.

---

*Следующий шаг: `executor-lite` пишет ADR-0040, затем `fastapi-api-developer` реализует LMS endpoint (Фаза A).*
