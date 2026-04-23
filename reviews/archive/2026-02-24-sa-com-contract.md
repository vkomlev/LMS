# Контракт SA_COM / SA+COM и observability для POST /attempts/{id}/answers

**Дата:** 2026-02-24

## Контекст

Внедрены правки по ревью контракта ответов ученика (P0–P3):

- **P0:** Принятие алиаса `SA+COM` с нормализацией в `SA_COM` и deprecation-логом.
- **Observability:** Логирование при 400 в `submit_attempt_answers` (attempt_id, при возможности answer.type); при 422 для маршрута attempts/answers — доп. лог с контекстом по полю answer/type.
- **Контрактные тесты:** Документ `docs/results-contract-answers.md` и скрипт `scripts/smoke_attempts_answers_contract.ps1`.

## Изменённые файлы

- `app/schemas/checking.py` — BeforeValidator для типа ответа: SA+COM → SA_COM, лог deprecation.
- `app/api/v1/attempts.py` — логгер, логи при 400 (завершённая попытка, таймаут, пустой items, задача не найдена, несовпадение типа).
- `app/api/main.py` — в обработчике RequestValidationError для пути attempts/answers добавлен лог с контекстом по answer.type.
- `docs/results-contract-answers.md` — новый документ с контрактом и curl-примерами.
- `scripts/smoke_attempts_answers_contract.ps1` — новый скрипт smoke-проверки контракта.

## Начало diff

```diff
diff --git a/app/api/main.py b/app/api/main.py
index 7fabaf7..99a4659 100644
--- a/app/api/main.py
+++ b/app/api/main.py
@@ -125,7 +125,24 @@ async def validation_exception_handler(...)
         if "ctx" in error:
             serializable_error["ctx"] = {k: str(v) for k, v in error["ctx"].items()}
         serializable_errors.append(serializable_error)
-    
+
+    # Observability: для attempts/answers логируем контекст по answer.type
+    path = request.url.path or ""
+    ...
diff --git a/app/schemas/checking.py b/app/schemas/checking.py
--- a/app/schemas/checking.py
+++ b/app/schemas/checking.py
+def _normalize_answer_type(v: Any) -> TaskType:
+    """Нормализует тип ответа: алиас SA+COM -> SA_COM с логом deprecation."""
+    if v == "SA+COM":
+        logger.warning("Deprecation: тип ответа 'SA+COM' устарел, используйте 'SA_COM'.")
+        return "SA_COM"
+    return v
+    ...
+    type: Annotated[TaskType, BeforeValidator(_normalize_answer_type)] = Field(...)
```

Полный diff: [reviews/2026-02-24-sa-com-contract.diff](2026-02-24-sa-com-contract.diff)
