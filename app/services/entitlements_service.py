"""Единая дверь прав подписки (tsk-301, Фаза 2).

**Все точки принуждения спрашивают разрешение здесь и только здесь.** Прецедент,
ради которого это правило существует: в tsk-572 правило применили в двух клиентах
из трёх, и ученик получил порченый текст. Дисциплиной такое не закрывается —
поэтому список точек расхода ИИ проверяется сторожевым тестом
(`tests/test_tsk301_ai_spend_guard.py`), а не памятью.

Контракт: `docs/specs/2026-08-08-contract-entitlements.md` §3-§6.
Принцип: **тариф даёт ПРАВА, расписание порождает ДЕНЬГИ** (ADR-0006).

Ключевое требование к форме ответа: «прав нет» и «проверка сломалась» — **разные
исходы**, а не общий `False`. Сведение их к булеву значению и есть тот дефект,
из-за которого анонимный посетитель 464 демо-курсов жёг бы токены.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal, Optional

from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.core.config import Settings

if TYPE_CHECKING:  # только для аннотации: импорт схемы в рантайме создал бы
    # лишнюю связь сервиса с представлением.
    from app.schemas.me import MyEntitlements

logger = logging.getLogger(__name__)
settings = Settings()

#: Возможности, которые проходят через эту дверь.
#: `lessons` и `content` сюда НЕ входят намеренно: первое — признак для расписания
#: и денег, второе принуждается демо-лимитом гостевой сессии (tsk-423). Дверь,
#: отвечающая на вопросы, которые она не решает, вводит в заблуждение.
GATED_CAPABILITIES: tuple[str, ...] = ("ai_tutor", "code_review", "teacher_escalation")

#: Полный набор для витрины `GET /me/entitlements` (Фаза 8).
ALL_CAPABILITIES: tuple[str, ...] = GATED_CAPABILITIES + ("lessons", "content")

Outcome = Literal[
    "allowed",
    "denied_no_plan",       # ученик не опознан либо подписки нет — ВАЛИДНЫЙ отказ
    "denied_not_in_plan",   # подписка есть, возможность в неё не входит
    "denied_limit",         # входит, но квота и пакеты исчерпаны
    "error_technical",      # проверку не удалось выполнить (инфраструктура)
    # tsk-605: у задания нет ни эталона, ни критериев, а у ученика нет выхода
    # на человека — машине нельзя оставлять последнее слово.
    "denied_task_not_gradable",
]

#: Исключения, на которых срабатывает fail-open. Список ЗАКРЫТЫЙ.
#: Всё остальное (`KeyError`, `AttributeError`, `TypeError`, любая ошибка в нашей
#: логике) поднимается наружу и доступ НЕ даёт: иначе первая же опечатка в коде
#: превращается в молча открытую дверь, неотличимую от штатной работы.
_TECHNICAL_ERRORS = (OperationalError, InterfaceError, asyncio.TimeoutError)


@dataclass(frozen=True)
class Decision:
    """Ответ двери. `allowed` и `outcome` возвращаются вместе и оба."""

    allowed: bool
    outcome: Outcome
    limit: Optional[int] = None
    remaining: Optional[int] = None
    upgrade_hint: Optional[str] = None
    #: Списание прошло сверх лимита — разговор, начатый до исчерпания, доводится
    #: до конца. Вызывающий обязан пометить этим `llm_usage_event.meta.over_limit`,
    #: иначе щедрость становится невидимой и вопрос «не злоупотребляют ли» нечем
    #: закрыть.
    over_limit: bool = False
    #: Откуда списана единица — чтобы вернуть ЕЁ ЖЕ, если вызов модели не удался.
    #: Резерв идёт ДО вызова (только так закрывается гонка), поэтому неудачу
    #: обязана компенсировать явная отдача, а не надежда на то, что сбоев не
    #: бывает. `None` — ничего не списывали (безлимит либо отказ).
    reserved_quota: bool = False
    reserved_grant_id: Optional[int] = None


_DENIED_NO_PLAN = Decision(allowed=False, outcome="denied_no_plan")
_ERROR_TECHNICAL = Decision(allowed=True, outcome="error_technical")


_PLAN_SQL = text("""
    SELECT p.code, p.name, p.ai_tutor_limit, p.code_review, p.teacher_escalation,
           p.lessons, p.content, p.upgrade_hint
      FROM student_subscription s
      JOIN subscription_plan p ON p.id = s.plan_id
     WHERE s.student_id = :sid AND s.ends_on IS NULL
""")


def month_start(day: Optional[date] = None) -> date:
    """Первое число месяца — период учёта квоты."""
    day = day or date.today()
    return day.replace(day=1)


async def _load_plan(db: AsyncSession, student_id: int) -> Optional[dict]:
    row = (await db.execute(_PLAN_SQL, {"sid": student_id})).mappings().first()
    return dict(row) if row is not None else None


async def _quota_used(db: AsyncSession, student_id: int, period: date) -> int:
    used = (
        await db.execute(
            text(
                "SELECT used FROM student_ai_quota "
                " WHERE student_id = :sid AND period = :p"
            ),
            {"sid": student_id, "p": period},
        )
    ).scalar()
    return int(used or 0)


async def _grants_left(db: AsyncSession, student_id: int) -> int:
    left = (
        await db.execute(
            text(
                "SELECT coalesce(sum(granted - used), 0) FROM student_ai_grant "
                " WHERE student_id = :sid AND used < granted"
            ),
            {"sid": student_id},
        )
    ).scalar()
    return int(left or 0)


def _hint(plan: dict) -> Optional[str]:
    return plan.get("upgrade_hint")


async def check(
    db: AsyncSession, *, student_id: Optional[int], capability: str
) -> Decision:
    """Можно ли ученику воспользоваться возможностью. Ничего не списывает.

    Args:
        student_id: `None` и `0` означают «ученик не опознан» (гость, сервисный
            вызов без явного ученика) — это валидный отрицательный ответ, а не
            повод пускать.
        capability: ключ из `GATED_CAPABILITIES`.

    Raises:
        ValueError: неизвестная возможность. Опечатка в ключе не должна молча
            означать «разрешено».
    """
    if capability not in GATED_CAPABILITIES:
        raise ValueError(
            f"{capability!r} не проходит через дверь прав; "
            f"доступны {GATED_CAPABILITIES}"
        )
    if not student_id:
        return _DENIED_NO_PLAN

    try:
        plan = await _load_plan(db, student_id)
        if plan is None:
            return _DENIED_NO_PLAN

        if capability in ("code_review", "teacher_escalation"):
            if plan[capability]:
                return Decision(allowed=True, outcome="allowed")
            return Decision(
                allowed=False, outcome="denied_not_in_plan", upgrade_hint=_hint(plan)
            )

        # ai_tutor — счётная возможность.
        limit = plan["ai_tutor_limit"]
        if limit is None:
            return Decision(allowed=True, outcome="allowed")
        if limit == 0:
            return Decision(
                allowed=False, outcome="denied_not_in_plan", upgrade_hint=_hint(plan)
            )

        period = month_start()
        used = await _quota_used(db, student_id, period)
        remaining = max(limit - used, 0) + await _grants_left(db, student_id)
        if remaining <= 0:
            return Decision(
                allowed=False, outcome="denied_limit", limit=limit, remaining=0,
                upgrade_hint=_hint(plan),
            )
        return Decision(
            allowed=True, outcome="allowed", limit=limit, remaining=remaining
        )
    except _TECHNICAL_ERRORS:
        logger.warning(
            "tsk-301: проверка права %s для ученика %s не выполнена технически — "
            "пускаем (fail-open)", capability, student_id, exc_info=True,
        )
        return _ERROR_TECHNICAL


async def check_machine_verdict(
    db: AsyncSession,
    *,
    student_id: Optional[int],
    task_type: Optional[str],
    solution_rules: object,
) -> Decision:
    """Можно ли оставить последнее слово по заданию за машиной (tsk-605).

    Вопрос состоит из двух половин, и обе обязаны сойтись:

    1. **Годится ли задание.** Калибровка tsk-590 на 180 живых сдачах: с
       эталоном собственные ошибки лучшей модели 1.2 %, без эталона —
       7.6–19.0 %, потому что без эталона модель не пересчитывает задачу, а
       подтверждает предъявленное учеником число. Предикат — единый,
       `ai_check_policy.evaluate`, здесь он вызывается, а не повторяется.
    2. **Есть ли кому перехватить ошибку.** Пока у ученика в тарифе есть
       выход на преподавателя (`teacher_escalation`), ошибочный зачёт ловит
       человек — так живёт сегодняшняя обязательная очередь. В автономном
       треке («ученик работает без преподавателя», решение оператора
       2026-08-08) перехватывать некому, и та же ошибка уезжает ученику как
       знание.

    Отказ означает не «ученику отказать», а «машине не решать»: работа
    уходит человеку. Что делать, если человека в тарифе нет вовсе —
    исключить задание из трека либо продать эскалацию — решается в tsk-301,
    здесь такое право не выдумывается.

    Отсутствие подписки трактуется как отсутствие ДОКАЗАННОГО выхода на
    человека: неизвестность на стороне безопасности, а не разрешения. На
    поведение это сегодня не влияет — применение отказа управляется
    `should_block` и режимом выката.

    :param student_id: `None`/`0` — ученик не опознан.
    :param task_type: `task_content.type` задания.
    :param solution_rules: правила задания (схема, словарь либо None).
    """
    # Импорт внутри функции: политика допуска тянет схемы заданий, а дверь
    # прав грузится в модулях, которым задания не нужны вовсе.
    from app.services import ai_check_policy  # noqa: PLC0415

    verdict = ai_check_policy.evaluate(task_type, solution_rules)
    if verdict.allowed:
        return Decision(allowed=True, outcome="allowed")

    denied = Decision(
        allowed=False,
        outcome="denied_task_not_gradable",
        upgrade_hint=(
            f"Задание проверяет преподаватель: {verdict.human_reason}."
        ),
    )
    if not student_id:
        return denied

    try:
        plan = await _load_plan(db, student_id)
        if plan is None:
            return denied
        if plan["teacher_escalation"]:
            # Человек в тарифе есть — сегодняшний порядок (машина ставит
            # предварительный итог, преподаватель подтверждает) не ломаем.
            return Decision(allowed=True, outcome="allowed")
        return denied
    except _TECHNICAL_ERRORS:
        logger.warning(
            "tsk-605: не удалось узнать, есть ли у ученика %s выход на "
            "преподавателя — пускаем (fail-open)", student_id, exc_info=True,
        )
        return _ERROR_TECHNICAL


_RESERVE_QUOTA_SQL = text("""
    INSERT INTO student_ai_quota (student_id, period, used)
    VALUES (:sid, :p, 1)
    ON CONFLICT (student_id, period) DO UPDATE
       SET used = student_ai_quota.used + 1, updated_at = now()
     WHERE student_ai_quota.used < :limit
    RETURNING used
""")

_RESERVE_QUOTA_OVERRUN_SQL = text("""
    INSERT INTO student_ai_quota (student_id, period, used)
    VALUES (:sid, :p, 1)
    ON CONFLICT (student_id, period) DO UPDATE
       SET used = student_ai_quota.used + 1, updated_at = now()
    RETURNING used
""")

#: FIFO по дате покупки: раньше купленный пакет тратится первым.
#: `SKIP LOCKED` — чтобы две одновременные реплики не ждали друг друга на одной
#: строке, а разошлись по разным пакетам.
_RESERVE_GRANT_SQL = text("""
    UPDATE student_ai_grant
       SET used = used + 1
     WHERE id = (
        SELECT id FROM student_ai_grant
         WHERE student_id = :sid AND used < granted
         ORDER BY purchased_at, id
         LIMIT 1
         FOR UPDATE SKIP LOCKED
     )
    RETURNING id
""")


async def check_and_reserve(
    db: AsyncSession,
    *,
    student_id: Optional[int],
    capability: str = "ai_tutor",
    allow_overrun: bool = False,
) -> Decision:
    """Проверить право и СРАЗУ списать единицу счётной возможности.

    Проверка и резерв — **один оператор SQL**, а не «прочитал, сравнил, записал»:
    две вкладки ученика иначе съедают одну единицу дважды.

    Порядок списания: сначала бесплатная месячная квота, потом купленные пакеты
    (FIFO). Обратный порядок сжигал бы оплаченное раньше бесплатного — человек
    терял бы деньги в месяц, когда мог не тратить ничего.

    Args:
        allow_overrun: разрешить списание сверх лимита. Ставится, когда разговор
            УЖЕ начат: исчерпание посреди диалога его не обрывает (решения 2C и
            19 брифа). Перерасход ограничен сверху жёстким пределом ходов сессии
            (`session_service`), отдельного потолка не нужно.
    """
    if capability != "ai_tutor":
        # Несчётные возможности резервировать нечего — это не отказ, а ошибка
        # вызова: молча вернуть `allowed` значило бы скрыть её.
        raise ValueError(f"{capability!r} не является счётной возможностью")
    if not student_id:
        return _DENIED_NO_PLAN

    try:
        plan = await _load_plan(db, student_id)
        if plan is None:
            return _DENIED_NO_PLAN

        limit = plan["ai_tutor_limit"]
        if limit is None:
            return Decision(allowed=True, outcome="allowed")
        if limit == 0:
            # `allow_overrun` снимает ЛИМИТ, но не выдаёт ПРАВО. Ноль означает
            # «наставника в тарифе нет вовсе», и никакой начатый разговор этого
            # не меняет. Иначе ученик Self с разговором, открытым до включения
            # гейта (такие строки уже есть в `ai_tutor_session`), получил бы
            # безлимитный доступ навсегда.
            return Decision(
                allowed=False, outcome="denied_not_in_plan", upgrade_hint=_hint(plan)
            )

        period = month_start()
        if allow_overrun:
            used = (
                await db.execute(
                    _RESERVE_QUOTA_OVERRUN_SQL, {"sid": student_id, "p": period}
                )
            ).scalar_one()
            return Decision(
                allowed=True, outcome="allowed", limit=limit,
                remaining=max(limit - used, 0) + await _grants_left(db, student_id),
                over_limit=used > limit,
                reserved_quota=True,
            )

        used = (
            await db.execute(
                _RESERVE_QUOTA_SQL, {"sid": student_id, "p": period, "limit": limit}
            )
        ).scalar()
        if used is not None:
            return Decision(
                allowed=True, outcome="allowed", limit=limit,
                remaining=max(limit - used, 0) + await _grants_left(db, student_id),
                reserved_quota=True,
            )

        # Квота исчерпана — идём в пакеты.
        grant_id = (
            await db.execute(_RESERVE_GRANT_SQL, {"sid": student_id})
        ).scalar()
        if grant_id is not None:
            return Decision(
                allowed=True, outcome="allowed", limit=limit,
                remaining=await _grants_left(db, student_id),
                reserved_grant_id=int(grant_id),
            )

        return Decision(
            allowed=False, outcome="denied_limit", limit=limit, remaining=0,
            upgrade_hint=_hint(plan),
        )
    except _TECHNICAL_ERRORS:
        logger.warning(
            "tsk-301: резерв лимита наставника для ученика %s не выполнен "
            "технически — пускаем (fail-open)", student_id, exc_info=True,
        )
        return _ERROR_TECHNICAL


async def release(
    db: AsyncSession, decision: Decision, *, student_id: int
) -> None:
    """Вернуть списанную единицу, если вызов модели не удался.

    Резерв идёт ДО вызова — иначе гонку двух вкладок закрыть нечем. Плата за это
    одна: неудачный вызов обязан явно отдать единицу назад, а не рассчитывать на
    то, что сбоев не бывает. Возвращается ровно тот источник, из которого
    списали (`reserved_quota` либо конкретный пакет), — иначе отдача бесплатной
    квоты «чинила» бы платный пакет и наоборот.

    Ничего не делает, если резерва не было (безлимит, отказ, технический сбой).
    Собственных исключений не поднимает: провалившаяся компенсация не должна
    превращать сбой модели в ошибку сервера.
    """
    try:
        if decision.reserved_quota:
            await db.execute(
                text(
                    "UPDATE student_ai_quota SET used = greatest(used - 1, 0), "
                    "       updated_at = now() "
                    " WHERE student_id = :sid AND period = :p"
                ),
                {"sid": student_id, "p": month_start()},
            )
        elif decision.reserved_grant_id is not None:
            await db.execute(
                text(
                    "UPDATE student_ai_grant SET used = greatest(used - 1, 0) "
                    " WHERE id = :gid"
                ),
                {"gid": decision.reserved_grant_id},
            )
    except _TECHNICAL_ERRORS:
        logger.warning(
            "tsk-301: не удалось вернуть единицу лимита ученику %s — "
            "останется списанной", student_id, exc_info=True,
        )


def should_block(decision: Decision, *, capability: str = "", student_id: int | None = None) -> bool:
    """Применяется ли отказ при текущем режиме выката.

    Решение считается ВСЕГДА, а применяется по режиму — так фаза наблюдения
    (`shadow`) отвечает на вопрос «кого бы отрезало» до того, как кого-то
    действительно отрежут.
    """
    if decision.allowed:
        return False

    mode = settings.subscription_gate_mode
    if mode == "off":
        return False

    if mode in ("shadow", "guests"):
        logger.info(
            "tsk-301 gate[%s]: ученик=%s возможность=%s исход=%s — %s",
            mode, student_id, capability, decision.outcome,
            "ПРИМЕНЁН" if (mode == "guests" and decision.outcome == "denied_no_plan")
            else "только лог",
        )
        return mode == "guests" and decision.outcome == "denied_no_plan"

    return True


#: Доля остатка, ниже которой ученика предупреждают ДО исчерпания. Порог считает
#: сервер, а не каждый клиент: иначе веб и бот однажды предупредят на разных
#: числах, и это будет выглядеть как ошибка в счётчике.
WARN_REMAINING_SHARE = 0.2

#: Подсказка, когда тарифа нет вовсе. Взять её из плана неоткуда, а «недоступно»
#: без объяснения — тупик: человек не понимает, он что-то сломал или так задумано.
NO_PLAN_HINT = "Тариф не назначен — напишите преподавателю, он подключит доступ."


async def snapshot(db: AsyncSession, *, student_id: int) -> "MyEntitlements":
    """Права ученика целиком — один ответ на все кнопки интерфейса.

    Собирается ЧЕРЕЗ ТУ ЖЕ дверь, что и сами гейты. Считать права для показа
    отдельным запросом нельзя: интерфейс показывал бы доступное там, где сервер
    откажет, и человек нажимал бы кнопку в ошибку вместо объяснения.
    """
    from app.schemas.me import CapabilityState, MyEntitlements  # noqa: PLC0415

    plan = await _load_plan(db, student_id)
    caps: dict[str, CapabilityState] = {}
    for capability in GATED_CAPABILITIES:
        decision = await check(db, student_id=student_id, capability=capability)
        limit, remaining = decision.limit, decision.remaining
        warn = bool(
            limit and remaining is not None
            and remaining <= max(1, int(limit * WARN_REMAINING_SHARE))
        )
        caps[capability] = CapabilityState(
            allowed=decision.allowed,
            reason=decision.outcome,
            limit=limit,
            remaining=remaining,
            warn=warn,
            upgrade_hint=(
                decision.upgrade_hint
                or (NO_PLAN_HINT if decision.outcome == "denied_no_plan" else None)
            ),
        )

    # Пакет предлагаем там же, где он продаётся: по наличию ЧИСЛЕННОГО лимита.
    # Правило одно с эндпоинтом покупки — разъехавшись, они дали бы кнопку,
    # которая ведёт в отказ.
    tutor = caps["ai_tutor"]
    offer = (
        {
            "units": settings_store.get_int("ai_package_units"),
            "price_minor": settings_store.get_int("ai_package_price_minor"),
        }
        if tutor.limit is not None
        else None
    )

    return MyEntitlements(
        plan_code=plan["code"] if plan else None,
        plan_name=plan.get("name") if plan else None,
        content=(plan or {}).get("content") or "full",
        capabilities=caps,
        package_offer=offer,
    )
