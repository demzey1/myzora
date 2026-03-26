"""
app/integrations/zora_field_map.py
─────────────────────────────────────────────────────────────────────────────
Single source of truth for Zora REST API field names.

This module defines the field name constants used in ZoraApiAdapter._parse_*
methods. When you confirm a field name from the Zora API docs or SDK source,
update the constant here — nowhere else needs to change.

Status codes:
  CONFIRMED   — field name verified from official docs or SDK source
  INFERRED    — field name derived from SDK TypeScript types (likely correct)
  UNCONFIRMED — best-guess camelCase; needs verification before production use

Source references:
  https://docs.zora.co
  https://github.com/ourzora/zora-protocol (SDK source)
  https://github.com/ourzora/zora-creator-subgraph (subgraph types)

Last reviewed: Phase 5 implementation
"""

from __future__ import annotations


class ZoraCreatorFields:
    """Fields in the creator/profile API response."""
    # INFERRED — matches zora-creator-subgraph Account entity
    ADDRESS        = "address"          # wallet address (checksummed)
    DISPLAY_NAME   = "displayName"      # INFERRED — camelCase from SDK
    BIO            = "bio"              # INFERRED
    PROFILE_URL    = "profileUrl"       # INFERRED
    # UNCONFIRMED — Zora may not expose connected social handles via REST
    TWITTER_HANDLE = "twitterHandle"    # UNCONFIRMED: may be "twitter" or absent


class ZoraCoinFields:
    """Fields in the coin/token API response."""
    # INFERRED — matches ERC-20 standard + Zora subgraph
    ADDRESS        = "address"          # contract address
    SYMBOL         = "symbol"           # CONFIRMED: ERC-20 standard
    NAME           = "name"             # CONFIRMED: ERC-20 standard
    CREATOR        = "creatorAddress"   # INFERRED — camelCase
    CHAIN_ID       = "chainId"          # INFERRED
    DECIMALS       = "decimals"         # CONFIRMED: ERC-20 standard
    # UNCONFIRMED — timestamp field name and format vary by API
    CREATED_AT     = "createdAt"        # UNCONFIRMED: may be "launchedAt" or "deployedAt"


class ZoraMarketFields:
    """Fields in the coin market state API response."""
    # UNCONFIRMED — these depend entirely on whether Zora provides a
    # REST market endpoint or whether these must be derived on-chain
    PRICE_USD      = "priceUsd"         # UNCONFIRMED
    LIQUIDITY_USD  = "liquidityUsd"     # UNCONFIRMED
    VOLUME_5M      = "volume5m"         # UNCONFIRMED: window may differ
    VOLUME_1H      = "volume1h"         # UNCONFIRMED
    VOLUME_24H     = "volume24h"        # UNCONFIRMED
    MARKET_CAP     = "marketCapUsd"     # UNCONFIRMED
    HOLDER_COUNT   = "holderCount"      # UNCONFIRMED: may be off-chain only


class ZoraSimFields:
    """Fields in the trade simulation response (if endpoint exists)."""
    # All UNCONFIRMED — Zora SDK simulation is npm-only as of last check
    OUTPUT_TOKENS  = "outputTokens"
    SLIPPAGE_BPS   = "slippageBps"
    PRICE_IMPACT   = "priceImpact"
    GAS_USD        = "gasEstimateUsd"


# ── Field resolution helpers ───────────────────────────────────────────────────

def resolve(raw: dict, *candidates: str) -> object | None:
    """
    Try each candidate key in order; return the first non-None value found.
    This handles both camelCase and snake_case field names gracefully.

    Example:
        price = resolve(raw, ZoraMarketFields.PRICE_USD, "price_usd", "price")
    """
    for key in candidates:
        val = raw.get(key)
        if val is not None:
            return val
    return None


def resolve_float(raw: dict, *candidates: str) -> float | None:
    val = resolve(raw, *candidates)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def resolve_int(raw: dict, *candidates: str) -> int | None:
    val = resolve(raw, *candidates)
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None
