"""Прямой Messages API Anthropic — второй провайдер рядом с CloseRouter (tsk-1259).

**Зачем.** CloseRouter подмешивает в `anthropic/*` свою скрытую инструкцию (сотни
входных токенов на одно «Привет») и однажды под меткой `openai/gpt-5.5` отдал
ученику Grok. Прямой API без посредника: что мы послали, то модель и видит.

**Как включается.** Метка модели `anthropic-direct/<id>` (например
`anthropic-direct/claude-sonnet-5-5`) в любой цепочке. Работает только при
`ANTHROPIC_DIRECT_ENABLED=1` и ключе `CLAUDE_API_KEY`; рубильник выключен по
умолчанию — без него такие метки из цепочки выбрасываются с предупреждением,
а цепочка CloseRouter работает как раньше.

**Как встроен.** Клиент (`client.py`) один на оба провайдера. Этот модуль
переводит наш запрос в формат Messages API, а ответ и события потока — в тот
OpenAI-подобный вид, который клиент уже умеет разбирать (`choices`, `usage`,
`error`, `id`). Так проверки клиента — ошибка внутри 200, заглушка текстом,
подлинность по `id` и самоназванию, предел первого куска — работают и здесь
без второй копии.

Параметры моделей Claude 5.5 (справка claude-api, 06.10):
  * `temperature` нестандартная — 400 у Sonnet 5.5 и Haiku 5.5, у Opus 5.5 снята
    вовсе; не передаём. `seed` API не знает — тоже не передаём.
  * Размышление: у Haiku 5.5 выключается (`disabled`, допустимо при effort ≤ high),
    у Sonnet 5.5 — только `between_tools`, у Opus 5.5 не выключается никак, его
    глубину задаёт `output_config.effort`. Наставнику размышление вредно: это
    пауза до первого куска, а предел первого куска 12 c.
  * `response_format` у Messages API нет; JSON просит сам промпт, а судьи и так
    вычищают обёртку перед разбором.
"""
from __future__ import annotations

import os
from typing import Optional, Sequence

from app.services.llm.contracts import LLMMessage

PREFIX = "anthropic-direct/"
PROVIDER_NAME = "anthropic-direct"
DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
# Пустой первый ход пользователя перед вступлением наставника (см. `payload`).
OPENING_PLACEHOLDER = "(Ученик открыл разговор с наставником.)"


def is_direct(model: str) -> bool:
    """Метка относится к прямому API Anthropic."""
    return (model or "").startswith(PREFIX)


def bare_model(model: str) -> str:
    """`anthropic-direct/claude-sonnet-5-5` → `claude-sonnet-5-5`."""
    return model[len(PREFIX):] if is_direct(model) else model


def enabled() -> bool:
    """Рубильник: прямой провайдер включён явно, по умолчанию выключен."""
    return (os.environ.get("ANTHROPIC_DIRECT_ENABLED") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def base_url() -> str:
    """Адрес Messages API: `ANTHROPIC_DIRECT_BASE_URL` или официальный.

    tsk-1376: с боевого сервера (RU) api.anthropic.com отвечает 403 по стране —
    временный выход через пересыльщик на машине оператора и обратный SSH-тоннель
    (`http://127.0.0.1:<порт>`). Ключ при этом по-прежнему берётся из окружения сервера.
    """
    raw = (os.environ.get("ANTHROPIC_DIRECT_BASE_URL") or "").strip().rstrip("/")
    return raw or DEFAULT_BASE_URL


def api_key() -> Optional[str]:
    """Ключ прямого API. Значение нигде не печатается и не логируется."""
    value = os.environ.get("CLAUDE_API_KEY")
    return value.strip() if value and value.strip() else None


def headers(key: str) -> dict[str, str]:
    """Заголовки Messages API: ключ в `x-api-key`, не в `Authorization`."""
    return {
        "x-api-key": key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }


def _thinking_fields(model: str) -> dict:
    """Минимальное размышление для модели: наставнику важен быстрый первый кусок."""
    name = bare_model(model)
    if name.startswith("claude-haiku-5"):
        return {"thinking": {"type": "disabled"}}
    if name.startswith("claude-sonnet-5-5"):
        return {"thinking": {"type": "between_tools"}}
    if name.startswith("claude-opus-5"):
        return {"output_config": {"effort": "low"}}
    return {}


def payload(
    messages: Sequence[LLMMessage], *, model: str, max_tokens: int, stream: bool,
) -> dict:
    """Тело запроса Messages API.

    Системные сообщения склеиваются в поле `system`: у Messages API системная
    роль в начале `messages` недопустима, а наш промпт приходит именно так.
    """
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
    if turns and turns[0]["role"] == "assistant":
        # Боевая история после вступления начинается с реплики наставника
        # (`session_service.build_llm_messages`), а Messages API требует первым
        # сообщение пользователя — без этой строки каждый второй ход давал бы 400.
        turns.insert(0, {"role": "user", "content": OPENING_PLACEHOLDER})
    body: dict = {
        "model": bare_model(model),
        "max_tokens": max_tokens,
        "messages": turns,
        "stream": stream,
        **_thinking_fields(model),
    }
    if system:
        body["system"] = system
    return body


def _input_tokens(usage: dict) -> int:
    """Весь вход: обычные токены плюс записанные и прочитанные из кеша."""
    return int(usage.get("input_tokens") or 0) + int(
        usage.get("cache_creation_input_tokens") or 0
    ) + int(usage.get("cache_read_input_tokens") or 0)


def _error(payload_: dict) -> Optional[dict]:
    err = payload_.get("error")
    if payload_.get("type") == "error" or err:
        message = err.get("message", "") if isinstance(err, dict) else str(err)
        return {"error": {"message": f"anthropic: {message}"}}
    return None


def _refusal(stop_reason: Optional[str]) -> Optional[dict]:
    # Отказ модели по безопасности приходит HTTP 200 с пустым или оборванным
    # текстом: для клиента это ошибка внутри успешного ответа, а не реплика.
    if stop_reason == "refusal":
        return {"error": {"message": "anthropic: модель отказалась отвечать (refusal)"}}
    return None


def normalize_message(data: dict) -> dict:
    """Ответ обычного вызова → вид `choices/usage/id`, понятный клиенту."""
    failed = _error(data) or _refusal(data.get("stop_reason"))
    if failed:
        return failed
    text = "".join(
        block.get("text", "") for block in data.get("content") or []
        if block.get("type") == "text"
    )
    usage = data.get("usage") or {}
    return {
        "id": data.get("id"),
        "choices": [{"message": {"content": text}}],
        "usage": {
            "prompt_tokens": _input_tokens(usage),
            "completion_tokens": int(usage.get("output_tokens") or 0),
        },
    }


def normalize_event(event: dict) -> dict:
    """Событие потока Messages API → кадр вида OpenAI-потока.

    `message_start` несёт `id` и входные токены, `content_block_delta` с
    `text_delta` — текст, `message_delta` — выходные токены и причину остановки.
    Размышления (`thinking_delta`) и служебные события дают пустой кадр.
    """
    failed = _error(event)
    if failed:
        return failed
    kind = event.get("type")
    if kind == "message_start":
        message = event.get("message") or {}
        usage = message.get("usage") or {}
        return {"id": message.get("id"), "usage": {"prompt_tokens": _input_tokens(usage)}}
    if kind == "content_block_delta":
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            return {"choices": [{"delta": {"content": delta.get("text", "")}}]}
        return {}
    if kind == "message_delta":
        refused = _refusal((event.get("delta") or {}).get("stop_reason"))
        if refused:
            return refused
        usage = event.get("usage") or {}
        return {"usage": {"completion_tokens": int(usage.get("output_tokens") or 0)}}
    return {}
