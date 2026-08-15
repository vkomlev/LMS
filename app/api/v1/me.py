"""Эндпоинты /me — профиль, identities, прогресс, last-position, streak,
история (Phase Y-1 + Y-3 + Y-4).

Phase Y-3 добавляет:
- GET  /me/identities         — список identity_link с masked values
- GET  /me/courses            — активные курсы + progress (single roundtrip CTE)
- GET  /me/last-position      — последняя активность + резолв next-item
- GET  /me/streak             — streak дней подряд в Europe/Moscow
- POST /me/identity/{kind}/link — привязка новой identity к current user

Phase Y-4 добавляет:
- GET  /me/history            — список последних попыток + фильтры

См. tech-spec Y-3 §5.1-5.4, §5.6, §7.6, §7.7;
    tech-spec Y-4 (LMS-side backend) §4.2.5.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_async_db,
    get_current_user,
    require_authenticated,
    resolve_student_owner,
)
from app.auth.current_user import CurrentUser
from app.services import entitlements_service
from app.services.courses_acl_service import assert_course_access
from app.core.config import Settings
from app.schemas.auth import (
    IdentityLinkEmailRequest,
    IdentityLinkResponse,
    IdentityLinkTgRequest,
    IdentityLinkVkRequest,
    IdentityLinkedItem,
)
from app.schemas.learning_guest import (
    AttributeGuestRequest,
    AttributeGuestResponse,
)
from app.schemas.me import (
    BrowserTimezoneRequest,
    BrowserTimezoneResponse,
    CourseProgress,
    CourseWithProgressRead,
    HistoryItem,
    IdentityRead,
    LastPositionRead,
    MeResponse,
    MeUpdateRequest,
    MyEntitlements,
    StreakRead,
    SyllabusStatesResponse,
)
from app.schemas.retention import RetentionRead
from app.schemas.task_history import TaskHistoryResponse
from app.schemas.users import UserRead
from app.services.parent_student_links_service import ParentStudentLinksService
from app.services.student_teacher_links_service import StudentTeacherLinksService
from app.services import (
    lesson_calendar_service,
    me_service,
    retention_service,
    roles_service,
    task_history_service,
)
from app.services.tasks_acl_service import assert_task_access
from app.services.audit_service import log_event
from app.services.full_name_validator import validate_full_name
from app.services.auth import (
    guest_attribution_service,
    identity_link_service,
    link_token_service,
    magic_link_service,
    tg_init_service,
    vk_oauth_service,
)
from app.services.auth.exceptions import IdentityConflictError
from app.services.auth.guest_attribution_service import (
    GuestAttributionConflictError,
)
from app.services.fernet_service import encrypt_token
from app.services.rate_limit_service import get_redis, is_rate_limited
from app.services.user_merge_service import check_and_merge_duplicate_on_registration

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/me", tags=["me"])
_settings = Settings()
_student_teacher_links_service = StudentTeacherLinksService()
_parent_student_links_service = ParentStudentLinksService()


# ── GET /me ─────────────────────────────────────────────────────────────────

@router.get("", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> MeResponse:
    """Вернуть профиль аутентифицированного пользователя.

    tsk-223: `full_name` подгружается из `users.full_name` (БД), а не из
    `CurrentUser` — dataclass CurrentUser имя не несёт.
    tsk-298: `roles` — имена ролей из user_roles (M2M); SPW гейтит по ним
    teacher-зону портала.
    tsk-427: доп. поля профиля (category/school_grade/city/timezone) грузятся
    вместе с full_name одним запросом (`me_service.get_profile`).
    """
    profile = await me_service.get_profile(db, current_user.id)
    roles = await roles_service.get_user_role_names(db, current_user.id)
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        tg_id=current_user.tg_id,
        is_service=current_user.is_service,
        full_name=profile["full_name"] if profile else None,
        category=profile["category"] if profile else None,
        school_grade=profile["school_grade"] if profile else None,
        city=profile["city"] if profile else None,
        timezone=profile["timezone"] if profile else None,
        timezone_source=profile["timezone_source"] if profile else None,
        roles=roles,
    )


# ── PATCH /me — self-service обновление профиля (tsk-223 + tsk-427) ──────────

@router.patch("", response_model=MeResponse)
async def update_me(
    body: MeUpdateRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> MeResponse:
    """Обновить собственный профиль — partial update, каждое поле независимо.

    ФИО (tsk-223, `full_name`): формат проверяется единым серверным правилом
    `validate_full_name` («Фамилия Имя [Отчество]» русскими буквами). При
    нарушении — 422 с русским сообщением. Пишет audit-событие
    `user.profile.full_name_updated` перед commit и запускает дедуп-хук
    (как раньше). Поле необязательно — если не передано, ФИО не трогается.

    Доп. поля (tsk-427, `category`/`school_grade`/`city`/`timezone`):
    формат (enum/диапазон/IANA id) проверен в самой Pydantic-схеме;
    кросс-валидация «класс только у школьника» — в `me_service.
    update_profile_extra` (нужен доступ к текущей category в БД, если она не
    передана этим же запросом) — при нарушении тоже 422.

    401 (без auth) и 403 (сервисный токен) даёт `require_authenticated`.
    """
    if body.full_name is not None:
        try:
            normalized = validate_full_name(body.full_name)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

        previous_full_name = await me_service.get_full_name(db, current_user.id)
        await me_service.update_full_name(db, current_user.id, normalized)

        ip = request.client.host if request.client else "unknown"
        await log_event(
            db,
            "user.profile.full_name_updated",
            user_id=current_user.id,
            ip=ip,
            details={"full_name": normalized},
        )
        if previous_full_name != normalized:
            # tsk-464: full_name часто становится "настоящим" не в момент
            # регистрации (magic-link создаёт юзера с дефолтным именем, реальное
            # ФИО приходит позже через этот эндпоинт) — дедуп-хук из tsk-455
            # срабатывает только на регистрации и пропускает такие случаи.
            # Только при РЕАЛЬНОМ изменении имени — эндпоинт без rate-limit,
            # полный скан кандидатов на каждый no-op PATCH был бы лишней
            # нагрузкой. Soft-fail — не должно ломать обновление профиля.
            try:
                await check_and_merge_duplicate_on_registration(db, new_user_id=current_user.id)
            except Exception:
                logger.exception(
                    "tsk-464 check_and_merge_duplicate_on_registration failed user_id=%s",
                    current_user.id,
                )

    if any(
        v is not None
        for v in (body.category, body.school_grade, body.city, body.timezone)
    ):
        try:
            await me_service.update_profile_extra(
                db,
                current_user.id,
                category=body.category,
                school_grade=body.school_grade,
                city=body.city,
                timezone_value=body.timezone,
            )
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    await db.commit()

    profile = await me_service.get_profile(db, current_user.id)
    roles = await roles_service.get_user_role_names(db, current_user.id)
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        tg_id=current_user.tg_id,
        is_service=current_user.is_service,
        full_name=profile["full_name"] if profile else None,
        category=profile["category"] if profile else None,
        school_grade=profile["school_grade"] if profile else None,
        city=profile["city"] if profile else None,
        timezone=profile["timezone"] if profile else None,
        timezone_source=profile["timezone_source"] if profile else None,
        roles=roles,
    )


# ── PUT /me/timezone/auto — системный пояс браузера (tsk-588) ───────────────

@router.put("/timezone/auto", response_model=BrowserTimezoneResponse)
async def set_browser_timezone(
    body: BrowserTimezoneRequest,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> BrowserTimezoneResponse:
    """Принять системный пояс устройства и записать его, если он не спорит с человеком.

    tsk-588: пояс был у 3 из 52 активных пользователей, потому что его надо
    было вписывать руками — а двое учеников из-за этого пришли на занятие
    мимо на своё смещение от Москвы. Клиент присылает сюда
    `Intl.DateTimeFormat().resolvedOptions().timeZone` при входе.

    Ручной выбор сильнее (решение оператора 2026-08-08): если пояс вписан
    человеком (`timezone_source = 'manual'`), запрос ничего не меняет и
    возвращает `applied=false` с прежним значением. Эндпоинт идемпотентен —
    повторный вызов с тем же поясом записи не делает.

    401 (без auth) и 403 (сервисный токен) даёт `require_authenticated`.
    """
    try:
        timezone_value, applied = await me_service.apply_browser_timezone(
            db, current_user.id, body.timezone
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    await db.commit()

    profile = await me_service.get_profile(db, current_user.id)
    return BrowserTimezoneResponse(
        timezone=timezone_value,
        source=profile["timezone_source"] if profile else None,
        applied=applied,
    )


# ── GET /me/identities ──────────────────────────────────────────────────────

@router.get("/identities", response_model=list[IdentityRead])
async def list_identities(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> list[IdentityRead]:
    """Список identity_link текущего пользователя с masked values (см. §5.1)."""
    items = await me_service.get_identities(db, current_user.id)
    return [IdentityRead(**item) for item in items]


# ── GET /me/courses ─────────────────────────────────────────────────────────

@router.get("/courses", response_model=list[CourseWithProgressRead])
async def list_courses(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> list[CourseWithProgressRead]:
    """Активные курсы пользователя + progress (см. §5.2). Single SQL roundtrip."""
    items = await me_service.get_courses_with_progress(db, current_user.id)
    return [
        CourseWithProgressRead(
            course_id=it["course_id"],
            course_uid=it["course_uid"],
            title=it["title"],
            order_number=it["order_number"],
            progress=CourseProgress(**it["progress"]),
            last_active_at=it["last_active_at"],
            is_completed=it["is_completed"],
        )
        for it in items
    ]


# ── GET /me/teachers ─────────────────────────────────────────────────────────

@router.get("/teachers", response_model=list[UserRead])
async def list_my_teachers(
    at: datetime | None = Query(
        default=None,
        description="Если задано — сузить список до преподавателей, чей слот "
                    "покрывает это время (tsk-443); пусто — вернуть всех привязанных",
    ),
    duration_minutes: int = Query(default=60, ge=1, description="Длительность занятия, минут"),
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> list[UserRead]:
    """Преподаватели, привязанные к текущему пользователю (`student_teacher_links`).

    tsk-436: cookie-авторизованная обёртка над `GET /users/{id}/teachers`
    (тот эндпоинт защищён сервисным API-ключом для ТГ-ботов — браузеру
    его не вызвать). Нужна ученику без закреплённого слота («плавающий»,
    напр. tsk-021), чтобы выбрать преподавателя для ad-hoc записи
    (`POST /lesson-occurrences/ad-hoc`) без предшествующего occurrence.

    tsk-443: с `at` — сужает список до преподавателей, чей активный слот
    покрывает это время (реальный кейс: ученик привязан сразу к 4
    преподавателям через `student_teacher_links`, но на конкретный час
    слот есть только у одного — спрашивать выбор преподавателя не нужно).
    Пересечение со списком привязанных (не произвольные преподаватели школы,
    даже если их слот покрывает время) — ученику нельзя предлагать записаться
    к тому, с кем у него формально нет связи. Ничего не пересеклось (совпало
    время вне слотов, или слот есть, но его преподаватель не в списке
    привязанных) — откат на полный список привязанных (обычный ad-hoc).
    """
    teachers = await _student_teacher_links_service.list_teachers(db, current_user.id)
    if at is not None:
        covering = await lesson_calendar_service.list_teachers_for_time(
            db, scheduled_at=at, duration_minutes=duration_minutes,
        )
        covering_ids = {t.id for t in covering}
        if covering_ids:
            restricted = [t for t in teachers if t.id in covering_ids]
            if restricted:
                return [UserRead.model_validate(t) for t in restricted]
    return [UserRead.model_validate(t) for t in teachers]


# ── GET /me/children (tsk-478, кабинет родителя) ────────────────────────────

@router.get(
    "/children",
    response_model=list[UserRead],
    summary="Ученики, привязанные к текущему родителю",
    description=(
        "Родитель узнаёт ID своего(-их) ученика(ов), чтобы запросить его "
        "дашборд (`GET /students/{student_id}/dashboard`). Пусто, если "
        "связок нет — не 403: роль `parent` без связки не ошибка входа, "
        "а состояние 'пока не привязан'."
    ),
)
async def list_my_children(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> list[UserRead]:
    children = await _parent_student_links_service.list_children(db, current_user.id)
    return [UserRead.model_validate(c) for c in children]


# ── GET /me/courses/{course_id}/syllabus-states (Phase Y-6.2) ───────────────

@router.get(
    "/courses/{course_id}/syllabus-states",
    response_model=SyllabusStatesResponse,
    summary="Снимок состояний задач+материалов поддерева курса (Phase Y-6.2)",
    responses={
        200: {"description": "Состояния всех items + blocked_courses"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "Student не зачислен в курс (или ancestor)"},
    },
)
async def get_course_syllabus_states(
    response: Response,
    course_id: int = Path(..., description="ID корневого course (любой узел дерева)"),
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> SyllabusStatesResponse:
    """Снимок состояний syllabus-дерева для рендера на SPW (Phase Y-6.2).

    SPW рендерит:
    - per-task chip (passed/pending_review/failed/blocked_limit/in_progress/not_started)
    - per-material chip (completed/not_started)
    - 🔒-маркер на subcourse-узле, если course_id ∈ blocked_courses

    ACL: тот же helper `assert_course_access` (Y-5.2): service-key /
    teacher / methodist / admin — bypass; student — только дерево
    `user_courses + course_parents`.

    Cache: `no-store` (tsk-214б). Раньше стоял `private, max-age=15` в расчёте на
    то, что SPW invalidate'ит queryKey после submit — но HTTP-кэш браузера и кэш
    TanStack Query это РАЗНЫЕ слои: `invalidateQueries` заново вызывает `fetch()`,
    а браузер отдаёт ответ из HTTP-кэша по `max-age` без обращения к серверу. Из-за
    этого счётчик прогресса/попыток отставал на ~15-25 сек и на одну попытку после
    ответа. `no-store` заставляет refetch всегда идти на сервер; защита от лишних
    запросов остаётся на клиентском `staleTime` TanStack, который уступает invalidate.
    """
    await assert_course_access(db, current_user=current_user, course_id=course_id)
    payload = await me_service.get_syllabus_states(
        db, user_id=current_user.id, root_course_id=course_id
    )
    response.headers["Cache-Control"] = "no-store"
    return SyllabusStatesResponse(**payload)


# ── GET /me/last-position ───────────────────────────────────────────────────

@router.get("/last-position", response_model=LastPositionRead | None)
async def get_last_position(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> LastPositionRead | None:
    """Последняя активность пользователя + next-item resolve (см. §5.3)."""
    pos = await me_service.get_last_position(db, current_user.id)
    if pos is None:
        return None
    return LastPositionRead(**pos)


# ── GET /me/streak ──────────────────────────────────────────────────────────

@router.get("/streak", response_model=StreakRead)
async def get_streak(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> StreakRead:
    """Streak дней подряд в Europe/Moscow (см. §5.4)."""
    s = await me_service.get_streak(db, current_user.id)
    return StreakRead(**s)


# ── GET /me/retention (tsk-032) ─────────────────────────────────────────────

@router.get("/retention", response_model=RetentionRead)
async def get_retention(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> RetentionRead:
    """Удержание между занятиями: недельная серия + личные вехи (tsk-032).

    Отличие от `/me/streak`: та серия — суточная и считает ЛЮБУЮ сдачу, в том
    числе сделанную на самом уроке, поэтому удержание между занятиями ею не
    измеряется. Здесь время урока вычтено, а единица серии — активная НЕДЕЛЯ
    (обоснование порога — на реальных данных, см. docstring
    `app/services/retention_service.py`)."""
    data = await retention_service.get_retention(db, student_id=current_user.id)
    return RetentionRead(**data)


# ── GET /me/history (Phase Y-4) ─────────────────────────────────────────────

@router.get("/history", response_model=list[HistoryItem])
async def get_history(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
    limit: int = Query(50, ge=1, le=200, description="Лимит (max 200)"),
    offset: int = Query(0, ge=0, description="Смещение"),
    filter_: Literal["all", "pending_review", "passed", "failed"] = Query(
        "all", alias="filter", description="Фильтр статусу"
    ),
) -> list[HistoryItem]:
    """История попыток ученика с фильтрами (Phase Y-4 backend §4.2.5)."""
    rows = await me_service.get_history(
        db, current_user.id, filter_=filter_, limit=limit, offset=offset
    )
    return [HistoryItem(**row) for row in rows]


# ── GET /me/tasks/{task_id}/history (tsk-349) ───────────────────────────────

@router.get(
    "/tasks/{task_id}/history",
    response_model=TaskHistoryResponse,
    summary="Моя история по конкретному заданию",
)
async def get_my_task_history(
    task_id: int = Path(..., ge=1, description="ID задания"),
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> TaskHistoryResponse:
    """История ученика по одному заданию: свои попытки, комментарии преподавателя,
    свои обращения за помощью, подсказки.

    Правило проверки и эталонный ответ ученику НЕ отдаются — ``solution`` всегда
    ``null`` (эталон только преподавателю, tsk-349/tsk-254). Доступ ограничен
    задачами в дереве курсов ученика (`assert_task_access`), чтобы условие чужого
    задания нельзя было прочитать перебором ``task_id``.
    """
    course_id = await task_history_service.course_of_task(db, task_id)
    if course_id is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Задание {task_id} не найдено"
        )
    await assert_task_access(db, current_user=current_user, task_course_id=course_id)

    data = await task_history_service.build_task_history(
        db, user_id=current_user.id, task_id=task_id, include_solution=False
    )
    if data is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Задание {task_id} не найдено"
        )
    return TaskHistoryResponse(**data)


# ── POST /me/identity/{kind}/link ───────────────────────────────────────────

def _conflict_to_http(e: IdentityConflictError) -> HTTPException:
    """Маппинг IdentityConflictError → HTTP 409 c унифицированным body."""
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "error": "identity_conflict",
            "conflict_kind": e.conflict_kind,
            "existing_identity_kinds": e.existing_kinds,
            "message": (
                "Эта identity уже привязана к другому аккаунту. "
                "Войдите через ту identity, чтобы управлять привязкой."
            ),
        },
    )


def _link_token_invalid_http() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "link_token недействителен, истёк или уже использован",
    )


async def _enforce_link_identity_rate_limit(current_user_id: int) -> None:
    """Rate-limit 30/мин per user на /me/identity/{kind}/link (Y-3.2 defence-in-depth).

    Защищает от token-guessing abuse: даже с действительным session-токеном
    атакующий не может бесконечно пробовать чужие link_token. Fail-open при
    недоступности Redis (через rate_limit_service — стандартный паттерн).
    """
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(
        redis, f"link_identity:user:{current_user_id}", max_requests=30, window_seconds=60
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много попыток привязки identity. Попробуйте через минуту.",
        )


async def _consume_link_token_for_user(
    db: AsyncSession,
    raw_token: str,
    expected_kind: Literal["email", "tg", "vk"],
    current_user_id: int,
    ip: str,
) -> None:
    """Atomic consume + валидация owner_user/kind.

    Raise HTTPException 401 на любую ошибку (invalid/expired/consumed или mismatch).
    Raise HTTPException 503 если link_token storage недоступен в production.

    На mismatch (wrong user_id / wrong kind) пишет audit_event
    `auth.link_token.consume_mismatch` для forensics (Y-3.1 / techlead S3-7).
    """
    redis = get_redis(_settings.redis_url)
    try:
        payload = await link_token_service.consume(redis, raw_token)
    except link_token_service.LinkTokenError:
        raise _link_token_invalid_http()
    except link_token_service.LinkTokenServiceUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Сервис привязки временно недоступен. Попробуйте через минуту.",
        )
    if payload.user_id != current_user_id or payload.kind != expected_kind:
        # Mismatch — токен принадлежит другому user или предназначен для другого kind.
        # Логируем как forensics event и не различаем причину для клиента.
        mismatch_reason = (
            "user_id" if payload.user_id != current_user_id else "kind"
        )
        await log_event(
            db,
            "auth.link_token.consume_mismatch",
            user_id=current_user_id,
            ip=ip,
            details={
                "expected_kind": expected_kind,
                "payload_kind": payload.kind,
                "expected_user_id": current_user_id,
                "payload_user_id": payload.user_id,
                "mismatch_reason": mismatch_reason,
            },
        )
        await db.commit()
        raise _link_token_invalid_http()


@router.post(
    "/identity/email/link",
    response_model=IdentityLinkResponse,
    status_code=status.HTTP_200_OK,
)
async def link_identity_email(
    body: IdentityLinkEmailRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> IdentityLinkResponse:
    """Привязать email-identity к current user. Email подтверждается magic-link consume.

    Body содержит:
    - link_token: одноразовый токен из /auth/link-token/issue {kind:'email'}
    - magic_link_token: raw token, который вернул /auth/magic-link/verify {link_mode:true}
    """
    ip = request.client.host if request.client else "unknown"
    await _enforce_link_identity_rate_limit(current_user.id)
    await _consume_link_token_for_user(db, body.link_token, "email", current_user.id, ip)

    # Consume magic_link атомарно (помечает consumed_at)
    link = await magic_link_service.consume_magic_link(db, body.magic_link_token)
    if link is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "magic_link_token недействителен, истёк или уже использован",
        )
    email = link.email.lower()

    try:
        new_link = await identity_link_service.link_existing_user(
            db, current_user.id, "email", email
        )
    except IdentityConflictError as e:
        await log_event(
            db,
            "auth.identity.linked.conflict",
            user_id=current_user.id,
            ip=ip,
            details={"kind": "email", "conflict_kind": e.conflict_kind},
        )
        await db.commit()
        raise _conflict_to_http(e)

    masked = me_service.mask_value("email", email)
    await log_event(
        db,
        "auth.identity.linked",
        user_id=current_user.id,
        ip=ip,
        details={"kind": "email", "value_masked": masked, "source": "magic_link"},
    )
    await db.commit()
    return IdentityLinkResponse(
        identity=IdentityLinkedItem(
            kind="email", value_masked=masked, created_at=new_link.created_at
        )
    )


@router.post(
    "/identity/tg/link",
    response_model=IdentityLinkResponse,
    status_code=status.HTTP_200_OK,
)
async def link_identity_tg(
    body: IdentityLinkTgRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> IdentityLinkResponse:
    """Привязать Telegram-identity к current user. Подтверждение — initData HMAC."""
    ip = request.client.host if request.client else "unknown"
    await _enforce_link_identity_rate_limit(current_user.id)
    await _consume_link_token_for_user(db, body.link_token, "tg", current_user.id, ip)

    if not _settings.tg_bot_token_for_initdata:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TG auth не настроен")

    params = tg_init_service.verify_tg_init_data(
        body.init_data, _settings.tg_bot_token_for_initdata
    )
    if params is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверная подпись initData")

    tg_id_str = tg_init_service.extract_tg_user_id(params)
    if not tg_id_str:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tg_id не найден в initData")

    try:
        new_link = await identity_link_service.link_existing_user(
            db, current_user.id, "tg", tg_id_str
        )
    except IdentityConflictError as e:
        await log_event(
            db,
            "auth.identity.linked.conflict",
            user_id=current_user.id,
            ip=ip,
            details={"kind": "tg", "conflict_kind": e.conflict_kind},
        )
        await db.commit()
        raise _conflict_to_http(e)

    masked = me_service.mask_value("tg", tg_id_str)
    await log_event(
        db,
        "auth.identity.linked",
        user_id=current_user.id,
        ip=ip,
        details={"kind": "tg", "value_masked": masked, "source": "init_data"},
    )
    await db.commit()
    return IdentityLinkResponse(
        identity=IdentityLinkedItem(
            kind="tg", value_masked=masked, created_at=new_link.created_at
        )
    )


@router.post(
    "/identity/vk/link",
    response_model=IdentityLinkResponse,
    status_code=status.HTTP_200_OK,
)
async def link_identity_vk(
    body: IdentityLinkVkRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> IdentityLinkResponse:
    """Привязать VK-identity к current user.

    PKCE flow для existing user: SPW отправил state="link:<token>",
    vk-relay перенаправил браузер на /me/identity/vk/link, body содержит
    уже очищенный link_token (без префикса 'link:').
    """
    ip = request.client.host if request.client else "unknown"
    await _enforce_link_identity_rate_limit(current_user.id)
    await _consume_link_token_for_user(db, body.link_token, "vk", current_user.id, ip)

    try:
        token_data = await vk_oauth_service.exchange_code(
            body.code, body.code_verifier, body.device_id, _settings
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"VK exchange failed: {e}")

    access_token: str = token_data["access_token"]
    refresh_token_vk: str | None = token_data.get("refresh_token")
    expires_in: int = token_data.get("expires_in", 3600)
    vk_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    try:
        userinfo = await vk_oauth_service.fetch_vk_userinfo(access_token)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"VK userinfo failed: {e}")

    vk_user_id: str = userinfo["user_id"]
    enc_access = encrypt_token(access_token, _settings)
    enc_refresh = encrypt_token(refresh_token_vk, _settings) if refresh_token_vk else None

    try:
        new_link = await identity_link_service.link_existing_user(
            db,
            current_user.id,
            "vk",
            vk_user_id,
            vk_access_token_enc=enc_access,
            vk_refresh_token_enc=enc_refresh,
            vk_token_expires_at=vk_token_expires_at,
        )
    except IdentityConflictError as e:
        await log_event(
            db,
            "auth.identity.linked.conflict",
            user_id=current_user.id,
            ip=ip,
            details={"kind": "vk", "conflict_kind": e.conflict_kind},
        )
        await db.commit()
        raise _conflict_to_http(e)

    masked = me_service.mask_value("vk", vk_user_id)
    await log_event(
        db,
        "auth.identity.linked",
        user_id=current_user.id,
        ip=ip,
        details={"kind": "vk", "value_masked": masked, "source": "vk_pkce"},
    )
    await db.commit()
    return IdentityLinkResponse(
        identity=IdentityLinkedItem(
            kind="vk", value_masked=masked, created_at=new_link.created_at
        )
    )


# ── POST /me/attribute-guest (Phase Y-5) ───────────────────────────────────

@router.post(
    "/attribute-guest",
    response_model=AttributeGuestResponse,
    status_code=status.HTTP_200_OK,
)
async def attribute_guest(
    body: AttributeGuestRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> AttributeGuestResponse:
    """Атрибуция guest_session к авторизованному user после login.

    Phase Y-5 §6.2.4. Используется когда юзер вошёл existing identity
    (не registration), а frontend сохранил guest_session_id cookie.
    Idempotent; кросс-юзер conflict даёт 409.
    """
    ip = request.client.host if request.client else "unknown"

    # Rate-limit 30/мин/user
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(
        redis,
        f"attribute_guest:{current_user.id}",
        max_requests=30,
        window_seconds=60,
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    try:
        result = await guest_attribution_service.attribute_guest_post_login(
            db=db,
            user_id=current_user.id,
            guest_session_id=body.guest_session_id,
        )
    except GuestAttributionConflictError as exc:
        await db.rollback()
        await log_event(
            db,
            "guest.attribute.conflict",
            user_id=current_user.id,
            ip=ip,
            details={"guest_session_id": str(body.guest_session_id)},
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "guest_session уже атрибутирован на другого пользователя",
        ) from exc

    if not result.found:
        # Frontend cookie мог истечь / guest_session был очищен
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "guest_session не найден",
        )

    if not result.already_attributed and result.attributed_count > 0:
        await log_event(
            db,
            "guest.attributed",
            user_id=current_user.id,
            ip=ip,
            details={
                "guest_session_id": str(body.guest_session_id),
                "attempts_count": result.attributed_count,
            },
        )
    await db.commit()

    return AttributeGuestResponse(
        guest_session_id=body.guest_session_id,
        attributed_count=result.attributed_count,
        already_attributed=result.already_attributed,
    )


# ─────────────────── tsk-301 Фаза 8: витрина прав подписки ──────────────────


@router.get(
    "/entitlements",
    response_model=MyEntitlements,
    summary="Что даёт мой тариф и сколько осталось",
)
async def my_entitlements(
    student_id: int | None = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MyEntitlements:
    """Один источник для кнопок ученика: что доступно, сколько осталось, что даст апгрейд.

    Собирается из той же двери прав, что и сами гейты, — иначе кнопка и запрет
    разъехались бы: интерфейс показывал бы доступное там, где сервер откажет.
    Именно так и выглядит худший вид расхождения — человек нажимает и получает
    ошибку вместо объяснения.

    `student_id` читается ТОЛЬКО у сервисного вызывающего — та же оговорка, что у
    наставника: боты ходят по сервисному ключу, и своего `current_user` у них нет.
    Обычный ученик, подставивший чужой номер, получает 403: иначе это сквозная
    дыра, показывающая чужой тариф и остаток.
    """
    owner = resolve_student_owner(current_user, student_id, subject="права ничьи")
    return await entitlements_service.snapshot(db, student_id=owner)
