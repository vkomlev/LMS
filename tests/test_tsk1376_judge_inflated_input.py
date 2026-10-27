"""tsk-1376 (разбор 27.10): судья не принимает вердикт с раздутым входом.

Образцы — боевые: неделя 20–27.10 (luna, id `chatcmpl-<24 hex>`, вход 4 854–5 635
при медиане обычных 940) и проба 27.10 13:2x (вопрос в ~20 токенов, вход 4 406,
самоназвание «Codex»). Настоящий вход судьи — 730–2 977 токенов.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.services.llm import client as llm_client
from app.services.llm.contracts import LLMIdentityMismatch, LLMMessage
from app.services.llm.identity import check_input_tokens
from tests.test_tsk572_llm_client import _isolate, _mount  # noqa: F401

JUDGE_PROMPT_CHARS = 2484  # системный промпт судьи + короткий код


@pytest.mark.parametrize("chars,tokens", [
    (JUDGE_PROMPT_CHARS, 830),    # обычный короткий разбор
    (JUDGE_PROMPT_CHARS, 2044),   # верх обычных у gemini при коротком коде
    (9000, 2977),                 # максимум недели — длинная программа
    (60, 29),                     # проба «Кто ты?» без инструкции
])
def test_genuine_input_passes(chars: int, tokens: int) -> None:
    check_input_tokens("openai/gpt-5.6-luna", chars, tokens)


@pytest.mark.parametrize("chars,tokens", [
    (JUDGE_PROMPT_CHARS, 4854),   # минимум hex24 за неделю
    (JUDGE_PROMPT_CHARS, 5635),   # максимум hex24
    (9000, 9774),                 # resp_ с самым длинным входом
    (60, 4406),                   # проба 27.10 — «Codex»
])
def test_inflated_input_is_mismatch(chars: int, tokens: int) -> None:
    with pytest.raises(LLMIdentityMismatch):
        check_input_tokens("openai/gpt-5.6-luna", chars, tokens)


def _reply(model: str, tokens_in: int) -> dict:
    return {"id": "chatcmpl-1791630205200016881", "model": model,
            "choices": [{"message": {"content": '{"verdict": "ok"}'}}],
            "usage": {"prompt_tokens": tokens_in, "completion_tokens": 5}}


@pytest.mark.asyncio
async def test_inflated_verdict_goes_to_next_model(monkeypatch, _isolate) -> None:  # noqa: F811
    """Первая модель с раздутым входом — вердикт берётся у следующей."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        return httpx.Response(200, json=_reply(model, 5000 if model == "model-a" else 900))

    _mount(monkeypatch, handler)
    result = await llm_client.complete([LLMMessage(role="user", content="x" * 2400)],
                                       purpose="code_review")
    assert seen == ["model-a", "model-b"]
    assert result.model == "model-b"


@pytest.mark.asyncio
async def test_inflated_everywhere_is_retryable(monkeypatch, _isolate) -> None:  # noqa: F811
    """Раздуты все — отказ с `retryable`: разбор уйдёт на повтор в следующем тике."""
    _mount(monkeypatch, lambda r: httpx.Response(
        200, json=_reply(json.loads(r.content)["model"], 5000)))
    with pytest.raises(LLMIdentityMismatch) as exc:
        await llm_client.complete([LLMMessage(role="user", content="x" * 2400)],
                                  purpose="code_review")
    assert exc.value.retryable
