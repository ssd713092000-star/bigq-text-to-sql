"""Encryption for stored Google refresh tokens, and session-token helpers."""

from __future__ import annotations

import hashlib
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings


class TokenKeyError(RuntimeError):
    pass


@lru_cache
def _fernet() -> MultiFernet:
    keys = [k.strip() for k in get_settings().token_encryption_key.split(",") if k.strip()]
    if not keys:
        raise TokenKeyError("TOKEN_ENCRYPTION_KEY is not set")
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except ValueError as e:
        raise TokenKeyError("TOKEN_ENCRYPTION_KEY must be url-safe base64 of 32 bytes (Fernet.generate_key())") from e


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    try:
        return _fernet().decrypt(ciphertext).decode()
    except InvalidToken as e:
        raise TokenKeyError("stored token can't be decrypted with the configured key(s)") from e


def new_session_token() -> tuple[str, str]:
    """(token for the client, sha256 hex to store)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def short_id() -> str:
    """8 lowercase base32-ish chars for corpus / personal-org names (user-<shortid>)."""
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))
