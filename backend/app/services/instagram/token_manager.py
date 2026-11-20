"""
Token Manager – encrypts and decrypts Instagram access tokens.
Tokens are NEVER stored in plaintext. NEVER logged.
Uses Fernet symmetric encryption.
"""
from __future__ import annotations
import base64
import os
from typing import Optional

from app.core.config import settings


class TokenManager:
    """Encrypts/decrypts Instagram OAuth tokens for secure DB storage."""

    def _get_key(self) -> bytes:
        key = settings.token_encryption_key
        if not key:
            # Derive from SECRET_KEY if no dedicated key (not ideal for production)
            import hashlib
            key_bytes = hashlib.sha256(settings.secret_key.encode()).digest()
            return base64.urlsafe_b64encode(key_bytes)
        return key.encode() if isinstance(key, str) else key

    def encrypt_token(self, plaintext_token: str) -> str:
        """Encrypt an access token for database storage."""
        try:
            from cryptography.fernet import Fernet
            f = Fernet(self._get_key())
            encrypted = f.encrypt(plaintext_token.encode())
            return encrypted.decode()
        except Exception:
            raise RuntimeError("Token encryption failed. Check TOKEN_ENCRYPTION_KEY configuration.")

    def decrypt_token(self, encrypted_token: Optional[str]) -> str:
        """Decrypt a stored token for API use. Never log the result."""
        if not encrypted_token:
            raise ValueError("No token available")
        try:
            from cryptography.fernet import Fernet
            f = Fernet(self._get_key())
            decrypted = f.decrypt(encrypted_token.encode())
            return decrypted.decode()
        except Exception:
            raise RuntimeError("Token decryption failed. Token may be corrupt or key mismatch.")
