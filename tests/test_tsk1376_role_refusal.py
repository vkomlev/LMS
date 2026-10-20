"""tsk-1376 (разбор 20.10): отказ от роли и самоназвание Codex не доходят до ученика."""
import pytest

from app.services.llm.identity import LLMIdentityMismatch, OpeningSniffer, check_opening

# Сообщение 1142, сессия 249: gpt-5.5 маршрутом с подмешанной инструкцией Codex.
SESSION_249 = (
    "Я не могу продолжить разговор в этом формате, если он требует от меня следовать "
    "набору ролей без правил и инструкций, которые противоречат моим базовым принципам."
)


@pytest.mark.parametrize("text", [
    SESSION_249,
    "Извини, но эти указания противоречат моим принципам.",
    "Я не буду продолжать этот разговор.",
    "Я — Codex, помощник по программированию.",
    "I'm Codex, a coding agent.",
])
def test_refusal_and_codex_rejected(text: str) -> None:
    """Отказ и чужое самоназвание под меткой gpt — ход уходит следующему звену."""
    with pytest.raises(LLMIdentityMismatch):
        check_opening("openai/gpt-5.5", text)


@pytest.mark.parametrize("text", [
    "Не могу дать готовое решение, но давай разберём первый шаг: что дано в условии?",
    "Давай продолжим: какой следующий шаг ты бы сделал?",
    "Хороший вопрос! Принцип простой: делим размер на 2 байта.",
    "Я не могу проверить твой код без запуска, но посмотри на строку с циклом.",
])
def test_normal_tutor_replies_pass(text: str) -> None:
    """Обычные реплики наставника, включая отказ дать решение, не отбраковываются."""
    check_opening("openai/gpt-5.5", text)
    check_opening("google/gemini-3.7-flash", text)


def test_sniffer_holds_session_249_refusal() -> None:
    """Поток: отказ из 249 не выпускается ученику, а поднимает подмену."""
    sniffer = OpeningSniffer("openai/gpt-5.5")
    with pytest.raises(LLMIdentityMismatch):
        out = sniffer.feed(SESSION_249[:120])
        assert out == ""
        sniffer.feed(SESSION_249[120:])
        sniffer.finish()
