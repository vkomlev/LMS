"""Схема сценария интерактивного тренажёра `trainer-scenario/1` (tsk-1247).

Формат описан в `docs/design/trainers/scenarios/README.md` §1. Сценарий —
данные, а не код: его исполняет движок SPW, LMS только хранит его в
`materials.content.scenario` и проверяет при публикации, чтобы битый сценарий
не дошёл до ученика (бриф tsk-1247, Р2).

Что проверяется здесь (структура и ссылки), а чего нет:
- структура верхнего уровня, уровней, шагов и вариантов ответа — с отказом на
  незнакомых ключах (опечатка в имени поля молча выключила бы шаг);
- ссылки на коды ошибок: каждый `misconception` в шагах есть в словаре
  `misconceptions`, и каждый код словаря где-то используется (README §1.6);
- у неверного варианта `choice` есть код ошибки, у каждого варианта — разбор;
- уникальность `id` уровней и шагов, `max_len` консоли ≤ 64 (бриф Р3);
- разметка текстов без HTML: сценарий показывается на чужих сайтах (README §1.3).

Семантику шагов (шаблоны `{…}`, условия, совпадение с настоящим Python)
проверяет `docs/design/trainers/scenarios/check_scenarios.py` — это задача
методиста, а не публикации.
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, Dict, Iterator, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

#: Версия формата, которую понимает эта схема.
SCENARIO_SCHEMA = "trainer-scenario/1"
#: Адрес тренажёра: `/t/{slug}`, `/embed/trainer/{slug}`.
SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
#: Код ошибки (ключ словаря `misconceptions`).
MISCONCEPTION_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
#: Потолок размера сценария в компактном JSON. Бриф Р7 целится в ≤30 КБ, но
#: пилотные сценарии уже 26–33 КБ в компактной записи: потолок — защита
#: хранилища и встройки от случайно раздутого файла, а не цель размера.
MAX_SCENARIO_BYTES = 96 * 1024
#: Потолок ввода в консоль тренажёра (бриф Р3).
CONSOLE_MAX_LEN = 64

# HTML в текстах запрещён (README §1.3): разрешённый набор — только `` `код` `` и
# `**жирный**`. Тег — `<` сразу с буквой, `/` или `!` (`a < b` в коде тегом не
# считается). Листинги и вывод программы (`code`, `lines`, `print`, `error`,
# `crash`) — текст кода, а не разметка: в них законны `<module>` и сравнения.
# Это вторая линия: SPW обязан выводить тексты сценария как текст.
_TAG_RE = re.compile(r"<[A-Za-z/!?]")
# Опасные схемы ссылок — после удаления пробельных и управляющих символов
# (браузер вырезает их из схемы: `java\nscript:`).
_SCHEME_RE = re.compile(r"(?:javascript|vbscript|data):", re.IGNORECASE)
_STRIP_RE = re.compile(r"[\s\x00-\x1f\x7f]+")
_CODE_KEYS = frozenset({"code", "lines", "print", "error", "crash"})
# Внутри `` `код` `` текст — код (`i<n` законно): из проверки тегов вырезается.
_INLINE_CODE_RE = re.compile(r"`[^`]*`")


class _Strict(BaseModel):
    """База частей сценария: незнакомый ключ — ошибка (опечатка не проходит)."""

    model_config = ConfigDict(extra="forbid")


#: Текст или выбор по условию: `[{"when": …, "text"|"value": …}, …]` (README §1.3).
ConditionalText = Union[str, List[Dict[str, Any]]]


class Misconception(_Strict):
    """Код ошибки: строка карты ошибок, уровень повтора, подсказка наставнику."""

    label: str = Field(..., min_length=1, max_length=200)
    level: int = Field(..., ge=1, description="Номер уровня (1…) для повтора")
    tutor_hint: str = Field(..., min_length=1, max_length=1000)
    map_ref: Optional[str] = Field(default=None, max_length=200)


class ScenarioParam(_Strict):
    """Параметр зерна: ровно один из `pick` / `int` / `expr` (README §1.2)."""

    name: str = Field(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
    pick: Optional[List[Any]] = Field(default=None, min_length=1)
    int_: Optional[List[int]] = Field(default=None, alias="int")
    expr: Optional[str] = Field(default=None, min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _one_kind(self) -> "ScenarioParam":
        kinds = [k for k in (self.pick, self.int_, self.expr) if k is not None]
        if len(kinds) != 1:
            raise ValueError(f"параметр {self.name}: нужен ровно один из pick / int / expr")
        if self.int_ is not None and (len(self.int_) != 2 or self.int_[0] > self.int_[1]):
            raise ValueError(f"параметр {self.name}: int — отрезок [от, до]")
        return self


class ChoiceOption(_Strict):
    """Вариант ответа шага `choice`."""

    text: str = Field(..., min_length=1, max_length=2000)
    correct: Union[bool, str, None] = None
    misconception: Optional[str] = None
    why: ConditionalText

    @model_validator(mode="after")
    def _wrong_has_code(self) -> "ChoiceOption":
        # Вариант, верный не всегда (`false`, условие или поле не задано), обязан
        # назвать ошибку: иначе карте ошибок нечего посчитать (README §1.5).
        if self.correct is not True and not self.misconception:
            raise ValueError(f"вариант «{self.text[:40]}»: у неверного варианта нужен misconception")
        return self


StepKind = Literal["choice", "console", "value", "trace"]


class ScenarioStep(_Strict):
    """Шаг уровня `stepper` или шаблон шага витка цикла (README §1.5)."""

    id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: StepKind
    line: Union[int, None, List[Dict[str, Any]]] = None
    q: Optional[ConditionalText] = None
    placeholder: Optional[str] = Field(default=None, max_length=200)
    variants: Optional[List[str]] = None
    code: Optional[List[str]] = None
    before: Optional[Dict[str, Any]] = None
    effect: Optional[Dict[str, Any]] = None
    why_ok: Optional[ConditionalText] = None
    # choice
    options: Optional[List[ChoiceOption]] = None
    # console
    normalize: Optional[List[Literal["trim", "collapse_spaces", "numbers", "casefold"]]] = None
    max_len: Optional[int] = Field(default=None, ge=1, le=CONSOLE_MAX_LEN)
    accept: Optional[str] = Field(default=None, max_length=500)
    reject: Optional[List[Dict[str, Any]]] = None
    crash_note: Optional[str] = None
    store: Optional[Dict[str, str]] = None
    # value
    expect: Union[str, int, Dict[str, Any], List[Dict[str, Any]], None] = None
    diagnose: Optional[List[Dict[str, Any]]] = None
    fallback: Optional[Dict[str, Any]] = None
    # trace
    loop: Optional[Dict[str, Any]] = None
    acc: Optional[Dict[str, Any]] = None
    per_iteration: Optional[List["ScenarioStep"]] = None
    iters: Optional[Literal["all", "first"]] = None
    on_exit: Optional[List[Dict[str, Any]]] = None

    @model_validator(mode="after")
    def _shape_by_kind(self) -> "ScenarioStep":
        where = f"шаг {self.id}"
        if self.kind == "trace":
            if not self.loop or self.loop.get("type") not in ("while", "for_range"):
                raise ValueError(f"{where}: trace требует loop.type = while | for_range")
            if not self.per_iteration:
                raise ValueError(f"{where}: trace требует непустой per_iteration")
            if any(s.kind == "trace" for s in self.per_iteration):
                raise ValueError(f"{where}: trace внутри trace не поддерживается")
            ids = [s.id for s in self.per_iteration]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{where}: id шагов per_iteration повторяются")
            return self
        if self.q is None:
            raise ValueError(f"{where}: нужен вопрос q")
        if self.kind == "choice":
            if not self.options or len(self.options) < 2:
                raise ValueError(f"{where}: у choice нужно не меньше двух вариантов")
            if not any(o.correct not in (None, False) for o in self.options):
                raise ValueError(f"{where}: у choice нет ни одного верного варианта")
        elif self.kind == "console":
            if self.max_len is None:
                raise ValueError(f"{where}: у console нужен max_len (≤ {CONSOLE_MAX_LEN})")
            if self.why_ok is None:
                raise ValueError(f"{where}: у console нужен why_ok")
            for pattern in [self.accept, *((r.get("match") for r in self.reject or []))]:
                if isinstance(pattern, str):
                    _check_regex(pattern, where)
        elif self.kind == "value":
            if self.expect is None:
                raise ValueError(f"{where}: у value нужен expect")
            if self.why_ok is None:
                raise ValueError(f"{where}: у value нужен why_ok")
            for rule in self.diagnose or []:
                if isinstance(rule.get("matches"), str):
                    _check_regex(rule["matches"], where)
        return self


class PuzzleItem(_Strict):
    """Мини-задача уровня `quotes_puzzle` (README §1.4)."""

    id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$")
    intro: str = Field(..., min_length=1)
    lines: List[List[Dict[str, Any]]] = Field(..., min_length=1)
    target: str = Field(..., min_length=1)
    diagnose: List[Dict[str, Any]] = Field(..., min_length=1)
    success: ConditionalText

    @model_validator(mode="after")
    def _tokens(self) -> "PuzzleItem":
        for line in self.lines:
            for token in line:
                kinds = [k for k in ("var", "code", "word") if k in token]
                if len(kinds) != 1:
                    raise ValueError(f"задача {self.id}: токен строки — ровно один из var / code / word")
                if "word" in token and not token.get("id"):
                    raise ValueError(f"задача {self.id}: у слова нужен id")
        return self


class QuotesPuzzleLevel(_Strict):
    """Уровень «Расставь кавычки»."""

    id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,32}$")
    title: str = Field(..., min_length=1, max_length=200)
    kind: Literal["quotes_puzzle"]
    hint: Optional[str] = None
    items: List[PuzzleItem] = Field(..., min_length=1)


class StepperLevel(_Strict):
    """Уровень «код + память + консоль + вопрос перед строкой»."""

    id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,32}$")
    title: str = Field(..., min_length=1, max_length=200)
    kind: Literal["stepper"]
    hint: Optional[str] = None
    code: Union[List[str], Dict[str, List[str]]]
    code_key: Optional[str] = None
    steps: List[ScenarioStep] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _code_and_ids(self) -> "StepperLevel":
        if isinstance(self.code, dict) and not self.code_key:
            raise ValueError(f"уровень {self.id}: code — словарь вариантов, нужен code_key")
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError(f"уровень {self.id}: id шагов повторяются")
        return self


ScenarioLevel = Annotated[Union[QuotesPuzzleLevel, StepperLevel], Field(discriminator="kind")]


class StarsRule(_Strict):
    """Правило звёзд за уровень: до `max_mistakes` ошибок → `stars`."""

    max_mistakes: Optional[int] = Field(default=None, ge=0)
    stars: int = Field(..., ge=0, le=3)


class Scoring(_Strict):
    """Счёт (README §1.7)."""

    stars_per_level: List[StarsRule] = Field(..., min_length=1)
    neutral_is_mistake: bool = False
    same_option_twice: Literal["not_counted", "counted"] = "not_counted"


class Finish(BaseModel):
    """Тексты финала. Состав подписей меняет методист — лишние ключи разрешены."""

    title: str = Field(..., min_length=1)
    buttons: Dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class TrainerScenario(_Strict):
    """Сценарий тренажёра `trainer-scenario/1` (README §1.1)."""

    schema_: Literal["trainer-scenario/1"] = Field(..., alias="schema")
    slug: str = Field(..., pattern=SLUG_PATTERN)
    title: str = Field(..., min_length=1, max_length=200)
    subtitle: Optional[str] = Field(default=None, max_length=500)
    lang: str = Field(..., pattern=r"^[a-z]{2}$")
    node_refs: Optional[List[int]] = None
    est_minutes: Optional[List[int]] = None
    intro: Optional[str] = None
    misconceptions: Dict[str, Misconception] = Field(..., min_length=1)
    params: List[ScenarioParam]
    levels: List[ScenarioLevel] = Field(..., min_length=1, max_length=20)
    scoring: Scoring
    finish: Finish

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("misconceptions")
    @classmethod
    def _codes(cls, v: Dict[str, Misconception]) -> Dict[str, Misconception]:
        bad = [code for code in v if not re.match(MISCONCEPTION_CODE_PATTERN, code)]
        if bad:
            raise ValueError(f"недопустимые коды ошибок: {', '.join(bad)}")
        return v

    @field_validator("est_minutes")
    @classmethod
    def _minutes(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is not None and (len(v) != 2 or not 0 < v[0] <= v[1]):
            raise ValueError("est_minutes — отрезок [от, до] в минутах")
        return v

    @model_validator(mode="after")
    def _references(self) -> "TrainerScenario":
        level_ids = [lvl.id for lvl in self.levels]
        if len(set(level_ids)) != len(level_ids):
            raise ValueError("id уровней повторяются")
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError("имена параметров повторяются")
        for code, m in self.misconceptions.items():
            if m.level > len(self.levels):
                raise ValueError(f"код {code}: level {m.level} — нет такого уровня")
        known = set(self.misconceptions)
        used: set[str] = set()
        for path, value in _walk_misconceptions([lvl.model_dump() for lvl in self.levels]):
            if value not in known:
                raise ValueError(f"{path}: неизвестный код ошибки «{value}»")
            used.add(value)
        unused = sorted(known - used)
        if unused:
            raise ValueError(f"коды ошибок нигде не используются: {', '.join(unused)}")
        for path, text in _walk_strings(self.model_dump(by_alias=True)):
            if _TAG_RE.search(_INLINE_CODE_RE.sub("", text)) or _SCHEME_RE.search(_STRIP_RE.sub("", text)):
                raise ValueError(f"{path}: HTML в тексте сценария запрещён (только `код` и **жирный**)")
        return self

    def misconception_codes(self) -> set[str]:
        """Коды ошибок, которые принимает ручка событий (бриф Р3)."""
        return set(self.misconceptions)


def _check_regex(pattern: str, where: str) -> None:
    """Регулярка сценария обязана компилироваться (иначе шаг упадёт у ученика)."""
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{where}: регулярка «{pattern}» не компилируется: {exc}") from exc


def _walk_misconceptions(node: Any, path: str = "levels") -> Iterator[Tuple[str, str]]:
    """Все значения ключей `misconception` в дереве уровней."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "misconception" and isinstance(value, str):
                yield path, value
            else:
                yield from _walk_misconceptions(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_misconceptions(value, f"{path}[{i}]")


def _walk_strings(node: Any, path: str = "scenario") -> Iterator[Tuple[str, str]]:
    """Все строки сценария (значения и ключи словарей) с путём для сообщения."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield path, str(key)
            if key in _CODE_KEYS:
                continue
            yield from _walk_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_strings(value, f"{path}[{i}]")


def scenario_size(scenario: Any) -> int:
    """Размер сценария в компактном JSON (байты UTF-8)."""
    return len(json.dumps(scenario, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def validate_scenario(raw: Any) -> TrainerScenario:
    """Проверить сценарий целиком. ValueError / ValidationError — сценарий битый."""
    if not isinstance(raw, dict):
        raise ValueError("scenario должен быть объектом")
    size = scenario_size(raw)
    if size > MAX_SCENARIO_BYTES:
        raise ValueError(f"сценарий {size} байт — больше потолка {MAX_SCENARIO_BYTES}")
    try:
        return TrainerScenario.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"сценарий не прошёл схему {SCENARIO_SCHEMA}: {describe_errors(exc)}") from None


def describe_errors(exc: ValidationError, limit: int = 8) -> str:
    """Ошибки схемы одной строкой: «путь: сообщение; …» (первые `limit`)."""
    parts = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "scenario"
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}")
    more = len(exc.errors()) - limit
    if more > 0:
        parts.append(f"и ещё {more}")
    return "; ".join(parts)


ScenarioStep.model_rebuild()
