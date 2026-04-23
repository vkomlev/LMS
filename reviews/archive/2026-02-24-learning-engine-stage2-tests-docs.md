# Review: Learning Engine этап 2 — тесты, документация, правки сервиса

**Дата:** 2026-02-24

**Контекст:** Завершение этапа 2 (Service layer) по ТЗ: тесты для Learning Engine Service, техдок по next item и производительности, обновление плана внедрения. Правки в сервисе: убран `db.commit()` из `compute_course_state`, исправлена сортировка детей курса (NULLS LAST), упрощена проверка зависимостей в `resolve_next_item`.

**Изменённые/новые файлы:**
- `app/services/learning_engine_service.py` — правки (commit, sort, deps)
- `docs/learning-engine-next-item.md` — новый техдок
- `docs/learning-engine-v1-implementation-plan.md` — этап 2 отмечен как done
- `tests/test_learning_engine_service.py` — новые интеграционные тесты

Полный diff: [2026-02-24-learning-engine-stage2-tests-docs.diff](./2026-02-24-learning-engine-stage2-tests-docs.diff)

```diff
--- правки в learning_engine_service: убран commit, сортировка детей (0 if x[1] is not None else 1, ...), проверка зависимостей через compute_course_state
--- добавлены тесты: get_effective_attempt_limit, compute_task_state, compute_course_state, resolve_next_item
--- добавлен docs/learning-engine-next-item.md, обновлён learning-engine-v1-implementation-plan.md (этап 2 done)
```
