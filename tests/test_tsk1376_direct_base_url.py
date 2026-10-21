"""tsk-1376: адрес прямого API Anthropic переопределяется окружением (выход через тоннель)."""
from app.services.llm import anthropic_direct, providers


def test_default_base_url(monkeypatch) -> None:
    """Без переменной — официальный адрес."""
    monkeypatch.delenv("ANTHROPIC_DIRECT_BASE_URL", raising=False)
    assert anthropic_direct.base_url() == "https://api.anthropic.com"


def test_override_base_url_used_by_provider(monkeypatch) -> None:
    """Переменная задаёт адрес; хвостовой слэш срезается; ключ — из окружения сервера."""
    monkeypatch.setenv("ANTHROPIC_DIRECT_BASE_URL", "http://127.0.0.1:18443/")
    monkeypatch.setenv("ANTHROPIC_DIRECT_ENABLED", "true")
    monkeypatch.setenv("CLAUDE_API_KEY", "test-key")
    cfg = providers.resolve_for("anthropic-direct/claude-haiku-5-5")
    assert cfg.base_url == "http://127.0.0.1:18443"
    assert cfg.api_key == "test-key"
