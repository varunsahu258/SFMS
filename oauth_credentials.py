"""Google OAuth token storage outside the SQLite database."""

from __future__ import annotations

import importlib
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ModuleNotFoundError:
    AESGCM = None

from config import BASE_DIR
from receipt_integrity import integrity_key

SERVICE_NAME = "SFMS_Google"
TOKEN_USERNAME = "oauth_token"
_FALLBACK_PATH = Path(BASE_DIR) / "google_oauth_token.enc"
_NONCE_SIZE = 12
_MAGIC = b"SFMSOAUTH1"


def _keyring():
    return importlib.import_module("keyring")


def _fallback_key() -> bytes:
    return integrity_key()[:32]


def _store_fallback(token_json: str) -> None:
    import os

    if AESGCM is None:
        return
    nonce = os.urandom(_NONCE_SIZE)
    encrypted = AESGCM(_fallback_key()).encrypt(nonce, token_json.encode("utf-8"), _MAGIC)
    _FALLBACK_PATH.write_bytes(_MAGIC + nonce + encrypted)


def _load_fallback() -> str | None:
    if AESGCM is None or not _FALLBACK_PATH.is_file():
        return None
    payload = _FALLBACK_PATH.read_bytes()
    if not payload.startswith(_MAGIC):
        return None
    nonce = payload[len(_MAGIC):len(_MAGIC) + _NONCE_SIZE]
    plaintext = AESGCM(_fallback_key()).decrypt(nonce, payload[len(_MAGIC) + _NONCE_SIZE:], _MAGIC)
    return plaintext.decode("utf-8")


def store_oauth_token(token_json: str) -> None:
    """Store the OAuth token in OS keyring, falling back to encrypted file storage."""
    try:
        _keyring().set_password(SERVICE_NAME, TOKEN_USERNAME, token_json)
    except Exception:
        _store_fallback(token_json)


def load_oauth_token() -> str | None:
    """Load the OAuth token from OS keyring or encrypted fallback file."""
    try:
        token = _keyring().get_password(SERVICE_NAME, TOKEN_USERNAME)
    except Exception:
        token = None
    return token or _load_fallback()
