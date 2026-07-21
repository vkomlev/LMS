r"""Конвертация литерального markdown `*курсив*` в стемах задач (tsk-215, класс A5).

Родня tsk-212 (там был `**жирный**`). Проблема та же: источник эмитил markdown-курсив
`*слово*` в `tasks.task_content->>'stem'`, а контракт рендера SPW его не поддерживает
ни в одном режиме → ученик видит литеральные звёздочки (`*N*`, `*R*` курса 156 и т.п.).

ГЛАВНОЕ ОТЛИЧИЕ от bold (риск, зафиксирован в ТЗ): одиночная `*` массово сталкивается
с Python-кодом и математикой — умножение `a * b`, распаковка `*args`/`print(*seq)`,
glob/маски `*2025*`, ASCII-арт, KaTeX-степени. Поэтому фильтр СТРОЖЕ, чем в tsk-212:

1. SQL-предикат берёт только «настоящий курсив на границе слова»: открытие сразу за
   границей слова + буква/цифра, содержимое БЕЗ `* \n < > « » ; ?`, закрытие на границе
   слова. `;` убивает CSS (`*float:right;*`), `?` — маски (`*2??3*`), `<>` — HTML-таблицы
   и код, `«»` — символы-джокеры в кавычках.
2. Stem-level skip: скопированные страницы РЕШУ-ЕГЭ (CSS-мусор `float:right`/`class="wrapper"`/
   `<style`/`SubjectNav`/`pred_btn`) и ПРЕ-отрендеренный KaTeX (`class="katex"` — там `*`
   в `<annotation>` это математические маски цифр, не курсив).
3. Content skip: содержимое из одних цифр `^\d+$` → это маска (`*2025*`, `*4*`), не курсив.
4. Код-блоки ``` ``` маскируются перед матчингом (одиночная `*` внутри кода — оператор).
5. SKIP_IDS: 3730 (курс 157 — вложенный `**Повтори *k* … *S***`, недобитый tsk-212
   bold+italic; правится вручную, авто-конвертация исказит разметку).

Контракт рендера — тот же, что в tsk-212 (`SPW components/task/TaskContentRenderer.tsx`,
`shouldRenderAsHtml`): HTML-режим (тег из белого списка или format=html) → `*` литерально,
`<em>` работает; plain-режим → markdown italic НЕ поддержан. По факту прод-выборки все
кандидаты — HTML-режим, но ветка plain (снять `*`) сохранена для паритета с tsk-212.

Стратегия по режиму:
- HTML-стем → `*x*` → `<em>x</em>` (остаётся HTML, безопасно).
- plain-стем → снять `*` (добавление `<em>` флипнуло бы в HTML и сломало plain-препроцесс).

Безопасность (/db-check Режим записи):
- по умолчанию DRY-RUN: пишет before/after (.md + .json), НИЧЕГО не меняет;
- `--apply` — в одной транзакции по id: UPDATE → verify (нет остаточного курсива в
  тронутых) → commit, иначе rollback;
- курс 561 (архив) по умолчанию пропускается (--include-561 чтобы включить).

Прод-подключение: хост/роль явно, пароль — из env LEARN_PROD_DB_PASSWORD. Запуск --apply — под
хуком db_write_gate.py: префикс `DBCHECK_OK=1` (протокол /db-check пройден).

Запуск (из корня LMS):
  python scripts/fix_stem_markdown_italic_tsk215.py                       # dry-run (живые)
  DBCHECK_OK=1 python scripts/fix_stem_markdown_italic_tsk215.py --apply  # запись (живые)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import psycopg2
import psycopg2.extras

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- Прод-подключение (явно, без URL-кодирования; пароль только из env) -------
_PROD_PASSWORD = os.environ.get("LEARN_PROD_DB_PASSWORD")
if not _PROD_PASSWORD:
    raise RuntimeError(
        "Не задана переменная окружения LEARN_PROD_DB_PASSWORD "
        "(пароль роли lms_prod). Секрет в коде не хардкодится."
    )

PROD = dict(
    host="5.42.107.253",
    port=5432,
    dbname="learn",
    user="lms_prod",
    password=_PROD_PASSWORD,
)

# --- Зеркало SQL-предиката «настоящий одиночный курсив на границе слова» -------
# Открытие: за не-alnum/_/* + буква/цифра; содержимое без спецсимволов, конец непробел;
# закрытие: * на границе слова. Совпадает с Python ITALIC_RE ниже.
# `>` разрешён в содержимом (математика `N > 100`), `<` — запрещён (ловит спан через
# HTML-теги: `</td>`, `<p>` содержат `<`, так что курсив не перепрыгнет границу тега).
SQL_ITALIC_PRED = (
    r"(^|[^[:alnum:]_*])\*[[:alnum:]]"
    r"([^*\n<«»;?]*[^*[:space:]\n<«»;?])?"
    r"\*([^[:alnum:]_*]|$)"
)

# --- Stem-level исключения (скипаем стем целиком) -----------------------------
# Скопированные страницы РЕШУ-ЕГЭ: `*` внутри — CSS-мусор (`*float:right;*`, `*{}`).
# Пре-рендер KaTeX: `*` внутри `<annotation>` — математические маски цифр, не курсив.
STEM_EXCLUDE_RE = re.compile(
    r"float:right|class=\"wrapper\"|<style|SubjectNav|pred_btn|class=\"katex\"",
    re.IGNORECASE,
)

# --- Зеркало HTML_TAG_RE из SPW (см. TaskContentRenderer.tsx) ------------------
HTML_TAG_RE = re.compile(
    r"</?(p|ol|ul|li|strong|b|em|i|u|s|code|pre|blockquote|br|hr|h[1-6]"
    r"|table|thead|tbody|tr|td|th|a|img|span|div)\b[^>]*>",
    re.IGNORECASE,
)

# --- Курсив: строгий regex (мирроринг SQL-предиката) --------------------------
# Открытие `*` только на границе слова (перед — не \w и не *), сразу буква/цифра
# (не `_`); содержимое без `* \n < > « » ; ?`, конец непробел; закрытие на границе
# слова. Так `a * b`, `*args`, `*2??3*`, `*float:right;*` не матчатся.
ITALIC_RE = re.compile(
    r"(?<![\w*])\*"
    r"([^\W_]"
    r"(?:[^*\n<«»;?]*[^*\s\n<«»;?])?)"
    r"\*(?![\w*])"
)
# Маска: содержимое из одних цифр — литеральный шаблон (`*2025*`, `*4*`), не курсив.
DIGIT_ONLY_RE = re.compile(r"^\d+$")
# Код-блок ``` ... ``` — внутри одиночная `*` это оператор; маскируем перед матчингом.
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Стемы, исключённые из авто-конвертации (per-stem разбор, tsk-215):
SKIP_IDS: dict[int, str] = {}

# Спец-правка (вычисляется программно из стема — сохраняет точные байты `\r\n`).
# Стемы с bold-family разметкой, которую tsk-212 не добил (её BOLD_RE не берёт bold с `*`
# внутри), и с тройной звездой `***x***` (bold+italic сразу):
#   3730, 3623 (курс 157/143) — `**…*italic*…**` (bold с вложенным italic → недобиток tsk-212);
#   3705 (курс 156) — `***кратна трём***` (тройная звезда).
# Порядок: тройная → двойная-с-вложенным-italic → одиночный italic. Обычный конвертер этого
# не осилит (границы `**`/`***` ломают его логику), поэтому — отдельная функция.
MANUAL_IDS: set[int] = {3730, 3623, 3705}
TRIPLE_RE = re.compile(r"\*\*\*(.+?)\*\*\*", re.DOTALL)
BOLD_WRAP_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def convert_nested(stem: str) -> tuple[str, int]:
    """Тройная + bold-с-вложенным-italic + одиночный italic (для MANUAL_IDS). Код не трогаем."""
    masked, blocks = _mask_code(stem)

    def _fix_bold(m: re.Match[str]) -> str:
        inner = ITALIC_RE.sub(_repl_html, m.group(1))
        return f"<strong>{inner}</strong>"

    new_masked, nt = TRIPLE_RE.subn(r"<strong><em>\1</em></strong>", masked)
    new_masked, nb = BOLD_WRAP_RE.subn(_fix_bold, new_masked)
    new_masked, ni = ITALIC_RE.subn(_repl_html, new_masked)
    return _unmask_code(new_masked, blocks), nt + nb + ni


def is_html_mode(stem: str, fmt: str | None) -> bool:
    """Точное зеркало SPW shouldRenderAsHtml."""
    if isinstance(fmt, str) and fmt.lower() == "html":
        return True
    return bool(HTML_TAG_RE.search(stem))


def _mask_code(stem: str) -> tuple[str, list[str]]:
    """Заменить код-блоки плейсхолдерами, вернуть (маскированный, список_блоков)."""
    blocks: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        blocks.append(m.group(0))
        return f"\x00CODE{len(blocks) - 1}\x00"

    return CODE_FENCE_RE.sub(_sub, stem), blocks


def _unmask_code(stem: str, blocks: list[str]) -> str:
    for i, block in enumerate(blocks):
        stem = stem.replace(f"\x00CODE{i}\x00", block)
    return stem


def _repl_html(m: re.Match[str]) -> str:
    content = m.group(1)
    if DIGIT_ONLY_RE.match(content):  # маска-число, не курсив — не трогаем
        return m.group(0)
    return f"<em>{content}</em>"


def _repl_plain(m: re.Match[str]) -> str:
    content = m.group(1)
    if DIGIT_ONLY_RE.match(content):
        return m.group(0)
    return content


def convert(stem: str, html_mode: bool) -> tuple[str, int]:
    """Вернуть (новый_стем, число_замен) по режиму, не трогая код-блоки."""
    masked, blocks = _mask_code(stem)
    repl = _repl_html if html_mode else _repl_plain
    new_masked, n = ITALIC_RE.subn(repl, masked)
    return _unmask_code(new_masked, blocks), n


def residual_italic(stem: str, html_mode: bool) -> bool:
    """Сходимость: сработал бы конвертер тем же режимом повторно?

    Проверка тем же regex, что и конвертация (с учётом маскировки кода и
    пропуска маски-чисел), — иначе цифровая маска ложно считается «остаточной».
    """
    return convert(stem, html_mode)[1] > 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Конвертация markdown italic в стемах (tsk-215)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    parser.add_argument("--include-561", action="store_true", help="Включить архив-курс 561")
    args = parser.parse_args()

    conn = psycopg2.connect(**PROD)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=== tsk-215: markdown italic → контракт stem ===")
    print(f"Подключение: {PROD['user']}@{PROD['host']}/{PROD['dbname']}")
    print(f"Режим: {'APPLY' if args.apply else 'DRY-RUN'} | курс 561: "
          f"{'ВКЛючён' if args.include_561 else 'пропущен'}")

    where_561 = "" if args.include_561 else "AND course_id <> 561"
    cur.execute(
        f"""
        SELECT id, course_id, is_active,
               task_content->>'stem'   AS stem,
               task_content->>'format' AS fmt
        FROM tasks
        WHERE is_active = true
          AND task_content->>'stem' ~ %s
          {where_561}
        ORDER BY course_id, id
        """,
        (SQL_ITALIC_PRED,),
    )
    rows = cur.fetchall()

    changes: list[dict] = []
    skipped_no_change: list[int] = []
    skipped_manual: list[dict] = []
    skipped_stem_excl: list[int] = []
    for r in rows:
        if r["id"] in SKIP_IDS:
            skipped_manual.append({"id": r["id"], "course_id": r["course_id"],
                                   "reason": SKIP_IDS[r["id"]]})
            continue
        stem = r["stem"]
        if STEM_EXCLUDE_RE.search(stem):  # скопированная страница / пре-рендер KaTeX
            skipped_stem_excl.append(r["id"])
            continue
        html = is_html_mode(stem, r["fmt"])
        if r["id"] in MANUAL_IDS:
            new, n = convert_nested(stem)
        else:
            new, n = convert(stem, html)
        if n == 0 or new == stem:
            skipped_no_change.append(r["id"])
            continue
        changes.append({
            "id": r["id"],
            "course_id": r["course_id"],
            "is_active": r["is_active"],
            "mode": "html" if html else "plain",
            "replacements": n,
            "before": stem,
            "after": new,
            "residual_after": residual_italic(new, html),
        })

    n_html = sum(1 for c in changes if c["mode"] == "html")
    n_plain = sum(1 for c in changes if c["mode"] == "plain")
    n_residual = sum(1 for c in changes if c["residual_after"])
    print(f"\nКандидатов в выборке (предикат): {len(rows)}")
    print(f"К изменению: {len(changes)} (html→<em>: {n_html}, plain→снять: {n_plain})")
    print(f"Пропущено (SKIP_IDS, ручной разбор): {len(skipped_manual)} "
          f"{[s['id'] for s in skipped_manual]}")
    print(f"Пропущено (stem-level: страница/KaTeX): {len(skipped_stem_excl)}")
    print(f"Без изменений (предикат сработал, конвертер не нашёл курсив): {len(skipped_no_change)}")
    print(f"Остаточный курсив после конвертации (ТРЕБУЕТ ВНИМАНИЯ): {n_residual}")

    # --- Отчёт (всегда) ---
    reviews = PROJECT_ROOT / "reviews"
    reviews.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    tag = "with561" if args.include_561 else "live"
    mode = "apply" if args.apply else "dryrun"
    json_path = reviews / f"{stamp}-tsk215-stem-italic-{tag}-{mode}.json"
    json_path.write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = reviews / f"{stamp}-tsk215-stem-italic-{tag}-{mode}.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write(f"# tsk-215 — конвертация markdown italic в стемах ({'APPLY' if args.apply else 'DRY-RUN'})\n\n")
        f.write(f"- Дата: {stamp}\n- Курс 561: {'включён' if args.include_561 else 'пропущен'}\n")
        f.write(f"- Кандидатов: {len(rows)}; к изменению: {len(changes)} "
                f"(html: {n_html}, plain: {n_plain}); остаточный курсив: {n_residual}; "
                f"skip-manual: {len(skipped_manual)}; skip-stem: {len(skipped_stem_excl)}\n\n")
        for c in changes:
            flag = " ⚠️RESIDUAL" if c["residual_after"] else ""
            f.write(f"## id {c['id']} · курс {c['course_id']} · {c['mode']} · замен {c['replacements']}{flag}\n\n")
            f.write("**BEFORE:**\n\n```\n" + c["before"] + "\n```\n\n")
            f.write("**AFTER:**\n\n```\n" + c["after"] + "\n```\n\n")
    print(f"\nОтчёт: {md_path}\n       {json_path}")

    if not args.apply:
        print("\nDRY-RUN: изменения НЕ записаны. Для записи — DBCHECK_OK=1 ... --apply.")
        conn.rollback()
        conn.close()
        return 0

    # --- APPLY: транзакция, поштучный UPDATE по id ---
    if n_residual:
        print(f"\nОТКАЗ: {n_residual} стемов с остаточным курсивом после конвертации — "
              "разобрать вручную до записи. Ничего не записано.")
        conn.rollback()
        conn.close()
        return 1

    updated = 0
    for c in changes:
        cur.execute(
            """
            UPDATE tasks
            SET task_content = jsonb_set(task_content, '{stem}', to_jsonb(%s::text), false)
            WHERE id = %s
            """,
            (c["after"], c["id"]),
        )
        updated += cur.rowcount

    # verify в той же транзакции: у тронутых id не осталось настоящего курсива
    mode_by_id = {c["id"]: (c["mode"] == "html") for c in changes}
    ids = list(mode_by_id)
    cur.execute(
        "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY(%s)",
        (ids,),
    )
    bad = [row["id"] for row in cur.fetchall()
           if residual_italic(row["stem"], mode_by_id[row["id"]])]
    if bad:
        conn.rollback()
        conn.close()
        print(f"\nROLLBACK: остаточный курсив в {len(bad)} стемах после UPDATE: {bad[:20]} — записи НЕ было.")
        return 1

    conn.commit()
    print(f"\nCOMMIT: обновлено {updated} стемов ({len(changes)} задач). Остаточного курсива нет.")
    conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
