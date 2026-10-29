"""Сверка сценариев тренажёров tsk-1247 с настоящим Python.

Раскрывает шаблоны для всех комбинаций параметров, строит трассу цикла так, как
её должен строить движок, и сравнивает с реальным запуском python3.
Запуск: python3 docs/design/trainers/scenarios/check_scenarios.py (около минуты).
"""
import ast
import itertools
import json
import os
import re
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__)) + "/"
errors = []
checks = 0


def fail(msg):
    errors.append(msg)


def ok(cond, msg):
    global checks
    checks += 1
    if not cond:
        fail(msg)


# ---------------- шаблоны и условия ----------------
TOK = re.compile(r"\{([^{}]+)\}")


def ev(expr, ctx):
    expr = expr.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|[A-Za-z0-9_+\-* ]+", expr):
        raise ValueError(expr)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
        if expr not in ctx:
            raise KeyError(expr)
        return ctx[expr]
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)
    env = {}
    for n in names:
        if n not in ctx:
            raise KeyError(n)
        if not isinstance(ctx[n], int):
            raise TypeError(f"{n} не число в {expr}")
        env[n] = ctx[n]
    return eval(expr, {"__builtins__": {}}, env)


def render(s, ctx):
    if s is None:
        return None
    return TOK.sub(lambda m: str(ev(m.group(1), ctx)), s)


def cond(c, ctx):
    if c in (None, "default"):
        return True
    for part in c.split("&"):
        neg = part.startswith("!")
        part = part[1:] if neg else part
        if part.startswith("quoted:"):
            v = part[7:] in ctx.get("_quoted", set())
        elif "!=" in part:
            k, val = part.split("!=", 1)
            v = str(ctx[k]) != val
        elif "=" in part:
            k, val = part.split("=", 1)
            v = str(ctx[k]) == val
        else:
            v = bool(ctx[part])
        if neg:
            v = not v
        if not v:
            return False
    return True


def pick_when(x, ctx, key="text"):
    """Строка или список {when, text|value}: первый подошедший."""
    if isinstance(x, list):
        for it in x:
            if cond(it.get("when"), ctx):
                return it[key] if key in it else it.get("value")
        return None
    return x


def correct_of(opt, ctx):
    c = opt.get("correct", False)
    if isinstance(c, bool):
        return c
    return cond(c, ctx)


def walk_strings(o, ctx, where):
    """Рендерит все строки шага: ловит незакрытые плейсхолдеры."""
    if isinstance(o, str):
        try:
            render(o, ctx)
        except Exception as e:  # noqa
            fail(f"{where}: шаблон {o[:60]!r}: {e!r}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk_strings(v, ctx, where)
    elif isinstance(o, dict):
        for k, v in o.items():
            if k in ("when", "correct", "match", "accept", "if", "variants", "iters"):
                continue
            walk_strings(v, ctx, f"{where}.{k}")


# ---------------- параметры ----------------
def combos(params):
    """Все комбинации pick/int; expr вычисляются."""
    axes = []
    for p in params:
        if "pick" in p:
            axes.append([(p["name"], v) for v in p["pick"]])
        elif "int" in p:
            lo, hi = p["int"]
            axes.append([(p["name"], v) for v in range(lo, hi + 1)])
    for vals in itertools.product(*axes):
        ctx = {}
        for name, v in vals:
            if isinstance(v, dict):
                ctx.update(v)
                ctx[name] = v
            else:
                ctx[name] = v
        for p in params:
            if "expr" in p:
                ctx[p["name"]] = ev(p["expr"], ctx)
        yield ctx


# Дочерний Python пишет в UTF-8 и на Windows: -I отключает PYTHON*-переменные, поэтому ещё -X utf8.
_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run_py(code, stdin="", timeout=3):
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], input=stdin, capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout, env=_ENV)
    return r.stdout, r.stderr


def nums(s):
    return [int(x) for x in re.split(r"[\s,;]+", s.strip()) if x]


# ---------------- трасса цикла (как должен строить движок) ----------------
def build_trace(loop, acc, ctx):
    var = loop["var"]
    its = []
    s = acc["init"] if acc else None
    hist = []
    if loop["type"] == "while":
        i = int(render(loop["start"], ctx))
        lim = int(render(loop["limit"], ctx))
        step = int(render(str(loop["step"]), ctx))
        cmpop = render(loop["cmp"], ctx)
        while True:
            c = i < lim if cmpop == "<" else i <= lim
            if not c:
                break
            it = {"i": i, "i_next": i + step, "cond": True, "eq": i == lim}
            if acc:
                it.update(s=s, s_new=s + i)
                s = s + i
                hist.append(s)
            its.append(it)
            i += step
        exit_ctx = {"i": i, "cond": False, "eq": i == lim}
        return its, exit_ctx, {"i_final": i, "s_final": s,
                               "last_printed": its[-1]["i"] if its else None,
                               "acc_history": " ".join(map(str, hist))}
    else:
        a = int(render(loop["start"], ctx)); b = int(render(loop["stop"], ctx))
        step = int(render(str(loop["step"]), ctx))
        for i in range(a, b, step):
            it = {"i": i, "i_next": i + step, "cond": True, "eq": False}
            if acc:
                it.update(s=s, s_new=s + i)
                s = s + i
                hist.append(s)
            its.append(it)
        return its, None, {"s_final": s, "acc_history": " ".join(map(str, hist)),
                           "i_final": its[-1]["i"]}


def py_trace(code):
    """Запускает код с settrace: список (номер строки, снимок переменных) перед строкой."""
    tracer = (
        "import sys, json\n"
        "ev=[]\n"
        "def t(f, e, a):\n"
        "    if f.f_code.co_filename=='<prog>' and e=='line':\n"
        "        ev.append([f.f_lineno, {k:v for k,v in f.f_locals.items() if k in ('i','s','n')}])\n"
        "    return t\n"
        "src=" + repr(code) + "\n"
        "import io, contextlib\n"
        "buf=io.StringIO()\n"
        "sys.settrace(t)\n"
        "with contextlib.redirect_stdout(buf):\n"
        "    exec(compile(src,'<prog>','exec'),{})\n"
        "sys.settrace(None)\n"
        "print(json.dumps({'ev':ev,'out':buf.getvalue()}))\n"
    )
    out, err = run_py(tracer)
    if err:
        raise RuntimeError(err)
    return json.loads(out)


def check_trace_vs_python(code_lines, loop, acc, ctx, where):
    code = "\n".join(render(l, ctx) for l in code_lines)
    its, exit_ctx, fin = build_trace(loop, acc, ctx)
    pt = py_trace(code)
    # витки: значения переменной цикла в момент входа в первую строку тела
    body_first = None
    for ln, l in enumerate(code_lines, 1):
        if l.startswith("    "):
            body_first = ln
            break
    seen = [e[1].get(loop["var"]) for e in pt["ev"] if e[0] == body_first]
    ok(seen == [it["i"] for it in its], f"{where}: витки {seen} != движок {[it['i'] for it in its]}")
    if acc:
        # s после тела: снимки на строке тела №1 следующего витка — проще сравнить итог
        sv = [e[1].get("s") for e in pt["ev"] if e[0] == body_first]
        ok(sv == [it["s"] for it in its], f"{where}: s до тела {sv} != {[it['s'] for it in its]}")
    return code, its, exit_ctx, fin, pt["out"]


# ---------------- проверки шагов ----------------
def check_value_step(st, ctx, where):
    """expect не совпадает ни с одним diagnose; рендер проходит."""
    walk_strings(st, ctx, where)
    exp = st.get("expect")
    exp = pick_when(exp, ctx, "value") if isinstance(exp, list) else exp
    if isinstance(exp, dict) and "range" in exp:
        e = list(range(*[int(render(x, ctx)) for x in exp["range"]]))
        for d in st.get("diagnose", []):
            if "range" in d and cond(d.get("when"), ctx):
                dv = list(range(*[int(render(x, ctx)) for x in d["range"]]))
                ok(dv != e, f"{where}: diagnose range совпал с ответом {e}")
        return e
    e = render(exp, ctx)
    for d in st.get("diagnose", []):
        if "equals" in d and cond(d.get("when"), ctx):
            dv = render(d["equals"], ctx)
            same = dv.lower() == e.lower() if d.get("ignore_case") else dv == e
            if same and not (d["equals"] == "{i}" and ctx.get("s") == 0):
                fail(f"{where}: diagnose equals {dv!r} совпал с ответом")
    return e


def check_choice(st, ctx, where):
    walk_strings(st, ctx, where)
    opts = st["options"]
    corr = [o for o in opts if correct_of(o, ctx)]
    ok(len(corr) == 1, f"{where}: верных вариантов {len(corr)}")
    texts = [render(o["text"], ctx) for o in opts]
    ok(len(set(texts)) == len(texts), f"{where}: одинаковые тексты вариантов {texts}")
    for o in opts:
        w = pick_when(o["why"], ctx) if isinstance(o.get("why"), list) else o.get("why")
        ok(bool(w), f"{where}: нет разбора у варианта {o['text']}")
        if not correct_of(o, ctx):
            ok(bool(o.get("misconception")), f"{where}: неверный вариант без кода ошибки")
    return corr[0] if corr else None


# =============== interpreter-input ===============
def check_interpreter():
    sc = json.load(open(DIR + "interpreter-input.json", encoding="utf-8"))
    mis = sc["misconceptions"]
    used = set()

    def collect(o):
        if isinstance(o, dict):
            if "misconception" in o:
                used.add(o["misconception"])
            for v in o.values():
                collect(v)
        elif isinstance(o, list):
            for v in o:
                collect(v)
    collect(sc["levels"])
    ok(used <= set(mis), f"interp: неизвестные коды {used - set(mis)}")
    ok(set(mis) <= used, f"interp: коды без шага {set(mis) - used}")
    maprefs = {m["map_ref"] for m in mis.values() if m["map_ref"]}
    need = {"аргумент", "кавычки", "input(подсказка)", "ожидание ввода", "int/float", "IDLE"}
    ok(need <= maprefs, f"interp: карта узла 106 не покрыта: {need - maprefs}")

    L1, L2, L3 = sc["levels"]
    n_combo = 0
    s1, s2, s3 = set(), set(), set()
    for ctx in combos(sc["params"]):
        n_combo += 1
        k1 = (ctx["name"], ctx["town"], ctx["age"]); k2 = (ctx["v1"], ctx["variant"]); k3 = (ctx["i0"], ctx["extra"])
        do1, do2, do3 = k1 not in s1, k2 not in s2, k3 not in s3
        s1.add(k1); s2.add(k2); s3.add(k3)
        # ---- L1: мини-вычислитель против настоящего Python на всех 2^k раскладках кавычек
        for item in (L1["items"] if do1 else []):
            words = [t for line in item["lines"] for t in line if "word" in t]
            ids = [t["id"] for t in words]
            target = render(item["target"], ctx)
            solutions = 0
            for mask in itertools.product([0, 1], repeat=len(ids)):
                quoted = {i for i, m in zip(ids, mask) if m}
                src_lines = []
                for line in item["lines"]:
                    s = ""
                    for t in line:
                        if "var" in t:
                            s += t["var"]
                        elif "code" in t:
                            s += t["code"]
                        else:
                            w = render(t["word"], ctx)
                            s += f'"{w}"' if t["id"] in quoted else w
                    src_lines.append(s)
                out, err = run_py("\n".join(src_lines))
                # мини-вычислитель (семантика из README)
                vars_ = {}; mout = []; merr = None
                for line in item["lines"]:
                    if "var" in line[0]:
                        t = line[2]; w = render(t["word"], ctx)
                        if t["id"] in quoted: vars_[line[0]["var"]] = w
                        elif w.isdigit(): vars_[line[0]["var"]] = int(w)
                        elif w in vars_: vars_[line[0]["var"]] = vars_[w]
                        else: merr = w; break
                    else:
                        parts = []
                        for t in line:
                            if "word" not in t: continue
                            w = render(t["word"], ctx)
                            if t["id"] in quoted: parts.append(w)
                            elif w.isdigit(): parts.append(w)
                            elif w in vars_: parts.append(str(vars_[w]))
                            else: merr = w; break
                        if merr: break
                        mout.append(" ".join(parts))
                if merr:
                    ok(f"NameError: name '{merr}' is not defined" in err,
                       f"L1 {item['id']} {quoted}: Python не дал NameError {merr}: {err[-80:]}")
                    ok(out == "", f"L1 {item['id']}: вывод до ошибки")
                else:
                    ok(err == "" and out.rstrip("\n") == "\n".join(mout),
                       f"L1 {item['id']} {quoted}: python {out!r}/{err[-60:]} != {mout}")
                    if "\n".join(mout) == target:
                        solutions += 1
                        ctx2 = dict(ctx, _quoted=quoted)
                        succ = item["success"]
                        walk_strings(pick_when(succ, ctx2) if isinstance(succ, list) else succ, ctx2, item["id"])
            ok(solutions >= 1, f"L1 {item['id']}: нет решения")
            # ловушка: в q2 решение ровно одно
            if item["id"] == "q2_trap":
                ok(solutions == 1, "L1 q2: решений не одно")
        # ---- L2
        code_tpl = L2["code"][ctx["variant"]]
        for a1, a2 in ([("Зорро", "5"), ("Мурка", "12"), ("Казань", "007")] if do2 else []):
            c2 = dict(ctx, a1=a1, a2=a2, a2i=int(a2), answer=a2)
            code = "\n".join(render(l, c2) for l in code_tpl)
            out, err = run_py(code, f"{a1}\n{a2}\n")
            lines = out.split("\n")
            # консоль: подсказки input печатаются без перевода строки
            l3 = render("{label3} {a1}", c2)
            ok(l3 in out, f"L2 {ctx['variant']}: нет строки 3 {l3!r} в {out!r}")
            for st in L2["steps"]:
                if "variants" in st and ctx["variant"] not in st["variants"]:
                    continue
                where = f"L2.{st['id']}[{ctx['v1']},{ctx['variant']},{a2}]"
                if st["kind"] == "choice":
                    check_choice(st, c2, where)
                else:
                    if st["kind"] == "value":
                        e = check_value_step(st, c2, where)
                        if st["id"] == "l3_output":
                            ok(e == l3, f"{where}: expect {e!r}")
                        if st["id"] == "l4_output":
                            ok(out.rstrip("\n").endswith(e), f"{where}: python {out!r} vs {e!r}")
                    else:
                        walk_strings(st, c2, where)
            if ctx["variant"] == "noint":
                ok("TypeError: can only concatenate str (not \"int\") to str" in err, f"L2 noint: {err[-80:]}")
            else:
                ok(err == "", f"L2 int упал: {err}")
            # l2_type: ValueError на 2.5
            if ctx["variant"] == "int":
                out, err = run_py(code, f"{a1}\n2.5\n")
                ok("ValueError: invalid literal for int() with base 10: '2.5'" in err, f"L2 ValueError: {err[-90:]}")
            # l3_args: два аргумента
            call = ast.parse(render(code_tpl[2], c2)).body[0].value
            ok(len(call.args) == 2, "L2 l3_args: аргументов не 2")
            # l3_plus
            out, err = run_py(f'{ctx["v1"]} = {a1!r}\nprint("{ctx["label3"]}" + {ctx["v1"]})')
            ok(out.strip() == f'{ctx["label3"]}{a1}', f"L2 l3_plus: {out!r}")
            # l4_fix: только верный вариант чинит программу
            if ctx["variant"] == "noint":
                st = next(s for s in L2["steps"] if s["id"] == "l4_fix")
                for o in st["options"]:
                    line2 = render(o["text"], c2).strip("`")
                    prog = "\n".join([render(code_tpl[0], c2), line2] + [render(l, c2) for l in code_tpl[2:]])
                    out, err = run_py(prog, f"{a1}\n{a2}\n")
                    works = err == "" and out.rstrip("\n").endswith(f"{ctx['label4']} {int(a2)+1}")
                    ok(works == bool(o.get("correct")), f"L2 l4_fix {line2}: works={works} err={err[-70:]}")
                    if "int(\"" in line2:
                        ok("ValueError" in err, "l4_fix: int(подсказка) не дал ValueError")
            # l5_float
            st = next(s for s in L2["steps"] if s["id"] == "l5_float")
            for o in st["options"]:
                line2 = render(o["text"], c2).strip("`")
                out, err = run_py(f"{render(code_tpl[0], c2)}\n{line2}\nprint(type({ctx['v2']}).__name__, {ctx['v2']})",
                                  f"{a1}\n2.5\n")
                if "int(" in line2:
                    ok("ValueError" in err, f"l5 int: {err[-60:]}")
                elif "float(" in line2:
                    ok(out.strip().endswith("float 2.5") and o.get("correct"), f"l5 float: {out!r}")
                else:
                    ok(out.strip().endswith("str 2.5"), f"l5 input: {out!r}")
        # ---- L3: трасса движка против Python
        if not do3:
            continue
        loop_step = next(s for s in L3["steps"] if s["kind"] == "trace")
        code, its, exit_ctx, fin, pyout = check_trace_vs_python(L3["code"], loop_step["loop"], loop_step["acc"], ctx, "interp L3")
        ok(pyout.strip() == str(fin["s_final"]), f"interp L3: print {pyout!r} != {fin['s_final']}")
        ok(2 <= len(its) <= 3, f"interp L3: витков {len(its)}")
        for it in its + [exit_ctx]:
            c3 = dict(ctx, **fin, **it)
            for st in loop_step["per_iteration"]:
                if it is exit_ctx and st["id"] != "cond":
                    continue
                where = f"interp L3.{st['id']} i={it['i']}"
                if st["kind"] == "choice":
                    check_choice(st, c3, where)
                else:
                    check_value_step(st, c3, where)
        for st in L3["steps"]:
            if st["kind"] == "choice":
                check_choice(st, dict(ctx, **fin), "interp L3." + st["id"])
            elif st["kind"] == "value":
                e = check_value_step(st, dict(ctx, **fin), "interp L3." + st["id"])
                ok(e == pyout.strip(), "interp L3 out")
        # финал
        walk_strings(sc["finish"], dict(ctx, stars=7, stars_max=9, minutes=6, top_label="x", top_level=2, link="u"), "finish")
    return n_combo


# =============== loop-machine ===============
def check_loops():
    sc = json.load(open(DIR + "loop-machine.json", encoding="utf-8"))
    mis = sc["misconceptions"]
    used = set()

    def collect(o):
        if isinstance(o, dict):
            if "misconception" in o:
                used.add(o["misconception"])
            for v in o.values():
                collect(v)
        elif isinstance(o, list):
            for v in o:
                collect(v)
    collect(sc["levels"])
    ok(used == set(mis), f"loops: коды {used ^ set(mis)}")
    L1, L2, L3 = sc["levels"]
    n = 0
    seen1 = set(); seen2 = set(); seen3 = set()
    for ctx in combos(sc["params"]):
        n += 1
        k1 = (ctx["cmp"], ctx["i0"], ctx["d"])
        if k1 not in seen1:
            seen1.add(k1)
            ls = next(s for s in L1["steps"] if s["kind"] == "trace")
            code, its, exit_ctx, fin, pyout = check_trace_vs_python(L1["code"], ls["loop"], None, ctx, f"L1 {k1}")
            printed = pyout.split("\n")[:-1]
            ok(printed == [str(it["i"]) for it in its] + ["Готово"], f"L1 {k1}: вывод {printed}")
            ok(len(its) == (2 if ctx["cmp"] == "<" else 3), f"L1 {k1}: витков {len(its)}")
            ok(exit_ctx["eq"] == (ctx["cmp"] == "<"), f"L1 {k1}: выход по равенству")
            for it in its + [exit_ctx]:
                c = dict(ctx, **fin, **it)
                for st in ls["per_iteration"]:
                    if it is exit_ctx and st["id"] != "cond":
                        continue
                    where = f"L1.{st['id']} {k1} i={it['i']}"
                    if st["kind"] == "choice":
                        check_choice(st, c, where)
                    else:
                        check_value_step(st, c, where)
            for st in L1["steps"]:
                c = dict(ctx, **fin)
                if st["kind"] == "choice":
                    check_choice(st, c, f"L1.{st['id']} {k1}")
                elif st["kind"] == "value":
                    e = check_value_step(st, c, f"L1.{st['id']} {k1}")
                    ok(e == printed[-2], f"L1 last {e} != {printed[-2]}")
            # сломанный цикл: не заканчивается и печатает i0
            br = next(s for s in L1["steps"] if s["id"] == "broken")
            src = "\n".join(render(l, ctx) for l in br["code"])
            p = subprocess.Popen([sys.executable, "-I", "-X", "utf8", "-c", src], stdout=subprocess.PIPE, text=True, encoding="utf-8", env=_ENV)
            try:
                p.wait(timeout=1)
                ok(False, f"L1 broken {k1}: программа закончилась")
            except subprocess.TimeoutExpired:
                p.kill()
                out = p.stdout.read(200)
                ok(set(out.split()) == {str(ctx["i0"])}, f"L1 broken: {out[:40]!r}")
        k2 = (ctx["a"], ctx["m"], ctx["c"], ctx["dd"])
        if k2 not in seen2:
            seen2.add(k2)
            for st in L2["steps"]:
                where = f"L2.{st['id']} {k2}"
                code = st.get("code", L2["code"])
                src = "\n".join(render(l, ctx) for l in code)
                if st["kind"] == "value":
                    e = check_value_step(st, ctx, where)
                    out, err = run_py(src)
                    ok(nums(out) == e and not err, f"{where}: python {nums(out)} != {e}")
                else:
                    corr = check_choice(st, ctx, where)
                    want = list(range(ctx["a"], ctx["b"] + 1))
                    for o in st["options"]:
                        first = render(o["text"], ctx).strip("`")
                        out, err = run_py(first + "\n    print(i)")
                        ok((nums(out) == want) == bool(o.get("correct")), f"{where} {first}: {nums(out)}")
        k3 = (ctx["pos"], ctx["p"])
        if k3 not in seen3:
            seen3.add(k3)
            ls = next(s for s in L3["steps"] if s["kind"] == "trace")
            code_tpl = L3["code"][ctx["pos"]]
            code, its, _, fin, pyout = check_trace_vs_python(code_tpl, ls["loop"], ls["acc"], ctx, f"L3 {k3}")
            for it in its:
                c = dict(ctx, **fin, **it)
                for st in ls["per_iteration"]:
                    where = f"L3.{st['id']} {k3} i={it['i']}"
                    if st["kind"] == "choice":
                        check_choice(st, c, where)
                    else:
                        check_value_step(st, c, where)
            for st in L3["steps"]:
                c = dict(ctx, **fin)
                where = f"L3.{st['id']} {k3}"
                if st["kind"] == "value":
                    e = render(pick_when(st["expect"], c, "value"), c)
                    ok(nums(pyout) == nums(e), f"{where}: python {nums(pyout)} != {e}")
                    for d in st["diagnose"]:
                        if "equals" in d and cond(d.get("when"), c):
                            ok(nums(render(d["equals"], c)) != nums(e), f"{where}: diag==ответ")
                    walk_strings(st, c, where)
                elif st["kind"] == "choice":
                    corr = check_choice(st, c, where)
                    src = "\n".join(render(l, ctx) for l in st["code"])
                    out, err = run_py(src)
                    ok(out.strip() == render(corr["text"], c), f"{where}: python {out!r} vs {corr['text']}")
    return n


ni = check_interpreter()
nl = check_loops()
print(f"комбинаций: интерпретатор {ni}, цикл-машина {nl}; проверок {checks}; ошибок {len(errors)}")
for e in errors[:60]:
    print(" -", e)


if __name__ == "__main__":
    sys.exit(1 if errors else 0)
