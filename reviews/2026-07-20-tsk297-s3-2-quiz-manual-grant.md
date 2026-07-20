# tsk-297 / S3-2 — запрет ручного зачёта квиз-вопросов

Дата: 2026-07-20 · Скилл: `/fastapi-api-developer` · Задача: `tsk-297` (follow-up ревью)

## Контекст

Ревью tsk-297 нашло (S3-2, не блокирующее): `manual_progress_service.grant_task`
не заполняет `task_results.scale_scores`, из-за чего для квизов (`SC_Qw`/`MC_Qw`)
ручной зачёт давал два побочных эффекта:

1. пока зачёт стоит, ученик получает 409 при реальной попытке ответить —
   `app/api/v1/attempts.py` шаг 2.3a отклоняет повторный ответ на квиз при
   наличии результата в неотменённой попытке. Снималось только снятием зачёта;
2. правило назначения курсов `trigger_event='quiz_scale'` (ADR-0003, argmax по
   накопленным `scale_scores`) видело квиз отвеченным с нулевым вкладом во все
   шкалы — назначение по итогам диагностики уходило неверным.

## Принятое решение

Вариант **(а)** — запретить ручной зачёт квиз-типов на уровне API, с уточнением:
**запрет только на выдачу, снятие остаётся разрешённым**. Иначе уже поставленный
зачёт было бы нечем убрать, а обратимость важнее симметрии.

Обоснование выбора (а), а не (б)/(в): квиз — диагностический инструмент, а не
учебное задание, которое ученик мог «освоить вне платформы». Нейтральные
`scale_scores` (б) означали бы придуманный преподавателем результат диагностики;
исключение зачтённых квизов из argmax (в) чинило бы только второй эффект, оставив
первый (заблокированный ответ ученика).

## Состояние прода на момент правки

Read-only проверка (MCP `learn_prod_db`): 3 активных квиз-задания (id 5266/5317/5320,
все `SC_Qw`, `requirement_level='required'`), ручных зачётов по ним — **0**.
Миграция и чистка данных не требуются, закрывается только путь вперёд.

## Changed Files

| Файл | Что |
|---|---|
| `app/services/manual_progress_service.py` | `is_quiz_task` / `ensure_task_grantable` (422); гейт в `grant_task` до проверки «уже PASSED»; `grant_course_subtree` пропускает квизы со счётчиком `skipped_quiz`; `get_student_progress` отдаёт `manual_grantable`; тип задания подтягивается в `_load_task` и `_tree_task_rows` |
| `app/api/v1/teacher_progress.py` | `ProgressBulkResponse.skipped_quiz`, `ProgressTreeItem.manual_grantable` |
| `tests/test_manual_progress_tsk297.py` | квиз в фикстуре графа + 7 тестов; правка двух тестов, опиравшихся на «массовый зачёт закрывает курс» |

Поведение по операциям:

* `POST .../tasks/{quiz_id}` → **422** с русским detail (кнопка в SPW прячется по
  `manual_grantable=false`, но сервер — источник истины);
* `POST .../courses/{id}` → квизы дерева **пропускаются**, не роняя операцию 422:
  один диагностический вопрос не должен блокировать зачёт остальных элементов
  темы. Пропущенные считаются в `skipped_quiz`;
* `DELETE .../tasks/{quiz_id}` → **разрешено**, идемпотентно (`already=true`).

## Контракт для SPW

Новое поле дерева прогресса `manual_grantable: bool` (у квизов `false`, у прочих
`true`). Кнопку зачёта прятать по нему, **а не по типу задания**: тип в дереве не
отдаётся, и дублирование списка квиз-типов во фронте разъехалось бы с сервером
при следующем новом типе. На снятие отметки флаг не влияет.

Новое поле массового ответа `skipped_quiz: int` (по умолчанию 0; у операции
снятия всегда 0) — показать в отчёте о массовом зачёте, иначе преподаватель не
поймёт, почему тема не закрылась целиком.

## Validation Commands

```
.venv/Scripts/python.exe -m pytest tests/test_manual_progress_tsk297.py -q -p no:randomly
.venv/Scripts/python.exe -m pytest tests/test_quiz_scales_tsk122.py \
    tests/test_quiz_scale_trigger_tsk122.py tests/test_quiz_single_attempt_tsk124.py \
    tests/test_assignment_rules_tsk031.py tests/test_attempts_limit_enforced_tsk269.py \
    -q -p no:randomly
```

Результат: `36 passed` и `47 passed` соответственно. Новые квиз-тесты отдельно —
3 прогона подряд `7 passed`, стабильно.

## Новые тесты

* `test_quiz_grant_rejected` — 422, результат не создаётся;
* `test_quiz_stays_answerable_after_rejected_grant` — после отказа предикат гейта
  приёма ответа пуст, т.е. 409 не сработает и ученик может ответить (это и был
  эффект №1);
* `test_quiz_grant_rejected_via_api` — тот же 422 по HTTP с русским detail;
* `test_bulk_grant_skips_quiz_without_failing` — `skipped_quiz=1`, остальные
  3 задания зачтены;
* `test_quiz_revoke_is_allowed` — снятие не запрещено;
* `test_progress_tree_marks_quiz_not_grantable` — `manual_grantable` по типам;
* `test_quiz_scale_scores_survive_only_real_answer` — шкалы копятся только с
  реального ответа (инвариант эффекта №2).

Правка существующих тестов: `test_course_node_status_rolls_up_subtree`,
`test_bulk_operations_refresh_course_state`, `test_progress_read_does_not_escalate`
опирались на «массовый зачёт закрывает курс». Теперь узел с квизом так не
закрывается — это **верное** следствие запрета, поэтому тесты доводят курс до
COMPLETED реальным ответом на квиз (helper `_answer_quiz`), а не прячут квиз из
фильтра обязательности.

## Risks / Follow-ups

1. **Не закоммичено намеренно.** В рабочем дереве параллельно работает другая
   сессия (tsk-297/S3-3 и tsk-332): изменены `app/services/learning_engine_service.py`,
   добавлены `scripts/fix_order_position_*`, и **`tests/test_manual_progress_tsk297.py`
   стал смешанным файлом** — мои квиз-тесты плюс чужой helper `_submit_result`.
   Коммит по pathspec присвоил бы чужое (ADR-0008: pathspec спасает на уровне
   файла, не кусков). Коммит — за оператором, после сведения с той сессией; для
   смешанного файла — процедура `git diff -U0` → `git apply --cached` из
   § «Общий файл» глобальной инструкции.
2. **Прогон всего файла тестов сейчас флапает** — отказы скачут по разным тестам,
   включая нетронутые мной (`test_api_course_acl_requires_student_enrollment`,
   `test_granted_manual_task_not_in_review_queue`). Причина внешняя: 4 чужих
   python-процесса гоняют тесты по той же БД, а session-scoped уборка в conftest
   сносит пользователей `@example.com` чужого прогона. К этой правке отношения не
   имеет; при одиночном прогоне — стабильно зелено.
3. **SPW**: скрыть кнопку зачёта по `manual_grantable`, показать `skipped_quiz` в
   итоге массовой операции. До этого пользователь увидит 422 вместо скрытой
   кнопки — деградация мягкая (внятный текст ошибки), но неаккуратная.
4. **Cross-project memory**: изменился публичный контракт (2 новых поля ответа +
   новый 422 у `POST .../tasks/{id}`) → обновить
   `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` и
   `CHANGELOG.md` вместе с коммитом.
5. `openapi.json` не регенерирован — это делает pre-commit hook, но в общем дереве
   он тянет чужой WIP-эндпоинт в коммит (известная ловушка). Регенерировать при
   коммите, проверив итоговый diff.
