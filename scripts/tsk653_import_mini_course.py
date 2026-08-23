# scripts/tsk653_import_mini_course.py
"""
tsk-653: импорт мини-курса повторения (корень + подкурсы) в LMS.

**Почему не `import_authored_course.py`.** Тот умеет ровно ОДИН плоский курс
(`data["courses"][0]`), а здесь нужна иерархия: корень и три подкурса по главам.
Плоским курс сделать нельзя — 33 сущности против порога «плоский только для мини,
≤ 20» (`lms-wp-export.md` § 1.1.1): LMS подаёт сначала все материалы, потом все
задания, и ранняя теория забывается к практике.

Идемпотентность: курсы ищутся по `course_uid`, материалы и задания — upsert по
`external_uid`. Повторный запуск не плодит дубли и не сбрасывает прогресс.

Безопасность: по умолчанию предпросмотр, запись только с `--apply`. Сервисный
ключ читается из окружения/`.env` и нигде не печатается.

Запуск:
    PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/tsk653_import_mini_course.py \
        docs/curriculum/2026-08-23-tsk653-mini-povtorenie-inf8.json
    ... --apply
    ... --base https://api.learn.victor-komlev.ru --apply
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _token(explicit: Optional[str]) -> str:
    """Сервисный ключ. Значение не печатается никогда."""
    if explicit:
        return explicit
    raw = os.environ.get("VALID_API_KEYS")
    if raw and raw.strip():
        return raw.split(",")[0].strip()
    try:
        from dotenv import dotenv_values
    except ImportError:
        sys.exit("нужен python-dotenv либо переменная окружения VALID_API_KEYS")
    env = dotenv_values(ROOT / ".env", encoding="utf-8-sig")
    if env.get("VALID_API_KEYS"):
        return env["VALID_API_KEYS"].split(",")[0].strip()
    sys.exit("не найден сервисный ключ (VALID_API_KEYS)")


def _call(base: str, token: str, method: str, path: str, payload: Any = None):
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def _find_course(base: str, token: str, uid: str) -> Optional[int]:
    st, found = _call(base, token, "GET", f"/api/v1/courses/by-code/{urllib.parse.quote(uid)}")
    return found.get("id") if st == 200 and isinstance(found, dict) else None


def _create_course(base: str, token: str, node: Dict[str, Any], parent_id: Optional[int]) -> int:
    payload: Dict[str, Any] = {
        "title": node["title"],
        "access_level": node.get("access_level", "auto_check"),
        "description": node.get("description"),
        "course_uid": node["course_uid"],
        "is_required": False,
    }
    if parent_id is not None:
        # `parent_courses` вместо `parent_course_ids`: порядок глав внутри курса
        # задаётся здесь, иначе он был бы случайным и ученица получила бы Python
        # раньше логики.
        payload["parent_courses"] = [
            {"parent_course_id": parent_id, "order_number": node.get("order_number", 1)}
        ]
    st, res = _call(base, token, "POST", "/api/v1/courses/", payload)
    if st not in (200, 201) or not isinstance(res, dict):
        sys.exit(f"не удалось создать курс {node['course_uid']}: {st} {res}")
    return int(res["id"])


def main() -> None:
    ap = argparse.ArgumentParser(description="tsk-653: импорт мини-курса повторения")
    ap.add_argument("plan", help="json с корнем, подкурсами, материалами и заданиями")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="базовый адрес API")
    ap.add_argument("--token", default=None, help="сервисный ключ (по умолчанию из окружения)")
    ap.add_argument("--apply", action="store_true", help="записать (без флага — предпросмотр)")
    args = ap.parse_args()

    data = json.loads(pathlib.Path(args.plan).read_text(encoding="utf-8"))
    root, subs = data["root"], data["subcourses"]
    token = _token(args.token)

    total_tasks = sum(len(s["tasks"]) for s in subs)
    print(f"цель      : {args.base}")
    print(f"корень    : {root['course_uid']} — {root['title']}")
    print(f"подкурсов : {len(subs)}, материалов {len(subs)}, заданий {total_tasks}")

    st, diffs = _call(args.base, token, "GET", "/api/v1/difficulty-levels/")
    rows = diffs.get("items") if isinstance(diffs, dict) else diffs
    if st != 200 or not isinstance(rows, list):
        sys.exit(f"не удалось прочитать справочник сложностей: {st} {diffs}")
    diff_id = {d["code"]: d["id"] for d in rows}

    unknown = sorted({
        t["difficulty"] for s in subs for t in s["tasks"] if t["difficulty"] not in diff_id
    })
    if unknown:
        sys.exit(f"неизвестные коды сложности: {unknown}")

    root_id = _find_course(args.base, token, root["course_uid"])
    print(f"состояние : корень {'есть, id=' + str(root_id) if root_id else 'не найден — будет создан'}")
    for s in subs:
        sid = _find_course(args.base, token, s["course_uid"])
        mix: Dict[str, int] = {}
        for t in s["tasks"]:
            mix[t["difficulty"]] = mix.get(t["difficulty"], 0) + 1
        types: Dict[str, int] = {}
        for t in s["tasks"]:
            k = t["task_content"]["type"]
            types[k] = types.get(k, 0) + 1
        print(f"  {s['course_uid']}: {'есть id=' + str(sid) if sid else 'будет создан'}, "
              f"заданий {len(s['tasks'])} {mix}, типы {types}")

    # Инвариант курса, а не пожелание: ни одного развёрнутого текстового задания.
    # Ради этого курс и делается — ученице ровно эти слоты и закрыла нейросеть.
    bad = [t["external_uid"] for s in subs for t in s["tasks"]
           if t["task_content"]["type"] not in ("SC", "MC", "SA")]
    if bad:
        sys.exit(f"в курсе повторения допустимы только авто-проверяемые типы, нарушают: {bad}")

    if not args.apply:
        print("\nПредпросмотр. Ничего не записано. Повторите с --apply.")
        return

    if not root_id:
        root_id = _create_course(args.base, token, root, parent_id=None)
        print(f"корень создан: id={root_id}")

    for s in subs:
        sub_id = _find_course(args.base, token, s["course_uid"])
        if not sub_id:
            sub_id = _create_course(args.base, token, s, parent_id=root_id)
            print(f"подкурс создан: {s['course_uid']} id={sub_id}")

        m = s["material"]
        st, res = _call(args.base, token, "POST", "/api/v1/materials/bulk-upsert", {"items": [{
            "course_id": sub_id,
            "external_uid": m["external_uid"],
            "title": m["title"],
            "type": "text",
            "order_position": 1,
            "content": {"text": m["body"], "format": "markdown"},
        }]})
        if st not in (200, 201):
            sys.exit(f"материал {m['external_uid']} не импортирован: {st} {res}")
        print(f"  материал  : {m['external_uid']} — HTTP {st}")

        items = [{
            "external_uid": t["external_uid"],
            "course_id": sub_id,
            "difficulty_id": diff_id[t["difficulty"]],
            "task_content": t["task_content"],
            "solution_rules": t["solution_rules"],
            "max_score": t["solution_rules"].get("max_score", 1),
            "order_position": i,
            "is_active": True,
        } for i, t in enumerate(s["tasks"], start=1)]
        st, res = _call(args.base, token, "POST", "/api/v1/tasks/bulk-upsert", {"items": items})
        if st not in (200, 201):
            sys.exit(f"задания подкурса {s['course_uid']} не импортированы: {st} {res}")
        print(f"  задания   : {len(items)} — HTTP {st}")

        st, chk = _call(args.base, token, "GET", f"/api/v1/tasks/by-course/{sub_id}")
        got = len(chk) if isinstance(chk, list) else (len(chk.get("items", [])) if isinstance(chk, dict) else "?")
        print(f"  проверка  : заданий в курсе {sub_id}: {got} (ожидали {len(items)})")

    print(f"\nГотово. Корень id={root_id}. Курс никому не назначен — это отдельный шаг.")


if __name__ == "__main__":
    main()
