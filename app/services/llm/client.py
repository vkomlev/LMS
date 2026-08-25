"""Транспорт к LLM-провайдеру (tsk-572 этап 1).

Контракт: docs/specs/2026-08-06-contract-llm-client.md.
Два входа — `complete` (батч, tsk-302) и `stream` (интерактив, tsk-572) — над
одним конвейером: резолв → остывание → вызов по цепочке моделей → учёт → ошибки.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Iterable, Optional, Sequence

import httpx

from app.services.llm import cooldown, providers, usage
from app.services.llm.contracts import (
    Budget,
    LLMChunk,
    LLMConfigError,
    LLMCooldown,
    LLMError,
    LLMMalformed,
    LLMMessage,
    LLMQuotaExceeded,
    LLMRateLimited,
    LLMResult,
    LLMTimeout,
    LLMUnavailable,
    LLMUpstreamUnavailable,
    LLMUpstreamError,
    UsageRecord,
)

logger = logging.getLogger(__name__)

# Подстрока, по которой узнаём отбой по квоте КЛЮЧА (не по балансу аккаунта).
# Формулировка провайдера, поймана живьём: "pre-consume quota failed, user
# remaing quota: $0.00045" — опечатка в "remaing" его собственная.
_QUOTA_MARKER = "quota"


def _raise_for_status(status: int, body: str) -> None:
    """HTTP-статус → класс ошибки по таблице §5."""
    if status == 429:
        cooldown.start(providers.PROVIDER_NAME, providers.cooldown_seconds())
        raise LLMRateLimited(f"429 от провайдера: {body[:200]}")
    if status in (401, 403):
        raise LLMConfigError(f"{status}: ключ отвергнут провайдером — {body[:200]}")
    if status == 404:
        raise LLMConfigError(f"404: модель или путь не найдены — {body[:200]}")
    if status >= 500:
        # Не голый LLMUnavailable: 5xx пришёл ОТ провайдера и относится к
        # конкретной модели — цепочка обязана попробовать следующую (tsk-666).
        raise LLMUpstreamUnavailable(f"{status} от провайдера: {body[:200]}")
    if status >= 400:
        raise LLMMalformed(f"{status}: неожиданный ответ — {body[:200]}")


def _check_payload_error(payload: dict) -> None:
    """Поднять ошибку, если тело содержит `error` при внешне успешном ответе.

    Провайдер умеет отдавать HTTP 200 + `text/event-stream`, внутри которого
    лежит `{"error": {...}, "status": 502}`. Клиент, который смотрит только на
    статус, покажет ученику ПУСТОЙ ОТВЕТ как нормальный. Проверено живьём.
    """
    err = payload.get("error")
    if not err:
        return
    message = err.get("message", "") if isinstance(err, dict) else str(err)
    if _QUOTA_MARKER in message.lower():
        raise LLMQuotaExceeded(f"квота ключа исчерпана: {message[:300]}")
    raise LLMUpstreamError(f"ошибка upstream внутри успешного ответа: {message[:300]}")


def _payload(
    messages: Sequence[LLMMessage],
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    stream: bool,
    seed: Optional[int],
    response_format: Optional[dict],
) -> dict:
    body: dict = {
        "model": model,
        "messages": [m.as_payload() for m in messages],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if seed is not None:
        # Просьба чипа tsk-302 (§12.1): при повторной калибровке рубрики
        # расхождение вердиктов должно означать правку рубрики, а не дрожание
        # модели. temperature=0 сам по себе детерминизма не даёт.
        body["seed"] = seed
    if response_format is not None:
        body["response_format"] = response_format
    return body


def _timeout(budget: Budget) -> httpx.Timeout:
    return httpx.Timeout(
        connect=budget.connect_timeout,
        read=budget.total_timeout,
        write=budget.total_timeout,
        pool=budget.connect_timeout,
    )


def _guard_cooldown() -> providers.ProviderConfig:
    cfg = providers.resolve_provider()
    if not cfg.usable:
        raise LLMConfigError(
            "ключ провайдера не задан (CLOSEROUTER_API_KEY / CB_CLAUDE_API_KEY)"
        )
    left = cooldown.remaining(cfg.name)
    if left > 0:
        raise LLMCooldown(f"провайдер остывает ещё {left:.0f} c после 429")
    return cfg


def _headers(cfg: providers.ProviderConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }


def _models_for(model: Optional[str], chain: Iterable[str]) -> list[str]:
    """Явная модель отменяет цепочку: вызывающий знает, что делает."""
    if model:
        return [model]
    models = list(chain)
    if not models:
        raise LLMConfigError("цепочка моделей пуста — нечего вызывать")
    return models


async def _usage_ok(
    *, purpose: str, student_id: Optional[int], model: str, provider: str,
    tokens_in: int, tokens_out: int, duration_ms: int, outcome: str, meta: dict | None = None,
) -> None:
    await usage.record(UsageRecord(
        purpose=purpose, student_id=student_id, model=model, provider=provider,
        tokens_in=tokens_in, tokens_out=tokens_out, duration_ms=duration_ms,
        outcome=outcome, meta=meta or {},
    ))


# ──────────────────────────────── Батч ──────────────────────────────────────


async def complete(
    messages: Sequence[LLMMessage],
    *,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    purpose: str,
    student_id: Optional[int] = None,
    budget: Budget = Budget.BATCH,
    seed: Optional[int] = None,
    response_format: Optional[dict] = None,
) -> LLMResult:
    """Один батч-вызов. Текст отдаётся как есть — предметный разбор у потребителя.

    `model=None` — идти по цепочке `LLM_JUDGE_MODELS` до первой рабочей.
    """
    cfg = _guard_cooldown()
    chain = _models_for(model, providers.judge_models())
    last_error: Optional[LLMError] = None

    async with httpx.AsyncClient(timeout=_timeout(budget)) as http:
        for candidate in chain:
            attempts = 0
            started = time.monotonic()
            while True:
                attempts += 1
                try:
                    resp = await http.post(
                        cfg.chat_url, headers=_headers(cfg),
                        json=_payload(messages, model=candidate, temperature=temperature,
                                      max_tokens=max_tokens, stream=False, seed=seed,
                                      response_format=response_format),
                    )
                    body = resp.text
                    _raise_for_status(resp.status_code, body)
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise LLMMalformed(f"ответ не JSON: {body[:200]}") from exc
                    _check_payload_error(payload)

                    choices = payload.get("choices") or []
                    if not choices:
                        raise LLMMalformed(f"в ответе нет choices: {body[:200]}")
                    text_out = (choices[0].get("message") or {}).get("content") or ""
                    usage_block = payload.get("usage") or {}
                    duration_ms = int((time.monotonic() - started) * 1000)

                    await _usage_ok(
                        purpose=purpose, student_id=student_id, model=candidate,
                        provider=cfg.name, tokens_in=int(usage_block.get("prompt_tokens") or 0),
                        tokens_out=int(usage_block.get("completion_tokens") or 0),
                        duration_ms=duration_ms, outcome="ok",
                    )
                    return LLMResult(
                        text=text_out, model=candidate,
                        tokens_in=int(usage_block.get("prompt_tokens") or 0),
                        tokens_out=int(usage_block.get("completion_tokens") or 0),
                        duration_ms=duration_ms, attempts=attempts,
                    )

                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    err: LLMError = (
                        LLMTimeout(f"таймаут бюджета {budget.value}: {exc}")
                        if isinstance(exc, httpx.TimeoutException)
                        else LLMUnavailable(f"сетевая ошибка: {exc}")
                    )
                    if attempts <= budget.timeout_retries:
                        logger.info("LLM: повтор после %s (модель %s)", type(exc).__name__, candidate)
                        continue
                    last_error = err
                except LLMError as exc:
                    last_error = exc

                await _usage_ok(
                    purpose=purpose, student_id=student_id, model=candidate,
                    provider=cfg.name, tokens_in=0, tokens_out=0,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    outcome=type(last_error).__name__,
                    meta={"message": str(last_error)[:300]},
                )
                break  # к следующей модели цепочки или к выходу

            if last_error is not None and not last_error.try_next_model:
                raise last_error

    raise last_error or LLMUnavailable("цепочка моделей исчерпана без результата")


# ────────────────────────────── Интерактив ──────────────────────────────────


def _sse_lines_to_payloads(chunk_text: str) -> Iterable[dict]:
    """Разобрать блок SSE в объекты. `[DONE]` пропускаем, битые строки — тоже."""
    for line in chunk_text.splitlines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            logger.debug("LLM: пропущен неразбираемый SSE-кадр: %.120s", data)



def sse_payloads_from_chunks(chunks: Iterable[str], carry: dict) -> Iterable[dict]:
    """Разобрать сетевые куски SSE, НЕ теряя кадр на границе чтения.

    Сеть режет поток произвольно, и кадр запросто приходит разорванным пополам
    между двумя чтениями. Разбор каждого куска по отдельности молча выбрасывал
    такой кадр — ответ выходил слегка неправильным («что ужеовал сделать»
    вместо «что уже попробовал»). Это не ловится ничем, кроме чтения глазами:
    ни ошибки, ни лога, просто кривая фраза.

    `carry` — изменяемый словарь с ключом `buf`: незавершённый хвост переносится
    между вызовами. Вынесено функцией, чтобы проверялось тестом без сети.
    """
    separator = "\n\n"
    buffer = carry.get("buf", "") + "".join(chunks)
    head, sep, tail = buffer.rpartition(separator)
    carry["buf"] = tail
    if not sep:
        return []
    return list(_sse_lines_to_payloads(head + separator))


async def stream(
    messages: Sequence[LLMMessage],
    *,
    model: Optional[str] = None,
    temperature: float = 0.6,
    max_tokens: int = 1024,
    purpose: str,
    student_id: Optional[int] = None,
    budget: Budget = Budget.INTERACTIVE,
    seed: Optional[int] = None,
) -> AsyncIterator[LLMChunk]:
    """Потоковый вызов.

    Ошибка ДО первого куска поднимается исключением — потребитель показывает
    деградацию (кнопку преподавателя). Ошибка ПОСЛЕ первого куска не поднимается:
    приходит финальный чанк с `truncated=True`, чтобы ученик увидел то, что уже
    написано, и предложение продолжить, а не стёртый экран с ошибкой.

    `model=None` — цепочка `LLM_TUTOR_MODELS`. Переход к следующей модели возможен
    только пока не отдан ни один кусок: подменять модель на середине фразы нельзя.
    """
    cfg = _guard_cooldown()
    chain = _models_for(model, providers.tutor_models())
    last_error: Optional[LLMError] = None
    # Потолок на ВЕСЬ вызов, а не на каждую модель по отдельности (tsk-671).
    # Ученик ждёт один раз: четыре модели по 40 c — это не «надёжность», это
    # три минуты в «Наставник думает…» с заблокированным полем ввода.
    deadline = time.monotonic() + budget.total_timeout

    async with httpx.AsyncClient(timeout=_timeout(budget)) as http:
        for candidate in chain:
            if last_error is not None and time.monotonic() >= deadline:
                logger.warning(
                    "LLM: бюджет %.0f c исчерпан, цепочку дальше не перебираем",
                    budget.total_timeout,
                )
                break
            started = time.monotonic()
            first_at: Optional[float] = None
            got_any = False
            text_len = 0
            tokens_in = tokens_out = 0

            try:
                async with http.stream(
                    "POST", cfg.chat_url, headers=_headers(cfg),
                    json=_payload(messages, model=candidate, temperature=temperature,
                                  max_tokens=max_tokens, stream=True, seed=seed,
                                  response_format=None),
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", "ignore")
                        _raise_for_status(resp.status_code, body)

                    # Хвост НЕЛЬЗЯ терять: сеть режет поток произвольно, и кадр
                    # запросто приходит разорванным пополам между двумя
                    # чтениями. Разбор каждого куска по отдельности молча
                    # выбрасывал такой кадр — ответ выходил слегка неправильным
                    # («что ужеовал сделать» вместо «что уже попробовал»),
                    # и это не ловится ничем, кроме чтения глазами.
                    buffer = ""
                    reader = resp.aiter_text().__aiter__()
                    while True:
                        # Ждать первый кусок бесконечно нельзя. Проверка ниже
                        # (`first_at is None and ... > first_token_timeout`) стоит
                        # ВНУТРИ обработки куска, поэтому при полностью молчащем
                        # upstream не выполняется ни разу: цикл не делает ни одной
                        # итерации, и ученик ждёт до общего таймаута соединения.
                        # Живой случай tsk-671: «Наставник думает…» две минуты
                        # подряд, в учёте расхода — ни одной записи (tsk-666).
                        if first_at is None and budget.first_token_timeout is not None:
                            left = budget.first_token_timeout - (time.monotonic() - started)
                            if left <= 0:
                                raise LLMTimeout(
                                    f"первый токен не пришёл за {budget.first_token_timeout} c"
                                )
                            try:
                                raw = await asyncio.wait_for(reader.__anext__(), timeout=left)
                            except asyncio.TimeoutError:
                                raise LLMTimeout(
                                    f"первый токен не пришёл за {budget.first_token_timeout} c"
                                )
                            except StopAsyncIteration:
                                break
                        else:
                            try:
                                raw = await reader.__anext__()
                            except StopAsyncIteration:
                                break
                        buffer += raw
                        head, sep, tail = buffer.rpartition("\n\n")
                        if not sep:
                            continue
                        buffer = tail
                        for payload in _sse_lines_to_payloads(head + "\n\n"):
                            # Ошибка внутри HTTP 200 — главная ловушка провайдера.
                            _check_payload_error(payload)

                            block = payload.get("usage") or {}
                            if block:
                                tokens_in = int(block.get("prompt_tokens") or tokens_in)
                                tokens_out = int(block.get("completion_tokens") or tokens_out)

                            for choice in payload.get("choices") or []:
                                delta = (choice.get("delta") or {}).get("content") or ""
                                if not delta:
                                    continue
                                if first_at is None:
                                    first_at = time.monotonic()
                                got_any = True
                                text_len += len(delta)
                                yield LLMChunk(delta=delta, model=candidate)

                        if (
                            first_at is None
                            and budget.first_token_timeout is not None
                            and time.monotonic() - started > budget.first_token_timeout
                        ):
                            raise LLMTimeout(
                                f"первый токен не пришёл за {budget.first_token_timeout} c"
                            )

                duration_ms = int((time.monotonic() - started) * 1000)
                await _usage_ok(
                    purpose=purpose, student_id=student_id, model=candidate,
                    provider=cfg.name, tokens_in=tokens_in, tokens_out=tokens_out,
                    duration_ms=duration_ms, outcome="ok", meta={"chars": text_len},
                )
                yield LLMChunk(
                    done=True, model=candidate, tokens_in=tokens_in,
                    tokens_out=tokens_out, duration_ms=duration_ms,
                )
                return

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = (
                    LLMTimeout(f"таймаут бюджета {budget.value}: {exc}")
                    if isinstance(exc, httpx.TimeoutException)
                    else LLMUnavailable(f"сетевая ошибка: {exc}")
                )
            except LLMError as exc:
                last_error = exc

            duration_ms = int((time.monotonic() - started) * 1000)
            await _usage_ok(
                purpose=purpose, student_id=student_id, model=candidate,
                provider=cfg.name, tokens_in=tokens_in, tokens_out=tokens_out,
                duration_ms=duration_ms, outcome=type(last_error).__name__,
                meta={"message": str(last_error)[:300], "chars": text_len},
            )

            if got_any:
                # Поток уже начался — обрыв показываем как незавершённый ответ,
                # а не как ошибку: стирать написанное на глазах у ученика хуже.
                logger.warning(
                    "LLM: поток оборван после %d символов (модель %s): %s",
                    text_len, candidate, last_error,
                )
                yield LLMChunk(
                    done=True, truncated=True, model=candidate,
                    tokens_in=tokens_in, tokens_out=tokens_out, duration_ms=duration_ms,
                )
                return

            if not last_error.try_next_model:
                raise last_error

    raise last_error or LLMUnavailable("цепочка моделей исчерпана без результата")
