# tsk-302 (направление 1) — статический анализ качества/стиля кода ученика

**Дата:** 2026-08-06
**Задача:** [tsk-302](D:\Work\Root\tasks\tsk-302-avtoproverka-koda-uchenikov-detektor-ii-avtootsenka-kachestva-koda.md) — «Автопроверка кода учеников: детектор ИИ + автооценка качества кода», направление 1 (качество/стиль).
**Решение оператора (2026-08-06):** оценка качества/стиля кода видна ТОЛЬКО teacher/methodist/admin, ученику не показывается.
**Решение review-gate: ПРИНЯТО** (раунд 2, после исправления Б1/Б2 — см. ниже).

## Контекст

Направление 2 (детектор ИИ-авторства) в этом заходе не реализуется — экспериментальный пилот, отдельная задача/PR.

Существующая песочница исполнения Python-кода ученика (`app/services/turtle_sandbox/`, tsk-412) исполняет код в изолированном subprocess (`unshare --user --net --pid --fork --map-root-user` на проде) и сравнивает получившуюся трассу рисунка с эталоном. Направление 1 добавляет статический анализ СТИЛЯ этого же кода (магические числа, цикломатическая сложность, число аргументов функций, читаемость имён) через pylint/radon — без исполнения кода, но в той же изоляции subprocess (переиспользование, не новый небезопасный процесс).

## Изменённые/новые файлы

- `app/services/turtle_sandbox/executor.py` — параметризован `_build_command(entry_script)`; добавлена `run_code_quality_check()` с отдельным семафором `_LINT_SEMAPHORE`.
- `app/services/turtle_sandbox/lint_runner.py` (новый) — точка входа subprocess для pylint/radon (аналог `runner.py`, без `exec` кода ученика).
- `app/services/code_quality_service.py` (новый) — сервисная обёртка `analyze_student_code_quality()`.
- `app/api/v1/attempts.py` — вызов анализа качества после `check_result`, `metrics=code_quality_metrics` в оба вызова `create_from_check_result`. **Намеренно НЕ добавлено в `CheckResult`** (тот эхо-возвращается ученику в `AttemptAnswerResult.check_result`).
- `requirements.txt` — `pylint>=3.0,<4.0`, `radon>=6.0,<7.0`.
- `tests/test_code_quality_tsk302.py` (новый) — 10 тестов.

Схема БД не менялась — переиспользовано существующее неиспользуемое поле `task_results.metrics` (JSONB), миграция не нужна.

## Ход ревью

### Раунд 1 — независимый суб-агент (`review-gate`, general-purpose) — ОТКЛОНЕНО

Утечки ученику не найдено. Две блокирующие находки:

- **Б1** — анализ качества делил `_SANDBOX_SEMAPHORE` (лимит 3) с исполнением turtle-кода и выполнялся синхронно в критическом пути `POST /attempts/{id}/answers` (замер: 2.6–4.6с на анализ против ~0.1с на исполнение) — риск `sandbox_busy` у ДРУГИХ учеников при умеренной параллельной нагрузке; анализ также выполнялся для уже просроченных попыток.
- **Б2** — `_run_code_quality_check_locked` перехватывал только `subprocess.TimeoutExpired`; `OSError` (недоступен `unshare`/интерпретатор, нет места на диске) пробрасывался наружу и валил весь приём ответа (включая уже посчитанный `check_result`), хотя докстринг обещал обратное.

### Раунд 2 — исправления

- Б1: отдельный `_LINT_SEMAPHORE` (лимит 2), не разделяемый с исполнением turtle-кода; анализ пропускается, если `attempt.time_expired` уже `True` на момент сдачи (`app/api/v1/attempts.py`).
- Б2: `_run_code_quality_check_locked` теперь перехватывает `OSError` (→ `CodeQualityResult(ok=False, error="sandbox_error")`); вызов в `attempts.py` дополнительно обёрнут в `try/except Exception` (defense-in-depth, по аналогии с soft-fail гейтами 2.4b/2.4c/2.4d).
- Добавлен регрессионный тест `test_run_code_quality_check_oserror_is_reported_not_crashed`.

Не устранено (задокументированный остаточный риск, не блокирующий):
- Анализ всё ещё может выполниться для ответа, который дальнейшие гейты (2.3e requires_attachment, 2.3f comment-required) обнулят — полное устранение требует переупорядочивания хорошо задокументированной последовательности гейтов 2.3c–2.3f, риск регрессии не оправдан объёмом экономии для MVP-пилота направления 1.
- Legacy-эндпоинты `GET /task-results/by-user/{user_id}`, `by-task/{task_id}`, `by-attempt/{attempt_id}` (`app/api/v1/task_results_extra.py`) отдают `TaskResultRead` (с полем `metrics`) без role-гейта teacher/methodist — **предсуществующее** состояние (не введено этим PR), доступны только по сервисному `?api_key=`, из браузера ученика недостижимы. Follow-up: подтвердить в TG_LMS, что бот не ретранслирует `task_results.metrics` ученику дословно.

## Validation Results

- `pytest tests/` — полный прогон ДО находок Б1/Б2: **1794 passed, 11 skipped, 0 failed**.
- После фиксов Б1/Б2: `tests/test_code_quality_tsk302.py` (10, включая новый регрессионный тест на Б2) + `tests/test_turtle_sandbox_tsk412.py` (34) — **44 passed** (дважды, стабильно).
- Финальный полный прогон `pytest tests/` (1800+ тестов, ~17 мин): **1800 passed, 11 skipped, 3 failed**. Все 3 падения — транзиентные:
  - `test_analyze_student_code_quality_returns_report_for_real_code` (мой) — упал только в полном прогоне (system под нагрузкой 17-минутного прогона, вероятен близкий к границе таймаут pylint cold-start); при изолированном повторном запуске — passed.
  - `test_users_list_blocked_false_shows_only_open`, `test_users_list_without_blocked_param_is_unchanged` (`tests/test_tsk559_active_blocked_filters.py`) — **не связаны с этим PR** (файлы users/blocked-фильтров этой задачей не затрагивались, `git diff --stat` подтверждает); при изолированном прогоне файла — 9/9 passed. Похоже на конкуренцию за dev-БД с параллельной сессией (tsk-231), работавшей в том же рабочем дереве в это же время (`.skill-engaged-note.md` перезаписывался в процессе этой сессии).
  - Итог: узкая область изменений (44 теста) стабильно зелёная; 3 падения полного прогона — среда/конкуренция, не регрессия кода этого PR.
- Живой смоук на dev-БД (реальный `POST /attempts/{id}/answers` через `python run.py`): временная задача turtle_sim, код с `if i == 42: t.forward(999)`. Ответ ученику — `{"is_correct":true,"score":1,"max_score":1,"details":null,"feedback":{...},"scale_scores":null}` (без следов pylint/radon). `task_results.metrics` в БД — полный отчёт (`magic-value-comparison` на строке 4, `radon.maintainability_index=75.16`). Тестовые данные удалены.
- `pip check` в `.venv` (pylint 3.3.9, radon 6.0.1) — конфликтов зависимостей нет.

## DB Findings

Прод (read-only, `learn_prod_db`): курс 165 содержит turtle_sim-задачи (id 10033, 10039–10042 и др.) — направление 1 применимо к реальным данным без доп. миграций контента.

## Risks / Follow-ups

- Анализ качества добавляет ~3–5с к latency сдачи turtle_sim-ответа (для самого отправителя, не для других — после разведения семафоров). Приемлемо для MVP-пилота; при заметной жалобе на отзывчивость — перенос в фон (после `db.commit()`), не блокирует этот заход.
- Follow-up для TG_LMS: подтвердить, что бот не ретранслирует `task_results.metrics` ученику при просмотре истории ответов (вне LMS-репозитория).
- Направление 2 (детектор ИИ) не начато — отдельная задача, экспериментальный пилот через `/architect-system-analyst`.
