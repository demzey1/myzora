"""
tests/unit/test_overrides.py
Tests for the CreatorOverride model and repository.
"""

from __future__ import annotations

import pytest

from app.db.models import CreatorOverride
from app.db.repositories.overrides import CreatorOverrideRepository


@pytest.mark.asyncio
async def test_blacklist_by_x_username(db_session):
    repo = CreatorOverrideRepository(db_session)
    override = CreatorOverride(
        x_username="spamcreator",
        is_blacklisted=True,
        reason="Known spam account",
        added_by=12345,
    )
    db_session.add(override)
    await db_session.flush()

    assert await repo.is_blacklisted(x_username="spamcreator") is True
    assert await repo.is_blacklisted(x_username="legitcreator") is False


@pytest.mark.asyncio
async def test_blacklist_by_contract_address(db_session):
    repo = CreatorOverrideRepository(db_session)
    override = CreatorOverride(
        contract_address="0xBADC0IN00000000000000000000000000000001",
        is_blacklisted=True,
    )
    db_session.add(override)
    await db_session.flush()

    assert await repo.is_blacklisted(
        contract_address="0xBADC0IN00000000000000000000000000000001"
    ) is True
    assert await repo.is_blacklisted(
        contract_address="0xGOODC0IN000000000000000000000000000001"
    ) is False


@pytest.mark.asyncio
async def test_score_multiplier_applied(db_session):
    repo = CreatorOverrideRepository(db_session)
    override = CreatorOverride(
        x_username="vipaccount",
        is_whitelisted=True,
        score_multiplier=1.5,
    )
    db_session.add(override)
    await db_session.flush()

    multiplier = await repo.get_score_multiplier(x_username="vipaccount")
    assert multiplier == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_default_multiplier_is_1(db_session):
    repo = CreatorOverrideRepository(db_session)
    multiplier = await repo.get_score_multiplier(x_username="nobody")
    assert multiplier == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_list_all_returns_overrides(db_session):
    repo = CreatorOverrideRepository(db_session)
    db_session.add_all([
        CreatorOverride(x_username="acc1", is_blacklisted=True),
        CreatorOverride(x_username="acc2", is_whitelisted=True, score_multiplier=1.2),
    ])
    await db_session.flush()

    all_overrides = await repo.list_all()
    usernames = {o.x_username for o in all_overrides}
    assert "acc1" in usernames
    assert "acc2" in usernames


@pytest.mark.asyncio
async def test_not_blacklisted_when_no_override(db_session):
    repo = CreatorOverrideRepository(db_session)
    result = await repo.is_blacklisted(x_username="cleanaccount")
    assert result is False


@pytest.mark.asyncio
async def test_get_for_account_returns_none_without_scope(db_session):
    repo = CreatorOverrideRepository(db_session)
    result = await repo.get_for_account(None, None)
    assert result is None
