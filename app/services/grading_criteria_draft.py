"""Черновик критериев оценивания по тексту задания (tsk-590).

**Зачем.** Поле критериев выкачено чипом tsk-605, и с тех пор оно заполнено у
нуля заданий из 279: писать критерии должен методист-человек, и задача стояла
на этом две недели. Черновик не заменяет методиста — он меняет его работу с
«написать с нуля» на «прочитать и поправить».

**Чего черновик НЕ делает.** Он не участвует в оценке ответа ученика.
Записанные здесь критерии несут `status="draft"`, а предикат допуска
(`SolutionRules.is_usable`, `ai_check_policy`) считает критериями только
подтверждённые человеком. Иначе получилась бы худшая из возможных развязок:
незачёт ученику по правилам, которых никто не читал.

**Что показал замер (12 живых заданий прода, `gpt-5.4-mini`, 2026-08-20).**

- Там, где методист УЖЕ написал «Критерий приёмки» в тексте условия (124
  задания из 279), модель раскладывает его на проверяемые пункты — заготовка
  годна почти без правки.
- Там, где ответ — суждение (описание, план, объяснение), заготовка тоже
  осмысленная.
- Там, где ученик должен прислать вычисленное значение или файл, заготовка
  ОПАСНА: для заданий «напишите программу» модель уверенно пишет критерии
  проверки программы, хотя ученик сдаёт короткий ответ. Текст складный, а
  проверять по нему нельзя.

Поэтому предупреждение о классе задания ставит КОД (см. `_warning_for`), а не
модель: просьба «скажи, если не можешь пересчитать» в промпте даёт обратный
результат — молчит там, где нужна, и срабатывает там, где не нужна (тот же
эффект зафиксирован замером tsk-605 §5).
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.schemas.solution_rules import (
    CRITERIA_MAX_ITEMS,
    CRITERION_MAX_LENGTH,
    CRITERION_MIN_LENGTH,
    GradingCriteria,
    SolutionRules,
)
from app.services.llm import client as llm_client
from app.services.llm.contracts import Budget, LLMMessage

logger = logging.getLogger(__name__)

#: Сколько символов условия отдаём модели. Условия длиннее встречаются
#: (самое длинное на проде — около 9 000 знаков), но хвост там обычно занимают
#: примеры ввода-вывода, а не требования к ответу.
STEM_LIMIT = 6000

#: Назначение вызова для учёта расхода (`llm.usage`). Отдельное от `judge` и
#: `tutor`: это работа методиста, а не ученика, и в отчёте по расходу она
#: должна быть видна отдельной строкой.
LLM_PURPOSE = "grading_criteria_draft"


class DraftError(RuntimeError):
    """Черновик составить не удалось — вызывающий решает, что показать методисту."""


@dataclass(frozen=True)
class DraftResult:
    """Готовый черновик и учётные данные вызова."""

    criteria: GradingCriteria
    model: str
    tokens_in: int
    tokens_out: int


_SYSTEM_PROMPT = """Ты методист онлайн-школы информатики. Составь критерии оценивания
для задания, у которого нет эталонного ответа.

Критерии читает проверяющий — человек или программа. Он видит только условие задания
и ответ ученика; решить он должен одно: зачесть ответ или нет.

Что писать:
- must: что ОБЯЗАТЕЛЬНО должно быть в ответе, чтобы его зачесть. От 2 до 5 пунктов.
  Каждый пункт — проверяемое требование, а не тема.
  Плохо: «Понимание темы». Хорошо: «Названы минимум два разных шага цепочки».
- accept: что считать равноценным и всё равно засчитывать — другие формулировки,
  другой верный способ решения. От 0 до 4 пунктов.
- reject: что НЕ засчитывать, даже если выглядит близким: пересказ условия без
  ответа, общие слова, один пункт вместо двух. От 0 до 4 пунктов.
- notes: короткое пояснение проверяющему или null.

Жёсткие правила:
1. Если в условии УЖЕ написан критерий приёмки — разложи именно его на отдельные
   проверяемые пункты. Ничего не добавляй сверх него от себя.
2. Критерии пишутся под ТОТ ответ, который ученик реально сдаёт (он указан ниже),
   а не под работу целиком. Если ученик сдаёт одно число, требовать в must
   «приведён алгоритм» нельзя.
3. Каждый пункт — от {min_len} до {max_len} символов, без нумерации и маркеров.
   Пункты внутри списка не повторяются.
4. Ответь ТОЛЬКО JSON-объектом вида:
   {{"must": [...], "accept": [...], "reject": [...], "notes": "..." | null}}
"""

#: Что ученик реально сдаёт — по типу задания. Замер показал, что без этой
#: строки модель пишет критерии под воображаемый артефакт: для задания
#: «напишите программу» — под разбор кода, хотя поле ответа принимает строку.
_ANSWER_SHAPE: dict[str, str] = {
    "SA": "одну короткую строку — сам ответ, без пояснений",
    "SA_COM": "короткий ответ одной строкой плюс свободный комментарий к нему",
    "TBL_COM": "заполненную таблицу плюс свободный комментарий",
    "TA": "развёрнутый текст в свободной форме",
}


def clean_stem(raw: Any) -> str:
    """Привести условие к читаемому тексту: снять разметку и мягкие переносы.

    Условия приходят и чистым текстом, и HTML-фрагментом (наследие импорта с
    внешних сайтов). Мягкие переносы `\\u00ad` — отдельная беда стемов ЕГЭ:
    они невидимы, но рвут слова внутри промпта.

    :param raw: значение `task_content.stem`.
    :returns: текст без разметки, схлопнутыми пробелами, обрезанный до
        `STEM_LIMIT` символов.
    """
    if not isinstance(raw, str):
        return ""
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("­", "").replace("​", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:STEM_LIMIT]


def _warning_for(rules: SolutionRules, task_type: Optional[str]) -> Optional[str]:
    """Предупреждение о классе задания — от кода, а не от модели.

    Модель на вопрос «можешь ли ты это проверить» отвечает неверно в обе
    стороны (замер tsk-605 §5 и повторный замер tsk-590), поэтому оговорки
    ставятся по фактам из `solution_rules`, а не по её самооценке.

    :param rules: правила задания.
    :param task_type: тип задания из `task_content.type`.
    :returns: текст предупреждения либо None.
    """
    if rules.requires_attachment:
        return (
            "Ответ подтверждается файлом, которого проверяющая модель не видит: "
            "критерии машинную проверку здесь не заменят, задание остаётся "
            "человеку."
        )
    if task_type in ("SA", "SA_COM", "TBL_COM"):
        return (
            "Ученик сдаёт короткий ответ. Если верное значение вычисляется по "
            "данным, которых нет в условии, критериев мало — нужен эталон "
            "(accepted_answers)."
        )
    return None


def build_messages(
    *, stem: str, task_type: Optional[str], course_title: Optional[str], title: Optional[str]
) -> list[LLMMessage]:
    """Собрать промпт. Вынесено отдельно, чтобы его можно было проверить тестом."""
    shape = _ANSWER_SHAPE.get(task_type or "", "текстовый ответ в свободной форме")
    user = (
        f"Курс: {course_title or '—'}\n"
        f"Название задания: {title or '—'}\n"
        f"Ученик сдаёт: {shape}\n\n"
        f"Условие задания:\n{stem}\n"
    )
    system = _SYSTEM_PROMPT.format(
        min_len=CRITERION_MIN_LENGTH, max_len=CRITERION_MAX_LENGTH
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def _parse_payload(text: str) -> dict:
    """Разобрать ответ модели в словарь.

    Модель просят вернуть голый JSON, но провайдеры иногда оборачивают его в
    ```json-блок либо добавляют строку до. Поэтому при неудаче ищем первый
    объект в тексте, а не роняем весь черновик.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise DraftError("модель вернула не JSON")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise DraftError(f"ответ модели не разбирается: {exc}") from exc
    if not isinstance(payload, dict):
        raise DraftError("модель вернула не объект")
    return payload


def _as_items(value: Any, *, limit: int) -> list[str]:
    """Привести список пунктов к виду, который примет валидатор схемы.

    Короткие огрызки («ок», «верно») отбрасываются здесь, а не роняют весь
    черновик: остальные пункты при этом остаются полезными методисту.
    """
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            continue
        item = " ".join(raw.split())
        if not CRITERION_MIN_LENGTH <= len(item) <= CRITERION_MAX_LENGTH:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        if len(items) >= limit:
            break
    return items


async def generate(
    *,
    task_content: Any,
    solution_rules: Any,
    course_title: Optional[str] = None,
    model: Optional[str] = None,
) -> DraftResult:
    """Составить черновик критериев для одного задания.

    :param task_content: содержимое задания (`tasks.task_content`) — нужны
        `stem`, `type`, `title`.
    :param solution_rules: правила задания — для предупреждения о классе.
    :param course_title: название курса, добавляется в промпт как контекст.
    :param model: явная модель; по умолчанию — цепочка `LLM_JUDGE_MODELS`.
    :returns: черновик со `status="draft"` и учётные данные вызова.
    :raises DraftError: пустое условие, сбой вызова, неразбираемый или
        бессодержательный ответ модели.
    """
    content = task_content if isinstance(task_content, dict) else {}
    stem = clean_stem(content.get("stem"))
    if not stem:
        raise DraftError("у задания пустое условие — составлять критерии не по чему")

    task_type = content.get("type")
    messages = build_messages(
        stem=stem,
        task_type=task_type,
        course_title=course_title,
        title=content.get("title"),
    )
    try:
        result = await llm_client.complete(
            messages,
            model=model,
            temperature=0.0,
            max_tokens=1200,
            purpose=LLM_PURPOSE,
            budget=Budget.BATCH,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 — таксономия ошибок клиента шире DraftError
        logger.warning("tsk-590: черновик критериев не составлен: %s", exc)
        raise DraftError(f"вызов модели не удался: {exc}") from exc

    payload = _parse_payload(result.text)
    must = _as_items(payload.get("must"), limit=CRITERIA_MAX_ITEMS)
    if not must:
        raise DraftError(
            "модель не назвала ни одного обязательного требования — "
            "такой черновик методисту бесполезен"
        )

    notes_raw = payload.get("notes")
    notes = " ".join(notes_raw.split()) if isinstance(notes_raw, str) and notes_raw.strip() else None

    rules = _rules_or_default(solution_rules)
    criteria = GradingCriteria(
        must=must,
        accept=_as_items(payload.get("accept"), limit=CRITERIA_MAX_ITEMS),
        reject=_as_items(payload.get("reject"), limit=CRITERIA_MAX_ITEMS),
        notes=notes,
        status="draft",
        origin="ai_draft",
        generated_by_model=result.model,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        draft_warning=_warning_for(rules, task_type),
    )
    return DraftResult(
        criteria=criteria,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )


def _rules_or_default(solution_rules: Any) -> SolutionRules:
    """Правила задания в виде схемы; нечитаемые — как пустые.

    Черновик составляется по УСЛОВИЮ, поэтому битые правила его не отменяют:
    они влияют только на текст предупреждения. Ронять здесь значило бы лишить
    методиста заготовки ровно там, где с заданием и так что-то не так.
    """
    if isinstance(solution_rules, SolutionRules):
        return solution_rules
    if isinstance(solution_rules, dict):
        try:
            return SolutionRules.model_validate(solution_rules)
        except Exception:  # noqa: BLE001 — предупреждение не стоит падения
            logger.info("tsk-590: solution_rules не разбирается, черновик без оговорки")
    return SolutionRules(max_score=1)
