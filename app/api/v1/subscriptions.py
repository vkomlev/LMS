"""Управление тарифами персоналом (tsk-301, Фаза 9).

Гейт `marketer|admin`. **Преподавателю нельзя** (решение 10 брифа): тариф — это
деньги и права, а не учебная работа; преподаватель распоряжается занятиями.
Методиста здесь тоже нет — по той же границе, что и в кабинете маркетолога.

Права включаются сразу, деньги — со следующего месяца (решение 14). Второе
обеспечивает не этот код, а резолвер группы: он берёт строку подписки,
действовавшую **на первое число расчётного месяца** (tsk-585). Поэтому смена
тарифа посреди месяца не переписывает уже названную человеку сумму, и делать
здесь для этого ничего не нужно — важно ровно НЕ звать пересчёт.
"""
from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.subscription import (
    GraduationPreview,
    GraduationResult,
    StudentSubscriptionState,
    SubscriptionChangeRequest,
    SubscriptionPlanRead,
    SubscriptionSummary,
    SubscriptionSummaryStudent,
)
from app.services import audit_service, graduation_service, subscription_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

_ROLE_GATE = require_role("marketer", "admin")


async def _staff_gate(
    current_user: CurrentUser = Depends(_ROLE_GATE),
) -> CurrentUser:
    """Роль marketer/admin И живой человек, а не сервисный ключ.

    `require_role` пропускает сервисный токен без проверки роли — это сделано
    ради ботов. Здесь так нельзя: держатель ключа TG_LMS менял бы тарифы, а в
    истории оставалось бы `changed_by = NULL` — то есть «неизвестно кто». Смена
    тарифа меняет и права, и сумму месяца; безымянная смена неразбираема.
    """
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Управление тарифами доступно только пользователю, не сервисному ключу",
        )
    return current_user


async def _require_user(db: AsyncSession, student_id: int) -> None:
    """404 на несуществующего ученика.

    Без проверки присвоение упало бы 500 на внешнем ключе, а чтение молча
    вернуло бы пустую историю — «тарифа нет» вместо «такого человека нет».
    """
    exists = await db.scalar(
        text("SELECT 1 FROM users WHERE id = :id"), {"id": student_id}
    )
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")


@router.get(
    "/plans",
    response_model=list[SubscriptionPlanRead],
    summary="Все тарифы с правами — витрина персонала",
    description=(
        "Весь набор, включая непродаваемые (`test`, `base_legacy`, «Выпускник»). "
        "Витрина покупки — другой адрес: `GET /payments/plans`, там только то, "
        "что человек может купить сам."
    ),
)
async def list_plans(
    db: AsyncSession = Depends(get_async_db),
    _staff: CurrentUser = Depends(_staff_gate),
) -> list[SubscriptionPlanRead]:
    """Тарифы с правами и тарифной группой."""
    return [SubscriptionPlanRead(**row) for row in await subscription_service.list_plans(db)]


@router.get(
    "/summary",
    response_model=SubscriptionSummary,
    summary="Раскладка учеников по тарифам",
    description=(
        "Сколько человек на каждом тарифе, сколько без тарифа вовсе, у скольких "
        "есть расписание, кто засиделся на одном тарифе и у кого просрочена "
        "оплата. Сумм денег здесь нет: их считает расписание, а не тариф — "
        "начисления живут на своём экране (`GET /marketer/charges`)."
    ),
)
async def read_summary(
    db: AsyncSession = Depends(get_async_db),
    _staff: CurrentUser = Depends(_staff_gate),
) -> SubscriptionSummary:
    """Обзорная картина по тарифам (tsk-619)."""
    return SubscriptionSummary(**await subscription_service.plan_distribution(db))


@router.get(
    "/summary/students",
    response_model=list[SubscriptionSummaryStudent],
    summary="Ученики одной строки сводки",
    description=(
        "Разворот строки: `plan_code` передаётся ровно тем значением, что стоит "
        "в строке сводки, — код тарифа либо ПУСТО для строки «без тарифа». "
        "Дольше всех на тарифе идут первыми: с этого конца списка и начинается "
        "работа."
    ),
    responses={404: {"description": "Нет такого тарифа"}},
)
async def read_summary_students(
    plan_code: str | None = Query(
        default=None,
        description="Код тарифа; не задан — ученики без тарифа",
        max_length=64,
    ),
    db: AsyncSession = Depends(get_async_db),
    _staff: CurrentUser = Depends(_staff_gate),
) -> list[SubscriptionSummaryStudent]:
    """Кто именно стоит за строкой сводки.

    Опечатка в коде тарифа отвечает 404, а не пустым списком: пустой список
    здесь — законный ответ («на Self никого»), и свести с ним ошибку значило бы
    показывать маркетологу «никого нет» вместо «такого тарифа нет».
    """
    if plan_code is not None:
        # Проверяем СУЩЕСТВОВАНИЕ тарифа, а не его активность: выключенный
        # тариф, на котором кто-то ещё сидит, остаётся строкой сводки — и
        # разворачиваться она обязана, иначе именно этих людей, ради которых
        # тариф и выключали, открыть было бы нельзя.
        known = await db.scalar(
            text("SELECT 1 FROM subscription_plan WHERE code = :c"),
            {"c": plan_code},
        )
        if not known:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Тариф не найден")

    rows = await subscription_service.students_on_plan(db, plan_code)
    return [SubscriptionSummaryStudent(**row) for row in rows]


@router.get(
    "/students/{student_id}",
    response_model=StudentSubscriptionState,
    summary="Тариф ученика: действующий и история",
    description=(
        "Кроме тарифа отдаёт `manual_pricing` — ручные деньги ученика, которые "
        "затронет перевод (tsk-634). Экран смены тарифа обязан показать их ДО "
        "нажатия: ручная цена ставится там, где есть личная договорённость, и "
        "её тихое изменение выглядит нормальным пересчётом. Сумма месяца "
        "(`monthly_amounts`) при переводе едет следом на новый тариф; цена "
        "группы (`group_prices`) перестаёт действовать — `applies_now = false`."
    ),
)
async def read_student_subscription(
    student_id: int = Path(..., description="ID ученика"),
    db: AsyncSession = Depends(get_async_db),
    _staff: CurrentUser = Depends(_staff_gate),
) -> StudentSubscriptionState:
    """Что действует сейчас и как к этому пришли."""
    await _require_user(db, student_id)
    return StudentSubscriptionState(**await subscription_service.student_state(db, student_id))


@router.get(
    "/students/{student_id}/graduation-preview",
    response_model=GraduationPreview,
    summary="Что произойдёт при переводе на «Выпускника»",
    description=(
        "Свод оплаты и след ученика в расписании — ДО нажатия и без единой "
        "записи (tsk-673). Отдаёт то же самое, что перевод потом и сделает: "
        "сколько привязок к слотам погаснет, сколько будущих занятий снимется, "
        "начислено / оплачено / остаток по всем открытым месяцам.\n\n"
        "Долгом считается остаток по всем открытым месяцам, а не просрочка: "
        "человек уходит, следующего счёта ему никто не выставит. Приложенный "
        "чек долг гасит."
    ),
)
async def read_graduation_preview(
    student_id: int = Path(..., description="ID ученика"),
    db: AsyncSession = Depends(get_async_db),
    _staff: CurrentUser = Depends(_staff_gate),
) -> GraduationPreview:
    """Предпросмотр выпуска: что снимется и сколько человек остался должен."""
    await _require_user(db, student_id)
    plan = await graduation_service.preview(db, student_id)
    return GraduationPreview.model_validate(plan, from_attributes=True)


@router.post(
    "/students/{student_id}",
    response_model=StudentSubscriptionState,
    summary="Присвоить ученику тариф",
    description=(
        "Права меняются сразу, сумма текущего месяца — нет (решение 14): деньги "
        "считаются по тарифу, действовавшему на первое число месяца.\n\n"
        "Ручные деньги перевод не теряет (tsk-634): сумма месяца, поставленная "
        "руками, переезжает на строку нового тарифа при ближайшем пересчёте, а "
        "бессрочная цена прежней группы перестаёт применяться, но из базы не "
        "исчезает. Что именно затронет перевод — в `manual_pricing` ответа "
        "`GET /subscriptions/students/{student_id}`; показать это ДО нажатия — "
        "работа экрана.\n\n"
        "**Перевод на «Выпускника» — не только смена тарифа** (tsk-673). Вместе "
        "с ним ученик снимается со всех слотов расписания и из будущих занятий, "
        "которые он не подтверждал сам; сводится оплата по всем открытым "
        "месяцам; при остатке месяцы закрываются (чтобы долг не стёрся "
        "пересчётом) и уходит эскалация маркетологу. Что именно произошло — в "
        "поле `graduation` ответа. Предпросмотр до нажатия: "
        "`GET /subscriptions/students/{student_id}/graduation-preview`."
    ),
    responses={
        404: {"description": "Нет такого ученика или тарифа"},
        409: {"description": "Ученик уже на этом тарифе либо тариф сменили параллельно"},
    },
)
async def change_student_subscription(
    request: Request,
    student_id: int = Path(..., description="ID ученика"),
    body: SubscriptionChangeRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_staff_gate),
) -> StudentSubscriptionState:
    """Сменить тариф ученику и записать, кто и зачем это сделал.

    Исходы разведены намеренно: сервис отвечает одним `False` и на «нет такого
    тарифа», и на «уже на нём», и на гонку. Для человека это три разных ответа —
    опечатка в коде тарифа, пустое действие и «кто-то успел раньше».
    """
    await _require_user(db, student_id)

    before = await subscription_service.student_state(db, student_id)
    current_code = (before["current"] or {}).get("plan_code")

    plan_exists = await db.scalar(
        text("SELECT 1 FROM subscription_plan WHERE code = :c AND is_active"),
        {"c": body.plan_code},
    )
    if not plan_exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тариф не найден")
    if current_code == body.plan_code:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Ученик уже на тарифе «{body.plan_code}»"
        )

    changed = await subscription_service.change_plan(
        db,
        student_id,
        body.plan_code,
        reason=body.reason,
        changed_by=current_user.id,
    )
    if not changed:
        # Проверки выше прошли, значит между ними и сменой успел кто-то ещё.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Тариф сменили параллельно — обновите страницу"
        )

    # tsk-673: выпуск — это событие целиком, а не смена строки подписки. Хук
    # висит ЗДЕСЬ, а не внутри `change_plan`, потому что это единственный путь
    # на «Выпускника»: продать его нельзя (нет тарифной группы), автоматика
    # переводит только на `base`. Один путь — один хук, и тихого обхода нет.
    graduation = None
    if body.plan_code == graduation_service.ALUMNI_PLAN_CODE:
        graduation = await graduation_service.apply(
            db, student_id, changed_by=current_user.id
        )

    await audit_service.log_event(
        db,
        audit_service.STAFF_SUBSCRIPTION_CHANGED,
        user_id=current_user.id,
        ip=request.client.host if request.client else None,
        details={
            "student_id": student_id,
            "from_plan": current_code,
            "to_plan": body.plan_code,
            "reason": body.reason,
        },
    )
    await db.commit()
    logger.info(
        "tsk-301: %s сменил тариф ученику %s: %s → %s",
        current_user.id, student_id, current_code, body.plan_code,
    )

    state = StudentSubscriptionState(
        **await subscription_service.student_state(db, student_id)
    )
    if graduation is not None:
        state.graduation = GraduationResult.model_validate(
            graduation, from_attributes=True
        )
    return state
