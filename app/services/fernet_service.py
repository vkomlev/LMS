"""Сервис Fernet-шифрования для хранения токенов VK."""
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings

logger = logging.getLogger(__name__)


def _get_fernet(settings: Settings) -> Fernet:
    """
    Ожидает FERNET_MASTER_KEY = результат Fernet.generate_key() — 44-char base64url строка.
    Передаётся в Fernet напрямую без повторного кодирования.
    """
    key = settings.fernet_master_key
    if not key:
        raise RuntimeError("FERNET_MASTER_KEY не задан")
    raw = key.encode() if isinstance(key, str) else key
    return Fernet(raw)


def encrypt_token(plaintext: str, settings: Settings) -> bytes:
    """Зашифровать строку токена в bytes."""
    f = _get_fernet(settings)
    return f.encrypt(plaintext.encode("utf-8"))


def decrypt_token(ciphertext: bytes, settings: Settings) -> str | None:
    """Расшифровать bytes обратно в строку; вернуть None при невалидном токене."""
    try:
        f = _get_fernet(settings)
        return f.decrypt(ciphertext).decode("utf-8")
    except (InvalidToken, Exception):
        logger.warning("Fernet decrypt failed")
        return None
