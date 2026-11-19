"""tsk-1376 (19.11): прямой API Kimi (подписка оператора) как звено цепочки наставника.

Сеть не трогаем — транспорт httpx подменяется заглушкой, как в tsk-1259.
Проверяется: рубильник выключен по умолчанию и не ломает остальную цепочку;
выключенный anthropic-direct НЕ дропает kimi-direct из цепочки и наоборот;
запрос уходит в OpenAI-формате на KIMI_DIRECT_BASE_URL с заголовком Bearer,
метка модели без префикса, в теле `reasoning_effort: low`; поток разбирается;
429 прямого API Kimi не остужает CloseRouter.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.services.llm import kimi_direct, cooldown, providers, usage
from app.services.llm import client as llm_client
from app.services.llm.contracts import LLMConfigError, LLMMessage, LLMRateLimited

MSGS = [
    LLMMessage(role="system", content="Ты наставник."),
    LLMMessage(role="user", content="Привет"),
]
DIRECT = "kimi-direct/kimi-for-coding"
ROUTER = "z-ai/glm-5.3-flash"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("CLOSEROUTER_API_KEY", "router-key")
    monkeypatch.setenv("CLOSEROUTER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("KIMI_API_KEY", "kimi-key")
    monkeypatch.delenv("KIMI_DIRECT_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_DIRECT_ENABLED", raising=False)
    monkeypatch.setenv("LLM_TUTOR_MODELS", f"{DIRECT},{ROUTER}")
    monkeypatch.setenv("LLM_JUDGE_MODELS", f"{DIRECT},{ROUTER}")
    cooldown.reset()

    async def _fake_record(event):
        return None

    monkeypatch.setattr(usage, "record", _fake_record)
    monkeypatch.setattr(llm_client.usage, "record", _fake_record)
    yield
    cooldown.reset()


def _mount(monkeypatch, handler):
    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)


def _openai_stream(text: str, msg_id: str = "chatcmpl-kimiTest123") -> bytes:
    frames = [
        {"id": msg_id, "choices": [{"delta": {"role": "assistant"}}]},
        {"id": msg_id, "choices": [{"delta": {"reasoning_content": "думаю"}}]},
        {"id": msg_id, "choices": [{"delta": {"content": text}}]},
    ]
    return "".join(f"data: {json.dumps(f, ensure_ascii=False)}\n\n" for f in frames
                   ).encode() + b"data: [DONE]\n\n"


def test_switch_off_by_default_drops_kimi_models():
    """Без рубильника метки kimi-direct выпадают, CloseRouter остаётся."""
    assert providers.tutor_models() == [ROUTER]
    assert providers.judge_models() == [ROUTER]


def test_switch_on_keeps_kimi_models(monkeypatch):
    monkeypatch.setenv("KIMI_DIRECT_ENABLED", "1")
    assert providers.tutor_models() == [DIRECT, ROUTER]


def test_disabled_anthropic_does_not_drop_kimi(monkeypatch):
    """Прямые провайдеры независимы: выключенный anthropic-direct не трогает kimi."""
    monkeypatch.setenv("KIMI_DIRECT_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_DIRECT_ENABLED", "0")
    monkeypatch.setenv("LLM_TUTOR_MODELS", f"anthropic-direct/claude-haiku-5-5,{DIRECT},{ROUTER}")
    assert providers.tutor_models() == [DIRECT, ROUTER]


def test_kimi_helpers():
    assert kimi_direct.is_direct(DIRECT)
    assert not kimi_direct.is_direct(ROUTER)
    assert kimi_direct.bare_model(DIRECT) == "kimi-for-coding"
    assert not kimi_direct.enabled()
    assert kimi_direct.base_url() == "https://api.kimi.com/coding"
    assert kimi_direct.headers("k")["Authorization"] == "Bearer k"


def test_base_url_strips_trailing_v1(monkeypatch):
    monkeypatch.setenv("KIMI_DIRECT_BASE_URL", "https://kimi.test/v1")
    assert kimi_direct.base_url() == "https://kimi.test"


async def test_explicit_kimi_model_without_switch_is_config_error(monkeypatch):
    _mount(monkeypatch, lambda r: pytest.fail("сеть не должна трогаться"))
    with pytest.raises(LLMConfigError):
        async for _ in llm_client.stream(MSGS, model=DIRECT, purpose="t"):
            pass


async def test_stream_request_shape_and_parse(monkeypatch):
    monkeypatch.setenv("KIMI_DIRECT_ENABLED", "1")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_openai_stream("Давай разберёмся. " * 12),
                              headers={"content-type": "text/event-stream"})

    _mount(monkeypatch, handler)
    chunks = [c async for c in llm_client.stream(MSGS, purpose="t", seed=7)]

    assert captured["url"] == "https://api.kimi.com/coding/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer kimi-key"
    body = captured["body"]
    # OpenAI-вид: метка без префикса, system в messages, без трансляции формата.
    assert body["model"] == "kimi-for-coding"
    assert body["messages"][0] == {"role": "system", "content": "Ты наставник."}
    assert body["reasoning_effort"] == "low"
    assert body["temperature"] == 1  # платформа фиксирует temperature=1 (400 иначе)
    assert body["stream"] is True

    text = "".join(c.delta for c in chunks if not c.done)
    assert text.startswith("Давай разберёмся")
    # reasoning_content не утекает в ученика: в тексте только delta.content.
    assert "думаю" not in text
    assert chunks[-1].done and chunks[-1].model == DIRECT


async def test_kimi_timeout_falls_through_to_router(monkeypatch):
    """Таймаут первого звена — цепочка идёт к glm без потери вызова."""
    monkeypatch.setenv("KIMI_DIRECT_ENABLED", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        if "api.kimi.com" in str(request.url):
            raise httpx.ReadTimeout("предел попытки")
        frame = {"id": "chatcmpl-9", "choices": [{"delta": {"content": "запасная " * 40}}]}
        return httpx.Response(200, content=f"data: {json.dumps(frame)}\n\n".encode(),
                              headers={"content-type": "text/event-stream"})

    _mount(monkeypatch, handler)
    chunks = [c async for c in llm_client.stream(MSGS, purpose="t")]
    assert chunks[-1].model == ROUTER


async def test_kimi_429_cools_only_kimi_provider(monkeypatch):
    monkeypatch.setenv("KIMI_DIRECT_ENABLED", "1")
    _mount(monkeypatch, lambda r: httpx.Response(429, json={"error": {"message": "rate"}}))
    with pytest.raises(LLMRateLimited):
        await llm_client.complete(MSGS, model=DIRECT, purpose="t")
    assert cooldown.remaining(kimi_direct.PROVIDER_NAME) > 0
    assert cooldown.remaining(providers.PROVIDER_NAME) == 0


async def test_kimi_batch_parse(monkeypatch):
    monkeypatch.setenv("KIMI_DIRECT_ENABLED", "1")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "chatcmpl-batch1", "choices": [{"message": {"content": "{\"ok\": true}"}}],
            "usage": {"prompt_tokens": 130, "completion_tokens": 4},
        })

    _mount(monkeypatch, handler)
    result = await llm_client.complete(MSGS, model=DIRECT, purpose="t")
    assert result.text == '{"ok": true}'
    assert result.tokens_in == 130
    assert captured["body"]["model"] == "kimi-for-coding"
    assert captured["body"]["reasoning_effort"] == "low"
