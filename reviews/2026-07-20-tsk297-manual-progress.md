# tsk-297 — правка прогресса ученика (LMS backend)

Дата: 2026-07-20. Ветка: tsk-297-manual-progress.
Решение по модели данных: docs/specs/2026-07-20-tech-spec-tsk297-manual-progress.md
Гейт: /review-gate — 1-й проход ОТКЛОНЕНО (4 блокирующие находки), после правок ПРИНЯТО.
Тесты: tests/test_manual_progress_tsk297.py — 27 passed.

## Diff (tracked)
```diff
diff --git a/app/api/main.py b/app/api/main.py
index d64b731..4da3e21 100644
--- a/app/api/main.py
+++ b/app/api/main.py
@@ -341,6 +341,10 @@ app.include_router(teacher_reviews_router, prefix=API_PREFIX)
 from app.api.v1.teacher_assignments import router as teacher_assignments_router
 app.include_router(teacher_assignments_router, prefix=API_PREFIX)
 
+# Штатная правка прогресса ученика преподавателем (tsk-297)
+from app.api.v1.teacher_progress import router as teacher_progress_router
+app.include_router(teacher_progress_router, prefix=API_PREFIX)
+
 # Upsert правил назначения из публикатора (tsk-120, ADR-0042)
 from app.api.v1.assignment_rules_admin import router as assignment_rules_admin_router
 app.include_router(assignment_rules_admin_router, prefix=API_PREFIX)
diff --git a/app/services/audit_service.py b/app/services/audit_service.py
index 7ba602e..331f495 100644
--- a/app/services/audit_service.py
+++ b/app/services/audit_service.py
@@ -24,6 +24,10 @@ STUDENT_ROLE_AUTO_ASSIGNED = "student.role.auto_assigned"
 AUTH_ROLE_MISSING_SELF_HEALED = "auth.role.missing_self_healed"
 AUTH_TEST_SESSION_ISSUED = "auth.test.session_issued"
 
+# tsk-297 event types (штатная правка прогресса ученика преподавателем)
+TEACHER_PROGRESS_GRANTED = "teacher.progress.granted"
+TEACHER_PROGRESS_REVOKED = "teacher.progress.revoked"
+
 
 async def log_event(
     db: AsyncSession,
diff --git a/app/services/learning_events_service.py b/app/services/learning_events_service.py
index f62fea4..b9c9863 100644
--- a/app/services/learning_events_service.py
+++ b/app/services/learning_events_service.py
@@ -336,6 +336,13 @@ async def set_material_completed(
     """
     Идемпотентный upsert в student_material_progress: status='completed', completed_at=now().
     Возвращает completed_at (текущее значение после upsert).
+
+    tsk-297: при конфликте провенанс переписывается на 'system'. Это путь РЕАЛЬНОГО
+    прохождения материала учеником, и он перебивает ручную отметку преподавателя:
+    иначе после «преподаватель отметил → ученик реально прошёл» строка осталась бы
+    со `source='manual_teacher'`, и снятие ручной отметки удалило бы настоящий
+    прогресс ученика (`manual_progress_service.revoke_material` удаляет строки
+    ровно по этому признаку).
     """
     await db.execute(
         text("""
@@ -344,7 +351,7 @@ async def set_material_completed(
             ON CONFLICT (student_id, material_id)
             DO UPDATE SET status = 'completed', completed_at = COALESCE(
                 student_material_progress.completed_at, now()
-            ), skipped_at = NULL
+            ), skipped_at = NULL, source = 'system'
         """),
         {"student_id": student_id, "material_id": material_id},
     )
diff --git a/docs/openapi.json b/docs/openapi.json
index 366d539..a266f81 100644
--- a/docs/openapi.json
+++ b/docs/openapi.json
@@ -15093,6 +15093,811 @@
         }
       }
     },
+    "/api/v1/teacher/students/{student_id}/progress": {
+      "get": {
+        "tags": [
+          "teacher_progress"
+        ],
+        "summary": "Прогресс ученика по дереву курса (для преподавателя)",
+        "description": "Отдать дерево курса со статусами элементов, флагом ручной отметки и селектор курсов.\n\nБез ``course_id`` отдаётся только ``courses`` (ACL-фильтрованный список\nкурсов ученика), ``items`` пуст: это первый запрос карточки, когда курс ещё\nне выбран. Ошибку доступа в этом режиме не бросаем — преподаватель просто\nувидит пустой список, если ни один курс ученика ему не доступен.",
+        "operationId": "get_progress_api_v1_teacher_students__student_id__progress_get",
+        "security": [
+          {
+            "APIKeyHeader": []
+          },
+          {
+            "APIKeyQuery": []
+          }
+        ],
+        "parameters": [
+          {
+            "name": "student_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "description": "ID ученика",
+              "title": "Student Id"
+            },
+            "description": "ID ученика"
+          },
+          {
+            "name": "course_id",
+            "in": "query",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "integer",
+                  "minimum": 1
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "description": "ID узла курса (берётся всё его дерево). Без него вернётся только список курсов",
+              "title": "Course Id"
+            },
+            "description": "ID узла курса (берётся всё его дерево). Без него вернётся только список курсов"
+          },
+          {
+            "name": "token",
+            "in": "query",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Token"
+            }
+          },
+          {
+            "name": "authorization",
+            "in": "header",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Authorization"
+            }
+          },
+          {
+            "name": "session",
+            "in": "cookie",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Session"
+            }
+          }
+        ],
+        "responses": {
+          "200": {
+            "description": "Successful Response",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/ProgressTreeResponse"
+                }
+              }
+            }
+          },
+          "422": {
+            "description": "Validation Error",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/HTTPValidationError"
+                }
+              }
+            }
+          }
+        }
+      }
+    },
+    "/api/v1/teacher/students/{student_id}/progress/tasks/{task_id}": {
+      "post": {
+        "tags": [
+          "teacher_progress"
+        ],
+        "summary": "Зачесть задание ученику",
+        "description": "Идемпотентно отметить задание пройденным (синтетическая попытка + результат).",
+        "operationId": "grant_task_api_v1_teacher_students__student_id__progress_tasks__task_id__post",
+        "security": [
+          {
+            "APIKeyHeader": []
+          },
+          {
+            "APIKeyQuery": []
+          }
+        ],
+        "parameters": [
+          {
+            "name": "student_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Student Id"
+            }
+          },
+          {
+            "name": "task_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Task Id"
+            }
+          },
+          {
+            "name": "token",
+            "in": "query",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Token"
+            }
+          },
+          {
+            "name": "authorization",
+            "in": "header",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Authorization"
+            }
+          },
+          {
+            "name": "session",
+            "in": "cookie",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Session"
+            }
+          }
+        ],
+        "requestBody": {
+          "content": {
+            "application/json": {
+              "schema": {
+                "$ref": "#/components/schemas/ProgressGrantRequest",
+                "default": {}
+              }
+            }
+          }
+        },
+        "responses": {
+          "200": {
+            "description": "Successful Response",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/ProgressItemResponse"
+                }
+              }
+            }
+          },
+          "422": {
+            "description": "Validation Error",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/HTTPValidationError"
+                }
+              }
+            }
+          }
+        }
+      },
+      "delete": {
+        "tags": [
+          "teacher_progress"
+        ],
+        "summary": "Снять зачёт задания",
+        "description": "Аннулировать синтетические попытки задания; реальные попытки не трогаются.",
+        "operationId": "revoke_task_api_v1_teacher_students__student_id__progress_tasks__task_id__delete",
+        "security": [
+          {
+            "APIKeyHeader": []
+          },
+          {
+            "APIKeyQuery": []
+          }
+        ],
+        "parameters": [
+          {
+            "name": "student_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Student Id"
+            }
+          },
+          {
+            "name": "task_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Task Id"
+            }
+          },
+          {
+            "name": "token",
+            "in": "query",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Token"
+            }
+          },
+          {
+            "name": "authorization",
+            "in": "header",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Authorization"
+            }
+          },
+          {
+            "name": "session",
+            "in": "cookie",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Session"
+            }
+          }
+        ],
+        "responses": {
+          "200": {
+            "description": "Successful Response",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/ProgressItemResponse"
+                }
+              }
+            }
+          },
+          "422": {
+            "description": "Validation Error",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/HTTPValidationError"
+                }
+              }
+            }
+          }
+        }
+      }
+    },
+    "/api/v1/teacher/students/{student_id}/progress/materials/{material_id}": {
+      "post": {
+        "tags": [
+          "teacher_progress"
+        ],
+        "summary": "Отметить материал пройденным",
+        "description": "Идемпотентно отметить материал пройденным от лица преподавателя.",
+        "operationId": "grant_material_api_v1_teacher_students__student_id__progress_materials__material_id__post",
+        "security": [
+          {
+            "APIKeyHeader": []
+          },
+          {
+            "APIKeyQuery": []
+          }
+        ],
+        "parameters": [
+          {
+            "name": "student_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Student Id"
+            }
+          },
+          {
+            "name": "material_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Material Id"
+            }
+          },
+          {
+            "name": "token",
+            "in": "query",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Token"
+            }
+          },
+          {
+            "name": "authorization",
+            "in": "header",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Authorization"
+            }
+          },
+          {
+            "name": "session",
+            "in": "cookie",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Session"
+            }
+          }
+        ],
+        "requestBody": {
+          "content": {
+            "application/json": {
+              "schema": {
+                "$ref": "#/components/schemas/ProgressGrantRequest",
+                "default": {}
+              }
+            }
+          }
+        },
+        "responses": {
+          "200": {
+            "description": "Successful Response",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/ProgressItemResponse"
+                }
+              }
+            }
+          },
+          "422": {
+            "description": "Validation Error",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/HTTPValidationError"
+                }
+              }
+            }
+          }
+        }
+      },
+      "delete": {
+        "tags": [
+          "teacher_progress"
+        ],
+        "summary": "Снять отметку материала",
+        "description": "Удалить только ручную отметку; прохождение самого ученика сохраняется.",
+        "operationId": "revoke_material_api_v1_teacher_students__student_id__progress_materials__material_id__delete",
+        "security": [
+          {
+            "APIKeyHeader": []
+          },
+          {
+            "APIKeyQuery": []
+          }
+        ],
+        "parameters": [
+          {
+            "name": "student_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Student Id"
+            }
+          },
+          {
+            "name": "material_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Material Id"
+            }
+          },
+          {
+            "name": "token",
+            "in": "query",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Token"
+            }
+          },
+          {
+            "name": "authorization",
+            "in": "header",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Authorization"
+            }
+          },
+          {
+            "name": "session",
+            "in": "cookie",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Session"
+            }
+          }
+        ],
+        "responses": {
+          "200": {
+            "description": "Successful Response",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/ProgressItemResponse"
+                }
+              }
+            }
+          },
+          "422": {
+            "description": "Validation Error",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/HTTPValidationError"
+                }
+              }
+            }
+          }
+        }
+      }
+    },
+    "/api/v1/teacher/students/{student_id}/progress/courses/{course_id}": {
+      "post": {
+        "tags": [
+          "teacher_progress"
+        ],
+        "summary": "Массово зачесть всё дерево узла",
+        "description": "Зачесть все задания и материалы дерева узла (фильтр обязательности — как у движка).",
+        "operationId": "grant_course_api_v1_teacher_students__student_id__progress_courses__course_id__post",
+        "security": [
+          {
+            "APIKeyHeader": []
+          },
+          {
+            "APIKeyQuery": []
+          }
+        ],
+        "parameters": [
+          {
+            "name": "student_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Student Id"
+            }
+          },
+          {
+            "name": "course_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Course Id"
+            }
+          },
+          {
+            "name": "token",
+            "in": "query",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Token"
+            }
+          },
+          {
+            "name": "authorization",
+            "in": "header",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Authorization"
+            }
+          },
+          {
+            "name": "session",
+            "in": "cookie",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Session"
+            }
+          }
+        ],
+        "requestBody": {
+          "content": {
+            "application/json": {
+              "schema": {
+                "$ref": "#/components/schemas/ProgressGrantRequest",
+                "default": {}
+              }
+            }
+          }
+        },
+        "responses": {
+          "200": {
+            "description": "Successful Response",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/ProgressBulkResponse"
+                }
+              }
+            }
+          },
+          "422": {
+            "description": "Validation Error",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/HTTPValidationError"
+                }
+              }
+            }
+          }
+        }
+      },
+      "delete": {
+        "tags": [
+          "teacher_progress"
+        ],
+        "summary": "Массово снять зачёты в дереве узла",
+        "description": "Снять ручные зачёты по всему дереву узла; реальный прогресс сохраняется.",
+        "operationId": "revoke_course_api_v1_teacher_students__student_id__progress_courses__course_id__delete",
+        "security": [
+          {
+            "APIKeyHeader": []
+          },
+          {
+            "APIKeyQuery": []
+          }
+        ],
+        "parameters": [
+          {
+            "name": "student_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Student Id"
+            }
+          },
+          {
+            "name": "course_id",
+            "in": "path",
+            "required": true,
+            "schema": {
+              "type": "integer",
+              "minimum": 1,
+              "title": "Course Id"
+            }
+          },
+          {
+            "name": "token",
+            "in": "query",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Token"
+            }
+          },
+          {
+            "name": "authorization",
+            "in": "header",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Authorization"
+            }
+          },
+          {
+            "name": "session",
+            "in": "cookie",
+            "required": false,
+            "schema": {
+              "anyOf": [
+                {
+                  "type": "string"
+                },
+                {
+                  "type": "null"
+                }
+              ],
+              "title": "Session"
+            }
+          }
+        ],
+        "responses": {
+          "200": {
+            "description": "Successful Response",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/ProgressBulkResponse"
+                }
+              }
+            }
+          },
+          "422": {
+            "description": "Validation Error",
+            "content": {
+              "application/json": {
+                "schema": {
+                  "$ref": "#/components/schemas/HTTPValidationError"
+                }
+              }
+            }
+          }
+        }
+      }
+    },
     "/api/v1/assignment-rules/bulk-upsert": {
       "post": {
         "tags": [
@@ -24802,6 +25607,254 @@
         "title": "PendingReviewListResponse",
         "description": "Ответ списка очереди проверки преподавателя."
       },
+      "ProgressBulkResponse": {
+        "properties": {
+          "student_id": {
+            "type": "integer",
+            "title": "Student Id"
+          },
+          "course_id": {
+            "type": "integer",
+            "title": "Course Id"
+          },
+          "tasks_affected": {
+            "type": "integer",
+            "title": "Tasks Affected"
+          },
+          "materials_affected": {
+            "type": "integer",
+            "title": "Materials Affected"
+          },
+          "skipped_already": {
+            "type": "integer",
+            "title": "Skipped Already"
+          }
+        },
+        "type": "object",
+        "required": [
+          "student_id",
+          "course_id",
+          "tasks_affected",
+          "materials_affected",
+          "skipped_already"
+        ],
+        "title": "ProgressBulkResponse",
+        "description": "Ответ массовой операции по дереву узла."
+      },
+      "ProgressGrantRequest": {
+        "properties": {
+          "comment": {
+            "anyOf": [
+              {
+                "type": "string",
+                "maxLength": 500
+              },
+              {
+                "type": "null"
+              }
+            ],
+            "title": "Comment",
+            "description": "Причина правки прогресса (до 500 символов), попадает в аудит"
+          }
+        },
+        "type": "object",
+        "title": "ProgressGrantRequest",
+        "description": "Тело POST-операций: необязательная причина/пояснение преподавателя."
+      },
+      "ProgressItemResponse": {
+        "properties": {
+          "student_id": {
+            "type": "integer",
+            "title": "Student Id"
+          },
+          "item_type": {
+            "type": "string",
+            "enum": [
+              "task",
+              "material"
+            ],
+            "title": "Item Type"
+          },
+          "item_id": {
+            "type": "integer",
+            "title": "Item Id"
+          },
+          "granted": {
+            "type": "boolean",
+            "title": "Granted",
+            "description": "True — элемент отмечен пройденным, False — отметка снята"
+          },
+          "already": {
+            "type": "boolean",
+            "title": "Already",
+            "description": "True — состояние уже было таким, ничего не менялось"
+          },
+          "source": {
+            "type": "string",
+            "title": "Source",
+            "description": "Провенанс отметки (`manual_teacher`)"
+          },
+          "attempt_id": {
+            "anyOf": [
+              {
+                "type": "integer"
+              },
+              {
+                "type": "null"
+              }
+            ],
+            "title": "Attempt Id",
+            "description": "ID созданной синтетической попытки (только для задания)"
+          }
+        },
+        "type": "object",
+        "required": [
+          "student_id",
+          "item_type",
+          "item_id",
+          "granted",
+          "already",
+          "source"
+        ],
+        "title": "ProgressItemResponse",
+        "description": "Ответ единичной операции над заданием/материалом."
+      },
+      "ProgressTreeItem": {
+        "properties": {
+          "item_type": {
+            "type": "string",
+            "enum": [
+              "course",
+              "task",
+              "material"
+            ],
+            "title": "Item Type"
+          },
+          "item_id": {
+            "type": "integer",
+            "title": "Item Id"
+          },
+          "course_id": {
+            "type": "integer",
+            "title": "Course Id"
+          },
+          "parent_course_id": {
+            "anyOf": [
+              {
+                "type": "integer"
+              },
+              {
+                "type": "null"
+              }
+            ],
+            "title": "Parent Course Id",
+            "description": "Узел, которому элемент принадлежит; null у запрошенного корня"
+          },
+          "title": {
+            "anyOf": [
+              {
+                "type": "string"
+              },
+              {
+                "type": "null"
+              }
+            ],
+            "title": "Title"
+          },
+          "status": {
+            "type": "string",
+            "title": "Status",
+            "description": "Задание: OPEN | IN_PROGRESS | FAILED | PASSED | BLOCKED_LIMIT. Материал: NOT_STARTED | COMPLETED | SKIPPED. Узел курса: NOT_STARTED | IN_PROGRESS | COMPLETED"
+          },
+          "manual": {
+            "anyOf": [
+              {
+                "type": "boolean"
+              },
+              {
+                "type": "null"
+              }
+            ],
+            "title": "Manual",
+            "description": "True — отметка поставлена вручную; у узлов курса всегда null"
+          },
+          "granted_by": {
+            "anyOf": [
+              {
+                "type": "integer"
+              },
+              {
+                "type": "null"
+              }
+            ],
+            "title": "Granted By",
+            "description": "Кто поставил ручную отметку (у материалов всегда null)"
+          },
+          "granted_at": {
+            "anyOf": [
+              {
+                "type": "string",
+                "format": "date-time"
+              },
+              {
+                "type": "null"
+              }
+            ],
+            "title": "Granted At"
+          }
+        },
+        "type": "object",
+        "required": [
+          "item_type",
+          "item_id",
+          "course_id",
+          "status"
+        ],
+        "title": "ProgressTreeItem",
+        "description": "Элемент дерева курса в карточке ученика.\n\nПорядок элементов в ответе — учебный (post-order обход дерева движком плюс\n``order_position`` внутри узла); клиент его не пересортировывает."
+      },
+      "ProgressTreeResponse": {
+        "properties": {
+          "student_id": {
+            "type": "integer",
+            "title": "Student Id"
+          },
+          "course_id": {
+            "anyOf": [
+              {
+                "type": "integer"
+              },
+              {
+                "type": "null"
+              }
+            ],
+            "title": "Course Id",
+            "description": "Запрошенный узел; null, если course_id не передан"
+          },
+          "courses": {
+            "items": {
+              "$ref": "#/components/schemas/StudentCourseRef"
+            },
+            "type": "array",
+            "title": "Courses",
+            "description": "Активные курсы ученика, доступные этому преподавателю"
+          },
+          "items": {
+            "items": {
+              "$ref": "#/components/schemas/ProgressTreeItem"
+            },
+            "type": "array",
+            "title": "Items",
+            "description": "Плоское дерево в учебном порядке; пусто, если course_id не передан"
+          }
+        },
+        "type": "object",
+        "required": [
+          "student_id"
+        ],
+        "title": "ProgressTreeResponse",
+        "description": "Прогресс ученика по дереву курса + список доступных курсов для селектора."
+      },
       "QuizRules": {
         "properties": {
           "scales": {
@@ -26148,6 +27201,31 @@
         "title": "StudentAnswer",
         "description": "Обёртка вокруг ответа ученика.\n\nМожет использоваться как для stateless-проверки, так и внутри попыток (attempts)."
       },
+      "StudentCourseRef": {
+        "properties": {
+          "course_id": {
+            "type": "integer",
+            "title": "Course Id"
+          },
+          "title": {
+            "anyOf": [
+              {
+                "type": "string"
+              },
+              {
+                "type": "null"
+              }
+            ],
+            "title": "Title"
+          }
+        },
+        "type": "object",
+        "required": [
+          "course_id"
+        ],
+        "title": "StudentCourseRef",
+        "description": "Курс ученика для селектора в карточке."
+      },
       "StudentResponse": {
         "properties": {
           "selected_option_ids": {
```

## Новые файлы

### app/api/v1/teacher_progress.py
```python
"""API штатной правки прогресса ученика преподавателем (tsk-297).

Базовый префикс: ``/api/v1/teacher/students/{student_id}/progress``.

* ``GET    ?course_id=``            — дерево курса ученика со статусами и флагом `manual`
* ``POST   /tasks/{task_id}``       — зачесть задание
* ``DELETE /tasks/{task_id}``       — снять зачёт задания
* ``POST   /materials/{id}``        — отметить материал пройденным
* ``DELETE /materials/{id}``        — снять отметку материала
* ``POST   /courses/{course_id}``   — массово зачесть дерево узла
* ``DELETE /courses/{course_id}``   — массово снять зачёты в дереве узла

Гейт: роль ``teacher`` / ``methodist`` / ``admin`` (или сервисный токен) плюс
scoped-ACL `ensure_can_edit_progress` — наличия роли мало, teacher правит только
своих учеников или учеников на закреплённых за ним курсах.

Все операции идемпотентны: повторный вызов не создаёт дубль и не ошибается —
возвращает ``already=true``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, require_role
from app.auth.current_user import CurrentUser
from app.services import manual_progress_service

logger = logging.getLogger("api.teacher_progress")

router = APIRouter(tags=["teacher_progress"])

_PROGRESS_GATE = require_role("teacher", "methodist", "admin")
_BASE = "/teacher/students/{student_id}/progress"


# ─── Схемы ──────────────────────────────────────────────────────────────────


class ProgressGrantRequest(BaseModel):
    """Тело POST-операций: необязательная причина/пояснение преподавателя."""

    comment: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Причина правки прогресса (до 500 символов), попадает в аудит",
    )


class ProgressItemResponse(BaseModel):
    """Ответ единичной операции над заданием/материалом."""

    student_id: int
    item_type: Literal["task", "material"]
    item_id: int
    granted: bool = Field(description="True — элемент отмечен пройденным, False — отметка снята")
    already: bool = Field(description="True — состояние уже было таким, ничего не менялось")
    source: str = Field(description="Провенанс отметки (`manual_teacher`)")
    attempt_id: Optional[int] = Field(
        default=None, description="ID созданной синтетической попытки (только для задания)"
    )


class ProgressBulkResponse(BaseModel):
    """Ответ массовой операции по дереву узла."""

    student_id: int
    course_id: int
    tasks_affected: int
    materials_affected: int
    skipped_already: int


class ProgressTreeItem(BaseModel):
    """Элемент дерева курса в карточке ученика.

    Порядок элементов в ответе — учебный (post-order обход дерева движком плюс
    ``order_position`` внутри узла); клиент его не пересортировывает.
    """

    item_type: Literal["course", "task", "material"]
    item_id: int
    course_id: int
    parent_course_id: Optional[int] = Field(
        default=None,
        description="Узел, которому элемент принадлежит; null у запрошенного корня",
    )
    title: Optional[str] = None
    status: str = Field(
        description=(
            "Задание: OPEN | IN_PROGRESS | FAILED | PASSED | BLOCKED_LIMIT. "
            "Материал: NOT_STARTED | COMPLETED | SKIPPED. "
            "Узел курса: NOT_STARTED | IN_PROGRESS | COMPLETED"
        )
    )
    manual: Optional[bool] = Field(
        default=None,
        description="True — отметка поставлена вручную; у узлов курса всегда null",
    )
    granted_by: Optional[int] = Field(
        default=None, description="Кто поставил ручную отметку (у материалов всегда null)"
    )
    granted_at: Optional[datetime] = None


class StudentCourseRef(BaseModel):
    """Курс ученика для селектора в карточке."""

    course_id: int
    title: Optional[str] = None


class ProgressTreeResponse(BaseModel):
    """Прогресс ученика по дереву курса + список доступных курсов для селектора."""

    student_id: int
    course_id: Optional[int] = Field(
        default=None, description="Запрошенный узел; null, если course_id не передан"
    )
    courses: list[StudentCourseRef] = Field(
        default_factory=list,
        description="Активные курсы ученика, доступные этому преподавателю",
    )
    items: list[ProgressTreeItem] = Field(
        default_factory=list,
        description="Плоское дерево в учебном порядке; пусто, если course_id не передан",
    )


# ─── Вспомогательное ────────────────────────────────────────────────────────


async def _course_of_task(db: AsyncSession, task_id: int) -> int:
    """Курс задания (нужен для scoped-ACL до самой операции). 404, если нет."""
    row = (
        await db.execute(
            text("SELECT course_id FROM tasks WHERE id = :task_id"), {"task_id": task_id}
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Задание {task_id} не найдено")
    return int(row[0])


async def _course_of_material(db: AsyncSession, material_id: int) -> int:
    """Курс материала (нужен для scoped-ACL до самой операции). 404, если нет."""
    row = (
        await db.execute(
            text("SELECT course_id FROM materials WHERE id = :material_id"),
            {"material_id": material_id},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Материал {material_id} не найден")
    return int(row[0])


def _actor_id(current_user: CurrentUser) -> Optional[int]:
    """ID автора правки; для сервисного токена — None (пользователя нет)."""
    return None if current_user.is_service else current_user.id


# ─── Эндпоинты ──────────────────────────────────────────────────────────────


@router.get(
    _BASE,
    response_model=ProgressTreeResponse,
    summary="Прогресс ученика по дереву курса (для преподавателя)",
)
async def get_progress(
    student_id: int = Path(..., ge=1, description="ID ученика"),
    course_id: Optional[int] = Query(
        default=None,
        ge=1,
        description="ID узла курса (берётся всё его дерево). Без него вернётся только список курсов",
    ),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressTreeResponse:
    """Отдать дерево курса со статусами элементов, флагом ручной отметки и селектор курсов.

    Без ``course_id`` отдаётся только ``courses`` (ACL-фильтрованный список
    курсов ученика), ``items`` пуст: это первый запрос карточки, когда курс ещё
    не выбран. Ошибку доступа в этом режиме не бросаем — преподаватель просто
    увидит пустой список, если ни один курс ученика ему не доступен.
    """
    courses = await manual_progress_service.list_accessible_student_courses(
        db, current_user, student_id
    )
    if course_id is None:
        return ProgressTreeResponse(student_id=student_id, course_id=None, courses=courses)

    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    data: dict[str, Any] = await manual_progress_service.get_student_progress(
        db, student_id=student_id, course_id=course_id
    )
    return ProgressTreeResponse(courses=courses, **data)


@router.post(
    _BASE + "/tasks/{task_id}",
    response_model=ProgressItemResponse,
    summary="Зачесть задание ученику",
)
async def grant_task(
    student_id: int = Path(..., ge=1),
    task_id: int = Path(..., ge=1),
    payload: ProgressGrantRequest = Body(default=ProgressGrantRequest()),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressItemResponse:
    """Идемпотентно отметить задание пройденным (синтетическая попытка + результат)."""
    course_id = await _course_of_task(db, task_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.grant_task(
        db,
        student_id=student_id,
        task_id=task_id,
        granted_by=_actor_id(current_user),
        comment=payload.comment,
    )
    await db.commit()
    return ProgressItemResponse(**result)


@router.delete(
    _BASE + "/tasks/{task_id}",
    response_model=ProgressItemResponse,
    summary="Снять зачёт задания",
)
async def revoke_task(
    student_id: int = Path(..., ge=1),
    task_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressItemResponse:
    """Аннулировать синтетические попытки задания; реальные попытки не трогаются."""
    course_id = await _course_of_task(db, task_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.revoke_task(
        db, student_id=student_id, task_id=task_id, revoked_by=_actor_id(current_user)
    )
    await db.commit()
    return ProgressItemResponse(**result)


@router.post(
    _BASE + "/materials/{material_id}",
    response_model=ProgressItemResponse,
    summary="Отметить материал пройденным",
)
async def grant_material(
    student_id: int = Path(..., ge=1),
    material_id: int = Path(..., ge=1),
    payload: ProgressGrantRequest = Body(default=ProgressGrantRequest()),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressItemResponse:
    """Идемпотентно отметить материал пройденным от лица преподавателя."""
    course_id = await _course_of_material(db, material_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.grant_material(
        db,
        student_id=student_id,
        material_id=material_id,
        granted_by=_actor_id(current_user),
        comment=payload.comment,
    )
    await db.commit()
    return ProgressItemResponse(**result)


@router.delete(
    _BASE + "/materials/{material_id}",
    response_model=ProgressItemResponse,
    summary="Снять отметку материала",
)
async def revoke_material(
    student_id: int = Path(..., ge=1),
    material_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressItemResponse:
    """Удалить только ручную отметку; прохождение самого ученика сохраняется."""
    course_id = await _course_of_material(db, material_id)
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.revoke_material(
        db,
        student_id=student_id,
        material_id=material_id,
        revoked_by=_actor_id(current_user),
    )
    await db.commit()
    return ProgressItemResponse(**result)


@router.post(
    _BASE + "/courses/{course_id}",
    response_model=ProgressBulkResponse,
    summary="Массово зачесть всё дерево узла",
)
async def grant_course(
    student_id: int = Path(..., ge=1),
    course_id: int = Path(..., ge=1),
    payload: ProgressGrantRequest = Body(default=ProgressGrantRequest()),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressBulkResponse:
    """Зачесть все задания и материалы дерева узла (фильтр обязательности — как у движка)."""
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.grant_course_subtree(
        db,
        student_id=student_id,
        course_id=course_id,
        granted_by=_actor_id(current_user),
        comment=payload.comment,
    )
    await db.commit()
    return ProgressBulkResponse(**result)


@router.delete(
    _BASE + "/courses/{course_id}",
    response_model=ProgressBulkResponse,
    summary="Массово снять зачёты в дереве узла",
)
async def revoke_course(
    student_id: int = Path(..., ge=1),
    course_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_PROGRESS_GATE),
) -> ProgressBulkResponse:
    """Снять ручные зачёты по всему дереву узла; реальный прогресс сохраняется."""
    await manual_progress_service.ensure_can_edit_progress(
        db, current_user, student_id, course_id
    )
    result = await manual_progress_service.revoke_course_subtree(
        db,
        student_id=student_id,
        course_id=course_id,
        revoked_by=_actor_id(current_user),
    )
    await db.commit()
    return ProgressBulkResponse(**result)
```

### app/services/manual_progress_service.py
```python
"""Штатная правка прогресса ученика преподавателем (tsk-297).

Не миграция из внешнего источника, а функция продукта: преподаватель/методист в
любой момент отмечает задание или материал как пройденные, чтобы ученик,
пришедший с наработками, продолжал со своего места, а не с нуля.

Модель данных (решение зафиксировано в
`docs/specs/2026-07-20-tech-spec-tsk297-manual-progress.md`, обосновано разведкой
прода):

* **Задание** — синтетическая ПОПЫТКА + результат, а не «результат без попытки».
  Движок (`compute_task_state`) склеивает результат с попыткой через
  `INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL`,
  поэтому результат без попытки он просто не увидел бы. Попытка пишется с
  ``root_course_id = NULL`` — «путь неизвестен», такая попытка не расходует лимит
  ни в одном корне (документированная семантика tsk-264), то есть зачёт НЕ съедает
  попытку ученика. ``score = max_score`` даёт ratio 1.0 ≥ ``PASS_THRESHOLD_RATIO``
  → задание становится ``PASSED`` и next-item его больше не выдаёт.
  ``checked_at``/``checked_by`` заполняются, иначе зачтённое задание ручного типа
  (SA_COM/TA) упало бы в очередь проверки преподавателя.
* **Материал** — строка `student_material_progress` со `source='manual_teacher'`
  (колонка добавлена миграцией `tsk297_manual_progress_source`).

Обратимость:

* задание — синтетическая попытка помечается ``cancelled_at``/``cancel_reason``,
  строки НЕ удаляются (история правок сохраняется), движок отсекает её тем же
  ``a.cancelled_at IS NULL``. Задание возвращается к состоянию, которое дают его
  РЕАЛЬНЫЕ попытки: ``OPEN``, если ученик его не решал, иначе то, что было до
  зачёта (``IN_PROGRESS`` / ``FAILED`` / ``BLOCKED_LIMIT``);
* материал — строка удаляется, но ТОЛЬКО при ``source='manual_teacher'``:
  реальное прохождение ученика не трогаем.

`student_task_progress` (пропуск задания, tsk-111) не затрагивается вовсе: зачёт
и пропуск — разные сущности.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser
from app.services import audit_service
from app.services.checking_service import CheckingService
from app.services.learning_engine_service import LearningEngineService
from app.services.teacher_queue_service import teacher_course_acl
from app.utils.task_title import humanize_task_title

logger = logging.getLogger(__name__)

#: Значение провенанса для всего, что поставлено преподавателем вручную.
MANUAL_SOURCE = "manual_teacher"
#: Причина аннулирования синтетической попытки при снятии зачёта.
REVOKE_REASON = "manual_progress_revoked"
#: Провенанс реального прохождения ученика (дефолт колонки `source`).
SYSTEM_SOURCE = "system"
#: Фильтр обхода — тот же, что у учебного движка (см. `compute_course_state`).
_REQUIREMENT_LEVELS = ("required", "skippable")
#: Максимальная длина комментария преподавателя (обрезается, а не отклоняется).
_COMMENT_MAX_LEN = 500

_engine = LearningEngineService()
_checking_service = CheckingService()

_ELEVATED_ROLES = frozenset({"admin", "methodist"})
_TEACHER_ROLES = frozenset({"teacher"})


# ─── ACL ────────────────────────────────────────────────────────────────────


async def can_edit_progress(
    db: AsyncSession,
    current_user: CurrentUser,
    student_id: int,
    course_id: Optional[int] = None,
) -> bool:
    """Может ли пользователь править прогресс данного ученика (без исключения).

    Иерархия (тот же принцип, что у `teacher_can_override_limit`):

    * сервисный токен — полный доступ (bypass);
    * роль ``admin`` / ``methodist`` — полный доступ (любой ученик);
    * роль ``teacher`` — ученик закреплён за ним (``student_teacher_links``)
      ИЛИ курс попадает под его ACL (``teacher_course_acl``, рекурсия вверх по
      ``course_parents``);
    * иначе — нет.

    :param db: async-сессия.
    :param current_user: текущий пользователь (или сервисный токен).
    :param student_id: ID ученика, чей прогресс правится.
    :param course_id: курс правимого элемента; None — проверка только по связке
        «ученик закреплён за преподавателем».
    :returns: True — доступ есть.
    """
    if current_user.is_service:
        return True

    from app.services import roles_service  # noqa: PLC0415 — избегаем цикла импортов

    roles = {r.lower().strip() for r in await roles_service.get_user_role_names(db, current_user.id)}
    if roles & _ELEVATED_ROLES:
        return True

    if not (roles & _TEACHER_ROLES):
        return False

    linked = (
        await db.execute(
            text(
                "SELECT 1 FROM student_teacher_links "
                "WHERE student_id = :student_id AND teacher_id = :teacher_id"
            ),
            {"student_id": student_id, "teacher_id": current_user.id},
        )
    ).fetchone()
    if linked is not None:
        return True

    if course_id is not None:
        allowed = (
            await db.execute(
                text(
                    f"SELECT {teacher_course_acl(':target_course_id')}"  # nosec B608
                ),
                {"target_course_id": course_id, "teacher_id": current_user.id},
            )
        ).scalar()
        # Одного ACL на курс мало: он говорит «этот курс мой», но ничего не
        # говорит про ученика. Без второй проверки преподаватель курса X мог бы
        # править прогресс ЛЮБОГО user_id (включая другого преподавателя),
        # просто перебирая идентификаторы. Требуем, чтобы ученик был реально
        # записан на корень дерева, в которое входит этот узел.
        if allowed:
            roots = await _engine.list_active_roots_of_node(db, student_id, course_id)
            if roots:
                return True

    return False


async def ensure_can_edit_progress(
    db: AsyncSession,
    current_user: CurrentUser,
    student_id: int,
    course_id: Optional[int] = None,
) -> None:
    """То же, что `can_edit_progress`, но при отказе бросает 403 с русским detail.

    :raises HTTPException: 403, если прав нет.
    """
    if await can_edit_progress(db, current_user, student_id, course_id):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail=(
            "Править прогресс ученика может преподаватель — только своих учеников "
            "или учеников на закреплённых за ним курсах; полный доступ у методиста и админа"
        ),
    )


async def list_accessible_student_courses(
    db: AsyncSession,
    current_user: CurrentUser,
    student_id: int,
) -> list[dict[str, Any]]:
    """Корневые курсы ученика, доступные текущему преподавателю.

    Питает селектор курсов в карточке ученика на портале: иначе фронт был бы
    вынужден звать `GET /users/{id}/courses`, объявленный только с
    APIKeyQuery-безопасностью, — лишняя точка отказа под cookie-сессией.

    Источник — активные записи ``user_courses`` (то, на что ученик записан),
    отфильтрованные тем же ACL, что и правка прогресса.

    :returns: список ``{"course_id": int, "title": str}`` в порядке
        ``user_courses.order_number``.
    """
    rows = (
        await db.execute(
            text(
                "SELECT c.id AS course_id, c.title "
                "FROM user_courses uc "
                "JOIN courses c ON c.id = uc.course_id "
                "WHERE uc.user_id = :student_id AND uc.is_active = true "
                "ORDER BY uc.order_number ASC NULLS LAST, c.id"
            ),
            {"student_id": student_id},
        )
    ).mappings().fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        cid = int(row["course_id"])
        if await can_edit_progress(db, current_user, student_id, cid):
            result.append({"course_id": cid, "title": row["title"]})
    return result


# ─── Вспомогательные запросы ────────────────────────────────────────────────


async def _load_task(db: AsyncSession, task_id: int) -> dict[str, Any]:
    """Курс, max_score и правила проверки задания. 404, если задания нет."""
    row = (
        await db.execute(
            text(
                "SELECT id, course_id, max_score, solution_rules, external_uid, "
                "       task_content->>'title' AS tc_title, task_content->>'stem' AS tc_stem "
                "FROM tasks WHERE id = :task_id"
            ),
            {"task_id": task_id},
        )
    ).mappings().fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Задание {task_id} не найдено")
    return dict(row)


async def _load_material(db: AsyncSession, material_id: int) -> dict[str, Any]:
    """Курс и заголовок материала. 404, если материала нет."""
    row = (
        await db.execute(
            text("SELECT id, course_id, title FROM materials WHERE id = :material_id"),
            {"material_id": material_id},
        )
    ).mappings().fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Материал {material_id} не найден")
    return dict(row)


def _resolve_max_score(task: dict[str, Any]) -> int:
    """Максимальный балл задания — ровно тем же путём, что и реальный приём ответа.

    Приём ответа (`POST /attempts/{id}/answers`) строит правило через
    ``CheckingService.build_solution_rules(task.solution_rules, task.max_score)``
    и берёт ``max_score`` оттуда. Повторяем этот же вызов, чтобы синтетический
    результат нельзя было отличить по шкале от честно решённого:

    * есть ``solution_rules`` → ``solution_rules.max_score`` (обязательное поле схемы);
    * правило пустое (JSON null у 1116 импортированных заданий, tsk-325) →
      ``tasks.max_score``;
    * и он пуст/нулевой → ``1`` (дефолт `build_solution_rules`; ratio 1/1 = 1.0
      всё равно даёт PASSED).

    Нижняя граница ``1`` ставится жёстко: в схеме `SolutionRules.max_score` нет
    ``ge=1``, и при нуле/отрицательном значении результат ушёл бы со шкалой 0 —
    движок считает PASSED через ``last_max > 0``, то есть зачёт вернул бы
    ``granted: true``, а задание так и не стало бы пройденным.
    """
    rules = _checking_service.build_solution_rules(
        task.get("solution_rules"), task.get("max_score")
    )
    return max(1, int(rules.max_score))


async def _lock(db: AsyncSession, key1: int, key2: int) -> None:
    """Сериализовать операцию по паре ключей на время транзакции."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"), {"k1": key1, "k2": key2}
    )


async def _refresh_course_state(db: AsyncSession, student_id: int, course_id: int) -> None:
    """Пересчитать `student_course_state` по корням, в чьё дерево входит узел.

    Без этого массовый зачёт давал расхождение: `me_service.get_courses_with_progress`
    берёт ``is_completed`` из `student_course_state`, а не считает на лету, — ученик
    видел 100% выполненных элементов при незавершённом курсе (и наоборот после
    массового снятия), пока сам не дёргал движок.

    Вызывается ТОЛЬКО на операциях записи и ОДИН раз на операцию (в массовой — в
    конце, а не на каждый элемент). В чтении (`get_student_progress`)
    `compute_course_state` по-прежнему не зовётся: при COMPLETED он дёргает Y-6
    эскалацию (уведомление методисту), а просмотр карточки рассылать ничего не
    должен. На записи эскалация уместна — курс действительно сменил состояние.

    :param course_id: затронутый узел (курс задания/материала либо корень массовой
        операции).
    """
    roots = await _engine.list_active_roots_of_node(db, student_id, course_id)
    for root_id in roots:
        await _engine.compute_course_state(
            db, student_id, root_id, update_state_table=True
        )


def _clip_comment(comment: Optional[str]) -> Optional[str]:
    """Обрезать комментарий преподавателя до лимита; пустой → None."""
    if comment is None:
        return None
    trimmed = comment.strip()
    if not trimmed:
        return None
    return trimmed[:_COMMENT_MAX_LEN]


async def _subtree_course_ids(db: AsyncSession, course_id: int) -> list[int]:
    """Курсы дерева узла (сам узел + все потомки) в порядке обхода движка."""
    tree_ids = await _engine._collect_courses_in_order(db, course_id)  # noqa: SLF001
    return tree_ids or [course_id]


async def _tree_task_rows(db: AsyncSession, course_ids: list[int]) -> list[dict[str, Any]]:
    """Активные задания дерева с тем же фильтром обязательности, что у движка."""
    rows = (
        await db.execute(
            text(
                "SELECT id, course_id, external_uid, "
                "       task_content->>'title' AS tc_title, task_content->>'stem' AS tc_stem "
                "FROM tasks "
                "WHERE course_id = ANY(:course_ids) AND is_active = true "
                "  AND requirement_level = ANY(:levels) "
                "ORDER BY course_id, order_position ASC NULLS LAST, id"
            ),
            {"course_ids": course_ids, "levels": list(_REQUIREMENT_LEVELS)},
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


async def _tree_material_rows(db: AsyncSession, course_ids: list[int]) -> list[dict[str, Any]]:
    """Активные материалы дерева с тем же фильтром обязательности, что у движка."""
    rows = (
        await db.execute(
            text(
                "SELECT id, course_id, title FROM materials "
                "WHERE course_id = ANY(:course_ids) AND is_active = true "
                "  AND requirement_level = ANY(:levels) "
                "ORDER BY course_id, order_position ASC NULLS LAST, id"
            ),
            {"course_ids": course_ids, "levels": list(_REQUIREMENT_LEVELS)},
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


# ─── Задания ────────────────────────────────────────────────────────────────


async def grant_task(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    granted_by: Optional[int],
    comment: Optional[str] = None,
    _standalone: bool = True,
) -> dict[str, Any]:
    """Идемпотентно зачесть задание ученику.

    Создаёт синтетическую попытку (``root_course_id=NULL`` — лимит ученика не
    расходуется) и результат с ``score = max_score``, из-за чего движок начинает
    считать задание ``PASSED``.

    :param db: async-сессия (коммит — на вызывающей стороне).
    :param student_id: ID ученика.
    :param task_id: ID задания.
    :param granted_by: ID преподавателя, поставившего зачёт.
    :param comment: причина/пояснение, попадает в ``task_results.metrics``.
    :param _standalone: True — операция самостоятельная: пишем событие аудита и
        пересчитываем `student_course_state`. False — элемент массовой операции:
        и аудит, и пересчёт состояния делаются один раз на всю пачку.
    :returns: словарь с ``granted``/``already``/``attempt_id``.
    """
    await _lock(db, student_id, task_id)

    task = await _load_task(db, task_id)

    state = await _engine.compute_task_state(db, student_id, task_id)
    if state.state == "PASSED":
        logger.info(
            "tsk-297: задание %s у ученика %s уже PASSED — зачёт не нужен",
            task_id, student_id,
        )
        return {
            "student_id": student_id,
            "item_type": "task",
            "item_id": task_id,
            "granted": True,
            "already": True,
            "source": MANUAL_SOURCE,
            "attempt_id": None,
        }

    max_score = _resolve_max_score(task)
    clean_comment = _clip_comment(comment)

    # Попытка вставляется сырым INSERT'ом, а НЕ через `AttemptsService.create_attempt`:
    # тот идёт в `BaseRepository.create` с `commit=True` и коммитит прямо посреди
    # операции. Это рвало транзакционный `pg_advisory_xact_lock` (он держится до
    # конца транзакции: коммит после вставки попытки, но до вставки результата —
    # и два параллельных POST создавали две попытки), а в массовой операции
    # фиксировало часть дерева до записи аудита. Коммитит только роутер.
    attempt_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts "
                    "  (user_id, course_id, root_course_id, source_system, meta) "
                    # NULL намеренно: «путь неизвестен» → попытка не расходует лимит
                    # ни в одном корне (tsk-264). Зачёт не съедает попытку ученика.
                    "VALUES (:user_id, :course_id, NULL, :source_system, CAST(:meta AS jsonb)) "
                    "RETURNING id"
                ),
                {
                    "user_id": student_id,
                    "course_id": task["course_id"],
                    "source_system": MANUAL_SOURCE,
                    "meta": json.dumps(
                        {"granted_by": granted_by, "task_ids": [task_id], "manual_grant": True},
                        ensure_ascii=False,
                    ),
                },
            )
        ).scalar()
    )

    await db.execute(
        text(
            "INSERT INTO task_results "
            "  (user_id, task_id, attempt_id, score, max_score, is_correct, "
            "   submitted_at, received_at, count_retry, checked_at, checked_by, "
            "   source_system, metrics) "
            "VALUES "
            "  (:user_id, :task_id, :attempt_id, :score, :max_score, true, "
            "   now(), now(), 0, now(), :checked_by, :source_system, "
            "   CAST(:metrics AS jsonb))"
        ),
        {
            "user_id": student_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "score": max_score,
            "max_score": max_score,
            # checked_at/checked_by обязательны: без них зачтённое SA_COM/TA
            # попало бы в очередь ручной проверки (предикат `checked_at IS NULL`).
            "checked_by": granted_by,
            "source_system": MANUAL_SOURCE,
            "metrics": json.dumps(
                {"manual_grant": True, "comment": clean_comment}, ensure_ascii=False
            ),
        },
    )

    if _standalone:
        await audit_service.log_event(
            db,
            audit_service.TEACHER_PROGRESS_GRANTED,
            user_id=granted_by,
            details={
                "student_id": student_id,
                "item_type": "task",
                "item_id": task_id,
                "course_id": task["course_id"],
                "bulk": False,
                "affected": 1,
                "comment": clean_comment,
            },
        )
        await _refresh_course_state(db, student_id, int(task["course_id"]))

    logger.info(
        "tsk-297: зачёт задания %s ученику %s преподавателем %s (attempt=%s, score=%s)",
        task_id, student_id, granted_by, attempt_id, max_score,
    )
    return {
        "student_id": student_id,
        "item_type": "task",
        "item_id": task_id,
        "granted": True,
        "already": False,
        "source": MANUAL_SOURCE,
        "attempt_id": attempt_id,
    }


async def revoke_task(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    revoked_by: Optional[int],
    _standalone: bool = True,
) -> dict[str, Any]:
    """Снять ручной зачёт задания.

    Синтетические попытки помечаются ``cancelled_at``/``cancel_reason``; движок
    отсекает их тем же ``a.cancelled_at IS NULL``. Строки не удаляются — история
    правок сохраняется. Реальные попытки ученика
    (``source_system <> 'manual_teacher'``) не трогаются, поэтому задание
    возвращается НЕ обязательно в ``OPEN``, а в то состояние, которое дают эти
    реальные попытки: ``OPEN`` — если ученик задание не решал, иначе
    ``IN_PROGRESS`` / ``FAILED`` / ``BLOCKED_LIMIT``.

    :returns: словарь с ``granted=False`` и ``already=True``, если зачёта не было
        (идемпотентность, а не ошибка).
    """
    await _lock(db, student_id, task_id)

    result = await db.execute(
        text(
            "UPDATE attempts a "
            "SET cancelled_at = now(), cancel_reason = :reason "
            "WHERE a.id IN ( "
            "    SELECT DISTINCT tr.attempt_id FROM task_results tr "
            "    JOIN attempts src ON src.id = tr.attempt_id "
            "    WHERE tr.user_id = :student_id AND tr.task_id = :task_id "
            "      AND src.source_system = :source "
            "      AND src.cancelled_at IS NULL "
            ")"
        ),
        {
            "reason": REVOKE_REASON,
            "student_id": student_id,
            "task_id": task_id,
            "source": MANUAL_SOURCE,
        },
    )
    affected = result.rowcount or 0

    if affected and _standalone:
        task = await _load_task(db, task_id)
        await audit_service.log_event(
            db,
            audit_service.TEACHER_PROGRESS_REVOKED,
            user_id=revoked_by,
            details={
                "student_id": student_id,
                "item_type": "task",
                "item_id": task_id,
                "course_id": task["course_id"],
                "bulk": False,
                "affected": affected,
                "comment": None,
            },
        )
        await _refresh_course_state(db, student_id, int(task["course_id"]))

    logger.info(
        "tsk-297: снятие зачёта задания %s у ученика %s преподавателем %s (попыток=%s)",
        task_id, student_id, revoked_by, affected,
    )
    return {
        "student_id": student_id,
        "item_type": "task",
        "item_id": task_id,
        "granted": False,
        "already": affected == 0,
        "source": MANUAL_SOURCE,
        "attempt_id": None,
    }


# ─── Материалы ──────────────────────────────────────────────────────────────


async def grant_material(
    db: AsyncSession,
    *,
    student_id: int,
    material_id: int,
    granted_by: Optional[int],
    comment: Optional[str] = None,
    _standalone: bool = True,
) -> dict[str, Any]:
    """Идемпотентно отметить материал пройденным от лица преподавателя.

    Если материал УЖЕ отмечен пройденным (в том числе самим учеником) — ничего не
    меняем и не перетираем провенанс: возвращаем ``already=True``.
    """
    await _lock(db, student_id, material_id)

    material = await _load_material(db, material_id)

    current = (
        await db.execute(
            text(
                "SELECT status, source FROM student_material_progress "
                "WHERE student_id = :student_id AND material_id = :material_id"
            ),
            {"student_id": student_id, "material_id": material_id},
        )
    ).fetchone()
    if current is not None and current[0] == "completed":
        return {
            "student_id": student_id,
            "item_type": "material",
            "item_id": material_id,
            "granted": True,
            "already": True,
            "source": current[1],
        }

    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "  (student_id, material_id, status, completed_at, skipped_at, source) "
            "VALUES (:student_id, :material_id, 'completed', now(), NULL, :source) "
            "ON CONFLICT (student_id, material_id) DO UPDATE SET "
            "  status = 'completed', completed_at = now(), skipped_at = NULL, "
            "  source = EXCLUDED.source"
        ),
        {"student_id": student_id, "material_id": material_id, "source": MANUAL_SOURCE},
    )

    if _standalone:
        await audit_service.log_event(
            db,
            audit_service.TEACHER_PROGRESS_GRANTED,
            user_id=granted_by,
            details={
                "student_id": student_id,
                "item_type": "material",
                "item_id": material_id,
                "course_id": material["course_id"],
                "bulk": False,
                "affected": 1,
                "comment": _clip_comment(comment),
            },
        )
        await _refresh_course_state(db, student_id, int(material["course_id"]))

    logger.info(
        "tsk-297: отметка материала %s ученику %s преподавателем %s",
        material_id, student_id, granted_by,
    )
    return {
        "student_id": student_id,
        "item_type": "material",
        "item_id": material_id,
        "granted": True,
        "already": False,
        "source": MANUAL_SOURCE,
    }


async def revoke_material(
    db: AsyncSession,
    *,
    student_id: int,
    material_id: int,
    revoked_by: Optional[int],
    _standalone: bool = True,
) -> dict[str, Any]:
    """Снять ручную отметку материала.

    Удаляет строку ТОЛЬКО при ``source='manual_teacher'``: если ученик прошёл
    материал сам (``source='system'``), его прогресс не трогаем и возвращаем
    ``already=True``.
    """
    await _lock(db, student_id, material_id)

    result = await db.execute(
        text(
            "DELETE FROM student_material_progress "
            "WHERE student_id = :student_id AND material_id = :material_id "
            "  AND source = :source"
        ),
        {"student_id": student_id, "material_id": material_id, "source": MANUAL_SOURCE},
    )
    affected = result.rowcount or 0

    if affected and _standalone:
        material = await _load_material(db, material_id)
        await audit_service.log_event(
            db,
            audit_service.TEACHER_PROGRESS_REVOKED,
            user_id=revoked_by,
            details={
                "student_id": student_id,
                "item_type": "material",
                "item_id": material_id,
                "course_id": material["course_id"],
                "bulk": False,
                "affected": affected,
                "comment": None,
            },
        )
        await _refresh_course_state(db, student_id, int(material["course_id"]))

    logger.info(
        "tsk-297: снятие отметки материала %s у ученика %s преподавателем %s (строк=%s)",
        material_id, student_id, revoked_by, affected,
    )
    return {
        "student_id": student_id,
        "item_type": "material",
        "item_id": material_id,
        "granted": False,
        "already": affected == 0,
        "source": MANUAL_SOURCE,
    }


# ─── Массовые операции по дереву узла ───────────────────────────────────────


async def grant_course_subtree(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
    granted_by: Optional[int],
    comment: Optional[str] = None,
) -> dict[str, Any]:
    """Массово зачесть всё дерево узла (сам узел + потомки по ``course_parents``).

    Обходятся только активные элементы с ``requirement_level IN
    ('required','skippable')`` — тот же фильтр, что у движка, иначе счётчики
    разъехались бы с представлением «курс пройден».

    :returns: счётчики ``tasks_affected`` / ``materials_affected`` /
        ``skipped_already`` (элементы, которые уже были пройдены).
    """
    tree_ids = await _subtree_course_ids(db, course_id)
    tasks = await _tree_task_rows(db, tree_ids)
    materials = await _tree_material_rows(db, tree_ids)

    tasks_affected = 0
    materials_affected = 0
    skipped_already = 0

    for task in tasks:
        res = await grant_task(
            db,
            student_id=student_id,
            task_id=int(task["id"]),
            granted_by=granted_by,
            comment=comment,
            _standalone=False,
        )
        if res["already"]:
            skipped_already += 1
        else:
            tasks_affected += 1

    for material in materials:
        res = await grant_material(
            db,
            student_id=student_id,
            material_id=int(material["id"]),
            granted_by=granted_by,
            comment=comment,
            _standalone=False,
        )
        if res["already"]:
            skipped_already += 1
        else:
            materials_affected += 1

    await audit_service.log_event(
        db,
        audit_service.TEACHER_PROGRESS_GRANTED,
        user_id=granted_by,
        details={
            "student_id": student_id,
            "item_type": "course",
            "item_id": course_id,
            "course_id": course_id,
            "bulk": True,
            "affected": tasks_affected + materials_affected,
            "tasks_affected": tasks_affected,
            "materials_affected": materials_affected,
            "skipped_already": skipped_already,
            "comment": _clip_comment(comment),
        },
    )

    # Один раз на всю пачку, а не на каждый элемент.
    await _refresh_course_state(db, student_id, course_id)

    logger.info(
        "tsk-297: массовый зачёт дерева %s ученику %s: заданий=%s, материалов=%s, уже было=%s",
        course_id, student_id, tasks_affected, materials_affected, skipped_already,
    )
    return {
        "student_id": student_id,
        "course_id": course_id,
        "tasks_affected": tasks_affected,
        "materials_affected": materials_affected,
        "skipped_already": skipped_already,
    }


async def revoke_course_subtree(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
    revoked_by: Optional[int],
) -> dict[str, Any]:
    """Массово снять ручные зачёты в дереве узла.

    Реальный прогресс ученика не затрагивается: у заданий отменяются только
    синтетические попытки, у материалов удаляются только строки
    ``source='manual_teacher'``.
    """
    tree_ids = await _subtree_course_ids(db, course_id)
    tasks = await _tree_task_rows(db, tree_ids)
    materials = await _tree_material_rows(db, tree_ids)

    tasks_affected = 0
    materials_affected = 0
    skipped_already = 0

    for task in tasks:
        res = await revoke_task(
            db,
            student_id=student_id,
            task_id=int(task["id"]),
            revoked_by=revoked_by,
            _standalone=False,
        )
        if res["already"]:
            skipped_already += 1
        else:
            tasks_affected += 1

    for material in materials:
        res = await revoke_material(
            db,
            student_id=student_id,
            material_id=int(material["id"]),
            revoked_by=revoked_by,
            _standalone=False,
        )
        if res["already"]:
            skipped_already += 1
        else:
            materials_affected += 1

    await audit_service.log_event(
        db,
        audit_service.TEACHER_PROGRESS_REVOKED,
        user_id=revoked_by,
        details={
            "student_id": student_id,
            "item_type": "course",
            "item_id": course_id,
            "course_id": course_id,
            "bulk": True,
            "affected": tasks_affected + materials_affected,
            "tasks_affected": tasks_affected,
            "materials_affected": materials_affected,
            "skipped_already": skipped_already,
            "comment": None,
        },
    )

    # Один раз на всю пачку, а не на каждый элемент.
    await _refresh_course_state(db, student_id, course_id)

    logger.info(
        "tsk-297: массовое снятие зачётов дерева %s у ученика %s: заданий=%s, материалов=%s",
        course_id, student_id, tasks_affected, materials_affected,
    )
    return {
        "student_id": student_id,
        "course_id": course_id,
        "tasks_affected": tasks_affected,
        "materials_affected": materials_affected,
        "skipped_already": skipped_already,
    }


# ─── Чтение прогресса ───────────────────────────────────────────────────────


async def get_student_progress(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
) -> dict[str, Any]:
    """Прогресс ученика по дереву курса для карточки преподавателя.

    Плоский список ``items`` трёх типов, в УЧЕБНОМ порядке — фронт на него
    опирается и не пересортировывает:

    * ``course`` — узел темы/подкурса; идёт непосредственно перед своим
      содержимым, сами узлы следуют порядку обхода движка (post-order:
      подкурсы раньше курса-контейнера, см. `_collect_courses_in_order`);
    * ``material`` — материалы узла по ``order_position``;
    * ``task`` — задания узла по ``order_position``.

    У каждого элемента есть ``parent_course_id`` — узел, которому элемент
    непосредственно принадлежит; у запрошенного корня он ``None``.

    Статусы: задания — ``OPEN``/``IN_PROGRESS``/``FAILED``/``PASSED``/
    ``BLOCKED_LIMIT`` (как их отдаёт движок), материалы —
    ``NOT_STARTED``/``COMPLETED``/``SKIPPED``. У узлов ``course`` статус
    ``NOT_STARTED``/``IN_PROGRESS``/``COMPLETED`` берётся у движка
    (`compute_course_state`, без записи в таблицу состояний — это чтение).

    Флаг ``manual``:

    * задание — ``True``, если ПОСЛЕДНИЙ учтённый результат записан с
      ``source_system='manual_teacher'`` (отменённые попытки не учитываются —
      снятый зачёт сразу перестаёт считаться ручным);
    * материал — ``True`` при ``source='manual_teacher'``;
    * узел ``course`` — всегда ``None``: массовая операция адресуется его
      ``item_id``, а «ручным» узел сам по себе не бывает.

    ``granted_by``/``granted_at`` у задания — ``checked_by``/``checked_at``
    последнего результата. У материала ``granted_by`` всегда ``None``: колонки
    автора у ``student_material_progress`` нет (провенанс ограничен ``source``,
    см. миграцию `tsk297_manual_progress_source`).
    """
    tree_ids = await _subtree_course_ids(db, course_id)
    tasks = await _tree_task_rows(db, tree_ids)
    materials = await _tree_material_rows(db, tree_ids)

    task_ids = [int(t["id"]) for t in tasks]
    material_ids = [int(m["id"]) for m in materials]

    last_results: dict[int, dict[str, Any]] = {}
    if task_ids:
        rows = (
            await db.execute(
                text(
                    "SELECT DISTINCT ON (tr.task_id) "
                    "       tr.task_id, tr.source_system, tr.checked_by, tr.checked_at "
                    "FROM task_results tr "
                    "JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL "
                    "WHERE tr.user_id = :student_id AND tr.task_id = ANY(:task_ids) "
                    "ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC"
                ),
                {"student_id": student_id, "task_ids": task_ids},
            )
        ).mappings().fetchall()
        last_results = {int(r["task_id"]): dict(r) for r in rows}

    material_progress: dict[int, dict[str, Any]] = {}
    if material_ids:
        rows = (
            await db.execute(
                text(
                    "SELECT material_id, status, source, completed_at, skipped_at "
                    "FROM student_material_progress "
                    "WHERE student_id = :student_id AND material_id = ANY(:material_ids)"
                ),
                {"student_id": student_id, "material_ids": material_ids},
            )
        ).mappings().fetchall()
        material_progress = {int(r["material_id"]): dict(r) for r in rows}

    # Курс → элементы, чтобы сохранить порядок обхода движка.
    by_course_materials: dict[int, list[dict[str, Any]]] = {}
    by_course_tasks: dict[int, list[dict[str, Any]]] = {}
    for m in materials:
        by_course_materials.setdefault(int(m["course_id"]), []).append(m)
    for t in tasks:
        by_course_tasks.setdefault(int(t["course_id"]), []).append(t)

    # Заголовки узлов и их родители внутри запрошенного дерева.
    course_titles = {
        int(r["id"]): r["title"]
        for r in (
            await db.execute(
                text("SELECT id, title FROM courses WHERE id = ANY(:ids)"),
                {"ids": tree_ids},
            )
        ).mappings().fetchall()
    }
    parent_of: dict[int, Optional[int]] = {}
    if len(tree_ids) > 1:
        for row in (
            await db.execute(
                text(
                    "SELECT course_id, parent_course_id FROM course_parents "
                    "WHERE course_id = ANY(:ids) AND parent_course_id = ANY(:ids) "
                    "ORDER BY order_number ASC NULLS LAST, parent_course_id"
                ),
                {"ids": tree_ids},
            )
        ).mappings().fetchall():
            # Узел может висеть под несколькими родителями одного дерева —
            # берём первый по тому же порядку, что и обход движка.
            parent_of.setdefault(int(row["course_id"]), int(row["parent_course_id"]))

    # Пропущенные задания (tsk-111) считаются пройденными при свёртке узла —
    # ровно как в `compute_course_state`.
    skipped_task_ids: set[int] = set()
    if task_ids:
        skipped_task_ids = {
            int(r[0])
            for r in (
                await db.execute(
                    text(
                        "SELECT task_id FROM student_task_progress "
                        "WHERE student_id = :student_id AND task_id = ANY(:task_ids) "
                        "  AND status = 'skipped'"
                    ),
                    {"student_id": student_id, "task_ids": task_ids},
                )
            ).fetchall()
        }

    # Элементы каждого узла собираем отдельно, чтобы сначала свернуть статусы
    # узлов, а потом выложить всё в учебном порядке.
    per_course_items: dict[int, list[dict[str, Any]]] = {cid: [] for cid in tree_ids}
    own_total: dict[int, int] = {cid: 0 for cid in tree_ids}
    own_done: dict[int, int] = {cid: 0 for cid in tree_ids}

    for cid in tree_ids:
        for m in by_course_materials.get(cid, []):
            mid = int(m["id"])
            prog = material_progress.get(mid)
            if prog is None:
                m_status = "NOT_STARTED"
            elif prog["status"] == "completed":
                m_status = "COMPLETED"
            elif prog["status"] == "skipped":
                m_status = "SKIPPED"
            else:
                m_status = str(prog["status"]).upper()
            per_course_items[cid].append({
                "item_type": "material",
                "item_id": mid,
                "course_id": cid,
                "parent_course_id": cid,
                "title": m["title"],
                "status": m_status,
                "manual": bool(prog is not None and prog.get("source") == MANUAL_SOURCE),
                "granted_by": None,
                "granted_at": prog.get("completed_at") if prog else None,
            })
            own_total[cid] += 1
            if m_status in ("COMPLETED", "SKIPPED"):
                own_done[cid] += 1
        for t in by_course_tasks.get(cid, []):
            tid = int(t["id"])
            state = await _engine.compute_task_state(db, student_id, tid)
            last = last_results.get(tid)
            is_manual = bool(last is not None and last.get("source_system") == MANUAL_SOURCE)
            per_course_items[cid].append({
                "item_type": "task",
                "item_id": tid,
                "course_id": cid,
                "parent_course_id": cid,
                "title": humanize_task_title(
                    tid, t.get("tc_title"), t.get("tc_stem"), t.get("external_uid")
                ),
                "status": state.state,
                "manual": is_manual,
                "granted_by": (last or {}).get("checked_by") if is_manual else None,
                "granted_at": (last or {}).get("checked_at") if is_manual else None,
            })
            own_total[cid] += 1
            if state.state == "PASSED" or tid in skipped_task_ids:
                own_done[cid] += 1

    # Свёртка статуса узла по его поддереву. Считаем сами, а НЕ через
    # `compute_course_state`: тот при COMPLETED дёргает Y-6 эскалацию
    # (уведомление методисту + запись в `task_results.metrics`), а это чтение —
    # просмотр карточки ученика не должен ничего рассылать и писать.
    children_of: dict[int, list[int]] = {cid: [] for cid in tree_ids}
    for child, parent in parent_of.items():
        if parent in children_of:
            children_of[parent].append(child)

    def _rollup(node: int, seen: frozenset[int] = frozenset()) -> tuple[int, int]:
        """(всего, пройдено) по поддереву узла; `seen` защищает от циклов в DAG."""
        if node in seen:
            return (0, 0)
        seen = seen | {node}
        total, done = own_total.get(node, 0), own_done.get(node, 0)
        for child in children_of.get(node, []):
            c_total, c_done = _rollup(child, seen)
            total += c_total
            done += c_done
        return (total, done)

    def _course_status(node: int) -> str:
        total, done = _rollup(node)
        if total == 0 or done >= total:
            return "COMPLETED"
        if done == 0:
            return "NOT_STARTED"
        return "IN_PROGRESS"

    items: list[dict[str, Any]] = []
    for cid in tree_ids:
        items.append({
            "item_type": "course",
            "item_id": cid,
            "course_id": cid,
            # У запрошенного корня родителя в этом дереве нет.
            "parent_course_id": None if cid == course_id else parent_of.get(cid),
            "title": course_titles.get(cid),
            "status": _course_status(cid),
            "manual": None,
            "granted_by": None,
            "granted_at": None,
        })
        items.extend(per_course_items[cid])

    return {"student_id": student_id, "course_id": course_id, "items": items}


__all__ = [
    "MANUAL_SOURCE",
    "REVOKE_REASON",
    "SYSTEM_SOURCE",
    "can_edit_progress",
    "ensure_can_edit_progress",
    "get_student_progress",
    "list_accessible_student_courses",
    "grant_course_subtree",
    "grant_material",
    "grant_task",
    "revoke_course_subtree",
    "revoke_material",
    "revoke_task",
]
```

### app/db/migrations/versions/20260720_010000_tsk297_manual_progress_source.py
```python
"""Провенанс отметки материала: `student_material_progress.source` (tsk-297).

Штатная правка прогресса преподавателем должна быть ОБРАТИМОЙ и не затирать
реальное прохождение ученика. Для заданий провенанс уже есть
(`attempts.source_system` / `task_results.source_system`), а у материалов колонок
провенанса нет вовсе — только `student_id, material_id, status, completed_at,
skipped_at`. Без источника снятие отметки не отличило бы «поставил преподаватель»
от «прошёл сам» и удаляло бы чужой прогресс.

Добавляем `source VARCHAR(32) NOT NULL DEFAULT 'system'` + CHECK на закрытый набор
значений. Дефолт покрывает уже существующие строки без отдельного бэкфилла:
всё, что записано до этой миграции, — прохождение самого ученика ('system').

Revision ID: tsk297_manual_progress_source
Revises: tsk264_attempts_root_course
Create Date: 2026-07-20 01:00:00
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "tsk297_manual_progress_source"
down_revision: Union[str, None] = "tsk264_attempts_root_course"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_CHECK_NAME = "ck_student_material_progress_source"


def upgrade() -> None:
    op.add_column(
        "student_material_progress",
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'system'"),
            comment=(
                "Провенанс отметки (tsk-297): 'system' — прохождение самого ученика, "
                "'manual_teacher' — зачёт поставлен преподавателем/методистом вручную. "
                "Снятие зачёта удаляет только строки 'manual_teacher'."
            ),
        ),
    )
    op.create_check_constraint(
        _CHECK_NAME,
        "student_material_progress",
        "source IN ('system', 'manual_teacher')",
    )


def downgrade() -> None:
    # Rollback-note: снос колонки теряет провенанс отметок. Строки, поставленные
    # вручную, останутся как обычные 'completed' — прогресс ученика не пострадает,
    # но отличить ручной зачёт от реального прохождения станет нечем.
    # Ни один существующий столбец миграция не трогает.
    op.drop_constraint(
        _CHECK_NAME, "student_material_progress", type_="check"
    )
    op.drop_column("student_material_progress", "source")
```

### tests/test_manual_progress_tsk297.py
```python
"""Штатная правка прогресса ученика преподавателем (tsk-297).

Проверяем на НАСТОЯЩЕЙ БД (не на моках): согласованность зачёта с учебным
движком живёт в SQL — рекурсивные CTE дерева, JOIN попытки с результатом,
предикат очереди проверки. Мок этого не воспроизводит.

Ключевые инварианты:
* зачёт делает задание PASSED и next-item больше его не выдаёт;
* зачёт НЕ расходует лимит попыток ученика (суть ``root_course_id=NULL``);
* снятие возвращает задание в OPEN, строки не удаляются;
* повторный зачёт идемпотентен (``already=True``, второй попытки нет);
* зачтённое SA_COM не падает в очередь ручной проверки (``checked_at`` заполнен);
* teacher правит только своих; methodist/admin — bypass;
* массовая операция покрывает всё поддерево и идемпотентна;
* снятие отметки материала не трогает строку с ``source='system'``.

Граф фикстуры:
    root ──> child
    root: task_root_a, task_root_b (SA), material_root
    child: task_child (SA_COM с ручной проверкой), material_child
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import (
    learning_events_service,
    manual_progress_service,
    teacher_queue_service,
)
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.learning_engine_service import LearningEngineService

engine_svc = LearningEngineService()

_TAG = "tsk297"


async def _new_user(db, role: str | None, name: str) -> tuple[int, str]:
    """Создать пользователя с сессией и (опционально) ролью."""
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


@pytest.fixture
async def graph(db):
    """Учебный граф + ученик + преподаватели. Полная уборка за собой."""
    ids: dict[str, int] = {}
    try:
        async def new_course(title: str) -> int:
            return (
                await db.execute(
                    text(
                        "INSERT INTO courses (title, access_level) "
                        "VALUES (:t, 'self_guided') RETURNING id"
                    ),
                    {"t": title},
                )
            ).scalar()

        ids["root"] = await new_course(f"{_TAG} корень")
        ids["child"] = await new_course(f"{_TAG} подкурс")
        await db.execute(
            text(
                "INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"
            ),
            {"c": ids["child"], "p": ids["root"]},
        )

        difficulty_id = (
            await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
        ).scalar()
        assert difficulty_id is not None, "нет difficulties — граф не собрать"

        async def new_task(
            course_id: int, uid: str, *, type_: str = "SA", manual: bool = False,
            order_position: int = 1,
        ) -> int:
            rules = {"max_score": 10}
            if manual:
                rules["manual_review_required"] = True
            return (
                await db.execute(
                    text(
                        "INSERT INTO tasks (task_content, solution_rules, course_id, "
                        "difficulty_id, external_uid, max_score, order_position) "
                        "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, "
                        ":uid, 10, :op) RETURNING id"
                    ),
                    {
                        "tc": json.dumps({"type": type_, "stem": f"{_TAG} условие"}),
                        "sr": json.dumps(rules),
                        "cid": course_id,
                        "did": difficulty_id,
                        "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
                        "op": order_position,
                    },
                )
            ).scalar()

        async def new_material(course_id: int, title: str, order_position: int = 1) -> int:
            return (
                await db.execute(
                    text(
                        "INSERT INTO materials (course_id, title, type, content, "
                        "order_position) VALUES (:cid, :t, 'text', "
                        "CAST(:c AS jsonb), :op) RETURNING id"
                    ),
                    {
                        "cid": course_id,
                        "t": title,
                        "c": json.dumps({"body": f"{_TAG}"}),
                        "op": order_position,
                    },
                )
            ).scalar()

        ids["task_child"] = await new_task(
            ids["child"], "child", type_="SA_COM", manual=True, order_position=1
        )
        ids["task_root_a"] = await new_task(ids["root"], "root-a", order_position=1)
        ids["task_root_b"] = await new_task(ids["root"], "root-b", order_position=2)
        ids["material_child"] = await new_material(ids["child"], f"{_TAG} материал подкурса")
        ids["material_root"] = await new_material(ids["root"], f"{_TAG} материал корня")

        student_id, _ = await _new_user(db, "student", "stud")
        teacher_id, teacher_token = await _new_user(db, "teacher", "teach")
        other_id, other_token = await _new_user(db, "teacher", "other")
        methodist_id, methodist_token = await _new_user(db, "methodist", "met")
        ids["student"] = student_id
        ids["teacher"] = teacher_id
        ids["other"] = other_id
        ids["methodist"] = methodist_id

        await db.execute(
            text(
                "INSERT INTO student_teacher_links (student_id, teacher_id) "
                "VALUES (:s, :t) ON CONFLICT DO NOTHING"
            ),
            {"s": student_id, "t": teacher_id},
        )
        await db.execute(
            text(
                "INSERT INTO user_courses (user_id, course_id, is_active) "
                "VALUES (:u, :c, true)"
            ),
            {"u": student_id, "c": ids["root"]},
        )
        await db.commit()

        yield {
            "ids": ids,
            "db": db,
            "tokens": {
                "teacher": teacher_token,
                "other": other_token,
                "methodist": methodist_token,
            },
        }
    finally:
        await db.rollback()
        user_ids = [ids.get(k) for k in ("student", "teacher", "other", "methodist") if k in ids]
        task_ids = [ids[k] for k in ("task_child", "task_root_a", "task_root_b") if k in ids]
        material_ids = [ids[k] for k in ("material_child", "material_root") if k in ids]
        course_ids = [ids[k] for k in ("root", "child") if k in ids]
        if user_ids:
            await db.execute(
                text("DELETE FROM task_results WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text("DELETE FROM attempts WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text("DELETE FROM student_material_progress WHERE student_id = ANY(:u)"),
                {"u": user_ids},
            )
            await db.execute(
                text("DELETE FROM student_task_progress WHERE student_id = ANY(:u)"),
                {"u": user_ids},
            )
            await db.execute(
                text("DELETE FROM student_course_state WHERE student_id = ANY(:u)"),
                {"u": user_ids},
            )
            await db.execute(
                text("DELETE FROM user_courses WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text(
                    "DELETE FROM student_teacher_links "
                    "WHERE student_id = ANY(:u) OR teacher_id = ANY(:u)"
                ),
                {"u": user_ids},
            )
            await db.execute(
                text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
            await db.execute(
                text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids}
            )
        if task_ids:
            await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
        if material_ids:
            await db.execute(
                text("DELETE FROM materials WHERE id = ANY(:m)"), {"m": material_ids}
            )
        if course_ids:
            await db.execute(
                text("DELETE FROM course_parents WHERE course_id = ANY(:c)"), {"c": course_ids}
            )
        # Сами `users` не удаляем: FK `audit_event.user_id` — SET NULL, а таблица
        # append-only (триггер `audit_event_no_modify`). Пользователей с
        # `@example.com` подбирает session-scoped sweep из conftest, который
        # умеет временно снимать триггер.
        if course_ids:
            await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": course_ids})
        await db.commit()


# ─── Согласованность с учебным движком ──────────────────────────────────────


async def test_grant_makes_task_passed(graph):
    """Зачёт задания переводит его в PASSED в глазах движка."""
    ids, db = graph["ids"], graph["db"]
    before = await engine_svc.compute_task_state(db, ids["student"], ids["task_root_a"])
    assert before.state == "OPEN"

    res = await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()
    assert res["granted"] is True and res["already"] is False

    after = await engine_svc.compute_task_state(db, ids["student"], ids["task_root_a"])
    assert after.state == "PASSED", "зачёт обязан быть виден движку как пройденное"


async def test_next_item_does_not_reissue_granted_task(graph):
    """Ключевая согласованность: next-item не выдаёт зачтённое задание заново."""
    ids, db = graph["ids"], graph["db"]

    # Материалы идут раньше заданий — зачитываем их, чтобы движок дошёл до задач.
    for key in ("material_child", "material_root"):
        await manual_progress_service.grant_material(
            db, student_id=ids["student"], material_id=ids[key], granted_by=ids["teacher"]
        )
    await db.commit()

    first = await engine_svc.resolve_next_item(
        db, ids["student"], root_course_id=ids["root"]
    )
    # Обход post-order: подкурс идёт раньше корня, поэтому первым выдаётся
    # задание подкурса.
    assert first.type == "task"
    assert first.task_id == ids["task_child"]
    granted_task = first.task_id

    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=granted_task, granted_by=ids["teacher"]
    )
    await db.commit()

    following = await engine_svc.resolve_next_item(
        db, ids["student"], root_course_id=ids["root"]
    )
    # Ассертим КОНКРЕТНОЕ следующее задание, а не «!= зачтённого»: последнее
    # проходило бы и при `task_id is None` (движок вообще ничего не выдал).
    assert following.type == "task"
    assert following.task_id == ids["task_root_a"], (
        "после зачёта движок обязан выдать следующее по порядку задание, "
        "а не повторить зачтённое и не остановиться"
    )


async def test_grant_does_not_consume_attempt_limit(graph):
    """Зачёт не расходует попытки ученика — суть `root_course_id = NULL`."""
    ids, db = graph["ids"], graph["db"]
    before = await engine_svc.compute_task_state(
        db, ids["student"], ids["task_root_a"], root_course_id=ids["root"]
    )
    assert before.attempts_used == 0

    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()

    after = await engine_svc.compute_task_state(
        db, ids["student"], ids["task_root_a"], root_course_id=ids["root"]
    )
    assert after.attempts_used == 0, (
        "синтетическая попытка съела попытку ученика — root_course_id должен быть NULL"
    )
    assert after.state == "PASSED"


async def test_revoke_cancels_synthetic_attempt(graph):
    """Снятие зачёта аннулирует синтетическую попытку, строки остаются в истории.

    OPEN здесь — частный случай: у ученика по этому заданию нет РЕАЛЬНЫХ попыток.
    Если бы они были, задание вернулось бы в своё настоящее состояние
    (``IN_PROGRESS`` / ``FAILED`` / ``BLOCKED_LIMIT``), а не в ``OPEN``.
    """
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()

    res = await manual_progress_service.revoke_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], revoked_by=ids["teacher"]
    )
    await db.commit()
    assert res["already"] is False

    state = await engine_svc.compute_task_state(db, ids["student"], ids["task_root_a"])
    assert state.state == "OPEN", "после снятия зачёта задание обязано снова быть открытым"

    kept = (
        await db.execute(
            text(
                "SELECT count(*) FROM attempts WHERE user_id = :u "
                "AND source_system = :s AND cancel_reason = :r"
            ),
            {
                "u": ids["student"],
                "s": manual_progress_service.MANUAL_SOURCE,
                "r": manual_progress_service.REVOKE_REASON,
            },
        )
    ).scalar()
    assert kept == 1, "строки не удаляются — история правок сохраняется"


async def test_revoke_without_grant_is_idempotent(graph):
    """Снятие несуществующего зачёта — не ошибка, а already=True."""
    ids, db = graph["ids"], graph["db"]
    res = await manual_progress_service.revoke_task(
        db, student_id=ids["student"], task_id=ids["task_root_b"], revoked_by=ids["teacher"]
    )
    await db.commit()
    assert res["already"] is True


async def test_grant_is_idempotent(graph):
    """Повторный зачёт не создаёт вторую попытку и возвращает already=True."""
    ids, db = graph["ids"], graph["db"]
    first = await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()
    second = await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()

    assert first["already"] is False
    assert second["already"] is True

    attempts = (
        await db.execute(
            text(
                "SELECT count(*) FROM attempts WHERE user_id = :u AND source_system = :s"
            ),
            {"u": ids["student"], "s": manual_progress_service.MANUAL_SOURCE},
        )
    ).scalar()
    assert attempts == 1, "повторный зачёт задвоил синтетическую попытку"


async def test_granted_manual_task_not_in_review_queue(graph):
    """Зачтённое SA_COM с ручной проверкой не попадает в очередь преподавателя."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_child"], granted_by=ids["methodist"]
    )
    await db.commit()

    items, _total = await teacher_queue_service.list_pending_reviews(
        db, ids["methodist"], limit=200
    )
    assert all(it["task_id"] != ids["task_child"] for it in items), (
        "зачтённая работа встала в очередь проверки — checked_at не заполнен"
    )

    checked = (
        await db.execute(
            text(
                "SELECT checked_at, checked_by FROM task_results "
                "WHERE user_id = :u AND task_id = :t"
            ),
            {"u": ids["student"], "t": ids["task_child"]},
        )
    ).fetchone()
    assert checked is not None and checked[0] is not None
    assert checked[1] == ids["methodist"]


# ─── Материалы ──────────────────────────────────────────────────────────────


async def test_material_grant_and_revoke(graph):
    """Материал: отметка ставится с провенансом и снимается."""
    ids, db = graph["ids"], graph["db"]
    res = await manual_progress_service.grant_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        granted_by=ids["teacher"],
    )
    await db.commit()
    assert res["already"] is False

    row = (
        await db.execute(
            text(
                "SELECT status, source FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).fetchone()
    assert row is not None and row[0] == "completed"
    assert row[1] == manual_progress_service.MANUAL_SOURCE

    revoked = await manual_progress_service.revoke_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        revoked_by=ids["teacher"],
    )
    await db.commit()
    assert revoked["already"] is False

    gone = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).scalar()
    assert gone == 0


async def test_material_revoke_keeps_real_progress(graph):
    """Снятие не трогает материал, пройденный самим учеником (source='system')."""
    ids, db = graph["ids"], graph["db"]
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "(student_id, material_id, status, completed_at, source) "
            "VALUES (:s, :m, 'completed', now(), 'system')"
        ),
        {"s": ids["student"], "m": ids["material_root"]},
    )
    await db.commit()

    res = await manual_progress_service.revoke_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        revoked_by=ids["teacher"],
    )
    await db.commit()
    assert res["already"] is True, "реального прохождения ученика тут не было — но и снимать нечего"

    kept = (
        await db.execute(
            text(
                "SELECT source FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).fetchone()
    assert kept is not None and kept[0] == "system", (
        "снятие ручной отметки удалило реальный прогресс ученика"
    )


async def test_real_completion_overrides_manual_provenance(graph):
    """Ученик реально прошёл материал после ручной отметки → снятие его не сотрёт.

    Порядок «преподаватель отметил → ученик прошёл сам → преподаватель снял»
    ломался: upsert реального прохождения не трогал `source`, тот оставался
    ``manual_teacher``, и снятие удаляло настоящий прогресс ученика.
    """
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        granted_by=ids["teacher"],
    )
    await db.commit()

    # Реальный путь ученика — ровно тот, которым ходит `POST /learning/materials/{id}/complete`.
    await learning_events_service.set_material_completed(
        db, ids["student"], ids["material_root"]
    )
    await db.commit()

    source = (
        await db.execute(
            text(
                "SELECT source FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).scalar()
    assert source == manual_progress_service.SYSTEM_SOURCE, (
        "реальное прохождение обязано перебить ручной провенанс"
    )

    res = await manual_progress_service.revoke_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        revoked_by=ids["teacher"],
    )
    await db.commit()
    assert res["already"] is True

    row = (
        await db.execute(
            text(
                "SELECT status, source FROM student_material_progress "
                "WHERE student_id = :s AND material_id = :m"
            ),
            {"s": ids["student"], "m": ids["material_root"]},
        )
    ).fetchone()
    assert row is not None, "снятие ручной отметки удалило реальное прохождение ученика"
    assert row[0] == "completed" and row[1] == manual_progress_service.SYSTEM_SOURCE


# ─── Массовые операции ──────────────────────────────────────────────────────


async def test_bulk_grant_covers_subtree_and_is_idempotent(graph):
    """Массовый зачёт покрывает всё поддерево; повтор ничего не добавляет."""
    ids, db = graph["ids"], graph["db"]
    res = await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()

    assert res["tasks_affected"] == 3, "в дереве 3 задания (2 в корне + 1 в подкурсе)"
    assert res["materials_affected"] == 2, "в дереве 2 материала"
    assert res["skipped_already"] == 0

    for key in ("task_root_a", "task_root_b", "task_child"):
        state = await engine_svc.compute_task_state(db, ids["student"], ids[key])
        assert state.state == "PASSED", f"{key} не зачтён массовой операцией"

    again = await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()
    assert again["tasks_affected"] == 0 and again["materials_affected"] == 0
    assert again["skipped_already"] == 5, "повтор обязан быть полностью идемпотентным"


async def test_bulk_revoke_rolls_subtree_back(graph):
    """Массовое снятие возвращает всё поддерево в исходное состояние."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()

    res = await manual_progress_service.revoke_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], revoked_by=ids["teacher"]
    )
    await db.commit()
    assert res["tasks_affected"] == 3 and res["materials_affected"] == 2

    for key in ("task_root_a", "task_root_b", "task_child"):
        state = await engine_svc.compute_task_state(db, ids["student"], ids[key])
        assert state.state == "OPEN", f"{key} остался зачтённым после массового снятия"


async def test_bulk_grant_is_atomic_on_failure(graph, monkeypatch):
    """Сбой посреди массового зачёта не оставляет частичных данных.

    Раньше попытка создавалась через `AttemptsService.create_attempt` →
    `BaseRepository.create(commit=True)`, то есть каждое задание коммитилось
    отдельно: исключение на середине фиксировало часть дерева, а запись аудита
    (одна на всю пачку, в конце) не сохранялась — прогресс менялся без следа.
    """
    ids, db = graph["ids"], graph["db"]
    real_load_task = manual_progress_service._load_task  # noqa: SLF001
    calls = {"n": 0}

    async def flaky_load_task(db_, task_id):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("tsk297: имитация сбоя на третьем задании")
        return await real_load_task(db_, task_id)

    monkeypatch.setattr(manual_progress_service, "_load_task", flaky_load_task)

    with pytest.raises(RuntimeError):
        await manual_progress_service.grant_course_subtree(
            db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
        )
    await db.rollback()

    attempts = (
        await db.execute(
            text(
                "SELECT count(*) FROM attempts WHERE user_id = :u AND source_system = :s"
            ),
            {"u": ids["student"], "s": manual_progress_service.MANUAL_SOURCE},
        )
    ).scalar()
    assert attempts == 0, (
        "после отката осталась синтетическая попытка — значит внутри операции был commit"
    )

    results = (
        await db.execute(
            text(
                "SELECT count(*) FROM task_results WHERE user_id = :u AND source_system = :s"
            ),
            {"u": ids["student"], "s": manual_progress_service.MANUAL_SOURCE},
        )
    ).scalar()
    assert results == 0, "после отката остался синтетический результат"

    materials = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_material_progress "
                "WHERE student_id = :s AND source = :src"
            ),
            {"s": ids["student"], "src": manual_progress_service.MANUAL_SOURCE},
        )
    ).scalar()
    assert materials == 0, "после отката осталась ручная отметка материала"


async def test_bulk_operations_refresh_course_state(graph):
    """Массовые операции пересчитывают `student_course_state`.

    Именно оттуда `me_service.get_courses_with_progress` берёт ``is_completed``:
    без пересчёта ученик видел бы 100% пройденных элементов при незавершённом
    курсе (и завершённый курс после массового снятия).
    """
    ids, db = graph["ids"], graph["db"]

    async def course_state() -> str | None:
        return (
            await db.execute(
                text(
                    "SELECT state FROM student_course_state "
                    "WHERE student_id = :s AND course_id = :c"
                ),
                {"s": ids["student"], "c": ids["root"]},
            )
        ).scalar()

    await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()
    assert await course_state() == "COMPLETED", (
        "после массового зачёта курс не отмечен завершённым — ученик увидит 100%, "
        "но курс останется незавершённым"
    )

    await manual_progress_service.revoke_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], revoked_by=ids["teacher"]
    )
    await db.commit()
    assert await course_state() == "NOT_STARTED", (
        "после массового снятия курс остался завершённым"
    )


async def test_single_grant_refreshes_course_state(graph):
    """Единичный зачёт тоже пересчитывает состояние корня (не только массовый)."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await db.commit()

    state = (
        await db.execute(
            text(
                "SELECT state FROM student_course_state "
                "WHERE student_id = :s AND course_id = :c"
            ),
            {"s": ids["student"], "c": ids["root"]},
        )
    ).scalar()
    assert state == "IN_PROGRESS"


# ─── Чтение прогресса ───────────────────────────────────────────────────────


async def test_progress_tree_marks_manual_items(graph):
    """GET-прогресс помечает ручные отметки флагом manual и автором."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], granted_by=ids["teacher"]
    )
    await manual_progress_service.grant_material(
        db, student_id=ids["student"], material_id=ids["material_root"],
        granted_by=ids["teacher"],
    )
    await db.commit()

    data = await manual_progress_service.get_student_progress(
        db, student_id=ids["student"], course_id=ids["root"]
    )
    by_key = {(i["item_type"], i["item_id"]): i for i in data["items"]}

    granted_task = by_key[("task", ids["task_root_a"])]
    assert granted_task["status"] == "PASSED"
    assert granted_task["manual"] is True
    assert granted_task["granted_by"] == ids["teacher"]
    assert granted_task["granted_at"] is not None

    plain_task = by_key[("task", ids["task_root_b"])]
    assert plain_task["status"] == "OPEN" and plain_task["manual"] is False

    granted_material = by_key[("material", ids["material_root"])]
    assert granted_material["status"] == "COMPLETED"
    assert granted_material["manual"] is True

    untouched_material = by_key[("material", ids["material_child"])]
    assert untouched_material["status"] == "NOT_STARTED"


async def test_progress_tree_has_course_nodes_and_parents(graph):
    """Дерево содержит узлы тем с parent_course_id и учебным порядком."""
    ids, db = graph["ids"], graph["db"]
    data = await manual_progress_service.get_student_progress(
        db, student_id=ids["student"], course_id=ids["root"]
    )
    items = data["items"]

    courses = [i for i in items if i["item_type"] == "course"]
    assert {c["item_id"] for c in courses} == {ids["root"], ids["child"]}

    root_node = next(c for c in courses if c["item_id"] == ids["root"])
    child_node = next(c for c in courses if c["item_id"] == ids["child"])
    assert root_node["parent_course_id"] is None, "у запрошенного корня родителя нет"
    assert child_node["parent_course_id"] == ids["root"]
    assert root_node["manual"] is None and child_node["manual"] is None

    # У всех заданий/материалов parent_course_id — их собственный узел.
    for item in items:
        if item["item_type"] in ("task", "material"):
            assert item["parent_course_id"] == item["course_id"]

    # Учебный порядок: post-order — подкурс идёт раньше курса-контейнера,
    # содержимое узла — сразу после его заголовка.
    order = [(i["item_type"], i["item_id"]) for i in items]
    assert order.index(("course", ids["child"])) < order.index(("course", ids["root"]))
    assert order.index(("course", ids["child"])) < order.index(("task", ids["task_child"]))
    assert order.index(("material", ids["material_child"])) < order.index(
        ("task", ids["task_child"])
    ), "внутри узла материалы идут раньше заданий — как у движка"
    assert order.index(("task", ids["task_root_a"])) < order.index(
        ("task", ids["task_root_b"])
    ), "задания узла идут по order_position"


async def test_course_node_status_rolls_up_subtree(graph):
    """Статус узла сворачивается по поддереву: NOT_STARTED → IN_PROGRESS → COMPLETED."""
    ids, db = graph["ids"], graph["db"]

    async def root_node_status() -> str:
        data = await manual_progress_service.get_student_progress(
            db, student_id=ids["student"], course_id=ids["root"]
        )
        return next(
            i["status"] for i in data["items"]
            if i["item_type"] == "course" and i["item_id"] == ids["root"]
        )

    assert await root_node_status() == "NOT_STARTED"

    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_child"], granted_by=ids["teacher"]
    )
    await db.commit()
    assert await root_node_status() == "IN_PROGRESS"

    await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()
    assert await root_node_status() == "COMPLETED"


async def test_progress_read_does_not_escalate(graph, monkeypatch):
    """Чтение карточки не зовёт `compute_course_state` и не рассылает уведомлений.

    Эскалации специально дан ПОВОД сработать: у ученика висит реальный
    непроверенный SA_COM (``checked_at IS NULL``) — именно этот предикат Y-6
    проверяет при COMPLETED. Без него проверка была бы вакуумной: ручной зачёт
    ``checked_at`` заполняет, и тест прошёл бы даже с возвращённым в read-путь
    `compute_course_state`. Дополнительно шпионим за самим вызовом — на случай,
    если эскалацию заглушат гард идемпотентности или rate-limit.
    """
    ids, db = graph["ids"], graph["db"]

    # Реальная непроверенная работа ученика по SA_COM-заданию подкурса.
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, source_system) "
                "VALUES (:u, :c, 'test') RETURNING id"
            ),
            {"u": ids["student"], "c": ids["child"]},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at) "
            "VALUES (:u, :t, :a, 0, 10, false, now(), now(), 0, NULL)"
        ),
        {"u": ids["student"], "t": ids["task_child"], "a": attempt_id},
    )
    await db.commit()

    await manual_progress_service.grant_course_subtree(
        db, student_id=ids["student"], course_id=ids["root"], granted_by=ids["teacher"]
    )
    await db.commit()

    calls: list[int] = []
    original = LearningEngineService.compute_course_state

    async def spy(self, db_, student_id, course_id, **kwargs):  # noqa: ANN001, ANN202
        calls.append(int(course_id))
        return await original(self, db_, student_id, course_id, **kwargs)

    monkeypatch.setattr(LearningEngineService, "compute_course_state", spy)

    notifications_before = (
        await db.execute(
            text("SELECT count(*) FROM notifications WHERE user_id = :u"),
            {"u": ids["methodist"]},
        )
    ).scalar()

    data = await manual_progress_service.get_student_progress(
        db, student_id=ids["student"], course_id=ids["root"]
    )
    await db.commit()

    root_status = next(
        i["status"] for i in data["items"]
        if i["item_type"] == "course" and i["item_id"] == ids["root"]
    )
    assert root_status == "COMPLETED"
    assert calls == [], (
        "чтение прогресса дёрнуло compute_course_state — свёртка на read-пути "
        "обязана считаться локально, иначе просмотр карточки рассылает уведомления"
    )

    notifications_after = (
        await db.execute(
            text("SELECT count(*) FROM notifications WHERE user_id = :u"),
            {"u": ids["methodist"]},
        )
    ).scalar()
    assert notifications_after == notifications_before, (
        "чтение прогресса разослало уведомления — read-эндпоинт не должен этого делать"
    )


# ─── ACL и HTTP-контракт ────────────────────────────────────────────────────


async def test_api_foreign_teacher_forbidden(graph, client):
    """Преподаватель без связки с учеником и без ACL на курс → 403."""
    ids = graph["ids"]
    resp = await client.post(
        f"/api/v1/teacher/students/{ids['student']}/progress/tasks/{ids['task_root_a']}",
        json={"comment": "чужой ученик"},
        headers={"Authorization": f"Bearer {graph['tokens']['other']}"},
    )
    assert resp.status_code == 403, resp.text


async def test_api_course_acl_requires_student_enrollment(graph, client):
    """ACL на курс не даёт власти над ЛЮБЫМ пользователем — только над учениками курса.

    Раньше ветка курсового ACL проверяла только «курс мой» и ничего не знала про
    ученика: преподаватель курса X мог править прогресс произвольного user_id
    (включая другого преподавателя), просто перебирая идентификаторы.
    """
    ids, db = graph["ids"], graph["db"]
    outsider_id, _ = await _new_user(db, "student", "outsider")
    try:
        # `other` — преподаватель без связки с учеником; даём ему ACL на корень.
        await db.execute(
            text(
                "INSERT INTO teacher_courses (teacher_id, course_id) "
                "VALUES (:t, :c) ON CONFLICT DO NOTHING"
            ),
            {"t": ids["other"], "c": ids["root"]},
        )
        await db.commit()

        headers = {"Authorization": f"Bearer {graph['tokens']['other']}"}

        # Контроль: ученик КУРСА теперь доступен — ACL работает.
        allowed = await client.post(
            f"/api/v1/teacher/students/{ids['student']}/progress/tasks/{ids['task_root_a']}",
            json={},
            headers=headers,
        )
        assert allowed.status_code == 200, allowed.text

        # А посторонний пользователь, не записанный на этот курс, — нет.
        denied = await client.post(
            f"/api/v1/teacher/students/{outsider_id}/progress/tasks/{ids['task_root_a']}",
            json={},
            headers=headers,
        )
        assert denied.status_code == 403, denied.text
    finally:
        await db.execute(
            text("DELETE FROM teacher_courses WHERE teacher_id = :t"), {"t": ids["other"]}
        )
        for table, col in (
            ("user_roles", "user_id"),
            ("user_session", "user_id"),
            ("identity_link", "user_id"),
        ):
            await db.execute(
                text(f"DELETE FROM {table} WHERE {col} = :u"),  # nosec B608
                {"u": outsider_id},
            )
        await db.commit()


async def test_api_methodist_can_grant(graph, client):
    """Методист правит прогресс любого ученика (bypass) → 200."""
    ids = graph["ids"]
    resp = await client.post(
        f"/api/v1/teacher/students/{ids['student']}/progress/tasks/{ids['task_root_a']}",
        json={"comment": "перенос наработок"},
        headers={"Authorization": f"Bearer {graph['tokens']['methodist']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["granted"] is True and body["already"] is False
    assert body["source"] == manual_progress_service.MANUAL_SOURCE


async def test_api_linked_teacher_full_cycle(graph, client):
    """Свой преподаватель: зачёт → прогресс → снятие через HTTP."""
    ids = graph["ids"]
    headers = {"Authorization": f"Bearer {graph['tokens']['teacher']}"}
    base = f"/api/v1/teacher/students/{ids['student']}/progress"

    granted = await client.post(f"{base}/tasks/{ids['task_root_a']}", json={}, headers=headers)
    assert granted.status_code == 200, granted.text

    tree = await client.get(f"{base}?course_id={ids['root']}", headers=headers)
    assert tree.status_code == 200, tree.text
    item = next(
        i for i in tree.json()["items"]
        if i["item_type"] == "task" and i["item_id"] == ids["task_root_a"]
    )
    assert item["status"] == "PASSED" and item["manual"] is True

    revoked = await client.delete(f"{base}/tasks/{ids['task_root_a']}", headers=headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["already"] is False


async def test_api_progress_returns_course_selector(graph, client):
    """GET отдаёт список доступных курсов ученика — селектор питается им же."""
    ids = graph["ids"]
    headers = {"Authorization": f"Bearer {graph['tokens']['teacher']}"}
    base = f"/api/v1/teacher/students/{ids['student']}/progress"

    with_tree = await client.get(f"{base}?course_id={ids['root']}", headers=headers)
    assert with_tree.status_code == 200, with_tree.text
    body = with_tree.json()
    assert any(c["course_id"] == ids["root"] for c in body["courses"])
    assert body["items"], "с course_id дерево обязано быть заполнено"

    only_courses = await client.get(base, headers=headers)
    assert only_courses.status_code == 200, only_courses.text
    body = only_courses.json()
    assert body["course_id"] is None
    assert body["items"] == [], "без course_id дерево пустое"
    assert any(c["course_id"] == ids["root"] for c in body["courses"])
    assert all(c.get("title") for c in body["courses"])


async def test_api_course_selector_respects_acl(graph, client):
    """Курсы чужого ученика в селектор постороннего преподавателя не попадают."""
    ids = graph["ids"]
    resp = await client.get(
        f"/api/v1/teacher/students/{ids['student']}/progress",
        headers={"Authorization": f"Bearer {graph['tokens']['other']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["courses"] == []


async def test_api_bulk_endpoint(graph, client):
    """Массовый эндпоинт по узлу отдаёт счётчики по всему поддереву."""
    ids = graph["ids"]
    headers = {"Authorization": f"Bearer {graph['tokens']['methodist']}"}
    resp = await client.post(
        f"/api/v1/teacher/students/{ids['student']}/progress/courses/{ids['root']}",
        json={"comment": "перевод ученика на его место"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tasks_affected"] == 3 and body["materials_affected"] == 2


async def test_audit_events_written(graph):
    """Каждая операция оставляет запись аудита нужного типа."""
    ids, db = graph["ids"], graph["db"]
    await manual_progress_service.grant_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"],
        granted_by=ids["teacher"], comment="перенос",
    )
    await manual_progress_service.revoke_task(
        db, student_id=ids["student"], task_id=ids["task_root_a"], revoked_by=ids["teacher"]
    )
    await db.commit()

    rows = (
        await db.execute(
            text(
                "SELECT event_type, details FROM audit_event "
                "WHERE user_id = :u AND event_type = ANY(:types) ORDER BY id"
            ),
            {
                "u": ids["teacher"],
                "types": ["teacher.progress.granted", "teacher.progress.revoked"],
            },
        )
    ).fetchall()
    types = [r[0] for r in rows]
    assert "teacher.progress.granted" in types
    assert "teacher.progress.revoked" in types
    granted_details = next(r[1] for r in rows if r[0] == "teacher.progress.granted")
    assert granted_details["student_id"] == ids["student"]
    assert granted_details["bulk"] is False
```
