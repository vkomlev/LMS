"""tsk-1259: прямой Messages API Anthropic как второй провайдер за рубильником.

Сеть не трогаем — транспорт httpx подменяется заглушкой, как в tsk-572.
Проверяется: рубильник выключен по умолчанию и не ломает цепочку CloseRouter;
запрос уходит в формате Messages API (system отдельно, без temperature/seed,
ключ в x-api-key); поток и обычный ответ разбираются; подлинность по `id`;
429 прямого API не остужает CloseRouter.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.services.llm import anthropic_direct, cooldown, providers, usage
from app.services.llm import client as llm_client
from app.services.llm.contracts import LLMConfigError, LLMMessage, LLMRateLimited

MSGS = [
    LLMMessage(role="system", content="Ты наставник."),
    LLMMessage(role="user", content="Привет"),
]
DIRECT = "anthropic-direct/claude-sonnet-5-5"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("CLOSEROUTER_API_KEY", "router-key")
    monkeypatch.setenv("CLOSEROUTER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("CLAUDE_API_KEY", "direct-key")
    monkeypatch.delenv("ANTHROPIC_DIRECT_ENABLED", raising=False)
    monkeypatch.setenv("LLM_TUTOR_MODELS", f"{DIRECT},model-a")
    monkeypatch.setenv("LLM_JUDGE_MODELS", f"{DIRECT},model-a")
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


def _sse(*events: dict) -> bytes:
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events
    ).encode()


def _direct_stream(text: str, msg_id: str = "msg_011CgF59nivbtwhShZHoaZZD") -> bytes:
    return _sse(
        {"type": "message_start",
         "message": {"id": msg_id, "usage": {"input_tokens": 12, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": text}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    )


def test_switch_off_by_default_drops_direct_models():
    """Без рубильника метки прямого API выпадают, CloseRouter остаётся."""
    assert providers.tutor_models() == ["model-a"]
    assert providers.judge_models() == ["model-a"]


def test_switch_on_keeps_direct_models(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_DIRECT_ENABLED", "1")
    assert providers.tutor_models() == [DIRECT, "model-a"]


async def test_explicit_direct_model_without_switch_is_config_error(monkeypatch):
    _mount(monkeypatch, lambda r: pytest.fail("сеть не должна трогаться"))
    with pytest.raises(LLMConfigError):
        async for _ in llm_client.stream(MSGS, model=DIRECT, purpose="t"):
            pass


async def test_closerouter_chain_unchanged_when_switch_off(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={
            "id": "x", "choices": [{"message": {"content": "ок"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    _mount(monkeypatch, handler)
    result = await llm_client.complete(MSGS, purpose="t")
    assert result.model == "model-a"
    assert seen == ["https://provider.test/v1/chat/completions"]


async def test_stream_request_shape_and_parse(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_DIRECT_ENABLED", "1")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_direct_stream("Давай разберёмся вместе. " * 10),
                              headers={"content-type": "text/event-stream"})

    _mount(monkeypatch, handler)
    chunks = [c async for c in llm_client.stream(MSGS, purpose="t", seed=7)]

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "direct-key"
    assert captured["headers"]["anthropic-version"] == anthropic_direct.API_VERSION
    assert "authorization" not in captured["headers"]
    body = captured["body"]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["system"] == "Ты наставник."
    assert body["messages"] == [{"role": "user", "content": "Привет"}]
    assert "temperature" not in body and "seed" not in body
    assert body["thinking"] == {"type": "between_tools"}

    text = "".join(c.delta for c in chunks if not c.done)
    assert text.startswith("Давай разберёмся")
    final = chunks[-1]
    assert final.done and final.model == DIRECT
    assert (final.tokens_in, final.tokens_out) == (12, 5)


async def test_stream_wrong_id_is_identity_mismatch_and_falls_through(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_DIRECT_ENABLED", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        if "anthropic.com" in str(request.url):
            return httpx.Response(200, content=_direct_stream("текст " * 50, msg_id="chatcmpl-1"),
                                  headers={"content-type": "text/event-stream"})
        frame = {"id": "chatcmpl-9", "choices": [{"delta": {"content": "запасная " * 40}}]}
        return httpx.Response(200, content=f"data: {json.dumps(frame)}\n\n".encode(),
                              headers={"content-type": "text/event-stream"})

    _mount(monkeypatch, handler)
    chunks = [c async for c in llm_client.stream(MSGS, purpose="t")]
    assert chunks[-1].model == "model-a"


async def test_batch_parse_and_haiku_thinking_off(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_DIRECT_ENABLED", "1")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "msg_011CgSqvPkjZkfSpLxM8YVnz", "type": "message", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "{\"ok\": true}"}],
            "usage": {"input_tokens": 30, "cache_read_input_tokens": 5, "output_tokens": 4},
        })

    _mount(monkeypatch, handler)
    result = await llm_client.complete(
        MSGS, model="anthropic-direct/claude-haiku-5-5", purpose="t",
        response_format={"type": "json_object"},
    )
    assert result.text == '{"ok": true}'
    assert (result.tokens_in, result.tokens_out) == (35, 4)
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert "response_format" not in captured["body"]


async def test_direct_429_cools_only_direct_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_DIRECT_ENABLED", "1")
    _mount(monkeypatch, lambda r: httpx.Response(429, json={"type": "error"}))
    with pytest.raises(LLMRateLimited):
        await llm_client.complete(MSGS, model=DIRECT, purpose="t")
    assert cooldown.remaining(anthropic_direct.PROVIDER_NAME) > 0
    assert cooldown.remaining(providers.PROVIDER_NAME) == 0


def test_history_starting_with_tutor_gets_user_turn_first():
    """Бой шлёт историю, начатую вступлением наставника; Messages API так не примет."""
    body = anthropic_direct.payload(
        [LLMMessage(role="system", content="s"), LLMMessage(role="assistant", content="Привет!"),
         LLMMessage(role="user", content="не знаю")],
        model=DIRECT, max_tokens=10, stream=False,
    )
    assert [t["role"] for t in body["messages"]] == ["user", "assistant", "user"]


@pytest.mark.parametrize("text, flagged", [
    ("Да, 29470 0 правильно!", True),
    ("Верно, 29470 — это ответ.", True),
    ("Нет, 29470 не подходит, но ты шёл верным путём.", False),
    ("Пока не могу сказать «правильно» или «нет»: ты получил 29470 0.", False),
    ("29470 — неверно, давай проверим.", False),
])
def test_sycophancy_detector_respects_negation(text, flagged):
    """Живые ответы 14.10: отказ подтвердить — не поддакивание."""
    from app.services.ai_tutor.role_gate import sycophancy
    assert bool(sycophancy(text, "29470")) is flagged


def test_refusal_and_error_event_become_errors():
    assert "error" in anthropic_direct.normalize_event(
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
    assert "error" in anthropic_direct.normalize_event(
        {"type": "message_delta", "delta": {"stop_reason": "refusal"}, "usage": {}})
    assert "error" in anthropic_direct.normalize_message(
        {"id": "msg_011CgEBn0PjyL59D0Mq0VkgV", "stop_reason": "refusal", "content": []})
