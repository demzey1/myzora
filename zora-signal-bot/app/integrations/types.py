"""
app/integrations/types.py
─────────────────────────────────────────────────────────────────────────────
Canonical Pydantic models that represent data flowing INTO the application
from external integrations. Every integration adapter must translate its
raw API response into one of these types — the rest of the codebase never
touches raw API payloads.

X API field sources (Twitter v2):
  https://developer.twitter.com/en/docs/twitter-api/tweets/timelines/api-reference
  https://developer.twitter.com/en/docs/twitter-api/fields

Zora field sources:
  https://docs.zora.co  (coins SDK / REST)
  Uncertain field names are documented in zora_field_map.py
  until the exact endpoint/field name is confirmed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field


# ── X / Twitter v2 ────────────────────────────────────────────────────────────

class XPublicMetrics(BaseModel):
    """
    Documented in Twitter v2 under tweet.public_metrics.
    https://developer.twitter.com/en/docs/twitter-api/data-dictionary/object-model/tweet
    """
    like_count: int = 0
    retweet_count: int = 0       # note: Twitter calls this "retweet_count" not "repost_count"
    reply_count: int = 0
    quote_count: int = 0
    # impression_count is available only with OAuth 2.0 user context on own tweets
    # For monitored third-party accounts it is typically None
    impression_count: int | None = None
    bookmark_count: int | None = None


class XUserPublicMetrics(BaseModel):
    """
    Documented in Twitter v2 under user.public_metrics.
    https://developer.twitter.com/en/docs/twitter-api/data-dictionary/object-model/user
    """
    followers_count: int = 0
    following_count: int = 0
    tweet_count: int = 0
    listed_count: int = 0


class XUser(BaseModel):
    """
    Twitter v2 User object — only fields we explicitly request.
    """
    id: str                               # Twitter user ID string (e.g. "12345")
    name: str
    username: str                          # without @ prefix
    public_metrics: XUserPublicMetrics = Field(default_factory=XUserPublicMetrics)
    verified: bool = False                 # Legacy blue-check; present in v2
    # verified_type: "blue"|"business"|"government" — added later by Twitter,
    #   not uniformly available; treated as optional
    verified_type: str | None = None
    description: str | None = None
    created_at: datetime | None = None


class XTweet(BaseModel):
    """
    Twitter v2 Tweet object — only the fields we request via tweet.fields param.
    """
    id: str
    text: str
    author_id: str
    created_at: datetime | None = None
    lang: str | None = None
    public_metrics: XPublicMetrics = Field(default_factory=XPublicMetrics)
    # conversation_id, in_reply_to_user_id etc. available but not currently used
    conversation_id: str | None = None
    in_reply_to_user_id: str | None = None


# ── Zora ──────────────────────────────────────────────────────────────────────
# Uncertain fields are documented in zora_field_map.py (CONFIRMED/INFERRED/UNCONFIRMED).

class ZoraCreatorProfile(BaseModel):
    """
    Creator profile as returned by the Zora profile lookup endpoint.
    Field mapping is in zora_field_map.py — update status when API docs confirm.
    """
    wallet_address: str                    # checksummed ERC-55
    display_name: str | None = None        # see zora_field_map.py
    bio: str | None = None                 # see zora_field_map.py
    profile_url: str | None = None         # see zora_field_map.py
    # Social link back to X — only present if creator connected their account
    x_username: str | None = None          # see zora_field_map.py


class ZoraCoinData(BaseModel):
    """
    Static/slow-changing metadata for a single Zora coin.
    Field status (CONFIRMED/INFERRED/UNCONFIRMED) is in zora_field_map.py.
    """
    contract_address: str                  # ERC-55 checksummed, on Base
    symbol: str                            # see zora_field_map.py
    name: str | None = None               # see zora_field_map.py
    creator_address: str | None = None    # see zora_field_map.py
    chain_id: int = 8453                  # Base mainnet — assumed
    decimals: int = 18                    # ERC-20 standard default
    launched_at: datetime | None = None   # see zora_field_map.py


class ZoraCoinMarketState(BaseModel):
    """
    Point-in-time market data for a Zora coin.
    All fields except contract_address are optional because:
      a) Zora REST endpoint shape is not fully confirmed
      b) Some metrics may only be available for coins with sufficient liquidity

    Field status is documented in zora_field_map.py.
    """
    contract_address: str

    # Price
    price_usd: float | None = None         # see zora_field_map.py

    # Liquidity
    # Zora coins use a bonding curve (Uniswap v3 LP); "liquidity" means
    # total TVL in the pool in USD. Exact field TBD.
    liquidity_usd: float | None = None     # see zora_field_map.py

    # Volume windows: see ZoraMarketFields in zora_field_map.py
    volume_5m_usd: float | None = None
    volume_1h_usd: float | None = None
    volume_24h_usd: float | None = None

    # Market cap (price × circulating supply)
    market_cap_usd: float | None = None    # see zora_field_map.py

    # Holder count — may not be available in real-time REST
    holder_count: int | None = None        # see zora_field_map.py

    # Slippage: estimated basis points for a reference ETH trade size
    # If Zora doesn't provide this directly, it will be estimated on-chain
    slippage_bps_for_reference_trade: int | None = None  # see zora_field_map.py


class ZoraTradeSimulation(BaseModel):
    """
    Result of a simulated buy/sell through Zora.
    Simulation shape is unconfirmed — see zora_field_map.ZoraSimFields.
    All fields are optional until confirmed.
    """
    contract_address: str
    trade_side: str                        # "buy" | "sell"
    input_amount_usd: float
    expected_output_tokens: float | None = None   # see zora_field_map.py
    expected_slippage_bps: int | None = None      # see zora_field_map.py
    price_impact_pct: float | None = None         # see zora_field_map.py
    gas_estimate_usd: float | None = None         # see zora_field_map.py
    simulation_timestamp: datetime | None = None


# ── LLM output schema ─────────────────────────────────────────────────────────
# Defined here (canonical types module) and re-exported from llm_client.py.

class LLMScore(BaseModel):
    """
    Structured output that the LLM must return.
    All numeric fields 0-100. None means the LLM produced no value.
    """
    meme_strength: Annotated[int | None, Field(None, ge=0, le=100)] = None
    narrative_fit: Annotated[int | None, Field(None, ge=0, le=100)] = None
    conversion_likelihood: Annotated[int | None, Field(None, ge=0, le=100)] = None
    spam_risk: Annotated[int | None, Field(None, ge=0, le=100)] = None
    summary: str | None = None
    recommendation_bias: str | None = None  # "positive" | "neutral" | "negative"

    def composite_score(self) -> float | None:
        """Weighted composite. Returns None if no sub-scores available."""
        weights = {
            "meme_strength": 0.25,
            "narrative_fit": 0.30,
            "conversion_likelihood": 0.35,
            "spam_risk": -0.10,
        }
        total_w = weighted_sum = 0.0
        for key, w in weights.items():
            val = getattr(self, key)
            if val is not None:
                weighted_sum += w * val
                total_w += abs(w)
        if total_w == 0:
            return None
        raw = weighted_sum / sum(abs(v) for v in weights.values())
        return round(max(0.0, min(100.0, raw)), 1)
