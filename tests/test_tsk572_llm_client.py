"""tsk-572 этап 1: общий LLM-транспорт.

Контракт: docs/specs/2026-08-06-contract-llm-client.md.

Провайдер подменяется транспортом httpx — сеть не трогаем: стенд по живым
моделям это отдельный инструмент (`scripts/llm_model_bakeoff.py`), а здесь
проверяется поведение клиента, включая те случаи, которые живой прогон
воспроизвести по команде не может (429, обрыв на середине потока, квота).

Главная проверка — §5: провайдер умеет отдать HTTP 200 и ошибку ВНУТРИ потока.
Клиент, который смотрит только на статус, покажет ученику пустой ответ как
нормальный. Это молчаливый отказ: ни исключения, ни лога, ни красного теста.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.services.llm import client as llm_client
from app.services.llm import cooldown, providers, usage
from app.services.llm.contracts import (
    Budget,
    LLMConfigError,
    LLMCooldown,
    LLMMessage,
    LLMQuotaExceeded,
    LLMRateLimited,
    LLMUpstreamError,
)

SSE_SEP = chr(10) * 2

MSGS = [LLMMessage(role="user", content="Как работает range?")]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Свой ключ, чистое остывание, учёт расхода в память (без БД)."""
    monkeypatch.setenv("CLOSEROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CLOSEROUTER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a,model-b")
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a,model-b")
    monkeypatch.setenv("LLM_COOLDOWN_SECONDS", "120")
    cooldown.reset()

    written: list = []

    async def _fake_record(event):
        written.append(event)

    monkeypatch.setattr(usage, "record", _fake_record)
    monkeypatch.setattr(llm_client.usage, "record", _fake_record)
    yield written
    cooldown.reset()


def _mount(monkeypatch, handler):
    """Подменить транспорт httpx.AsyncClient на обработчик-заглушку."""
    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)


def _sse(*frames: dict) -> bytes:
    return "".join(f"data: {json.dumps(f, ensure_ascii=False)}\n\n" for f in frames).encode()


def _ok_batch(text: str = "готово") -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


# ───────────────────────── Нормализация адреса ──────────────────────────────


@pytest.mark.parametrize("raw, expect", [
    ("https://api.closerouter.dev", "https://api.closerouter.dev"),
    ("https://api.closerouter.dev/", "https://api.closerouter.dev"),
    ("https://api.closerouter.dev/v1", "https://api.closerouter.dev"),
    ("https://api.closerouter.dev/v1/", "https://api.closerouter.dev"),
])
def test_base_url_normalized_both_spellings(raw, expect):
    """Оператор естественно пишет адрес с `/v1` — без нормализации выйдет `/v1/v1`.

    Гоча найдена живьём чипом tsk-302, поэтому лечится кодом, а не инструкцией.
    """
    assert providers.normalize_base_url(raw) == expect


def test_chat_url_has_single_v1(monkeypatch):
    monkeypatch.setenv("CLOSEROUTER_BASE_URL", "https://provider.test/v1/")
    assert providers.resolve_provider().chat_url == "https://provider.test/v1/chat/completions"


# ──────────────── Ошибка внутри HTTP 200 — главная ловушка ──────────────────


@pytest.mark.asyncio
async def test_stream_raises_on_error_inside_successful_response(monkeypatch):
    """HTTP 200 + `{"error": ...}` в потоке обязан стать исключением.

    Иначе ученик получает пустой ответ, неотличимый от нормального короткого.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse({
            "error": {"code": "upstream_error", "message": "Model not found", "status": 502}
        }), headers={"Content-Type": "text/event-stream"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a")

    with pytest.raises(LLMUpstreamError):
        async for _ in llm_client.stream(MSGS, purpose="tutor"):
            pass


@pytest.mark.asyncio
async def test_quota_error_is_separate_class(monkeypatch):
    """Отбой по квоте КЛЮЧА — отдельный класс: это деньги, а не поломка модели."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse({
            "error": {"message": "pre-consume quota failed, user remaing quota: $0.00045",
                      "status": 502}
        }), headers={"Content-Type": "text/event-stream"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a")

    with pytest.raises(LLMQuotaExceeded) as exc:
        async for _ in llm_client.stream(MSGS, purpose="tutor"):
            pass
    assert exc.value.alert_staff is True, "по квоте нужно звать персонал, а не ученика"


@pytest.mark.asyncio
async def test_batch_raises_on_error_inside_200(monkeypatch):
    """Та же ловушка на батч-пути (tsk-302)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"message": "upstream boom", "status": 502}})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a")

    with pytest.raises(LLMUpstreamError):
        await llm_client.complete(MSGS, purpose="code_review")


# ───────────────────────── 429 и остывание ──────────────────────────────────


@pytest.mark.asyncio
async def test_429_never_retried_and_starts_cooldown(monkeypatch):
    """Повтор на 429 кормит брейкер провайдера (инцидент 2026-07-05).

    Проверяем ровно два факта: запрос ушёл РОВНО один раз, и провайдер остывает.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "slow down"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a")

    with pytest.raises(LLMRateLimited):
        await llm_client.complete(MSGS, purpose="code_review")

    assert calls["n"] == 1, f"429 повторён {calls['n']} раз — брейкер провайдера кормится"
    assert cooldown.is_cooling(providers.PROVIDER_NAME)


@pytest.mark.asyncio
async def test_cooldown_refuses_without_network_call(monkeypatch):
    """Пока провайдер остывает — отказ мгновенный, сетевого вызова быть не должно."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_ok_batch())

    _mount(monkeypatch, handler)
    cooldown.start(providers.PROVIDER_NAME, 60)

    with pytest.raises(LLMCooldown):
        await llm_client.complete(MSGS, purpose="code_review")
    assert calls["n"] == 0, "во время остывания клиент всё-таки полез в сеть"


@pytest.mark.asyncio
async def test_429_does_not_walk_the_model_chain(monkeypatch):
    """429 — свойство ПРОВАЙДЕРА, а не модели: перебирать цепочку бессмысленно."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "slow down"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a,model-b,model-c")

    with pytest.raises(LLMRateLimited):
        await llm_client.complete(MSGS, purpose="code_review")
    assert calls["n"] == 1, "цепочка моделей перебиралась на 429 — лишняя нагрузка"


# ─────────────────────────── Цепочка моделей ────────────────────────────────


async def test_chain_falls_through_to_working_model(monkeypatch):
    """Модель может быть В КАТАЛОГЕ и при этом недоступна у upstream.

    Без цепочки любая такая модель кладёт наставника целиком (живой факт стенда:
    `z-ai/glm-5.2` → «Model not found»).
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "model-a":
            return httpx.Response(200, json={"error": {"message": "Model not found", "status": 502}})
        return httpx.Response(200, json=_ok_batch("ответ второй модели"))

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a,model-b")

    res = await llm_client.complete(MSGS, purpose="code_review")
    assert res.text == "ответ второй модели"
    assert seen == ["model-a", "model-b"]


async def test_http_503_about_one_model_walks_the_chain(monkeypatch):
    """HTTP 503 «нет доступного upstream для ЭТОЙ модели» — повод взять следующую.

    tsk-666, боевой случай. Провайдер отдаёт именно так:
    `{"error":{"code":"no_available_provider","status":503,
      "metadata":{"requested_models":["x-ai/grok-4.1-fast"]}}}`.
    Ошибка про КОНКРЕТНУЮ модель, остальные три в цепочке живы — но
    `LLMUnavailable` не разрешала переход, и наставник замолкал целиком.

    Так оборвались оба последних живых разговора контура (22.08 и 24.08) и
    разговор Шестаева 12.08: ученик писал реплику и получал «сбой на нашей
    стороне» при трёх работающих запасных моделях.

    429 при этом по-прежнему цепочку НЕ перебирает — там остывает провайдер
    целиком (`test_429_does_not_walk_the_model_chain`).
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "model-a":
            return httpx.Response(503, json={"error": {
                "code": "no_available_provider",
                "message": "No available upstream endpoint for requested model(s)",
                "status": 503,
                "metadata": {"endpoint_type": "chat", "requested_models": [model]},
            }})
        return httpx.Response(200, json=_ok_batch("ответ запасной модели"))

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a,model-b")

    res = await llm_client.complete(MSGS, purpose="code_review")
    assert res.text == "ответ запасной модели"
    assert seen == ["model-a", "model-b"], (
        "503 про одну модель положил весь вызов, хотя запасные в цепочке живы"
    )


@pytest.mark.asyncio
async def test_silent_upstream_is_bounded_and_falls_through(monkeypatch):
    """Молчащий upstream ограничен по времени и не запирает ученика (tsk-671).

    Провайдер отдаёт HTTP 200 и не присылает НИ ОДНОГО куска. Прежняя проверка
    первого токена стояла ВНУТРИ обработки куска, поэтому при полной тишине не
    выполнялась ни разу: цикл чтения не делал ни одной итерации. Живой случай —
    ученик две минуты смотрел в «Наставник думает…» с заблокированным полем, а в
    учёте расхода не появилось ни одной записи (tsk-666, живая проверка).

    Проверяем не «фолбэк починен», а главное: в любом сбое ученик получает
    быстрый честный ответ, а не вечное ожидание.
    """
    seen: list[str] = []

    class _Silent(httpx.AsyncByteStream):
        """Соединение открыто, данных нет — ровно то, что делал провайдер."""

        async def __aiter__(self):
            await asyncio.sleep(30)
            yield b""

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "model-a":
            return httpx.Response(200, stream=_Silent(),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=(
                "data: " + json.dumps({"choices": [{"delta": {"content": "ответ живой модели"}}]}) + SSE_SEP
                + "data: [DONE]" + SSE_SEP
            ).encode("utf-8"),
        )

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a,model-b")

    loop = asyncio.get_running_loop()
    started = loop.time()
    text = ""
    async for chunk in llm_client.stream(MSGS, purpose="tutor",
                                         budget=llm_client.Budget.INTERACTIVE):
        if chunk.done:
            break
        text += chunk.delta
    spent = loop.time() - started

    assert text == "ответ живой модели", "молчащая модель заперла весь вызов"
    assert seen == ["model-a", "model-b"]
    assert spent < 25, f"ждали {spent:.0f} c — предел первого куска не сработал"


@pytest.mark.asyncio
async def test_hang_before_headers_is_bounded_by_call_budget(monkeypatch):
    """Молчание ДО заголовков ответа тоже ограничено (tsk-671).

    Разбор зависания на бою: предел первого куска покрывает уже открытый поток,
    а висеть можно раньше — на ожидании заголовков, куда он не достаёт. Проверка
    между моделями там тоже не помогает: она срабатывает МЕЖДУ попытками, а
    попытка не кончается.

    Ученику в этот момент видно «Наставник думает…» с заблокированным полем и без
    выхода. Поэтому проверяем не «взяли следующую модель», а «вызов кончился
    вовремя»: любой сбой обязан превратиться в понятный отказ, а не в ожидание.
    """
    async def never_answers(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(600)          # заголовки не приходят никогда
        return httpx.Response(200)

    _mount(monkeypatch, never_answers)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a,model-b,model-c,model-d")

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(llm_client.LLMError):
        async for _ in llm_client.stream(MSGS, purpose="tutor",
                                         budget=llm_client.Budget.INTERACTIVE):
            pass
    spent = loop.time() - started

    limit = llm_client.Budget.INTERACTIVE.total_timeout + 5
    assert spent < limit, (
        f"ждали {spent:.0f} c при бюджете "
        f"{llm_client.Budget.INTERACTIVE.total_timeout:.0f} c — ученик заперт"
    )


@pytest.mark.asyncio
async def test_explicit_model_overrides_chain(monkeypatch):
    """Явная модель отменяет цепочку — вызывающий знает, что делает (стенд, калибровка)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json=_ok_batch())

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a,model-b")

    await llm_client.complete(MSGS, model="точная/модель", purpose="code_review")
    assert seen == ["точная/модель"]


# ──────────────────────────── Стриминг ──────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_yields_deltas_then_final_chunk(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "Пред"}}]},
            {"choices": [{"delta": {"content": "ставь"}}]},
            {"choices": [{"delta": {"content": " турникет"}}],
             "usage": {"prompt_tokens": 9, "completion_tokens": 4}},
        ), headers={"Content-Type": "text/event-stream"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a")

    chunks = [c async for c in llm_client.stream(MSGS, purpose="tutor")]
    assert "".join(c.delta for c in chunks) == "Представь турникет"
    assert chunks[-1].done is True and chunks[-1].truncated is False
    assert chunks[-1].tokens_out == 4


@pytest.mark.asyncio
async def test_break_after_first_chunk_is_truncation_not_error(monkeypatch):
    """Обрыв ПОСЛЕ начала ответа не должен стирать написанное.

    Ученик уже читает фразу; поднять исключение — значит убрать её с экрана и
    показать ошибку. Контракт §4.2: отдаём `truncated=True`, потребитель
    предлагает продолжить.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "Начало ответа"}}]},
            {"error": {"message": "upstream died", "status": 502}},
        ), headers={"Content-Type": "text/event-stream"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a,model-b")

    chunks = [c async for c in llm_client.stream(MSGS, purpose="tutor")]
    assert chunks[0].delta == "Начало ответа"
    assert chunks[-1].done is True
    assert chunks[-1].truncated is True, "обрыв выдан как ошибка — текст ученика стёрт"


@pytest.mark.asyncio
async def test_stream_does_not_switch_model_midphrase(monkeypatch):
    """Начав отдавать текст, менять модель нельзя — получится склейка двух ответов."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "кусок"}}]},
            {"error": {"message": "Model not found", "status": 502}},
        ), headers={"Content-Type": "text/event-stream"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a,model-b")

    chunks = [c async for c in llm_client.stream(MSGS, purpose="tutor")]
    assert seen == ["model-a"], f"после начала потока сходили ещё в {seen[1:]}"
    assert chunks[-1].truncated is True


# ───────────────────── Конфигурация и признак повторяемости ─────────────────


@pytest.mark.asyncio
async def test_missing_key_is_config_error_not_network(monkeypatch):
    """Без ключа — ошибка настройки и алерт персоналу, а не «сервис недоступен»."""
    monkeypatch.delenv("CLOSEROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CB_CLAUDE_API_KEY", raising=False)

    with pytest.raises(LLMConfigError) as exc:
        await llm_client.complete(MSGS, purpose="code_review")
    assert exc.value.alert_staff is True


def test_retryable_flags_match_contract():
    """Признак `retryable` — просьба чипа tsk-302 (§12.1): фоновая очередь должна
    отличать «повторить потом» от «повторять бесполезно», не выводя это заново."""
    from app.services.llm import contracts as c

    assert c.LLMCooldown.retryable and c.LLMRateLimited.retryable
    assert c.LLMTimeout.retryable and c.LLMUnavailable.retryable
    assert not c.LLMConfigError.retryable
    assert not c.LLMMalformed.retryable
    assert not c.LLMUpstreamError.retryable


@pytest.mark.asyncio
async def test_seed_reaches_provider(monkeypatch):
    """`seed` нужен калибровке рубрики tsk-302: расхождение вердиктов должно
    означать правку рубрики, а не дрожание модели."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_ok_batch())

    _mount(monkeypatch, handler)
    await llm_client.complete(MSGS, model="m", purpose="code_review", seed=42)
    assert seen.get("seed") == 42
    assert seen.get("temperature") == 0.0
    assert seen.get("stream") is False


@pytest.mark.asyncio
async def test_usage_recorded_for_failures_too(monkeypatch, _isolate):
    """Неуспешные вызовы тоже попадают в учёт: по ним видно, куда уходят деньги."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"message": "upstream boom", "status": 502}})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a")

    with pytest.raises(LLMUpstreamError):
        await llm_client.complete(MSGS, purpose="code_review")

    assert _isolate, "расход неуспешного вызова не записан"
    assert _isolate[-1].outcome == "LLMUpstreamError"
    assert _isolate[-1].purpose == "code_review"


@pytest.mark.asyncio
async def test_budgets_differ_by_profile():
    """Интерактив не повторяет таймаут: ученик не ждёт второй круг."""
    assert Budget.INTERACTIVE.timeout_retries == 0
    assert Budget.BATCH.timeout_retries == 1
    # 12 c, а не 5: стенд дал медиану первого токена 4.4-4.6 c, и бюджет,
    # равный медиане, обрывал бы примерно половину живых ответов. Проверено
    # регресс-прогоном — наставник отваливался посреди нормального разговора.
    assert Budget.INTERACTIVE.first_token_timeout == 12.0
    assert Budget.INTERACTIVE.first_token_timeout > 4.6 * 2, (
        "бюджет первого токена должен быть с запасом к медиане, а не равен ей"
    )
    assert Budget.BATCH.first_token_timeout is None
    assert Budget.INTERACTIVE.total_timeout < Budget.BATCH.total_timeout


def test_frame_split_across_network_chunks_is_not_lost():
    """Кадр, разорванный между чтениями, обязан доехать целиком.

    Найдено ЖИВОЙ проверкой, а не тестом: наставник ответил «что ужеовал
    сделать» вместо «что уже попробовал сделать» — кусок текста пропал. Разбор
    каждого сетевого чанка по отдельности выбрасывал незавершённый хвост, и
    потеря была МОЛЧАЛИВОЙ: ни ошибки, ни лога, просто чуть кривая фраза,
    которую замечаешь, только если читаешь глазами.
    """
    from app.services.llm.client import sse_payloads_from_chunks

    # Второй кадр разорван ровно посередине JSON — так и режет сеть.
    d = chr(10)
    frame1 = 'event: delta' + d + '{"text": "что уже "}'
    # Кадры собираем через chr(10), чтобы перенос не зависел от того,
    # как файл прошёл через оболочку при правке.
    f = lambda t: 'event: delta' + d + 'data: ' + t + d + d
    whole = f('{"text": "что уже "}') + f('{"text": "попробовал"}') + f('{"text": " сделать"}')
    # Режем поток посередине второго кадра — так и делает сеть.
    cut = whole.index('попроб') + 3
    pieces = [whole[:cut], whole[cut:]]
    carry: dict = {"buf": ""}
    text_out = ""
    for piece in pieces:
        for payload in sse_payloads_from_chunks([piece], carry):
            for choice in payload.get("choices") or []:
                text_out += (choice.get("delta") or {}).get("content") or ""
            if isinstance(payload.get("text"), str):
                text_out += payload["text"]

    assert text_out == "что уже попробовал сделать", (
        f"кусок потерялся на границе чтения: {text_out!r}"
    )
