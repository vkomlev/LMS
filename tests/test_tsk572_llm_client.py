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
    LLMTimeout,
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


# Тест `test_silent_upstream_is_bounded_and_falls_through` удалён 2026-08-25
# (tsk-671). Он проверял то же, что `test_silent_socket_is_bounded_by_read_timeout`
# выше, но через `httpx.MockTransport` — а мок не производит таймаутов, они живут
# в настоящем транспорте. Держался он лишь на самодельной обёртке `wait_for`,
# которая сама и оказалась причиной зависания на бою. Проверка молчащего
# собеседника осталась одна и идёт через живой сокет.


@pytest.mark.asyncio
async def test_failed_model_is_rotated_to_the_end(monkeypatch):
    """Отказавшая модель уходит в конец очереди — следующий ученик её обходит.

    Ротация (tsk-671). Смысл не в наказании модели: первый ученик упёрся в
    мёртвый маршрут и подождал, остальные не должны платить за тот же отказ
    снова и снова. На бою это разница между «наставник отвечает за 3 секунды»
    и «каждый ждёт 12 секунд таймаута».
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "model-dead":
            return httpx.Response(503, json={"error": {"code": "no_available_provider"}})
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=("data: " + json.dumps({"choices": [{"delta": {"content": "ок"}}]})
                     + SSE_SEP + "data: [DONE]" + SSE_SEP).encode("utf-8"),
        )

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-dead,model-live")
    cooldown.reset()

    async def ask() -> None:
        async for chunk in llm_client.stream(MSGS, purpose="tutor",
                                             budget=llm_client.Budget.INTERACTIVE):
            if chunk.done:
                break

    await ask()
    assert seen == ["model-dead", "model-live"], "первый проход идёт по порядку цепочки"

    seen.clear()
    await ask()
    assert seen == ["model-live"], (
        "второй ученик снова пошёл в мёртвую модель — ротации нет"
    )

    # Остывание кончилось — порядок возвращается: цепочка утверждена стендом,
    # и «лучшая» модель не должна навсегда уступить место запасной.
    cooldown.reset()
    seen.clear()
    await ask()
    assert seen == ["model-dead", "model-live"], "после остывания порядок цепочки прежний"


@pytest.mark.asyncio
async def test_dribbling_stream_without_frames_is_bounded(monkeypatch):
    """Поток сыплет байты, но не закрывает кадр — предел всё равно срабатывает.

    tsk-671, живой случай. Провайдер отдаёт заголовки за 0,2 c и начинает
    «думать вслух»: байты идут, а конец кадра не приходит. Тогда таймаут httpx
    не срабатывает (каждое чтение укладывается в свой предел), а наша проверка
    первого куска стояла ПОСЛЕ `continue` и не выполнялась ни разу. Ученик
    смотрел в «Наставник думает…», и ротация не включалась: формально отказа нет.
    """
    class _Dribble(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(60):          # минута по кусочку, кадр не закрываем
                yield b": ping"
                await asyncio.sleep(1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_Dribble(),
                              headers={"content-type": "text/event-stream"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-dribble")
    cooldown.reset()

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(llm_client.LLMError):
        async for _ in llm_client.stream(MSGS, purpose="tutor",
                                         budget=llm_client.Budget.INTERACTIVE):
            pass
    spent = loop.time() - started

    limit = llm_client.Budget.INTERACTIVE.first_token_timeout + 5
    assert spent < limit, (
        f"ждали {spent:.0f} c — предел первого куска снова не сработал"
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
async def test_batch_stops_walking_chain_at_call_ceiling(monkeypatch, _isolate):
    """Батч обязан иметь потолок на ВЕСЬ вызов, а не только на каждую модель.

    tsk-678. Пока таймаут не переходил к следующей модели, потолок был не нужен:
    вызов обрывался на первой. С tsk-671 переход включён — и без потолка одна
    работа стоит всей цепочки подряд. На бою это не абстракция: за такой проход
    истекает срок пометки «работа взята», следующий тик берёт ту же работу, и
    провайдеру платят за неё дважды.

    Часы поддельные: тест про арифметику бюджета, а не про ожидание вживую.
    """
    clock = {"t": 0.0}
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: clock["t"])
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a,model-b,model-c,model-d")

    def handler(request: httpx.Request) -> httpx.Response:
        clock["t"] += Budget.BATCH.attempt_timeout
        raise httpx.ReadTimeout("модель молчит", request=request)

    _mount(monkeypatch, handler)

    with pytest.raises(LLMTimeout):
        await llm_client.complete(MSGS, purpose="code_review")

    tried = [e.model for e in _isolate]
    assert tried == ["model-a", "model-b", "model-c"], (
        f"перебор обязан остановиться на потолке вызова, а прошёл {tried}"
    )
    assert "model-d" not in tried


@pytest.mark.asyncio
async def test_batch_usage_says_which_model_of_chain_answered(monkeypatch, _isolate):
    """В учёте расхода видно, какая по счёту модель ответила и с какой попытки.

    tsk-678: без этого запись «разбор занял 95 c» неразличима — одна медленная
    попытка или таймаут плюс удачный повтор. Разбор замедления упёрся ровно
    в это: по учёту ответить было нечем.
    """
    monkeypatch.setenv("LLM_JUDGE_MODELS", "model-a,model-b")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:  # первая модель молчит — отвечает вторая
            raise httpx.ReadTimeout("модель молчит", request=request)
        return httpx.Response(200, json=_ok_batch())

    _mount(monkeypatch, handler)

    await llm_client.complete(MSGS, purpose="code_review")

    ok = [e for e in _isolate if e.outcome == "ok"]
    assert ok and ok[-1].model == "model-b"
    assert ok[-1].meta["chain_pos"] == 2, "позиция в цепочке не записана"
    assert ok[-1].meta["chain_len"] == 2
    assert ok[-1].meta["attempt"] == 1


@pytest.mark.asyncio
async def test_budgets_differ_by_profile():
    """Повтор той же модели после таймаута не делает ни один профиль.

    У батча он был (60 c × 2 = 120 c в учёте расхода) и был единственным
    спасением, пока таймаут не переходил к следующей модели. С tsk-671 переход
    есть и он лучше по замеру: стенд `--judge` 25.08 дал у следующей модели
    цепочки медиану 7,2 c при худшем 7,6 c — быстрее, чем первая доедет до
    своего предела. Повтор же той же модели приносил второй такой же таймаут
    за наши деньги: все три вызова «ровно 120,0 c» выглядели именно так.
    """
    assert Budget.INTERACTIVE.timeout_retries == 0
    assert Budget.BATCH.timeout_retries == 0
    # 12 c, а не 5: стенд дал медиану первого токена 4.4-4.6 c, и бюджет,
    # равный медиане, обрывал бы примерно половину живых ответов. Проверено
    # регресс-прогоном — наставник отваливался посреди нормального разговора.
    assert Budget.INTERACTIVE.first_token_timeout == 12.0
    assert Budget.INTERACTIVE.first_token_timeout > 4.6 * 2, (
        "бюджет первого токена должен быть с запасом к медиане, а не равен ей"
    )
    assert Budget.BATCH.first_token_timeout is None
    assert Budget.INTERACTIVE.total_timeout < Budget.BATCH.total_timeout
    # tsk-678: предел ОДНОЙ попытки батча — 30 c. Боевое распределение 1052
    # удачных разборов: медиана 5,6 c, p90 22,2 c. За 30 c приходят 91,9%
    # ответов, а прежние 60 c ждали уже не ответа, а чуда.
    assert Budget.BATCH.attempt_timeout == 30.0
    assert Budget.INTERACTIVE.attempt_timeout == Budget.INTERACTIVE.first_token_timeout
    # Потолок вызова обязан вмещать больше одной попытки — иначе перебор
    # цепочки, ради которого он и заведён, не состоится ни разу.
    assert Budget.BATCH.total_timeout >= Budget.BATCH.attempt_timeout * 2


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


# ───────────────── Учёт расхода: время до первого куска (tsk-683) ───────────


@pytest.mark.asyncio
async def test_stream_usage_keeps_time_to_first_chunk(monkeypatch, _isolate):
    """В учёте видно, сколько ученик ждал НАЧАЛА текста, а не только весь поток.

    tsk-683 (находка tsk-680). `duration_ms` меряет поток целиком, и по нему
    нельзя сказать, уложился ли вызов в предел первого токена: в учёте видно
    лишь «ok» или «LLMTimeout». Ради tsk-680 пришлось поднимать отдельный
    замерщик на проде. Здесь первый кусок приходит сразу, а хвост тянется —
    записи обязаны отличаться друг от друга.
    """
    async def body():
        yield _sse({"choices": [{"delta": {"content": "Пред"}}]})
        await asyncio.sleep(0.2)
        yield _sse({"choices": [{"delta": {"content": "ставь"}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(),
                              headers={"Content-Type": "text/event-stream"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a")

    chunks = [c async for c in llm_client.stream(MSGS, purpose="tutor")]
    assert "".join(c.delta for c in chunks) == "Представь"

    ok = [e for e in _isolate if e.outcome == "ok"]
    assert ok, "успешный поток не попал в учёт"
    assert "first_ms" in ok[-1].meta, "время первого куска не записано"
    assert ok[-1].meta["first_ms"] < ok[-1].duration_ms, (
        "записано полное время потока, а не время до первого куска: "
        f"first_ms={ok[-1].meta['first_ms']}, duration_ms={ok[-1].duration_ms}"
    )


@pytest.mark.asyncio
async def test_stream_usage_omits_first_ms_when_nothing_arrived(monkeypatch, _isolate):
    """Отказ ДО первого куска не пишет `first_ms`: ждать было нечего.

    Ноль здесь соврал бы сильнее пропуска — он неотличим от мгновенного ответа
    и испортил бы любую сводку по времени ожидания.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(
            {"error": {"message": "upstream boom", "status": 502}},
        ), headers={"Content-Type": "text/event-stream"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a")

    with pytest.raises(LLMUpstreamError):
        async for _ in llm_client.stream(MSGS, purpose="tutor"):
            pass

    assert _isolate, "неуспешный поток не попал в учёт"
    assert "first_ms" not in _isolate[-1].meta
    assert _isolate[-1].meta["chars"] == 0, "пустой поток различается по chars"


@pytest.mark.asyncio
async def test_stream_usage_keeps_first_ms_on_broken_stream(monkeypatch, _isolate):
    """Оборванный поток тоже несёт время первого куска.

    Ученик успел увидеть начало ответа — значит вопрос «долго ли он ждал» имеет
    смысл ровно так же, как у доведённого до конца потока.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "Начало ответа"}}]},
            {"error": {"message": "upstream died", "status": 502}},
        ), headers={"Content-Type": "text/event-stream"})

    _mount(monkeypatch, handler)
    monkeypatch.setenv("LLM_TUTOR_MODELS", "model-a")

    chunks = [c async for c in llm_client.stream(MSGS, purpose="tutor")]
    assert chunks[-1].truncated is True

    assert _isolate and _isolate[-1].outcome != "ok"
    assert "first_ms" in _isolate[-1].meta, "у оборванного потока время первого куска потеряно"
    assert _isolate[-1].meta["chars"] == len("Начало ответа")
