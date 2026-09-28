# tsk-1147 — дашборд преподавателей и способ помощи у ответа

**Контекст:** спек `docs/specs/2026-09-28-spec-tsk1147-teacher-dashboard.md`.
**Review-gate:** ПРИНЯТО (независимый агент, 28.09); находки P3 №1–3 исправлены до коммита
(«голосом» после возврата, скрыть voice при hideClose, сброс выбора после закрытия), №4 (граница слова
в JS для кириллицы) и №5 (порядок выката: LMS первым) — зафиксированы в спеке.

**Изменения LMS:** миграция `tsk1147_reply_kind` (колонка + CHECK + разметка истории),
`help_reply_kind.guess_reply_kind`, `reply_kind` в `POST …/reply`, общие SQL-фрагменты
хозяина/когорты/состава в `help_requests_service`, `teacher_dashboard_service` +
`GET /teacher/help-requests/kpi/dashboard` (методист/админ).

**Проверки:** pytest 12 (tsk1147) + 18 (tsk303 KPI) + соседние заявки — зелёные; EXPLAIN на проде 11 мс.

Diff: `reviews/2026-09-28-tsk1147-teacher-dashboard.diff`.
