"""
app/integrations/zora_client.py
─────────────────────────────────────────────────────────────────────────────
Zora integration layer, structured as:

  ZoraAdapterProtocol   — the interface the rest of the app depends on
  ZoraApiAdapter        — concrete implementation against the Zora REST API
                          (uncertain fields use zora_field_map.py constants)
  ZoraStubAdapter       — returns None/empty for all calls (used in tests
                          and when the API key is not configured)

WHY THIS STRUCTURE:
  Zora's Coins product is relatively new. The exact REST endpoint paths,
  JSON field names, and pagination shapes are not fully confirmed from
  public documentation at time of writing.

  Rather than guessing field names (which would silently produce wrong data),
  we isolate ALL mapping logic inside `_parse_*` private methods annotated
  via zora_field_map.py constants. The scoring engine and pipeline only
  Protocol — they never parse Zora JSON directly.

KNOWN / ASSUMED:
  - Coins live on Base (chain_id 8453)
  - Contract addresses are standard ERC-20 on Base
  - The Zora SDK (npm) exposes getCoin / getCoins / tradeCoin
  - A REST API exists at api.zora.co — exact paths unconfirmed

Field name status (CONFIRMED/INFERRED/UNCONFIRMED) is documented in
app/integrations/zora_field_map.py — update that file when API docs confirm.
"""

from __future__ import annotations

import abc
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.integrations.types import (
    ZoraCoinData,
    ZoraCoinMarketState,
    ZoraCreatorProfile,
    ZoraTradeSimulation,
)
from app.logging_config import get_logger

log = get_logger(__name__)


# ── Protocol (interface) ───────────────────────────────────────────────────────

class ZoraAdapterProtocol(abc.ABC):
    """
    Contract that every Zora adapter must fulfil.
    The scoring engine and pipeline only depend on this interface.
    """

    @abc.abstractmethod
    async def get_creator_profile(self, wallet_address: str) -> ZoraCreatorProfile | None:
        """Look up a Zora creator by their wallet address."""

    @abc.abstractmethod
    async def get_coin_by_address(self, contract_address: str) -> ZoraCoinData | None:
        """Fetch static metadata for a single coin by contract address."""

    @abc.abstractmethod
    async def get_coins_for_creator(self, wallet_address: str) -> list[ZoraCoinData]:
        """Return all coins created by a given wallet address."""

    @abc.abstractmethod
    async def get_coin_market_state(
        self, contract_address: str
    ) -> ZoraCoinMarketState | None:
        """Fetch current market state (price, liquidity, volume, etc.)."""

    @abc.abstractmethod
    async def simulate_trade(
        self,
        contract_address: str,
        trade_side: str,
        input_amount_usd: float,
    ) -> ZoraTradeSimulation | None:
        """
        Simulate a buy/sell and return expected slippage, price impact, etc.
        Returns None if simulation is unavailable.
        """

    @abc.abstractmethod
    async def resolve_creator_by_x_username(
        self, x_username: str
    ) -> ZoraCreatorProfile | None:
        """
        Attempt to find a Zora creator profile linked to an X/Twitter handle.
        Zora does not currently expose a public search-by-twitter REST endpoint.
        """


# ── Concrete REST adapter ──────────────────────────────────────────────────────

class ZoraApiAdapter(ZoraAdapterProtocol):
    """
    Calls the Zora REST API (base_url from settings).
    All _parse_* methods are the only place that touches raw API JSON.
    Uncertain field names use zora_field_map.py constants; status is
    documented there (CONFIRMED / INFERRED / UNCONFIRMED).
    """

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            # API key header name: confirm from Zora developer docs
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(15.0, connect=5.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        log.debug("zora_api_request", path=path)
        response = await self._client.get(path, params=params or {})
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            raise ZoraRateLimitError("Zora API rate limit hit")
        if response.status_code >= 400:
            raise ZoraAPIError(f"Zora API {response.status_code}: {response.text[:200]}")
        return response.json()

    # ── Parsers — ALL raw field access lives here ──────────────────────────────

    def _parse_creator_profile(self, raw: dict[str, Any]) -> ZoraCreatorProfile:
        """Map raw Zora creator response to ZoraCreatorProfile using field map."""
        from app.integrations.zora_field_map import ZoraCreatorFields as F, resolve
        return ZoraCreatorProfile(
            wallet_address=str(resolve(raw, F.ADDRESS, "wallet_address") or ""),
            display_name=str(resolve(raw, F.DISPLAY_NAME, "display_name") or "") or None,
            bio=str(resolve(raw, F.BIO, "description") or "") or None,
            profile_url=str(resolve(raw, F.PROFILE_URL, "profile_url") or "") or None,
            x_username=str(resolve(raw, F.TWITTER_HANDLE, "twitter", "twitter_username") or "") or None,
        )

    def _parse_coin_data(self, raw: dict[str, Any]) -> ZoraCoinData:
        """Map raw Zora coin response to ZoraCoinData using field map."""
        from app.integrations.zora_field_map import ZoraCoinFields as F, resolve, resolve_int
        launched_at: datetime | None = None
        launched_raw = resolve(raw, F.CREATED_AT, "launchedAt", "deployedAt", "launched_at")
        if launched_raw:
            try:
                launched_at = datetime.fromisoformat(
                    str(launched_raw).replace("Z", "+00:00")
                )
            except ValueError:
                pass

        return ZoraCoinData(
            contract_address=str(resolve(raw, F.ADDRESS, "contract_address") or ""),
            symbol=str(resolve(raw, F.SYMBOL, "ticker") or ""),
            name=str(resolve(raw, F.NAME) or "") or None,
            creator_address=str(resolve(raw, F.CREATOR, "creator_address") or "") or None,
            chain_id=resolve_int(raw, F.CHAIN_ID, "chain_id") or 8453,
            decimals=resolve_int(raw, F.DECIMALS) or 18,
            launched_at=launched_at,
        )

    def _parse_market_state(self, raw: dict[str, Any], contract_address: str) -> ZoraCoinMarketState:
        """Map raw Zora market data to ZoraCoinMarketState using field map."""
        from app.integrations.zora_field_map import ZoraMarketFields as F, resolve_float, resolve_int
        return ZoraCoinMarketState(
            contract_address=contract_address,
            price_usd=resolve_float(raw, F.PRICE_USD, "price_usd", "price"),
            liquidity_usd=resolve_float(raw, F.LIQUIDITY_USD, "liquidity_usd", "tvl"),
            volume_5m_usd=resolve_float(raw, F.VOLUME_5M, "volume_5m_usd"),
            volume_1h_usd=resolve_float(raw, F.VOLUME_1H, "volume_1h_usd"),
            volume_24h_usd=resolve_float(raw, F.VOLUME_24H, "volume_24h_usd"),
            market_cap_usd=resolve_float(raw, F.MARKET_CAP, "market_cap_usd"),
            holder_count=resolve_int(raw, F.HOLDER_COUNT, "holder_count"),
            slippage_bps_for_reference_trade=None,
        )

    def _parse_trade_simulation(
        self, raw: dict[str, Any], contract_address: str, side: str, input_usd: float
    ) -> ZoraTradeSimulation:
        """Map raw trade simulation response using field map."""
        from datetime import timezone
        from app.integrations.zora_field_map import ZoraSimFields as F, resolve_float, resolve_int
        return ZoraTradeSimulation(
            contract_address=contract_address,
            trade_side=side,
            input_amount_usd=input_usd,
            expected_output_tokens=resolve_float(raw, F.OUTPUT_TOKENS, "expected_output"),
            expected_slippage_bps=resolve_int(raw, F.SLIPPAGE_BPS, "slippage_bps"),
            price_impact_pct=resolve_float(raw, F.PRICE_IMPACT, "price_impact_pct"),
            gas_estimate_usd=resolve_float(raw, F.GAS_USD, "gas_estimate_usd"),
            simulation_timestamp=datetime.now(timezone.utc),
        )

    # ── Public adapter methods ─────────────────────────────────────────────────

    async def get_creator_profile(self, wallet_address: str) -> ZoraCreatorProfile | None:
        # Endpoint: /profiles/:address (INFERRED — confirm from Zora API docs)
        raw = await self._get(f"/profiles/{wallet_address}")
        if not raw:
            log.debug("zora_creator_not_found", address=wallet_address)
            return None
        return self._parse_creator_profile(raw)

    async def get_coin_by_address(self, contract_address: str) -> ZoraCoinData | None:
        # Endpoint: /coins/:address (INFERRED — confirm from Zora API docs)
        raw = await self._get(f"/coins/{contract_address}")
        if not raw:
            log.debug("zora_coin_not_found", address=contract_address)
            return None
        return self._parse_coin_data(raw)

    async def get_coins_for_creator(self, wallet_address: str) -> list[ZoraCoinData]:
        # Endpoint: /profiles/:address/coins (INFERRED — confirm from Zora API docs)
        raw = await self._get(f"/profiles/{wallet_address}/coins")
        if not raw:
            return []
        # Response shape: bare list OR {"coins": [...]} — try both
        items = raw if isinstance(raw, list) else raw.get("coins") or raw.get("items") or raw.get("data") or []
        return [self._parse_coin_data(item) for item in items]

    async def get_coin_market_state(self, contract_address: str) -> ZoraCoinMarketState | None:
        # Endpoint: /coins/:address/market (INFERRED — confirm from Zora API docs)
        raw = await self._get(f"/coins/{contract_address}/market")
        if not raw:
            return None
        return self._parse_market_state(raw, contract_address)

    async def simulate_trade(
        self, contract_address: str, trade_side: str, input_amount_usd: float
    ) -> ZoraTradeSimulation | None:
        # Trade simulation endpoint is not confirmed as a public REST API.
        # On-chain slippage quotes are handled by ZoraOnChainAdapter.execute_buy().
        log.debug("zora_simulate_not_implemented", address=contract_address)
        return None

    async def resolve_creator_by_x_username(self, x_username: str) -> ZoraCreatorProfile | None:
        # Zora does not currently expose a search-by-twitter REST endpoint.
        # Profile<->Twitter linking is done manually via /addaccount + creator wallet lookup.
        log.debug("zora_x_resolve_not_implemented", x_username=x_username)
        return None


# ── Stub adapter (tests / unconfigured) ───────────────────────────────────────

class ZoraStubAdapter(ZoraAdapterProtocol):
    """
    Returns None / empty lists for all calls.
    Used when ZORA_API_KEY is not set or in unit tests.
    The scoring engine handles None market state gracefully.
    """

    async def get_creator_profile(self, wallet_address: str) -> None:
        return None

    async def get_coin_by_address(self, contract_address: str) -> None:
        return None

    async def get_coins_for_creator(self, wallet_address: str) -> list:
        return []

    async def get_coin_market_state(self, contract_address: str) -> None:
        return None

    async def simulate_trade(
        self, contract_address: str, trade_side: str, input_amount_usd: float
    ) -> None:
        return None

    async def resolve_creator_by_x_username(self, x_username: str) -> None:
        return None

    async def explore_trending(self, limit: int = 20) -> list:
        return []



# ── Exceptions ────────────────────────────────────────────────────────────────

class ZoraAPIError(Exception): ...
class ZoraRateLimitError(ZoraAPIError): ...


# ── Singleton factory ──────────────────────────────────────────────────────────

_zora_adapter: ZoraAdapterProtocol | None = None


def get_zora_adapter() -> ZoraAdapterProtocol:
    """
    Return the configured Zora adapter.
    Falls back to ZoraStubAdapter when the API key / base URL is missing.
    """
    global _zora_adapter
    if _zora_adapter is None:
        if settings.zora_api_base_url:
            api_key = settings.zora_api_key.get_secret_value() if settings.zora_api_key else None
            _zora_adapter = ZoraApiAdapter(
                base_url=settings.zora_api_base_url,
                api_key=api_key,
            )
            log.info("zora_adapter_initialised", base_url=settings.zora_api_base_url)
        else:
            log.warning("zora_adapter_stub_active", reason="ZORA_API_BASE_URL not set")
            _zora_adapter = ZoraStubAdapter()
    return _zora_adapter
