# CHANGELOG

## 2026-08-29 — Служебный вход лидов для соседних систем (tsk-718)

- `POST /api/v1/integrations/leads` — соседняя система заводит лида в мини-CRM кабинета маркетолога. Пускает **только сервисный ключ**: сам кабинет для сервисных ключей намеренно закрыт, и открывать его машине значило бы завести вторую дверь в те же данные с другими правилами.
- Вход **идемпотентен** по паре «источник + внешний номер человека»: повторный вызов возвращает уже заведённого лида с `created: false`. Первый потребитель — переписка Авито, где один человек пишет по нескольким объявлениям и в разное время.
- Таблица `lead_external_ref` (миграция `tsk718_lead_external_ref`) — память о том, из какого внешнего обращения лид уже заведён. Отдельная таблица, а не пара колонок в `leads`: у лида, заведённого руками в кабинете, внешнего номера нет, колонки были бы пустыми, а уникальный ключ с пустой колонкой в PostgreSQL не работает вовсе — два NULL друг другу не противоречат, и дедуп молча перестал бы срабатывать. Здесь обе колонки ключа NOT NULL.
- На кабинет миграция не влияет: таблица после накатки пуста, лиды и их правка работают как прежде.

## 2026-06-02 - Add task_content_json passthrough to task import

- Added optional `task_content_json` Google Sheets column with shallow merge semantics.
- Preserved unknown `TaskContent` keys for images, attachments, and future extensions.
- Kept invalid JSON isolated to its source row.

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
