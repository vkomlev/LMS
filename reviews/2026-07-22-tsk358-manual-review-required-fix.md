# tsk-358 — снятие manual_review_required=true у 170 SA_COM-заданий с известным ответом

## Контекст
Живой инцидент: реальная ученица (курс 138 «Задание №3. Базы данных в Excel»,
задание про базы «Города и страны») попала в очередь ручной проверки
преподавателю, хотя её ответ уже был верно автопроверен. Root cause: task
id=2058 имел `solution_rules.short_answer.accepted_answers=["319862"]`
(известный ответ) одновременно с `manual_review_required=true`. Очередь
обязательной проверки (`teacher_queue_service.mandatory_review_sql`) роутит
SA_COM по этому флагу вне зависимости от наличия верного ответа.

Масштаб (read-only аудит прод БД `learn`, 2026-07-22): 186 активных SA_COM-
заданий с этим противоречием (accepted_answers непустой + manual_review_
required=true) — 173 sdamgia (`ext:d4:sdamgia:20260602:*`), 10 sdamgia-дубль
(`ext:calib:sdamgia:20260525:*`), 2 Полякова, 1 авторский (WP).

## Разбор всех 186 (не выборка)
16 исключены из фикса — реального надёжного ответа нет:
- id=54 — пустой ответ (`value=""`)
- id=2242, 2355, 2300, 2386 — обрывок прозы/кода вместо ответа
- id=2303, 2304, 2338 — формула вместо результата (`"720 + 576 = 1296"` и т.п.)
- id=2379, 2380, 2381, 2382 — только `":"` (сломанная экстракция)
- id=2306, 2310, 2313 — `"1253&2494"` и т.п. (неоднозначное объединение вариантов)
- id=2311 — `"667 и 4009"` (неоднозначное объединение вариантов)

Проверено и признано безопасным (не блокирует фикс для оставшихся 170):
- `requires_attachment=false` у всех 186 — наличие приложенного файла-источника
  (75/186) не требует ручной проверки (сам инцидент 2058 — с приложением .xls).
- Формат `"— NNN"` (em-dash вместо минуса) — нормализация
  `strip_punctuation`+`collapse_spaces` (`app/services/checking_service.py:740-743`)
  съедает и `—`, и `-` одинаково; не мешает сравнению.
- Пробел как разделитель тысяч (`"233 024 691"`) — уже стандартная практика
  у 197 живых автопроверяемых заданий платформы (проверено запросом), не новый риск.

## Прод-фикс
`/db-check` протокол: read-аудит → dry-run → apply в транзакции → верификация.

Скрипт: [scripts/fix_manual_review_required_tsk358.py](../scripts/fix_manual_review_required_tsk358.py)
— `UPDATE tasks SET solution_rules = jsonb_set(solution_rules, '{manual_review_required}', 'false')`
для 170 из 186 (16 явно исключены списком id, идемпотентная проверка
count-кандидатов до апдейта).

Запуск: `ssh lms-spw-vds` (локальный `.env` указывает на dev, не на прод —
см. `feedback_local_env_prod_dsn_gotcha` в памяти), `.env` на сервере с прод
DSN, `DBCHECK_OK=1 --apply` (боевая запись, хук `db_write_gate` требует этот
префикс). Dry-run и apply прошли идентично: 186 кандидатов, 170 обновлено,
16 корректно не тронуты, контрольное задание 2058 → `manual_review_required=false`.

**Независимая верификация** через MCP `learn_prod_db` (read-only, отдельный
канал от write-скрипта, обязательна после инцидента tsk-356): подтверждено —
ровно 16 всё ещё `true`, 2058/2059/2211/2294/2337 → `false`, 54/2242/2379 →
всё ещё `true` (как и задумано). Существующий результат Софьи (`task_results`
id=4167, submitted 2026-07-21, уже `is_correct=true`) больше не попадает под
предикат `mandatory_review_sql` — проверено прямым запросом.

## Durable-фикс — пересмотрен
Первая гипотеза (поправить `ContentBackbone/monolith/external_tasks/adapter/
builder.py:148`: заменить `methodist_answer is None` на `answer is None`)
оказалась **неверной**. Это не баг, а намеренный дизайн: докстринг
`_build_solution_rules`/`TaskAdapter.adapt()` и тест
`test_adapt_html_basic_with_answer_raw` (ContentBackbone,
`tests/test_external_tasks_adapter.py:126-148`) явно фиксируют — ответ
только из парсера (`answer_raw`) должен идти на `manual_review_required=True`
до методист-верификации (Stage 3), только `methodist_answer` снимает флаг.
Правка была сделана, найдена ошибочной при чтении тестов и **отменена**
(`git checkout` в ContentBackbone — рабочее дерево CB не изменено).

Настоящая причина инцидента — процессная: `ContentBackbone/docs/plans/
change-plan-tsk004-6.7-production-readiness-v1.md` требует Go-критерием «без
непроверенных answer_final» перед боевым прогоном D.4, но sdamgia/polyakov
mini50-партия ушла в LMS с непройденным Stage 3 (существующий инструмент
`monolith/external_tasks/review/xlsx_export.py`+`xlsx_import.py`) и провисела
на проде без дедлайна на дозавершение. Ручной построчный разбор 186 записей
в этой сессии фактически стал этим пропущенным Stage 3 для затронутой партии —
разовая компенсация, не системная защита. Follow-up вынесен в
`D:\Work\Root\tasks\tsk-359-...md` (форсинг Stage 3 / quality-triage хелпер
для будущих партий).

## Changed Files
- `scripts/fix_manual_review_required_tsk358.py` (новый)
- `reviews/2026-07-22-tsk358-manual-review-required-fix.md` / `.diff` (этот отчёт)
- `D:\Work\Root\tasks\tsk-358-...md` — закрыт (`status: done`)
- `D:\Work\Root\tasks\tsk-359-...md` — создан (follow-up, P2)
- ContentBackbone: без изменений (правка сделана и отменена)

## Validation Commands
```
ssh lms-spw-vds "sudo -u app bash -c 'cd /opt/lms && ./venv/bin/python scripts/fix_manual_review_required_tsk358.py'"                    # dry-run
ssh lms-spw-vds "sudo -u app bash -c 'cd /opt/lms && DBCHECK_OK=1 ./venv/bin/python scripts/fix_manual_review_required_tsk358.py --apply'"  # apply
```

## DB Findings
186 кандидатов на прод БД `learn` до фикса → 170 исправлено, 16 корректно
оставлены под ручной проверкой. Независимо верифицировано через MCP
`learn_prod_db` после апдейта.

## Risks / Follow-ups
- tsk-359 (P2, ContentBackbone) — форсинг Stage 3 review / quality-triage
  хелпер для будущих sdamgia/kompege/polyakov-партий, чтобы дефект не повторился.
- Возможный рассинхрон CB-стороны (`content_hub.external_tasks_task`) —
  LMS-фикс был прямым UPDATE в LMS-БД, минуя штатный CB xlsx-review-flow;
  CB может по-прежнему считать эти 170 заданий непройденными Stage 3
  (зафиксировано в tsk-359, не проверялось — CB prod DB в этой сессии не
  дала доступа к таблице `external_tasks_task`).
- 16 оставленных заданий по-прежнему в ручной очереди — оператор просил
  разобрать их вручную отдельно в этой же сессии (ещё не сделано).
