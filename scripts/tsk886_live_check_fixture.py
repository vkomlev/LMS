# -*- coding: utf-8 -*-
"""tsk-886: временный курс на проде для живой проверки признака «вне работы».

Проверять поведение на реальном курсе школы нельзя: выключенный курс пропадает
из форм и перестаёт принимать записи, а трогать чужую программу ради проверки
своей правки — не то, что делают на боевой системе. Поэтому заводится
собственный корневой курс, на нём всё и проверяется.

**Прод-БД скрипт не трогает вовсе** — только боевой API штатными адресами
(`POST /courses/`, `GET /courses/{id}`, `DELETE /courses/{id}`). Вызовы идут
С САМОГО СЕРВЕРА (`ssh lms-spw-vds` → `curl http://127.0.0.1:8000`): сервисный
ключ лежит в `/opt/lms/.env` и наружу не выносится — локальный `.env` смотрит на
dev-контур, его ключ прод не принимает (проверено: 401). Приём — тот же, что в
tsk-325 (`docs/ai/operator-runbook.md`).

Номер заведённого курса записывается в `scratchpad/tsk886-fixture-ids.json`, и
уборка идёт РОВНО по нему, со сверкой кода курса перед удалением. Не по
названию и не по «свежий курс без учеников»: признак похожести авторства не
доказывает — 10.09 уборка «по признаку» снесла чужой объект (урок tsk-885).

Запуск:
    python scripts/tsk886_live_check_fixture.py --create
    python scripts/tsk886_live_check_fixture.py --status
    python scripts/tsk886_live_check_fixture.py --cleanup
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Боевой сервер приложения (алиас из ~/.ssh/config) и локальный адрес сервиса.
SSH_HOST = "lms-spw-vds"
API_BASE = "http://127.0.0.1:8000/api/v1"

#: Куда пишется номер заведённого курса. Уборка читает ТОЛЬКО его.
STATE_FILE = PROJECT_ROOT / "scratchpad" / "tsk886-fixture-ids.json"

#: Достать сервисный ключ на самом сервере. Ключ не покидает сервер и не
#: попадает ни в мою командную строку, ни в вывод скрипта.
_KEY_CMD = (
    "KEY=$(grep ^VALID_API_KEYS= /opt/lms/.env | cut -d= -f2- | tr , '\\n' | head -1)"
)


def _api(method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    """Дёрнуть боевой API с сервера. Возвращает (код ответа, тело или None)."""
    parts = [_KEY_CMD]
    if body is not None:
        # Тело через файл, а не через аргумент: кириллица в argv по дороге
        # ssh → shell → curl ломается («error parsing the body», tsk-545).
        payload = json.dumps(body, ensure_ascii=False)
        parts.append(
            "printf '%s' " + _sh_quote(payload) + " > /tmp/tsk886-body.json"
        )
    curl = [
        "curl -sS -o /tmp/tsk886-out.json -w '%{http_code}'",
        f"-X {method}",
        f"'{API_BASE}{path}'",
        '-H "X-API-Key: $KEY"',
    ]
    if body is not None:
        curl += ["-H 'Content-Type: application/json'", "--data-binary @/tmp/tsk886-body.json"]
    parts.append(" ".join(curl))
    parts.append("echo")
    parts.append("cat /tmp/tsk886-out.json")
    parts.append("rm -f /tmp/tsk886-out.json /tmp/tsk886-body.json")

    proc = subprocess.run(
        ["ssh", SSH_HOST, "; ".join(parts)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ssh упал: {proc.stderr.strip()}")
    head, _, tail = proc.stdout.partition("\n")
    status = int(head.strip())
    tail = tail.strip()
    try:
        return status, (json.loads(tail) if tail else None)
    except json.JSONDecodeError:
        return status, {"_raw": tail}


def _sh_quote(value: str) -> str:
    """Одинарные кавычки для POSIX-шелла на сервере."""
    return "'" + value.replace("'", "'\\''") + "'"


def create() -> int:
    """Завести временный корневой курс и записать его номер."""
    if STATE_FILE.exists():
        print(f"Фикстура уже заведена: {STATE_FILE.read_text(encoding='utf-8')}")
        print("Сначала убрать: --cleanup")
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    status, course = _api(
        "POST",
        "/courses/",
        {
            "title": f"[tsk-886] временный курс живой проверки {stamp}",
            "access_level": "self_guided",
            "description": (
                "Служебный курс живого прогона tsk-886. Учеников на нём нет, "
                "убирается тем же скриптом с --cleanup."
            ),
            "course_uid": f"tsk886-live-{stamp}",
            "parent_course_ids": [],
        },
    )
    if status not in (200, 201) or not course or "id" not in course:
        print(f"Не удалось завести курс: {status} {course}")
        return 1

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "course_id": course["id"],
                "course_uid": course.get("course_uid"),
                "title": course["title"],
                "created_at": stamp,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Заведён курс #{course['id']} «{course['title']}»")
    print(f"is_active={course.get('is_active')}  (номер записан в {STATE_FILE})")
    return 0


def status_cmd() -> int:
    """Показать текущее состояние заведённого курса."""
    if not STATE_FILE.exists():
        print("Фикстуры нет — файл с номером отсутствует.")
        return 1
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    code, course = _api("GET", f"/courses/{state['course_id']}")
    if code == 404:
        print(f"Курс #{state['course_id']} на проде не найден (уже убран?).")
        return 1
    if code != 200 or not course:
        print(f"Неожиданный ответ: {code} {course}")
        return 1
    print(f"#{course['id']}  «{course['title']}»  is_active={course['is_active']}")
    return 0


def cleanup() -> int:
    """Убрать РОВНО заведённый курс — по записанному номеру, не по признаку."""
    if not STATE_FILE.exists():
        print("Убирать нечего: файла с номером нет.")
        return 1
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    course_id = int(state["course_id"])

    code, course = _api("GET", f"/courses/{course_id}")
    if code == 404:
        print(f"Курс #{course_id} уже отсутствует — снимаю файл состояния.")
        STATE_FILE.unlink()
        return 0
    if code != 200 or not course:
        print(f"Не смог прочитать курс #{course_id}: {code} {course}")
        return 1

    # Сверка личности перед удалением: номер мог протухнуть, а удалять «то, что
    # лежит по этому номеру сегодня» нельзя.
    if course.get("course_uid") != state.get("course_uid"):
        print(
            f"ОТКАЗ: у курса #{course_id} код «{course.get('course_uid')}», "
            f"а записан «{state.get('course_uid')}». Это не наш курс — "
            "разбираться руками."
        )
        return 2

    users_code, users = _api("GET", f"/courses/{course_id}/users?limit=5")
    if users_code == 200 and users and (users.get("total") or 0) > 0:
        print(f"ОТКАЗ: на курсе #{course_id} есть ученики — сначала отчислить.")
        return 2

    del_code, del_body = _api("DELETE", f"/courses/{course_id}")
    if del_code not in (200, 204):
        print(f"Удаление не прошло: {del_code} {del_body}")
        return 1

    left, _ = _api("GET", f"/courses/{course_id}")
    print(f"Курс #{course_id} удалён (проверка: GET вернул {left}).")
    if left == 404:
        STATE_FILE.unlink()
        return 0
    print("Курс всё ещё отвечает — файл состояния оставлен, разобраться руками.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="завести курс")
    group.add_argument("--status", action="store_true", help="показать состояние")
    group.add_argument("--cleanup", action="store_true", help="убрать по номеру")
    args = parser.parse_args()

    if args.create:
        return create()
    if args.status:
        return status_cmd()
    return cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
