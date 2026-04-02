from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import get_db
from app.logging_config import get_logger
from app.services.wallet_linking import (
    set_nonce_address,
    verify_and_finalize,
    verify_session_url_signature,
)

log = get_logger(__name__)
router = APIRouter(prefix="/wallet", tags=["wallet"])


class NonceRequest(BaseModel):
    address: str
    session_token: str


class NonceResponse(BaseModel):
    nonce: str
    session_token: str
    expires_in_seconds: int


class VerifyRequest(BaseModel):
    session_token: str
    address: str
    signature: str


class VerifyResponse(BaseModel):
    success: bool
    message: str


@router.get("/connect", response_class=HTMLResponse)
async def wallet_connect_page(
    session: str = Query(...),
    sig: str = Query(...),
) -> str:
    if not settings.enable_wallet_linking:
        raise HTTPException(status_code=404, detail="Not found")

    if not verify_session_url_signature(session, sig):
        raise HTTPException(status_code=403, detail="Invalid session link")

    html = f"""<!DOCTYPE html>
<html>
<head>
  <title>Zora Signal Bot - Link Wallet</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 480px; margin: 60px auto; padding: 20px; text-align: center; }}
    h1 {{ font-size: 1.4rem; }}
    .btn {{ background: #0052FF; color: white; border: none; border-radius: 8px;
            padding: 14px 28px; font-size: 1rem; cursor: pointer; margin-top: 20px; }}
    .btn:hover {{ background: #0040CC; }}
    .status {{ margin-top: 20px; color: #666; font-size: 0.9rem; }}
    .success {{ color: #00AA44; font-weight: bold; }}
    .error   {{ color: #CC0000; font-weight: bold; }}
  </style>
</head>
<body>
  <h1>Link Your Wallet</h1>
  <p>Connect your wallet to link it to your Telegram account on Zora Signal Bot.</p>
  <p style="font-size:0.8rem;color:#999">
    You will be asked to sign a message. This does <strong>not</strong> cost gas
    and does <strong>not</strong> grant trading permissions.
  </p>

  <button class="btn" id="connectBtn" onclick="connectWallet()">Connect Wallet</button>
  <div class="status" id="status"></div>

  <script>
    const SESSION_TOKEN = "{session}";

    async function connectWallet() {{
      const status = document.getElementById("status");
      if (!window.ethereum) {{
        status.className = "status error";
        status.textContent = "No wallet detected. Please install MetaMask.";
        return;
      }}
      try {{
        status.textContent = "Requesting accounts...";
        const accounts = await window.ethereum.request({{ method: "eth_requestAccounts" }});
        const address = accounts[0];
        status.textContent = `Connected: ${{address.slice(0,6)}}...${{address.slice(-4)}}. Fetching nonce...`;

        const nonceResp = await fetch("/wallet/nonce", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ address, session_token: SESSION_TOKEN }})
        }});
        const {{ nonce }} = await nonceResp.json();

        status.textContent = "Please sign the message in your wallet...";
        const signature = await window.ethereum.request({{
          method: "personal_sign",
          params: [nonce, address]
        }});

        status.textContent = "Verifying signature...";
        const verifyResp = await fetch("/wallet/verify", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ session_token: SESSION_TOKEN, address, signature }})
        }});
        const result = await verifyResp.json();
        status.className = result.success ? "status success" : "status error";
        status.textContent = result.message;
        if (result.success) {{
          document.getElementById("connectBtn").style.display = "none";
        }}
      }} catch(e) {{
        status.className = "status error";
        status.textContent = "Error: " + (e.message || e);
      }}
    }}
  </script>
</body>
</html>"""
    return html


@router.post("/nonce", response_model=NonceResponse)
async def get_nonce(
    req: NonceRequest,
    db: AsyncSession = Depends(get_db),
) -> NonceResponse:
    if not settings.enable_wallet_linking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet linking is disabled")

    nonce = await set_nonce_address(db, req.session_token, req.address)
    if nonce is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid. Please request a new link via /linkwallet.",
        )

    return NonceResponse(
        nonce=nonce,
        session_token=req.session_token,
        expires_in_seconds=settings.wallet_nonce_ttl_seconds,
    )


@router.post("/verify", response_model=VerifyResponse)
async def verify_signature(
    req: VerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> VerifyResponse:
    if not settings.enable_wallet_linking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet linking is disabled")

    ok, msg = await verify_and_finalize(db, req.session_token, req.address, req.signature)
    if ok:
        await db.commit()
        _notify_wallet_linked(req.address)

    return VerifyResponse(success=ok, message=msg)


@router.get("/status")
async def session_status(
    session_token: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not settings.enable_wallet_linking:
        return {"valid": False, "reason": "wallet_linking_disabled"}

    from app.db.repositories.wallet import WalletLinkNonceRepository

    repo = WalletLinkNonceRepository(db)
    nonce_row = await repo.get_valid_nonce(session_token)
    if nonce_row is None:
        return {"valid": False, "reason": "expired_or_used"}
    return {"valid": True, "address": nonce_row.wallet_address}


def _notify_wallet_linked(wallet_address: str) -> None:
    try:
        from app.jobs.tasks.wallet_tasks import notify_wallet_linked_telegram

        notify_wallet_linked_telegram.apply_async(
            kwargs={"wallet_address": wallet_address}, queue="alerts"
        )
    except Exception as exc:
        log.warning("wallet_notify_schedule_failed", error=str(exc))
