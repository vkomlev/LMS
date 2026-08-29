# -*- coding: utf-8 -*-
"""tsk-558, третий проход: 2 из 4 оставшихся Yandex-заданий курса 147.

Оператор поправил репортированный ранее вывод "нужна авторизация": реально
`POST /api/v5/gpttr` для публичных задач ЕГЭ анонимный (см. живой прецедент
tsk-369, `scripts/tsk369_fetch_files.py:339` — "Авторизация НЕ нужна... метод
tsk-100 требовал входа оператора для закрытых подборок, для публичных задач
ЕГЭ хватает анонимного запроса"). Переиспользован тот же метод:
`public_get_variant_request_item` по UUID коллекции (уже был записан в
`source_url` двух `wp_nav`-заданий) → в ответе задания под `number` 19/20/21.

ЧТО НАШЛОСЬ
- id=3472 (wp_nav:19:8ab610f5, коллекция a97d888a-5402-4044-bb08-35bcc66f9ec7):
  Q19=17 (сверено — совпадает с уже лежащим в LMS ответом, значит верная
  задача), Q20=9, Q21=8. У ЭТОЙ игры вопросы 20 и 21 — каждый ОДНО значение
  (не "1+2+1", как у большинства блоков курса — форма ответа зависит от
  конкретной формулировки вопроса, не от шаблона), итог 3 поля.
- id=3470 (wp_nav:19:1d75c02b, коллекция 5a55834b-8221-4fe0-bdb9-f5b356188024):
  Q19=22 (сверено), Q20=(18,21), Q21=17. Итог 4 поля (1+2+1).

ЧТО НЕ ЗАКРЫТО ЭТИМ СКРИПТОМ
id=2997 (tg:ege:987) и id=3329 (tg:ege:518) — UUID КОЛЛЕКЦИИ для них
неизвестен (TG-пост принёс только UUID самой задачи 19, не подборки, откуда
она взята; `4be8eb33-506c-4c17-88a9-9e214b8f1f51` у 2997 резолвится только
как отдельная задача через `get_task_by_id`, без пути к соседям 20/21 — ни
`task_series_id`, ни `category_id` не открылись известными вызовами API).
Остаются открытым хвостом, см. трекер.

Запуск: dry-run по умолчанию;
  python scripts/tsk558_yandex_wp_nav_19_21.py
  DBCHECK_OK=1 python scripts/tsk558_yandex_wp_nav_19_21.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import html as html_mod
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

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0 Safari/537.36")
YANDEX_SUBJECT_ID = "ac7328ca-dd3d-4bea-8566-9c3177273a57"
_sk: list[str] = []

# {lms_id: (collection_uuid, ожидаемый текущий Q19-ответ — самопроверка, что
#  коллекция резолвит ТУ ЖЕ задачу, что уже лежит в LMS, а не совпадение)}
YANDEX_WP_NAV = {
    3472: ("a97d888a-5402-4044-bb08-35bcc66f9ec7", "17"),
    3470: ("5a55834b-8221-4fe0-bdb9-f5b356188024", "22"),
}


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _yandex_call(payload: list[dict]):
    if not _sk:
        raw = _fetch("https://education.yandex.ru/api/v5/get-csrf-token")
        _sk.append(json.loads(raw)["sk"])
    req = urllib.request.Request(
        "https://education.yandex.ru/api/v5/gpttr",
        data=json.dumps(payload).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/json", "x-csrf-token": _sk[0]},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _variant_tasks(variant_id: str) -> list[dict]:
    data = _yandex_call([{"type": "public_get_variant_request_item", "variant_id": variant_id,
                           "params": {"subject_id": YANDEX_SUBJECT_ID}}])
    return (data or {}).get("tasks") or []


def _markdown_to_html(raw_md: str) -> str:
    """Yandex отдаёт условие Markdown-подобной строкой (**жирный**, \\r\\n\\r\\n = абзац).
    Экранируем сначала (без этого `<`/`>` из текста стали бы тегами), потом
    восстанавливаем только жирный.

    tsk-731: «большего в этих текстах нет» было неверно — есть ещё маркированные
    списки (пункты «* ...» через ОДИНОЧНЫЙ перевод строки внутри одного абзаца).
    Именно на них и порвалось: абзац-список уезжал в один `<p>` с переводами
    строк внутри, а разбор ниже его терял. Теперь такой абзац сразу становится
    `<ul>`, и переводов строк внутри абзацев не остаётся вовсе.
    """
    s = html_mod.escape(raw_md)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = s.replace("\xa0", " ")
    paras = [p.strip() for p in re.split(r"\r?\n\r?\n", s) if p.strip()]
    out: list[str] = []
    for p in paras:
        lines = [ln.strip() for ln in re.split(r"\r?\n", p) if ln.strip()]
        if lines and all(ln.startswith(("* ", "- ")) for ln in lines):
            items = "".join(f"<li>{ln[2:].strip()}</li>" for ln in lines)
            out.append(f"<ul>{items}</ul>")
        else:
            out.append("<p>" + " ".join(lines) + "</p>")
    return "".join(out)


def _task_stem(task: dict) -> str:
    markup = task.get("markup") or {}
    parts = [blk.get("content", {}).get("text") or ""
             for blk in markup.get("layout") or [] if blk.get("kind") == "text"]
    return _markdown_to_html("\n\n".join(parts))


def _task_answer_values(task: dict) -> list[str]:
    """`content.correct_answers` — строка (одно значение) ИЛИ список (список
    строк / список списков "table_match" для двузначных ответов)."""
    acl = (task.get("markup") or {}).get("answer_control_layout") or []
    if not acl:
        raise AssertionError("нет answer_control_layout — эталона нет")
    ca = (acl[0].get("content") or {}).get("correct_answers")
    if isinstance(ca, str):
        return [ca]
    if isinstance(ca, list):
        flat: list[str] = []
        for item in ca:
            flat.extend(item) if isinstance(item, list) else flat.append(item)
        return flat
    raise AssertionError(f"неожиданный формат correct_answers: {ca!r}")


def _label(n: int) -> str:
    return f"<p><strong>Задание {n}.</strong></p>"


def _tail_after_common_paragraphs(q19_html: str, qn_html: str) -> str:
    """Как и у sdamgia-блоков — Q20/Q21 у Yandex тоже несут условие целиком;
    режем на параграфы (`<p>`) и берём то, что идёт ПОСЛЕ общего с Q19
    префикса. Здесь (в отличие от sdamgia) абзацы совпадают дословно — Yandex
    не перепечатывает текст заново для каждого вопроса подборки, поэтому
    точное сравнение достаточно (в отличие от sdamgia, экранированные
    </p><p> совпадают буква в букву)."""
    # tsk-731: блоками считаем и `<p>`, и `<ul>` (списки условий), и берём их с
    # `re.S`. Прежний шаблон `<p>.*?</p>` без `re.S` молча выбрасывал любой блок
    # с переводом строки внутри — а это ровно список из двух условий заданий
    # 20/21. Так у 3470 и 3472 из условия исчезло то, ради чего они существуют.
    blocks_re = re.compile(r"<p>.*?</p>|<ul>.*?</ul>", re.S)
    p19 = blocks_re.findall(q19_html)
    pn = blocks_re.findall(qn_html)
    common = 0
    while common < len(p19) and common < len(pn) and p19[common] == pn[common]:
        common += 1
    tail = "".join(pn[common:])
    if not tail:
        raise AssertionError("общий префикс параграфов совпал целиком — вопрос не вычленился")
    # Первая буква вопроса — с маленькой, вопрос идёт как отсылка к заданию 19.
    m = re.match(r"^<p>(.)(.*)$", tail, re.S)
    first, rest = m.group(1), m.group(2)
    return f"<p>Для игры, описанной в задании 19, {first.lower()}{rest}"


REORDER_SQL = """
WITH new_order AS (
    SELECT id, ROW_NUMBER() OVER (
        ORDER BY difficulty_id ASC,
            CASE task_content->>'type'
                WHEN 'SC' THEN 1 WHEN 'MC' THEN 1 WHEN 'TA' THEN 2 WHEN 'SA' THEN 2
                WHEN 'SA_COM' THEN 3 ELSE 99 END ASC,
            order_position ASC NULLS LAST, id ASC
    ) AS new_op
    FROM tasks WHERE course_id = $1
)
UPDATE tasks t SET order_position = n.new_op FROM new_order n
WHERE t.id = n.id AND t.course_id = $1 AND (t.order_position IS DISTINCT FROM n.new_op)
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


def _proverit(content: dict, rules: dict, task_type: str, otvet: str):
    result = checking.check_task(
        TaskContent.model_validate(content), SolutionRules.model_validate(rules),
        StudentAnswer(type=task_type, response=StudentResponse(value=otvet)),
    )
    return result.is_correct


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            ids = list(YANDEX_WP_NAV)
            live = await conn.fetch(
                "SELECT task_id, count(*) FROM task_results WHERE task_id = ANY($1::int[]) GROUP BY task_id", ids)
            if live:
                raise AssertionError(f"живые попытки на {ids}: {[(r['task_id'], r['count']) for r in live]} — СТОП")
            print(f"Живых попыток на {ids}: 0 (подтверждено перед записью)")

            rows = await conn.fetch(
                "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[]) AND course_id = $2 AND is_active",
                ids, COURSE_ID)
            by_id = {int(r["id"]): r for r in rows}
            missing = sorted(set(ids) - set(by_id))
            if missing:
                raise AssertionError(f"не найдены/не активны: {missing}")

            plan: list[tuple[int, dict, dict, str]] = []
            for lms_id, (uid, expected_q19) in YANDEX_WP_NAV.items():
                content = json.loads(by_id[lms_id]["task_content"])
                rules = json.loads(by_id[lms_id]["solution_rules"])
                if content.get("type") != "SA_COM":
                    raise AssertionError(f"id={lms_id}: ожидали SA_COM, тип {content.get('type')!r}")

                tasks = {t.get("number"): t for t in _variant_tasks(uid)}
                for n in (19, 20, 21):
                    if n not in tasks:
                        raise AssertionError(f"id={lms_id}: в коллекции {uid} нет задания №{n}")
                q19_answers = _task_answer_values(tasks[19])
                if q19_answers != [expected_q19]:
                    raise AssertionError(
                        f"id={lms_id}: ответ Q19 источника {q19_answers} != ожидаемому {[expected_q19]} "
                        "— похоже, коллекция резолвит ДРУГУЮ задачу, не чиню вслепую"
                    )
                q19_stem = _task_stem(tasks[19])
                q20_stem = _task_stem(tasks[20])
                q21_stem = _task_stem(tasks[21])
                q20_answers = _task_answer_values(tasks[20])
                q21_answers = _task_answer_values(tasks[21])

                tail20 = _tail_after_common_paragraphs(q19_stem, q20_stem)
                tail21 = _tail_after_common_paragraphs(q19_stem, q21_stem)
                stem = _label(19) + q19_stem + _label(20) + tail20 + _label(21) + tail21
                content["stem"] = stem
                content["type"] = "TBL_COM"
                content["table"] = {"columns": 1}

                etalon = "\n".join([*q19_answers, *q20_answers, *q21_answers])
                rules["short_answer"]["accepted_answers"] = [{"score": 1, "value": etalon}]
                plan.append((lms_id, content, rules, etalon))

            for task_id, content, rules, _ in plan:
                await conn.execute(
                    "UPDATE tasks SET task_content = $2::jsonb, solution_rules = $3::jsonb WHERE id = $1",
                    task_id, json.dumps(content, ensure_ascii=False), json.dumps(rules, ensure_ascii=False))
            print(f"Обновлено (SA_COM -> TBL_COM): {len(plan)} заданий: {sorted(p[0] for p in plan)}")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            await conn.execute(REORDER_SQL, COURSE_ID)

            ids2 = [p[0] for p in plan]
            posle = {int(r["id"]): r for r in await conn.fetch(
                "SELECT id, task_content, solution_rules FROM tasks WHERE id = ANY($1::int[])", ids2)}
            oshibki: list[str] = []
            for task_id, _, _, etalon in plan:
                row = posle[task_id]
                content = json.loads(row["task_content"])
                rules = json.loads(row["solution_rules"])
                if content.get("type") != "TBL_COM" or (content.get("table") or {}).get("columns") != 1:
                    oshibki.append(f"id={task_id}: тип/columns не соответствуют")
                    continue
                if _proverit(content, rules, "TBL_COM", etalon) is not True:
                    oshibki.append(f"id={task_id}: эталон не засчитывается")
                    continue
                for мутация in (f"  {etalon}  ", etalon.upper(), etalon.replace("\n", "\n\n") + "\n"):
                    if _proverit(content, rules, "TBL_COM", мутация) is not True:
                        oshibki.append(f"id={task_id}: мутация {мутация[:40]!r} не засчитана")
                        break
                if "\n" in etalon:
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
