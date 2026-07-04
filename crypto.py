"""AES-256 шифрование敏感ных полей БД через Fernet (cryptography)."""

import os, base64, logging, secrets, string
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

def generate_dispute_id() -> str:
    alphabet = string.ascii_letters + string.digits + "$#@!%&"
    return ''.join(secrets.choice(alphabet) for _ in range(12))


def is_encryption_enabled() -> bool:
    return bool(_ENCRYPTION_KEY)


def _get_fernet():
    if not _ENCRYPTION_KEY:
        raise RuntimeError("ENCRYPTION_KEY не задан в .env")
    try:
        from cryptography.fernet import Fernet
        key = _ENCRYPTION_KEY.encode() if not _ENCRYPTION_KEY.endswith("=") else _ENCRYPTION_KEY.encode()
        if len(key) != 44:
            key = base64.urlsafe_b64encode(key.ljust(32, b'\0')[:32])
        return Fernet(key)
    except Exception as e:
        logger.error(f"Fernet init failed: {e}")
        raise


def encrypt_value(value: str) -> str:
    if not value:
        return ""
    if not _ENCRYPTION_KEY:
        return value
    try:
        f = _get_fernet()
        return f.encrypt(value.encode()).decode()
    except Exception as e:
        logger.error(f"Encrypt error: {e}")
        return value


def decrypt_value(value: str) -> str:
    if not value:
        return ""
    if not _ENCRYPTION_KEY:
        return value
    try:
        f = _get_fernet()
        return f.decrypt(value.encode()).decode()
    except Exception as e:
        return value
