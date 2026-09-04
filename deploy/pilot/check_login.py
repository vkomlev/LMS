"""Проверить вход по ссылке из письма ТЕМ ЖЕ путём, каким идёт человек (tsk-789).

Зачем: после переноса учётки в базе всё выглядит на месте, а войти нельзя.
Вход ищет человека по `identity_link`, а не по полю `users.email`; учётку с
почтой, но без привязки платформа считает осиротевшей и отказывает словами
«Email уже привязан к другому аккаунту в нестандартном состоянии». Именно так
и случилось при первом переносе — отказ увидел оператор, а не проверка.

Не «привязка в базе есть», а «ссылка сработала»: выпускаем токен сервисом
платформы и гасим его тем же эндпоинтом, который открывает браузер. Письмо не
шлём — оно только доставляет ссылку, а проверяем мы её приём.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/lms-pilot")

API = "http://127.0.0.1:8020/api/v1"
EMAILS = sys.argv[1:] or ["victor.komlev@mail.ru", "ivankrynin086@gmail.com"]


async def issue(email: str) -> str:
    import app.db.base  # noqa: F401
    from app.db.session import async_session_factory
    from app.services.auth import magic_link_service

    async with async_session_factory() as db:
        token = await magic_link_service.create_magic_link(db, email)
        await db.commit()
        return token if isinstance(token, str) else token.token


def consume(token: str) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{API}/auth/magic-link/verify",
        data=json.dumps({"token": token}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8")[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


async def main() -> int:
    bad = 0
    for email in EMAILS:
        try:
            token = await issue(email)
        except Exception as exc:  # noqa: BLE001
            print(f"{email}: не удалось выпустить ссылку — {type(exc).__name__}: {exc}")
            bad = 1
            continue
        code, body = consume(token)
        ok = code == 200
        print(f"{email}: вход по ссылке -> HTTP {code} {'ОК' if ok else 'ОТКАЗ'}")
        if not ok:
            print("   ", body)
            bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
