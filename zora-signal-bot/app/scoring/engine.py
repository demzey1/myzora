"""
app/scoring/engine.py
─────────────────────────────────────────────────────────────────────────────
Deterministic scoring engine.

Design goals:
  1. Every sub-score is named, bounded [0, 100], and documented.
  2. The final deterministic score is a weighted sum of sub-scores.
  3. Hard disqualifiers (too new, no liquidity) force score to 0 regardless
     of social signal strength — these are checked BEFORE soft scoring.
  4. The LLM score is a MODIFIER applied after the deterministic pass.
  5. ScoreResult is a dataclass — fully serialisable, loggable, auditable.

Score formula:
  deterministic = Σ(weight_i × sub_score_i)

  sub_scores:
    A. Social (50% weight total)
       a1. follower_tier         (10%) — audience size proxy
       a2. engagement_rate       (15%) — likes+rt+reply+quote / followers
       a3. engagement_velocity   (15%) — velocity of likes+rt per minute
       a4. post_freshness        (10%) — younger posts score higher

    B. Coin (40% weight total)
       b1. coin_existence        (10%) — no coin = 0
       b2. coin_maturity         (10%) — not brand-new, not ancient
       b3. liquidity_score       (10%) — higher liquidity = higher score
       b4. volume_momentum       (10%) — rising 5m volume

    C. Context (10% weight total)
       c1. time_of_day           (10%) — peak hours score higher

  final = deterministic (if LLM disabled)
        = 0.7 × deterministic + 0.3 × llm_composite (if LLM enabled)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import NamedTuple

from app.config import settings
from app.integrations.llm_client import LLMScore
from app.scoring.features import FeatureSet


# ── Sub-score breakdown ────────────────────────────────────────────────────────

@dataclass
class ScoreBreakdown:
    """Named sub-scores in [0, 100]. Each field maps to a scoring component."""
    # Social
    follower_tier: float = 0.0
    engagement_rate_score: float = 0.0
    velocity_score: float = 0.0
    freshness_score: float = 0.0
    # Coin
    coin_existence_score: float = 0.0
    coin_maturity_score: float = 0.0
    liquidity_score: float = 0.0
    volume_momentum_score: float = 0.0
    # Context
    time_of_day_score: float = 0.0


@dataclass
class ScoreResult:
    """
    Complete scoring result for one signal candidate.
    Persisted to the signals table.
    """
    deterministic_score: float
    llm_score: float | None
    final_score: float
    breakdown: ScoreBreakdown
    disqualified: bool                   # True if a hard rule blocked the score
    disqualify_reasons: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)

    @property
    def risk_notes_str(self) -> str:
        return " / ".join(self.risk_notes) if self.risk_notes else ""


# ── Scoring engine ─────────────────────────────────────────────────────────────

class ScoringEngine:
    """
    Pure scoring engine — no I/O, no DB access.
    Instantiate once; call score() for each candidate.
    """

    # Component weights (must sum to 1.0)
    WEIGHTS = {
        "follower_tier":         0.10,
        "engagement_rate_score": 0.15,
        "velocity_score":        0.15,
        "freshness_score":       0.10,
        "coin_existence_score":  0.10,
        "coin_maturity_score":   0.10,
        "liquidity_score":       0.10,
        "volume_momentum_score": 0.10,
        "time_of_day_score":     0.10,
    }

    # LLM blend weight when LLM is available
    LLM_WEIGHT = 0.30
    DETERMINISTIC_WEIGHT = 0.70

    def score(
        self,
        features: FeatureSet,
        llm_score: LLMScore | None = None,
    ) -> ScoreResult:
        """
        Compute the full ScoreResult for the given feature set.
        llm_score is optional; if None the final score == deterministic score.
        """
        disqualifiers: list[str] = []
        risk_notes: list[str] = []

        # ── Hard disqualifiers — checked first ────────────────────────────────
        if not features.coin.coin_exists:
            disqualifiers.append("no_zora_coin_mapped")

        if features.coin.is_new_coin:
            disqualifiers.append(
                f"coin_launch_lockout ({settings.no_trade_after_launch_seconds}s)"
            )
            risk_notes.append("launch window")

        if not features.coin.has_sufficient_liquidity:
            liq = features.coin.liquidity_usd or 0
            disqualifiers.append(
                f"liquidity_too_low (${liq:,.0f} < ${settings.min_liquidity_usd:,.0f})"
            )
            risk_notes.append("low liquidity")

        if not features.coin.slippage_acceptable:
            risk_notes.append("high slippage")

        if disqualifiers:
            bd = ScoreBreakdown()  # All zeros
            return ScoreResult(
                deterministic_score=0.0,
                llm_score=None,
                final_score=0.0,
                breakdown=bd,
                disqualified=True,
                disqualify_reasons=disqualifiers,
                risk_notes=risk_notes,
            )

        # ── Soft scoring ──────────────────────────────────────────────────────
        bd = ScoreBreakdown(
            follower_tier=self._score_follower_tier(features),
            engagement_rate_score=self._score_engagement_rate(features),
            velocity_score=self._score_velocity(features),
            freshness_score=self._score_freshness(features),
            coin_existence_score=100.0,  # Already checked above
            coin_maturity_score=self._score_coin_maturity(features),
            liquidity_score=self._score_liquidity(features),
            volume_momentum_score=self._score_volume_momentum(features),
            time_of_day_score=self._score_time_of_day(features),
        )

        # Risk notes for advisory (not disqualifying)
        if features.coin.holder_count is not None and features.coin.holder_count < 20:
            risk_notes.append("concentrated holders")
        if features.coin.coin_age_minutes is not None and features.coin.coin_age_minutes < 60:
            risk_notes.append("very new coin")

        det_score = self._weighted_sum(bd)
        llm_composite: float | None = None

        if llm_score is not None:
            llm_composite = llm_score.composite_score()
            if llm_composite is not None:
                if llm_score.spam_risk is not None and llm_score.spam_risk > 70:
                    risk_notes.append("high LLM spam risk")
                final = (
                    self.DETERMINISTIC_WEIGHT * det_score
                    + self.LLM_WEIGHT * llm_composite
                )
            else:
                final = det_score
        else:
            final = det_score

        return ScoreResult(
            deterministic_score=round(det_score, 1),
            llm_score=round(llm_composite, 1) if llm_composite is not None else None,
            final_score=round(min(final, 100.0), 1),
            breakdown=bd,
            disqualified=False,
            disqualify_reasons=[],
            risk_notes=risk_notes,
        )

    # ── Sub-scorers ────────────────────────────────────────────────────────────

    @staticmethod
    def _score_follower_tier(f: FeatureSet) -> float:
        """
        Buckets: 0=<1k (10), 1=1-10k (30), 2=10-100k (60),
                 3=100-500k (85), 4=500k+ (100)
        """
        return [10.0, 30.0, 60.0, 85.0, 100.0][f.social.follower_bucket]

    @staticmethod
    def _score_engagement_rate(f: FeatureSet) -> float:
        """
        Sigmoid-like mapping: 0% → 0, 0.5% → 50, 2% → 80, 5%+ → 100
        """
        rate_pct = f.social.engagement_rate * 100
        # Logistic-style: score = 100 / (1 + e^(-k*(x-x0)))
        return round(100.0 / (1.0 + math.exp(-0.8 * (rate_pct - 2.0))), 1)

    @staticmethod
    def _score_velocity(f: FeatureSet) -> float:
        """
        Velocity of likes per minute (0 if not available).
        0 vel → 0, 10/min → 50, 50/min → 80, 200+/min → 100
        """
        vel = f.social.likes_velocity_per_min
        if vel is None:
            return 0.0
        return round(min(100.0, 100.0 * (1 - math.exp(-vel / 50.0))), 1)

    @staticmethod
    def _score_freshness(f: FeatureSet) -> float:
        """
        Post age decay: < 5m → 100, 30m → 70, 2h → 30, > 6h → 0
        """
        age = f.social.post_age_minutes
        if age is None:
            return 50.0  # Unknown — neutral
        if age < 5:
            return 100.0
        if age > 360:
            return 0.0
        # Linear decay between 5 min and 360 min
        return round(max(0.0, 100.0 * (1 - (age - 5) / (360 - 5))), 1)

    @staticmethod
    def _score_coin_maturity(f: FeatureSet) -> float:
        """
        Sweet spot: 10–120 min old → peak score.
        Too new (<10m, enforced by disqualifier) or too old (>30 days) score lower.
        """
        age_m = f.coin.coin_age_minutes
        if age_m is None:
            return 40.0  # Unknown — below neutral
        if age_m < 10:
            return 10.0  # Should be caught by disqualifier; belt-and-suspenders
        if age_m < 120:
            return 100.0
        if age_m < 1440:  # < 1 day
            return 70.0
        if age_m < 10080:  # < 1 week
            return 50.0
        if age_m < 43200:  # < 30 days
            return 30.0
        return 10.0

    @staticmethod
    def _score_liquidity(f: FeatureSet) -> float:
        """
        $10k (minimum) → 10, $50k → 50, $200k → 80, $1M+ → 100
        """
        liq = f.coin.liquidity_usd
        if liq is None:
            return 0.0
        if liq < 10_000:
            return 0.0
        if liq >= 1_000_000:
            return 100.0
        # Log-scale interpolation between $10k and $1M
        return round(
            10.0 + 90.0 * (math.log10(liq) - math.log10(10_000))
            / (math.log10(1_000_000) - math.log10(10_000)),
            1,
        )

    @staticmethod
    def _score_volume_momentum(f: FeatureSet) -> float:
        """
        5-minute USD volume as a proxy for buy pressure.
        $0 → 0, $1k → 30, $5k → 60, $20k+ → 100
        """
        vol = f.coin.volume_5m_usd
        if vol is None:
            return 0.0
        if vol <= 0:
            return 0.0
        if vol >= 20_000:
            return 100.0
        return round(min(100.0, 100.0 * math.log1p(vol) / math.log1p(20_000)), 1)

    @staticmethod
    def _score_time_of_day(f: FeatureSet) -> float:
        """
        Crypto attention peaks 14:00–22:00 UTC (US/EU overlap).
        Off-peak hours score lower.
        Scores: peak window → 100, overnight → 30, shoulder → 60.
        """
        h = f.context.hour_of_day_utc
        if 14 <= h < 22:
            return 100.0
        if 10 <= h < 14 or 22 <= h < 24:
            return 60.0
        return 30.0

    def _weighted_sum(self, bd: ScoreBreakdown) -> float:
        total = 0.0
        for key, weight in self.WEIGHTS.items():
            total += weight * getattr(bd, key)
        return round(min(total, 100.0), 1)


# ── Singleton ──────────────────────────────────────────────────────────────────
_engine: ScoringEngine | None = None


def get_scoring_engine() -> ScoringEngine:
    global _engine
    if _engine is None:
        _engine = ScoringEngine()
    return _engine
