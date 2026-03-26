"""
app/integrations/zora_discovery.py
─────────────────────────────────────────────────────────────────────────────
Zora coin discovery and candidate ranking service.

Priority order (per product spec):
  1. creator_coin       — the creator's own Zora creator coin
  2. content_coin       — coins the creator has deployed as content
  3. keyword_match      — trending/explore coins matching extracted keywords
  4. trending_match     — highly ranked coins in the explore feed

Each candidate is scored on:
  - creator_linkage   (0–40): creator coin=40, content=30, no link=0
  - relevance         (0–30): keyword/entity/cashtag overlap
  - momentum          (0–20): 5m volume + holder growth proxy
  - liquidity         (0–10): normalised liquidity

Hard reject rules (applied before ranking):
  - liquidity_usd < settings.min_liquidity_usd
  - slippage_bps    > settings.max_slippage_bps
  - launched < no_trade_after_launch_seconds ago

The service calls the ZoraAdapterProtocol — never a concrete class.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.integrations.types import ZoraCoinData, ZoraCoinMarketState
from app.integrations.zora_client import ZoraAdapterProtocol, get_zora_adapter
from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class CoinCandidate:
    coin: ZoraCoinData
    market: ZoraCoinMarketState | None
    match_type: str                   # creator_coin | content_coin | keyword_match | trending_match
    creator_linkage_score: int = 0    # 0–40
    relevance_score: int = 0          # 0–30
    momentum_score: int = 0           # 0–20
    liquidity_score: int = 0          # 0–10
    risk_flags: list[str] = field(default_factory=list)
    rejected: bool = False

    @property
    def final_score(self) -> int:
        return (
            self.creator_linkage_score
            + self.relevance_score
            + self.momentum_score
            + self.liquidity_score
        )


@dataclass
class DiscoveryResult:
    candidates: list[CoinCandidate]
    top: CoinCandidate | None

    def ranked(self) -> list[CoinCandidate]:
        return sorted(
            [c for c in self.candidates if not c.rejected],
            key=lambda c: c.final_score,
            reverse=True,
        )


# ── Scoring helpers ────────────────────────────────────────────────────────────

def _score_liquidity(liquidity_usd: float | None) -> int:
    if liquidity_usd is None or liquidity_usd <= 0:
        return 0
    if liquidity_usd >= 500_000:
        return 10
    return max(1, int(10 * math.log10(liquidity_usd) / math.log10(500_000)))


def _score_momentum(volume_5m: float | None, holder_count: int | None) -> int:
    score = 0
    if volume_5m and volume_5m > 0:
        score += min(15, int(15 * math.log1p(volume_5m) / math.log1p(50_000)))
    if holder_count and holder_count > 10:
        score += min(5, int(5 * math.log10(holder_count) / math.log10(1_000)))
    return score


def _score_relevance(
    coin: ZoraCoinData,
    keywords: list[str],
    entities: list[str],
    cashtags: list[str],
) -> int:
    score = 0
    name_lower = (coin.name or "").lower()
    symbol_lower = (coin.symbol or "").lower()

    all_terms = [k.lower() for k in keywords + entities + cashtags]

    for term in all_terms:
        if term == symbol_lower:
            score += 15  # exact cashtag/symbol match
            break
        if term in name_lower:
            score += 8
            break

    # Partial overlap bonus
    for term in all_terms:
        if len(term) > 3 and term in name_lower:
            score += 3
    return min(score, 30)


def _hard_reject(
    coin: ZoraCoinData,
    market: ZoraCoinMarketState | None,
) -> list[str]:
    """Return list of risk flag strings; if non-empty, the coin is rejected."""
    flags: list[str] = []

    if market is None:
        flags.append("no_market_data")
        return flags

    if market.liquidity_usd is None or market.liquidity_usd < settings.min_liquidity_usd:
        flags.append("low_liquidity")

    if (
        market.slippage_bps_for_reference_trade is not None
        and market.slippage_bps_for_reference_trade > settings.max_slippage_bps
    ):
        flags.append("high_slippage")

    if coin.launched_at:
        launched = coin.launched_at
        if launched.tzinfo is None:
            launched = launched.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - launched).total_seconds()
        if age_seconds < settings.no_trade_after_launch_seconds:
            flags.append("too_new")

    return flags


# ── Discovery service ──────────────────────────────────────────────────────────

class ZoraDiscoveryService:
    """
    Discovers and ranks Zora coin candidates for a classified creator post.

    Usage:
        service = ZoraDiscoveryService()
        result = await service.discover(
            x_username="somehandle",
            creator_wallet=None,
            keywords=["base", "zora"],
            entities=["zora", "base"],
            cashtags=["ZORA"],
            mode="hybrid",
        )
    """

    def __init__(self, adapter: ZoraAdapterProtocol | None = None) -> None:
        self._adapter = adapter or get_zora_adapter()

    async def discover(
        self,
        x_username: str,
        creator_wallet: str | None,
        keywords: list[str],
        entities: list[str],
        cashtags: list[str],
        mode: str = "hybrid",  # creator_only | keyword_only | hybrid
    ) -> DiscoveryResult:
        candidates: list[CoinCandidate] = []

        # ── Step 1: creator-linked coins ──────────────────────────────────
        if mode in ("creator_only", "hybrid"):
            creator_candidates = await self._find_creator_coins(
                x_username, creator_wallet, keywords, entities, cashtags
            )
            candidates.extend(creator_candidates)

        # ── Step 2: keyword/trending coins ────────────────────────────────
        if mode in ("keyword_only", "hybrid") and (keywords or entities or cashtags):
            kw_candidates = await self._find_keyword_coins(
                keywords, entities, cashtags
            )
            # Deduplicate by contract address
            existing_addrs = {
                c.coin.contract_address for c in candidates if c.coin.contract_address
            }
            for c in kw_candidates:
                if c.coin.contract_address not in existing_addrs:
                    candidates.append(c)
                    if c.coin.contract_address:
                        existing_addrs.add(c.coin.contract_address)

        # ── Step 3: reject + score ─────────────────────────────────────────
        for c in candidates:
            flags = _hard_reject(c.coin, c.market)
            if flags:
                c.risk_flags.extend(flags)
                c.rejected = True
            else:
                c.liquidity_score = _score_liquidity(
                    c.market.liquidity_usd if c.market else None
                )
                c.momentum_score = _score_momentum(
                    c.market.volume_5m_usd if c.market else None,
                    c.market.holder_count if c.market else None,
                )
                c.relevance_score = _score_relevance(c.coin, keywords, entities, cashtags)

        ranked = [c for c in candidates if not c.rejected]
        ranked.sort(key=lambda c: c.final_score, reverse=True)

        log.info(
            "zora_discovery_complete",
            total=len(candidates),
            accepted=len(ranked),
            rejected=len(candidates) - len(ranked),
            mode=mode,
        )

        return DiscoveryResult(
            candidates=candidates,
            top=ranked[0] if ranked else None,
        )

    async def _find_creator_coins(
        self,
        x_username: str,
        creator_wallet: str | None,
        keywords: list[str],
        entities: list[str],
        cashtags: list[str],
    ) -> list[CoinCandidate]:
        results: list[CoinCandidate] = []

        # Resolve creator profile by X username
        profile = await self._adapter.resolve_creator_by_x_username(x_username)
        if profile is None and creator_wallet:
            profile = await self._adapter.get_creator_profile(creator_wallet)

        if profile is None:
            return results

        wallet = profile.wallet_address or creator_wallet
        if not wallet:
            return results

        coins = await self._adapter.get_coins_for_creator(wallet)
        for i, coin in enumerate(coins):
            market = await self._adapter.get_coin_market_state(coin.contract_address or "")
            match_type = "creator_coin" if i == 0 else "content_coin"
            linkage = 40 if match_type == "creator_coin" else 30
            candidate = CoinCandidate(
                coin=coin,
                market=market,
                match_type=match_type,
                creator_linkage_score=linkage,
            )
            results.append(candidate)

        return results

    async def _find_keyword_coins(
        self,
        keywords: list[str],
        entities: list[str],
        cashtags: list[str],
    ) -> list[CoinCandidate]:
        """Search Zora trending/explore feed and filter by keyword relevance."""
        results: list[CoinCandidate] = []

        try:
            trending = await self._fetch_trending()
        except Exception as exc:
            log.warning("zora_trending_fetch_failed", error=str(exc))
            return results

        for coin in trending:
            rel = _score_relevance(coin, keywords, entities, cashtags)
            if rel < 5:
                continue  # not relevant enough
            market = await self._adapter.get_coin_market_state(coin.contract_address or "")
            candidate = CoinCandidate(
                coin=coin,
                market=market,
                match_type="keyword_match" if rel >= 10 else "trending_match",
                creator_linkage_score=0,
            )
            results.append(candidate)

        return results

    async def _fetch_trending(self) -> list[ZoraCoinData]:
        """
        Fetch trending coins from Zora's explore endpoint.
        Falls back to empty list if endpoint is unavailable.
        """
        # The ZoraApiAdapter.explore_trending is an extension added below.
        # If the adapter doesn't support it, return empty gracefully.
        if hasattr(self._adapter, "explore_trending"):
            coins = await self._adapter.explore_trending(  # type: ignore[attr-defined]
                limit=settings.zora_trending_limit
            )
            return coins or []
        return []
