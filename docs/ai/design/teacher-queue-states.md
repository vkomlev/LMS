# Teacher Review Queue — State Machine (FSM)

**Дата:** 2026-04-27
**Статус:** живой документ (описывает существующее + расширение под SA_COM из SPW)
**Реализация:** `app/models/task_results.py`, `app/api/v1/teacher_reviews.py`, `app/services/teacher_queue_service.py`
**Источник решения:** Phase Y-4 (MVP: ручная проверка в TG_LMS teacher-bot; sandbox — post-MVP)

> **Назначение:** зафиксировать lifecycle SA_COM-результата от submit ученика до grade преподавателем. Документ описывает то, **что уже есть в LMS** (claim/release/lock_token), плюс жизненный цикл `is_correct` и `checked_at` для SA_COM. Используется для Phase Y-4 (teacher-bot UX в TG_LMS) и интеграции SPW.

## 1. Сущности и поля

`task_results` (`app/models/task_results.py`):

| Поле | Назначение в FSM |
|---|---|
| `id` | PK |
| `user_id`, `task_id`, `attempt_id` | связь |
| `answer_json` | хранит `{type: "SA_COM", response: {value: <код>, comment?: <текст>}}` |
| `score`, `max_score` | оценка (выставляется при grade) |
| `is_correct: Optional[bool]` | **ключевой field FSM**: NULL = ждёт ручной проверки, true/false = оценено |
| `checked_at: Optional[datetime]` | timestamp grade |
| `checked_by: Optional[int]` | id преподавателя; NULL для авто-проверок SA/SC/MC |
| `submitted_at` | submit от ученика |
| `received_at` | начало работы над задачей (start_or_get_attempt) |
| `metrics` | JSONB — комментарий преподавателя, дополнительные сигналы |
| **`review_claimed_by`** | id преподавателя, удерживающего lock |
| **`review_claim_token`** | opaque токен для release (CSRF-style) |
| **`review_claim_expires_at`** | TTL lock'а (auto-release при просрочке) |

## 2. Состояния (states)

```
                       ┌───────────────────┐
 submit от ученика ──► │ AWAITING_REVIEW   │
 (is_correct=NULL,     │                   │
  review_claimed_by=   │  is_correct=NULL  │
  NULL)                │  checked_at=NULL  │
                       └─────────┬─────────┘
                                 │
          claim-next (atomic)    │
          ┌──────────────────────┘
          ▼
 ┌──────────────────────────┐
 │  CLAIMED                 │ ── lock_expires_at просрочен ──┐
 │                          │ ── teacher release (revert) ───┤
 │ review_claimed_by=tid    │                                 │
 │ review_claim_token=…     │                                 │
 │ review_claim_expires_at  │ ── teacher grade ─────────┐     │
 └──────────────────────────┘                            │     │
                                                         │     │
                                                         ▼     │
                                               ┌──────────────┐│
                                               │  GRADED      ││
                                               │              ││
                                               │ is_correct = ││
                                               │   true|false ││
                                               │ score=…      ││
                                               │ checked_at=… ││
                                               │ checked_by=… ││
                                               │ review_      ││
                                               │   claim_*    ││
                                               │   = NULL     ││
                                               └──────────────┘│
                                                               │
                                                               ▼
                                                     ┌──────────────────┐
                                                     │ AWAITING_REVIEW  │
                                                     │ (повтор claim    │
                                                     │ возможен другим  │
                                                     │ преподавателем)  │
                                                     └──────────────────┘
```

## 3. Переходы

### 3.1. `none → AWAITING_REVIEW` — submit ответа

**Триггер:** `POST /api/v1/attempts/{attempt_id}/finish` для попытки, содержащей SA_COM ответ.
**Эффект:** create `task_results` (`is_correct=NULL`, `checked_at=NULL`, `review_claim_*=NULL`).
**Идемпотентность:** одна `task_results` запись на (user_id, task_id, attempt_id). Двойной finish — server upsert, не дубликат.

### 3.2. `AWAITING_REVIEW → CLAIMED` — преподаватель берёт в работу

**Триггер:** `POST /api/v1/teacher/reviews/claim-next` с `teacher_id`, `ttl_sec`, optional `course_id`/`user_id`/`idempotency_key`.
**Service:** `claim_next_review` в `teacher_queue_service.py`.
**Эффект:**
- atomic UPDATE: `review_claimed_by = teacher_id`, `review_claim_token = <random>`, `review_claim_expires_at = now() + ttl_sec`
- Возвращает `ReviewClaimItem` (result_id, user, task, answer_json, attempt) + `lock_token` + `lock_expires_at`
- Если очередь пуста — `empty=true`

**RBAC:** в текущей реализации не enforced. После Phase Y-1 — проверка `current_user.role IN ('teacher','admin')` + фильтр по teacher_courses в Phase Y-4.

### 3.3. `CLAIMED → AWAITING_REVIEW` — release (отмена claim'а)

**Триггер 1:** `POST /api/v1/teacher/reviews/{result_id}/release` с правильным `lock_token`.
**Триггер 2:** TTL истёк — lazy cleanup перед следующим `claim-next` сбрасывает `review_claim_*` в NULL.
**Race condition:** release с неверным токеном — 409 conflict.

### 3.4. `CLAIMED → GRADED` — grade

**Триггер:** `POST /api/v1/teacher/reviews/{result_id}/grade`

> **Статус:** endpoint запланирован на Phase Y-4. Проверить, существует ли уже под другим именем в `teacher_reviews.py` или `task_results_extra.py` перед реализацией.

**Предложенный контракт:**
```json
POST /api/v1/teacher/reviews/{result_id}/grade
Body: {
  "teacher_id": int,
  "lock_token": string,
  "score": int,
  "is_correct": bool,
  "comment": string | null
}
```

**Эффект:**
- atomic UPDATE: `score=…`, `max_score=…`, `is_correct=…`, `checked_at=now()`, `checked_by=teacher_id`, `metrics={comment, …}`, `review_claim_*=NULL`
- 409 conflict если `lock_token` не совпадает или `review_claim_expires_at` просрочен
- Уведомление ученика (in-app badge + email если `users.email` присутствует)
- Audit-event `teacher.graded`

### 3.5. `GRADED → AWAITING_REVIEW` — повторная сдача / переоценка

**Триггер 1 (ученик):** новая попытка + finish → **новая** `task_results` запись; старая остаётся в истории.
**Триггер 2 (переоценка):** post-MVP. Web-кабинет преподавателя — endpoint `regrade` с audit-trail.

## 4. Race conditions и инварианты

| Инвариант | Защита |
|---|---|
| Один SA_COM submit = одна `task_results` запись | UPSERT по `attempt_uid` |
| Два преподавателя одновременно `claim-next` → разные результаты | atomic claim; `SELECT … FOR UPDATE SKIP LOCKED` в `teacher_queue_service` |
| Release с неверным токеном | 409 через сравнение `lock_token` |
| Grade просрочен | grade endpoint валидирует `lock_token` + `expires_at > now()` |
| Cleanup просроченных lock'ов | Lazy: при `claim-next` сначала чистка `WHERE review_claim_expires_at < now()` |

## 5. RBAC и видимость очереди (Phase Y-4)

В MVP teacher-бот вызывает `claim-next` с `teacher_id` из Telegram identity. Защита от cross-teacher leak:
- Фильтр по `task_id IN (SELECT task_id WHERE course IN teacher_courses(teacher_id))`
- Проверить, есть ли уже в `teacher_queue_service.py` — если да, переиспользовать

## 6. Метрики и алерты

| Метрика | SQL |
|---|---|
| Pending depth | `SELECT count(*) FROM task_results WHERE is_correct IS NULL AND review_claimed_by IS NULL` |
| Stale claims | `SELECT count(*) FROM task_results WHERE review_claim_expires_at < now()` |
| Time-to-grade (p50) | `SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY checked_at - submitted_at) FROM task_results WHERE checked_at IS NOT NULL AND submitted_at >= now() - interval '7 days'` |

Cron-alert: если pending depth > 0 старше 24h → уведомление в TG.

## 7. Будущие расширения (post-MVP)

- **Sandbox-runner:** стартует на `AWAITING_REVIEW`, проверяет, при success — обновляет `is_correct` без преподавателя. FSM не меняется.
- **LLM-критерии:** заполняет `metrics.llm_score`; при low confidence — оставляет преподавателю.
- **Web-кабинет преподавателя:** UI поверх того же API.
- **SLA visibility:** view `vw_pending_review_age_p95` + отображение в SPW.
