"""
tests/unit/test_llm_client.py
Tests for LLMScore schema validation and composite score calculation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.integrations.llm_client import LLMScore, LLMStubClient


def test_llm_score_valid():
    score = LLMScore(
        meme_strength=80,
        narrative_fit=70,
        conversion_likelihood=75,
        spam_risk=10,
        summary="Strong meme, good narrative.",
        recommendation_bias="positive",
    )
    assert score.meme_strength == 80
    assert score.recommendation_bias == "positive"


def test_llm_score_all_none():
    score = LLMScore()
    assert score.meme_strength is None
    assert score.composite_score() is None


def test_llm_score_composite_with_all_fields():
    score = LLMScore(
        meme_strength=80,
        narrative_fit=80,
        conversion_likelihood=80,
        spam_risk=5,
    )
    composite = score.composite_score()
    assert composite is not None
    assert 0.0 <= composite <= 100.0


def test_llm_score_high_spam_penalises():
    low_spam = LLMScore(
        meme_strength=80, narrative_fit=80,
        conversion_likelihood=80, spam_risk=5,
    )
    high_spam = LLMScore(
        meme_strength=80, narrative_fit=80,
        conversion_likelihood=80, spam_risk=90,
    )
    c_low = low_spam.composite_score()
    c_high = high_spam.composite_score()
    assert c_low is not None and c_high is not None
    assert c_low > c_high


def test_llm_score_out_of_range_raises():
    with pytest.raises(ValidationError):
        LLMScore(meme_strength=150)


def test_llm_score_negative_raises():
    with pytest.raises(ValidationError):
        LLMScore(spam_risk=-1)


@pytest.mark.asyncio
async def test_stub_client_returns_none():
    client = LLMStubClient()
    result = await client.classify_post("Some tweet text", "COIN")
    assert result is None
