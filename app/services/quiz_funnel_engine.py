"""Движок правил квиза-воронки (tsk-1139, итерация 2) — чистые функции, без БД.

Спецификация квиза приходит из контента как есть (`quiz-razvilka.lms.json`):

* ``branches`` — {имя ветки: [вопрос]}; вопрос — ``code``, ``task_content``,
  необязательные ``show_if`` (условие показа), ``goto`` ({вариант: ветка} — переход),
  ``stem_by_role`` ({роль: формулировка}), ``check`` (мини-проверка),
  ``options_show_if`` ({вариант: условие} — показ варианта);
* ``link_params.skip`` — {"branch=ege&role=parent": [коды]} — что пропускается при
  параметрах ссылки; ``dir=*`` — любое значение параметра;
* ``derived`` — {признак: [{value, if}]} — первое подходящее правило сверху;
* ``outcomes`` — [{code, branch, if, title, visible, buttons, target_course_uid,
  full_template}] — первый подходящий итог своей ветки сверху, ``if: "else"`` — иначе;
* ``modifiers`` — [{code, branch, if, text}] — все подходящие абзацы-уточнения;
* ``check_feedback`` — {код: {вариант: разбор}}.

Условие — словарь: ``{"all": [...]}``, ``{"any": [...]}``, ``{"not": {...}}`` или
{ключ: [значения]}, где ключ — код вопроса (выбран хотя бы один из вариантов),
``role`` или имя производного признака. Несколько ключей в одном словаре — «и».
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

#: Ветки, имя которых само задаёт роль (голос) для дальнейших вопросов.
ROLE_BRANCHES = ("parent", "teen")
ROOT_BRANCH = "root"
#: Защита от петель goto в спецификации.
_MAX_BRANCH_HOPS = 10


@dataclass
class Context:
    """Всё, от чего зависят условия: ответы, роль, производные признаки."""

    answers: Mapping[str, Sequence[str]]
    role: Optional[str] = None
    derived: Dict[str, str] = field(default_factory=dict)


@dataclass
class PathStep:
    """Вопрос на пути человека."""

    branch: str
    code: str
    question: Dict[str, Any]


@dataclass
class Walk:
    """Результат прохода по спецификации с текущими ответами."""

    steps: List[PathStep]
    branch: str
    role: Optional[str]
    is_complete: bool
    prefilled: Dict[str, List[str]]
    remaining_estimate: int


def eval_condition(cond: Any, ctx: Context) -> bool:
    """Проверить условие спецификации на контексте.

    Неизвестная форма условия — False с предупреждением: лучше не показать
    вопрос или итог, чем показать не тому человеку.
    """
    if cond is None:
        return True
    if cond == "else":
        return True
    if not isinstance(cond, dict):
        logger.warning("quiz_funnel_engine: неизвестное условие %r", cond)
        return False
    for key, value in cond.items():
        if key == "all":
            if not all(eval_condition(c, ctx) for c in value):
                return False
        elif key == "any":
            if not any(eval_condition(c, ctx) for c in value):
                return False
        elif key == "not":
            if eval_condition(value, ctx):
                return False
        elif key == "role":
            if ctx.role not in value:
                return False
        elif key in ctx.derived:
            if ctx.derived[key] not in value:
                return False
        else:
            chosen = set(ctx.answers.get(key) or ())
            if not chosen & set(value):
                return False
    return True


def _skip_codes(spec: Mapping[str, Any], params: Mapping[str, str]) -> List[str]:
    """Коды вопросов, пропускаемых при данных параметрах ссылки."""
    skip = ((spec.get("link_params") or {}).get("skip")) or {}
    codes: List[str] = []
    for rule, rule_codes in skip.items():
        ok = True
        for pair in rule.split("&"):
            name, _, expected = pair.partition("=")
            actual = params.get(name)
            if not actual or (expected != "*" and actual != expected):
                ok = False
                break
        if ok:
            codes.extend(rule_codes)
    return codes


def valid_params(spec: Mapping[str, Any], raw: Mapping[str, Optional[str]]) -> Dict[str, str]:
    """Оставить только параметры ссылки, которые знает спецификация."""
    allowed = (spec.get("link_params") or {})
    clean: Dict[str, str] = {}
    for name in ("branch", "role", "dir"):
        value = raw.get(name)
        if value and value in (allowed.get(name) or []):
            clean[name] = value
    return clean


def _prefill(question: Mapping[str, Any], params: Mapping[str, str]) -> Optional[List[str]]:
    """Ответ за человека на пропущенный вопрос: вариант с id на ``_<значение параметра>``.

    Так ``dir=qa`` отвечает на «Какое направление ближе?» вариантом ``a_dir_qa``.
    """
    options = (question.get("task_content") or {}).get("options") or []
    for value in params.values():
        for opt in options:
            if str(opt.get("id", "")).endswith(f"_{value}"):
                return [opt["id"]]
    return None


def walk(
    spec: Mapping[str, Any],
    answers: Mapping[str, Sequence[str]],
    params: Mapping[str, str],
) -> Walk:
    """Пройти путь человека: видимые вопросы до первого неотвеченного.

    Путь зависит от ответов (goto, show_if), поэтому дальше первого
    неотвеченного вопроса его не видно — это и есть текущий вопрос.
    """
    branches: Mapping[str, List[Dict[str, Any]]] = spec.get("branches") or {}
    branch = params.get("branch") if params.get("branch") in branches else ROOT_BRANCH
    role = params.get("role") or (branch if branch in ROLE_BRANCHES else None)
    skip = set(_skip_codes(spec, params))
    merged: Dict[str, List[str]] = {k: list(v) for k, v in answers.items() if v}
    prefilled: Dict[str, List[str]] = {}
    steps: List[PathStep] = []

    for _hop in range(_MAX_BRANCH_HOPS):
        jumped = False
        questions = branches.get(branch) or []
        for index, question in enumerate(questions):
            code = question.get("code")
            if not code:
                continue
            ctx = Context(answers=merged, role=role)
            if code in skip:
                filled = _prefill(question, params)
                if filled and code not in merged:
                    merged[code] = filled
                    prefilled[code] = filled
                target = _goto(question, merged.get(code))
            else:
                if question.get("show_if") is not None and not eval_condition(
                    question["show_if"], ctx
                ):
                    continue
                steps.append(PathStep(branch=branch, code=code, question=question))
                if not merged.get(code):
                    remaining = sum(
                        1 for q in questions[index + 1:] if q.get("code") not in skip
                    )
                    return Walk(steps, branch, role, False, prefilled, remaining)
                target = _goto(question, merged.get(code))
            if target and target in branches and target != branch:
                if branch == ROOT_BRANCH and target in ROLE_BRANCHES:
                    role = role or target
                elif role is None and branch in ROLE_BRANCHES:
                    role = branch
                branch = target
                jumped = True
                break
        if not jumped:
            return Walk(steps, branch, role, True, prefilled, 0)
    logger.warning("quiz_funnel_engine: превышено число переходов goto — петля в спецификации")
    return Walk(steps, branch, role, True, prefilled, 0)


def _goto(question: Mapping[str, Any], chosen: Optional[Sequence[str]]) -> Optional[str]:
    """Ветка перехода по выбранному варианту, если он задан."""
    goto = question.get("goto") or {}
    for opt in chosen or ():
        if opt in goto:
            return goto[opt]
    return None


def compute_derived(spec: Mapping[str, Any], ctx: Context) -> Dict[str, str]:
    """Производные признаки: по каждому — первое подходящее правило сверху."""
    result: Dict[str, str] = {}
    for name, rules in (spec.get("derived") or {}).items():
        probe = Context(answers=ctx.answers, role=ctx.role, derived=dict(result))
        for rule in rules:
            if eval_condition(rule.get("if"), probe):
                result[name] = rule.get("value")
                break
    return result


def pick_outcome(
    spec: Mapping[str, Any], branch: str, ctx: Context
) -> Optional[Dict[str, Any]]:
    """Первый подходящий итог ветки сверху; ``else`` — запасной."""
    for outcome in spec.get("outcomes") or []:
        if outcome.get("branch") != branch:
            continue
        if eval_condition(outcome.get("if"), ctx):
            return outcome
    return None


def pick_modifiers(spec: Mapping[str, Any], branch: str, ctx: Context) -> List[Dict[str, Any]]:
    """Все подходящие уточнения ветки, в порядке спецификации."""
    return [
        m
        for m in spec.get("modifiers") or []
        if m.get("branch") == branch and eval_condition(m.get("if"), ctx)
    ]


def check_feedback(spec: Mapping[str, Any], code: str, chosen: Sequence[str]) -> Optional[str]:
    """Разбор мини-проверки для выбранного варианта."""
    by_option = (spec.get("check_feedback") or {}).get(code) or {}
    for opt in chosen:
        if opt in by_option:
            return by_option[opt]
    return None


def stem_for(question: Mapping[str, Any], role: Optional[str]) -> str:
    """Формулировка вопроса голосом роли (родителю — «ваш ребёнок»)."""
    by_role = question.get("stem_by_role") or {}
    if role and role in by_role:
        return by_role[role]
    return (question.get("task_content") or {}).get("stem", "")


def visible_options(question: Mapping[str, Any], ctx: Context) -> List[Dict[str, Any]]:
    """Варианты ответа, видимые в этом контексте (``options_show_if``)."""
    rules = question.get("options_show_if") or {}
    return [
        opt
        for opt in (question.get("task_content") or {}).get("options") or []
        if opt.get("id") not in rules or eval_condition(rules[opt["id"]], ctx)
    ]


@dataclass
class Evaluation:
    """Итог прохождения: ветка, роль, признаки, итог и уточнения."""

    walk: Walk
    ctx: Context
    outcome: Optional[Dict[str, Any]]
    modifiers: List[Dict[str, Any]]


def evaluate(
    spec: Mapping[str, Any],
    answers: Mapping[str, Sequence[str]],
    params: Mapping[str, str],
) -> Evaluation:
    """Полный расчёт: путь, признаки, итог (только у пройденного до конца)."""
    w = walk(spec, answers, params)
    merged = {**{k: list(v) for k, v in answers.items() if v}, **w.prefilled}
    ctx = Context(answers=merged, role=w.role)
    ctx.derived = compute_derived(spec, ctx)
    if not w.is_complete:
        return Evaluation(w, ctx, None, [])
    return Evaluation(
        w, ctx, pick_outcome(spec, w.branch, ctx), pick_modifiers(spec, w.branch, ctx)
    )


#: Наборы параметров ссылки, на которых проверяется полнота правил.
def _param_sets(spec: Mapping[str, Any]) -> List[Dict[str, str]]:
    link = spec.get("link_params") or {}
    sets: List[Dict[str, str]] = [{}]
    for branch in link.get("branch") or []:
        sets.append({"branch": branch})
        for role in link.get("role") or []:
            sets.append({"branch": branch, "role": role})
        for direction in link.get("dir") or []:
            sets.append({"branch": branch, "dir": direction})
    return [valid_params(spec, s) for s in sets]


@dataclass
class Coverage:
    """Итог проверки полноты: пути без итога и итоги, до которых не дойти."""

    runs: int
    without_outcome: int
    unreached_outcomes: List[str]
    samples_without_outcome: List[Dict[str, Any]]


def coverage_check(spec: Mapping[str, Any], runs_per_params: int = 500, seed: int = 1) -> Coverage:
    """Случайные прохождения при всех параметрах ссылки: у каждого пройденного
    пути должен быть итог, каждый итог должен быть достижим.

    Проверка вероятностная (перебор всех путей слишком велик), но на реальном
    квизе 500 прогонов на набор параметров находят все 25 итогов.
    """
    import random

    rnd = random.Random(seed)
    hit: set = set()
    missing = 0
    samples: List[Dict[str, Any]] = []
    total = 0
    for params in _param_sets(spec):
        for _ in range(runs_per_params):
            answers: Dict[str, List[str]] = {}
            for _step in range(100):
                w = walk(spec, answers, params)
                if w.is_complete:
                    break
                step = w.steps[-1]
                ctx = Context(answers={**answers, **w.prefilled}, role=w.role)
                options = [o["id"] for o in visible_options(step.question, ctx)]
                if not options:
                    break
                if (step.question.get("task_content") or {}).get("type") == "MC_Qw":
                    answers[step.code] = rnd.sample(options, rnd.randint(1, len(options)))
                else:
                    answers[step.code] = [rnd.choice(options)]
            total += 1
            evaluation = evaluate(spec, answers, params)
            if evaluation.outcome is None:
                missing += 1
                if len(samples) < 5:
                    samples.append({"params": params, "answers": answers,
                                    "branch": evaluation.walk.branch})
            else:
                hit.add(evaluation.outcome.get("code"))
    unreached = [o.get("code") for o in spec.get("outcomes") or [] if o.get("code") not in hit]
    return Coverage(total, missing, unreached, samples)
