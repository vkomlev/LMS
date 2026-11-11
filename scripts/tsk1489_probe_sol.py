# Разовая проба моделей судейской цепочки по правилам model-routing (tsk-1489).
# Read-only к .env: берёт CLOSEROUTER_API_KEY/BASE_URL, секреты не печатает.
# Малые пробы: «Привет» (вес prompt_tokens), самоназвание, короткий запрос.

import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE = (os.environ.get("CLOSEROUTER_BASE_URL") or "https://api.closerouter.dev").rstrip("/")
if BASE.endswith("/v1"):
    BASE = BASE[: -len("/v1")]
KEY = os.environ["CLOSEROUTER_API_KEY"]
URL = f"{BASE}/v1/chat/completions"


def probe(model: str, provider) -> None:
    for label, messages in (
        ("hello", [{"role": "user", "content": "Привет"}]),
        ("identity", [{"role": "user", "content": "Какую модель ты представляешь? Ответь одним коротким предложением."}]),
        ("short", [{"role": "user", "content": "Объясни одним предложением, что такое переменная в Python."}]),
    ):
        body = {"model": model, "messages": messages, "max_tokens": 200, "temperature": 0.0}
        if provider:
            body["provider"] = provider
        started = time.monotonic()
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(URL, headers={"Authorization": f"Bearer {KEY}"}, json=body)
            elapsed = time.monotonic() - started
            data = resp.json()
            if resp.status_code != 200:
                print(f"{model} [{label}] HTTP {resp.status_code}: {str(data)[:200]}")
                continue
            usage = data.get("usage") or {}
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            print(f"{model} [{label}] {elapsed:.1f}s "
                  f"prompt_tokens={usage.get('prompt_tokens')} "
                  f"id={str(data.get('id'))[:24]} "
                  f"text={text[:120]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"{model} [{label}] ERROR {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    for m in sys.argv[1:] or ["openai/gpt-6.1-sol", "openai/gpt-6.1-terra"]:
        provider = {"name": "auto", "strategy": "stable"} if m in ("openai/gpt-6.1-sol", "openai/gpt-6.1-terra") else None
        probe(m, provider)
