"""tsk-573: стенд LLM меряет боевые цепочки и различает, чем пуст ответ наставника.

Прогон 2026-10-01 нашёл два дефекта стенда: (1) без `--models` он мерил зашитый
августовский список, которого в цепочках давно нет; (2) наставницкая ось слала
`max_tokens=700` при боевых 900, и думающие модели, потратив лимит на
`reasoning_content`, попадали в «пустой ответ без ошибки в потоке».
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import llm_model_bakeoff as bakeoff  # noqa: E402

from app.services.llm import TUTOR_MAX_TOKENS, providers  # noqa: E402


@pytest.fixture
def _no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Локальный `.env` не должен подменять цепочку в тесте."""
    monkeypatch.setattr(bakeoff, "dotenv_values", lambda *a, **k: {})


def test_default_candidates_are_live_chains(monkeypatch: pytest.MonkeyPatch, _no_env_file) -> None:
    monkeypatch.delenv("LLM_TUTOR_MODELS", raising=False)
    monkeypatch.delenv("LLM_JUDGE_MODELS", raising=False)
    tutor = bakeoff.default_candidates(judge=False)
    judge = bakeoff.default_candidates(judge=True)
    assert tutor == [m.strip() for m in providers._DEFAULT_TUTOR_MODELS.split(",")]
    assert judge == [m.strip() for m in providers._DEFAULT_JUDGE_MODELS.split(",")]
    assert "x-ai/grok-4.1-fast" not in tutor + judge


def test_default_candidates_follow_env_override(monkeypatch: pytest.MonkeyPatch, _no_env_file) -> None:
    """Переменная окружения главнее цепочки в коде — как у рантайма."""
    monkeypatch.setenv("LLM_TUTOR_MODELS", "a/one, b/two")
    monkeypatch.setenv("LLM_JUDGE_MODELS", "c/three")
    assert bakeoff.default_candidates(judge=False) == ["a/one", "b/two"]
    assert bakeoff.default_candidates(judge=True) == ["c/three"]


def test_tutor_axis_has_no_stale_list() -> None:
    assert not hasattr(bakeoff, "DEFAULT_CANDIDATES")


def test_tutor_cap_shared_with_live() -> None:
    """Бой и стенд берут потолок из одной константы, число не дублируется."""
    assert bakeoff._tutor_max_tokens() == TUTOR_MAX_TOKENS
    src = (ROOT / "app" / "api" / "v1" / "ai_tutor.py").read_text(encoding="utf-8")
    assert "max_tokens=TUTOR_MAX_TOKENS" in src
    stand = (ROOT / "scripts" / "llm_model_bakeoff.py").read_text(encoding="utf-8")
    assert '"max_tokens": 700' not in stand and '"max_tokens": 900' not in stand


def _sse(frames: list[dict]):
    body = "".join("data: " + json.dumps(f) + "\n\n" for f in frames) + "data: [DONE]\n\n"

    @contextlib.contextmanager
    def fake_post(base, key, path, payload, timeout):
        fake_post.payload = payload
        yield io.BytesIO(body.encode())

    return fake_post


def test_probe_sends_live_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _sse([{"choices": [{"delta": {"content": "Давай разберём"}, "finish_reason": "stop"}]}])
    monkeypatch.setattr(bakeoff, "_post", fake)
    r = bakeoff.probe("m", "sys", "https://p.test", "k")
    assert fake.payload["max_tokens"] == TUTOR_MAX_TOKENS
    assert r["error"] is None and r["text"] == "Давай разберём"


def test_probe_reasoning_ate_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """`glm-5.3` 01.10: рассуждение в `reasoning_content`, `finish_reason=length`."""
    monkeypatch.setattr(bakeoff, "_post", _sse([
        {"choices": [{"delta": {"reasoning_content": "Ученик просит готовый код..."}}]},
        {"choices": [{"delta": {"reasoning_content": " надо дать подсказку"}}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
    ]))
    r = bakeoff.probe("z-ai/glm-5.3", "sys", "https://p.test", "k")
    assert r["text"] == ""
    assert "съеден рассуждением" in r["error"] and str(TUTOR_MAX_TOKENS) in r["error"]


def test_probe_plain_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bakeoff, "_post", _sse([
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]))
    r = bakeoff.probe("m", "sys", "https://p.test", "k")
    assert r["error"] == "пустой ответ без ошибки в потоке"


@pytest.mark.parametrize("finish, reasoning, expected", [
    ("length", 120, "съеден рассуждением"),
    ("length", 0, "исчерпан"),
    ("stop", 120, "пустой ответ без ошибки в потоке"),
    (None, 0, "пустой ответ без ошибки в потоке"),
])
def test_tutor_empty_reason(finish: str | None, reasoning: int, expected: str) -> None:
    assert expected in bakeoff._tutor_empty_reason(finish, reasoning, 900)


def test_tutor_unstable_verdict_lists_causes() -> None:
    runs = [
        {"model": "m", "error": None, "text": "ок", "leak": False, "first": 1.0, "chunks": 30},
        {"model": "m", "error": "потолок 900 съеден рассуждением (reasoning 3000 симв.), текста нет",
         "text": "", "leak": None, "first": None, "chunks": 0},
    ]
    v, why = bakeoff.verdict(bakeoff.aggregate(runs))
    assert v == "НЕСТАБИЛЕН" and "1 из 2" in why and "съеден рассуждением" in why
