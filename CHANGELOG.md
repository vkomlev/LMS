# CHANGELOG

## 2026-04-23 — Реорганизация документации

**Для разработчика и AI-агента:**

- Появился AI-слой документации в [docs/ai/](docs/ai/): архитектура, модель данных, глоссарий, контракт агентов, workflows. Точка входа — [docs/ai/INDEX.md](docs/ai/INDEX.md).
- `README.md` переписан под новую структуру: быстрый старт, карта API-справочников, ссылки на AI-слой и архив. Без эмодзи, 148 строк.
- В `.claude/CLAUDE.md` добавлен раздел «Documentation paths» — единая мапа «скилл → куда сохранять артефакт» для `/project-docs`, `/fastapi-api-developer`, `/techlead-code-reviewer`, `/review-gate`, `/pr-review`, `/document-release`, `/session-digest`, `/retro`, `/qa-report`, `/qa-fix`, `/spec-writer`, `/change-plan-architect`, `/tech-spec-composer`, `/response-quality-coach`.
- Директория `docs/` очищена: 68 исторических документов (ТЗ, smoke-результаты, стадии, чаты, legacy) перенесены в [docs/archive/](docs/archive/) по подкатегориям.
- Директория `reviews/` очищена: 70 пар `.md`/`.diff` (февраль-март 2026) перенесены в [reviews/archive/](reviews/archive/) через `git mv` с сохранением истории.
- Восстановлена кодировка трёх файлов с mojibake: `docs/assignments-and-results-api.md`, `docs/api-reference.md`, `docs/openapi.json`. Теперь все файлы в `docs/` — UTF-8 без BOM.
- Созданы placeholder-директории для будущих артефактов: `docs/releases/`, `docs/sessions/`, `docs/retro/`, `docs/qa/`, `docs/specs/`.
- Обновлён `.gitignore`: в whitelist добавлены `docs/ai/**` и новые сервисные директории, чтобы AI-документация и артефакты скиллов попадали под версионный контроль.
