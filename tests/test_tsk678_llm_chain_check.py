"""tsk-678: еженедельная проверка боевых цепочек моделей.

Провайдер подменяется транспортом httpx — сеть не трогаем. Проверяется главное:
проход отличает «жива» от «годна судить» (на этом попался `claude-haiku-4.5`:
отвечал быстро, а балла чистоты в ответе не было), задвигает отказавшую модель
в конец очереди и НЕ меняет состав цепочки.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.services import llm_chain_check_cron_service as chain_check
from app.services.llm import client as llm_client
from app.services.llm import cooldown, providers, usage

GOOD_VERDICT = {
    "language": "Python",
    "code_quality": {"score": 8, "notes": ["строка 5: имя переменной ни о чём не говорит"]},
    "ai_authorship": {"verdict": "student_likely", "reasoning": "транслит в комментарии"},
}
# Ровно то, чем нас подвёл haiku: валидный JSON, в котором балла нет.
NO_SCORE_VERDICT = {
    "language": "Python",
    "code_quality": {"notes": ["нормально"]},
    "ai_authorship": {"verdict": "ambiguous", "reasoning": "коротко"},
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Свой ключ, чистое остывание, учёт расхода в память (без БД)."""
    monkeypatch.setenv("CLOSEROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CLOSEROUTER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_TUTOR_MODELS", "tutor-a,tutor-b")
    monkeypatch.setenv("LLM_JUDGE_MODELS", "judge-a,judge-b,judge-c")
    monkeypatch.setenv("LLM_CHAIN_CHECK_INTERVAL_HOURS", "168")
    cooldown.reset()

    written: list = []

    async def _fake_record(event):
        written.append(event)

    monkeypatch.setattr(usage, "record", _fake_record)
    monkeypatch.setattr(llm_client.usage, "record", _fake_record)
    yield written
    cooldown.reset()


def _mount(monkeypatch, handler):
    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)


def _answer(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 120},
    })


def _by_model(request: httpx.Request) -> str:
    return json.loads(request.content.decode())["model"]


@pytest.mark.asyncio
async def test_alive_but_useless_judge_is_demoted(monkeypatch):
    """«Жива» и «годна судить» — разное, и проход обязан их различать.

    `claude-haiku-4.5` стоял вторым в судейской цепочке и считался запасом:
    дешёвая проверка доступности его бы пропустила. На боевом промпте он вернул
    JSON БЕЗ балла чистоты 3 раза из 3 — то есть запаса не было вовсе.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        model = _by_model(request)
        return _answer(NO_SCORE_VERDICT if model == "judge-b" else GOOD_VERDICT)

    _mount(monkeypatch, handler)

    summary = await chain_check.llm_chain_check_tick()

    assert summary["judge_ok"] == 2 and summary["judge_bad"] == 1
    assert cooldown.is_cooling("model:judge-b"), "негодный судья обязан уйти в конец очереди"
    assert not cooldown.is_cooling("model:judge-a")


@pytest.mark.asyncio
async def test_check_never_changes_chain_composition(monkeypatch):
    """Проход меняет ПОРЯДОК, а не состав: состав утверждает стенд с оператором.

    «Живая» и «быстрая» ещё не значит «годная» — 25.08 две модели отвечали
    быстро и слили эталон 3 раза из 3. Поэтому подставлять и выбрасывать модели
    автоматически нельзя, даже когда очень хочется.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "no_available_provider"}})

    _mount(monkeypatch, handler)
    before = providers.judge_models()

    await chain_check.llm_chain_check_tick()

    assert providers.judge_models() == before
    # Задвинуты все — значит порядок сохраняется, и разбор всё равно состоится:
    # остаться без судьи хуже, чем сходить к сомнительной модели.
    assert llm_client._rotate_by_cooldown(providers.judge_models()) == before


@pytest.mark.asyncio
async def test_dead_head_raises_alert(monkeypatch):
    """Мёртвая ПЕРВАЯ модель — отдельный сигнал: в неё упирается каждый разбор."""
    def handler(request: httpx.Request) -> httpx.Response:
        if _by_model(request) == "judge-a":
            return httpx.Response(503, json={"error": {"message": "no_available_provider"}})
        return _answer(GOOD_VERDICT)

    _mount(monkeypatch, handler)

    summary = await chain_check.llm_chain_check_tick()

    assert any("первая модель судьи" in a for a in summary["alerts"])
    assert cooldown.is_cooling("model:judge-a")


@pytest.mark.asyncio
async def test_thin_bench_raises_alert_even_when_head_is_fine(monkeypatch):
    """Один годный судья — это не запас, а последний рубеж.

    Мёртвая четвёрка 25.08 начиналась ровно с такого состояния: голова отвечает,
    и потому никто не смотрит, что за ней пусто.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if _by_model(request) == "judge-a":
            return _answer(GOOD_VERDICT)
        return httpx.Response(503, json={"error": {"message": "no_available_provider"}})

    _mount(monkeypatch, handler)

    summary = await chain_check.llm_chain_check_tick()

    assert summary["judge_ok"] == 1
    assert any("годных судей осталось" in a for a in summary["alerts"])


@pytest.mark.asyncio
async def test_check_spend_is_visible_in_usage_under_own_purpose(monkeypatch, _isolate):
    """Расход прохода виден на пульте отдельным назначением, а не тишиной.

    Отдельного экрана у проверки нет намеренно: замедление судьи заметили именно
    на пульте расхода, туда же проход и пишет — мёртвая модель будет видна
    еженедельной строкой с ошибкой.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return _answer(GOOD_VERDICT)

    _mount(monkeypatch, handler)

    await chain_check.llm_chain_check_tick()

    purposes = {e.purpose for e in _isolate}
    assert purposes == {chain_check.CHECK_PURPOSE}
    # Три судьи + два наставника: каждая модель обеих цепочек проверена ровно раз.
    assert len(_isolate) == 5


@pytest.mark.asyncio
async def test_slow_judge_is_demoted_even_when_answer_is_good(monkeypatch):
    """Модель, не уложившаяся в бюджет попытки, негодна при любом качестве ответа.

    На бою у неё ещё и очередь работ сверху — там она не уложится тем более.
    """
    monkeypatch.setattr(chain_check.Budget.BATCH.__class__, "attempt_timeout",
                        property(lambda self: 0.0))

    def handler(request: httpx.Request) -> httpx.Response:
        return _answer(GOOD_VERDICT)

    _mount(monkeypatch, handler)

    summary = await chain_check.llm_chain_check_tick()

    assert summary["judge_bad"] == 3
    assert cooldown.is_cooling("model:judge-a")


@pytest.mark.asyncio
async def test_provider_cooldown_postpones_pass_instead_of_condemning_chain(monkeypatch):
    """Остывание провайдера после 429 — не приговор моделям.

    Оно про ПРОВАЙДЕРА целиком, и если считать его отказом модели, проход
    задвинет всю цепочку и поднимет тревогу на ровном месте. Плюс повтор на 429
    кормит брейкер, который у нас уже срабатывал (2026-07-05), — значит проход
    обязан отложиться, а не долбить.
    """
    cooldown.start("closerouter", 120)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("при остывании провайдера сеть трогать нельзя")

    _mount(monkeypatch, handler)

    summary = await chain_check.llm_chain_check_tick()

    assert summary.get("skipped") is True
    assert summary["alerts"] == []
    assert not cooldown.is_cooling("model:judge-a"), "цепочку задвигать не за что"
