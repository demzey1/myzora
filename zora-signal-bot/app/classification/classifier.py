"""
app/classification/classifier.py
─────────────────────────────────────────────────────────────────────────────
Post classification pipeline.

Priority:
  1. Deterministic rules (always runs — fast, no API cost)
  2. LLM enrichment (optional, if enable_llm_classification=true)

If LLM fails or is disabled, the deterministic result is used as-is.
The output schema mirrors the spec:

  {
    "actionable": true,
    "sentiment": "bullish",
    "confidence": 84,
    "conviction_score": 78,
    "entities": ["base", "zora"],
    "keywords": ["base", "coins"],
    "narratives": ["base ecosystem"],
    "summary": "creator is positively signaling..."
  }
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.classification.keywords import ExtractionResult, extract
from app.db.models import PostSentiment
from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class ClassificationResult:
    actionable: bool = False
    sentiment: PostSentiment = PostSentiment.NOISE
    confidence: int = 0       # 0–100
    conviction_score: int = 0  # 0–100
    entities: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    narratives: list[str] = field(default_factory=list)
    summary: str = ""
    used_llm: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sentiment"] = self.sentiment.value
        return d


# ── Deterministic classifier ───────────────────────────────────────────────────

def classify_deterministic(
    text: str,
    follower_count: int = 0,
    like_count: int = 0,
    retweet_count: int = 0,
) -> ClassificationResult:
    """
    Pure deterministic classification from post text + engagement metrics.
    Never calls external APIs.
    """
    ex: ExtractionResult = extract(text)
    result = ClassificationResult(
        entities=ex.entities,
        keywords=ex.keywords,
        narratives=ex.narratives,
    )

    # ── Sarcasm / noise guard ─────────────────────────────────────────────
    if ex.sarcasm_detected:
        result.sentiment = PostSentiment.NOISE
        result.confidence = 30
        result.summary = "Likely sarcastic or non-actionable content."
        return result

    if ex.noise_signal_count >= 3 and ex.bullish_signal_count == 0:
        result.sentiment = PostSentiment.NOISE
        result.confidence = 40
        result.summary = "Appears to be social noise with no trading signal."
        return result

    # ── Sentiment decision ────────────────────────────────────────────────
    net = ex.bullish_signal_count - ex.bearish_signal_count * 2  # bearish weighted 2x

    if net > 2:
        result.sentiment = PostSentiment.BULLISH
    elif ex.bearish_signal_count > ex.bullish_signal_count:
        result.sentiment = PostSentiment.BEARISH
    elif ex.bullish_signal_count > 0 or ex.has_zora_url or ex.cashtags:
        result.sentiment = PostSentiment.BULLISH
    else:
        result.sentiment = PostSentiment.NEUTRAL

    # ── Confidence score ──────────────────────────────────────────────────
    confidence = 30  # base
    confidence += min(ex.bullish_signal_count * 8, 30)
    confidence += min(len(ex.entities) * 5, 20)
    confidence += 15 if ex.has_zora_url else 0
    confidence += 10 if ex.cashtags else 0
    if result.sentiment == PostSentiment.BEARISH:
        confidence = max(20, confidence - 10)
    result.confidence = min(confidence, 95)

    # ── Conviction score (combines sentiment + engagement) ────────────────
    engagement_score = 0
    if follower_count > 0:
        er = (like_count + retweet_count * 2) / max(follower_count, 1)
        engagement_score = min(int(er * 100 * 50), 30)  # cap at 30

    conviction = (result.confidence * 0.6) + engagement_score
    # Boost for Zora URL (creator explicitly linking)
    if ex.has_zora_url:
        conviction = min(conviction + 15, 100)
    result.conviction_score = int(min(conviction, 100))

    # ── Actionability ─────────────────────────────────────────────────────
    result.actionable = (
        result.sentiment == PostSentiment.BULLISH
        and result.conviction_score >= 40
        and (ex.has_zora_url or ex.cashtags or len(ex.entities) >= 2)
    )

    # ── Summary ───────────────────────────────────────────────────────────
    if result.sentiment == PostSentiment.BULLISH:
        topics = ", ".join(ex.entities[:3]) if ex.entities else "crypto/Zora assets"
        result.summary = (
            f"Creator signals positive intent toward {topics}. "
            f"Confidence: {result.confidence}."
        )
    elif result.sentiment == PostSentiment.BEARISH:
        result.summary = "Creator signals caution or negative sentiment."
    else:
        result.summary = "Neutral or non-actionable content."

    return result


# ── LLM-enriched classification ────────────────────────────────────────────────

async def classify_with_llm(
    text: str,
    deterministic: ClassificationResult,
) -> ClassificationResult:
    """
    Optionally enrich the deterministic result with LLM classification.
    If LLM fails, returns the deterministic result unchanged.
    """
    from app.config import settings
    if not settings.enable_llm_classification:
        return deterministic

    from app.integrations.llm_client import get_llm_client
    client = get_llm_client()

    try:
        coin_symbol = deterministic.keywords[0].upper() if deterministic.keywords else "UNKNOWN"
        llm_score = await client.classify_post(post_text=text, coin_symbol=coin_symbol)
    except Exception as exc:
        log.warning("llm_classification_failed", error=str(exc))
        return deterministic

    if llm_score is None:
        return deterministic

    # Blend LLM results with deterministic
    enriched = ClassificationResult(
        entities=deterministic.entities,
        keywords=deterministic.keywords,
        narratives=deterministic.narratives,
        used_llm=True,
    )

    # Use LLM spam risk to gate
    if llm_score.spam_risk and llm_score.spam_risk > 70:
        enriched.sentiment = PostSentiment.NOISE
        enriched.confidence = 20
        enriched.conviction_score = 0
        enriched.actionable = False
        enriched.summary = llm_score.summary or "LLM flagged as spam/noise."
        return enriched

    # Blend confidence: 60% deterministic, 40% LLM conversion_likelihood
    llm_conf = llm_score.conversion_likelihood or deterministic.confidence
    blended_conf = int(deterministic.confidence * 0.6 + llm_conf * 0.4)

    # Use deterministic sentiment as primary; LLM recommendation_bias as modifier
    enriched.sentiment = deterministic.sentiment
    if llm_score.recommendation_bias == "negative" and deterministic.sentiment == PostSentiment.BULLISH:
        enriched.sentiment = PostSentiment.NEUTRAL
    elif llm_score.recommendation_bias == "positive":
        enriched.sentiment = PostSentiment.BULLISH

    enriched.confidence = min(blended_conf, 95)
    enriched.conviction_score = min(
        int(deterministic.conviction_score * 0.6 + (llm_score.meme_strength or 50) * 0.4),
        100,
    )
    enriched.actionable = (
        enriched.sentiment == PostSentiment.BULLISH
        and enriched.conviction_score >= 40
    )
    enriched.summary = llm_score.summary or deterministic.summary
    return enriched


# ── Main entry point ───────────────────────────────────────────────────────────

async def classify_post(
    text: str,
    follower_count: int = 0,
    like_count: int = 0,
    retweet_count: int = 0,
) -> ClassificationResult:
    """
    Full classification pipeline: deterministic → optional LLM enrichment.
    Always returns a result (never raises).
    """
    try:
        det = classify_deterministic(text, follower_count, like_count, retweet_count)
    except Exception as exc:
        log.error("deterministic_classification_failed", error=str(exc))
        return ClassificationResult(summary="Classification error.")

    try:
        return await classify_with_llm(text, det)
    except Exception as exc:
        log.warning("llm_classification_error", error=str(exc))
        return det
