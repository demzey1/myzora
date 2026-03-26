"""
app/classification/keywords.py
─────────────────────────────────────────────────────────────────────────────
Deterministic keyword, entity, and narrative extraction from post text.

No external API calls. Falls back gracefully if text is empty.
All functions are pure — no side effects, fully testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Keyword lists ──────────────────────────────────────────────────────────────

_BULLISH_SIGNALS = frozenset([
    "bullish", "moon", "mooning", "pump", "pumping", "ripping", "up only",
    "buying", "bought", "accumulate", "accumulating", "long", "going up",
    "all time high", "ath", "breaking out", "breakout", "launch", "launching",
    "deploying", "just dropped", "dropping", "new coin", "creator coin",
    "minting", "minted", "collect", "collecting", "let's go", "letsgo",
    "ngmi", "wagmi", "alpha", "gem", "based", "bullrun", "bull run",
    "excited", "big news", "announcement", "huge", "major", "big drop",
    "fr fr", "no cap", "fire", "🔥", "🚀", "💎", "🌙", "📈",
])

_BEARISH_SIGNALS = frozenset([
    "bearish", "dump", "dumping", "crash", "crashing", "falling", "down",
    "sell", "selling", "short", "overvalued", "rug", "rugpull", "scam",
    "avoid", "beware", "warning", "caution", "📉", "🐻",
])

_NOISE_SIGNALS = frozenset([
    "gm", "good morning", "good night", "gn", "ngmi", "lol", "lmao",
    "just woke up", "vibes", "mood", "random", "idk", "whatever",
    "off topic", "not crypto", "not finance",
])

_SARCASM_SIGNALS = frozenset([
    "totally not", "definitely not", "suuure", "yeah right", "lol ok",
    "/s", "sarcasm", "obviously", "clearly",
])

# ── Entity vocabulary ──────────────────────────────────────────────────────────

_CRYPTO_ENTITIES = {
    # Chains
    "base": "chain",
    "ethereum": "chain",
    "eth": "chain",
    "polygon": "chain",
    "optimism": "chain",
    "arbitrum": "chain",
    "solana": "chain",
    "sol": "chain",
    # Protocols
    "zora": "protocol",
    "uniswap": "protocol",
    "opensea": "protocol",
    "friend.tech": "protocol",
    "farcaster": "protocol",
    "lens": "protocol",
    # Concepts
    "creator coin": "concept",
    "social token": "concept",
    "content coin": "concept",
    "nft": "concept",
    "defi": "concept",
    "dao": "concept",
    "airdrop": "concept",
    "allowlist": "concept",
    "whitelist": "concept",
    "mint": "concept",
    "collect": "concept",
}

# ── Narrative tags ─────────────────────────────────────────────────────────────

_NARRATIVE_PATTERNS: list[tuple[str, str]] = [
    # (regex pattern, narrative tag)
    (r"\bbase\b", "base ecosystem"),
    (r"\bzora\b", "zora protocol"),
    (r"\bcreator\s*(coin|economy|token)\b", "creator economy"),
    (r"\bcontent\s*coin\b", "content coins"),
    (r"\bsocial\s*trad(ing|e)\b", "social trading"),
    (r"\bonchain\b|\bon.chain\b|\bon chain\b", "onchain activity"),
    (r"\bmeme\b|\bmemecoin\b", "meme coins"),
    (r"\bairdrop\b", "airdrop"),
    (r"\b(nft|pfp|art)\b", "nft/art"),
    (r"\b(defi|yield|farm)\b", "defi"),
    (r"\b(dao|governance|vote)\b", "dao/governance"),
    (r"\b(launch|deploy|mint)\b", "new launch"),
    (r"\b(alpha|gem|early)\b", "alpha signal"),
]


# ── Output schema ──────────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    narratives: list[str] = field(default_factory=list)
    bullish_signal_count: int = 0
    bearish_signal_count: int = 0
    noise_signal_count: int = 0
    sarcasm_detected: bool = False
    cashtags: list[str] = field(default_factory=list)     # $TICKER
    mentions: list[str] = field(default_factory=list)     # @handle
    urls: list[str] = field(default_factory=list)
    has_zora_url: bool = False


# ── Main extractor ─────────────────────────────────────────────────────────────

def extract(text: str) -> ExtractionResult:
    """
    Run deterministic extraction on post text.
    Returns ExtractionResult with all extracted signals.
    """
    if not text:
        return ExtractionResult()

    text_lower = text.lower()
    result = ExtractionResult()

    # ── Cashtags ($TICKER) ───────────────────────────────────────────────────
    result.cashtags = list({
        m.group(1).upper()
        for m in re.finditer(r"\$([A-Za-z]{2,10})\b", text)
    })

    # ── Mentions ─────────────────────────────────────────────────────────────
    result.mentions = list({
        m.group(1).lower()
        for m in re.finditer(r"@([A-Za-z0-9_]{1,50})", text)
    })

    # ── URLs + Zora detection ────────────────────────────────────────────────
    result.urls = re.findall(r"https?://\S+", text)
    result.has_zora_url = any(
        "zora.co" in url or "zora.co/collect" in url
        for url in result.urls
    )

    # ── Bullish / bearish / noise signal counting ────────────────────────────
    words = set(re.findall(r"\b\w+\b", text_lower))
    # Also check emojis (not word-tokenised)
    for sig in _BULLISH_SIGNALS:
        if sig in text_lower or sig in words:
            result.bullish_signal_count += 1

    for sig in _BEARISH_SIGNALS:
        if sig in text_lower or sig in words:
            result.bearish_signal_count += 1

    for sig in _NOISE_SIGNALS:
        if sig in text_lower or sig in words:
            result.noise_signal_count += 1

    for sig in _SARCASM_SIGNALS:
        if sig in text_lower:
            result.sarcasm_detected = True
            break

    # ── Entity extraction ────────────────────────────────────────────────────
    for term, _etype in _CRYPTO_ENTITIES.items():
        pattern = r"\b" + re.escape(term) + r"\b"
        if re.search(pattern, text_lower):
            result.entities.append(term)

    # ── Keyword extraction: cashtags + entities + high-signal words ──────────
    keywords: set[str] = set(result.cashtags)
    for e in result.entities:
        keywords.add(e)
    # Add strong bullish signal words found
    for w in words & (_BULLISH_SIGNALS | _BEARISH_SIGNALS):
        if len(w) > 3:  # skip short noise like "up"
            keywords.add(w)
    result.keywords = sorted(keywords)[:15]  # cap at 15

    # ── Narrative tags ───────────────────────────────────────────────────────
    seen: set[str] = set()
    for pattern, tag in _NARRATIVE_PATTERNS:
        if re.search(pattern, text_lower) and tag not in seen:
            result.narratives.append(tag)
            seen.add(tag)

    return result
