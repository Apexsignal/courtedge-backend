"""
backend_api.py — CourtEdge FastAPI aplikace.

Appka je záměrně tenká vrstva nad db.py / ticket_generation.py /
settlement.py / odds_sync.py / data_ingest.py — logika appky žije v
těch modulech, tenhle soubor jen routuje HTTP požadavky na ně.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

import auth
import data_ingest
import db
import rate_limiter
import settlement
import ticket_generation
from ticket_telegram import send_ticket_to_telegram

app = FastAPI(title="CourtEdge API")

# appka 2026-08-25 přidala CORS jen pro appčin veřejný `/member/*`
# endpoint — appka ho volá přímo z prohlížeče na webu (jiná doména,
# Netlify), ostatní appčiny endpointy appka volá jen server-to-server
# (cron, appka sama), CORS appce tam nic neřeší.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["X-Member-Key"],
)


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


def require_member_key(x_member_key: Optional[str] = Header(None)) -> None:
    """appka 2026-08-25 přidala jako ZJEDNODUŠENOU ochranu webové stránky
    s dnešním tiketem — jeden sdílený klíč pro všechny platící, ne
    per-uživatelské přihlášení. appka to nahradí skutečným
    Stripe/login napojením, až appka postaví checkout (viz README,
    "Co dál chybí"). Do té doby appka klíč rozdá platícím ručně."""
    expected = os.environ.get("MEMBER_ACCESS_KEY")
    if not expected or x_member_key != expected:
        raise HTTPException(403, "Neplatný nebo chybějící X-Member-Key.")


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
# Admin — ingest historických dat přes api-tennis.com (PRIMÁRNÍ cesta,
# viz README — appka na tomhle zdroji nemá licenční problém jako u
# Sackmann/TML CSV, a zvládá i settlement, viz níže).
# ------------------------------------------------------------
class ApiTennisIngestRequest(BaseModel):
    tour: str  # 'atp' | 'wta'
    date_start: date
    date_stop: date


@app.post("/admin/ingest-api-tennis")
def ingest_api_tennis(body: ApiTennisIngestRequest, _: None = Depends(require_admin_key)) -> dict:
    import api_tennis_ingest

    records = api_tennis_ingest.ingest_from_api_tennis(body.tour, body.date_start, body.date_stop)
    for record in records:
        db.upsert_player(record)
    return {"tour": body.tour, "players_updated": len(records)}


# ------------------------------------------------------------
# Admin — sync nadcházejících zápasů + kurzů
# ------------------------------------------------------------
@app.post("/admin/sync-api-tennis")
def sync_api_tennis(tour: str, days_ahead: int = 7, _: None = Depends(require_admin_key)) -> dict:
    import api_tennis_sync
    return api_tennis_sync.sync_upcoming_matches(tour, days_ahead=days_ahead)


@app.post("/admin/sync-odds")
def sync_odds(_: None = Depends(require_admin_key)) -> dict:
    """Záložní/druhý zdroj kurzů (the-odds-api.com) — appka primárně
    používá api-tennis.com (`/admin/sync-api-tennis`), tohle appka
    nechává jako alternativu/cross-check, viz odds_provider.py."""
    import odds_sync
    return odds_sync.sync_upcoming_matches()


# ------------------------------------------------------------
# Admin — generování denních tiketů. Appka od 2026-08-15 posílá DVA:
# hlavní na favority (viz ticket_generation.generate_daily_ticket)
# a vedlejší na gemy (viz ticket_generation.generate_games_ticket,
# README "Vedlejší tiket na gemy"). Appka je posílá nezávisle na
# sobě — pokud appka nenajde kandidáta na jeden z nich, druhý appka
# stejně pošle.
# ------------------------------------------------------------
def _generate_and_send(build_fn, ticket_type: str, no_candidate_reason: str, user_id: Optional[int], send_telegram: bool) -> dict:
    ticket = build_fn(user_id=user_id)
    if ticket is None:
        return {"generated": False, "reason": no_candidate_reason}

    result = {"generated": True, "ticket_id": ticket["id"], "total_odds": float(ticket["total_odds"])}
    if send_telegram and os.environ.get("TELEGRAM_BOT_TOKEN"):
        try:
            send_ticket_to_telegram({**ticket, "ticket_id": ticket["id"], "ticket_type": ticket_type})
        except Exception as exc:
            result["telegram_sent"] = False
            result["telegram_error"] = str(exc)
    return result


@app.post("/admin/daily-tickets")
def daily_tickets(send_telegram: bool = True, _: None = Depends(require_admin_key)) -> dict:
    daily_user_id = os.environ.get("DAILY_TICKETS_USER_ID")
    user_id = int(daily_user_id) if daily_user_id else None

    return {
        "favorites": _generate_and_send(
            ticket_generation.generate_daily_ticket, "favorites",
            "Appka nenašla ani jednoho favorita v pásmu 1,2-1,7.", user_id, send_telegram,
        ),
        "games": _generate_and_send(
            ticket_generation.generate_games_ticket, "games",
            "Appka nenašla ani jednoho použitelného kandidáta na trhu gemů.", user_id, send_telegram,
        ),
    }


# ------------------------------------------------------------
# Admin — ruční zápis výsledku zápasu. Appka tohle používá jako FALLBACK
# — primárně appka výsledky zápasů ingestovaných přes api-tennis.com
# (`/admin/sync-api-tennis`) doplní automaticky v `/admin/settle-all-pending`
# (viz níže, api_tennis_sync.settle_finished_matches). Ruční zápis appka
# potřebuje jen pro zápasy z jiného zdroje (např. the-odds-api fallback).
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
def settle_all_pending(lookback_days: int = 3, _: None = Depends(require_admin_key)) -> dict:
    """Appka nejdřív zkusí AUTOMATICKY doplnit výsledky posledních
    `lookback_days` dní z api-tennis.com (pro oba tury), pak teprve
    vyhodnotí všechny pending tikety, co už mají všechny zápasy hotové."""
    import api_tennis_sync
    from datetime import timedelta

    today = date.today()
    window_start = today - timedelta(days=lookback_days)
    auto_settlement = {}
    for tour in ("atp", "wta"):
        try:
            auto_settlement[tour] = api_tennis_sync.settle_finished_matches(tour, window_start, today)
        except Exception as exc:
            auto_settlement[tour] = {"error": str(exc)}

    return {"auto_settlement": auto_settlement, "tickets": settlement.settle_all_pending()}


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


# ------------------------------------------------------------
# Web s dnešním tiketem — appka to volá přímo z prohlížeče (viz
# netlify_site/), zamčené X-Member-Key (viz require_member_key výš).
# ------------------------------------------------------------
@app.get("/member/today-ticket")
def member_today_ticket(_: None = Depends(require_member_key)) -> dict:
    ticket = db.get_latest_daily_ticket("favorites")
    if ticket is None:
        return {"ready": False}

    from ticket_telegram import selection_label

    legs = [
        {
            "match": f"{leg['player_a']} – {leg['player_b']}",
            "tourney_name": leg["tourney_name"],
            "winner": selection_label(leg["market_code"], leg["selection"], leg["line"], leg["player_a"], leg["player_b"]),
            "odds": float(leg["market_odds"]) if leg["market_odds"] is not None else None,
        }
        for leg in ticket["legs"]
    ]
    return {
        "ready": True,
        "id": ticket["id"],
        "total_odds": float(ticket["total_odds"]),
        "created_at": ticket["created_at"].isoformat(),
        "legs": legs,
    }
