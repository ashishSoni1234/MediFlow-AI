"""Unit tests for password hashing (bcrypt) and JWT issuance/verification.

Run with: pytest agent_service/tests
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt as pyjwt  # noqa: E402
import pytest  # noqa: E402

from auth import create_access_token, decode_access_token, hash_password, verify_password  # noqa: E402


def test_correct_password_verifies():
    stored = hash_password("mediflow123")
    assert verify_password("mediflow123", stored) is True


def test_wrong_password_fails():
    stored = hash_password("mediflow123")
    assert verify_password("wrong-password", stored) is False


def test_each_hash_gets_a_fresh_random_salt():
    a = hash_password("mediflow123")
    b = hash_password("mediflow123")
    assert a != b
    assert verify_password("mediflow123", a) is True
    assert verify_password("mediflow123", b) is True


def test_malformed_stored_hash_fails_closed():
    assert verify_password("mediflow123", "not-a-valid-hash") is False


def test_access_token_round_trips_claims():
    token = create_access_token(user_id=1, role="doctor", linked_id=2, name="Dr. Ahuja")
    claims = decode_access_token(token)
    assert claims["user_id"] == 1
    assert claims["role"] == "doctor"
    assert claims["linked_id"] == 2
    assert claims["name"] == "Dr. Ahuja"


def test_tampered_token_fails_to_decode():
    token = create_access_token(user_id=1, role="patient", linked_id=5, name="Aarav Sharma")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(tampered)
