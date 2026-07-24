# Указатель: плейбук импорта заданий ЕГЭ/ОГЭ (свод работ и находок)

> **Это стаб-указатель.** Полный документ живёт в ContentBackbone (хаб семьи репо, там весь код парсеров):
>
> **`D:\Work\ContentBackbone\docs\ai\ege-import-playbook.md`**

## Зачем читать перед работой с заданиями ЕГЭ/ОГЭ/Python

Направление «парсинг → нормализация → импорт заданий → чистка дефектов» велось с мая по июль 2026 (CB + LMS + SPW + TG_LMS). Плейбук сводит ~80 задач трекера и сессии, чтобы **не переоткрывать грабли**. Родительская задача — tsk-399.

Если твоя задача касается заданий ЕГЭ/ОГЭ, внешних источников (kompege/sdamgia/polyakov/yandex/krylov/TG), `solution_rules`, `accepted_answers`, `difficulty_id`, `order_position`, стемов, медиа-вложений, дублей — **сначала плейбук, потом работа.**

## Что на стороне LMS (быстрые ссылки)

**Мастер-аудит F1–F10:** [`2026-07-19-tsk299-import-audit.md`](2026-07-19-tsk299-import-audit.md) (2785 активных заданий, 10 находок).

**Durable-чеки (LMS/scripts):**
- `check_ungradable_tasks.py` — пустое `solution_rules` (⚠️ ТРИ формы: SQL-null, JSON-null, объект-но-пустой; `IS NULL` ловит только первую).
- `check_missing_attachments.py` — задания без обязательного файла-приложения.
- `ege_stem_markup_invariant.py` — порча sup/sub/katex.
- `ege_answer_invariant.py` / `oge_answer_invariant.py` — нет ответа + manual-review (правила ЕГЭ ≠ ОГЭ).

**Смежные аудиты в этой папке:** tsk-337 (order_position), tsk-354/355/381 (difficulty), tsk-373 (answer mismatch).

**Ключевые инварианты LMS-стороны (детали — в плейбуке §6, §7):**
- Реордер `order_position` — durable-хук в `TasksService.bulk_upsert`; триггер глушить **только** через session-var `app.skip_task_order_trigger`, **никогда** `ALTER TABLE DISABLE TRIGGER` (лок всей `tasks`).
- Методические поля вне payload (`requirement_level`, `difficulty_provenance`) — семантика «не передано = не менять» (`exclude_unset`/`model_fields_set`) или отдельная колонка.
- Пустое правило = задание тихо «всегда неверно» И не попадает в очередь преподавателя.
- Новый тип задания = три клиента контракта (LMS + SPW + TG_LMS); Zod-фронт SPW ломается молча при зелёных тестах — ловится только живым прогоном.
- Allowlist медиа-расширений дублируется в CB (`cas_downloader`) и LMS (`media.py`) — рассинхрон → `GET media` 400 (ADR-0049).

---

*Создано 2026-07-24 (tsk-399). Источник истины — плейбук в CB; при расхождении верить ему.*
