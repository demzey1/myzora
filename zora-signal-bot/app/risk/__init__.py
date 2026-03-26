"""
app/risk/__init__.py
─────────────────────────────────────────────────────────────────────────────
Risk management module.

Exports:
  - RiskManager: Enforce trade restrictions
  - RiskCheckResult: Result of risk check
  - WalletVerification: Verify wallet ownership
  - check_trade_allowed: Public API
"""

from app.risk.risk_manager import (
    RiskManager,
    RiskCheckResult,
    check_trade_allowed,
)
from app.risk.wallet_verification import (
    WalletVerification,
    create_wallet_link_challenge,
    verify_wallet_signature,
)

__all__ = [
    "RiskManager",
    "RiskCheckResult",
    "check_trade_allowed",
    "WalletVerification",
    "create_wallet_link_challenge",
    "verify_wallet_signature",
]
