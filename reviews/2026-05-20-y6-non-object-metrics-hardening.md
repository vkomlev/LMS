# 2026-05-20 — Y-6 escalation: защита от non-object metrics

## Инцидент

TG_LMS teacher-бот падал на DETAIL результата с pydantic ValidationError:
`metrics` приходил `[None, {'escalated_at': '...'}, ...]` (994 элемента).

## Root cause

Тестовая строка `task_results.id=985` (`source_system=test_http_409_cancel`)
имела начальный `metrics=[null]` (массив). Escalation cron каждые 20 минут:

1. Гейт `COALESCE(tr.metrics,'{}'::jsonb) ? 'escalated_at'` для **массива**
   возвращает FALSE (jsonb `? key` работает только для object) → строка
   попадает в кандидаты на каждом тике.
2. UPDATE: `COALESCE(metrics,'{}'::jsonb) || jsonb_build_object('escalated_at', ts)`
   — jsonb-оператор `array || object` **аппендит** объект элементом массива
   (не делает merge). Размер растёт линейно.

За ~2 недели (с 2026-05-04) накопилось 994 элемента → бот не может
десериализовать `metrics: Optional[Dict[str, Any]]`.

## Fix

### 1. `escalation_service.py` — гейт исключает non-object metrics

```diff
   WHERE tr.checked_at IS NULL
     AND t.task_content->>'type' IN ('SA_COM','TA')
     AND tr.submitted_at < (now() - (:h || ' hours')::interval)
-    AND NOT (COALESCE(tr.metrics, '{}'::jsonb) ? 'escalated_at')
+    AND (
+        tr.metrics IS NULL
+        OR (
+            jsonb_typeof(tr.metrics) = 'object'
+            AND NOT (tr.metrics ? 'escalated_at')
+        )
+    )
```

Если в БД появится строка с non-object metrics — она будет проигнорирована
escalation'ом (вместо вечного роста массива).

### 2. `methodist_notify_service.py` — нормализация в UPDATE

Все 6 UPDATE-statements (3× `escalated_at`, 3× `completion_escalated_at`):

```diff
- "UPDATE task_results SET metrics = "
- "  COALESCE(metrics,'{}'::jsonb) || jsonb_build_object('escalated_at', CAST(:ts AS text)) "
- "WHERE id = :rid"
+ "UPDATE task_results SET metrics = "
+ "  (CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics ELSE '{}'::jsonb END) "
+ "  || jsonb_build_object('escalated_at', CAST(:ts AS text)) "
+ "WHERE id = :rid"
```

Defense-in-depth: если строка с non-object metrics всё же прошла гейт
(race, legacy данные), UPDATE заменит её на корректный dict вместо роста массива.

### 3. `teacher_queue_service.py` — guard для grade/regrade

```diff
- metrics_dict = dict(metrics_existing) if metrics_existing else {}
+ metrics_dict = dict(metrics_existing) if isinstance(metrics_existing, dict) else {}
```

`dict([None, {...}])` падал бы `TypeError: cannot convert dictionary update`
при попытке учителя поставить оценку строке с битым `metrics`.

### 4. DB-cleanup

`UPDATE task_results SET metrics = '{}'::jsonb WHERE id=985` (одобрено
оператором). После cleanup: 0 строк с non-object metrics в БД.

## Validation

- `pytest tests/test_y6_review_loop.py --deselect ::test_y6_escalation_cron_tick_idempotent`
  → **8 passed**.
- Deselect test'а: пред-существующая проблема — фоновый LMS-сервер держит
  advisory lock, baseline-stash подтверждает что падение не от моих правок.

## Risks / Follow-ups

- Если `is_correct`/`grade` будет правомерно вызван для тестовой записи
  с битым metrics — он начнёт metrics с `{}` (потеря потенциального
  легитимного contents). Для production не критично: единственная такая
  строка очищена; гейт не пропускает новых.
- Defense-in-depth защищает только от типов `array/string/number/boolean`.
  Не защищает от corrupted JSON или namespace-конфликтов — но эти кейсы
  не наблюдались.
