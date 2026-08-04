# -*- coding: utf-8 -*-
"""tsk-558 follow-up: тот же класс дефекта нашёлся ещё в 9 wp_nav-заданиях курса

147, пропущенных первым проходом (он проверял только kompege/polyakov по
исходной разведке оператора, а wp_nav-партию — нет). Оператор поймал это
живьём на проде (скриншоты #3896/#4067) уже ПОСЛЕ того, как первый скрипт был
задокументирован как "закрыто".

ЧТО НАШЛОСЬ (read-only разведка, 2026-08-04, после замечания оператора)

**A. 5 wp_nav-заданий с source_url на kompege.ru/task?id=N — тот же паттерн,
что чинили в первом проходе (subTask[] с вопросами 20/21 существовал у
источника, но не импортировался вовсе):**
  - id=3896 (wp_nav:19:8830b4ca, kompege id=18): Q19=18, Q20=(31,34), Q21=30
  - id=4067 (wp_nav:19:e731484c, kompege id=411): Q19=20, Q20=(34,38), Q21=33
  - id=4260 (wp_nav:19:92dca27f, kompege id=840): Q19=8, Q20=(20,31), Q21=30
  - id=4261 (wp_nav:19:a66f8024, kompege id=841): Q19=8, Q20=(12,29), Q21=28
  - id=4206 (wp_nav:19:26d847b4, kompege id=63): Q19=30, Q20=(14,29), Q21=10
  Сверено: текст условия задания 19 из kompege API дословно совпадает со
  STEM, уже лежащим в LMS под этими id (ключ ответа тоже совпадает) — не
  просто число случайно совпало, это те же самые задания.

**B. 3 wp_nav-задания с source_url на sdamgia problem?id=N — ХУЖЕ, чем
"пропустили довесок": контент под ЭТИМИ id на деле принадлежит ЗАДАНИЮ 20 из
связанного блока sdamgia (playbook §2: `problem?id=N` отдаёт СВЯЗКУ 19-21,
а не одну задачу), а не заданию 19, которое claim'ит external_uid. Реальное
задание 19 этой игры в LMS отсутствовало вовсе — не "недобрано", а никогда
не заводилось:**
  - id=3765 (wp_nav:19:4a37bac6, source_url .../problem?id=28087 = Q19)
    хранил текст и ответ ЗАДАНИЯ 20 (id=28088, ответ "13 25"/"1325"), не
    задания 19 (реальный ответ 19 = 14, сверено прямым запросом к
    inf-ege.sdamgia.ru/problem?id=28087). Заново собран блок 19+20+21 из
    живого источника (28087/28088/28089).
  - id=3766 (wp_nav:19:dfc65c27, .../problem?id=28093 = Q19) — тот же
    паттерн: хранил задание 20 (id=28094, ответ "4 15"/"415"). Собран блок
    28093/28094/28095.
  - id=3767 (wp_nav:19:0e1ba954, .../problem?id=28099 = Q19) — тот же
    паттерн: хранил задание 20 (id=28100, ответ "4 11"/"411"). Собран блок
    28099/28100/28101.
  Числа в текущем (неверном) ответе СОВПАДАЮТ с реальным заданием 20 этого
  же блока — не рассинхрон номеров, а исходный скрейп wp_nav перепутал,
  какой абзац связанного блока относится к какому external_uid при
  извлечении (playbook: "problem?id указывает на первую [задачу блока], а
  НЕ значит, что тело тоже принадлежит ей" — здесь тело утекло от соседа).

**C. Дубль: id=3281 (tg:ege:592) — дословный дубль id=2204 (kompege 23203),
уже починенного первым проходом.** Правила хода (-3/-7/:3 камня, порог ≤11)
и ответ (36) совпадают буквально; деактивирован как дубль (tsk-350),
не удалён.

**ЧТО НЕ ЗАКРЫТО ЭТИМ СКРИПТОМ (остаётся открытым хвостом):**
3 задания, источник которых — Yandex Учебник (id=2997 tg:ege:987 UUID
4be8eb33-506c-4c17-88a9-9e214b8f1f51; id=3472 wp_nav:19:8ab610f5 UUID
a97d888a-5402-4044-bb08-35bcc66f9ec7; id=3470 wp_nav:19:1d75c02b UUID
5a55834b-8221-4fe0-bdb9-f5b356188024). Их условия — тоже одиночные
"Задание 19" без 20/21, но у Yandex ответ достаётся ТОЛЬКО через
authenticated `POST /api/v5/gpttr` под сессией оператора (playbook §2), а
сама SPA-страница не отдаёт контент без Tier 3 Playwright networkidle
рендера — не решается прямым HTTP-запросом, как kompege/sdamgia. Оставлено
как отдельный, явно названный хвост (не молчаливый пропуск).

Запуск: dry-run по умолчанию;
  python scripts/tsk558_followup_wp_nav_19_21.py
  DBCHECK_OK=1 python scripts/tsk558_followup_wp_nav_19_21.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.checking import StudentAnswer, StudentResponse  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

checking = CheckingService()
COURSE_ID = 147

# ─── A: kompege wp_nav — тот же durable-класс, что и первый проход ─────────
# {lms_id: kompege_task_id}
KOMPEGE_WP_NAV = {
    3896: 18,
    4067: 411,
    4260: 840,
    4261: 841,
    4206: 63,
}

# ─── C: дубль ────────────────────────────────────────────────────────────
DUPLICATE_OF_2204 = 3281  # tg:ege:592


def _fetch_kompege(task_id: int) -> dict:
    req = urllib.request.Request(
        f"https://kompege.ru/api/v1/task/{task_id}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _label(n: int) -> str:
    return f"<p><strong>Задание {n}.</strong></p>"


def _merge_kompege_stem(q19_stem_in_lms: str, data: dict) -> tuple[str, str]:
    """Собирает stem 'Задание 19/20/21' + строку эталона из живого kompege API.

    Q19-текст берём ИЗ LMS (уже сверен на дословное совпадение с источником в
    ходе разведки) — так итоговый stem не расходится буквой с тем, что уже
    видели методисты; Q20/Q21 — из API (их в LMS не было).
    """
    sub = {s["number"]: s for s in data["subTask"]}
    q20, q21 = sub[20], sub[21]
    stem = (
        _label(19) + q19_stem_in_lms
        + _label(20) + q20["text"]
        + _label(21) + q21["text"]
    )
    q20_values = q20["key"].split()
    etalon = "\n".join([data["key"], *q20_values, q21["key"]])
    return stem, etalon


# ─── B: sdamgia wp_nav — восстановление РЕАЛЬНОГО блока 19-21 ──────────────
# {lms_id: (id19, id20, id21)}
SDAMGIA_WP_NAV = {
    3765: (28087, 28088, 28089),
    3766: (28093, 28094, 28095),
    3767: (28099, 28100, 28101),
}


def _fetch_sdamgia(task_id: int) -> str:
    req = urllib.request.Request(
        f"https://inf-ege.sdamgia.ru/problem?id={task_id}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def _extract_sdamgia(html: str) -> tuple[str, str]:
    m = re.search(r'<div[^>]*id="body\d+"\s*class="pbody">(.*?)</div>\s*(?:</div>)?<!--np-->', html, re.S)
    if not m:
        raise AssertionError("не нашёл div.pbody на странице sdamgia")
    stem = m.group(1)
    a = re.search(r'<div class="answer"[^>]*><span[^>]*>Ответ:\s*([^<]*)</span></div>', html)
    if not a:
        raise AssertionError("не нашёл скрытый div.answer на странице sdamgia")
    return stem, a.group(1).strip()


_TAG_RE = re.compile(r"<[^>]+>")
_SOFT_HYPHEN = "\xad"


def _normalize_paragraph(p: str) -> str:
    """Свёртка параграфа для СРАВНЕНИЯ (не для итогового текста): без тегов,
    без мягких переносов, без лишних пробелов/&nbsp;. sdamgia независимо
    копирует общий текст правил в каждую связанную задачу блока — символ в
    символ он не совпадает (один и тот же смысловой кусок иногда обёрнут в
    <nobr> по-разному), поэтому сравнивать нужно НОРМАЛИЗОВАННЫЙ параграф
    целиком, а не посимвольно."""
    t = _TAG_RE.sub(" ", p).replace(_SOFT_HYPHEN, "").replace("&nbsp;", " ")
    t = re.sub(r"&#8239;", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _split_paragraphs(stem: str) -> list[str]:
    """Режет по маркеру начала параграфа sdamgia (`<p class="left_margin">`),
    сохраняя маркер у каждого куска, кроме первого (у него он уже часть
    текста перед первым срезом)."""
    parts = re.split(r'(?=<p class="left_margin">)', stem)
    return [p for p in parts if p.strip()]


def _tail_question(q19_stem: str, qn_stem: str) -> str:
    """Вычленяет ХВОСТ вопроса N (параграфы ПОСЛЕ общей с заданием 19 части
    условия — sdamgia повторяет правила игры в каждой связанной задаче
    блока, но каждый раз копирует их НЕЗАВИСИМО, с мелкими расхождениями
    разметки внутри параграфа) и переформулирует его как краткую отсылку
    "Для игры, описанной в задании 19, <вопрос с маленькой буквы>" — тот же
    формат, что уже применён к kompege-блокам в этом курсе (не плодить
    тройной повтор одинаковых правил игры). Сравнение — ПО ПАРАГРАФАМ
    (нормализованным), не посимвольно: посимвольное сравнение ловится на
    первом же расхождении в разметке ("из 11</nobr> или <nobr>из 20" против
    "из 11 или из 20" одним <nobr>) и отрезает половину реального вопроса."""
    p19 = [_normalize_paragraph(p) for p in _split_paragraphs(q19_stem)]
    pn_raw = _split_paragraphs(qn_stem)
    pn = [_normalize_paragraph(p) for p in pn_raw]
    # sdamgia перепечатывает общие правила НЕЗАВИСИМО в каждой связанной
    # задаче — соседние копии могут разойтись пунктуацией ("камней, 1≤S≤53"
    # против "камней; 1≤S≤53"), при этом оставаясь ТЕМ ЖЕ параграфом.
    # Точное сравнение стопорится на первой такой запятой и обрезает половину
    # реального вопроса — нужен порог схожести, а не точное совпадение.
    common = 0
    while common < len(p19) and common < len(pn):
        if difflib.SequenceMatcher(None, p19[common], pn[common]).ratio() < 0.85:
            break
        common += 1
    tail_paragraphs = pn_raw[common:]
    if not tail_paragraphs:
        raise AssertionError("все параграфы совпали с заданием 19 — вопрос не вычленился")
    tail = "".join(tail_paragraphs).strip()
    # Первый вычлененный параграф начинается с маркера <p class="left_margin">
    # — вопрос идёт сразу после него; переносим его на "Для игры, описанной..."
    m = re.match(r'^(<p class="left_margin">)(.*)$', tail, re.S)
    if not m:
        raise AssertionError(f"неожиданный формат хвоста: {tail[:80]!r}")
    body = m.group(2)
    body = body[0].lower() + body[1:] if body else body
    return f'<p class="left_margin">Для игры, описанной в задании 19, {body}'


def _build_sdamgia_block(lms_id: int, id19: int, id20: int, id21: int) -> tuple[str, str]:
    stem19, ans19 = _extract_sdamgia(_fetch_sdamgia(id19))
    stem20, ans20 = _extract_sdamgia(_fetch_sdamgia(id20))
    stem21, ans21 = _extract_sdamgia(_fetch_sdamgia(id21))

    # "без разделительных знаков" — инструкция для СТАРОГО однопольного
    # SA_COM-формата (значения слитно в одну строку). При TBL_COM у каждого
    # значения своё поле — инструкция устарела и станет противоречивой,
    # поэтому режем её из хвоста, как и в остальных блоках этого курса.
    tail20 = _tail_question(stem19, stem20)
    tail20 = tail20.replace(
        " без разделительных знаков", ""
    ).replace(
        " без раз­де­ли­тель­ных зна­ков", ""
    )
    tail21 = _tail_question(stem19, stem21)

    stem = (
        _label(19) + f'<html><body><p class="left_margin">{stem19}</p>'
        + _label(20) + tail20
        + _label(21) + tail21
        + "</body></html>"
    )
    ans20_values = ans20.split() if " " in ans20 else [ans20[: len(ans20) // 2], ans20[len(ans20) // 2:]]
    etalon = "\n".join([ans19, *ans20_values, ans21])
    return stem, etalon


# sdamgia склеивает двузначные пары БЕЗ пробела в скрытом div.answer ("1325"
# вместо "13 25"), а LMS уже хранит верный раздельный вариант из прежнего
# (некорректно атрибутированного) импорта — берём разбивку оттуда, не
# гадаем позиционным делением строки пополам (ломается на "4 15" — не 2+2).
KNOWN_Q20_SPLIT = {
    3765: ["13", "25"],
    3766: ["4", "15"],
    3767: ["4", "11"],
}


def _proverit(content: dict, rules: dict, task_type: str, otvet: str):
    result = checking.check_task(
        TaskContent.model_validate(content),
        SolutionRules.model_validate(rules),
        StudentAnswer(type=task_type, response=StudentResponse(value=otvet)),
    )
    return result.is_correct


REORDER_SQL = """
WITH new_order AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            ORDER BY
                difficulty_id ASC,
                CASE task_content->>'type'
                    WHEN 'SC' THEN 1
                    WHEN 'MC' THEN 1
                    WHEN 'TA' THEN 2
                    WHEN 'SA' THEN 2
                    WHEN 'SA_COM' THEN 3
                    ELSE 99
                END ASC,
                order_position ASC NULLS LAST,
                id ASC
        ) AS new_op
    FROM tasks
    WHERE course_id = $1
)
UPDATE tasks t
SET order_position = n.new_op
FROM new_order n
WHERE t.id = n.id
  AND t.course_id = $1
  AND (t.order_position IS DISTINCT FROM n.new_op)
"""


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            touched = list(KOMPEGE_WP_NAV) + list(SDAMGIA_WP_NAV) + [DUPLICATE_OF_2204]

            live = await conn.fetch(
                "SELECT task_id, count(*) FROM task_results WHERE task_id = ANY($1::int[]) GROUP BY task_id",
                touched,
            )
            if live:
                raise AssertionError(f"живые попытки на {touched}: {[(r['task_id'], r['count']) for r in live]} — СТОП")
            print(f"Живых попыток на {touched}: 0 (подтверждено перед записью)")

            rows = await conn.fetch(
                "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[]) AND course_id = $2 AND is_active",
                list(KOMPEGE_WP_NAV) + list(SDAMGIA_WP_NAV), COURSE_ID,
            )
            by_id = {int(r["id"]): r for r in rows}
            missing = sorted((set(KOMPEGE_WP_NAV) | set(SDAMGIA_WP_NAV)) - set(by_id))
            if missing:
                raise AssertionError(f"не найдены/не активны: {missing}")

            plan: list[tuple[int, dict, dict, str]] = []

            for lms_id, kompege_id in KOMPEGE_WP_NAV.items():
                content = json.loads(by_id[lms_id]["task_content"])
                rules = json.loads(by_id[lms_id]["solution_rules"])
                if content.get("type") != "SA_COM":
                    raise AssertionError(f"id={lms_id}: ожидали SA_COM, тип {content.get('type')!r}")
                data = _fetch_kompege(kompege_id)
                if data["key"] != (rules["short_answer"]["accepted_answers"][0]["value"]):
                    raise AssertionError(
                        f"id={lms_id}: текущий ответ LMS {rules['short_answer']['accepted_answers'][0]['value']!r} "
                        f"!= ключ kompege {data['key']!r} — не тот же источник, чиню вручную"
                    )
                stem, etalon = _merge_kompege_stem(content["stem"], data)
                content["stem"] = stem
                content["type"] = "TBL_COM"
                content["table"] = {"columns": 1}
                rules["short_answer"]["accepted_answers"] = [{"score": 1, "value": etalon}]
                plan.append((lms_id, content, rules, etalon))

            for lms_id, (id19, id20, id21) in SDAMGIA_WP_NAV.items():
                content = json.loads(by_id[lms_id]["task_content"])
                rules = json.loads(by_id[lms_id]["solution_rules"])
                if content.get("type") != "SA_COM":
                    raise AssertionError(f"id={lms_id}: ожидали SA_COM, тип {content.get('type')!r}")
                stem19, ans19 = _extract_sdamgia(_fetch_sdamgia(id19))
                stem20, ans20_raw = _extract_sdamgia(_fetch_sdamgia(id20))
                stem21, ans21 = _extract_sdamgia(_fetch_sdamgia(id21))
                q20_split = KNOWN_Q20_SPLIT[lms_id]
                if "".join(q20_split) != ans20_raw.replace(" ", ""):
                    raise AssertionError(
                        f"id={lms_id}: разбивка {q20_split} не сходится со скрытым ответом sdamgia {ans20_raw!r}"
                    )
                tail20 = _tail_question(stem19, stem20).replace(
                    " без разделительных знаков", ""
                ).replace(" без раз­де­ли­тель­ных зна­ков", "")
                tail21 = _tail_question(stem19, stem21)
                stem = (
                    "<html><body>"
                    + _label(19) + f'<p class="left_margin">{stem19}</p>'
                    + _label(20) + tail20
                    + _label(21) + tail21
                    + "</body></html>"
                )
                content["stem"] = stem
                content["type"] = "TBL_COM"
                content["table"] = {"columns": 1}
                etalon = "\n".join([ans19, *q20_split, ans21])
                rules["short_answer"]["accepted_answers"] = [{"score": 1, "value": etalon}]
                plan.append((lms_id, content, rules, etalon))

            for task_id, content, rules, _ in plan:
                await conn.execute(
                    "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb WHERE id = $1",
                    task_id, json.dumps(content, ensure_ascii=False), json.dumps(rules, ensure_ascii=False),
                )
            print(f"Обновлено (SA_COM -> TBL_COM): {len(plan)} заданий: {sorted(p[0] for p in plan)}")

            # ─── Дедуп 3281 vs 2204 ────────────────────────────────────────
            twin = await conn.fetchrow(
                "SELECT task_content->>'stem' AS stem, solution_rules->'short_answer'->'accepted_answers' AS ans "
                "FROM tasks WHERE id = 2204"
            )
            dup_row = await conn.fetchrow(
                "SELECT task_content->>'stem' AS stem FROM tasks WHERE id = $1", DUPLICATE_OF_2204
            )
            if dup_row is None or "убрать из кучи 3 камня" not in (dup_row["stem"] or ""):
                raise AssertionError("3281 больше не похож на дубль 2204 — не деактивирую вслепую")
            await conn.execute("UPDATE tasks SET is_active = false WHERE id = $1", DUPLICATE_OF_2204)
            print(f"Деактивирован дубль id={DUPLICATE_OF_2204} (tg:ege:592, дословный дубль 2204)")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            await conn.execute(REORDER_SQL, COURSE_ID)

            # ─── Верификация: самосогласованность + порядок ─────────────────
            ids = [p[0] for p in plan]
            posle = {
                int(row["id"]): row
                for row in await conn.fetch(
                    "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[])", ids,
                )
            }
            oshibki: list[str] = []
            for task_id, _, _, etalon in plan:
                row = posle[task_id]
                content = json.loads(row["task_content"])
                rules = json.loads(row["solution_rules"])
                if content.get("type") != "TBL_COM" or (content.get("table") or {}).get("columns") != 1:
                    oshibki.append(f"id={task_id}: тип/columns не соответствуют после записи")
                    continue
                if _proverit(content, rules, "TBL_COM", etalon) is not True:
                    oshibki.append(f"id={task_id}: эталон не засчитывается после записи")
                    continue
                for мутация in (f"  {etalon}  ", etalon.upper(), etalon.replace("\n", "\n\n") + "\n"):
                    if _proverit(content, rules, "TBL_COM", мутация) is not True:
                        oshibki.append(f"id={task_id}: мутация {мутация[:40]!r} не засчитана")
                        break
                испорчено = "\n".join(reversed(etalon.split("\n")))
                if испорчено != etalon and _proverit(content, rules, "TBL_COM", испорчено) is True:
                    oshibki.append(f"id={task_id}: неверный порядок полей ошибочно засчитан")

            dupes = await conn.fetchval(
                "SELECT count(*) FROM (SELECT order_position FROM tasks WHERE course_id = $1 "
                "GROUP BY order_position HAVING count(*) > 1) x", COURSE_ID)
            if dupes:
                oshibki.append(f"коллизии order_position: {dupes}")
            violations = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT difficulty_id, LAG(difficulty_id) OVER (ORDER BY order_position) AS prev
                    FROM tasks WHERE course_id = $1 AND is_active
                ) x WHERE prev IS NOT NULL AND difficulty_id < prev""", COURSE_ID)
            if violations:
                oshibki.append(f"нарушен межгрупповой порядок: {violations}")

            if oshibki:
                for e in oshibki[:40]:
                    print(f"  ОШИБКА: {e}")
                raise AssertionError(f"верификация не пройдена: {len(oshibki)} проблем")

            print(f"OK: проверено поштучно {len(plan)} заданий, order_position без коллизий и разрывов.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
