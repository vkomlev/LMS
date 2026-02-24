# Контракт комментария в ответах (SA_COM)

**Дата:** 2026-02-24

## Контекст

ТЗ: расширение JSON-контракта для хранения комментария ученика в задачах с комментарием (SA_COM): приём, сохранение в task_results.answer_json, выдача во всех API.

## Изменения

- **app/schemas/checking.py:** В `StudentResponse` добавлено поле `comment: Optional[str]` (опционально, на scoring не влияет).
- **app/services/checking_service.py:** В docstring `_check_short_answer` зафиксировано, что в расчёте баллов участвует только `response.value`, `response.comment` только сохраняется.
- **app/services/task_results_service.py:** Без изменений — `answer.model_dump()` уже включает все поля, в т.ч. comment.
- **Документация:** assignments-and-results-api.md, api-reference.md, api-examples.md, results-contract-answers.md — описаны/примеры для SA_COM с полем comment.
- **tests/test_attempts_answers_comment.py:** Автотесты API (4 кейса: value+comment, только value, GET возвращает comment, регрессия без comment).
- **scripts/smoke_attempts_answers_comment.ps1:** Smoke: создание попытки, ответ с comment, finish, GET и проверка comment в ответе; проверка в БД — через MCP или SQL.

## Начало diff

```diff
diff --git a/app/schemas/checking.py b/app/schemas/checking.py
--- a/app/schemas/checking.py
+++ b/app/schemas/checking.py
@@ -54,6 +54,12 @@ class StudentResponse(BaseModel):
     text: Optional[str] = Field(
         ...
     )
+    comment: Optional[str] = Field(
+        default=None,
+        description="Комментарий ученика (SA_COM и др. типы с комментарием). Только хранение и выдача, на проверку/баллы не влияет.",
+        ...
+    )
     meta: Optional[Dict[str, Any]] = Field(
```

Полный diff: [reviews/2026-02-24-sa-com-comment-contract.diff](2026-02-24-sa-com-comment-contract.diff)
