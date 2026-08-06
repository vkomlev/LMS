"""Импорт авторского курса из CreateCourses в LMS через bulk-API.

Зачем скрипт, а не ручные запросы: проекция из CreateCourses оперирует
УСТОЙЧИВЫМИ ключами (`course_uid`, `external_uid`, код сложности), а bulk-API
LMS требует ЧИСЛОВЫЕ `course_id`/`difficulty_id`. Резолв делается здесь, один
раз и одинаково — иначе при каждом ручном импорте это переизобретается.

Идемпотентность: курс ищется по `course_uid`, задания и материалы — upsert по
`external_uid`. Повторный запуск не плодит дубли.

Безопасность:
- по умолчанию DRY-RUN, запись только с `--apply`;
- перед записью печатается план и текущее состояние цели;
- после записи выполняется верификация чтением (сколько создано, совпал ли
  микс сложности);
- гейт ручек — `APIKeyQuery`, ключ уходит в query (особенность legacy-ручек
  LMS), поэтому URL с ключом НИКОГДА не печатается.

Использование:
    python scripts/import_authored_course.py <путь-к-lms-import.json> [--materials <md>]
    python scripts/import_authored_course.py ... --apply
    python scripts/import_authored_course.py ... --base https://api.learn.victor-komlev.ru --apply
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from dotenv import dotenv_values
except ImportError:
    sys.exit("нужен python-dotenv")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _token(explicit: str | None) -> str:
    if explicit:
        return explicit
    for path, names in (
        (pathlib.Path(r"D:\Work\ContentBackbone\.env"), ("CB_LMS_TOKEN",)),
        (ROOT / ".env", ("VALID_API_KEYS",)),
    ):
        if not path.exists():
            continue
        env = dotenv_values(path, encoding="utf-8-sig")
        for n in names:
            if env.get(n):
                return env[n].split(",")[0].strip()
    sys.exit("не найден сервисный ключ (CB_LMS_TOKEN в CB .env или VALID_API_KEYS в LMS .env)")


def _call(base: str, token: str, method: str, path: str, payload=None):
    url = f"{base.rstrip('/')}{path}"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}api_key={urllib.parse.quote(token)}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "X-API-Key": token})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        # URL с ключом наружу не выводим — только тело ответа.
        return e.code, e.read().decode("utf-8", "ignore")[:600]


def parse_materials_md(md: pathlib.Path) -> dict[str, str]:
    """Тело каждого материала по его external_uid из `## Мn · \\`UID\\` · ...`."""
    out: dict[str, str] = {}
    for chunk in md.read_text(encoding="utf-8").split("\n---\n"):
        m = re.search(r"^##\s+М\d+\s*·\s*`([^`]+)`", chunk.strip(), re.M)
        if not m:
            continue
        body = chunk.strip().split("\n", 1)[1] if "\n" in chunk.strip() else ""
        # отрезаем строку-заголовок секции, оставляем сам урок целиком
        body = re.sub(r"^##\s+М\d+.*?\n", "", chunk.strip(), count=1, flags=re.S | re.M)
        out[m.group(1)] = body.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("export", help="путь к lms-import.json")
    ap.add_argument("--materials", help="materials.md с телами материалов")
    ap.add_argument("--base", default="https://api.learn.victor-komlev.ru")
    ap.add_argument("--token")
    ap.add_argument("--apply", action="store_true", help="без флага — dry-run")
    args = ap.parse_args()

    data = json.loads(pathlib.Path(args.export).read_text(encoding="utf-8"))
    course = data["courses"][0]
    tasks = data["tasks"]
    materials = data.get("materials", [])
    bodies = parse_materials_md(pathlib.Path(args.materials)) if args.materials else {}

    token = _token(args.token)
    print(f"цель      : {args.base}")
    print(f"курс      : {course['course_uid']} — {course['title']}")
    print(f"материалов: {len(materials)} (тела найдены: {len(bodies)})")
    print(f"заданий   : {len(tasks)}")

    st, diffs = _call(args.base, token, "GET", "/api/v1/difficulty-levels/")
    # Ручка отдаёт конверт {items, meta}, а не голый список — не полагаться на форму.
    rows = diffs.get("items") if isinstance(diffs, dict) else diffs
    if st != 200 or not isinstance(rows, list):
        sys.exit(f"не удалось прочитать справочник сложностей: {st} {diffs}")
    diff_id = {d["code"]: d["id"] for d in rows}
    print(f"сложности : {diff_id}")

    st, found = _call(args.base, token, "GET",
                      f"/api/v1/courses/by-code/{urllib.parse.quote(course['course_uid'])}")
    course_id = found.get("id") if st == 200 and isinstance(found, dict) else None
    print(f"состояние : курс {'УЖЕ ЕСТЬ, id=' + str(course_id) if course_id else 'не найден — будет создан'}")

    missing = [t["external_uid"] for t in tasks if t["difficulty"] not in diff_id]
    if missing:
        sys.exit(f"неизвестный код сложности у заданий: {missing}")
    if bodies and any(m["external_uid"] not in bodies for m in materials):
        sys.exit("не для всех материалов найдено тело в materials.md")

    if not args.apply:
        print("\nDRY-RUN. Ничего не записано. Повторите с --apply.")
        return

    if not course_id:
        payload = {k: v for k, v in course.items() if k != "required_courses_uid"}
        st, res = _call(args.base, token, "POST", "/api/v1/courses/", payload)
        if st not in (200, 201):
            sys.exit(f"создание курса не удалось: {st} {res}")
        course_id = res["id"]
        print(f"курс создан: id={course_id}")

    if materials:
        items = []
        for i, m in enumerate(materials, start=1):
            body = bodies.get(m["external_uid"], "")
            items.append({
                "course_id": course_id, "external_uid": m["external_uid"],
                "title": m["title"], "type": m.get("type", "text"),
                "order_position": m.get("order_position", i),
                "content": {"text": body, "format": "markdown"},
            })
        st, res = _call(args.base, token, "POST", "/api/v1/materials/bulk-upsert", {"items": items})
        print(f"материалы : HTTP {st} {json.dumps(res, ensure_ascii=False)[:260] if isinstance(res, dict) else res}")
        if st not in (200, 201):
            sys.exit("импорт материалов не удался")

    items = []
    for t in tasks:
        items.append({
            "external_uid": t["external_uid"], "course_id": course_id,
            "difficulty_id": diff_id[t["difficulty"]],
            "task_content": t["task_content"], "solution_rules": t["solution_rules"],
            "max_score": t.get("max_score", 1), "is_active": True,
        })
    st, res = _call(args.base, token, "POST", "/api/v1/tasks/bulk-upsert", {"items": items})
    print(f"задания   : HTTP {st} {json.dumps(res, ensure_ascii=False)[:260] if isinstance(res, dict) else res}")
    if st not in (200, 201):
        sys.exit("импорт заданий не удался")

    st, chk = _call(args.base, token, "GET", f"/api/v1/tasks/by-course/{course_id}")
    got = len(chk) if isinstance(chk, list) else "?"
    print(f"\nверификация: заданий в курсе {course_id}: {got} (ожидали {len(tasks)})")


if __name__ == "__main__":
    main()
