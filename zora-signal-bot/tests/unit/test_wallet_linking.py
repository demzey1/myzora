"""
tests/unit/test_wallet_linking.py
Tests for wallet linking service: session creation, nonce management,
signature verification, and replay attack prevention.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

import pytest

from app.services.wallet_linking import (
    _make_nonce,
    _verify_eip191,
    build_wallet_link_url,
    verify_session_url_signature,
)


# ── Nonce generation ──────────────────────────────────────────────────────────

def test_nonce_contains_prefix():
    nonce = _make_nonce(telegram_user_id=12345, session_token="abc123")
    assert "ZoraSignalBot" in nonce


def test_nonce_contains_session_prefix():
    nonce = _make_nonce(telegram_user_id=12345, session_token="deadbeef1234")
    assert "deadbeef" in nonce


def test_nonce_unique_per_call():
    n1 = _make_nonce(12345, "token1")
    n2 = _make_nonce(12345, "token2")
    assert n1 != n2


def test_nonce_contains_expiry_info():
    nonce = _make_nonce(12345, "tok")
    assert "expire" in nonce.lower()


# ── Session URL signing ───────────────────────────────────────────────────────

def test_build_wallet_link_url_format():
    with patch("app.services.wallet_linking.settings") as ms:
        ms.wallet_link_base_url = "http://localhost:8000"
        ms.wallet_link_secret = None
        url = build_wallet_link_url("mytoken123")
    assert "mytoken123" in url
    assert "/wallet/connect" in url
    assert "sig=" in url


def test_verify_session_url_signature_valid():
    with patch("app.services.wallet_linking.settings") as ms:
        ms.wallet_link_secret = None
        url = build_wallet_link_url("testtoken")
    # Extract sig from URL
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(url).query)
    token = qs["session"][0]
    sig = qs["sig"][0]
    with patch("app.services.wallet_linking.settings") as ms:
        ms.wallet_link_secret = None
        assert verify_session_url_signature(token, sig) is True


def test_verify_session_url_signature_tampered():
    with patch("app.services.wallet_linking.settings") as ms:
        ms.wallet_link_secret = None
        assert verify_session_url_signature("token", "wrong_sig") is False


# ── EIP-191 signature verification ────────────────────────────────────────────

def test_verify_eip191_missing_eth_account():
    """Should fail gracefully when eth_account not installed (mock import error)."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "eth_account":
            raise ImportError("mocked")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        ok, err = _verify_eip191("test message", "0xsig", "0xaddress")
        assert ok is False
        assert "eth-account" in err


def test_verify_eip191_bad_signature_format():
    """Malformed signature should return False without crashing."""
    try:
        from eth_account import Account  # noqa: F401
    except ImportError:
        pytest.skip("eth_account not installed")

    ok, err = _verify_eip191("test message", "0xinvalid", "0xABCDEF")
    assert ok is False
    assert len(err) > 0


# ── DB-backed flows (using in-memory SQLite) ──────────────────────────────────

@pytest.mark.asyncio
async def test_create_link_session_returns_url(db_session):
    from app.services.wallet_linking import create_link_session
    with patch("app.services.wallet_linking.settings") as ms:
        ms.wallet_link_base_url = "http://localhost:8000"
        ms.wallet_link_secret = None
        ms.wallet_nonce_ttl_seconds = 300
        url = await create_link_session(db_session, telegram_user_id=99999)

    assert "http://localhost:8000/wallet/connect" in url
    assert "session=" in url


@pytest.mark.asyncio
async def test_set_nonce_address_valid_session(db_session):
    from app.services.wallet_linking import create_link_session, set_nonce_address
    with patch("app.services.wallet_linking.settings") as ms:
        ms.wallet_link_base_url = "http://localhost:8000"
        ms.wallet_link_secret = None
        ms.wallet_nonce_ttl_seconds = 300
        url = await create_link_session(db_session, telegram_user_id=11111)

    from urllib.parse import urlparse, parse_qs
    session_token = parse_qs(urlparse(url).query)["session"][0]

    nonce = await set_nonce_address(db_session, session_token, "0xTestWallet1234")
    assert nonce is not None
    assert "ZoraSignalBot" in nonce


@pytest.mark.asyncio
async def test_set_nonce_address_expired_session(db_session):
    from app.db.models import WalletLinkNonce
    from app.services.wallet_linking import set_nonce_address

    # Insert an already-expired nonce
    expired_nonce = WalletLinkNonce(
        session_token="expiredtoken123",
        telegram_user_id=22222,
        nonce="ZoraSignalBot test nonce",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db_session.add(expired_nonce)
    await db_session.flush()

    result = await set_nonce_address(db_session, "expiredtoken123", "0xWallet")
    assert result is None


@pytest.mark.asyncio
async def test_used_nonce_cannot_be_reused(db_session):
    from app.db.models import WalletLinkNonce
    from app.services.wallet_linking import set_nonce_address

    used_nonce = WalletLinkNonce(
        session_token="usedtoken456",
        telegram_user_id=33333,
        nonce="ZoraSignalBot test nonce",
        is_used=True,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db_session.add(used_nonce)
    await db_session.flush()

    result = await set_nonce_address(db_session, "usedtoken456", "0xWallet")
    assert result is None
