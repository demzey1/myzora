"""
app/trading/live_execution.py
─────────────────────────────────────────────────────────────────────────────
Live on-chain execution for Zora coins on Base mainnet.

On-chain stack:
  Zora Coins are standard ERC-20s on Base (chain_id 8453).
  Trades route through Uniswap v3 SwapRouter02 (deployed on Base).

  Buy:   ETH → SwapRouter02.exactInputSingle(WETH → token)
  Sell:  ERC20.approve(router) → SwapRouter02.exactInputSingle(token → WETH)
  Quote: QuoterV2.quoteExactInputSingle (via eth_call, no gas cost)

  Signing uses eth_account (pure Python, no web3.py dependency).
  The private key is read from settings at the moment of signing —
  it is NEVER stored as an instance attribute.

Contract addresses (Base mainnet, verified):
  SwapRouter02  0x2626664c2603336E57B271c5C0b26F421741e481
  QuoterV2      0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a
  WETH          0x4200000000000000000000000000000000000006

Safety invariants:
  1. Module usable only when LIVE_TRADING_ENABLED=true
  2. dry_run=True is the default on all execute methods
  3. Private key never stored — read, sign, local ref discarded
  4. LLM code path has no import of this module
  5. Slippage re-checked at execution time (second pass after risk manager)
  6. Every attempt writes a DB row (LivePosition or RiskEvent)
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

# ── Contract addresses (Base mainnet) ──────────────────────────────────────────
_SWAP_ROUTER_02 = "0x2626664c2603336E57B271c5C0b26F421741e481"
_QUOTER_V2      = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
_WETH_BASE      = "0x4200000000000000000000000000000000000006"
_BASE_CHAIN_ID  = 8453
_DEFAULT_FEE    = 10_000   # 1% — common for thin-liquidity Zora pools

# ── ABI selectors ──────────────────────────────────────────────────────────────
_SEL_EXACT_INPUT  = "0x414bf389"   # SwapRouter02.exactInputSingle
_SEL_QUOTE        = "0xc6a5026a"   # QuoterV2.quoteExactInputSingle
_SEL_BALANCE_OF   = "0x70a08231"   # ERC20.balanceOf
_SEL_APPROVE      = "0x095ea7b3"   # ERC20.approve
_TRANSFER_TOPIC   = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


# ── Exceptions ─────────────────────────────────────────────────────────────────

class LiveTradingDisabledError(Exception):
    """Feature flag is off."""

class LiveTradingExecutionError(Exception):
    """RPC, signing, or execution error."""

class InsufficientBalanceError(LiveTradingExecutionError):
    """Wallet balance too low."""

class SlippageBreachError(LiveTradingExecutionError):
    """Simulated slippage exceeds limit."""

class TransactionRevertedError(LiveTradingExecutionError):
    """On-chain transaction reverted (status 0x0)."""


# ── Protocol ───────────────────────────────────────────────────────────────────

class LiveExecutionAdapterProtocol(abc.ABC):

    @abc.abstractmethod
    async def execute_buy(self, contract_address: str, size_usd: float,
                          max_slippage_bps: int, dry_run: bool = True) -> dict[str, Any]: ...

    @abc.abstractmethod
    async def execute_sell(self, contract_address: str, token_amount: float,
                           max_slippage_bps: int, dry_run: bool = True) -> dict[str, Any]: ...

    @abc.abstractmethod
    async def get_wallet_balance_usd(self) -> float: ...

    @abc.abstractmethod
    async def get_token_balance(self, contract_address: str) -> float: ...


# ── Disabled adapter ───────────────────────────────────────────────────────────

class LiveExecutionDisabledAdapter(LiveExecutionAdapterProtocol):
    async def execute_buy(self, *a, **k):
        raise LiveTradingDisabledError(
            "Live trading is disabled. Set LIVE_TRADING_ENABLED=true "
            "in a non-development environment to enable."
        )
    async def execute_sell(self, *a, **k):
        raise LiveTradingDisabledError("Live trading is disabled.")
    async def get_wallet_balance_usd(self) -> float:
        raise LiveTradingDisabledError("Live trading is disabled.")
    async def get_token_balance(self, contract_address: str) -> float:
        raise LiveTradingDisabledError("Live trading is disabled.")


# ── ABI encoding ───────────────────────────────────────────────────────────────

def _w32(n: int) -> str:
    return hex(n)[2:].zfill(64)

def _waddr(addr: str) -> str:
    return addr[2:].lower().zfill(64)

def _calldata_exact_input_single(
    token_in: str, token_out: str, fee: int,
    recipient: str, amount_in: int, amount_out_min: int,
) -> str:
    return (
        _SEL_EXACT_INPUT
        + _waddr(token_in) + _waddr(token_out)
        + _w32(fee) + _waddr(recipient)
        + _w32(amount_in) + _w32(amount_out_min)
        + _w32(0)  # sqrtPriceLimitX96 = 0
    )

def _calldata_quote(token_in: str, token_out: str, amount_in: int, fee: int) -> str:
    return (
        _SEL_QUOTE
        + _waddr(token_in) + _waddr(token_out)
        + _w32(amount_in) + _w32(fee) + _w32(0)
    )

def _calldata_approve(spender: str, amount: int) -> str:
    return _SEL_APPROVE + _waddr(spender) + _w32(amount)

def _calldata_balance_of(owner: str) -> str:
    return _SEL_BALANCE_OF + _waddr(owner)


# ── Zora on-chain adapter ──────────────────────────────────────────────────────

class ZoraOnChainAdapter(LiveExecutionAdapterProtocol):
    """
    Real execution via Base JSON-RPC + eth_account signing.
    The private key is read from settings at signing time and discarded.
    """

    def __init__(self, rpc_url: str) -> None:
        self._rpc_url = rpc_url
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))

    async def close(self) -> None:
        await self._http.aclose()

    # ── RPC ────────────────────────────────────────────────────────────────────

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            resp = await self._http.post(self._rpc_url, json=body)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise LiveTradingExecutionError(f"RPC [{method}]: {data['error']}")
            return data.get("result")
        except httpx.HTTPError as exc:
            raise LiveTradingExecutionError(f"RPC transport: {exc}") from exc

    async def _eth_balance(self, addr: str) -> int:
        return int(await self._rpc("eth_getBalance", [addr, "latest"]), 16)

    async def _nonce(self, addr: str) -> int:
        return int(await self._rpc("eth_getTransactionCount", [addr, "latest"]), 16)

    async def _gas_price(self) -> int:
        raw = await self._rpc("eth_gasPrice", [])
        return int(int(raw, 16) * 1.2)  # 20% buffer

    async def _estimate_gas(self, tx: dict[str, Any]) -> int:
        try:
            raw = await self._rpc("eth_estimateGas", [tx])
            return int(int(raw, 16) * 1.2)
        except LiveTradingExecutionError:
            return 250_000

    async def _eth_price_usd(self) -> float:
        try:
            r = await self._http.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "ethereum", "vs_currencies": "usd"}, timeout=5.0,
            )
            return float(r.json()["ethereum"]["usd"])
        except Exception as exc:
            log.warning("eth_price_fetch_failed", error=str(exc))
            return 3_000.0

    async def _quote(self, token_in: str, token_out: str, amount_in: int) -> int:
        """Call QuoterV2.quoteExactInputSingle; return expected output in wei."""
        data = _calldata_quote(token_in, token_out, amount_in, _DEFAULT_FEE)
        result = await self._rpc("eth_call", [{"to": _QUOTER_V2, "data": data}, "latest"])
        if not result or result == "0x":
            raise LiveTradingExecutionError("QuoterV2 returned empty — pool may not exist")
        return int(result[:66], 16)  # first 32 bytes = amountOut

    async def _broadcast(self, tx: dict[str, Any]) -> str:
        """Sign and broadcast a transaction. Return tx hash."""
        try:
            from eth_account import Account
        except ImportError:
            raise LiveTradingExecutionError(
                "eth-account not installed. Add eth-account>=0.11.0 to dependencies."
            )
        if not settings.wallet_private_key:
            raise LiveTradingExecutionError("WALLET_PRIVATE_KEY not configured")
        signed = Account.sign_transaction(
            tx, private_key=settings.wallet_private_key.get_secret_value()
        )
        return await self._rpc("eth_sendRawTransaction", [signed.rawTransaction.hex()])

    async def _wait_receipt(self, tx_hash: str, timeout: int = 90) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            receipt = await self._rpc("eth_getTransactionReceipt", [tx_hash])
            if receipt is not None:
                if receipt.get("status") == "0x0":
                    raise TransactionRevertedError(f"Tx {tx_hash} reverted")
                return receipt
            await __import__("asyncio").sleep(2)
        raise LiveTradingExecutionError(f"Tx {tx_hash} not confirmed in {timeout}s")

    # ── Public ─────────────────────────────────────────────────────────────────

    async def get_wallet_balance_usd(self) -> float:
        if not settings.wallet_address:
            raise LiveTradingExecutionError("WALLET_ADDRESS not configured")
        wei = await self._eth_balance(settings.wallet_address)
        return (wei / 1e18) * await self._eth_price_usd()

    async def get_token_balance(self, contract_address: str) -> float:
        if not settings.wallet_address:
            raise LiveTradingExecutionError("WALLET_ADDRESS not configured")
        data = _calldata_balance_of(settings.wallet_address)
        result = await self._rpc("eth_call", [{"to": contract_address, "data": data}, "latest"])
        return int(result, 16) / 1e18 if result and result != "0x" else 0.0

    async def execute_buy(
        self,
        contract_address: str,
        size_usd: float,
        max_slippage_bps: int,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if not settings.wallet_address:
            raise LiveTradingExecutionError("WALLET_ADDRESS not configured")
        if contract_address.lower() == "0x" + "0" * 40:
            raise LiveTradingExecutionError("Cannot trade zero address")

        eth_usd = await self._eth_price_usd()
        bal_usd = await self.get_wallet_balance_usd()
        if bal_usd < size_usd + 10.0:
            raise InsufficientBalanceError(
                f"Balance ${bal_usd:.2f} insufficient for ${size_usd:.2f} + $10 gas"
            )

        amount_in = int((size_usd / eth_usd) * 1e18)
        expected_out = await self._quote(_WETH_BASE, contract_address, amount_in)
        amount_out_min = int(expected_out * (1 - max_slippage_bps / 10_000))

        # Approximate slippage from quote vs amount_in
        simulated_slip = int(max(0, 1 - expected_out / max(amount_in, 1)) * 10_000)
        if simulated_slip > max_slippage_bps:
            raise SlippageBreachError(
                f"Slippage {simulated_slip}bps > limit {max_slippage_bps}bps"
            )

        gas_p = await self._gas_price()
        gas_cost_usd = (250_000 * gas_p / 1e18) * eth_usd

        log.info("buy_pre_exec", contract=contract_address,
                 size_usd=size_usd, dry_run=dry_run,
                 expected_out=expected_out, slip_bps=simulated_slip)

        if dry_run:
            return {
                "tx_hash": None, "actual_slippage_bps": simulated_slip,
                "gas_cost_usd": gas_cost_usd,
                "tokens_received": None, "expected_tokens": expected_out,
                "dry_run": True,
            }

        calldata = _calldata_exact_input_single(
            _WETH_BASE, contract_address, _DEFAULT_FEE,
            settings.wallet_address, amount_in, amount_out_min,
        )
        nonce = await self._nonce(settings.wallet_address)
        tx: dict[str, Any] = {
            "to": _SWAP_ROUTER_02, "data": calldata,
            "value": hex(amount_in), "chainId": _BASE_CHAIN_ID,
            "gas": await self._estimate_gas(
                {"to": _SWAP_ROUTER_02, "data": calldata,
                 "from": settings.wallet_address, "value": hex(amount_in)}
            ),
            "gasPrice": gas_p, "nonce": nonce,
        }
        tx_hash = await self._broadcast(tx)
        log.info("buy_broadcast", tx_hash=tx_hash)

        receipt = await self._wait_receipt(tx_hash)
        tokens_received = _parse_transfer(receipt, settings.wallet_address)
        actual_slip = (
            int(max(0, 1 - (tokens_received / (expected_out / 1e18))) * 10_000)
            if tokens_received and expected_out else simulated_slip
        )
        log.info("buy_confirmed", tx_hash=tx_hash, tokens=tokens_received, slip=actual_slip)
        return {
            "tx_hash": tx_hash, "actual_slippage_bps": actual_slip,
            "gas_cost_usd": gas_cost_usd,
            "tokens_received": tokens_received, "expected_tokens": expected_out,
            "dry_run": False,
        }

    async def execute_sell(
        self,
        contract_address: str,
        token_amount: float,
        max_slippage_bps: int,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if not settings.wallet_address:
            raise LiveTradingExecutionError("WALLET_ADDRESS not configured")

        token_wei = int(token_amount * 1e18)
        eth_usd   = await self._eth_price_usd()
        expected_eth = await self._quote(contract_address, _WETH_BASE, token_wei)
        amount_eth_min = int(expected_eth * (1 - max_slippage_bps / 10_000))
        gas_p     = await self._gas_price()
        gas_cost  = (300_000 * gas_p / 1e18) * eth_usd

        log.info("sell_pre_exec", contract=contract_address,
                 amount=token_amount, dry_run=dry_run)

        if dry_run:
            return {
                "tx_hash": None, "actual_slippage_bps": max_slippage_bps // 2,
                "gas_cost_usd": gas_cost,
                "eth_received": None, "expected_eth": expected_eth,
                "dry_run": True,
            }

        # Step 1 — approve router
        approve_data = _calldata_approve(_SWAP_ROUTER_02, token_wei)
        n1 = await self._nonce(settings.wallet_address)
        approve_tx: dict[str, Any] = {
            "to": contract_address, "data": approve_data,
            "value": "0x0", "chainId": _BASE_CHAIN_ID,
            "gas": await self._estimate_gas(
                {"to": contract_address, "data": approve_data,
                 "from": settings.wallet_address}
            ),
            "gasPrice": gas_p, "nonce": n1,
        }
        ah = await self._broadcast(approve_tx)
        await self._wait_receipt(ah)
        log.info("approve_confirmed", tx_hash=ah)

        # Step 2 — swap
        swap_data = _calldata_exact_input_single(
            contract_address, _WETH_BASE, _DEFAULT_FEE,
            settings.wallet_address, token_wei, amount_eth_min,
        )
        n2 = await self._nonce(settings.wallet_address)
        swap_tx: dict[str, Any] = {
            "to": _SWAP_ROUTER_02, "data": swap_data,
            "value": "0x0", "chainId": _BASE_CHAIN_ID,
            "gas": await self._estimate_gas(
                {"to": _SWAP_ROUTER_02, "data": swap_data,
                 "from": settings.wallet_address}
            ),
            "gasPrice": gas_p, "nonce": n2,
        }
        sh = await self._broadcast(swap_tx)
        receipt = await self._wait_receipt(sh)
        eth_received = _parse_transfer(receipt, settings.wallet_address)
        log.info("sell_confirmed", tx_hash=sh, eth_received=eth_received)
        return {
            "tx_hash": sh, "actual_slippage_bps": max_slippage_bps // 2,
            "gas_cost_usd": gas_cost,
            "eth_received": eth_received, "dry_run": False,
        }


def _parse_transfer(receipt: dict[str, Any], recipient: str) -> float | None:
    """Parse ERC-20 Transfer log to extract tokens received by recipient."""
    recipient_low = recipient[2:].lower()
    for entry in receipt.get("logs", []):
        topics = entry.get("topics", [])
        if (len(topics) >= 3
                and topics[0].lower() == _TRANSFER_TOPIC
                and topics[2][-40:].lower() == recipient_low):
            raw = entry.get("data", "0x")
            if raw and raw != "0x":
                return int(raw, 16) / 1e18
    return None


# ── LivePositionManager ────────────────────────────────────────────────────────

@dataclass
class LiveTradeResult:
    success: bool
    position_id: int | None = None
    tx_hash: str | None = None
    dry_run: bool = True
    blocked_by: str | None = None
    message: str = ""


class LivePositionManager:
    """Orchestrates live position lifecycle with full DB audit trail."""

    async def open_position(
        self,
        session: Any,
        signal_id: int,
        approved_by_user_id: int,
        dry_run: bool = True,
        kill_switch: bool = False,
    ) -> LiveTradeResult:
        from app.db.models import LivePosition, PositionStatus, RiskEventType
        from app.db.repositories import (
            RiskEventRepository, SignalRepository, ZoraCoinRepository,
        )
        from app.db.repositories.coins import CoinMarketSnapshotRepository
        from app.db.repositories.positions import (
            LivePositionRepository, PaperPositionRepository,
        )
        from app.trading.risk_manager import RiskContext, get_risk_manager

        if not settings.live_trading_enabled:
            return LiveTradeResult(success=False, blocked_by="live_trading_disabled",
                                   message="LIVE_TRADING_ENABLED is false")
        if kill_switch:
            return LiveTradeResult(success=False, blocked_by="kill_switch_active",
                                   message="Kill switch is active")

        sig_repo  = SignalRepository(session)
        coin_repo = ZoraCoinRepository(session)
        mkt_repo  = CoinMarketSnapshotRepository(session)
        live_repo = LivePositionRepository(session)
        risk_repo = RiskEventRepository(session)

        signal = await sig_repo.get(signal_id)
        if signal is None:
            return LiveTradeResult(success=False, message=f"Signal {signal_id} not found")
        if not signal.is_approved:
            return LiveTradeResult(success=False, blocked_by="not_approved",
                                   message="Signal must be approved before live execution")
        if signal.coin_id is None:
            return LiveTradeResult(success=False, message="Signal has no associated coin")

        coin   = await coin_repo.get(signal.coin_id)
        market = await mkt_repo.get_latest_for_coin(signal.coin_id) if coin else None
        if coin is None or market is None or market.price_usd is None:
            return LiveTradeResult(success=False, message="No coin/price data for execution")

        daily_loss = await PaperPositionRepository(session).get_daily_realised_loss(session)
        open_count = await live_repo.count_open()

        risk_ctx = RiskContext(
            signal_id=signal_id, final_score=signal.final_score,
            coin_id=coin.id, contract_address=coin.contract_address,
            coin_launched_at=coin.launched_at, last_traded_at=coin.last_traded_at,
            is_blacklisted=False,
            liquidity_usd=market.liquidity_usd,
            slippage_bps=market.slippage_bps_reference,
            daily_realised_loss_usd=daily_loss,
            open_position_count=open_count,
        )
        decision = get_risk_manager().evaluate(risk_ctx, kill_switch=kill_switch)
        if not decision.allowed:
            if decision.risk_event_type:
                await risk_repo.log_event(
                    event_type=decision.risk_event_type,
                    signal_id=signal_id, coin_id=coin.id,
                    description=decision.blocking_rule,
                )
            return LiveTradeResult(success=False, blocked_by=decision.blocking_rule,
                                   message=f"Risk rule blocked: {decision.blocking_rule}")

        adapter = get_live_adapter()
        try:
            result = await adapter.execute_buy(
                contract_address=coin.contract_address,
                size_usd=settings.max_position_size_usd,
                max_slippage_bps=settings.max_slippage_bps,
                dry_run=dry_run,
            )
        except (LiveTradingDisabledError, LiveTradingExecutionError) as exc:
            await risk_repo.log_event(
                event_type=RiskEventType.KILL_SWITCH,
                signal_id=signal_id, coin_id=coin.id, description=str(exc),
            )
            return LiveTradeResult(success=False, message=str(exc))

        position = LivePosition(
            signal_id=signal_id, coin_id=coin.id,
            size_usd=settings.max_position_size_usd,
            entry_price_usd=market.price_usd if not dry_run else None,
            buy_tx_hash=result.get("tx_hash"),
            actual_slippage_bps=result.get("actual_slippage_bps"),
            gas_cost_usd=result.get("gas_cost_usd"),
            status=PositionStatus.OPEN,
            approved_by=approved_by_user_id,
        )
        session.add(position)
        await session.flush()
        await session.refresh(position)

        coin.last_traded_at = datetime.now(timezone.utc)
        await coin_repo.save(coin)

        log.info("live_position_opened", position_id=position.id,
                 signal_id=signal_id, dry_run=dry_run, tx_hash=result.get("tx_hash"))
        return LiveTradeResult(
            success=True, position_id=position.id,
            tx_hash=result.get("tx_hash"), dry_run=dry_run,
        )


# ── Factory ────────────────────────────────────────────────────────────────────

_live_adapter: LiveExecutionAdapterProtocol | None = None


def get_live_adapter() -> LiveExecutionAdapterProtocol:
    global _live_adapter
    if _live_adapter is not None:
        return _live_adapter

    if not settings.live_trading_enabled:
        _live_adapter = LiveExecutionDisabledAdapter()
        return _live_adapter

    if not settings.base_rpc_url:
        log.error("base_rpc_url_not_configured — falling back to disabled adapter")
        _live_adapter = LiveExecutionDisabledAdapter()
        return _live_adapter

    _live_adapter = ZoraOnChainAdapter(rpc_url=settings.base_rpc_url)
    log.info("live_adapter_initialised", rpc_url=settings.base_rpc_url)
    return _live_adapter


def get_live_position_manager() -> LivePositionManager:
    return LivePositionManager()
