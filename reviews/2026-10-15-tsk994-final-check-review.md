# Review-gate: tsk-994 (П5) — итоговый срез после курса

Дата: 2026-10-15. Постановка: `docs/briefs/2026-10-14-p3-p5-retrieval-help-gate-final-check.md`, Фаза C (шаги 13–17), Р4, «Границы», «Ограничения», критерии 1–3, 6–8, «Риски».
Объект: изменения в LMS (ветка `claude/tsk-994-final-check`) и SPW (та же ветка). Ревью — независимым агентом по диффу до коммита; образец для сверки — `app/services/retrieval_service.py` и `reviews/2026-10-14-tsk992-retrieval-review.md`.

## Вердикт

**ПРИНЯТО** — BLOCKER и MAJOR нет, 12 MINOR. Классы ошибок ревью П3 не повторены: замок строки прогона держится до записи (тест гонки ловит снятие замка), гейты записи/оплаты/выпускника/зависимостей/активности курса на месте, исключение `final_check` есть во всех расчётах статуса (единое правило), сдачи среза помечены в истории и ленте, модель совпадает с миграцией, даты только с поясом.

## Что проверено

| Пункт | Результат |
|---|---|
| Шаг 13: рубильник | `courses.final_check_config` JSONB NULL; `GET/PUT /courses/{id}/final-check`, `require_role("methodist","admin")`; включается только `enabled is True`; размер 1…50 (PUT вне пределов — 422) |
| Шаг 14: выборка | `select_task_ids`: `deterministic_seed(..., salt="final")` (без соли ключ прежний — выборка tsk-314 цела); главы по кругу, ≥1 на главу при глав ≤ size, при глав > size порядок глав перемешан сидом. «Пройдено» — последний результат учебного пути проходной и это своя сдача (не `manual_teacher`); фильтр `task_supports_review`. Состав в `final_check_runs`, уникальность (ученик, курс), гонка первого открытия — `ON CONFLICT DO NOTHING` |
| Шаг 15: гейт | 409 `final_check_locked` при выключенном рубильнике или `compute_course_state != COMPLETED` — на старте и на каждом ответе |
| Гейты записи | GET — оплата; start/answer — оплата и «Выпускник»; answer — `assert_task_access`, `assert_course_not_locked`, `assert_course_active_for_student` (курс задания и корень), гейт 2.3f → 422 |
| Шаг 16: Р4 | Тест `test_wrong_answer_keeps_passed_limit_course_gaps_and_p4`: после неверного ответа среза `compute_task_state` (с корнем и без), batch, `compute_course_state`, syllabus, лимит 2 из 2, пробелы, `has_own_attempt` — без изменений; обычная сдача принимается. Кеш `student_course_state` не трогается. Планировщик П3 на ошибку среза не срабатывает |
| Итог | доля верных, главы с ошибками, порог 80 %; при включённом П3 ошибки → стадия 0, +3 дня через `retrieval_service.schedule_forgotten` в той же транзакции |
| Миграция | `tsk994_final_check` ← `tsk992_task_review_schedule`, одна голова; `upgrade` → `downgrade -1` → `upgrade` на чистой БД прошли |

## Места с `retrieval` и учтён ли `final_check`

| Место | `final_check` |
|---|---|
| `learning_gaps_service.learning_path_results_filter` / `_clause` — статус задания, счёт попыток, «пройдено» (используют `learning_engine_service`, `me_service`, `task_results_service`, `content_grace_service`, `assignment_rules_service`, `homework_service`, `student_dashboard_service`, `retention_service`, `teacher_lesson_summary_service`, `teacher_queue_service`, `manual_progress_service`) | исключён (`NON_LEARNING_PATH_SOURCES`) |
| `attempts.py` `_build_attempt_with_results` (`GET /attempts/{id}`, `/finish`) | исключён |
| `task_results_extra.py` очередь необязательного просмотра | исключён |
| `task_results_service.py` суммы баллов | исключён |
| `teacher_lesson_summary_service.py` «последнее решённое» | исключён |
| `retrieval_service.schedule_after_pass` (триггер и «провал до сдачи») | исключён |
| `REAL_STUDENT_SOURCES` (пробелы, явка, `course_position`, `topic_mastery`, `learning_gap_signals`) | не входит (белый список) |
| `ai_tutor/session_service.py` (белый список `spw_web`) | не входит |
| `own_activity_results_filter` (серия, куратор, напоминание «вернись») | **входит** — ответ среза считается активностью, как повтор |
| `task_history_service`, `me_service` `/me/history`, `teacher_activity_feed_service` | помечен: `is_final_check`, номер попытки не растёт, в фильтр `failed` не попадает, «проверил в итоговом срезе» |
| `learning_engine_service.last_own_attempt_id` / `has_own_attempt` (П4) | не исключён; вреда нет — в срез идут только задания со своей проходной сдачей, значит `has_own_attempt` уже истинна (тест) |
| `stuck_tasks_service`, `program_scope_service`, `quiz_funnel_service` и др. без фильтра источника | срез не влияет: задания в срезе уже пройдены, задания с проверкой преподавателем в срез не идут |

## Находки и разбор исполнителем

| № | Находка | Решение |
|---|---|---|
| 1 | Выпускник видит «Пройти срез», но старт даёт 403 | Исправлено: статус `closed` в GET, карточка не рендерится, экран среза пишет понятный текст. Тест `test_alumni_can_see_but_not_take` |
| 2 | Задание удалили из базы посреди среза — итог не посчитается | Исправлено: повторное открытие (`start`) исключает удалённые задания и считает итог; SPW открывает срез заново, когда неотвеченных нет, а итога нет. Тесты `test_deleted_task_is_skipped_on_reopen` и vitest |
| 3 | SPW: после последнего ответа сразу итог, результат ответа не виден | Исправлено: результат держит страница до «Посмотреть итог». vitest |
| 4 | `start` не проверяет активность курса и замок зависимости | Оставлено: старт пишет только строку прогона, каждый ответ эти гейты проверяет |
| 5 | Docstring «только чтение» у `course_completed` неточен (эскалация в `compute_course_state`) | Исправлено: docstring; поведение то же, что у любого вызова `compute_course_state` |
| 6 | Теоретическая взаимная блокировка «срез ↔ повтор» в двух вкладках | Оставлено: вероятность ничтожна, PostgreSQL снимает одну транзакцию целиком, данные не портятся |
| 7 | Итог «0 из 0» выглядит провалом | Исправлено: «Срез не состоялся: задания больше не проверяются автоматически» |
| 8 | Ссылки ведут на ошибочные задания глав, а не на сами главы | Оставлено осознанно: у программы курса нет якорей на главы, а страница подкурса перенаправляет на корень; ссылка «Разобрать: …» ведёт туда, где ошибку можно разобрать |
| 9 | Правильный ответ можно подсмотреть на экране пройденного задания | Вынесено оператору (как MINOR 11 ревью П3) |
| 10 | PUT рубильника не проверяет, что курс корневой | Оставлено, как у `retrieval_config`; включать — только на корне |
| 11 | При глав > size срез покрывает случайные главы | Задокументировано |
| 12 | Метка `asyncio` на синхронных тестах; нет тестов на № 2, 3 | Исправлено: метка снята (`asyncio_mode = auto`), тесты добавлены |

## Прогоны

- LMS, полный `pytest` до правок по ревью: 161 failed / 4156 passed / 141 skipped / 17 errors. На чистом main в той же среде: 161 failed / 4133 passed / 17 errors. **Наборы упавших тестов совпадают полностью**, новых падений нет (падения — среда облака: хранилище файлов, внешние сервисы и т. п.; известные на main `test_tsk301_ai_spend_guard`, `test_tsk521_link_audit`, `test_tx_isolation_optout` — в их числе).
- После правок по ревью: `tsk994` (24), `tsk992`, `tsk993`, движок, syllabus, пробелы, лимит попыток, выборка, история — 136 passed, 22 skipped.
- SPW: `tsc --noEmit` — 0; eslint по затронутым файлам — 0 ошибок (5 старых предупреждений в нетронутых строках); vitest затронутых файлов — зелёные.

Итог после правок: **ПРИНЯТО**.
