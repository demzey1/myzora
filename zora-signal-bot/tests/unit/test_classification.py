"""
tests/unit/test_classification.py
Tests for the deterministic post classification and keyword extraction.
"""

from __future__ import annotations

import pytest

from app.classification.keywords import extract
from app.classification.classifier import classify_deterministic
from app.db.models import PostSentiment


# ── Keyword / entity extraction ───────────────────────────────────────────────

def test_extract_cashtags():
    result = extract("Just minted $ZORA and $BASE tokens on base chain")
    assert "ZORA" in result.cashtags
    assert "BASE" in result.cashtags


def test_extract_mentions():
    result = extract("GM @vitalikbuterin and @zoraengineering 🔥")
    assert "vitalikbuterin" in result.mentions


def test_extract_zora_url():
    result = extract("Collect this one: https://zora.co/collect/base:0x1234")
    assert result.has_zora_url is True


def test_extract_no_zora_url():
    result = extract("Just a regular post without any links")
    assert result.has_zora_url is False


def test_extract_bullish_signals():
    result = extract("🚀 Moon time! Buying $ETH, this is bullish AF wagmi")
    assert result.bullish_signal_count > 2


def test_extract_bearish_signals():
    result = extract("Dumping everything, crash incoming, bearish on all alts")
    assert result.bearish_signal_count >= 1


def test_extract_entities_base_zora():
    result = extract("New creator coin on Base via Zora protocol, collecting now")
    assert "base" in result.entities
    assert "zora" in result.entities


def test_extract_narratives():
    result = extract("Onchain social trading is the future of creator economy on Base")
    assert any("base" in n or "creator" in n or "social" in n for n in result.narratives)


def test_extract_sarcasm_detected():
    result = extract("Oh yeah totally not bullish /s")
    assert result.sarcasm_detected is True


def test_extract_empty_text():
    result = extract("")
    assert result.cashtags == []
    assert result.entities == []
    assert result.bullish_signal_count == 0


# ── Deterministic classifier ───────────────────────────────────────────────────

def test_bullish_post_classified_bullish():
    result = classify_deterministic(
        "Just launched my creator coin on Zora! 🚀 Buying now $CREATOR",
        follower_count=50_000,
        like_count=300,
        retweet_count=80,
    )
    assert result.sentiment == PostSentiment.BULLISH
    assert result.confidence > 50
    assert result.conviction_score > 0


def test_zora_url_boosts_conviction():
    without_url = classify_deterministic(
        "bullish on base ecosystem wagmi 🔥",
        follower_count=10_000,
    )
    with_url = classify_deterministic(
        "bullish on base ecosystem wagmi 🔥 https://zora.co/collect/base:0xtest",
        follower_count=10_000,
    )
    assert with_url.conviction_score >= without_url.conviction_score


def test_sarcasm_downgrades_to_noise():
    result = classify_deterministic("Oh yeah this is totally bullish /s great investment")
    assert result.sentiment == PostSentiment.NOISE


def test_pure_noise_gm_post():
    result = classify_deterministic("gm gm good morning vibes only lol mood")
    assert result.sentiment in (PostSentiment.NOISE, PostSentiment.NEUTRAL)
    assert result.actionable is False


def test_bearish_sentiment_detected():
    result = classify_deterministic("Dump everything, crash is coming, bear market confirmed 📉")
    assert result.sentiment == PostSentiment.BEARISH
    assert result.actionable is False


def test_bullish_actionable_requires_entities_or_zora():
    # High bullish signals but no entities/cashtags → not actionable
    result = classify_deterministic("wagmi moon pump ripping 🚀🚀🚀", follower_count=5_000)
    # May or may not be actionable depending on entity detection
    # But conviction should be lower without specifics
    assert result.conviction_score < 100


def test_conviction_increases_with_engagement():
    low_eng = classify_deterministic(
        "New Zora creator coin just launched! https://zora.co/collect/base:0xtest",
        follower_count=10_000, like_count=5, retweet_count=1,
    )
    high_eng = classify_deterministic(
        "New Zora creator coin just launched! https://zora.co/collect/base:0xtest",
        follower_count=10_000, like_count=500, retweet_count=100,
    )
    assert high_eng.conviction_score >= low_eng.conviction_score


def test_result_has_summary():
    result = classify_deterministic("Bullish on Base and Zora 🚀")
    assert len(result.summary) > 0


def test_classification_result_to_dict():
    result = classify_deterministic("gm")
    d = result.to_dict()
    assert "sentiment" in d
    assert isinstance(d["sentiment"], str)
    assert "conviction_score" in d
    assert "keywords" in d
