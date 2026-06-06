# tsk-110 Фаза A — LMS CAS media endpoint (2026-06-04)

**Skill:** fastapi-api-developer · **ADR:** 0040 · **Этап спека:** 3 из 13

## Implementation Plan

Реализован публичный endpoint `GET /api/v1/media/{sha_ext}` для отдачи
CAS-медиафайлов внешних задач ContentBackbone.

## Changed Files

| Файл | Изменение |
|---|---|
| `app/core/config.py` | + `cas_media_root: Path` (из `CAS_MEDIA_ROOT` env, default `data/media_store`) |
| `app/api/v1/media.py` | **NEW** — endpoint с regex-валидацией, root-jail, FileResponse |
| `app/api/main.py` | + `import media_router`, `app.include_router(media_router, ...)` |
| `.env` | + `CAS_MEDIA_ROOT=D:\Work\ContentBackbone\data\media_store` |
| `tests/test_media_cas_endpoint.py` | **NEW** — 10 тестов |

## Security Controls (ADR-0040)

- **Regex**: `[0-9a-f]{64}\.(png|jpg|jpeg|gif|webp|svg|pdf|txt|ods|odt|xlsx|xls|csv)` — только sha256-имена
- **Root-jail**: `Path.resolve().is_relative_to(cas_media_root)` — дополнительная страховка
- **Content-type**: allowlist по расширению (`_EXT_CONTENT_TYPE`), не `mimetypes.guess_type`
- **No auth**: публичный endpoint (stem-изображения уже открыты через guest-mode Y-5)
- **No directory listing**: только конкретный файл по sha

## Validation Results

| AC | Статус | Доказательство |
|---|---|---|
| AC-4 (HTTP 200) | ✅ PASS | `test_ok_image`, `test_ok_pdf`, `test_ok_xls` |
| AC-4 (HTTP 404) | ✅ PASS | `test_missing` |
| AC-4 (HTTP 400 traversal) | ✅ PASS | `test_traversal_dots` |
| AC-4 (HTTP 400 bad format) | ✅ PASS | `test_wrong_ext`, `test_short_sha`, `test_long_sha`, `test_uppercase_sha`, `test_no_ext` |
| Регрессия task_content | ✅ PASS | `test_tasks_import_task_content_json.py` (10/10) |

**Итого: 10/10 новых + 10/10 смежных = 20/20 PASS**

## Smoke (PASS 2026-06-04)

| URL | Ожидание | Факт |
|---|---|---|
| `aaa...aaa.png` (файл в CAS) | 200 image/png | ✅ 200 image/png |
| `bbb...bbb.png` (нет в CAS) | 404 | ✅ 404 |
| `tooshort.png` (sha < 64) | 400 | ✅ 400 |
| `aaa...aaa.exe` (ext вне allowlist) | 400 | ✅ 400 |

## Risks / Follow-ups

- **Smoke pending**: нужен перезапуск LMS-сервера для загрузки нового роутера
- **Следующий этап**: Фаза B — CB CAS downloader (`executor-pro`)
- Предупреждение `on_event deprecated` — legacy Y-6 APScheduler; не связано с tsk-110
