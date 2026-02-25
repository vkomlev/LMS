# Review: Learning Engine V1, этап 7 (Docs + smoke)

**Дата:** 2026-02-25

**Контекст:** Заключительный этап по ТЗ [tz-learning-engine-stage7-docs-smoke.md](docs/tz-learning-engine-stage7-docs-smoke.md): актуализация документации и сквозной smoke по маршруту API + БД + логи.

**Изменения:**

1. **docs/assignments-and-results-api.md** (новый)  
   Единый контракт: задачи (hints_text, hints_video, has_hints из task_content), попытки (time_expired, attempts_used, attempts_limit_effective, last_based_status), статистика (last-based и дополнительные поля), Learning API (таблица эндпоинтов), обратная совместимость. Ссылки на этапные техдоки (hints-stage5, last-attempt-statistics-stage6, attempts-integration-stage4, smoke-learning-api, smoke-learning-engine-stage7).

2. **docs/api-reference.md**  
   - В содержание добавлен пункт «Learning API (Learning Engine V1)».  
   - Новая секция «Learning API (Learning Engine V1)»: таблица из 6 эндпоинтов, ссылки на assignments-and-results-api и smoke-learning-api.  
   - В ответе GET /attempts/by-user: пример дополнен полями time_expired, attempts_used, attempts_limit_effective, last_based_status и ссылкой на техдоки.  
   - В ответе GET /tasks/by-course: в пример элемента добавлены hints_text, hints_video, has_hints, абзац про этап 5 и ссылки.  
   - Нумерация разделов 8–12 исправлена после вставки Learning API.

3. **docs/api-examples.md**  
   Добавлен блок «Learning Engine V1» со ссылками на assignments-and-results-api, smoke-learning-api, hints-stage5, last-attempt-statistics-stage6, smoke-learning-engine-stage7.

4. **docs/smoke-learning-engine-stage7.md** (новый)  
   Сквозной smoke: подготовка данных (MCP/SQL), порядок вызовов API (next-item, materials/complete, start-or-get-attempt, answers, finish, GET attempt, GET tasks + list, stats by-user/by-course/by-task, request-help, override), проверки БД (attempts, task_results, student_material_progress, student_task_limit_override, learning_events, last-attempt), проверки логов, артефакты и ограничения. Команды приведены в формате PowerShell.

5. **scripts/smoke_learning_engine_stage7.ps1** (новый)  
   Автоматизированный прогон ключевых шагов: параметры из env (API_KEY, USER_ID, COURSE_ID, TASK_ID; опционально HOST, MATERIAL_ID, UPDATED_BY). Вызовы next-item, materials/complete (если MATERIAL_ID), start-or-get-attempt, GET attempt, GET task + list (проверка hints), stats by-user/by-course/by-task (проверка last-based полей), request-help, teacher/task-limits/override (если UPDATED_BY). Вывод [PASS]/[SKIP]/[FAIL], exit 0/1.

6. **docs/learning-engine-v1-implementation-plan.md**  
   Этап 7 отмечен как **done**, добавлено описание реализованного (assignments-and-results-api, обновления api-reference и api-examples, smoke-learning-engine-stage7.md и smoke_learning_engine_stage7.ps1).

**Проверки:**  
Скрипт `smoke_learning_engine_stage7.ps1` запускается; при неверном API_KEY ожидаемо получаем 403 на защищённых эндпоинтах. Для полного прохождения smoke нужны валидные API_KEY, USER_ID, COURSE_ID, TASK_ID и запущенное приложение.

Полный diff: [2026-02-25-learning-engine-stage7-docs-smoke.diff](2026-02-25-learning-engine-stage7-docs-smoke.diff)
