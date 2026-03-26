"""
tests/unit/test_live_execution.py
Tests for the live execution layer — safety gates and dry-run paths.
All on-chain calls are mocked; no real RPC needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.trading.live_execution import (
    InsufficientBalanceError,
    LiveExecutionDisabledAdapter,
    LiveTradingDisabledError,
    SlippageBreachError,
    ZoraOnChainAdapter,
)


# ── LiveExecutionDisabledAdapter ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_adapter_buy_raises():
    adapter = LiveExecutionDisabledAdapter()
    with pytest.raises(LiveTradingDisabledError):
        await adapter.execute_buy("0xABCD", 100.0, 200)


@pytest.mark.asyncio
async def test_disabled_adapter_sell_raises():
    adapter = LiveExecutionDisabledAdapter()
    with pytest.raises(LiveTradingDisabledError):
        await adapter.execute_sell("0xABCD", 1000.0, 200)


@pytest.mark.asyncio
async def test_disabled_adapter_balance_raises():
    adapter = LiveExecutionDisabledAdapter()
    with pytest.raises(LiveTradingDisabledError):
        await adapter.get_wallet_balance_usd()


# ── ZoraOnChainAdapter (dry-run paths) ────────────────────────────────────────

CONTRACT = "0xTEST0000000000000000000000000000001234"
RPC_URL   = "https://mainnet.base.org"


@pytest.fixture
def adapter():
    return ZoraOnChainAdapter(rpc_url=RPC_URL)


def _mock_rpc(result):
    """Return a mock for ZoraOnChainAdapter._rpc that returns `result`."""
    return AsyncMock(return_value=result)


@pytest.mark.asyncio
async def test_dry_run_buy_passes_with_sufficient_balance(adapter):
    with (
        patch.object(adapter, "_get_eth_balance", AsyncMock(return_value=int(2e18))),  # 2 ETH
        patch.object(adapter, "_get_eth_price_usd", AsyncMock(return_value=3000.0)),
        patch.object(adapter, "_simulate_swap", AsyncMock(return_value={
            "expected_output": 100000,
            "estimated_slippage_bps": 80,
            "gas_estimate": 150_000,
            "simulated": True,
        })),
        patch("app.trading.live_execution.settings") as mock_settings,
    ):
        mock_settings.wallet_address = "0xWALLET00000000000000000000000000000001"
        mock_settings.max_slippage_bps = 200
        result = await adapter.execute_buy(
            CONTRACT, size_usd=50.0, max_slippage_bps=200, dry_run=True
        )
    assert result["dry_run"] is True
    assert result["tx_hash"] is None
    assert result["actual_slippage_bps"] == 80


@pytest.mark.asyncio
async def test_dry_run_buy_raises_on_insufficient_balance(adapter):
    with (
        patch.object(adapter, "_get_eth_balance", AsyncMock(return_value=int(0.001e18))),  # 0.001 ETH
        patch.object(adapter, "_get_eth_price_usd", AsyncMock(return_value=3000.0)),
        patch("app.trading.live_execution.settings") as mock_settings,
    ):
        mock_settings.wallet_address = "0xWALLET00000000000000000000000000000001"
        with pytest.raises(InsufficientBalanceError):
            await adapter.execute_buy(CONTRACT, 100.0, 200, dry_run=True)


@pytest.mark.asyncio
async def test_dry_run_buy_raises_on_slippage_breach(adapter):
    with (
        patch.object(adapter, "_get_eth_balance", AsyncMock(return_value=int(10e18))),
        patch.object(adapter, "_get_eth_price_usd", AsyncMock(return_value=3000.0)),
        patch.object(adapter, "_simulate_swap", AsyncMock(return_value={
            "expected_output": 100000,
            "estimated_slippage_bps": 400,  # exceeds 200 limit
            "gas_estimate": 150_000,
            "simulated": True,
        })),
        patch("app.trading.live_execution.settings") as mock_settings,
    ):
        mock_settings.wallet_address = "0xWALLET00000000000000000000000000000001"
        with pytest.raises(SlippageBreachError):
            await adapter.execute_buy(CONTRACT, 50.0, max_slippage_bps=200, dry_run=True)


@pytest.mark.asyncio
async def test_dry_run_buy_raises_on_zero_address(adapter):
    from app.trading.live_execution import LiveTradingExecutionError
    with patch("app.trading.live_execution.settings") as mock_settings:
        mock_settings.wallet_address = "0xWALLET00000000000000000000000000000001"
        with pytest.raises(LiveTradingExecutionError):
            await adapter.execute_buy("0x" + "0" * 40, 50.0, 200, dry_run=True)


@pytest.mark.asyncio
async def test_live_buy_raises_not_implemented(adapter):
    """Attempting dry_run=False must raise until TODO_CHAIN is implemented."""
    from app.trading.live_execution import LiveTradingExecutionError
    with (
        patch.object(adapter, "_get_eth_balance", AsyncMock(return_value=int(10e18))),
        patch.object(adapter, "_get_eth_price_usd", AsyncMock(return_value=3000.0)),
        patch.object(adapter, "_simulate_swap", AsyncMock(return_value={
            "expected_output": 100000,
            "estimated_slippage_bps": 80,
            "gas_estimate": 150_000,
            "simulated": True,
        })),
        patch("app.trading.live_execution.settings") as mock_settings,
    ):
        mock_settings.wallet_address = "0xWALLET00000000000000000000000000000001"
        with pytest.raises(LiveTradingExecutionError, match="TODO_CHAIN"):
            await adapter.execute_buy(CONTRACT, 50.0, 200, dry_run=False)


@pytest.mark.asyncio
async def test_dry_run_sell_returns_result(adapter):
    result = await adapter.execute_sell(CONTRACT, 1000.0, 200, dry_run=True)
    assert result["dry_run"] is True
    assert result["tx_hash"] is None


@pytest.mark.asyncio
async def test_wallet_balance_converts_eth_to_usd(adapter):
    with (
        patch.object(adapter, "_get_eth_balance", AsyncMock(return_value=int(1e18))),  # 1 ETH
        patch.object(adapter, "_get_eth_price_usd", AsyncMock(return_value=3000.0)),
        patch("app.trading.live_execution.settings") as mock_settings,
    ):
        mock_settings.wallet_address = "0xWALLET00000000000000000000000000000001"
        balance = await adapter.get_wallet_balance_usd()
    assert balance == pytest.approx(3000.0)


# ── Factory ───────────────────────────────────────────────────────────────────

def test_factory_returns_disabled_when_live_off():
    from app.trading.live_execution import get_live_adapter
    import app.trading.live_execution as le_mod
    le_mod._live_adapter = None  # reset singleton
    with patch("app.trading.live_execution.settings") as ms:
        ms.live_trading_enabled = False
        adapter = get_live_adapter()
        assert isinstance(adapter, LiveExecutionDisabledAdapter)
    le_mod._live_adapter = None


def test_factory_returns_zora_adapter_when_live_on():
    from app.trading.live_execution import get_live_adapter
    import app.trading.live_execution as le_mod
    le_mod._live_adapter = None
    with patch("app.trading.live_execution.settings") as ms:
        ms.live_trading_enabled = True
        ms.base_rpc_url = "https://mainnet.base.org"
        adapter = get_live_adapter()
        assert isinstance(adapter, ZoraOnChainAdapter)
    le_mod._live_adapter = None
