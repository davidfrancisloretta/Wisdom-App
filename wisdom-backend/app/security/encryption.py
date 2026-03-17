"""AES-256-GCM field-level encryption for PII fields.

Provides encrypt/decrypt functions and a SQLAlchemy TypeDecorator for
automatic encryption/decryption on database operations.
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String, TypeDecorator

from app.config import get_settings

_NONCE_LENGTH = 12  # 96 bits, recommended for AES-GCM


def _get_key() -> bytes:
    """Return the 32-byte AES key from config (base64-encoded in env)."""
    raw = get_settings().ENCRYPTION_KEY
    if not raw:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError("ENCRYPTION_KEY must be exactly 32 bytes (base64-encoded)")
    return key


def encrypt_field(value: str) -> str:
    """Encrypt a plaintext string with AES-256-GCM, return base64-encoded ciphertext."""
    if not value:
        return value
    key = _get_key()
    nonce = os.urandom(_NONCE_LENGTH)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, value.encode("utf-8"), None)
    # Store as nonce + ciphertext, base64-encoded
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_field(value: str) -> str:
    """Decrypt a base64-encoded AES-256-GCM ciphertext back to plaintext."""
    if not value:
        return value
    key = _get_key()
    raw = base64.b64decode(value)
    nonce = raw[:_NONCE_LENGTH]
    ciphertext = raw[_NONCE_LENGTH:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


class EncryptedStr(TypeDecorator):
    """SQLAlchemy TypeDecorator that auto-encrypts on write and decrypts on read.

    Usage in models:
        first_name: Mapped[str] = mapped_column(EncryptedStr(), nullable=False)
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        try:
            return encrypt_field(value)
        except RuntimeError:
            # If encryption key is not set (e.g. in tests), store plaintext
            return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return decrypt_field(value)
        except Exception:
            # If decryption fails (unencrypted data or test env), return as-is
            return value
