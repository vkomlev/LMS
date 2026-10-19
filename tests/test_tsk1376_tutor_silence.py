"""tsk-1376: наставник молчал — ложная отбраковка Google и сбой хода без следа.

Неделя 12–19.10: 5 разговоров без единого ответа. Прод-БД показала две причины,
которые лежат в коде: родной `responseId` Gemini (base64url) считался подменой, а
ход, на который цепочка не ответила, не оставлял в сессии ничего — `meta` пустой.
"""
from __future__ import annotations

import pytest

from app.services.ai_tutor import session_service
from app.services.llm.contracts import LLMIdentityMismatch
from app.services.llm.identity import check_response_id
from tests.test_tsk1259_llm_identity import MSGS, _handler  # noqa: F401
from tests.test_tsk572_llm_client import _isolate, _mount  # noqa: F401
from tests.test_tsk748_tutor_answer_guard import _bare_session, _drop_session, _meta


# Боевые id: llm_usage_event 14812/14818/16533 и проба 19.10 (17 входных токенов,
# самоназвание «Gemini… Google»).
@pytest.mark.parametrize("rid", [
    "oOvMarr0ObaM1e8P096wYQ",
    "POzMaoq7HZL81e8Pz7HlgAM",
    "KjDTavf2PP_Fg8UPzMiL8As",
    "ZATWap2aM7rLjuMP0YLf2A4",
    "chatcmpl-db71dc9c-5773-48c4-a3a8-b5b60d1",
])
def test_native_gemini_ids_are_genuine(rid: str) -> None:
    check_response_id("google/gemini-3.7-flash", rid)


# Настоящие подмены под меткой google: Anthropic, OpenAI, `msg_<hex>` (на пробе
# называл себя YandexGPT/ChatGPT при 270 входных токенах), Grok (24 hex и uuid).
@pytest.mark.parametrize("rid", [
    "msg_01noomeCgRIntDvL21Rx",
    "msg_4f098d9c920690f2947f",
    "chatcmpl-BTLOaqmwOdGxjuM",
    "resp_0fe5bfb6ab6c115d016ad5fab890c887d18",
    "4f098d9c920690f2947f0a1b",
    "d208ce38-9bba-9f2a-9e20-23fedfbeccd5",
])
def test_foreign_ids_under_google_label_still_caught(rid: str) -> None:
    with pytest.raises(LLMIdentityMismatch):
        check_response_id("google/gemini-3.6-flash", rid)


@pytest.mark.asyncio
async def test_exhausted_chain_reports_every_model(monkeypatch) -> None:
    """18.10: 404, подмена и таймаут — наружу раньше уходил только последний."""
    from app.services.llm import client as llm_client
    from app.services.llm.contracts import LLMError

    seen: list[str] = []
    _mount(monkeypatch, _handler(seen, {
        "google/gemini-3.6-flash": ("msg_4f098d9c920690f2947f", "Я YandexGPT."),
        "openai/gpt-5.5": ("chatcmpl-1791630205200016881", "Я Grok, создан xAI."),
    }))
    monkeypatch.setenv("LLM_TUTOR_MODELS", "google/gemini-3.6-flash,openai/gpt-5.5")
    with pytest.raises(LLMError) as info:
        [c async for c in llm_client.stream(MSGS, purpose="tutor")]
    attempts = info.value.attempts
    assert [a["model"] for a in attempts] == ["google/gemini-3.6-flash", "openai/gpt-5.5"]
    assert {a["outcome"] for a in attempts} == {"LLMIdentityMismatch"}
    assert all(isinstance(a["ms"], int) for a in attempts)


def _plain_handler(seen: list[str], ids: dict[str, str]):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        seen.append(model)
        return httpx.Response(200, json={
            "id": ids[model], "model": model,
            "choices": [{"message": {"content": '{"score": 7}'}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        })
    return handler


@pytest.mark.asyncio
async def test_judge_rejects_substitutes_without_stream(monkeypatch) -> None:
    """Судья (батч) проверку id не проходил вовсе: 304 вердикта от подмен за неделю.

    id — с пробы 19.10: gemini-3.6 `msg_<hex>`, gpt-5.6-sol `chatcmpl-msg_01…`.
    """
    from app.services.llm import client as llm_client

    seen: list[str] = []
    _mount(monkeypatch, _plain_handler(seen, {
        "google/gemini-3.6-flash": "msg_7b6584027c8be489d2e59371",
        "openai/gpt-5.6-sol": "chatcmpl-msg_01LKs767Upt1k8w5SYBtZ94XQf",
        "openai/gpt-5.6-luna": "chatcmpl-1792414895091029348",
    }))
    monkeypatch.setenv(
        "LLM_JUDGE_MODELS",
        "google/gemini-3.6-flash,openai/gpt-5.6-sol,openai/gpt-5.6-luna",
    )
    result = await llm_client.complete(MSGS, purpose="code_review")
    assert seen == ["google/gemini-3.6-flash", "openai/gpt-5.6-sol", "openai/gpt-5.6-luna"]
    assert result.model == "openai/gpt-5.6-luna"


@pytest.mark.asyncio
async def test_judge_accepts_native_gemini_id(monkeypatch) -> None:
    from app.services.llm import client as llm_client

    seen: list[str] = []
    _mount(monkeypatch, _plain_handler(seen, {
        "google/gemini-3.7-flash": "NBTWar_uGYewjuMPidrguA4",
    }))
    monkeypatch.setenv("LLM_JUDGE_MODELS", "google/gemini-3.7-flash")
    result = await llm_client.complete(MSGS, purpose="code_review")
    assert result.model == "google/gemini-3.7-flash"


@pytest.mark.asyncio
async def test_failed_turn_is_journaled_in_session(db):
    """Сессии 232/235/240/244/246: ход без ответа не оставлял в `meta` ничего."""
    sid, uid = await _bare_session(db)
    try:
        await session_service.note_turn(db, sid, model="google/gemini-3.7-flash")
        attempts = [{"model": "google/gemini-3.7-flash", "outcome": "LLMTimeout",
                     "ms": 12011, "message": "таймаут"}]
        for turn in range(2, 2 + session_service.FAILURES_KEEP + 2):
            await session_service.note_failure(
                db, sid, turn=turn, error="LLMTimeout: таймаут", attempts=attempts,
            )
        await db.commit()

        meta = await _meta(db, sid)
        assert meta["last_model"] == "google/gemini-3.7-flash"  # прежнее не затёрто
        assert meta["failed_turns"] == session_service.FAILURES_KEEP + 2
        assert len(meta["failures"]) == session_service.FAILURES_KEEP
        assert meta["failures"][-1]["turn"] == session_service.FAILURES_KEEP + 3
        assert meta["failures"][0]["attempts"][0]["outcome"] == "LLMTimeout"
    finally:
        await _drop_session(db, sid, uid)


@pytest.mark.asyncio
async def test_failure_on_json_null_meta(db):
    """JSON-null в jsonb — не SQL NULL: запись не должна на нём падать."""
    from sqlalchemy import text

    sid, uid = await _bare_session(db)
    try:
        await db.execute(text("UPDATE ai_tutor_session SET meta = 'null'::jsonb WHERE id = :sid"),
                         {"sid": sid})
        await session_service.note_failure(db, sid, turn=1, error="x")
        await db.commit()
        assert (await _meta(db, sid))["failed_turns"] == 1
    finally:
        await _drop_session(db, sid, uid)
