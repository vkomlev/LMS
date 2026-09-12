# app/services/unseen_constructs_service.py
"""
Конструкции, которых не было в пройденных ЭТИМ учеником материалах (tsk-864).

Наблюдение оператора 09.09: ученик на второй теме Python («Первая программа»)
сдаёт `print(f"После a = 5+4: значение = {a}, тип = {type(a).__name__}")`, а
детектор ИИ-авторства называет конструкцию обычной. И он прав вообще: f-строка
— базовая часть языка. Но не здесь: в материалах первых трёх тем она не
упоминается ни разу, а `__name__` — это несколько тем вперёд.

**Почему признак считается у нас, а не моделью.** В `_SYSTEM_PROMPT` детектора
примета «конструкции заметно выше уровня задания» есть с самого начала, но
опереться ей не на что: `_build_user_message` подаёт модели только условие
задания и код. Позиция ученика в программе в промпт не передаётся вовсе.
Решение оператора 09.09 — промпт не трогать, а считать факт на нашей стороне:
модель судит о стиле, мы — о том, что этот ученик уже читал.

**Что именно считается фактом.** Конструкция помечается непройденной, если она
не встречается НИ В ОДНОМ материале, который ученик отметил пройденным, и ни в
одном материале темы, в которой он сдаёт работу сейчас. Карта «тема →
конструкции» руками не составлена и не составляется: сопоставление идёт по
тексту самих материалов. Составлять её руками значило бы завести второй
источник истины, который разъедется с курсом на первой же правке урока.

**Тема, в которой ученик сейчас, считается пройденной целиком** — даже те её
материалы, которые он ещё не отметил. Ученик внутри темы, материалы у него
перед глазами, и порядок «сначала задание, потом чтение» — обычное дело.
Уступка намеренно в сторону молчания: ложная пометка дороже пропущенной.

**Порядок тем персональный, и здесь он не нужен вовсе.** Программа режется под
срок ([[tsk-798]]) и определяется фактической записью на курсы ([[tsk-797]]),
поэтому номинальный порядок курса конкретному ученику может не соответствовать.
Отметки о пройденных материалах — факт об этом человеке, а не о курсе; поэтому
и грабли «подкурсы напрямую не назначаются, охват считается по дереву» признак
не задевают: он вообще не спрашивает, на что ученик записан.

**Чего признак НЕ доказывает.** Ученик мог узнать конструкцию сам — в
интернете, в школе, от родителя, на прошлом курсе. Это повод посмотреть работу,
а не обвинение; на балл и зачёт он не влияет и ученику не показывается.
Формулировка в интерфейсе говорит только о факте: «в пройденных темах эта
конструкция не объяснялась».

**tsk-916: первое расширение каталога — списки.** Живой случай: задание на
тему «Строки» (курс 108), ученик получает список через `.split()` и собирает
строку обратно через `.join()`, курс 109 «Списки» не открывал вообще. У
`split_join` (и у `list_literal`) есть особенность: сам метод `.split()`/
`.join()` мельком объясняется и в материале курса 108 «Строковые методы»
(попутно, без упражнений на списки) — то есть под общее правило «текущая тема
пройдена целиком» эти два кода НЕ подпадают (`course_covers=False` у записи
каталога): решение оператора 12.09 — практики со списками мельком объяснённый
метод не даёт, пометка важнее риска лишний раз показаться. Для всех остальных
14 конструкций правило tsk-864 не изменилось.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Больше этого разбирать не берёмся: осмысленной учебной программы такого
#: размера не бывает, а `ast.parse` на мусоре такой длины — трата времени.
_MAX_CODE_CHARS = 100_000

#: Сколько пометок показываем. Список из десятка строк преподаватель не читает,
#: а первые несколько — читает.
_MAX_ITEMS = 5


@dataclass(frozen=True)
class Construct:
    """Одна конструкция языка: как её видно в коде и как — в материале."""

    code: str
    label: str
    #: Как конструкция выглядит в тексте материала. Совпадение ЛЮБОГО образца
    #: считается объяснением. Образцы намеренно широкие: лишнее совпадение
    #: гасит пометку, то есть ошибается в сторону молчания.
    material_patterns: Tuple[re.Pattern[str], ...]
    #: tsk-916. По умолчанию `True` — общее правило tsk-864: материалы ТЕКУЩЕГО
    #: курса (в котором сдана работа) считаются доступными ученику целиком,
    #: даже неотмеченными. Для `False` эта льгота не действует: конструкция
    #: считается объяснённой только тем, что ученик САМ отметил пройденным
    #: (`student_material_progress`), из любого курса. Нужно там, где текущий
    #: курс мельком упоминает конструкцию, но не даёт по ней практики — ровно
    #: случай `split_join`: курс «Строки» называет метод `.split()`, но
    #: упражнения на списки только в курсе «Списки».
    course_covers: bool = True


def _p(*patterns: str) -> Tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


#: Каталог конструкций. Порядок — от простого к сложному: в таком же порядке
#: пометки читаются преподавателем.
#:
#: Каталог намеренно короткий и состоит из того, что (а) надёжно видно в разборе
#: кода без догадок и (б) в школьном курсе действительно приходит отдельной
#: темой. Расширять его стоит по находкам преподавателя, а не «на всякий
#: случай»: каждая лишняя строка — это ещё один повод отвлечь человека.
CONSTRUCTS: Tuple[Construct, ...] = (
    Construct(
        "fstring", 'f-строка (`f"…"`)',
        _p(r"f-строк", r"f-string", r"(?<![\w.])f[\"']"),
    ),
    Construct(
        "format_method", "метод `.format()`",
        _p(r"\.format\s*\("),
    ),
    Construct(
        "list_literal", "литерал списка (`[...]`)",
        _p(r"список\w*\s+можно\s+созда", r"созда(ть|ни[ея])\s+списк", r"\blist\s*\("),
        course_covers=False,
    ),
    Construct(
        "split_join", "методы `.split()`/`.join()` (строка ↔ список)",
        _p(r"\.split\s*\(", r"\.join\s*\(", r"метод\w*\s+split", r"метод\w*\s+join"),
        course_covers=False,
    ),
    Construct(
        "func_def", "объявление своей функции (`def`)",
        _p(r"\bdef\s+\w", r"собственн\w+ функц"),
    ),
    Construct(
        "with_stmt", "конструкция `with`",
        _p(r"\bwith\s+\w", r"менеджер\w* контекст"),
    ),
    Construct(
        "try_except", "обработка ошибок (`try`/`except`)",
        _p(r"\bexcept\b", r"\btry\s*:", r"обработк\w+ (ошибок|исключен)"),
    ),
    Construct(
        "comprehension", "генератор списка/словаря/множества",
        _p(
            r"\[[^\[\]]{0,150}\bfor\b[^\[\]]{0,150}\]",
            r"\{[^{}]{0,150}\bfor\b[^{}]{0,150}\}",
            r"генератор\w*\s+(списк|словар|множеств)",
            r"списочн\w+ включен",
        ),
    ),
    Construct(
        "lambda_expr", "безымянная функция (`lambda`)",
        _p(r"\blambda\b", r"лямбд"),
    ),
    Construct(
        "ternary", "условие в одну строку (`a if … else b`)",
        _p(r"\bif\b[^\n]{1,150}\belse\b", r"тернарн"),
    ),
    Construct(
        "slice_step", "срез с шагом (`[::-1]`)",
        _p(r"\[[^\]\n]{0,40}:[^\]\n]{0,40}:", r"срез\w*\s+с\s+шагом"),
    ),
    Construct(
        "star_args", "переменное число аргументов (`*args` / `**kwargs`)",
        _p(r"\*args", r"\*\*kwargs", r"распаковк\w+ аргумент"),
    ),
    Construct(
        "type_hints", "подсказки типов в объявлении функции",
        _p(r"->\s*[A-Za-z_]", r"аннотац\w+ типов", r"подсказк\w+ типов"),
    ),
    Construct(
        "class_def", "объявление класса (`class`)",
        _p(r"\bclass\s+[A-Za-z_]", r"\bкласс\w*\s+(и\s+)?объект"),
    ),
    Construct(
        "decorator", "декоратор (`@…`)",
        _p(r"@[A-Za-z_]\w*", r"декоратор"),
    ),
    Construct(
        "walrus", "оператор `:=`",
        _p(r":="),
    ),
    Construct(
        "dunder", "служебный атрибут вида `__имя__`",
        _p(r"__\w+__", r"дандер"),
    ),
)

_BY_CODE: Dict[str, Construct] = {c.code: c for c in CONSTRUCTS}


def _is_dunder(name: str) -> bool:
    """`__name__` — да, `__` и `_x_` — нет."""
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def detect_in_code(code: str) -> List[Tuple[str, str]]:
    """
    Какие конструкции каталога есть в коде ученика: пары (код конструкции, строка).

    Разбор идёт по СИНТАКСИЧЕСКОМУ ДЕРЕВУ (`ast`), а не регуляркой по тексту, и
    это не педантизм: регулярка нашла бы `lambda` в комментарии и `class` в
    строковом литерале, то есть выдала бы пометку на ровном месте — ровно то,
    чего признак обязан избегать. По материалам, наоборот, ищем регуляркой: там
    код перемешан с прозой, деревом его не разберёшь. Асимметрия работает в одну
    сторону — точно в коде, широко в материалах, — и обе половины ошибаются в
    пользу молчания.

    Не-Python (в LMS это Arduino/C++, 40 заданий курсов «МАМ») не разбирается
    вовсе: `ast.parse` упадёт, и мы честно вернём пустой список. Признак
    появится там, когда появится каталог конструкций для этого языка.

    :param code: Исходный код ученика.
    """
    if not isinstance(code, str) or not code.strip() or len(code) > _MAX_CODE_CHARS:
        return []
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return []

    lines = code.splitlines()
    found: Dict[str, str] = {}

    def remember(node: ast.AST, code_name: str) -> None:
        """Первое вхождение конструкции и строка кода, где оно найдено."""
        if code_name in found:
            return
        lineno = getattr(node, "lineno", None)
        snippet = ""
        if isinstance(lineno, int) and 1 <= lineno <= len(lines):
            snippet = lines[lineno - 1].strip()[:160]
        found[code_name] = snippet

    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            remember(node, "fstring")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "format":
                remember(node, "format_method")
            elif isinstance(func, ast.Attribute) and func.attr in ("split", "join"):
                remember(node, "split_join")
        elif isinstance(node, ast.List):
            # tsk-916: литерал `[...]`, не генератор списка (тот - ast.ListComp,
            # отдельная ветка ниже) и не срез (ast.Slice, отдельная конструкция).
            remember(node, "list_literal")
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            remember(node, "comprehension")
        elif isinstance(node, ast.Lambda):
            remember(node, "lambda_expr")
        elif isinstance(node, ast.IfExp):
            remember(node, "ternary")
        elif isinstance(node, ast.NamedExpr):
            remember(node, "walrus")
        elif isinstance(node, ast.Try):
            remember(node, "try_except")
        elif isinstance(node, ast.ClassDef):
            remember(node, "class_def")
            if node.decorator_list:
                remember(node, "decorator")
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            remember(node, "with_stmt")
        elif isinstance(node, ast.Slice) and node.step is not None:
            remember(node, "slice_step")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            remember(node, "func_def")
            if node.decorator_list:
                remember(node, "decorator")
            args = node.args
            if args.vararg is not None or args.kwarg is not None:
                remember(node, "star_args")
            if node.returns is not None or any(
                a.annotation is not None
                for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            ):
                remember(node, "type_hints")
        elif isinstance(node, ast.Attribute):
            if _is_dunder(node.attr):
                remember(node, "dunder")
        elif isinstance(node, ast.Name):
            if _is_dunder(node.id):
                remember(node, "dunder")

    # Порядок каталога, а не порядок обхода дерева: список пометок должен
    # читаться одинаково у двух работ с одинаковым набором конструкций.
    return [(c.code, found[c.code]) for c in CONSTRUCTS if c.code in found]


def collect_text(value: Any, out: List[str]) -> None:
    """Собрать все строки из JSON материала: текст урока лежит на разной глубине."""
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            collect_text(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            collect_text(item, out)


def codes_in_material(content_json: Optional[str], *, wanted: Optional[Set[str]] = None) -> Set[str]:
    """
    Какие конструкции каталога встречаются в тексте одного материала.

    :param content_json: `materials.content` в виде текста JSON.
    :param wanted: Считать только эти конструкции (обычно — найденные в коде).
    """
    if not content_json:
        return set()
    try:
        data = json.loads(content_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        # Материал с нечитаемым содержимым — не повод уронить оценку целиком.
        return set()
    chunks: List[str] = []
    collect_text(data, chunks)
    body = "\n".join(chunks)
    if not body:
        return set()
    hits: Set[str] = set()
    for construct in CONSTRUCTS:
        if wanted is not None and construct.code not in wanted:
            continue
        if any(p.search(body) for p in construct.material_patterns):
            hits.add(construct.code)
    return hits


#: `as_of` нужен пересчёту истории: у работы 2026-08-05 сверка обязана идти с
#: тем, что ученик прошёл К ТОМУ ДНЮ, а не с сегодняшним состоянием. Иначе
#: пересчёт молча снял бы пометку с работ, где ученик просто дошёл до нужной
#: темы позже — а в момент сдачи конструкции он действительно не знал. На живом
#: пути параметр не передаётся: там «сейчас» и есть момент сдачи.
_SEEN_SQL = """
    SELECT m.content::text AS content
    FROM student_material_progress smp
    JOIN materials m ON m.id = smp.material_id
    WHERE smp.student_id = :student_id
      AND smp.status = 'completed'
      AND (CAST(:as_of AS timestamptz) IS NULL OR smp.completed_at <= CAST(:as_of AS timestamptz))
"""

_COURSE_SQL = """
    SELECT m.content::text AS content
    FROM materials m
    WHERE m.course_id = :course_id
"""


async def covered_codes(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: Optional[int],
    as_of: Optional[datetime] = None,
    cache: Optional[Dict[Any, Any]] = None,
) -> Tuple[Set[str], int]:
    """
    Конструкции, объяснённые ученику, и число материалов, по которым это видно.

    Содержимое берётся ТЕКСТОМ (`content::text`) и разбирается здесь, в Python.
    В jsonb-виде кавычки экранированы (`f\\"`), и образец `f"` не нашёл бы ни
    одной f-строки: первая же попытка искать это средствами Postgres дала ноль
    совпадений там, где их две.

    Считаются ВСЕ конструкции каталога, а не только найденные в коде: разбор
    текста дешевле его вычитки из базы, зато результат становится пригодным для
    кэша. Пачка тика — до десяти работ, и четыре из семи наблюдавшихся сдач
    принадлежали одному ученику; без кэша его материалы читались бы четырежды.

    :param student_id: Чьи отметки о пройденных материалах берём.
    :param course_id: Тема, в которой сдана работа: её материалы считаются
        доступными ученику целиком, даже неотмеченные.
    :param as_of: Учитывать только материалы, пройденные до этого момента.
        Нужен пересчёту истории; на живом пути не передаётся.
    :param cache: Словарь на время прохода. Живёт ровно столько, сколько идёт
        пачка: материалы курса за это время не меняются, а между проходами
        могли бы — поэтому кэш не переживает тик.
    :return: (коды объяснённых конструкций, число отмеченных пройденными материалов)
    """
    box = cache if cache is not None else {}

    student_key = ("student", student_id, as_of)
    if student_key not in box:
        seen: Set[str] = set()
        rows = (await db.execute(
            text(_SEEN_SQL), {"student_id": student_id, "as_of": as_of}
        )).fetchall()
        for (content,) in rows:
            seen |= codes_in_material(content)
        box[student_key] = (seen, len(rows))
    student_seen, materials_seen = box[student_key]

    course_seen: Set[str] = set()
    if course_id is not None:
        course_key = ("course", course_id)
        if course_key not in box:
            acc: Set[str] = set()
            course_rows = (
                await db.execute(text(_COURSE_SQL), {"course_id": course_id})
            ).fetchall()
            for (content,) in course_rows:
                acc |= codes_in_material(content)
            box[course_key] = acc
        course_seen = box[course_key]

    # tsk-916: льгота "текущий курс пройден целиком" не действует для конструкций
    # с course_covers=False - см. докстринг Construct.course_covers. Студент
    # мог сам отметить такой материал пройденным (student_seen), это считается
    # как обычно; не считается только бесплатный проход от всего курса.
    course_seen_counted = {c for c in course_seen if _BY_CODE[c].course_covers}

    return student_seen | course_seen_counted, materials_seen


async def build_report(
    db: AsyncSession,
    *,
    student_id: Optional[int],
    course_id: Optional[int],
    code: str,
    as_of: Optional[datetime] = None,
    cache: Optional[Dict[Any, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Секция отчёта `code_review.unseen_constructs` — или `None`, если не считали.

    `None` и пустой список — РАЗНЫЕ вещи, и различать их обязательно. Пустой
    список значит «сверили, всё пройдено»; `None` — «сверить не с чем»: язык не
    Python, ученик неизвестен, отметок о пройденных материалах нет вовсе. Слепив
    их в одно, мы показали бы преподавателю тишину как результат проверки,
    которой не было.

    Исключений не бросает: признак — довесок к оценке кода, и его сбой не должен
    ни ронять фоновый тик, ни лишать преподавателя самой оценки.

    :param student_id: Автор работы.
    :param course_id: Курс задания, по которому сдана работа.
    :param code: Исходный код ученика, тот же, что уходит модели.
    :param as_of: Момент, на который считать пройденное (пересчёт истории).
    :param cache: Общий словарь на время прохода пачки — см. `covered_codes`.
    """
    try:
        return await _build_report(
            db, student_id=student_id, course_id=course_id, code=code,
            as_of=as_of, cache=cache,
        )
    except Exception:  # noqa: BLE001 — намеренно широкий: см. докстринг
        logger.exception("tsk-864: признак непройденных конструкций не посчитан")
        return None


async def _build_report(
    db: AsyncSession,
    *,
    student_id: Optional[int],
    course_id: Optional[int],
    code: str,
    as_of: Optional[datetime] = None,
    cache: Optional[Dict[Any, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Сам расчёт. Обёртка выше гарантирует, что наружу не улетит исключение."""
    if student_id is None:
        return None
    detected = detect_in_code(code)
    if not detected:
        return None

    seen, materials_seen = await covered_codes(
        db, student_id=student_id, course_id=course_id, as_of=as_of, cache=cache
    )
    if materials_seen == 0:
        # Ученик не отметил пройденным ни одного материала. Тогда «не
        # встречается в пройденном» — правда обо всём подряд, и пометка
        # означала бы только то, что человек не нажимал кнопку.
        return None

    items = [
        {"code": code_name, "label": _BY_CODE[code_name].label, "evidence": snippet}
        for code_name, snippet in detected
        if code_name not in seen
    ][:_MAX_ITEMS]

    return {"items": items, "materials_seen": materials_seen}
