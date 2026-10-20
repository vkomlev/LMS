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
     google/*     `chatcmpl-<uuid>`; с 12.10 — `chatcmpl-<27 цифр><буквы>` (tsk-1321);
                  родной `responseId` base64url (tsk-1376)
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
    # С 12.10 Google отдаёт `chatcmpl-<27 цифр><буквы>` без дефисов (tsk-1321: 20 из
    # 20 вызовов gemini-3.6/3.7 назвались Gemini/Google; прежний uuid-вид не пришёл
    # ни разу, и голову наставника задвинули как «подмену»). Отличие от OpenAI —
    # хвост из букв после длинного числа: у gpt-5.5 там 19 цифр и ничего больше.
    #
    # Третий вид — родной `responseId` Gemini API: base64url на 20–28 символов
    # (`ZATWap2aM7rLjuMP0YLf2A4`). Проба 19.10 (tsk-1376): 6 из 6 потоков gemini-3.7
    # пришли с таким id, 17 входных токенов на вопрос (без подмешанной инструкции),
    # самоназвание «Gemini… Google». Неделю 12–19.10 такие ответы отбраковывались как
    # подмена — 10 из 17 отказов LLMIdentityMismatch, часть разговоров без ответа.
    # Обязателен хотя бы один не-hex символ: 24 hex-символа — отпечаток Grok. Чужие
    # префиксы (`msg_`, `resp_`, `chatcmpl-`) тоже укладываются в base64url — их
    # исключаем явно: `msg_<hex>` под gemini-3.6 на пробе назывался YandexGPT/ChatGPT.
    "google": re.compile(
        r"^(?:chatcmpl-(?:[0-9a-f]{8}-|\d{20,}[A-Za-z])"
        r"|(?!chatcmpl-|msg_|resp_)(?=[A-Za-z0-9_-]{20,28}$)[A-Za-z0-9_-]*[g-zG-Z_-])"
    ),
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
    r"(?P<name>claude|anthropic|gpt|chatgpt|openai|codex|gemini|google|grok|xai|x\.ai)",
    re.IGNORECASE,
)
# Отказ от роли наставника — тоже признак чужой модели или подмешанной инструкции.
# tsk-1376 (разбор 20.10, сессия 249): gpt-5.5 маршрутом с подмешанной инструкцией
# Codex ответила ученику «не могу продолжить разговор в этом формате… противоречат
# моим базовым принципам» — это отказ от роли, а не реплика наставника.
_ROLE_REFUSAL = re.compile(
    r"не\s+(?:могу|буду)\s+(?:принять|играть|исполнять|взять)\s+(?:на\s+себя\s+)?(?:эту\s+)?рол"
    r"|не\s+(?:могу|буду)\s+продолжа?\w*\s+(?:этот\s+|наш\s+)?разговор"
    r"|противореч\w*\s+моим\s+(?:базовым\s+|основным\s+)?принцип",
    re.IGNORECASE,
)


def vendor_of(model: str) -> str:
    """Семейство модели по метке: `openai/gpt-5.5` → `openai`.

    Прямой API (`anthropic-direct/…`, tsk-1259) — то же семейство anthropic:
    отпечаток `msg_` и самоназвание Claude проверяются так же.
    """
    vendor = (model or "").split("/", 1)[0].lower()
    return "anthropic" if vendor == "anthropic-direct" else vendor


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
