# Tech-Spec Y-4 (LMS-side): SA_COM teacher grade flow + inbox + history — backend

**Дата:** 2026-04-30
**Phase:** Y-4 (Stage S1, backend-only)
**Источник:** [CB authority Y-4 v1](../../../ContentBackbone/docs/tech-specs/tech-spec-Y4-sa-com-teacher-queue-v1.md)
**Статус:** READY (создан до реализации; pre-implementation backsync per ERRORS #1, #2)
**Alembic head на старт:** `20260429_010000_m7_streak_idx`
**Alembic head после S1:** `20260430_010000_m8_inbox` (revision_id ограничен 32 символами alembic_version.version_num)

---

## 1. Цель Stage S1

Замкнуть backend часть учебного цикла SA_COM:
- Преподаватель в TG_LMS оценивает попытку → INSERT inbox-уведомление + email ученику + audit
- Ученик через SPW читает inbox + историю попыток + значок «непрочитанное»
- Поллер TG_LMS читает количество pending заявок без захвата

S2 (SPW frontend) и S3+S4 (TG_LMS dialog/poller) — отдельные чаты в своих проектах.

## 2. Контекст и ключевые расхождения с CB authority

| Пункт | CB Y-4 v1 | Реальность LMS | Решение |
|---|---|---|---|
| §9.1.7 «расширить claim-next фильтром teacher_courses» | предполагается отсутствие фильтра | **`REVIEW_ACL_SQL` в `app/services/teacher_queue_service.py:60-64` уже фильтрует по `teacher_courses` + methodist bypass с stage39 (2026-03-01)** | no-op в коде; задача сводится к **покрытию тестами**, чтобы предотвратить регрессию |
| §9.1.8 audit constants enum | предлагается enum-класс | `audit_service.log_event` принимает `event_type: str` (без enum) | добавить **module-level константы** в `audit_service` для новых event_types; не ломать существующий API |
| §9.1.4 grade endpoint psevdokod использует `db.begin_nested()` | savepoint pattern | существующий `me.py` использует **flat транзакцию** + `db.commit()` в конце handler'а | следуем паттерну `me.py`: handler выполняет sequence в одной транзакции; на любом raise — `db.rollback()` через FastAPI exception flow. `begin_nested` нужен только если есть partial commit branches — здесь нет |
| `notifications` table | "расширяется существующая таблица" | Реально таблица — legacy `template_versions` (PK constraint `template_versions_pkey`, sequence `template_versions_id_seq`); count=0 | M8 миграция добавляет 5 nullable колонок поверх; legacy PK/FK имена сохраняем |

## 3. Frontend Routes vs API Endpoints

> Phase Y-4 backend поставляет только **API endpoints** (раздел §4). Frontend Routes (страницы SPW) — Stage S2 в SPW проекте, не в этом spec.

## 4. API Endpoints

### 4.1. Существующие — переиспользуем без изменений

| Endpoint | Файл | Использование в Y-4 |
|---|---|---|
| `POST /api/v1/teacher/reviews/claim-next` | `app/api/v1/teacher_reviews.py:28` | TG_LMS вызывает; фильтр teacher_courses **уже есть** |
| `POST /api/v1/teacher/reviews/{result_id}/release` | `app/api/v1/teacher_reviews.py:64` | Без изменений |
| `POST /api/v1/attempts/{id}/answers` | `app/api/v1/attempts.py` | SA_COM submit `{type:'SA_COM', response:{value, comment}}` — без изменений |

### 4.2. Новые endpoints Y-4

#### 4.2.1. `POST /api/v1/teacher/reviews/{result_id}/grade`

**Файл:** `app/api/v1/teacher_reviews.py` (расширение существующего роутера)
**Сервис:** `app/services/teacher_queue_service.py::grade_review` (новая функция рядом с claim/release)

- **Auth:** `Depends(get_current_user)`; `current_user.id == teacher_id` ИЛИ `current_user.is_service`
- **Body** (`schemas/teacher_next_modes.py::ReviewGradeRequest`):
  ```python
  {
    "teacher_id": int,
    "lock_token": str,
    "score": int,           # 0..max_score
    "is_correct": bool,
    "comment": Optional[str]  # length <= 4096
  }
  ```
- **Validation (Pydantic + service):**
  - `score >= 0` (Pydantic Field ge=0); проверка `score <= max_score` в service после SELECT
  - `comment: Optional[str]` с max_length=4096
  - `lock_token` non-empty
- **Логика (одна транзакция, без savepoint — flat по паттерну me.py):**
  1. `SELECT FROM task_results WHERE id=:result_id FOR UPDATE`
  2. Проверки: `not_found` → 404; `review_claimed_by != teacher_id` ИЛИ `review_claim_token != lock_token` → 409; `review_claim_expires_at IS NOT NULL AND review_claim_expires_at < now()` → 409 «истёк»; `is_correct IS NOT NULL` → 409 «уже оценено»
  3. `score > max_score` → 422
  4. `UPDATE task_results SET is_correct=:is_correct, score=:score, checked_at=now(), checked_by=:teacher_id, metrics = jsonb_set(coalesce(metrics, '{}'::jsonb), '{comment}', to_jsonb(:comment)), review_claimed_by=NULL, review_claim_token=NULL, review_claim_expires_at=NULL`
  5. `inbox_service.create_for_user(db, user_id=tr.user_id, kind='sa_com_graded', title='Преподаватель оценил вашу попытку', content=<rendered>, payload={task_id, attempt_id, score, max_score, is_correct, comment}, created_by=teacher_id)`
  6. `audit_service.log_event(db, 'teacher.review.graded', user_id=teacher_id, details={result_id, task_id, score, max_score, is_correct, comment_length})`
  7. `audit_service.log_event(db, 'student.notification.created', user_id=tr.user_id, details={notification_id, kind:'sa_com_graded'})`
  8. `await db.commit()`
  9. `BackgroundTasks.add_task(_send_email_after_commit, …)` — отправка email best-effort, **не валит ответ**

- **Response 200** (`ReviewGradeResponse`):
  ```python
  {
    "result_id": int,
    "task_id": int,
    "score": int,
    "max_score": int,
    "is_correct": bool,
    "comment": Optional[str],
    "notification_id": int
  }
  ```
- **Response 401:** auth required
- **Response 403:** `current_user.id != teacher_id AND not is_service`
- **Response 404:** `task_result not found`
- **Response 409:** lock mismatch / expired / already graded → `{"detail": "<reason>"}`
- **Response 422:** `score > max_score` → `{"detail": "score превышает max_score"}`

**Idempotency:** второй POST с тем же `lock_token` после первого grade → **409 «уже оценено»** (после grade `review_claim_token=NULL` + `is_correct IS NOT NULL`). Никакого attempt_uid.

**Concurrency:** `FOR UPDATE` сериализует двух teacher'ов; первый дойдёт до commit, второй увидит `is_correct IS NOT NULL` → 409.

#### 4.2.2. `GET /api/v1/me/notifications/unread-count`

**Файл:** `app/api/v1/me_notifications.py` (новый, отдельный от `me.py` для cohesion)
**Сервис:** `app/services/inbox_service.py::unread_count`

- **Auth:** `require_authenticated`
- **Query:** `SELECT count(*) FROM notifications WHERE user_id=:uid AND read_at IS NULL` (использует partial idx `idx_notifications_user_unread`)
- **Response 200:** `{"count": int, "last_check_at": datetime}` (last_check_at — серверное `now()` для дебага клиента)
- **Performance:** O(log n) через partial index

#### 4.2.3. `GET /api/v1/me/notifications`

**Файл:** `app/api/v1/me_notifications.py`
**Сервис:** `inbox_service.list_for_user`

- **Auth:** `require_authenticated`
- **Query params:** `limit` (default 50, max 100); `offset` (default 0); `unread_only` (default false)
- **Response 200:** `[NotificationRead]`:
  ```python
  {
    "id": int,
    "kind": str,
    "title": Optional[str],
    "content": str,
    "payload": Optional[dict],
    "created_at": datetime,   # из modified_at
    "read_at": Optional[datetime],
    "is_unread": bool
  }
  ```
- **Order:** `modified_at DESC`

#### 4.2.4. `POST /api/v1/me/notifications/{id}/read`

**Файл:** `app/api/v1/me_notifications.py`
**Сервис:** `inbox_service.mark_read`

- **Auth:** `require_authenticated`
- **Logic (atomic):** `UPDATE notifications SET read_at=now() WHERE id=:id AND user_id=:current.id AND read_at IS NULL RETURNING id`
- **Если rowcount=1:** записать audit `student.notification.read` и вернуть `{id, read_at}`
- **Если rowcount=0:** проверить существование (SELECT id, user_id WHERE id=:id):
  - не существует → 404
  - `user_id != current.id` → 403 (IDOR защита)
  - `read_at IS NOT NULL` → 200 idempotent (вернуть текущее `read_at`, без audit)
- **Response 200:** `{"id": int, "read_at": datetime}`
- **Response 403:** `{"detail": "Запись принадлежит другому пользователю"}`
- **Response 404:** `{"detail": "Запись не найдена"}`

#### 4.2.5. `GET /api/v1/me/history`

**Файл:** `app/api/v1/me.py` (расширение существующего me-роутера)
**Сервис:** `app/services/me_service.py::get_history`

- **Auth:** `require_authenticated`
- **Query params:** `limit` (default 50, max 200); `offset` (default 0); `filter` ∈ `{all, pending_review, passed, failed}` (default `all`)
- **SQL (single roundtrip с JOIN tasks/courses):**
  ```sql
  SELECT tr.id AS task_result_id, tr.task_id, t.external_uid AS task_external_uid,
         t.course_id, c.course_uid, c.title AS course_title,
         t.title AS task_title, t.task_content->>'type' AS type,
         CASE
           WHEN tr.is_correct IS NULL AND (tr.review_claim_expires_at IS NULL OR tr.review_claim_expires_at > now())
                THEN 'pending_review'
           WHEN tr.is_correct = TRUE THEN 'passed'
           WHEN tr.is_correct = FALSE THEN 'failed'
           ELSE 'pending_review'
         END AS status,
         tr.score, tr.max_score,
         tr.metrics->>'comment' AS comment,
         tr.received_at, tr.submitted_at, tr.checked_at
  FROM task_results tr
  JOIN tasks t ON t.id = tr.task_id
  LEFT JOIN courses c ON c.id = t.course_id
  WHERE tr.user_id = :uid
    AND (
      :filter = 'all'
      OR (:filter = 'pending_review' AND tr.is_correct IS NULL)
      OR (:filter = 'passed' AND tr.is_correct = TRUE)
      OR (:filter = 'failed' AND tr.is_correct = FALSE)
    )
  ORDER BY tr.received_at DESC
  LIMIT :limit OFFSET :offset
  ```
- **Performance:** использует существующий M7 `idx_task_results_user_received`
- **Response 200:** `[HistoryItem]` (см. поля выше)

#### 4.2.6. `GET /api/v1/teacher/reviews/pending-count`

**Файл:** `app/api/v1/teacher_reviews.py` (расширение)
**Сервис:** `teacher_queue_service.get_pending_count` (новая функция)

- **Auth:** `Depends(get_current_user)`; `current_user.id == teacher_id` ИЛИ `is_service`
- **Query params:** `teacher_id: int`
- **SQL (без захвата):**
  ```sql
  SELECT COUNT(*) AS count, MIN(tr.submitted_at) AS oldest_received_at
  FROM task_results tr
  JOIN tasks t ON t.id = tr.task_id
  WHERE tr.checked_at IS NULL
    AND (tr.review_claim_expires_at IS NULL OR tr.review_claim_expires_at < now())
    AND <REVIEW_ACL_SQL>  -- переиспользуем существующий фильтр teacher_courses + methodist
  ```
- **Response 200:** `{"count": int, "oldest_received_at": Optional[datetime]}`
- **Response 403:** `current_user.id != teacher_id AND not is_service`

> **Замечание:** `submitted_at` (а не `received_at`) — потому что в claim-next ORDER BY использует `submitted_at` (FIFO по времени отправки ученика). Synchronization для consistency с очередью.

## 5. DB Schema — миграция M8

### 5.1. M8 — расширение `notifications` под inbox-семантику

**Файл:** `app/db/migrations/versions/20260430_010000_M8_notifications_inbox.py`
**Revision:** `20260430_010000_m8_inbox` (≤32 символов — ограничение alembic_version.version_num)
**Down revision:** `20260429_010000_m7_streak_idx`

```python
def upgrade():
    op.add_column('notifications', sa.Column('user_id', sa.Integer(), nullable=True))
    op.add_column('notifications', sa.Column('kind', sa.String(64), nullable=True))
    op.add_column('notifications', sa.Column('title', sa.String(255), nullable=True))
    op.add_column('notifications', sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('notifications', sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key(
        'fk_notifications_user_id', 'notifications', 'users',
        ['user_id'], ['id'], ondelete='CASCADE',
    )

    op.create_index(
        'idx_notifications_user_unread', 'notifications',
        ['user_id', sa.text('modified_at DESC')],
        postgresql_where=sa.text('user_id IS NOT NULL AND read_at IS NULL'),
    )
    op.create_index(
        'idx_notifications_user_all', 'notifications',
        ['user_id', sa.text('modified_at DESC')],
        postgresql_where=sa.text('user_id IS NOT NULL'),
    )

def downgrade():
    op.drop_index('idx_notifications_user_all', table_name='notifications')
    op.drop_index('idx_notifications_user_unread', table_name='notifications')
    op.drop_constraint('fk_notifications_user_id', 'notifications', type_='foreignkey')
    op.drop_column('notifications', 'read_at')
    op.drop_column('notifications', 'payload')
    op.drop_column('notifications', 'title')
    op.drop_column('notifications', 'kind')
    op.drop_column('notifications', 'user_id')
```

**Безопасность:** `notifications` count=0 на 2026-04-30 (verified MCP) — М8 не задевает данные.

**Семантика после M8:**
- `id`, `content` (NOT NULL), `modified_by`, `modified_at` (NOT NULL) — legacy template-поля; используются для inbox-записей: `content` = готовый текст, `modified_by` = teacher_id (создатель), `modified_at` = время создания.
- `user_id`, `kind`, `title`, `payload`, `read_at` — новые inbox-поля; nullable, чтобы legacy template-записи (если когда-нибудь появятся) совместимы.

## 6. Сервисы

### 6.1. `app/services/inbox_service.py` (новый)

```python
class InboxService:
    async def create_for_user(
        self, db, *, user_id, kind, title, content, payload, created_by,
    ) -> Notifications: ...

    async def list_for_user(
        self, db, *, user_id, limit, offset, unread_only,
    ) -> list[Notifications]: ...

    async def unread_count(self, db, user_id: int) -> int: ...

    async def mark_read(self, db, notification_id: int, user_id: int) -> Optional[datetime]:
        """Returns read_at if updated; None if rowcount=0 (already read or not own)."""
```

### 6.2. `app/services/notification_email_service.py` (новый)

Wrapper над Resend API, паттерн из `magic_link_service.send_magic_link_email`.

```python
async def send_sa_com_graded(
    *, recipient_email: str, task_title: str, score: int, max_score: int,
    comment: Optional[str], settings: Settings,
) -> bool:
    """Send via Resend; on transport error → log + audit 'email.failed' (логируется
    в caller с db handle), return False; never raises."""
```

Шаблон письма (RU plain text с минимальным HTML):
```
Здравствуйте,

Преподаватель оценил вашу попытку по задаче «{task_title}»:
Балл: {score} из {max_score}

{comment if comment else ''}

Открыть историю: {public_base_url}/me/history
```

`public_base_url` берётся из `Settings` (а не hardcode).

### 6.3. Расширение `audit_service.py`

Добавить module-level константы:
```python
TEACHER_REVIEW_GRADED = "teacher.review.graded"
STUDENT_NOTIFICATION_CREATED = "student.notification.created"
STUDENT_NOTIFICATION_READ = "student.notification.read"
EMAIL_FAILED = "email.failed"
```

Сами call sites используют эти константы (не сырые строки) — для grep-friendliness и предотвращения опечаток.

### 6.4. Расширение `teacher_queue_service.py`

- `grade_review(db, *, result_id, teacher_id, lock_token, score, is_correct, comment) -> dict` — атомарный grade через `FOR UPDATE`; raises `GradeConflictError` (новый exception) на 409 ветви; raises `GradeNotFoundError` на 404
- `get_pending_count(db, teacher_id) -> tuple[int, Optional[datetime]]` — без захвата

## 7. Pydantic schemas

### 7.1. Расширение `app/schemas/teacher_next_modes.py`

```python
class ReviewGradeRequest(BaseModel):
    teacher_id: int
    lock_token: str = Field(min_length=1)
    score: int = Field(ge=0)
    is_correct: bool
    comment: Optional[str] = Field(default=None, max_length=4096)

class ReviewGradeResponse(BaseModel):
    result_id: int
    task_id: int
    score: int
    max_score: int
    is_correct: bool
    comment: Optional[str]
    notification_id: int

class PendingCountResponse(BaseModel):
    count: int
    oldest_received_at: Optional[datetime]
```

### 7.2. `app/schemas/me_notifications.py` (новый)

```python
class NotificationRead(BaseModel):
    id: int
    kind: Optional[str]
    title: Optional[str]
    content: str
    payload: Optional[dict]
    created_at: datetime  # alias modified_at
    read_at: Optional[datetime]
    is_unread: bool

class UnreadCountResponse(BaseModel):
    count: int
    last_check_at: datetime

class MarkReadResponse(BaseModel):
    id: int
    read_at: datetime
```

### 7.3. `app/schemas/me.py` — расширение

```python
class HistoryItem(BaseModel):
    task_result_id: int
    task_id: int
    task_external_uid: Optional[str]
    course_id: Optional[int]
    course_uid: Optional[str]
    course_title: Optional[str]
    task_title: Optional[str]
    type: Optional[str]
    status: Literal["pending_review", "passed", "failed"]
    score: Optional[int]
    max_score: Optional[int]
    comment: Optional[str]
    received_at: datetime
    submitted_at: datetime
    checked_at: Optional[datetime]
```

## 8. Расширение модели `Notifications`

После M8 SQLAlchemy model расширяется:
```python
class Notifications(Base):
    # existing
    id, content, modified_by, modified_at
    # new (Y-4)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    kind: Mapped[Optional[str]] = mapped_column(String(64))
    title: Mapped[Optional[str]] = mapped_column(String(255))
    payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
```

Новый FK constraint name = `fk_notifications_user_id` (уникален, не конфликтует с legacy `template_versions_modified_by_fkey`).

Backref на Users (если нужен) — добавить `inbox_messages` (отличается от legacy `notifications` коллекции, чтобы не ломать существующее поведение); в Y-4 backend backref не используется, можно отложить.

## 9. Регистрация роутера

В `app/main.py` (или где регистрируются роутеры) добавить:
```python
from app.api.v1 import me_notifications
app.include_router(me_notifications.router, prefix="/api/v1")
```

Существующий `me.router` (prefix='/me', tags=['me']) расширяется handler'ом `/history`.

## 10. Тесты

### 10.1. Новые test файлы

| Файл | Что покрывает |
|---|---|
| `tests/test_m8_notifications_migration.py` | upgrade → downgrade → upgrade roundtrip; legacy записи (count=0); новые поля nullable |
| `tests/test_inbox_service.py` | create_for_user / list / unread_count / mark_read; IDOR (chu user не видит чужие); pagination |
| `tests/test_grade_endpoint.py` | happy path; 401/403/404/409 ветви; lock_token expiration; score > max_score → 422; повторный grade → 409 |
| `tests/test_me_notifications_endpoints.py` | unread-count; list (filter unread_only); mark-read idempotent; mark-read для чужой → 403 |
| `tests/test_me_history.py` | все 4 фильтра; pagination; status=pending_review|passed|failed |
| `tests/test_pending_count.py` | count корректен; teacher_courses фильтр; RBAC (`is_service`, чужой teacher_id → 403) |
| `tests/test_claim_next_teacher_courses_filter.py` | покрытие существующего фильтра (no-op коды): teacher без teacher_courses → empty=true; teacher с курсом X видит только tr своего курса |
| `tests/test_notification_email_service.py` | mock Resend → success; transport error → return False, no raise |

### 10.2. Команды проверки

```bash
cd D:\Work\LMS
alembic upgrade head
alembic downgrade -1
alembic upgrade head
pytest tests/test_m8_notifications_migration.py tests/test_inbox_service.py tests/test_grade_endpoint.py tests/test_me_notifications_endpoints.py tests/test_me_history.py tests/test_pending_count.py tests/test_claim_next_teacher_courses_filter.py tests/test_notification_email_service.py -v
pytest tests/ -m "not slow" -v
bandit -r app/ -ll
```

## 11. Acceptance criteria S1

- [ ] M8 миграция: upgrade + downgrade + upgrade зелёные; existing data (count=0) не повреждена
- [ ] grade endpoint: happy path; 401 без auth; 403 чужой teacher_id; 404 несуществующий result; 409 lock mismatch; 409 «уже оценено»; 422 score > max_score
- [ ] inbox endpoints: unread-count корректен; list pagination; mark-read idempotent для уже прочитанной; mark-read для чужой → 403
- [ ] /me/history: 4 фильтра дают корректный результат; pagination; правильный `status`-fallback
- [ ] pending-count: corretct count; RBAC; teacher без teacher_courses → 0
- [ ] claim-next teacher_courses фильтр (no-op + tests): teacher_id=1 без teacher_courses → empty=true; methodist bypass работает
- [ ] Audit events записываются: `teacher.review.graded`, `student.notification.created`, `student.notification.read`, `email.failed` (если случилось)
- [ ] Email через Resend best-effort: при `RESEND_API_KEY=""` (dev) — лог; при transport error — `audit email.failed` + grade не откатывается
- [ ] Cross-project memory обновлён same-commit с merge (per ERRORS #1, #2): `lms-api.md`, `lms-db-schema.md`, `CHANGELOG.md`, `STATE.md`
- [ ] Bandit без новых HIGH issues
- [ ] Existing tests `pytest tests/` остались зелёные (Y-1, Y-3 регрессия)

## 12. Riski + откат

| Риск | Митигация |
|---|---|
| **`Notifications` model зависит от backref** | Проверка: existing notifications count=0; новый relationship добавлен с `Optional` — не ломает существующий `Users.notifications` backref если он есть |
| **race grade vs claim expiry** | `FOR UPDATE` сериализует; первый видит активный lock, второй — `NULL` после grade |
| **email upal — grade откатился** | НЕ должно быть: email через `BackgroundTasks.add_task` после `db.commit()`; если внутри grade-логики INSERT inbox упал — flat транзакция откатится целиком, ни UPDATE task_results, ни inbox не закоммитятся |
| **legacy template-записи появятся в /me/notifications** | Список фильтрует `WHERE user_id = :current.id`; legacy `user_id IS NULL` — не попадёт |

**Rollback:** `alembic downgrade -1` (M8 → M7); revert commits; existing endpoints не задеваются.

## 13. Skill-routing (по CB §22)

| Под-задача | Главный | Ревью |
|---|---|---|
| M8 миграция | `/executor-pro` | `/db-check` + `/pr-review` + `/review-gate` |
| InboxService | `/executor-pro` | `/pr-review` |
| NotificationEmailService | `/executor-pro` | `/pr-review` |
| grade endpoint | `/executor-pro` | `/techlead-code-reviewer` |
| /me/notifications/* | `/executor-pro` | `/techlead-code-reviewer` (IDOR) |
| /me/history | `/executor-pro` | `/pr-review` |
| pending-count | `/executor-pro` | `/pr-review` |
| audit constants | `/executor-lite` | `/pr-review` |
| Тесты | `/qa-fix` | `/review-gate` |
| Backsync | `/executor-pro` | `/context-auditor` |
| Финальный merge | — | `/review-gate` (12 измерений) + `/context-auditor` |

Cross-cutting: `/encoding-guard` (RU-тексты), `/db-check` (pre+post M8).

## 14. Boundary contracts (для S2/S3 чатов)

- **SA_COM submit:** `{type:'SA_COM', response:{value:<python_code>, comment:<optional>}}` (передаётся через `/attempts/{id}/answers`, не меняется в Y-4)
- **Grade boundary teacher → LMS:** `{teacher_id, lock_token, score:int, is_correct:bool, comment:str|null}`
- **Inbox boundary LMS → SPW:** `content` — plain text, `payload` — JSON; SPW рендерит content + опциональный link на `task_id` из payload
- **Email boundary LMS → Resend:** plain+HTML; user-input не в теме; ссылки только через `settings.public_base_url`
- **Poller boundary TG_LMS → LMS:** GET `/teacher/reviews/pending-count?teacher_id=N`; idempotent

## 15. Связанные документы

- [CB authority Y-4 v1](../../../ContentBackbone/docs/tech-specs/tech-spec-Y4-sa-com-teacher-queue-v1.md)
- [CB ADR-0021 user auto-registration](../../../ContentBackbone/docs/adr/0021-user-auto-registration-unified-flow.md)
- [LMS Y-3 backend spec](2026-04-29-tech-spec-Y3-learning-loop-backend.md) — Y-3 baseline
- [cross-project lms-api.md mirror](../../../ContentBackbone/docs/cross-project/contracts/lms-api.md)
- [cross-project lms-db-schema.md mirror](../../../ContentBackbone/docs/cross-project/contracts/lms-db-schema.md)
- [docs/design/teacher-queue-states.md](../../../ContentBackbone/docs/design/teacher-queue-states.md)
- [api-contract-rules.md](~/.claude/skills/claude-booster/references/api-contract-rules.md)

## 16. Operator handoff (для S5 smoke)

Решения 2026-04-30 (фиксированы):
- §23.1 = **C** (28 legacy pending — оставляем как есть; нагрузочный smoke)
- §23.4 = **A.1 ОТКЛОНЁН → NO-OP** (см. §16.1; seed невыполним, methodist-bypass уже работает)
- §23.3 = **A** (агент выполняет автомат-часть; оператор проверяет письмо вручную)

### 16.1. Errata §23.4 — seed teacher_courses избыточен (2026-04-30)

При попытке выполнить вариант A.1 обнаружены 4 факта, делающие seed невозможным **и** ненужным:

1. **`course_id=10` не root-курс.** Цепочка иерархии в БД: `10 → parent 7 → parent 1 (PY)`. DB-триггер `teacher_courses` запрещает линковку non-root курсов: `Course 10 has parents. Teachers and students can only be linked to courses without parents.` → `INSERT INTO teacher_courses VALUES (2, 10)` упадёт.

2. **Все pending SA_COM на non-root курсах.** Запрос задач SA_COM с `course_id NOT IN (SELECT course_id FROM course_parents)` вернул пустое множество. Ни одна из 380 SA_COM-задач не лежит на корневом курсе. Все 11 pending — на course_id=10.

3. **`REVIEW_ACL_SQL` не рекурсивен по `course_parents`.** В `app/services/teacher_queue_service.py:60-64` точное равенство `tc.course_id = t.course_id`. Даже если бы привязка Виктора к root существовала — фильтр не нашёл бы заявки в потомках 7/10.

4. **Виктор уже имеет роль `methodist`** (verified MCP 2026-04-30) → `REVIEW_ACL_SQL` содержит `OR EXISTS user_roles ... role.name='methodist'` → bypass активен → 11 pending видны без seed'а.

**Решение для S5 smoke:** seed не выполняем. Виктор как methodist видит весь pending pool.

**Production-задача (вне scope Y-4):** расширить `REVIEW_ACL_SQL` рекурсивно через `course_parents` (WITH RECURSIVE), чтобы teacher на root-курсе автоматически получал доступ к потомкам. Это позволит ограничивать teacher'ов отдельными ветками без выдачи methodist-роли (которая широка). Открыть отдельный change-plan: **«Y-4.1 follow-up: REVIEW_ACL_SQL hierarchical scope»**.

**Resolution:** Y-4.1 follow-up MERGED 2026-04-30. См. [Y-4.1 spec](2026-04-30-tech-spec-Y4.1-review-acl-hierarchical.md). Helper `teacher_course_acl()` + `TEACHER_COURSE_HIERARCHY_ACL_TEMPLATE` в `teacher_queue_service.py` устраняют gap; `REVIEW_ACL_SQL` + `HELP_REQUESTS_ACL_SQL` теперь используют WITH RECURSIVE через `course_parents`. Live verify: 11 pending видны Виктору без methodist-bypass.

### 16.2. Acceptance gate для S5 (обновлён)

```sql
-- methodist-bypass: должен возвращать ≥ 11 (исторический backlog).
SELECT COUNT(*)
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id
WHERE tr.is_correct IS NULL
  AND tr.checked_by IS NULL
  AND t.task_content->>'type' = 'SA_COM'
  AND EXISTS (
    SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
    WHERE ur.user_id = 2 AND r.name = 'methodist'
  );
-- Ожидаемое: >= 11
```

## 17. Phase ordering inside S1

```
1. M8 миграция (db-check pre+post, roundtrip)
2. inbox_service + notification_email_service + audit constants (без HTTP)
3. grade endpoint + /me/notifications/* + /me/history + pending-count
4. Тесты unit + integration
5. encoding-guard + techlead-code-reviewer + pr-review
6. Cross-project memory backsync (lms-api.md, lms-db-schema.md, CHANGELOG, STATE) — SAME COMMIT
7. review-gate (12 измерений) + context-auditor
8. Commit + push (single atomic PR)
```

---

**Готовность:** spec написан до реализации; backsync drift предотвращён.
