# -*- coding: utf-8 -*-
"""tsk-947: убрать критерий приёмки из условия заданий QA-курсов.

Методист, сочиняя курсы «Ручное тестирование» и «Веб-скрейпинг», писал
критерий проверки прямо в текст задания — хвостом «Критерий приёмки: …» или
«Принято, если: …». Ученик видит разбор верного ответа. Утром 2026-09-15
(tsk-590) критерии 139 заданий из очереди вычитки были разложены в
`solution_rules.grading_criteria` (approved, reviewed_by=2), но хвост из
условия никто не убирал. Ещё 40 заданий тех же курсов с эталоном/рубрикой
несут тот же хвост, а критериев в правиле у них нет.

Скрипт делает три согласованные вещи (иначе LMS и исходник разъедутся):

1. `--source` — правит исходник CreateCourses (`exports/wp-blocks/*.json`
   обоих курсов и собранный граф `qa-manual-lms-graph.json`): хвост
   вырезается из `stem` и кладётся дословно в `review_note` блока `task` —
   поле, которое публикатор ContentBackbone научится переносить в
   `grading_criteria` (tsk-946).
2. `--dry-run` — читает задания из боевого LMS через API (сервисный ключ в
   `LMS_API_KEY`), показывает «было → стало» и пишет отчёт JSON, ничего
   не меняя.
3. `--apply` — `PATCH /tasks/{id}` с новым `task_content` (только `stem`;
   правило не трогаем — критерии там уже есть). Для 40 заданий без критериев
   в правиле (`--with-reference`) дополнительно кладёт хвост в
   `grading_criteria`, разбив на пункты; статус — по решению оператора
   (`--status approved|draft`). PATCH ставит `content_provenance=manual_web`,
   поэтому переиздание из исходника эти задания больше не перезапишет —
   ради этого и правится исходник (п. 1).

Без флагов — локальный план по исходнику: сколько блоков, образцы «было → стало».

Запуск (после протокола /db-check, снимок и выборка — в reviews/2026-09-15-tsk947-*.md):
    python scripts/tsk947_move_criteria.py                # план по исходнику
    python scripts/tsk947_move_criteria.py --source       # правка исходника
    LMS_API_KEY=... python scripts/tsk947_move_criteria.py --dry-run
    LMS_API_KEY=... python scripts/tsk947_move_criteria.py --apply [--with-reference --status draft]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("tsk947")

COURSES_DIR = Path(r"D:\Work\CreateCourses\courses")
SOURCE_DIRS = {
    "ruchnoe-testirovanie": COURSES_DIR / "ruchnoe-testirovanie" / "exports" / "wp-blocks",
    "veb-skrejping-python": COURSES_DIR / "veb-skrejping-python" / "exports" / "wp-blocks",
}
API_BASE = os.environ.get("LMS_API_BASE", "https://api.learn.victor-komlev.ru/api/v1")

#: Оператор, подтвердивший критерии (victor.komlev@mail.ru).
REVIEWED_BY = 2

#: 139 заданий очереди критериев: хвост в условии, критерии уже в правиле
#: (tsk-590, 2026-09-15). Снимок read-only SQL по проду 2026-09-15.
QUEUE_IDS: list[int] = [
    8011, 8023, 8041, 8052, 8064, 8075, 8085, 8097, 8108, 8119, 8153, 8164, 8172, 8173,
    8185, 8186, 8187, 8196, 8197, 8198, 8206, 8220, 8231, 8239, 8251, 8252, 8276, 8278,
    8289, 8290, 8301, 8302, 8312, 8313, 8314, 8325, 8337, 8345, 8346, 8358, 8359, 8370,
    8381, 8392, 8403, 8415, 8426, 8435, 8446, 8457, 8469, 8470, 8480, 8481, 8491, 8492,
    8691, 8708, 8718, 8735, 8752, 8985, 8986, 8995, 8996, 9005, 9006, 9015, 9016, 9025,
    9026, 9035, 9036, 9165, 9166, 9175, 9176, 9185, 9186, 9195, 9196, 9205, 9206, 9215,
    9216, 9225, 9226, 9235, 9236, 9245, 9246, 9255, 9256, 9265, 9266, 9275, 9276, 9285,
    9286, 9295, 9296, 9305, 9306, 9315, 9316, 9325, 9326, 9335, 9336, 9345, 9346, 9355,
    9356, 9365, 9366, 9375, 9376, 9385, 9386, 9395, 9396, 9405, 9406, 9726, 9727, 9736,
    9737, 9746, 9747, 9756, 9757, 9763, 9764, 10396, 10409, 10431, 10566, 10572, 10577,
]

#: 40 заданий тех же курсов с эталоном (SA_COM), вариантами (SC) или рубрикой
#: (TA): хвост в условии есть, критериев в правиле нет. Тот же снимок.
REFERENCE_IDS: list[int] = [
    9759, 9760, 9761, 9762, 10397, 10400, 10404, 10418, 10422, 10426, 10436, 10439,
    10443, 10447, 10451, 10456, 10460, 10465, 10471, 10476, 10481, 10485, 10490, 10494,
    10498, 10502, 10506, 10510, 10514, 10518, 10523, 10527, 10532, 10537, 10541, 10545,
    10549, 10550, 10556, 10561,
]

# ---------------------------------------------------------------------------
# Разбор хвоста с критерием.
# ---------------------------------------------------------------------------

#: Метка, с которой начинается хвост. Формы, найденные на проде: «Критерий
#: приёмки: …», «Критерии приёмки: …», «Принято, если: …» и «Принято, если
#: названа …» — без двоеточия (8 заданий; первый прогон их пропустил). «Принято,
#: если» внутри первой формы — тот же хвост, режем по первой метке.
_MARKER = re.compile(
    r"(?:Критери[йи]\s+при[её]мки\s*:|(?:Принято|Засчитывается),?\s+если(?=[\s:])\s*:?)",
    re.IGNORECASE,
)


def split_stem(stem: str) -> tuple[str, str | None]:
    """Отделить хвост с критерием от условия.

    Хвост — от первой метки до конца текста (на проде проверено SQL: после
    метки ни абзацев, ни HTML-тегов нет ни у одного из 185 заданий).
    Перед меткой стоит либо пустая строка, либо (8 заданий) просто пробел
    после точки — оба варианта подчищаются.

    :returns: (условие без хвоста, хвост дословно) либо (stem, None).
    """
    m = _MARKER.search(stem)
    if m is None:
        return stem, None
    head = stem[: m.start()].rstrip()
    tail = stem[m.start():].strip()
    return head, tail


def review_note_text(tail: str) -> str:
    """Текст заметки проверяющему: хвост без метки «Критерий приёмки:» / «Принято, если:».

    Публикатор (tsk-946) режет `review_note` по «;» на пункты `must`; метка в
    первом пункте — мусор («Принято, если: названа активность …»). Само тело
    критерия остаётся дословным, первая буква — заглавная.
    """
    body = _MARKER.sub("", tail, count=1).strip()
    return body[:1].upper() + body[1:] if body else tail


_ITEM_SPLIT = re.compile(
    r";\s+|\s*\(\d+\)\s*|\s*\d+\)\s+|\.\s+(?=(?:Не\s+засчит|Не\s+прин|Отклон))"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«])")
_REJECT_HINT = re.compile(r"^(не\s+засчит|не\s+прин|отклон|не\s+принимается)", re.IGNORECASE)


def criteria_from_tail(tail: str) -> dict[str, list[str]]:
    """Разложить хвост на пункты `must` / `reject` для заданий без критериев.

    Текст после метки режется по «;» и нумерации «(1)», «1)»; пункт короче
    10 символов приклеивается к предыдущему (валидатор LMS такие не примет).
    В `reject` уходит только пункт, который сам начинается со слов
    «не засчитывать / не принимается / отклоняется» — остальное `must`.
    """
    body = _MARKER.sub("", tail, count=1).strip()
    raw = [p.strip(" .;") for p in _ITEM_SPLIT.split(body)]
    items: list[str] = []
    for part in raw:
        if not part:
            continue
        if len(part) > 500:
            # Один пункт длиннее лимита LMS — режем по предложениям.
            raw_tail = [q.strip(" .;") for q in _SENTENCE_SPLIT.split(part)]
            items.extend(q for q in raw_tail if q)
            continue
        if len(part) < 10 and items:
            items[-1] = f"{items[-1]}; {part}"
        else:
            items.append(part)
    must = [p[0].upper() + p[1:] for p in items if not _REJECT_HINT.match(p)]
    reject = [p[0].upper() + p[1:] for p in items if _REJECT_HINT.match(p)]
    return {"must": must, "reject": reject}


# ---------------------------------------------------------------------------
# Исходник CreateCourses.
# ---------------------------------------------------------------------------

def _iter_task_blocks(node: Any) -> Iterable[dict[str, Any]]:
    """Обойти блоки `task` в файле урока (список блоков) или в графе (дерево)."""
    if isinstance(node, dict):
        if node.get("type") == "task" and isinstance(node.get("stem"), str):
            yield node
            return
        for value in node.values():
            yield from _iter_task_blocks(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_task_blocks(item)


def _dump(path: Path, data: Any) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def process_source(*, write: bool) -> list[dict[str, Any]]:
    """Вырезать хвост из `stem` и положить в `review_note` во всех файлах исходника.

    :param write: False — только собрать план; True — переписать файлы.
    :returns: список правок (файл, было, стало, review_note).
    """
    changes: list[dict[str, Any]] = []
    for course, wp_dir in SOURCE_DIRS.items():
        for path in sorted(wp_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            touched = 0
            for block in _iter_task_blocks(data):
                head, tail = split_stem(block["stem"])
                if tail is None:
                    # Повторный прогон: хвост уже в review_note — только снять метку.
                    note = block.get("review_note")
                    if isinstance(note, str) and _MARKER.match(note):
                        block["review_note"] = review_note_text(note)
                        touched += 1
                    continue
                if block.get("review_note"):
                    raise RuntimeError(f"{path.name}: у блока уже есть review_note, хвост нельзя положить молча")
                note = review_note_text(tail)
                changes.append({
                    "course": course, "file": path.name,
                    "stem_before": block["stem"], "stem_after": head, "review_note": note,
                })
                block["stem"] = head
                block["review_note"] = note
                touched += 1
            if touched and write:
                _dump(path, data)
                logger.info("%s/%s: %s блоков", course, path.name, touched)
    return changes


# ---------------------------------------------------------------------------
# Боевой LMS через API.
# ---------------------------------------------------------------------------

def _api(method: str, path: str, body: Any | None = None) -> Any:
    import urllib.error
    import urllib.request

    key = os.environ.get("LMS_API_KEY")
    if not key:
        raise RuntimeError("LMS_API_KEY не задан")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, method=method,
        headers={"X-API-Key": key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    # Обрыв TLS/сети на 97-м из 179 запросов первого прогона (UNEXPECTED_EOF):
    # повторяем только сетевые сбои, HTTP-ошибки — сразу наверх.
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} → {e.code}: {detail[:2000]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt == 3:
                raise
            logger.warning("%s %s: сеть (%s), повтор %s/3", method, path, e, attempt + 1)
            time.sleep(3 * attempt)
    raise AssertionError("unreachable")


def _criteria_block(parts: dict[str, list[str]], *, status: str) -> dict[str, Any]:
    """Блок `grading_criteria` для задания, где критериев ещё не было."""
    block: dict[str, Any] = {
        "must": parts["must"],
        "accept": [],
        "reject": parts["reject"],
        "notes": (
            "Разложено из хвоста «Принято, если» в условии (tsk-947, 2026-09-15); "
            "у задания есть эталон, критерии проверяются по комментарию или коду ученика."
        ),
        "status": status,
        "origin": "manual",
        "generated_by_model": None,
        "generated_at": None,
        "draft_warning": None,
    }
    if status == "approved":
        block["reviewed_by"] = REVIEWED_BY
        block["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    else:
        block["reviewed_by"] = None
        block["reviewed_at"] = None
    return block


def build_lms_payload(current: dict[str, Any], *, add_criteria: bool, status: str) -> dict[str, Any] | None:
    """PATCH-тело: условие без хвоста (+ критерии, если их в правиле ещё нет).

    :returns: None, если в условии нет хвоста — задание пропускается.
    """
    content = dict(current["task_content"])
    head, tail = split_stem(content.get("stem") or "")
    if tail is None:
        return None
    content["stem"] = head
    payload: dict[str, Any] = {"task_content": content}
    rules = current.get("solution_rules") or {}
    has_criteria = bool((rules.get("grading_criteria") or {}).get("must"))
    # TA с рубрикой: `has_grading_criteria` читает и рубрику — второй набор
    # тех же пунктов ей не нужен (#9762: хвост 660 символов одним предложением).
    has_rubric = bool((rules.get("text_answer") or {}).get("rubric"))
    if add_criteria and not has_criteria and not has_rubric:
        new_rules = dict(rules)
        new_rules["grading_criteria"] = _criteria_block(criteria_from_tail(tail), status=status)
        payload["solution_rules"] = new_rules
    return payload


def run_lms(ids: list[int], *, apply: bool, add_criteria: bool, status: str, report: Path) -> None:
    """Пройти задания: показать «было → стало», при `apply` записать через PATCH."""
    rows: list[dict[str, Any]] = []
    for task_id in ids:
        current = _api("GET", f"/tasks/{task_id}")
        body = build_lms_payload(current, add_criteria=add_criteria, status=status)
        row: dict[str, Any] = {
            "id": task_id, "external_uid": current.get("external_uid"),
            "course_id": current.get("course_id"),
            "stem_before": current["task_content"].get("stem"),
        }
        if body is None:
            row["skipped"] = "хвоста в условии нет"
            logger.info("id=%s: хвоста нет, пропуск", task_id)
            rows.append(row)
            continue
        row["stem_after"] = body["task_content"]["stem"]
        if "solution_rules" in body:
            row["grading_criteria"] = body["solution_rules"]["grading_criteria"]
        if apply:
            updated = _api("PATCH", f"/tasks/{task_id}", body)
            prov = updated.get("content_provenance") or {}
            gc = (updated.get("solution_rules") or {}).get("grading_criteria") or {}
            row["result"] = {
                "stem_len": len(updated["task_content"]["stem"]),
                "provenance": prov.get("source"),
                "criteria_status": gc.get("status"),
                "must": len(gc.get("must") or []),
            }
            logger.info(
                "id=%s: stem %s→%s симв., критерии %s (must=%s), provenance=%s",
                task_id, len(row["stem_before"] or ""), row["result"]["stem_len"],
                gc.get("status"), row["result"]["must"], prov.get("source"),
            )
        else:
            logger.info("id=%s: stem %s→%s симв.%s", task_id, len(row["stem_before"] or ""),
                        len(row["stem_after"]), " + критерии" if "grading_criteria" in row else "")
        rows.append(row)
        # Отчёт после каждой строки: первые два прогона упали на сети и 422,
        # и «было → стало» по 141 заданию не сохранилось нигде.
        report.parent.mkdir(parents=True, exist_ok=True)
        _dump(report, rows)
    logger.info("отчёт: %s (%s строк)", report, len(rows))


def plan_source() -> None:
    changes = process_source(write=False)
    by_course: dict[str, int] = {}
    for ch in changes:
        by_course[ch["course"]] = by_course.get(ch["course"], 0) + 1
    print("Блоков с хвостом в исходнике:", by_course, "всего", len(changes))
    for ch in changes[:3] + changes[-2:]:
        print(f"\n=== {ch['course']}/{ch['file']} ===")
        print("БЫЛО:", ch["stem_before"][-260:])
        print("СТАЛО:", ch["stem_after"][-120:])
        print("review_note:", ch["review_note"][:200])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", action="store_true", help="править исходник CreateCourses")
    parser.add_argument("--dry-run", action="store_true", help="прочитать задания из LMS, показать план, отчёт JSON")
    parser.add_argument("--apply", action="store_true", help="записать в боевой LMS через API")
    parser.add_argument("--with-reference", action="store_true",
                        help="включить 40 заданий с эталоном (им критерии кладутся в правило)")
    parser.add_argument("--status", choices=("approved", "draft"), default="draft",
                        help="статус критериев для заданий с эталоном (по умолчанию draft)")
    parser.add_argument("--ids", type=str, default=None, help="только эти id через запятую (пробный прогон)")
    parser.add_argument("--report", type=Path,
                        default=Path("reviews") / "tsk947" / f"lms-{datetime.now():%Y%m%d-%H%M%S}.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    if not (args.source or args.dry_run or args.apply):
        plan_source()
        return 0
    if args.source:
        changes = process_source(write=True)
        logger.info("исходник: %s блоков переписано", len(changes))
    if args.dry_run or args.apply:
        ids = list(QUEUE_IDS) + (list(REFERENCE_IDS) if args.with_reference else [])
        if args.ids:
            wanted = {int(x) for x in args.ids.split(",")}
            ids = [i for i in ids if i in wanted]
        run_lms(ids, apply=args.apply, add_criteria=args.with_reference, status=args.status,
                report=args.report)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
