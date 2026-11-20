"""
Tests for security: token encryption/decryption, no token leakage.
"""
import pytest
from app.services.instagram.token_manager import TokenManager


def test_token_encryption_and_decryption():
    mgr = TokenManager()
    plain_token = "EAABwzLIX564BAO7...mock_meta_access_token_secret"

    # Encrypt
    encrypted = mgr.encrypt_token(plain_token)
    assert encrypted != plain_token
    assert len(encrypted) > 20

    # Decrypt
    decrypted = mgr.decrypt_token(encrypted)
    assert decrypted == plain_token


def test_decrypt_empty_token():
    mgr = TokenManager()
    with pytest.raises(ValueError):
        mgr.decrypt_token(None)
    with pytest.raises(ValueError):
        mgr.decrypt_token("")


def test_decrypt_corrupted_token():
    mgr = TokenManager()
    with pytest.raises(RuntimeError):
        mgr.decrypt_token("not-a-valid-fernet-token-string")
