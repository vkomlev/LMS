"""Механический инвентарь запросов без полного порядка (tsk-996).

Проходит по `app/` (без миграций и `app/tests`) и находит:

* SQL-строки (литералы и f-строки, начинающиеся с SELECT/WITH и содержащие
  FROM) — в `text(...)`, в константах `*_SQL` и в любых других местах;
* цепочки SQLAlchemy `select(...)` (с `.order_by`/`.limit`/`.distinct`/
  `.group_by` по цепочке, включая переприсваивания `stmt = stmt.where(...)`).

Для каждого запроса определяет признаки самого SQL (ORDER BY и его ключи,
DISTINCT, DISTINCT ON, GROUP BY, LIMIT, агрегат без GROUP BY, поиск по
уникальному ключу) и потребление результата в объемлющей функции
(`.first()`, `.fetchone()`, `.scalar()`, `[0]`, `.all()`, ...).

Выход — CSV-подобная таблица кандидатов с флагами. Решение по каждому
кандидату (А/Б/В) принимается вручную, скрипт лишь гарантирует, что ни одно
место не пропущено.

Запуск: ``python scripts/order_by_inventory.py [--all] > inventory.tsv``
(``--all`` печатает и отсеянные запросы с причиной отсева).
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SKIP_PARTS = {"migrations", "tests"}

SQL_START = re.compile(r"^\s*(\(\s*)?(SELECT|WITH)\b", re.I | re.S)
ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.I)
LIMIT_1 = re.compile(r"\bLIMIT\s+(1|:\w+|\$\d+|%s)\b", re.I)
LIMIT_ANY = re.compile(r"\bLIMIT\b", re.I)
DISTINCT_ON = re.compile(r"\bDISTINCT\s+ON\b", re.I)
DISTINCT = re.compile(r"\bSELECT\s+DISTINCT\b", re.I)
GROUP_BY = re.compile(r"\bGROUP\s+BY\b", re.I)
AGG_ONLY = re.compile(
    r"^\s*SELECT\s+(COUNT|SUM|MAX|MIN|AVG|EXISTS|BOOL_OR|BOOL_AND|COALESCE\s*\(\s*(SUM|MAX|MIN|COUNT))\s*\(",
    re.I,
)
UNIQUE_LOOKUP = re.compile(
    r"\bWHERE\s+(\w+\.)?(id|external_uid|uid|token_hash|email|code)\s*=\s*(:\w+|\$\d+|%s|'[^']*')"
    r"(?![^;]*\bJOIN\b)",
    re.I | re.S,
)

FIRST_CONSUME = re.compile(
    r"\.(first|fetchone|scalar|scalar_one_or_none|one_or_none|scalar_one|one)\(\)|\[0\]"
)
MANY_CONSUME = re.compile(r"\.(all|fetchall|fetchmany|partitions)\(\)|\bfor\s+\w+(\s*,\s*\w+)*\s+in\s+")


@dataclass
class Query:
    """Найденный запрос и его признаки."""

    file: str
    line: int
    func: str
    kind: str  # sql | orm
    text: str
    flags: list[str] = field(default_factory=list)
    excluded: str = ""


def _string_value(node: ast.AST) -> str | None:
    """Склеить литерал/f-строку/конкатенацию в текст (подстановки — `{}`)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            else:
                out.append("{}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left = _string_value(node.left)
        right = _string_value(node.right) if isinstance(node.op, ast.Add) else ""
        if left is not None:
            return left + (right or "{}")
    return None


def _chain_methods(node: ast.AST) -> list[str]:
    """Имена методов в цепочке вызовов `a.b(...).c(...)`."""
    names: list[str] = []
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        else:
            break
    if isinstance(node, ast.Name):
        names.append(node.id)
    return list(reversed(names))


def _strip_sql_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _top_level(sql: str) -> str:
    """Убрать содержимое скобок — признаки внешнего уровня запроса."""
    out, depth = [], 0
    for ch in sql:
        if ch == "(":
            depth += 1
            out.append(" ")
        elif ch == ")":
            depth = max(0, depth - 1)
            out.append(" ")
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _final_select(sql: str) -> str:
    """Последний (внешний) SELECT после CTE: текст внешнего уровня."""
    top = _top_level(sql)
    return top


def order_keys(sql: str) -> str:
    top = _final_select(sql)
    m = list(ORDER_BY.finditer(top))
    if not m:
        return ""
    tail = top[m[-1].end():]
    tail = re.split(r"\b(LIMIT|OFFSET|FOR\s+UPDATE|FOR\s+SHARE)\b", tail, flags=re.I)[0]
    return " ".join(tail.split())


def analyse_sql(q: Query, func_src: str) -> None:
    sql = _strip_sql_comments(q.text)
    top = _final_select(sql)
    has_order = bool(ORDER_BY.search(top))
    keys = order_keys(sql)
    if AGG_ONLY.search(top) and not GROUP_BY.search(top):
        q.excluded = "агрегат без GROUP BY"
        return
    if re.match(r"^\s*SELECT\s+(1|true)\b", top, re.I) or re.search(r"^\s*SELECT\s+EXISTS", top, re.I):
        q.excluded = "EXISTS/SELECT 1"
        return
    if not has_order and UNIQUE_LOOKUP.search(top) and not GROUP_BY.search(top):
        q.excluded = "поиск по уникальному ключу"
        return
    first = bool(FIRST_CONSUME.search(func_src)) or bool(LIMIT_1.search(top))
    many = bool(MANY_CONSUME.search(func_src))
    if DISTINCT_ON.search(sql):
        q.flags.append("DISTINCT_ON")
    if DISTINCT.search(top):
        q.flags.append("DISTINCT")
    if GROUP_BY.search(top):
        q.flags.append("GROUP_BY")
    if LIMIT_ANY.search(top):
        q.flags.append("LIMIT")
    if not has_order:
        q.flags.append("NO_ORDER")
        if first:
            q.flags.append("FIRST")
        if many:
            q.flags.append("LIST")
    else:
        last = keys.split(",")[-1].strip().lower()
        last = re.sub(r"\s+(asc|desc)(\s+nulls\s+(first|last))?$", "", last).strip()
        if not re.search(r"(^|\.)(id|\w+_id|uid|external_uid)$", last):
            q.flags.append(f"ORDER_TAIL:{last[:40]}")
        if first:
            q.flags.append("FIRST")
    if not q.flags:
        q.excluded = "порядок есть, хвост по id"


def analyse_orm(q: Query, func_src: str, chain: str) -> None:
    if "order_by" in chain:
        m = re.findall(r"order_by\(([^)]*)\)", q.text)
        last = (m[-1] if m else "").split(",")[-1].strip()
        if not re.search(r"(\.id|_id|uid)\b", last):
            q.flags.append(f"ORDER_TAIL:{last[:40]}")
    else:
        q.flags.append("NO_ORDER")
    if re.search(r"select\(\s*func\.(count|sum|max|min)", q.text) and "group_by" not in chain:
        q.excluded = "агрегат"
        return
    if "distinct" in chain:
        q.flags.append("DISTINCT")
    if "group_by" in chain:
        q.flags.append("GROUP_BY")
    if "limit" in chain:
        q.flags.append("LIMIT")
    if re.search(r"\.where\([^)]*\.(id|external_uid)\s*==", q.text) and "NO_ORDER" in q.flags:
        q.excluded = "поиск по уникальному ключу"
        return
    if FIRST_CONSUME.search(func_src):
        q.flags.append("FIRST")
    if MANY_CONSUME.search(func_src):
        q.flags.append("LIST")
    if q.flags and all(f.startswith(("LIST", "FIRST")) for f in q.flags):
        q.excluded = "порядок есть"


class Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, src: str) -> None:
        self.path = path
        self.src = src
        self.lines = src.splitlines()
        self.func_stack: list[ast.AST] = []
        self.found: list[Query] = []
        self.seen_strings: set[int] = set()

    def _func_src(self) -> str:
        if not self.func_stack:
            return ""
        f = self.func_stack[-1]
        return "\n".join(self.lines[f.lineno - 1 : f.end_lineno])

    def _func_name(self) -> str:
        return getattr(self.func_stack[-1], "name", "<module>") if self.func_stack else "<module>"

    def visit_FunctionDef(self, node: ast.AST) -> None:
        self.func_stack.append(node)
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _add_sql(self, node: ast.AST, text_: str) -> None:
        if id(node) in self.seen_strings:
            return
        self.seen_strings.add(id(node))
        q = Query(str(self.path.relative_to(ROOT)), node.lineno, self._func_name(), "sql", text_)
        analyse_sql(q, self._func_src())
        self.found.append(q)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        val = _string_value(node)
        if val and SQL_START.search(val) and re.search(r"\bFROM\b", val, re.I):
            self._add_sql(node, val)
            for sub in ast.walk(node):
                self.seen_strings.add(id(sub))
            return
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        val = _string_value(node)
        if val and SQL_START.search(val) and re.search(r"\bFROM\b", val, re.I):
            self._add_sql(node, val)
        for sub in ast.walk(node):
            self.seen_strings.add(id(sub))

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and SQL_START.search(node.value) and re.search(r"\bFROM\b", node.value, re.I):
            self._add_sql(node, node.value)

    def visit_Call(self, node: ast.Call) -> None:
        names = _chain_methods(node)
        if names and names[0] == "select" and not self._is_inner_chain(node):
            seg = ast.get_source_segment(self.src, node) or ""
            q = Query(str(self.path.relative_to(ROOT)), node.lineno, self._func_name(), "orm", " ".join(seg.split()))
            func_src = self._func_src()
            # переприсваивания stmt = stmt.order_by(...) в той же функции
            chain = " ".join(names) + " " + " ".join(re.findall(r"\.(order_by|limit|distinct|group_by)\(", func_src))
            analyse_orm(q, func_src, chain)
            self.found.append(q)
        self.generic_visit(node)

    def _is_inner_chain(self, node: ast.Call) -> bool:
        # Внешний вызов цепочки обработает его: помечаем внутренние звенья.
        return id(node) in self.seen_strings

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            # пометить внутренние звенья цепочки select(...).x(...)
            inner = node.func
            while isinstance(inner, (ast.Attribute, ast.Call)):
                inner = inner.value if isinstance(inner, ast.Attribute) else inner.func
                if isinstance(inner, ast.Call):
                    self.seen_strings.add(id(inner))
        super().generic_visit(node)


def main() -> None:
    show_all = "--all" in sys.argv
    rows: list[Query] = []
    for path in sorted(APP.rglob("*.py")):
        if SKIP_PARTS & set(path.relative_to(APP).parts):
            continue
        src = path.read_text(encoding="utf-8")
        v = Visitor(path, src)
        v.visit(ast.parse(src))
        rows.extend(v.found)
    print("file\tline\tfunc\tkind\tflags\texcluded\tsql")
    for q in rows:
        if q.excluded and not show_all:
            continue
        print(
            f"{q.file}\t{q.line}\t{q.func}\t{q.kind}\t{','.join(q.flags)}\t{q.excluded}\t"
            f"{' '.join(q.text.split())[:220]}"
        )
    total = len(rows)
    cand = sum(1 for q in rows if not q.excluded)
    print(f"# всего запросов: {total}, кандидатов: {cand}, отсеяно: {total - cand}", file=sys.stderr)


if __name__ == "__main__":
    main()
