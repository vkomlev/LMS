"""Проверка «ответила та модель, которую просили» (tsk-1259).

**Зачем.** 10.10 маршрутизатор под меткой `openai/gpt-5.5` отдал ученику Grok:
«Я Grok 4.5, созданный xAI… не могу принять эту роль наставника». Поле `model`
в ответе маршрутизатора — эхо запрошенной метки, а не правда о том, кто отвечал:
проба 10.10 показала его равным запросу во всех 30 вызовах, включая заведомо
разные модели. Поэтому подлинность проверяется по двум признакам, которые
маршрутизатор не переписывает:

1. **Отпечаток идентификатора ответа.** Каждый вендор выдаёт `id` своего вида
   (проба 10.10, обычный и потоковый режим):
     anthropic/*  `msg_…`
     openai/*     `chatcmpl-<цифры>` или `resp_…`
     google/*     `chatcmpl-<uuid>`
     x-ai/*       голый uuid или 24 hex-символа
   Несовпадение с семейством запрошенной модели = подмена.

2. **Самоназвание в начале ответа.** Подменённая модель часто называет себя
   («Я Grok…») или отказывается от роли. Начало потока придерживается до
   `SNIFF_CHARS` символов — этого хватает на первую фразу.

Это признаки, а не доказательство: маршрутизатор может однажды начать
переписывать `id`. Поэтому проверка — одна из линий, а вторая — страж ответа
(`app/services/ai_tutor/answer_guard.py`), который режет решение независимо от
того, кто отвечает.
"""
from __future__ import annotations

import re
from typing import Optional

from app.services.llm.contracts import LLMIdentityMismatch

# Первая фраза ответа. Больше — заметная задержка у ученика, меньше — самоназвание
# «Я Grok 4.5, созданный компанией xAI» не помещается целиком.
SNIFF_CHARS = 200

_ID_RULES: dict[str, re.Pattern[str]] = {
    "anthropic": re.compile(r"^msg_"),
    # У OpenAI после `chatcmpl-` нет дефисов; у Google там uuid с дефисами —
    # иначе подмена gpt на gemini проходила бы проверку.
    "openai": re.compile(r"^(?:resp_|chatcmpl-[A-Za-z0-9]+$)"),
    "google": re.compile(r"^chatcmpl-[0-9a-f]{8}-"),
}

# Вендор → как модели этого вендора себя называют.
_VENDOR_NAMES: dict[str, str] = {
    "anthropic": r"claude|anthropic",
    "openai": r"gpt|chatgpt|openai",
    "google": r"gemini|google",
    "x-ai": r"grok|xai|x\.ai",
}
_SELF_INTRO = re.compile(
    r"(?:\bя\s*[—–-]?\s*|\bi\s+am\s+|\bi'm\s+|создан\w*\s+(?:компанией\s+)?|"
    r"разработан\w*\s+(?:компанией\s+)?|trained\s+by\s+|made\s+by\s+)"
    r"(?P<name>claude|anthropic|gpt|chatgpt|openai|gemini|google|grok|xai|x\.ai)",
    re.IGNORECASE,
)
# Отказ от роли наставника — тоже признак чужой модели или подмешанной инструкции.
_ROLE_REFUSAL = re.compile(
    r"не\s+(?:могу|буду)\s+(?:принять|играть|исполнять|взять)\s+(?:на\s+себя\s+)?(?:эту\s+)?рол",
    re.IGNORECASE,
)


def vendor_of(model: str) -> str:
    """Семейство модели по метке: `openai/gpt-5.5` → `openai`."""
    return (model or "").split("/", 1)[0].lower()


def check_response_id(model: str, response_id: Optional[str]) -> None:
    """Поднять `LLMIdentityMismatch`, если `id` ответа не того вендора.

    Семейства без правила не проверяются: отпечаток известен не для всех.
    """
    if not response_id:
        return
    rule = _ID_RULES.get(vendor_of(model))
    if rule is not None and not rule.search(str(response_id)):
        raise LLMIdentityMismatch(
            f"id ответа {str(response_id)[:24]!r} не похож на {vendor_of(model)}: "
            f"под меткой {model} отвечала другая модель"
        )


def check_opening(model: str, text: str) -> None:
    """Поднять `LLMIdentityMismatch`, если начало ответа выдаёт чужую модель."""
    vendor = vendor_of(model)
    own = _VENDOR_NAMES.get(vendor)
    m = _SELF_INTRO.search(text)
    if m and own and not re.fullmatch(own, m.group("name"), re.IGNORECASE):
        raise LLMIdentityMismatch(
            f"модель под меткой {model} назвалась «{m.group('name')}»"
        )
    if _ROLE_REFUSAL.search(text):
        raise LLMIdentityMismatch(f"модель под меткой {model} отказалась от роли")


class OpeningSniffer:
    """Придержать начало потока до проверки самоназвания.

    Отдаёт накопленное разом, как только набралось `SNIFF_CHARS` символов или
    поток кончился, — и дальше пропускает без задержки.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self._held = ""
        self._decided = False

    def feed(self, delta: str) -> str:
        if self._decided:
            return delta
        self._held += delta
        if len(self._held) < SNIFF_CHARS:
            return ""
        return self._decide()

    def finish(self) -> str:
        if self._decided:
            return ""
        return self._decide()

    def _decide(self) -> str:
        check_opening(self._model, self._held)
        self._decided = True
        held, self._held = self._held, ""
        return held
