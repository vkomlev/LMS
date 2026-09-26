"""Воронка сайта (tsk-1139): квиз с развилкой по правилам контента.

Вопросы — задания курса-квиза (`external_uid = <course_uid>:<код>`), поэтому
гостевые попытки, привязка к ученику после регистрации и лимиты работают как у
обычного квиза. Правила — ветки, условия показа, переходы, итоги со своими
текстами и кнопками — берутся из `quiz_funnel_spec` и считаются движком
`quiz_funnel_engine`.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.courses import Courses
from app.models.guest_attempt import GuestAttempt
from app.models.guest_session import GuestSession
from app.models.quiz_funnel import QuizFunnelBotLead, QuizFunnelProgress, QuizFunnelSpec
from app.models.tasks import Tasks
from app.services import lead_magnet_service
from app.services import quiz_funnel_engine as engine
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
_settings = Settings()

#: Какие метки принимаем. Одна схема на сайт — согласуется с tsk-1072.
ATTRIBUTION_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "page",
    "referrer",
    "branch",
    "role",
    "dir",
)
_MAX_VALUE_LEN = 300

#: Префикс параметра стартовой ссылки бота — отличает воронку от прочих /start.
BOT_START_PREFIX = "q_"

#: Виды кнопок итога, ведущие в регистрацию: скрываются, если она закрыта для ветки.
REGISTRATION_BUTTON_KINDS = ("register", "demo")
#: До текста согласия на данные несовершеннолетнего регистрация родителя закрыта
#: (решение оператора 26.09). Спецификация может переопределить.
DEFAULT_REGISTRATION_CLOSED = ("parent",)
#: Подпись кнопки, заменяющей регистрацию, когда регистрирует взрослый.
SHARE_PARENT_TEXT = "Отправить ссылку родителям"


def is_enabled() -> bool:
    """Включена ли воронка (рубильник QUIZ_FUNNEL_ENABLED)."""
    return _settings.quiz_funnel_enabled


def reminders_enabled() -> bool:
    """Слать ли напоминания гостям в боте (нужны оба рубильника)."""
    return _settings.quiz_funnel_enabled and _settings.quiz_funnel_reminders_enabled


def sanitize_attribution(raw: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Оставить только известные метки-строки, обрезать длину.

    Метки приходят из адресной строки — то есть от кого угодно. Произвольные
    ключи и вложенные объекты в jsonb не пускаем.
    """
    if not raw:
        return {}
    clean: Dict[str, str] = {}
    for key in ATTRIBUTION_KEYS:
        value = raw.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            clean[key] = str(value).strip()[:_MAX_VALUE_LEN]
    return clean


# ── загрузка ────────────────────────────────────────────────────────────────

@dataclass
class Funnel:
    """Курс-квиз воронки и его спецификация."""

    course: Courses
    spec: Dict[str, Any]


async def load_funnel(db: AsyncSession, course_uid: str) -> Optional[Funnel]:
    """Квиз-воронка по course_uid: публичный курс со спецификацией. Иначе None."""
    if not is_enabled():
        return None
    row = (
        await db.execute(
            select(Courses, QuizFunnelSpec.spec)
            .join(QuizFunnelSpec, QuizFunnelSpec.course_id == Courses.id)
            .where(Courses.course_uid == course_uid, Courses.is_public_demo.is_(True))
        )
    ).first()
    if row is None:
        return None
    return Funnel(course=row[0], spec=row[1] or {})


async def load_funnel_by_id(db: AsyncSession, course_id: int) -> Optional[Funnel]:
    """То же по id курса (бот знает id)."""
    row = (
        await db.execute(
            select(Courses, QuizFunnelSpec.spec)
            .join(QuizFunnelSpec, QuizFunnelSpec.course_id == Courses.id)
            .where(Courses.id == course_id)
        )
    ).first()
    return Funnel(course=row[0], spec=row[1] or {}) if row else None


async def _task_ids(db: AsyncSession, funnel: Funnel) -> Dict[str, int]:
    """Код вопроса → id задания курса."""
    prefix = f"{funnel.course.course_uid}:"
    rows = await db.execute(
        select(Tasks.id, Tasks.external_uid).where(
            Tasks.course_id == funnel.course.id, Tasks.external_uid.like(f"{prefix}%")
        )
    )
    return {uid[len(prefix):]: tid for tid, uid in rows.all() if uid}


async def _answers(
    db: AsyncSession, guest_session_id: Optional[UUID], task_ids: Mapping[str, int]
) -> Dict[str, List[str]]:
    """Последний ответ гостя по каждому вопросу: код → выбранные варианты.

    Последний, а не первый: ответ разрешено менять, путь пересчитывается.
    """
    if guest_session_id is None or not task_ids:
        return {}
    by_task = {tid: code for code, tid in task_ids.items()}
    rows = (
        await db.execute(
            select(GuestAttempt)
            .where(
                GuestAttempt.guest_session_id == guest_session_id,
                GuestAttempt.task_id.in_(list(by_task)),
            )
            .order_by(GuestAttempt.task_id, GuestAttempt.id.desc())
            .distinct(GuestAttempt.task_id)
        )
    ).scalars()
    result: Dict[str, List[str]] = {}
    for attempt in rows:
        response = (attempt.answer_json or {}).get("response") or {}
        selected = response.get("selected_option_ids")
        if isinstance(selected, list) and attempt.task_id in by_task:
            result[by_task[attempt.task_id]] = selected
    return result


async def _progress(
    db: AsyncSession, guest_session_id: UUID, course_id: int
) -> Optional[QuizFunnelProgress]:
    return await db.get(QuizFunnelProgress, (guest_session_id, course_id))


async def _params(
    db: AsyncSession, funnel: Funnel, guest_session_id: Optional[UUID]
) -> Dict[str, str]:
    """Параметры ссылки, с которыми начато прохождение."""
    if guest_session_id is None:
        return {}
    progress = await _progress(db, guest_session_id, funnel.course.id)
    return dict(progress.params or {}) if progress else {}


async def evaluate_session(
    db: AsyncSession, funnel: Funnel, guest_session_id: Optional[UUID]
) -> engine.Evaluation:
    """Путь, признаки и итог для сессии по её ответам и параметрам ссылки."""
    task_ids = await _task_ids(db, funnel)
    answers = await _answers(db, guest_session_id, task_ids)
    params = await _params(db, funnel, guest_session_id)
    return engine.evaluate(funnel.spec, answers, params)


# ── старт, состояние, ответ ─────────────────────────────────────────────────

async def start(
    db: AsyncSession,
    funnel: Funnel,
    guest_session_id: UUID,
    params_raw: Mapping[str, Optional[str]],
    attribution_raw: Optional[Mapping[str, Any]],
) -> None:
    """Начало прохождения: параметры ссылки и метки первого касания.

    Метки пишутся в сессию один раз (повторный заход с другой страницы источник
    не подменяет); параметры ссылки — на прохождение, последние заданные.
    """
    params = engine.valid_params(funnel.spec, params_raw)
    session = await db.get(GuestSession, guest_session_id)
    if session is not None and not session.attribution:
        clean = sanitize_attribution({**(attribution_raw or {}), **params})
        clean["entry_uid"] = funnel.course.course_uid or ""
        session.attribution = clean
    progress = await _progress(db, guest_session_id, funnel.course.id)
    if progress is None:
        db.add(
            QuizFunnelProgress(
                guest_session_id=guest_session_id,
                course_id=funnel.course.id,
                params=params,
                branch=params.get("branch"),
                role=params.get("role"),
            )
        )
    elif params:
        progress.params = params
    await db.flush()


def _question_view(
    step: engine.PathStep, ctx: engine.Context, spec: Mapping[str, Any]
) -> Dict[str, Any]:
    """Вопрос для экрана: формулировка голосом роли, видимые варианты, код, ответ."""
    content = step.question.get("task_content") or {}
    chosen = list(ctx.answers.get(step.code) or [])
    feedback = None
    if chosen and step.question.get("check"):
        feedback = engine.check_feedback(spec, step.code, chosen)
    return {
        "code": step.code,
        "branch": step.branch,
        "type": content.get("type", "SC_Qw"),
        "stem": engine.stem_for(step.question, ctx.role),
        "options": [
            {"id": o["id"], "text": o.get("text", "")}
            for o in engine.visible_options(step.question, ctx)
        ],
        "code_snippet": content.get("code"),
        "selected_option_ids": chosen or None,
        "feedback": feedback,
    }


async def get_state(
    db: AsyncSession, funnel: Funnel, guest_session_id: Optional[UUID]
) -> Dict[str, Any]:
    """Видимые вопросы до текущего включительно и признак завершения."""
    evaluation = await evaluate_session(db, funnel, guest_session_id)
    w = evaluation.walk
    questions = [_question_view(s, evaluation.ctx, funnel.spec) for s in w.steps]
    return {
        "quiz_uid": funnel.course.course_uid,
        "title": funnel.spec.get("title") or funnel.course.title,
        "description": funnel.spec.get("description") or funnel.course.description,
        "branch": w.branch,
        "role": w.role,
        "questions": questions,
        "answered_count": sum(1 for q in questions if q["selected_option_ids"]),
        "remaining_estimate": w.remaining_estimate,
        "is_complete": w.is_complete,
        "feedback": None,
    }


async def submit_answer(
    db: AsyncSession,
    funnel: Funnel,
    guest_session_id: UUID,
    code: str,
    option_ids: List[str],
) -> Dict[str, Any]:
    """Принять ответ на вопрос, который сейчас на пути человека.

    Raises:
        DomainError 404: вопроса нет на текущем пути (скрыт условием или чужой ветки).
        DomainError 400: вариант не из видимых или одиночный выбор с несколькими.
    """
    task_ids = await _task_ids(db, funnel)
    answers = await _answers(db, guest_session_id, task_ids)
    params = await _params(db, funnel, guest_session_id)
    w = engine.walk(funnel.spec, answers, params)
    step = next((s for s in w.steps if s.code == code), None)
    if step is None or code not in task_ids:
        raise DomainError(
            detail="Этого вопроса нет на вашем пути.", status_code=404, payload={"code": code}
        )
    ctx = engine.Context(answers=dict(w.path_answers), role=w.role)
    visible = {o["id"] for o in engine.visible_options(step.question, ctx)}
    qtype = (step.question.get("task_content") or {}).get("type", "SC_Qw")
    if (
        not option_ids
        or not set(option_ids) <= visible
        or (qtype != "MC_Qw" and len(option_ids) != 1)
    ):
        raise DomainError(detail="Такой вариант ответа выбрать нельзя.", status_code=400)

    check = step.question.get("check") or {}
    is_correct = (option_ids == [check["correct_option"]]) if check.get("correct_option") else None
    db.add(
        GuestAttempt(
            guest_session_id=guest_session_id,
            task_id=task_ids[code],
            answer_json={"type": qtype, "response": {"selected_option_ids": option_ids}},
            # У опросного вопроса верного ответа нет — NULL, не False.
            is_correct=is_correct,
            scale_scores={},
        )
    )
    await db.flush()
    await save_progress(db, funnel, guest_session_id)
    state = await get_state(db, funnel, guest_session_id)
    answered = next((q for q in state["questions"] if q["code"] == code), None)
    state["feedback"] = answered["feedback"] if answered else None
    return state


async def save_progress(
    db: AsyncSession, funnel: Funnel, guest_session_id: UUID
) -> engine.Evaluation:
    """Обновить ветку, роль и итог прохождения — для замеров по веткам."""
    evaluation = await evaluate_session(db, funnel, guest_session_id)
    progress = await _progress(db, guest_session_id, funnel.course.id)
    if progress is None:
        progress = QuizFunnelProgress(
            guest_session_id=guest_session_id, course_id=funnel.course.id, params={}
        )
        db.add(progress)
    now = datetime.now(timezone.utc)
    progress.branch = evaluation.walk.branch
    progress.role = evaluation.walk.role
    progress.outcome_code = evaluation.outcome.get("code") if evaluation.outcome else None
    progress.completed_at = (progress.completed_at or now) if evaluation.outcome else None
    progress.updated_at = now
    await db.flush()
    return evaluation


# ── итог ────────────────────────────────────────────────────────────────────

def _by_role(outcome: Mapping[str, Any], key: str, role: Optional[str]) -> Any:
    """Поле итога голосом роли: `<key>_by_role[role]`, иначе общее."""
    by_role = outcome.get(f"{key}_by_role") or {}
    return by_role[role] if role and role in by_role else outcome.get(key)


def registration_open(
    spec: Mapping[str, Any], branch: str, ctx: Optional[engine.Context] = None
) -> bool:
    """Открыта ли регистрация из итога.

    Закрыта, если ветка в ``registration_closed_branches`` (по умолчанию
    ``parent`` — до текста согласия) или сработало ``registration_closed_if``
    (например, подросток 11–13: регистрирует только родитель).
    """
    closed = spec.get("registration_closed_branches")
    closed = DEFAULT_REGISTRATION_CLOSED if closed is None else closed
    if branch in closed:
        return False
    condition = spec.get("registration_closed_if")
    if condition is not None and ctx is not None and engine.eval_condition(condition, ctx):
        return False
    return True


def contact_url(title: str) -> str:
    """Переписка с заполненным сообщением от лица человека."""
    message = (
        f"Здравствуйте! Прошёл квиз, итог — «{title}». Хочу записаться на пробное занятие."
    )
    return f"https://t.me/{_settings.quiz_contact_tg}?text={quote(message)}"


def pdf_url(spec: Mapping[str, Any], branch: str) -> Optional[str]:
    """Путь PDF ветки: имя файла из спецификации под базовым путём настройки."""
    name = (spec.get("pdf") or {}).get(branch)
    if not name:
        return None
    if name.startswith(("http://", "https://", "/")):
        return name
    return f"{_settings.quiz_funnel_pdf_base.rstrip('/')}/{name.rsplit('/', 1)[-1]}"


def _buttons(
    outcome: Mapping[str, Any], reg_open: bool, bot_url: Optional[str], contact: str
) -> List[Dict[str, Any]]:
    """Кнопки итога. Регистрационные — только при открытой регистрации, бот —
    только при настроенном боте и PDF; ``url`` заполняется там, где его знает
    сервер (бот, переписка), остальное SPW строит по ``kind``/``target``."""
    out: List[Dict[str, Any]] = []
    has_share = any(b.get("kind") == "share_parent_link" for b in outcome.get("buttons") or [])
    for button in outcome.get("buttons") or []:
        kind = button.get("kind")
        if kind in REGISTRATION_BUTTON_KINDS and not reg_open:
            # Регистрирует взрослый: вместо входа — ссылка родителям (одна).
            if has_share:
                continue
            has_share = True
            button = {**button, "kind": "share_parent_link", "text": SHARE_PARENT_TEXT}
            kind = "share_parent_link"
        url: Optional[str] = None
        if kind == "telegram_bot":
            if not bot_url:
                continue
            url = bot_url
        elif kind == "contact":
            url = contact
        out.append({
            "id": button.get("id"),
            "text": button.get("text"),
            "kind": kind,
            "target": button.get("target"),
            "url": url,
        })
    return out


async def get_result(
    db: AsyncSession, funnel: Funnel, guest_session_id: Optional[UUID]
) -> Dict[str, Any]:
    """Видимая гостю часть итога и кнопки. Не пройден — ``is_complete=false``."""
    empty = {"quiz_uid": funnel.course.course_uid, "is_complete": False}
    if guest_session_id is None:
        return empty
    evaluation = await save_progress(db, funnel, guest_session_id)
    outcome = evaluation.outcome
    if outcome is None:
        return empty
    branch, role = evaluation.walk.branch, evaluation.walk.role
    reg_open = registration_open(funnel.spec, branch, evaluation.ctx)
    bot_url = None
    if pdf_url(funnel.spec, branch):
        bot_url = bot_start_url(await ensure_bot_token(db, guest_session_id, funnel.course.id))
    title = _by_role(outcome, "title", role) or ""
    contact = contact_url(title)
    return {
        "quiz_uid": funnel.course.course_uid,
        "is_complete": True,
        "outcome_code": outcome.get("code"),
        "branch": branch,
        "role": role,
        "title": title,
        "visible": list(_by_role(outcome, "visible", role) or []),
        "buttons": _buttons(outcome, reg_open, bot_url, contact),
        "target_course_uid": outcome.get("target_course_uid"),
        "registration_enabled": reg_open,
        "bot_start_url": bot_url,
        "contact_url": contact,
    }


def full_breakdown(funnel: Funnel, evaluation: engine.Evaluation) -> Dict[str, Any]:
    """Полный разбор после регистрации: видимая часть, уточнения, разборы
    мини-проверок и абзацы шаблона полного разбора, если контент их задал
    (`spec.full_templates[<full_template>]` — список или {роль: список})."""
    outcome = evaluation.outcome or {}
    role = evaluation.walk.role
    template = (funnel.spec.get("full_templates") or {}).get(outcome.get("full_template") or "")
    if isinstance(template, dict):
        template = template.get(role or "") or template.get("default")
    checks = []
    for step in evaluation.walk.steps:
        if not step.question.get("check"):
            continue
        feedback = engine.check_feedback(
            funnel.spec, step.code, evaluation.ctx.answers.get(step.code) or []
        )
        if feedback:
            checks.append({"stem": engine.stem_for(step.question, role), "feedback": feedback})
    return {
        "title": _by_role(outcome, "title", role) or "",
        "visible": list(_by_role(outcome, "visible", role) or []),
        "modifiers": [m["text"] for m in evaluation.modifiers if m.get("text")],
        "checks": checks,
        "full": list(template or []),
    }


# ── бот ─────────────────────────────────────────────────────────────────────

async def ensure_bot_token(db: AsyncSession, guest_session_id: UUID, course_id: int) -> str:
    """Токен стартовой ссылки бота для пары (сессия, квиз) — один на пару.

    В токене нет ни id сессии, ни контактов: ссылку пересылают, и по ней не
    должно читаться ничего, кроме «это гость такого-то квиза».
    """
    existing = (
        await db.execute(
            select(QuizFunnelBotLead).where(
                QuizFunnelBotLead.guest_session_id == guest_session_id,
                QuizFunnelBotLead.quiz_course_id == course_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.start_token
    row = QuizFunnelBotLead(
        start_token=secrets.token_urlsafe(18),  # 24 символа [A-Za-z0-9_-]
        guest_session_id=guest_session_id,
        quiz_course_id=course_id,
    )
    db.add(row)
    await db.flush()
    return row.start_token


def bot_start_url(token: str) -> Optional[str]:
    """Стартовая ссылка бота или None, если ник бота не настроен."""
    username = _settings.quiz_funnel_bot_username.strip().lstrip("@")
    if not username:
        return None
    return f"https://t.me/{username}?start={BOT_START_PREFIX}{token}"


# ── замеры ──────────────────────────────────────────────────────────────────

#: Шаги по веткам: открыли → ответили хоть раз → итог → регистрация → первое
#: решённое задание → бот → пробное → подтверждённая оплата после регистрации.
_SITE_FUNNEL_SQL = """
WITH p AS (
    SELECT pr.guest_session_id, pr.course_id, COALESCE(pr.branch, 'root') AS branch,
           pr.completed_at
      FROM quiz_funnel_progress pr
      JOIN guest_session gs ON gs.id = pr.guest_session_id
     WHERE pr.course_id = :course_id
       AND (CAST(:utm_source AS text) IS NULL OR gs.attribution->>'utm_source' = :utm_source)
       AND (CAST(:utm_campaign AS text) IS NULL OR gs.attribution->>'utm_campaign' = :utm_campaign)
),
answered AS (
    SELECT DISTINCT ga.guest_session_id
      FROM guest_attempt ga JOIN tasks t ON t.id = ga.task_id
     WHERE t.course_id = :course_id
),
reg AS (
    SELECT p.branch, l.linked_student_id AS user_id, l.updated_at
      FROM p JOIN leads l ON l.guest_session_id = p.guest_session_id
                         AND l.quiz_course_id = p.course_id
     WHERE l.linked_student_id IS NOT NULL
),
bot AS (
    SELECT p.branch, bl.started_at, bl.trial_requested_at
      FROM p JOIN quiz_funnel_bot_lead bl ON bl.guest_session_id = p.guest_session_id
                                         AND bl.quiz_course_id = p.course_id
)
SELECT b.branch,
       (SELECT count(*) FROM p WHERE p.branch = b.branch) AS opened,
       (SELECT count(*) FROM p JOIN answered a USING (guest_session_id)
         WHERE p.branch = b.branch) AS started,
       (SELECT count(*) FROM p WHERE p.branch = b.branch AND p.completed_at IS NOT NULL) AS completed,
       (SELECT count(*) FROM reg WHERE reg.branch = b.branch) AS registered,
       (SELECT count(*) FROM reg WHERE reg.branch = b.branch AND EXISTS (
            SELECT 1 FROM task_results tr
             WHERE tr.user_id = reg.user_id AND tr.is_correct IS TRUE)) AS first_solved,
       (SELECT count(*) FROM bot WHERE bot.branch = b.branch
          AND bot.started_at IS NOT NULL) AS bot_started,
       (SELECT count(*) FROM bot WHERE bot.branch = b.branch
          AND bot.trial_requested_at IS NOT NULL) AS trial_requested,
       (SELECT count(DISTINCT reg.user_id) FROM reg
          JOIN student_payment sp ON sp.student_id = reg.user_id
         WHERE reg.branch = b.branch AND sp.status = 'confirmed'
           AND sp.created_at >= reg.updated_at) AS paid
  FROM (SELECT DISTINCT branch FROM p) b
 ORDER BY b.branch
"""


async def get_site_funnel(
    db: AsyncSession,
    quiz_uid: str,
    utm_source: Optional[str] = None,
    utm_campaign: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Шаги воронки по веткам квиза. None — квиза-воронки нет."""
    course_id = (
        await db.execute(
            select(Courses.id)
            .join(QuizFunnelSpec, QuizFunnelSpec.course_id == Courses.id)
            .where(Courses.course_uid == quiz_uid)
        )
    ).scalar_one_or_none()
    if course_id is None:
        return None
    rows = await db.execute(
        text(_SITE_FUNNEL_SQL),
        {"course_id": course_id, "utm_source": utm_source, "utm_campaign": utm_campaign},
    )
    return [dict(r) for r in rows.mappings()]


async def submit_lead(
    db: AsyncSession,
    funnel: Funnel,
    guest_session_id: UUID,
    contact: str,
    full_name: Optional[str],
    kind: str = "trial",
) -> int:
    """Заявка с итога квиза: контакт + ветка, роль, итог, метки.

    ``kind``: ``trial`` — запись на пробное; ``waitlist`` — лист ожидания
    (итог без курса: «группу собираю»).

    Одна заявка на (сессию, квиз): повторная отправка обновляет контакт, а
    регистрация и бот потом дописывают в ту же запись.

    :raises DomainError 409: квиз не пройден — заявке не к чему привязаться.
    """
    evaluation = await save_progress(db, funnel, guest_session_id)
    outcome = evaluation.outcome
    if outcome is None:
        raise DomainError(detail="Сначала пройдите квиз до конца.", status_code=409)
    session = await db.get(GuestSession, guest_session_id)
    attribution = {
        **((session.attribution or {}) if session else {}),
        "branch": evaluation.walk.branch,
        "role": evaluation.walk.role,
        "outcome": outcome.get("code"),
        ("waitlist" if kind == "waitlist" else "trial_requested"): True,
    }
    what = "лист ожидания" if kind == "waitlist" else "запись на пробное"
    note = (
        f"Квиз «{funnel.course.title}», ветка {evaluation.walk.branch}, итог "
        f"{outcome.get('code')}: {what} с сайта."
    )
    lead_id, _ = await lead_magnet_service.upsert_lead(
        db,
        course=funnel.course,
        guest_session_id=guest_session_id,
        contact=contact,
        full_name=full_name,
        note=note,
    )
    lead = await lead_magnet_service.find_lead(db, guest_session_id, funnel.course.id)
    if lead is not None:
        lead.attribution = {**(lead.attribution or {}), **attribution}
    await db.flush()
    return lead_id
