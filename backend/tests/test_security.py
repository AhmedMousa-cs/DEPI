import jwt
import pytest

from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h)


def test_wrong_password_rejected():
    assert not verify_password("wrong", hash_password("secret123"))


def test_garbage_hash_rejected():
    assert not verify_password("x", "not-a-hash")


def test_token_roundtrip():
    token = create_access_token(5, "PM")
    data = decode_access_token(token)
    assert data["sub"] == "5"
    assert data["role"] == "PM"


def test_tampered_token_rejected():
    token = create_access_token(5, "PM")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token[:-2] + "xx")