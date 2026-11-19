"""Прямой OpenAI-совместимый API Kimi (подписка оператора) — третий провайдер (tsk-1376, 19.11).

**Зачем.** Единственное звено наставника `z-ai/glm-5.3-flash` через CloseRouter
молчит: еженедельный разбор 19.11 — 79 % ходов без ответа, все таймауты первого
токена (предел 12 c). По решению оператора голова цепочки — Kimi по его подписке
(модель `kimi-for-coding`, «K2.8 Preview»), CloseRouter остаётся запасным звеном.

**Как включается.** Метка `kimi-direct/<id>` (например `kimi-direct/kimi-for-coding`)
в любой цепочке. Работает только при `KIMI_DIRECT_ENABLED=1` и ключе `KIMI_API_KEY`;
рубильник выключен по умолчанию — без него такие метки из цепочки выбрасываются
с предупреждением. Адрес — `KIMI_DIRECT_BASE_URL`, по умолчанию
`https://api.kimi.com/coding` (проверено 19.11: этот ключ живёт именно там;
api.moonshot.cn ключ отвергает с 401).

**Как встроен.** В отличие от `anthropic_direct.py`, трансляция формата не нужна:
эндпоинт отдаёт обычный OpenAI-вид (`choices`, `usage`, SSE с `delta.content`),
который клиент уже разбирает. Модуль даёт клиенту подключение, заголовки
и одну особенность тела — `reasoning_effort: low`: модели этой подписки
рассуждающие (`reasoning.type: only`, дефолтный effort `max`), а наставнику
критичен быстрый первый кусок (предел 12 c). Замер 19.11: первый content-куск
0,8–2,5 c при `low`. Поле `reasoning_content` приходит отдельно от `content` —
клиент читает только `delta.content`, в ученика рассуждения не утекают.

**Подлинность.** Проверки `identity.py` для семейства `kimi-direct` не заданы:
это прямой API без посредника, подмене неоткуда взяться, а отпечаток `id`
(`chatcmpl-<alnum>`) совпадает по форме с OpenAI — отдельное правило дало бы
ложные срабатывания. Самоназвание («Kimi») в списке имён `_SELF_INTRO` нет —
на ответ не влияет. Раздутый вход ловит общий `check_input_tokens` (у платформы
~90 токенов служебной обвязки сверху — далеко до порога).
"""
from __future__ import annotations

import os
from typing import Optional

PREFIX = "kimi-direct/"
PROVIDER_NAME = "kimi-direct"
DEFAULT_BASE_URL = "https://api.kimi.com/coding"


def is_direct(model: str) -> bool:
    """Метка относится к прямому API Kimi."""
    return (model or "").startswith(PREFIX)


def bare_model(model: str) -> str:
    """`kimi-direct/kimi-for-coding` → `kimi-for-coding`."""
    return model[len(PREFIX):] if is_direct(model) else model


def enabled() -> bool:
    """Рубильник: прямой провайдер включён явно, по умолчанию выключен."""
    return (os.environ.get("KIMI_DIRECT_ENABLED") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def base_url() -> str:
    """Базовый адрес OpenAI-совместимого API (без хвостового `/v1`)."""
    raw = (os.environ.get("KIMI_DIRECT_BASE_URL") or "").strip().rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[: -len("/v1")]
    return raw or DEFAULT_BASE_URL


def api_key() -> Optional[str]:
    """Ключ подписки Kimi. Значение нигде не печатается и не логируется."""
    value = os.environ.get("KIMI_API_KEY")
    return value.strip() if value and value.strip() else None


def headers(key: str) -> dict[str, str]:
    """Заголовки OpenAI-совместимого API: ключ в `Authorization`."""
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def extra_body() -> dict:
    """Дополнительные поля тела запроса для этого провайдера."""
    return {"reasoning_effort": "low"}
