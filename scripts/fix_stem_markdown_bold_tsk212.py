"""Конвертация литерального markdown `**жирный**` в стемах задач (tsk-212, класс A5).

Проблема (разобрана в ТЗ, НЕ баг рендера): источник эмитил markdown `**bold**`
в `tasks.task_content->>'stem'`, а контракт рендера SPW его не поддерживает ни в
одном режиме → ученик видит литеральные звёздочки.

Контракт рендера — `SPW components/task/TaskContentRenderer.tsx`:
  shouldRenderAsHtml(html, format) = (format=='html') ИЛИ HTML_TAG_RE.test(html)
  HTML_TAG_RE — реальный тег из белого списка (p|strong|code|pre|...\b[^>]*>).
  HTML-режим → SanitizedHTML (markdown НЕ парсится, `**` литерально, `<strong>` работает).
  plain-режим → многострочность/code-fence/KaTeX/linkify/spoiler, но markdown bold НЕ поддержан.

Стратегия конвертации ЗАВИСИТ от режима конкретного стема (не слепой mass-UPDATE):
- stem уже в HTML-режиме → `**X**` → `<strong>X</strong>` (остаётся HTML, безопасно).
- stem в plain-режиме → снять `**` (текст без выделения); добавление `<strong>` флипнуло
  бы стем в HTML-режим и сломало plain-preprocess (KaTeX/code-fence).

Защита от ложных срабатываний:
- В выборку берём ТОЛЬКО стемы с настоящим парным bold на границе слова
  (SQL-предикат SQL_BOLD_PRED) → чистая математическая степень `7**170` (курс 142,
  задания на системы счисления) НЕ попадает.
- HTML-режим: пермиссивный regex (bold допускается внутри слова: `с**час**тливым`,
  `'**0**XY..**1**'`) — в html-целях нет `**`-арифметики (KaTeX пишет `$..$`).
- plain-режим: СТРОГИЙ regex с границей слова — чтобы питоновская степень `2**3`
  в тех же Python-стемах не склеилась с настоящим bold.

Безопасность (/db-check Режим записи):
- по умолчанию DRY-RUN: пишет before/after отчёт (.md + .json), НИЧЕГО не меняет в БД;
- `--apply` пишет В ОДНОЙ ТРАНЗАКЦИИ по id: UPDATE → verify (не осталось настоящего
  bold в тронутых) → commit, иначе rollback;
- курс 561 (архив legacy) по умолчанию ПРОПУСКАЕТСЯ (--include-561 чтобы включить).

Прод-подключение задано явно (хост 5.42.107.253, роль lms_prod). Запуск --apply —
под хуком db_write_gate.py: префикс `DBCHECK_OK=1` (протокол /db-check пройден).

Запуск (из корня LMS):
  python scripts/fix_stem_markdown_bold_tsk212.py                       # dry-run (живые курсы)
  python scripts/fix_stem_markdown_bold_tsk212.py --include-561         # dry-run + архив 561
  DBCHECK_OK=1 python scripts/fix_stem_markdown_bold_tsk212.py --apply  # запись (живые)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import psycopg2
import psycopg2.extras

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- Прод-подключение (явно, без URL-кодирования) -----------------------------
PROD = dict(
    host="5.42.107.253",
    port=5432,
    dbname="learn",
    user="lms_prod",
    password="%1MVd16z~h8I=f",
)

# --- Зеркало предиката "настоящий парный bold на границе слова" (совпадает с Python) --
SQL_BOLD_PRED = r"(^|[^[:alnum:]*])\*\*[^[:space:]*]([^*\n]*[^[:space:]*])?\*\*"

# --- Зеркало HTML_TAG_RE из SPW (см. TaskContentRenderer.tsx) ------------------
HTML_TAG_RE = re.compile(
    r"</?(p|ol|ul|li|strong|b|em|i|u|s|code|pre|blockquote|br|hr|h[1-6]"
    r"|table|thead|tbody|tr|td|th|a|img|span|div)\b[^>]*>",
    re.IGNORECASE,
)

# Контент bold: `[^*]+?` — допускаем перенос строки внутри (bold `**второго\nэлемента**`,
# id 269), но НЕ `*` (иначе съест соседние пары и ASCII-`*****`); ленивый — берёт ближайшую пару.
# HTML-режим: пермиссивный — bold где угодно, в т.ч. внутри слова (`с**час**`, `'**0**XY'`);
# в html-целях нет `**`-арифметики (KaTeX пишет `$..$`, чистые `7**170` отсеяны предикатом).
BOLD_HTML_RE = re.compile(r"(?<!\*)\*\*(?!\s)([^*]+?)(?<!\s)\*\*(?!\*)")
# Вложенный bold+италик: `**Вперёд *n***` → `<strong>Вперёд <em>n</em></strong>` (Черепаха,
# курс 157, id 3715/3730). Применяется в html ДО обычного bold; на нормальном `**X**` не срабатывает.
NESTED_BI_RE = re.compile(r"\*\*([^*]+?)\*([^*\n]+?)\*\*\*")
# plain-режим: строгий — открытие/закрытие только на границе слова (не \w и не *),
# чтобы питоновская степень 2**3 в тех же Python-стемах не склеилась с bold.
# \w в re для str = Unicode (вкл. кириллицу).
BOLD_PLAIN_RE = re.compile(r"(?<![\w*])\*\*(?!\s)([^*]+?)(?<!\s)\*\*(?![\w*])")

# Стемы, исключённые из авто-конвертации (per-stem разбор, tsk-212):
# 3356 (курс 154) — `(x2−x1)**2 +(y2−y1)**2)**0,5`: `**` здесь математическая степень
#   в формуле расстояния, настоящего bold НЕТ. Предикат зацепил `)**` как границу слова.
#   Трогать нельзя — это не наш дефект (звёздочки задуманы как математика, не как выделение).
SKIP_IDS: dict[int, str] = {
    3356: "чистая математическая степень в формуле расстояния, bold отсутствует",
}
# Подозрения на арифметику в <strong>, проверенные вручную и признанные КОРРЕКТНЫМИ:
# 3576 (курс 151) — пример-строка '**0**XY..**1**..**23**..**4**': автор выделил hex-цифры,
#   обёртка цифр в <strong> — верна.
KNOWN_OK_SUSPECT: set[int] = {3576}


def is_html_mode(stem: str, fmt: str | None) -> bool:
    """Точное зеркало SPW shouldRenderAsHtml."""
    if isinstance(fmt, str) and fmt.lower() == "html":
        return True
    return bool(HTML_TAG_RE.search(stem))


def convert(stem: str, html_mode: bool) -> tuple[str, int]:
    """Вернуть (новый_стем, число_замен) по режиму."""
    if html_mode:
        new, n1 = NESTED_BI_RE.subn(r"<strong>\1<em>\2</em></strong>", stem)
        new, n2 = BOLD_HTML_RE.subn(r"<strong>\1</strong>", new)
        n = n1 + n2
    else:
        new, n = BOLD_PLAIN_RE.subn(r"\1", stem)
    return new, n


def residual_bold(stem: str, html_mode: bool) -> bool:
    """Сходимость: сработал бы конвертер тем же режимом повторно?

    Проверка ведётся ТЕМ ЖЕ regex, что и конвертация, — иначе ASCII-`*****` в
    code-fence (plain) ложно считается «остаточным bold». Конвертерный regex
    требует непробельного не-`*` контента, поэтому строку из одних `*` не берёт.
    """
    return convert(stem, html_mode)[1] > 0


# Подозрительно: <strong> обёрнут вокруг только цифр/операторов — возможный
# ложный захват арифметики (для ручного глаза в dry-run).
SUSPECT_ARITH_RE = re.compile(r"<strong>[\d\s+\-*/().,=]+</strong>")


def main() -> int:
    parser = argparse.ArgumentParser(description="Конвертация markdown bold в стемах (tsk-212)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    parser.add_argument("--include-561", action="store_true", help="Включить архив-курс 561")
    args = parser.parse_args()

    conn = psycopg2.connect(**PROD)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=== tsk-212: markdown bold → контракт stem ===")
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
        WHERE task_content->>'stem' ~ %s
          {where_561}
        ORDER BY course_id, id
        """,
        (SQL_BOLD_PRED,),
    )
    rows = cur.fetchall()

    changes: list[dict] = []
    skipped_no_change: list[int] = []
    skipped_manual: list[dict] = []
    suspects: list[dict] = []
    for r in rows:
        if r["id"] in SKIP_IDS:
            skipped_manual.append({"id": r["id"], "course_id": r["course_id"],
                                   "reason": SKIP_IDS[r["id"]]})
            continue
        stem = r["stem"]
        html = is_html_mode(stem, r["fmt"])
        new, n = convert(stem, html)
        if n == 0 or new == stem:
            skipped_no_change.append(r["id"])
            continue
        rec = {
            "id": r["id"],
            "course_id": r["course_id"],
            "is_active": r["is_active"],
            "mode": "html" if html else "plain",
            "replacements": n,
            "before": stem,
            "after": new,
            "residual_after": residual_bold(new, html),
        }
        changes.append(rec)
        if html and SUSPECT_ARITH_RE.search(new):
            suspects.append(rec)

    n_html = sum(1 for c in changes if c["mode"] == "html")
    n_plain = sum(1 for c in changes if c["mode"] == "plain")
    n_residual = sum(1 for c in changes if c["residual_after"])
    unknown_suspects = [s for s in suspects if s["id"] not in KNOWN_OK_SUSPECT]
    print(f"\nКандидатов в выборке: {len(rows)}")
    print(f"К изменению: {len(changes)} (html→<strong>: {n_html}, plain→снять: {n_plain})")
    print(f"Пропущено (ручной разбор, SKIP_IDS): {len(skipped_manual)} "
          f"{[s['id'] for s in skipped_manual]}")
    print(f"Без изменений (предикат сработал, но конвертер не нашёл пары): {len(skipped_no_change)}")
    print(f"Остаточный bold после конвертации (ТРЕБУЕТ ВНИМАНИЯ): {n_residual}")
    print(f"Подозрение на арифметику в <strong>: всего {len(suspects)}, "
          f"из них НЕизвестных (блокируют apply): {len(unknown_suspects)}")
    if suspects:
        for s in suspects:
            print(f"  SUSPECT id={s['id']} c={s['course_id']}: {SUSPECT_ARITH_RE.findall(s['after'])}")

    # --- Отчёт (всегда) ---
    reviews = PROJECT_ROOT / "reviews"
    reviews.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    tag = "with561" if args.include_561 else "live"
    json_path = reviews / f"{stamp}-tsk212-stem-bold-{tag}-{'apply' if args.apply else 'dryrun'}.json"
    json_path.write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = reviews / f"{stamp}-tsk212-stem-bold-{tag}-{'apply' if args.apply else 'dryrun'}.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write(f"# tsk-212 — конвертация markdown bold в стемах ({'APPLY' if args.apply else 'DRY-RUN'})\n\n")
        f.write(f"- Дата: {stamp}\n- Курс 561: {'включён' if args.include_561 else 'пропущен'}\n")
        f.write(f"- Кандидатов: {len(rows)}; к изменению: {len(changes)} "
                f"(html: {n_html}, plain: {n_plain}); остаточный bold: {n_residual}; "
                f"подозрений-арифметики: {len(suspects)}\n\n")
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
        print(f"\nОТКАЗ: {n_residual} стемов с остаточным bold после конвертации — "
              "разобрать вручную до записи. Ничего не записано.")
        conn.rollback()
        conn.close()
        return 1
    if unknown_suspects:
        print(f"\nОТКАЗ: {len(unknown_suspects)} новых подозрений на арифметику в <strong> "
              f"(id {[s['id'] for s in unknown_suspects]}) — проверить вручную и внести в "
              "KNOWN_OK_SUSPECT или SKIP_IDS до записи. Ничего не записано.")
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

    # verify в той же транзакции: у тронутых id не осталось настоящего bold
    mode_by_id = {c["id"]: (c["mode"] == "html") for c in changes}
    ids = list(mode_by_id)
    cur.execute(
        "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY(%s)",
        (ids,),
    )
    bad = [row["id"] for row in cur.fetchall()
           if residual_bold(row["stem"], mode_by_id[row["id"]])]
    if bad:
        conn.rollback()
        conn.close()
        print(f"\nROLLBACK: остаточный bold в {len(bad)} стемах после UPDATE: {bad[:20]} — записи НЕ было.")
        return 1

    conn.commit()
    print(f"\nCOMMIT: обновлено {updated} стемов ({len(changes)} задач). Остаточного bold нет.")
    conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
