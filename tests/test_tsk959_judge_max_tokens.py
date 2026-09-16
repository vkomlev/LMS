"""tsk-959: потолок ответа судьи и стенд, который различает, чем ответ пуст.

Думающая модель (`z-ai/glm-5.3` через маршрут провайдера) кладёт рассуждение
в тот же `max_tokens`, что и ответ: при потолке 700 приходил HTTP 200 с
`finish_reason=length` и пустым `content` — учёт расхода писал `ok`, а вердикта
не было. Проверяется: (1) все три судьи ходят к модели с одним общим потолком
`JUDGE_MAX_TOKENS`, достаточным для рассуждения; (2) стенд `--judge` называет
причину пустого ответа и выносит причины в строку вердикта «НЕСТАБИЛЕН».
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import llm_model_bakeoff as bakeoff  # noqa: E402

from app.services.llm import JUDGE_MAX_TOKENS  # noqa: E402


def test_judge_max_tokens_covers_reasoning() -> None:
    """Замер tsk-959: рассуждение 2037 + вердикт ~300 токенов. Потолок ниже —
    снова пустые ответы при `ok` в учёте."""
    assert JUDGE_MAX_TOKENS >= 2400


@pytest.mark.parametrize(
    "service",
    ["code_review_service", "rubric_review_service", "text_authorship_service"],
)
def test_all_judges_use_shared_cap(service: str) -> None:
    """У трёх осей судьи одна причина и один потолок: свои цифры (700/900/500)
    разъезжались бы молча, и следующая думающая модель попалась бы на той
    оси, где потолок забыли поднять."""
    src = (ROOT / "app" / "services" / f"{service}.py").read_text(encoding="utf-8")
    assert "max_tokens=JUDGE_MAX_TOKENS," in src
    assert "max_tokens=700," not in src and "max_tokens=900," not in src and "max_tokens=500," not in src


def test_stand_mirrors_live_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Стенд обязан мерить с боевым потолком, а не со своим: с 1024 при боевых
    700 он выпускал модель, которую бой резал по длине."""
    monkeypatch.setattr(bakeoff, "JUDGE_MAX_TOKENS_LIVE", 1024)
    monkeypatch.setattr(bakeoff, "JUDGE_BUDGET_SEC", 60.0)
    monkeypatch.setattr(bakeoff, "dotenv_values", lambda *a, **k: {})
    bakeoff._judge_prompt()
    assert bakeoff.JUDGE_MAX_TOKENS_LIVE == JUDGE_MAX_TOKENS


def test_empty_reason_names_reasoning_cap() -> None:
    choice = {"finish_reason": "length", "message": {"content": ""}}
    usage = {"completion_tokens": 700, "completion_tokens_details": {"reasoning_tokens": 700}}
    why = bakeoff._empty_reason(choice, usage)
    assert "рассуждением" in why and "reasoning_tokens=700" in why


def test_empty_reason_plain_silence() -> None:
    assert bakeoff._empty_reason({"finish_reason": "stop"}, {}) == "пустой ответ без ошибки"


def test_unstable_verdict_lists_causes() -> None:
    """«1 из 3» без причин заставлял лезть в сырые ответы руками."""
    runs = [
        {"model": "m", "total": 12.0, "tokens_in": 1, "tokens_out": 1, "format_ok": True,
         "text": '{"code_quality": {"score": 8}}', "error": None},
        {"model": "m", "total": 15.0, "tokens_in": 1, "tokens_out": 700, "format_ok": False,
         "text": "", "error": "потолок 700 съеден рассуждением (reasoning_tokens=700), текста нет"},
        {"model": "m", "total": 26.0, "tokens_in": 1, "tokens_out": 700, "format_ok": False,
         "text": "", "error": "потолок 700 съеден рассуждением (reasoning_tokens=700), текста нет"},
    ]
    verdict, why = bakeoff.judge_verdict(bakeoff.judge_aggregate(runs))
    assert verdict == "НЕСТАБИЛЕН"
    assert "1 из 3" in why and "съеден рассуждением" in why
    # Одинаковые причины схлопываются — строка вердикта не растёт с числом прогонов.
    assert why.count("съеден рассуждением") == 1


# ---------------------------------------------------------------------------
# Заглушка провайдера текстом ответа: "[req_…] [glm-5.3] **Bad request from AI
# provider**" при HTTP 200 без поля `error`. Клиент обязан взять следующую
# модель, а не отдать это как ответ.
# ---------------------------------------------------------------------------
import json  # noqa: E402

import httpx  # noqa: E402

from app.services.llm import LLMMessage, client as llm_client  # noqa: E402
from app.services.llm import cooldown, usage  # noqa: E402

STUB = ("[req_35e09394] [glm-5.3]\n**Bad request from AI provider**\n"
        "- Your request was rejected by the AI provider (invalid parameters or unsupported content).\n"
        "How to fix\n- Check the request parameters.\n- This request still counts as a request "
        "and is billed based on its input (minimum 1,000 prompt / 1,000 completion / 1,000 cached tokens).")
SSE_SEP = chr(10) * 2


@pytest.fixture
def _llm_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOSEROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CLOSEROUTER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_TUTOR_MODELS", "tutor-stub,tutor-ok")
    monkeypatch.setenv("LLM_JUDGE_MODELS", "judge-stub,judge-ok")
    cooldown.reset()
    written: list = []

    async def _fake_record(event):
        written.append(event)

    monkeypatch.setattr(usage, "record", _fake_record)
    monkeypatch.setattr(llm_client.usage, "record", _fake_record)
    yield written
    cooldown.reset()


def _mount(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)


def _model_of(request: httpx.Request) -> str:
    return json.loads(request.content.decode())["model"]


def _batch(text: str) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1508, "completion_tokens": 1000},
    })


def _sse(chunks: list[str]) -> httpx.Response:
    frames = "".join(
        "data: " + json.dumps({"choices": [{"delta": {"content": c}}]}) + SSE_SEP for c in chunks
    )
    return httpx.Response(200, content=frames.encode(), headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_batch_stub_falls_to_next_model(monkeypatch: pytest.MonkeyPatch, _llm_env) -> None:
    """Судья: заглушка → `LLMUpstreamError` в учёте и вердикт от следующей модели
    в ТОМ ЖЕ вызове, а не `ok` с мусором и повтор через тик."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _batch(STUB if _model_of(request) == "judge-stub" else '{"code_quality": {"score": 8}}')

    _mount(monkeypatch, handler)
    res = await llm_client.complete([LLMMessage(role="user", content="оцени")], purpose="code_review")
    assert res.model == "judge-ok"
    outcomes = [(e.model, e.outcome) for e in _llm_env]
    assert ("judge-stub", "LLMUpstreamError") in outcomes
    assert ("judge-ok", "ok") in outcomes


@pytest.mark.asyncio
async def test_stream_stub_never_reaches_student(monkeypatch: pytest.MonkeyPatch, _llm_env) -> None:
    """Наставник: заглушка приходит кусками — ни один кусок не должен уйти
    ученику; ответ даёт следующая модель."""
    def handler(request: httpx.Request) -> httpx.Response:
        if _model_of(request) == "tutor-stub":
            # Режем заглушку на куски по 20 символов — как её льёт поток.
            return _sse([STUB[i:i + 20] for i in range(0, len(STUB), 20)])
        return _sse(["Давай ", "разберём ", "по шагам."])

    _mount(monkeypatch, handler)
    got = []
    async for chunk in llm_client.stream([LLMMessage(role="user", content="помоги")], purpose="ai_tutor"):
        if chunk.delta:
            got.append((chunk.model, chunk.delta))
    text = "".join(d for _, d in got)
    assert "Bad request" not in text and "[req_" not in text
    assert text == "Давай разберём по шагам."
    assert {m for m, _ in got} == {"tutor-ok"}


@pytest.mark.asyncio
async def test_stream_bracket_reply_not_withheld(monkeypatch: pytest.MonkeyPatch, _llm_env) -> None:
    """Реплика, начинающаяся со скобки, но не с `[req_`, проходит целиком и
    без задержки: сниффер отпускает её на первом же непохожем символе."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse(["[", "Подсказка] ", "смотри на цикл."])

    _mount(monkeypatch, handler)
    text = "".join(
        [c.delta async for c in llm_client.stream([LLMMessage(role="user", content="?")], purpose="ai_tutor")]
    )
    assert text == "[Подсказка] смотри на цикл."


@pytest.mark.asyncio
async def test_stream_short_stub_caught_at_finish(monkeypatch: pytest.MonkeyPatch, _llm_env) -> None:
    """Заглушка короче порога сниффера — поток кончился, решение на `finish()`."""
    short = "[req_1] [glm-5.3]\n**Bad request from AI provider**"

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse([short]) if _model_of(request) == "tutor-stub" else _sse(["ок"])

    _mount(monkeypatch, handler)
    text = "".join(
        [c.delta async for c in llm_client.stream([LLMMessage(role="user", content="?")], purpose="ai_tutor")]
    )
    assert text == "ок"


def test_stand_names_provider_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Стенд ходит к провайдеру напрямую, мимо клиента, — заглушку он обязан
    назвать своими словами, а не «JSONDecodeError на символе 1»."""
    import contextlib
    import io

    body = json.dumps({"choices": [{"message": {"content": STUB}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 1508, "completion_tokens": 1000}}).encode()

    @contextlib.contextmanager
    def fake_post(*a, **k):
        yield io.BytesIO(body)

    monkeypatch.setattr(bakeoff, "_post", fake_post)
    r = bakeoff.judge_probe("m", "sys", "user", "https://p.test", "k")
    assert r["error"] and r["error"].startswith("заглушка провайдера")
    assert r["text"] == "" and r["tokens_out"] == 1000
