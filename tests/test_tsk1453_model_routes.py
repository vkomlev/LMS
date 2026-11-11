"""Маршрут принадлежит запросу модели, включая поток и запасную попытку."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from app.services.llm import client, cooldown, providers, usage
from app.services.llm.contracts import (
    JUDGE_MAX_TOKENS, TUTOR_MAX_TOKENS, LLMMessage, LLMRateLimited,
)

TERRA = "openai/gpt-6.1-terra"
SOL = "openai/gpt-6.1-sol"
LUNA = "openai/gpt-5.6-luna"
GLM = "z-ai/glm-5.3-flash"
STABLE = {"name": "auto", "strategy": "stable"}
MESSAGES = [LLMMessage(role="user", content="Дай одну подсказку про цикл.")]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("CLOSEROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CLOSEROUTER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_JUDGE_MODELS", f"{TERRA},{LUNA}")
    monkeypatch.setenv("LLM_TUTOR_MODELS", GLM)
    cooldown.reset()

    async def record(_event):
        pass

    monkeypatch.setattr(usage, "record", record)
    yield
    cooldown.reset()


def mount(monkeypatch, handler):
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)


def reply(**changes):
    body = {
        "id": "chatcmpl-12345",
        "choices": [{"message": {"content": "Проверь границу цикла."}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 9},
    }
    body.update(changes)
    return httpx.Response(200, json=body)


@pytest.mark.parametrize("model, route", [
    (TERRA, STABLE), (SOL, STABLE), (GLM, STABLE),
    ("openai/gpt-6.1-terra-other", None),
    ("anthropic-direct/claude-haiku-5-5", None), ("unknown/model", None),
])
def test_routes_are_exact_and_request_local(model, route):
    assert providers.request_provider(model) == route
    if isinstance(route, dict):
        providers.request_provider(model)["strategy"] = "fast"
        assert providers.request_provider(model) == STABLE


def test_approved_default_chains(monkeypatch):
    for name in ("LLM_TUTOR_MODELS", "LLM_JUDGE_MODELS", "LLM_FEEDBACK_MODELS"):
        monkeypatch.delenv(name, raising=False)
    assert providers.tutor_models() == [GLM]
    assert providers.judge_models() == [TERRA, LUNA]
    assert not any("anthropic" in m for m in providers.feedback_models())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["503", "timeout", "identity"])
async def test_judge_fallback_stays_on_stable_routes(monkeypatch, failure):
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        if body["model"] == TERRA:
            if failure == "503":
                return httpx.Response(503, json={"error": "unavailable"})
            if failure == "timeout":
                raise httpx.ReadTimeout("timeout", request=request)
            return reply(id="msg_011CgF59nivbtwhShZHoaZZD")
        return reply()

    mount(monkeypatch, handler)
    result = await client.complete(MESSAGES, purpose="code_review")
    assert result.model == LUNA
    assert [(b["model"], b.get("provider")) for b in sent] == [
        (TERRA, STABLE), (LUNA, None),
    ]


@pytest.mark.asyncio
async def test_explicit_terra_uses_stable_route(monkeypatch):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return reply()

    mount(monkeypatch, handler)
    result = await client.complete(MESSAGES, purpose="code_review", model=TERRA)
    assert result.model == TERRA
    assert sent[0]["provider"] == STABLE


@pytest.mark.asyncio
async def test_stream_uses_stable_route(monkeypatch):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        frame = {"choices": [{"delta": {"content": "Проверь границу цикла."}}]}
        sse = "data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"})

    mount(monkeypatch, handler)
    parts = [p async for p in client.stream(MESSAGES, purpose="tutor")]
    assert parts
    assert sent[0]["model"] == GLM
    assert sent[0]["provider"] == STABLE


@pytest.mark.asyncio
async def test_429_preserves_provider_cooldown(monkeypatch):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(429, json={"error": "rate limited"})

    mount(monkeypatch, handler)
    with pytest.raises(LLMRateLimited):
        await client.complete(MESSAGES, purpose="code_review")
    assert len(sent) == 1
    assert sent[0]["provider"] == STABLE


@pytest.mark.parametrize("judge, model, route", [
    (True, TERRA, STABLE), (True, SOL, STABLE), (False, GLM, STABLE),
])
def test_monthly_bakeoff_uses_same_routes(monkeypatch, judge, model, route):
    from scripts import llm_model_bakeoff as bakeoff

    sent = []

    @contextlib.contextmanager
    def post(base, key, path, payload, timeout):
        sent.append(payload)
        data = b'{}' if judge else b'data: [DONE]\n\n'
        yield io.BytesIO(data)

    monkeypatch.setattr(bakeoff, "_post", post)
    if judge:
        bakeoff.judge_probe(model, "system", "user", "https://provider.test", "test-key")
    else:
        bakeoff.probe(model, "system", "https://provider.test", "test-key")
    assert sent[0]["provider"] == route
    if judge:
        assert sent[0]["seed"] == 42


@pytest.mark.parametrize("axis", ["tutor", "judge"])
def test_explicit_bakeoff_candidate_bootstraps_imports(axis):
    """Запуск файла из scripts не требует внешнего PYTHONPATH и не идёт в сеть."""
    script = r'''
import contextlib, importlib.util, io, sys
spec = importlib.util.spec_from_file_location('stand', sys.argv[1])
stand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stand)
sent = []
@contextlib.contextmanager
def post(base, key, path, payload, timeout):
    sent.append(payload)
    yield io.BytesIO(b'{}' if sys.argv[2] == 'judge' else b'data: [DONE]\n\n')
stand._post = post
if sys.argv[2] == 'judge':
    stand.judge_probe('openai/gpt-6.1-terra', 'system', 'user', 'https://invalid.invalid', 'test-key')
    assert sent[0]['provider'] == {'name': 'auto', 'strategy': 'stable'}
else:
    stand.probe('z-ai/glm-5.3-flash', 'system', 'https://invalid.invalid', 'test-key')
    assert sent[0]['provider'] == {'name': 'auto', 'strategy': 'stable'}
'''
    path = Path(__file__).resolve().parents[1] / "scripts/llm_model_bakeoff.py"
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(path), axis],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_weekly_judge_uses_live_cap_and_seed(monkeypatch):
    from app.services import llm_chain_check_cron_service as check

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        verdict = {"language": "Python", "code_quality": {"score": 8, "notes": []},
                   "ai_authorship": {"verdict": "student_likely", "reasoning": "учебный код"}}
        return reply(choices=[{"message": {"content": json.dumps(verdict)}}])

    mount(monkeypatch, handler)
    good, reason, kind = await check.probe_judge_model(TERRA, check.PROBE_WORKS[0])
    assert good, reason
    assert sent[0]["max_tokens"] == JUDGE_MAX_TOKENS
    assert sent[0]["seed"] == 42
    assert sent[0]["provider"] == STABLE


@pytest.mark.asyncio
async def test_weekly_tutor_uses_live_cap(monkeypatch):
    from app.services import llm_chain_check_cron_service as check

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        data = 'data: {"choices": [{"delta": {"content": "готов"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=data, headers={"Content-Type": "text/event-stream"})

    mount(monkeypatch, handler)
    good, reason, kind = await check.probe_tutor_model(GLM)
    assert good, reason
    assert sent[0]["max_tokens"] == TUTOR_MAX_TOKENS
    assert sent[0]["provider"] == STABLE
