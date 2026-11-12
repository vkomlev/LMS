# Review-артефакт: tsk-765 — started_pages в воронке магнитов (12.11.2026)

## Цель

Бот-всплески (51 IP / 1 UA / 0 ответов 11–12.11) загрязняют замер квиза: сырые
старты страницы (`quiz_funnel_progress`) пишутся при загрузке. Добавить поле
`started_pages`, не ломая существующие `started`/`completed`.

## Затронутые файлы

- LMS `app/services/lead_magnet_service.py` (get_funnel): CTE `page_starts` +
  поле `started_pages` в SELECT; GROUP BY дополнен `ps.n`.
- LMS `app/schemas/guest_quiz_funnel.py` (`QuizFunnelRow`): обязательное поле
  `started_pages: int`. Это расширение response схемы — старые клиенты
  (extra=ignore) не ломаются.

## Валидация (raw)

- `pytest tests/ -q -k "funnel or magnet or quiz"` → **125 passed**
  (включая тесты воронки магнитов).
- Существующие поля started/completed/leads/lead_rate не изменены (diff
  подтверждает: только добавление).

## Риски

- Response схема стала строже на сериализации своих же данных — значение всегда
  есть (coalesce 0). Низкий.

## Rollback

`git revert 68ec4df`.
