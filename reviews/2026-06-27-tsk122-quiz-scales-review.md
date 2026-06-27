# Техлид-ревью: квиз-вопросы со шкалами SC_Qw/MC_Qw (tsk-122)

- **Дата:** 2026-06-27
- **Ветки:** LMS `claude/modest-wright-ae2630`, ContentBackbone `feat/quiz-scales`, CreateCourses `feat/quiz-scales`
- **Decision: FAIL**
- **Review Horizon:** phase complete (оценивается вся фича tsk-122, 3 этапа + перевод пробного)

## Blocking Findings

### S2 — квиз-задача никогда не «проходится» → ученик застревает, курс не завершается

**Файлы:** [checking_service.py:326-333](app/services/checking_service.py) (`_check_quiz` пишет `score=0`); коллизия с [learning_engine_service.py:152-180](app/services/learning_engine_service.py) (`compute_task_state`) и [learning_engine_service.py:241-260](app/services/learning_engine_service.py) (`compute_course_state`).

**Суть:** `_check_quiz` возвращает `score=0, max_score=1`. Learning Engine определяет завершённость задачи **по отношению `last_score/last_max_score >= 0.5`**, а не по `is_correct`:
- `compute_task_state` → для квиза ratio=0 → `state="FAILED"`.
- `resolve_next_item` ([learning_engine_service.py:500-502](app/services/learning_engine_service.py)) возвращает задачу в состоянии `OPEN/IN_PROGRESS/FAILED` как «следующую». Квиз всегда `FAILED` → после ответа `resolve_next_item` снова отдаёт тот же квиз → **ученик зациклен на вопросе-предпочтении**.
- `compute_course_state` ([learning_engine_service.py:241-260](app/services/learning_engine_service.py)) считает задачу «done» только при `last_score/last_max >= 0.5` или skip. Квиз никогда не done → `done_items < total_items` → **курс с квизом никогда не COMPLETED**.

**Production impact:** основной сценарий фичи (ответ на routing-квиз → маршрутизация → продолжение обучения) сломан на уровне прогрессии. Назначение курса (триггер quiz_scale) работает — это подтверждено e2e, — но сам пробный курс с квизом для ученика становится непроходимым. Дефект не задевает существующие курсы (квиз-задач в проде нет), но делает новую фичу нефункциональной в её главном пути → integration-unsafe для фичи.

**Почему проскочило:** live-тест проверил только цепочку назначения (enrollment), но не `compute_task_state`/`compute_course_state`/`resolve_next_item` с квиз-задачей. ADR-0003 §3 формулировал «score справочно, не pass/fail», но не учёл, что Learning Engine использует `score/max_score` как сигнал завершённости (а не `is_correct`).

## Required Fixes

1. **`_check_quiz`** ([checking_service.py:326-333](app/services/checking_service.py)): при непустом ответе (≥1 выбранный вариант) ставить `score = solution_rules.max_score` (квиз отвечен = задача выполнена, ratio=1.0 → PASSED/done), при пустом — `score=0`. `is_correct` оставить `None`, `scale_scores` — без изменений (остаётся сигналом маршрутизации). Это зеркалит паттерн optimistic-PASSED для SA_COM/TA ([attempts.py:387-400](app/api/v1/attempts.py)).
   ```python
   answered = len(user_set) > 0
   score = solution_rules.max_score if answered else 0
   ...
   return CheckResult(is_correct=None, score=score, max_score=solution_rules.max_score, ...)
   ```
2. **Обновить тесты** под новую семантику: `test_sc_qw_scoring_single_choice` и интеграционный `test_quiz_answer_persists_scale_scores` ([tests/test_quiz_scales_tsk122.py](tests/test_quiz_scales_tsk122.py)) — assert `score == max_score` для отвеченного квиза; `test_sc_qw_empty_answer_zero_scales` — `score == 0`.
3. **Добавить интеграционный тест прогрессии:** квиз-задача в курсе → ответ → `compute_task_state` == PASSED, `compute_course_state` == COMPLETED (когда квиз — единственная required-задача). Это закрывает дыру в покрытии, из-за которой дефект проскочил.
4. **ADR-0003:** добавить примечание, что для квиза `score=max_score` при ответе (совместимость с completion-gating Learning Engine), `scale_scores` — отдельный сигнал.

## Required Validation Commands
```bash
cd "D:/Work/LMS/.claude/worktrees/modest-wright-ae2630"
.venv/Scripts/python.exe -m pytest tests/test_quiz_scales_tsk122.py tests/test_quiz_scale_trigger_tsk122.py -q
# новый тест прогрессии должен подтвердить compute_course_state == COMPLETED для курса с квизом
```

## Architecture Assessment
Слоистость соблюдена (api→services→repos), `_check_quiz` корректно изолирован, `evaluate_rules_for_attempt` чисто разбит на `_evaluate_course_failed`/`_evaluate_quiz_scale`. Единственное нарушение — неучтённая связь scoring-слоя с completion-семантикой Learning Engine (см. блокер).

## Migration Assessment
PASS. Две миграции аддитивны (`task_results.scale_scores` nullable; CHECK trigger_event extend). Обратимость проверена (downgrade→upgrade зелёные, head `tsk122_trigger_quiz_scale`). DB-enum на тип задач нет — корректно. Данные не затрагиваются.

## Test Adequacy Assessment
FAIL. Юнит/интеграция scoring и триггера — хорошее покрытие (17 LMS + 8 CB). Но **отсутствует тест прогрессии** (compute_task_state/course_state/next_item с квизом) — именно он бы поймал блокер. Обязателен (Required Fix #3).

## Observability Assessment
PASS. Soft-fail движка правил с `logger.warning` сохранён; `quiz_scale` ветка логируется единообразно.

## Security Assessment
PASS. Квиз идёт через существующую auth попытки (owner/service). IDOR-поверхность не расширена. `scale_scores` — числовой JSONB, инъекций нет (параметризованный SQL). Публикатор CB: `course_id` инъектируется сервером, не из исходника.

## UX/UI Critical Assessment
FAIL (следствие блокера): зацикливание на квизе и незавершаемый курс — критический UX-слом основного пути. Рендер SC_Qw/MC_Qw в SDK-клиентах (SPW/TG_LMS) — вынесен в отдельную сессию (согласовано), не блокер этого мержа, но без фикса #1 даже корректный рендер не спасёт прогрессию.

## Spec Ambiguity Assessment
ADR-0003 неоднозначен в §3: «score не используется для pass/fail» вступил в конфликт с реальной completion-логикой Learning Engine. Требует уточнения (Required Fix #4).

## Date/Time Type Safety Assessment
N/A — date/time логика не затрагивалась. Накопление шкал использует `submitted_at DESC` только для ORDER BY (DISTINCT ON), без сравнения raw str с datetime.

## Residual Risks
- После фикса #1: квиз будет считаться «passed» в `get_stats_by_*` (answered=passed) — приемлемо, но стоит держать в уме для аналитики прогресса.
- CB-публикатор хардкодит `max_score=1` ([blocks_to_lms.py to_task_payloads](monolith/lms_publish/blocks_to_lms.py)) — согласовано со `solution_rules.max_score=1` квиза, рассинхрона нет.

## Claude Skills Improvement Entries
OPEN-кандидат для `skills-errors.md` (через `/response-quality-coach`): новый тип задачи добавлен без интеграционного теста против completion/next-item путей Learning Engine.

## Skill Improvement Actions
- **fastapi-api-developer** → `references/` (или `SKILL.md` §Debug loop/Smoke): при добавлении нового `TaskType` в scoring обязателен интеграционный тест против `learning_engine_service.compute_task_state` / `compute_course_state` / `resolve_next_item` (completion-gating по `score/max_score`, не только по `is_correct`). Приоритет: next-iteration.
