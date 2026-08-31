# scripts/tsk745_import_onboarding.py
"""
tsk-745: публикация общего онбординга по платформе + назначение ученикам.

Три фазы, каждая включается своим флагом; без флагов — предпросмотр.

  --upload-images  загрузить образы экранов и подставить ссылки в план
  --apply          создать курсы, материалы и задания
  --assign         назначить курс ученикам

**Почему картинки лежат в LMS, а не в WP-медиатеке.** Обычно материалы курсов
ссылаются на `victor-komlev.ru/wp-content/...`, но 31.08 сайт не отвечал
(ReadTimeout на любой запрос, включая корень), а срок задачи — 2 сентября.
`POST /api/v1/materials/upload` кладёт файл в то же CAS-хранилище, что и медиа
заданий, и отдаёт путь `/api/v1/materials/files/<sha>.png`. Он относительный, и
это правильно: SPW проксирует `/api/v1/*` на свой origin (next.config rewrites),
а CSP разрешает `img-src 'self'`. Загрузка адресуется по содержимому, поэтому
повторный запуск не плодит копий — тот же файл даёт тот же адрес.

**Почему назначение отдельной фазой.** Автоназначения по регистрации в платформе
нет: `assignment_rule` умеет только события заданий и квиза. Курс появится в
«Моих курсах» лишь у того, кому его назначили, поэтому назначение — явный шаг, а
не побочный эффект публикации. Критерий выборки (решение оператора 31.08): те, у
кого курсы уже назначены, но прогресса ещё нет — то есть новички.

Идемпотентность на всех фазах: курсы ищутся по `course_uid`, материалы и задания
идут через bulk-upsert по `external_uid`, назначение возвращает
`already_enrolled` вместо дубля. Повторный запуск ничего не ломает.

Запуск на боевом сервере (там сервисный ключ и локальный API):
    sudo -u app /opt/lms/venv/bin/python /opt/lms/scripts/tsk745_import_onboarding.py \\
        /opt/lms/docs/curriculum/2026-08-31-tsk745-onboarding-platforma.json \\
        --images /opt/lms/docs/curriculum/visuals --upload-images --apply
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
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: image_id -> (alt, подпись под картинкой). Alt пишется для того, кто картинку
#: не видит: экранный диктор и поиск. Подпись — для того, кто видит.
IMAGES: Dict[str, tuple] = {
    "onb-victor": (
        "Фотография Виктора Комлева: мужчина в синем пиджаке и галстуке сидит за столом, "
        "перед ним раскрытая книга",
        "",
    ),
    "onb-menu": (
        "Верхняя панель кабинета: слева логотип, дальше разделы Курсы, Занятия, "
        "Сообщения, Прогресс, История, Оплата, Тариф, Профиль; справа колокольчик "
        "уведомлений с числом",
        "Так выглядит верхнее меню кабинета.",
    ),
    "onb-kurs-karta": (
        "Раздел «Мои курсы»: карточки курсов, у каждой название, счётчик решённых "
        "задач с процентом, дата последнего входа и синяя кнопка «Продолжить»",
        "Список курсов. Числа на образе — пример, у тебя будут свои.",
    ),
    "onb-kurs-derevo": (
        "Страница курса: название, счётчик задач и материалов, большая синяя кнопка "
        "«Перейти к следующей задаче», ниже вкладки Разделы, Лента, Программа курса "
        "и список разделов с галочкой у пройденного и знаком внимания у начатого",
        "Кнопка следующего шага — ответ на вопрос «что мне делать». Надпись на ней "
        "меняется: пока дальше идёт теория, там написано «Открыть следующий материал».",
    ),
    "onb-zadanie": (
        "Экран задания: ссылка «К курсу», пометка «Обязательно», счётчик «Попыток: 1 / 3», "
        "условие задачи, поля «Ответ» и «Комментарий», выбор файла и синяя кнопка "
        "«Отправить на проверку»",
        "Экран задания. Задача на образе — пример, у тебя будут свои.",
    ),
    "onb-pomosch": (
        "Экран после неверного ответа: красная плашка «Ответ неверный» с остатком попыток, "
        "синяя кнопка «Разобраться с наставником», кнопка «Запросить помощь преподавателя» "
        "и свёрнутый блок «Подсказки»",
        "Три ступени помощи появляются на одном экране, сразу после неверного ответа.",
    ),
    "onb-zanyatie": (
        "Раздел «Мои занятия»: карточка ближайшего занятия с датой, временем по Москве и "
        "пересчётом в своё время, статусом «Запланировано» и кнопками «Я на занятии», "
        "«Присоединиться», «Отказаться»; ниже пропущенное занятие со ссылкой записаться взамен",
        "Даты и время на образе — пример.",
    ),
    "onb-oplata": (
        "Раздел «Оплата»: реквизиты для перевода, начисление за текущий месяц с пометкой "
        "срока и кнопкой «Приложить чек», ниже прошлый месяц с пометкой «Чек на подтверждении»",
        "Суммы и реквизиты на образе — пример, настоящие показаны в твоём кабинете.",
    ),
}


# ------------------------------------------------------------------- HTTP

def _token(explicit: Optional[str]) -> str:
    """Сервисный ключ. Значение не печатается никогда."""
    if explicit:
        return explicit
    raw = os.environ.get("VALID_API_KEYS")
    if raw and raw.strip():
        return raw.split(",")[0].strip()
    for env_path in (ROOT / ".env", pathlib.Path("/opt/lms/.env")):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("VALID_API_KEYS="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value.split(",")[0].strip()
    sys.exit("не найден сервисный ключ (VALID_API_KEYS)")


def _call(base: str, token: str, method: str, path: str, payload: Any = None):
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def _upload_png(base: str, token: str, png: pathlib.Path):
    """multipart/form-data вручную — чтобы не тянуть requests на боевой сервер."""
    boundary = "----tsk745boundary7a1c"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{png.name}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + png.read_bytes() + tail
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/v1/materials/upload", data=body, method="POST"
    )
    req.add_header("X-API-Key", token)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


# -------------------------------------------------------------- подстановка

def fill_images(plan: Dict[str, Any], urls: Dict[str, str]) -> int:
    """Заменить плейсхолдеры `IMG::<id>` на картинку с подписью."""
    count = 0

    def patch(body: str) -> str:
        nonlocal count
        for image_id, url in urls.items():
            marker = f"IMG::{image_id}"
            if marker not in body:
                continue
            alt, caption = IMAGES[image_id]
            # Плейсхолдер стоит внутри своего <p>: закрываем его после картинки и
            # открываем новый под подпись, иначе подпись слипнется с картинкой.
            tail = f"</p><p>{caption}" if caption else ""
            body = body.replace(marker, f'<img src="{url}" alt="{alt}">{tail}')
            count += 1
        return body

    for node in [plan["root"], *plan["subcourses"]]:
        for material in _node_materials(node):
            material["body"] = patch(material["body"])
    return count


# ------------------------------------------------------------------ курсы

def _find_course(base: str, token: str, uid: str) -> Optional[int]:
    st, found = _call(base, token, "GET", f"/api/v1/courses/by-code/{urllib.parse.quote(uid)}")
    return found.get("id") if st == 200 and isinstance(found, dict) else None


def _create_course(base: str, token: str, node: Dict[str, Any], parent_id: Optional[int]) -> int:
    payload: Dict[str, Any] = {
        "title": node["title"],
        "access_level": node.get("access_level", "self_guided"),
        "description": node.get("description"),
        "course_uid": node["course_uid"],
        "is_required": False,
    }
    if parent_id is not None:
        # `parent_courses` с явным order_number: порядок разделов задаётся здесь.
        # Иначе он определялся бы порядком вставки, и «Оплата» могла бы встать
        # раньше «Кабинета».
        payload["parent_courses"] = [
            {"parent_course_id": parent_id, "order_number": node.get("order_number", 1)}
        ]
    st, res = _call(base, token, "POST", "/api/v1/courses/", payload)
    if st not in (200, 201) or not isinstance(res, dict):
        sys.exit(f"не удалось создать курс {node['course_uid']}: {st} {res}")
    return int(res["id"])


def _node_materials(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Материалы узла: `material` (один) и/или `materials` (список, по порядку).

    Оба ключа, а не только список: у разделов материал один, и заставлять их
    носить список из одного элемента значило бы переписать весь план ради
    единственного узла, которому понадобился второй материал.
    """
    out: List[Dict[str, Any]] = []
    if node.get("material"):
        out.append(node["material"])
    out.extend(node.get("materials") or [])
    return out


def _push_node(base: str, token: str, course_id: int, materials: List[Dict[str, Any]],
               tasks: List[Dict[str, Any]], diff_id: Dict[str, int]) -> None:
    for position, material in enumerate(materials, start=1):
        st, res = _call(base, token, "POST", "/api/v1/materials/bulk-upsert", {"items": [{
            "course_id": course_id,
            "external_uid": material["external_uid"],
            "title": material["title"],
            "type": "text",
            "order_position": position,
            "content": {"text": material["body"], "format": "html"},
        }]})
        if st not in (200, 201):
            sys.exit(f"материал {material['external_uid']} не импортирован: {st} {res}")
        print(f"    материал : {material['external_uid']} — HTTP {st}")

    if not tasks:
        return
    items = [{
        "external_uid": t["external_uid"],
        "course_id": course_id,
        "difficulty_id": diff_id[t["difficulty"]],
        "task_content": t["task_content"],
        "solution_rules": t["solution_rules"],
        "max_score": t["solution_rules"].get("max_score", 1),
        "order_position": i,
        "is_active": True,
    } for i, t in enumerate(tasks, start=1)]
    st, res = _call(base, token, "POST", "/api/v1/tasks/bulk-upsert", {"items": items})
    if st not in (200, 201):
        sys.exit(f"задания курса {course_id} не импортированы: {st} {res}")
    print(f"    задания  : {len(items)} — HTTP {st}")


# --------------------------------------------------------------- назначение

def pick_students_auto() -> List[int]:
    """Ученики, у кого курсы назначены, а прогресса ещё нет (критерий оператора)."""
    # asyncpg, а не psycopg2: на боевом сервере стоит только он — проект целиком
    # на асинхронном драйвере, синхронного в venv нет.
    import asyncio

    import asyncpg  # только в этой ветке: локальному предпросмотру драйвер не нужен

    dsn = os.environ.get("DATABASE_URL") or ""
    for env_path in (pathlib.Path("/opt/lms/.env"), ROOT / ".env"):
        if dsn or not env_path.exists():
            break
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not dsn:
        sys.exit("не найден DATABASE_URL для выборки учеников")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://").replace("+psycopg2", "")

    async def fetch() -> List[int]:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch("""
                SELECT DISTINCT uc.user_id
                  FROM user_courses uc
                  JOIN user_roles ur ON ur.user_id = uc.user_id
                  JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
                 WHERE uc.user_id NOT IN (SELECT DISTINCT user_id FROM attempts)
                 ORDER BY uc.user_id
            """)
            return [r["user_id"] for r in rows]
        finally:
            await conn.close()

    return asyncio.run(fetch())


def assign(base: str, token: str, course_uid: str, students: List[int]) -> None:
    added = already = failed = 0
    for sid in students:
        st, res = _call(
            base, token, "POST", f"/api/v1/teacher/students/{sid}/assignments",
            {"course_uid": course_uid, "reason": "tsk-745: общий онбординг по платформе"},
        )
        if st != 200 or not isinstance(res, dict):
            print(f"    ученик {sid}: ОШИБКА {st} {res}")
            failed += 1
        elif res.get("already_enrolled"):
            already += 1
        else:
            added += 1
    print(f"  назначено: новых {added}, уже было {already}, ошибок {failed}")


# ------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description="tsk-745: публикация онбординга")
    ap.add_argument("plan", help="json с корнем, разделами, материалами и заданиями")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="базовый адрес API")
    ap.add_argument("--token", default=None, help="сервисный ключ (по умолчанию из окружения)")
    ap.add_argument("--images", default=None, help="каталог с PNG образов экранов")
    ap.add_argument("--upload-images", action="store_true", help="загрузить образы и подставить ссылки")
    ap.add_argument("--apply", action="store_true", help="создать курсы, материалы и задания")
    ap.add_argument("--assign", action="store_true", help="назначить курс ученикам")
    ap.add_argument("--students", default="auto",
                    help="'auto' (записаны, но без прогресса) или список id через запятую")
    args = ap.parse_args()

    plan_path = pathlib.Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    root, subs = plan["root"], plan["subcourses"]
    token = _token(args.token)

    total_tasks = sum(len(s["tasks"]) for s in subs)
    print(f"цель      : {args.base}")
    print(f"корень    : {root['course_uid']} — {root['title']}")
    total_materials = sum(len(_node_materials(n)) for n in [root, *subs])
    print(f"разделов  : {len(subs)}, материалов {total_materials}, заданий {total_tasks}")

    # Инвариант курса: только авто-проверяемые типы. Развёрнутый текст в
    # онбординге означал бы, что человека на входе просят написать сочинение,
    # которое никто не прочтёт раньше чем через день.
    bad = [t["external_uid"] for s in subs for t in s["tasks"]
           if t["task_content"]["type"] not in ("SC", "MC", "SA")]
    if bad:
        sys.exit(f"в онбординге допустимы только SC/MC/SA, нарушают: {bad}")

    # ---------------------------------------------------------- образы
    if args.upload_images:
        if not args.images:
            sys.exit("--upload-images требует --images <каталог с PNG>")
        img_dir = pathlib.Path(args.images)
        urls: Dict[str, str] = {}
        for image_id in IMAGES:
            png = img_dir / f"{image_id}.png"
            if not png.exists():
                sys.exit(f"нет файла образа: {png}")
            st, res = _upload_png(args.base, token, png)
            if st not in (200, 201) or not isinstance(res, dict) or "url" not in res:
                sys.exit(f"образ {image_id} не загружен: {st} {res}")
            urls[image_id] = res["url"]
            print(f"  образ    : {image_id} -> {res['url']}")
        n = fill_images(plan, urls)
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        print(f"  подставлено ссылок: {n} (план перезаписан)")

    st, diffs = _call(args.base, token, "GET", "/api/v1/difficulty-levels/")
    rows = diffs.get("items") if isinstance(diffs, dict) else diffs
    if st != 200 or not isinstance(rows, list):
        sys.exit(f"не удалось прочитать справочник сложностей: {st} {diffs}")
    diff_id = {d["code"]: d["id"] for d in rows}
    unknown = sorted({t["difficulty"] for s in subs for t in s["tasks"]
                      if t["difficulty"] not in diff_id})
    if unknown:
        sys.exit(f"неизвестные коды сложности: {unknown}")

    root_id = _find_course(args.base, token, root["course_uid"])
    print(f"состояние : корень {'есть, id=' + str(root_id) if root_id else 'не найден — будет создан'}")
    for s in subs:
        sid = _find_course(args.base, token, s["course_uid"])
        mix: Dict[str, int] = {}
        types: Dict[str, int] = {}
        for t in s["tasks"]:
            mix[t["difficulty"]] = mix.get(t["difficulty"], 0) + 1
            k = t["task_content"]["type"]
            types[k] = types.get(k, 0) + 1
        print(f"  {s['course_uid']}: {'есть id=' + str(sid) if sid else 'будет создан'}, "
              f"заданий {len(s['tasks'])} {mix}, типы {types}")

    students: List[int] = []
    if args.assign:
        students = ([int(x) for x in args.students.split(",") if x.strip()]
                    if args.students != "auto" else pick_students_auto())
        print(f"назначение: учеников в выборке {len(students)}")

    if not (args.apply or args.assign):
        print("\nПредпросмотр. Ничего не записано. Повторите с --apply / --assign.")
        return

    # ---------------------------------------------------------- запись
    if args.apply:
        if not root_id:
            root_id = _create_course(args.base, token, root, parent_id=None)
            print(f"корень создан: id={root_id}")
        print(f"  корень id={root_id}")
        _push_node(args.base, token, root_id, _node_materials(root), [], diff_id)

        for s in subs:
            sub_id = _find_course(args.base, token, s["course_uid"])
            if not sub_id:
                sub_id = _create_course(args.base, token, s, parent_id=root_id)
                print(f"  раздел создан: {s['course_uid']} id={sub_id}")
            else:
                print(f"  раздел есть  : {s['course_uid']} id={sub_id}")
            _push_node(args.base, token, sub_id, _node_materials(s), s["tasks"], diff_id)
            st, chk = _call(args.base, token, "GET", f"/api/v1/tasks/by-course/{sub_id}")
            got = len(chk) if isinstance(chk, list) else (
                len(chk.get("items", [])) if isinstance(chk, dict) else "?")
            print(f"    проверка : заданий в курсе {sub_id}: {got} (ожидали {len(s['tasks'])})")

    if args.assign:
        if not root_id:
            root_id = _find_course(args.base, token, root["course_uid"])
        if not root_id:
            sys.exit("курс ещё не создан — сначала --apply")
        assign(args.base, token, root["course_uid"], students)

    print("\nГотово.")


if __name__ == "__main__":
    main()
