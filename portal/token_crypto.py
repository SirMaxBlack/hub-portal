from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def get_token_cipher() -> Fernet:
    return Fernet(settings.HUB_PORTAL_TOKEN_ENCRYPTION_KEY.encode("ascii"))


def encrypt_token(raw_token: str) -> str:
    value = (raw_token or "").strip().encode("utf-8")
    return get_token_cipher().encrypt(value).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return get_token_cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError, UnicodeDecodeError):
        return ""
