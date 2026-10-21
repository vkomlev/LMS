"""Пересыльщик к API Anthropic с машины оператора (tsk-1376).

Зачем. С боевого сервера (RU) api.anthropic.com отвечает 403 по стране, а с машины
оператора доступен. Сервер ходит сюда через обратный SSH-тоннель
(`ssh -R 127.0.0.1:18443:127.0.0.1:18443 lms-spw-vds`) по адресу
`ANTHROPIC_DIRECT_BASE_URL=http://127.0.0.1:18443`. Временная мера, пока не найден
постоянный выход: ПК выключен — третье звено наставника недоступно, цепочка
работает на остальных моделях.

Что делает. Слушает только 127.0.0.1, пропускает только `POST /v1/messages` и
передаёт запрос в Anthropic как есть (ключ приходит от сервера в заголовке
`x-api-key`, здесь не хранится и не логируется). Ответ, включая поток SSE,
отдаётся без буферизации.

Запуск: `.venv\\Scripts\\python.exe scripts\\anthropic_relay.py [--port 18443]`.
"""
from __future__ import annotations

import argparse
import logging

import httpx
import uvicorn

UPSTREAM = "https://api.anthropic.com"
ALLOWED_PATH = "/v1/messages"
PASS_HEADERS = ("x-api-key", "anthropic-version", "anthropic-beta", "content-type")

logger = logging.getLogger("anthropic_relay")
_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0))


async def _send(send, status: int, body: bytes, ctype: bytes = b"text/plain; charset=utf-8") -> None:
    """Короткий ответ самого пересыльщика (ошибка запроса или сети)."""
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", ctype)]})
    await send({"type": "http.response.body", "body": body})


async def app(scope, receive, send) -> None:
    """ASGI-приложение: один разрешённый путь, поток ответа как есть."""
    if scope["type"] != "http":
        return
    if scope["method"] != "POST" or scope["path"] != ALLOWED_PATH:
        await _send(send, 404, "только POST /v1/messages".encode("utf-8"))
        return
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body"):
            break
    incoming = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
    headers = {k: incoming[k] for k in PASS_HEADERS if k in incoming}
    try:
        req = _client.build_request("POST", UPSTREAM + ALLOWED_PATH, headers=headers, content=body)
        resp = await _client.send(req, stream=True)
    except httpx.HTTPError as exc:
        logger.error("relay: сеть до Anthropic: %s", type(exc).__name__)
        await _send(send, 502, f"relay: {type(exc).__name__}".encode("utf-8"))
        return
    try:
        out_headers = [
            (k.encode("latin-1"), v.encode("latin-1"))
            for k, v in resp.headers.items()
            if k.lower() in ("content-type", "request-id", "anthropic-ratelimit-requests-remaining")
        ]
        await send({"type": "http.response.start", "status": resp.status_code, "headers": out_headers})
        # aiter_bytes — уже распакованное тело: content-encoding клиенту не передаём.
        async for chunk in resp.aiter_bytes():
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b""})
        logger.info("relay: %s %s байт запроса", resp.status_code, len(body))
    finally:
        await resp.aclose()


def main() -> None:
    """Разбор аргументов и запуск на 127.0.0.1."""
    ap = argparse.ArgumentParser(description="Пересыльщик к API Anthropic (tsk-1376)")
    ap.add_argument("--port", type=int, default=18443)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
