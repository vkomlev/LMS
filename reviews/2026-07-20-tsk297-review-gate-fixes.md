# tsk-297 — устранение находок review-gate

Ветка: `tsk-297-manual-progress`. Артефакт по правилу «Review-changes» (`.claude/CLAUDE.md`).
Исходная реализация — `reviews/2026-07-20-tsk297-manual-progress.md`; здесь только правки
по блокирующим находкам независимого ревью.

## Что исправлено

| Находка | Суть дефекта | Правка |
|---|---|---|
| S1-1 (критично) | `create_attempt` → `BaseRepository.create(commit=True)` коммитил внутри операции: транзакционный `pg_advisory_xact_lock` отпускался до вставки `task_results` (идемпотентность мнимая), массовый зачёт фиксировался кусками без аудита, оставались попытки-сироты | Попытка вставляется сырым `INSERT ... RETURNING id` в той же транзакции. `db.commit()` в сервисе нет нигде — коммитит только роутер |
| S2-1 (высоко) | `set_material_completed` не трогал `source` при `DO UPDATE`: после «преподаватель отметил → ученик прошёл сам» строка оставалась `manual_teacher`, и снятие отметки удаляло реальный прогресс | В `DO UPDATE` добавлено `source = 'system'` — реальное прохождение перебивает провенанс |
| S2-2 (высоко) | Ни grant, ни revoke не обновляли `student_course_state`, откуда `me_service` берёт `is_completed` | Хелпер `_refresh_course_state` зовёт `compute_course_state(..., update_state_table=True)` по активным корням узла. Один раз на операцию (в массовой — в конце). На чтении (`get_student_progress`) по-прежнему НЕ зовётся |
| S3-1 (средне) | Ветка курсового ACL не проверяла отношение ученика к курсу: преподаватель курса X мог править прогресс любого `user_id` перебором | В ветке ACL дополнительно требуется активная запись ученика на корень дерева (`list_active_roots_of_node`) |
| S4-1 | Два теста были зелёными по совпадению | Проверка «чтение не эскалирует» вынесена в отдельный тест с реальным непроверенным `SA_COM` и шпионом на `compute_course_state`; next-item ассертит конкретный ожидаемый `task_id` |
| S4-3 | `max_score <= 0` давал `granted: true` без PASSED; докстрока/ТЗ/имя теста обещали возврат в `OPEN` | `max(1, max_score)`; формулировки уточнены (возврат — к состоянию реальных попыток); тест переименован в `test_revoke_cancels_synthetic_attempt` |

Внутренний флаг `_audit` переименован в `_standalone`: он теперь гейтит и аудит, и пересчёт
состояния курса — обе вещи делаются один раз на операцию.

## Изменённые файлы

- `app/services/manual_progress_service.py`
- `app/services/learning_events_service.py` (`set_material_completed`)
- `tests/test_manual_progress_tsk297.py`
- `docs/specs/2026-07-20-tech-spec-tsk297-manual-progress.md` (§ Обратимость)

`app/api/v1/teacher_progress.py` не менялся: коммит уже был только в роутере.

## Проверка

```
.venv/Scripts/python.exe -m pytest tests/test_manual_progress_tsk297.py -q
27 passed

.venv/Scripts/python.exe -m pytest tests/test_manual_progress_tsk297.py tests/test_y6_review_loop.py \
  tests/test_learning_engine_service.py tests/test_learning_api_routes.py tests/test_y62_syllabus_states.py -q
67 passed (три прогона подряд)
```

Новые тесты проверены на невакуумность: при временно откаченных правках S2-1/S2-2/S3-1
падают `test_real_completion_overrides_manual_provenance`,
`test_bulk_operations_refresh_course_state`, `test_api_course_acl_requires_student_enrollment`.

## Follow-up (в этой правке не трогали)

- N+1 в `get_student_progress` (`compute_task_state` на каждое задание) — вынесен ревью в follow-up.
- Поведение квизов — там же.
- Суита работает на общей БД: сразу после прогона с падениями следующий прогон может дать
  ошибки в setup фикстуры из-за недоубранных строк. На чистой БД повторяется стабильно зелёным.
