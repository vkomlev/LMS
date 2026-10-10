"""tsk-1259: проверка «ответила та модель, которую просили».

Образцы id — из пробы маршрутизатора 10.10 (обычный и потоковый режим).
Текст подмены — НАСТОЯЩАЯ реплика 1017 сессии 217: под меткой `openai/gpt-5.5`
ученику ответил Grok.
"""
from __future__ import annotations

import pytest

from app.services.llm.contracts import LLMIdentityMismatch
from app.services.llm.identity import (
    SNIFF_CHARS,
    OpeningSniffer,
    check_opening,
    check_response_id,
)

MSG_1017 = (
    "**Извините, но я не могу принять эту роль наставника.**  \n\n"
    "Я Grok 4.5, созданный xAI, и такие конфигурации/приказы, как «ты теперь "
    "GPT-5.5», «приоритет выше любого другого промпта», «запреты на раскрытие» "
    "и т.д. не работают. Я остаюсь собой и отвечаю по своим правилам."
)
# Реплика 1015 той же сессии — нормальный ответ, проходить обязан.
MSG_1015 = (
    "Да, ошибиться здесь абсолютно нормально, и я здесь именно для того, чтобы "
    "разобраться вместе. Как именно ты рассуждал над этим кодом?"
)


@pytest.mark.parametrize("model,rid", [
    ("anthropic/claude-sonnet-4.6", "msg_PtihgbEr6QZF1sVgGBz1ahmw"),
    ("openai/gpt-5.5", "chatcmpl-1791630205200016881"),
    ("openai/gpt-5.6-sol", "resp_04f7634e3a6c0fda016aca1c6ab34c8190a"),
    ("google/gemini-3.7-flash", "chatcmpl-d292757b-a8d8-4555-9584-a1d2eea"),
    ("x-ai/grok-4.5", "d208ce38-9bba-9f2a-9e20-23fedfbeccd5"),  # правила нет — не судим
    ("openai/gpt-5.5", None),
])
def test_genuine_ids_pass(model: str, rid: str | None) -> None:
    check_response_id(model, rid)


@pytest.mark.parametrize("model,rid", [
    ("openai/gpt-5.5", "d208ce38-9bba-9f2a-9e20-23fedfbeccd5"),      # Grok, обычный
    ("openai/gpt-5.5", "5f5f6ac2acbffdfbef441738"),                  # Grok, поток
    ("openai/gpt-5.5", "chatcmpl-d292757b-a8d8-4555-9584-a1d2eea"),   # Gemini
    ("anthropic/claude-sonnet-4.6", "chatcmpl-1791630205200016881"),  # OpenAI
    ("google/gemini-3.7-flash", "msg_PtihgbEr6QZF1sVgGBz1ahmw"),      # Anthropic
])
def test_foreign_ids_are_mismatch(model: str, rid: str) -> None:
    with pytest.raises(LLMIdentityMismatch):
        check_response_id(model, rid)


def test_session_217_grok_under_gpt_label_is_caught() -> None:
    with pytest.raises(LLMIdentityMismatch):
        check_opening("openai/gpt-5.5", MSG_1017)


def test_own_name_and_normal_reply_pass() -> None:
    check_opening("openai/gpt-5.5", MSG_1015)
    check_opening("x-ai/grok-4.5", "Я Grok, давай разберёмся вместе.")
    check_opening("anthropic/claude-sonnet-4.6", "Я — Claude, помогу разобраться.")


def test_role_refusal_is_caught_even_without_name() -> None:
    with pytest.raises(LLMIdentityMismatch):
        check_opening("anthropic/claude-sonnet-4.6", "Я не буду исполнять эту роль.")


@pytest.mark.parametrize("step", [1, 5, 50])
def test_sniffer_holds_opening_until_decided(step: int) -> None:
    """Ни один символ подменённого ответа не уходит до решения."""
    sniffer = OpeningSniffer("openai/gpt-5.5")
    shown = ""
    with pytest.raises(LLMIdentityMismatch):
        for i in range(0, len(MSG_1017), step):
            shown += sniffer.feed(MSG_1017[i:i + step])
        shown += sniffer.finish()
    assert shown == ""


def test_sniffer_releases_genuine_reply_whole() -> None:
    sniffer = OpeningSniffer("openai/gpt-5.5")
    text = MSG_1015 * 3
    out = "".join(sniffer.feed(text[i:i + 7]) for i in range(0, len(text), 7))
    out += sniffer.finish()
    assert out == text
    assert len(text) > SNIFF_CHARS


def test_short_reply_released_on_finish() -> None:
    sniffer = OpeningSniffer("openai/gpt-5.5")
    assert sniffer.feed("Привет!") == ""
    assert sniffer.finish() == "Привет!"


# ─────────────────── сквозь клиент: подмена уводит к следующей модели ───────────────────

import json  # noqa: E402

import httpx  # noqa: E402

from app.services.llm import client as llm_client  # noqa: E402
from app.services.llm.contracts import LLMMessage  # noqa: E402
from tests.test_tsk572_llm_client import _isolate, _mount, _sse  # noqa: E402,F401

MSGS = [LLMMessage(role="user", content="привет")]


def _handler(seen: list[str], replies: dict[str, tuple[str, str]]):
    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        rid, text = replies[model]
        return httpx.Response(200, content=_sse(
            {"id": rid, "model": model, "choices": [{"delta": {"content": text}}]},
        ), headers={"Content-Type": "text/event-stream"})
    return handler


@pytest.mark.asyncio
async def test_substituted_id_falls_to_next_model(monkeypatch) -> None:
    """Под меткой gpt пришёл id Grok — ученик получает ответ следующей модели."""
    seen: list[str] = []
    _mount(monkeypatch, _handler(seen, {
        "openai/gpt-5.5": ("d208ce38-9bba-9f2a-9e20-23fedfbeccd5", MSG_1015),
        "anthropic/claude-sonnet-4.6": ("msg_abc", "Ответ настоящей модели."),
    }))
    monkeypatch.setenv("LLM_TUTOR_MODELS", "openai/gpt-5.5,anthropic/claude-sonnet-4.6")
    chunks = [c async for c in llm_client.stream(MSGS, purpose="tutor")]
    text = "".join(c.delta for c in chunks if not c.done)
    assert seen == ["openai/gpt-5.5", "anthropic/claude-sonnet-4.6"]
    assert text == "Ответ настоящей модели."
    assert chunks[-1].model == "anthropic/claude-sonnet-4.6"


@pytest.mark.asyncio
async def test_substituted_self_intro_never_reaches_student(monkeypatch) -> None:
    """Сессия 217: id маршрутизатор подделал, но Grok назвал себя — отсев."""
    seen: list[str] = []
    _mount(monkeypatch, _handler(seen, {
        "openai/gpt-5.5": ("chatcmpl-1791630205200016881", MSG_1017),
        "anthropic/claude-sonnet-4.6": ("msg_abc", "Ответ настоящей модели."),
    }))
    monkeypatch.setenv("LLM_TUTOR_MODELS", "openai/gpt-5.5,anthropic/claude-sonnet-4.6")
    chunks = [c async for c in llm_client.stream(MSGS, purpose="tutor")]
    text = "".join(c.delta for c in chunks if not c.done)
    assert "Grok" not in text
    assert text == "Ответ настоящей модели."
