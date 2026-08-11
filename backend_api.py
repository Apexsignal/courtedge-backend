"""
backend_api.py — CourtEdge FastAPI aplikace.

Appka je záměrně tenká vrstva nad db.py / ticket_generation.py /
settlement.py / odds_sync.py / data_ingest.py — logika appky žije v
těch modulech, tenhle soubor jen routuje HTTP požadavky na ně.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr

import auth
import data_ingest
import db
import rate_limiter
import settlement
import ticket_generation
from ticket_telegram import send_ticket_to_telegram

app = FastAPI(title="CourtEdge API")


# ------------------------------------------------------------
# Auth pomocníci
# ------------------------------------------------------------
def get_current_user_id(authorization: Optional[str] = Header(None)) -> int:
    """Appka NIKDY nedůvěřuje user_id poslanému klientem — vždy ho
    odvozuje z podepsaného tokenu (viz auth.py)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Chybí přihlašovací token.")
    user_id = auth.verify_token(authorization.removeprefix("Bearer ").strip())
    if user_id is None:
        raise HTTPException(401, "Neplatný nebo vypršelý token.")
    return user_id


def require_admin_key(x_admin_key: Optional[str] = Header(None)) -> None:
    expected = os.environ.get("ADMIN_TASK_KEY")
    if not expected or x_admin_key != expected:
        raise HTTPException(403, "Neplatný nebo chybějící X-Admin-Key.")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------
# Health
# ------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "courtedge-backend"}


# ------------------------------------------------------------
# Auth
# ------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@app.post("/auth/register")
def register(body: RegisterRequest, request: Request) -> dict:
    if len(body.password) < 8:
        raise HTTPException(400, "Heslo musí mít aspoň 8 znaků.")
    if db.get_user_by_email(body.email):
        raise HTTPException(409, "Účet s tímhle e-mailem už existuje.")
    user = db.create_user(body.email, auth.hash_password(body.password))
    token = auth.create_token(user["id"])
    return {"token": token, "user_id": user["id"]}


@app.post("/auth/login")
def login(body: LoginRequest, request: Request) -> dict:
    ip = _client_ip(request)
    if rate_limiter.is_locked_out(body.email, ip):
        raise HTTPException(429, "Příliš mnoho neúspěšných pokusů. Zkus to znovu později.")

    user = db.get_user_by_email(body.email)
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        rate_limiter.record_failed_attempt(body.email, ip)
        raise HTTPException(401, "Nesprávný e-mail nebo heslo.")

    rate_limiter.record_success(body.email, ip)
    db.touch_last_login(user["id"])
    token = auth.create_token(user["id"])
    return {"token": token, "user_id": user["id"]}


@app.get("/me")
def get_me(user_id: int = Depends(get_current_user_id)) -> dict:
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "Účet nenalezen.")
    user.pop("password_hash", None)
    return user


# ------------------------------------------------------------
# Tikety (uživatelské)
# ------------------------------------------------------------
@app.get("/tickets")
def list_my_tickets(user_id: int = Depends(get_current_user_id)) -> list[dict]:
    return db.get_users_tickets(user_id)


# ------------------------------------------------------------
# Admin — ingest historických dat (Elo/ace-rate/retirement přepočet)
# ------------------------------------------------------------
class IngestRequest(BaseModel):
    tour: str  # 'atp' | 'wta'
    source_dir: Optional[str] = None  # default: env HISTORICAL_DATA_SOURCE


@app.post("/admin/ingest-historical-data")
def ingest_historical_data(body: IngestRequest, _: None = Depends(require_admin_key)) -> dict:
    source_dir = body.source_dir or os.environ.get("HISTORICAL_DATA_SOURCE", "")
    records = data_ingest.ingest_from_source(source_dir, tour=body.tour)
    for record in records:
        db.upsert_player(record)
    return {"tour": body.tour, "players_updated": len(records)}


# ------------------------------------------------------------
# Admin — sync kurzů z the-odds-api
# ------------------------------------------------------------
@app.post("/admin/sync-odds")
def sync_odds(_: None = Depends(require_admin_key)) -> dict:
    import odds_sync
    return odds_sync.sync_upcoming_matches()


# ------------------------------------------------------------
# Admin — generování denního tiketu
# ------------------------------------------------------------
@app.post("/admin/daily-tickets")
def daily_tickets(send_telegram: bool = True, _: None = Depends(require_admin_key)) -> dict:
    daily_user_id = os.environ.get("DAILY_TICKETS_USER_ID")
    ticket = ticket_generation.generate_daily_ticket(user_id=int(daily_user_id) if daily_user_id else None)
    if ticket is None:
        return {"generated": False, "reason": "Appka nenašla platnou kombinaci 2 legů v pásmu kurzu 2,00–3,00."}

    if send_telegram and os.environ.get("TELEGRAM_BOT_TOKEN"):
        try:
            send_ticket_to_telegram({**ticket, "ticket_id": ticket["id"]})
        except Exception as exc:
            return {"generated": True, "ticket_id": ticket["id"], "telegram_sent": False, "telegram_error": str(exc)}

    return {"generated": True, "ticket_id": ticket["id"], "total_odds": float(ticket["total_odds"])}


# ------------------------------------------------------------
# Admin — ruční zápis výsledku zápasu (settlement zdroj zatím NEVYŘEŠEN,
# viz README a settlement.py)
# ------------------------------------------------------------
class MatchResultRequest(BaseModel):
    match_id: int
    winner_id: int
    score: str
    total_games: int
    total_aces: int
    retirement: bool = False


@app.post("/admin/match-result")
def submit_match_result(body: MatchResultRequest, _: None = Depends(require_admin_key)) -> dict:
    db.record_match_result(
        body.match_id, body.winner_id, body.score, body.total_games, body.total_aces, body.retirement,
    )
    return {"match_id": body.match_id, "status": "finished"}


@app.post("/admin/settle-all-pending")
def settle_all_pending(_: None = Depends(require_admin_key)) -> dict:
    return settlement.settle_all_pending()


# ------------------------------------------------------------
# Stripe webhook — appka teprve čeká na SAMOSTATNÝ Stripe účet (viz
# README), scaffold appka nechává připravený, ne aktivně zapojený.
# ------------------------------------------------------------
@app.post("/stripe/webhook")
async def stripe_webhook(request: Request) -> dict:
    import stripe

    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        raise HTTPException(503, "Stripe webhook není nakonfigurovaný (STRIPE_WEBHOOK_SECRET chybí).")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(400, "Neplatný Stripe webhook signature.")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_details", {}).get("email")
        if customer_email:
            user = db.get_user_by_email(customer_email)
            # Appka tady zatím jen loguje — skutečné napojení na
            # subscription_tier/subscription_until appka doplní, až
            # bude znát reálné ceníkové plány (viz README).
            if user:
                pass

    return {"received": True}
