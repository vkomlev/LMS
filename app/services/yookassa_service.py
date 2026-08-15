"""tsk-010 — оплата картой через ЮKassa.

Тонкий клиент поверх REST, без синхронного SDK: приложение асинхронное, и
блокирующие вызовы в нём стоили бы занятых воркеров на каждом платеже.

Два правила этого модуля, оба про деньги:

1. **Телу уведомления не верим.** ЮKassa не подписывает уведомления — подлинность
   проверяется перезапросом платежа по его номеру. Поэтому зачисление всегда
   идёт по ответу API, а не по тому, что прислали на webhook: иначе любой, кто
   знает адрес, «оплатит» месяц пустым POST-запросом.
2. **Боевой ключ не включается сам.** Ключ тестового магазина начинается с
   `test_`; любой другой принимается только при явном YOOKASSA_ALLOW_LIVE=true.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)
settings = Settings()

__all__ = [
    "GatewayDisabledError",
    "GatewayError",
    "GatewayPayment",
    "is_enabled",
    "is_test_mode",
    "create_payment",
    "fetch_payment",
    "list_succeeded",
]

#: Ключ тестового магазина. Всё остальное считаем боевым.
_TEST_KEY_PREFIX = "test_"


class GatewayDisabledError(RuntimeError):
    """Оплата картой не настроена или настроена боевым ключом без разрешения."""


class GatewayError(RuntimeError):
    """Шлюз не ответил или ответил ошибкой."""


@dataclass
class GatewayPayment:
    """Платёж на стороне шлюза — то, что нужно нам, без остальных полей ответа."""

    id: str
    status: str
    amount_minor: int
    paid: bool
    #: Ссылка, по которой плательщик вводит данные карты. Есть только у нового платежа.
    confirmation_url: Optional[str]
    #: Признак тестового платежа. Боевой контур обязан на него смотреть.
    test: bool
    metadata: dict[str, Any]
    #: Когда деньги реально захвачены, по данным шлюза (UTC). Нужна там, где
    #: платёж учитывается задним числом: «сегодня» тогда не дата платежа, а
    #: дата разбора, и сверка с чеками разъедется на эту разницу (tsk-615).
    captured_at: Optional[datetime] = None


def is_test_mode() -> bool:
    return settings.yookassa_secret_key.startswith(_TEST_KEY_PREFIX)


def is_enabled() -> bool:
    """Настроен ли способ оплаты картой.

    Боевой ключ без явного разрешения — то же самое, что выключенный способ:
    лучше отсутствующая кнопка, чем случайное списание настоящих денег.
    """
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        return False
    return is_test_mode() or settings.yookassa_allow_live


def _guard() -> tuple[str, str]:
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise GatewayDisabledError("Оплата картой не настроена")
    if not is_test_mode() and not settings.yookassa_allow_live:
        logger.error(
            "tsk-010: попытка работать боевым ключом ЮKassa без YOOKASSA_ALLOW_LIVE — отказ"
        )
        raise GatewayDisabledError(
            "Задан боевой ключ ЮKassa, но боевой режим не разрешён"
        )
    return settings.yookassa_shop_id, settings.yookassa_secret_key


def _parse(payload: dict[str, Any]) -> GatewayPayment:
    amount = payload.get("amount") or {}
    # Сумма приходит строкой рублей («5500.00») — переводим в копейки через целое,
    # без float: 0.1 + 0.2 в деньгах недопустимо.
    value = str(amount.get("value") or "0")
    rubles, _, kopecks = value.partition(".")
    minor = int(rubles) * 100 + int((kopecks + "00")[:2] or 0)
    confirmation = payload.get("confirmation") or {}
    return GatewayPayment(
        id=str(payload.get("id") or ""),
        status=str(payload.get("status") or ""),
        amount_minor=minor,
        paid=bool(payload.get("paid")),
        confirmation_url=confirmation.get("confirmation_url"),
        test=bool(payload.get("test")),
        metadata=dict(payload.get("metadata") or {}),
        captured_at=_as_datetime(payload.get("captured_at") or payload.get("created_at")),
    )


def _as_datetime(raw: Any) -> Optional[datetime]:
    """Дата из ответа шлюза. Формат ISO с `Z`, который `fromisoformat` не берёт.

    Разбор не должен ронять приём платежа: дата — сведение для сверки, а не
    условие зачисления денег. Не разобралось — считаем, что её нет.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("tsk-615: шлюз прислал дату в непонятном виде: %r", raw)
        return None


async def create_payment(
    *,
    amount_minor: int,
    description: str,
    return_url: str,
    metadata: dict[str, Any],
    idempotence_key: Optional[str] = None,
) -> GatewayPayment:
    """Завести платёж в шлюзе и получить ссылку на оплату.

    `idempotence_key` защищает от двойного списания при повторе запроса: тот же
    ключ в течение суток вернёт тот же платёж, а не создаст второй.
    """
    shop_id, secret = _guard()
    body = {
        "amount": {"value": f"{amount_minor // 100}.{amount_minor % 100:02d}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": description[:128],
        "metadata": metadata,
    }
    headers = {"Idempotence-Key": idempotence_key or str(uuid.uuid4())}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.yookassa_api_url}/payments",
                json=body,
                headers=headers,
                auth=(shop_id, secret),
            )
    except httpx.HTTPError as exc:
        raise GatewayError(f"шлюз недоступен: {exc}") from exc
    if resp.status_code >= 400:
        # Тело ответа в лог не пишем целиком: там эхо наших данных и служебные поля.
        logger.error("tsk-010: ЮKassa вернула %s на создание платежа", resp.status_code)
        raise GatewayError(f"шлюз отказал: HTTP {resp.status_code}")
    return _parse(resp.json())


async def list_succeeded(
    *, created_from: date, created_to: date, limit: int = 100
) -> list[GatewayPayment]:
    """Успешные платежи шлюза за период — основа сверки.

    Нужна потому, что уведомление может не дойти: не настроено, не достучалось,
    наш сервер перезагружался. Тогда деньги списаны, а у нас долг. Сверка
    смотрит на шлюз как на источник правды и добирает пропущенное.
    """
    shop_id, secret = _guard()
    params = {
        "status": "succeeded",
        "created_at.gte": f"{created_from.isoformat()}T00:00:00.000Z",
        "created_at.lte": f"{created_to.isoformat()}T23:59:59.999Z",
        "limit": min(limit, 100),
    }
    collected: list[GatewayPayment] = []
    cursor: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Пагинация курсором: без неё сверка молча ограничилась бы первой
            # сотней платежей и «не нашла» как раз старые потерянные.
            while True:
                query = dict(params)
                if cursor:
                    query["cursor"] = cursor
                resp = await client.get(
                    f"{settings.yookassa_api_url}/payments",
                    params=query,
                    auth=(shop_id, secret),
                )
                if resp.status_code >= 400:
                    raise GatewayError(f"шлюз отказал: HTTP {resp.status_code}")
                data = resp.json()
                collected.extend(_parse(item) for item in data.get("items", []))
                cursor = data.get("next_cursor")
                if not cursor or len(collected) >= limit:
                    break
    except httpx.HTTPError as exc:
        raise GatewayError(f"шлюз недоступен: {exc}") from exc
    return collected


async def fetch_payment(payment_id: str) -> GatewayPayment:
    """Перезапросить платёж у шлюза — единственный источник правды о его статусе."""
    shop_id, secret = _guard()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.yookassa_api_url}/payments/{payment_id}",
                auth=(shop_id, secret),
            )
    except httpx.HTTPError as exc:
        raise GatewayError(f"шлюз недоступен: {exc}") from exc
    if resp.status_code >= 400:
        logger.error(
            "tsk-010: ЮKassa вернула %s на запрос платежа %s", resp.status_code, payment_id
        )
        raise GatewayError(f"шлюз отказал: HTTP {resp.status_code}")
    return _parse(resp.json())
