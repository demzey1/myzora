"""
tests/unit/test_tools.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Unit tests for tool executor (Phase 2).

Tests the ToolExecutor class and individual tool implementations:
  - Creator tracking (track/list)
  - Signal queries (get/explain)
  - Coin market data
  - Trade preview
  - Wallet linking
  - User preferences (get/set)

All tests use mocked database session (AsyncSession).
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.tools import ToolExecutor
from app.db.models import (
    CreatorWatchMode,
    TrackedCreator,
    UserPreferences,
    Recommendation,
)


@pytest.fixture
def mock_session():
    """Create a mocked AsyncSession."""
    return AsyncMock()


@pytest.fixture
def executor(mock_session):
    """Create a ToolExecutor instance with mocked session."""
    return ToolExecutor(telegram_user_id=123456, session=mock_session)


class TestTrackCreator:
    """Tests for track_creator tool."""

    async def test_track_creator_success(self, executor, mock_session):
        """Should track a new creator."""
        # Mock repo.get_by_user_and_handle to return None (not tracking yet)
        with patch("app.bot.tools.TrackedCreatorRepository") as MockRepo:
            mock_repo_instance = AsyncMock()
            MockRepo.return_value = mock_repo_instance
            mock_repo_instance.get_by_user_and_handle.return_value = None

            result = await executor.track_creator(
                {"x_username": "vitalik", "mode": "hybrid"}
            )

            assert result["success"] is True
            assert "vitalik" in result["data"]["message"]
            assert "hybrid" in result["data"]["message"]

    async def test_track_creator_already_tracking(self, executor, mock_session):
        """Should return message if already tracking creator."""
        # Mock find existing creator
        existing = MagicMock()
        existing.x_username = "vitalik"
        existing.mode.value = "hybrid"

        with patch("app.bot.tools.TrackedCreatorRepository") as MockRepo:
            mock_repo_instance = AsyncMock()
            MockRepo.return_value = mock_repo_instance
            mock_repo_instance.get_by_user_and_handle.return_value = existing

            result = await executor.track_creator(
                {"x_username": "vitalik", "mode": "hybrid"}
            )

            assert result["success"] is True
            assert "Already tracking" in result["data"]["message"]

    async def test_track_creator_missing_username(self, executor):
        """Should fail if x_username is empty."""
        result = await executor.track_creator({"x_username": "", "mode": "hybrid"})

        assert result["success"] is False
        assert "x_username is required" in result["error"]

    async def test_track_creator_invalid_mode(self, executor):
        """Should fail if mode is invalid."""
        result = await executor.track_creator(
            {"x_username": "vitalik", "mode": "invalid_mode"}
        )

        assert result["success"] is False
        assert "Invalid mode" in result["error"]


class TestListTrackedCreators:
    """Tests for list_tracked_creators tool."""

    async def test_list_tracked_creators_empty(self, executor, mock_session):
        """Should return empty list if no creators tracked."""
        with patch("app.bot.tools.TrackedCreatorRepository") as MockRepo:
            mock_repo_instance = AsyncMock()
            MockRepo.return_value = mock_repo_instance
            mock_repo_instance.get_active_for_user.return_value = []

            result = await executor.list_tracked_creators({})

            assert result["success"] is True
            assert result["data"]["count"] == 0
            assert result["data"]["creators"] == []

    async def test_list_tracked_creators_with_creators(self, executor, mock_session):
        """Should return list of tracked creators."""
        creator1 = MagicMock()
        creator1.x_username = "vitalik"
        creator1.mode.value = "hybrid"
        creator1.created_at = datetime.utcnow()

        creator2 = MagicMock()
        creator2.x_username = "satoshi"
        creator2.mode.value = "creator_only"
        creator2.created_at = datetime.utcnow()

        with patch("app.bot.tools.TrackedCreatorRepository") as MockRepo:
            mock_repo_instance = AsyncMock()
            MockRepo.return_value = mock_repo_instance
            mock_repo_instance.get_active_for_user.return_value = [creator1, creator2]

            result = await executor.list_tracked_creators({})

            assert result["success"] is True
            assert result["data"]["count"] == 2
            assert len(result["data"]["creators"]) == 2
            assert result["data"]["creators"][0]["username"] == "vitalik"
            assert result["data"]["creators"][1]["username"] == "satoshi"


class TestZoraSignals:
    """Tests for Zora signal query tools."""

    async def test_get_zora_signals_success(self, executor, mock_session):
        """Should return recent signals."""
        signal1 = MagicMock()
        signal1.id = 1
        signal1.coin.symbol = "TEST"
        signal1.final_score = 75
        signal1.recommendation = Recommendation.ALERT
        signal1.created_at = datetime.utcnow()

        with patch("app.bot.tools.SignalRepository") as MockRepo:
            mock_repo_instance = AsyncMock()
            MockRepo.return_value = mock_repo_instance
            mock_repo_instance.get_recent.return_value = [signal1]

            result = await executor.get_zora_signals(
                {"hours": 24, "min_score": 50}
            )

            assert result["success"] is True
            assert result["data"]["count"] == 1
            assert result["data"]["signals"][0]["coin_symbol"] == "TEST"
            assert result["data"]["signals"][0]["score"] == 75

    async def test_explain_signal_success(self, executor, mock_session):
        """Should explain signal scoring."""
        signal = MagicMock()
        signal.id = 1
        signal.coin.symbol = "TEST"
        signal.deterministic_score = 70
        signal.llm_score = 80
        signal.final_score = 75
        signal.recommendation = Recommendation.ALERT
        signal.risk_notes = None

        with patch("app.bot.tools.SignalRepository") as MockRepo:
            mock_repo_instance = AsyncMock()
            MockRepo.return_value = mock_repo_instance
            mock_repo_instance.get.return_value = signal

            result = await executor.explain_signal({"signal_id": 1})

            assert result["success"] is True
            assert result["data"]["final_score"] == 75
            assert "📊 Score Breakdown" in result["data"]["explanation"]

    async def test_explain_signal_not_found(self, executor, mock_session):
        """Should fail if signal not found."""
        with patch("app.bot.tools.SignalRepository") as MockRepo:
            mock_repo_instance = AsyncMock()
            MockRepo.return_value = mock_repo_instance
            mock_repo_instance.get.return_value = None

            result = await executor.explain_signal({"signal_id": 999})

            assert result["success"] is False
            assert "not found" in result["error"]


class TestCoinMarketData:
    """Tests for coin market data queries."""

    async def test_get_coin_market_state_success(self, executor, mock_session):
        """Should return coin market data."""
        coin = MagicMock()
        coin.symbol = "TEST"
        coin.id = 1

        snapshot = MagicMock()
        snapshot.price_usd = 0.0234
        snapshot.liquidity_usd = 45000
        snapshot.volume_5m_usd = 12000
        snapshot.market_cap_usd = 1000000
        snapshot.holder_count = 150
        snapshot.captured_at = datetime.utcnow()

        with patch("app.bot.tools.ZoraCoinRepository") as MockCoinRepo:
            mock_coin_repo = AsyncMock()
            MockCoinRepo.return_value = mock_coin_repo
            mock_coin_repo.get_by_symbol.return_value = coin

            with patch("app.bot.tools.CoinMarketSnapshotRepository") as MockSnapRepo:
                mock_snap_repo = AsyncMock()
                MockSnapRepo.return_value = mock_snap_repo
                mock_snap_repo.get_latest_for_coin.return_value = snapshot

                result = await executor.get_coin_market_state(
                    {"coin_symbol": "TEST"}
                )

                assert result["success"] is True
                assert result["data"]["symbol"] == "TEST"
                assert result["data"]["price_usd"] == 0.0234
                assert result["data"]["liquidity_usd"] == 45000

    async def test_get_coin_market_state_not_found(self, executor, mock_session):
        """Should fail if coin not found."""
        with patch("app.bot.tools.ZoraCoinRepository") as MockCoinRepo:
            mock_coin_repo = AsyncMock()
            MockCoinRepo.return_value = mock_coin_repo
            mock_coin_repo.get_by_symbol.return_value = None

            result = await executor.get_coin_market_state(
                {"coin_symbol": "NONEXISTENT"}
            )

            assert result["success"] is False
            assert "not found" in result["error"]


class TestTradingTools:
    """Tests for trading-related tools."""

    async def test_preview_trade_success(self, executor, mock_session):
        """Should preview a trade with estimates."""
        coin = MagicMock()
        coin.symbol = "TEST"
        coin.id = 1

        snapshot = MagicMock()
        snapshot.price_usd = 0.0234
        snapshot.liquidity_usd = 45000

        with patch("app.bot.tools.ZoraCoinRepository") as MockCoinRepo:
            mock_coin_repo = AsyncMock()
            MockCoinRepo.return_value = mock_coin_repo
            mock_coin_repo.get_by_symbol.return_value = coin

            with patch("app.bot.tools.CoinMarketSnapshotRepository") as MockSnapRepo:
                mock_snap_repo = AsyncMock()
                MockSnapRepo.return_value = mock_snap_repo
                mock_snap_repo.get_latest_for_coin.return_value = snapshot

                result = await executor.preview_trade(
                    {"coin_symbol": "TEST", "action": "buy", "amount_usd": 100}
                )

                assert result["success"] is True
                assert result["data"]["coin"] == "TEST"
                assert result["data"]["action"] == "buy"
                assert result["data"]["amount_usd"] == 100
                assert "estimated_slippage_bps" in result["data"]

    async def test_execute_trade_gated(self, executor, mock_session):
        """Should fail for execute_trade (gated feature)."""
        result = await executor.execute_trade(
            {"coin_symbol": "TEST", "action": "buy", "amount_usd": 100}
        )

        assert result["success"] is False
        assert "wallet linking" in result["error"].lower()

    async def test_start_wallet_link_success(self, executor, mock_session):
        """Should initiate wallet linking."""
        with patch("app.bot.tools.create_link_session") as MockCreateSession:
            MockCreateSession.return_value = "session-token-123"

            with patch("app.bot.tools.settings") as mock_settings:
                mock_settings.wallet_link_base_url = "http://localhost:8000"
                mock_settings.wallet_nonce_ttl_seconds = 300

                result = await executor.start_wallet_link({})

                assert result["success"] is True
                assert "session-token-123" in result["data"]["link"]
                assert result["data"]["expires_seconds"] == 300


class TestUserPreferences:
    """Tests for user preference tools."""

    async def test_get_user_preferences_empty(self, executor, mock_session):
        """Should return empty preferences dict if none set."""
        with patch("app.bot.tools.select") as mock_select:
            mock_session.execute.return_value.scalars.return_value.all.return_value = []

            result = await executor.get_user_preferences({})

            assert result["success"] is True
            assert result["data"]["preferences"] == {}

    async def test_get_user_preferences_with_data(self, executor, mock_session):
        """Should return user preferences."""
        pref1 = MagicMock()
        pref1.preference_key = "mode"
        pref1.preference_value = "hybrid"

        pref2 = MagicMock()
        pref2.preference_key = "risk"
        pref2.preference_value = "medium"

        with patch("app.bot.tools.select") as mock_select:
            mock_session.execute.return_value.scalars.return_value.all.return_value = [
                pref1,
                pref2,
            ]

            result = await executor.get_user_preferences({})

            assert result["success"] is True
            assert result["data"]["preferences"]["mode"] == "hybrid"
            assert result["data"]["preferences"]["risk"] == "medium"

    async def test_update_user_preferences_new(self, executor, mock_session):
        """Should create new preferences."""
        with patch("app.bot.tools.select") as mock_select:
            mock_session.execute.return_value.scalar_one_or_none.return_value = None

            result = await executor.update_user_preferences(
                {"preferences": {"mode": "creator_only"}}
            )

            assert result["success"] is True
            assert result["data"]["count"] == 1
            mock_session.add.assert_called_once()
            mock_session.commit.assert_called_once()

    async def test_update_user_preferences_existing(self, executor, mock_session):
        """Should update existing preferences."""
        existing_pref = MagicMock()

        with patch("app.bot.tools.select") as mock_select:
            mock_session.execute.return_value.scalar_one_or_none.return_value = (
                existing_pref
            )

            result = await executor.update_user_preferences(
                {"preferences": {"mode": "keyword_only"}}
            )

            assert result["success"] is True
            assert existing_pref.preference_value == "keyword_only"
            mock_session.commit.assert_called_once()


class TestPositionManagement:
    """Tests for position tracking tools."""

    async def test_get_position_status_empty(self, executor, mock_session):
        """Should return empty positions if none open."""
        with patch("app.bot.tools.PaperPositionRepository") as MockRepo:
            mock_repo_instance = AsyncMock()
            MockRepo.return_value = mock_repo_instance
            mock_repo_instance.get_open.return_value = []

            result = await executor.get_position_status({})

            assert result["success"] is True
            assert result["data"]["count_open"] == 0
            assert result["data"]["positions"] == []

    async def test_get_position_status_with_positions(self, executor, mock_session):
        """Should return open positions."""
        pos1 = MagicMock()
        pos1.id = 1
        pos1.coin.symbol = "TEST"
        pos1.size_usd = 100
        pos1.entry_price_usd = 0.0234
        pos1.opened_at = datetime.utcnow()

        with patch("app.bot.tools.PaperPositionRepository") as MockRepo:
            mock_repo_instance = AsyncMock()
            MockRepo.return_value = mock_repo_instance
            mock_repo_instance.get_open.return_value = [pos1]

            result = await executor.get_position_status({})

            assert result["success"] is True
            assert result["data"]["count_open"] == 1
            assert result["data"]["positions"][0]["coin"] == "TEST"
            assert result["data"]["positions"][0]["size_usd"] == 100


class TestToolUnknown:
    """Tests for unknown tools."""

    async def test_unknown_tool(self, executor):
        """Should fail for unknown tool."""
        result = await executor.execute("unknown_tool", {})

        assert result["success"] is False
        assert "Unknown tool" in result["error"]
