"""
backend_api.py — CourtEdge FastAPI aplikace.

Appka je záměrně tenká vrstva nad db.py / ticket_generation.py /
settlement.py / odds_sync.py / data_ingest.py — logika appky žije v
těch modulech, tenhle soubor jen routuje HTTP požadavky na ně.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

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

# appka 2026-08-25 přidala CORS pro appčinu webovou stránku s dnešním
# tiketem (jiná doména, Netlify) — appka odtamtud volá přihlášení,
# registraci, uplatnění kupónu i samotný tiket přímo z prohlížeče.
# Ostatní appčiny endpointy appka volá jen server-to-server (cron,
# appka sama), CORS appce tam nic neřeší.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
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


def require_active_subscription(user_id: int = Depends(get_current_user_id)) -> int:
    """Appka nahradila dřívější jeden sdílený klíč skutečným
    přihlášením — appka teď ověřuje `subscription_until` konkrétního
    uživatele, ne jedno společné heslo pro všechny. Aktivní ho
    uživatel má buď zaplacením (Stripe appka ještě nemá napojený),
    nebo uplatněním kódu (viz `db.redeem_coupon`)."""
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "Účet nenalezen.")
    if not user["subscription_until"] or user["subscription_until"] < datetime.now(timezone.utc):
        raise HTTPException(402, "Nemáš aktivní předplatné.")
    return user_id


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------
# Health
# ------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "courtedge-backend"}


# ------------------------------------------------------------
# Admin — jednorázové založení schématu na čerstvé DB (viz db.apply_schema_file).
# ------------------------------------------------------------
@app.post("/admin/apply-schema")
def apply_schema(_: None = Depends(require_admin_key)) -> dict:
    try:
        message = db.apply_schema_file()
    except Exception as exc:
        raise HTTPException(500, f"Schéma se nepodařilo aplikovat: {exc}")
    return {"message": message}


# ------------------------------------------------------------
# Admin — nahrání JSON snapshotu hráčů s předpočítaným Elo (viz
# db.load_player_snapshot) — appka to používá pro nasazení na čerstvý
# Render, ať appce nemusí znovu stahovat historii z api-tennis.com.
# ------------------------------------------------------------
@app.post("/admin/ensure-user-bets-table")
def ensure_user_bets_table(_: None = Depends(require_admin_key)) -> dict:
    return {"message": db.ensure_user_bets_table()}


@app.post("/admin/load-snapshot")
def load_snapshot(filename: str, _: None = Depends(require_admin_key)) -> dict:
    try:
        return db.load_player_snapshot(filename)
    except FileNotFoundError:
        raise HTTPException(404, f"Snapshot '{filename}' appka v repozitáři nenašla.")


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
# Admin — generování denního tiketu. Appka od 2026-08-15 do 2026-08-26
# posílala DVA tikety (hlavní na favority + vedlejší na gemy), uživatel
# ale chtěl do Telegramu posílat jen favority (výherce) — appka proto
# přestala gemový tiket v denním cyklu volat. `ticket_generation.
# generate_games_ticket` appka v modulu nechává nedotčenou, appka ji
# funkčně otestovala i reálnými daty a mohla by se hodit znovu.
# ------------------------------------------------------------
def _generate_and_send(build_fn, ticket_type: str, no_candidate_reason: str, user_id: Optional[int], send_telegram: bool, **build_kwargs) -> dict:
    ticket = build_fn(user_id=user_id, **build_kwargs)
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
def daily_tickets(
    send_telegram: bool = True, force: bool = False, exclude_players: str = "",
    _: None = Depends(require_admin_key),
) -> dict:
    daily_user_id = os.environ.get("DAILY_TICKETS_USER_ID")
    user_id = int(daily_user_id) if daily_user_id else None
    exclude_set = {p.strip() for p in exclude_players.split(",") if p.strip()} or None

    # Pojistka proti dvojímu vygenerování ve stejný den (2026-08-27) — appka
    # dřív žádnou neměla, takže druhé volání (ruční test, retry po chybě...)
    # tiše přepsalo ranní tiket novým, i kdyby mezitím některý zápas z
    # prvního tiketu už začal/skončil a appka ho musela z nové kombinace
    # vynechat (reálně nastalo — appka místo dobrého ranního tiketu vrátila
    # horší náhradu). Appka teď radši vrátí ten, co už dnes vygenerovala —
    # `force=true` appku obchází (appka to potřebuje jen výjimečně, např.
    # hned po nasazení opravy výběru kandidátů, appka ať nemusí čekat na
    # zítřek), volající za to nese odpovědnost sám.
    today_prague = datetime.now(ZoneInfo("Europe/Prague"))
    today_start_utc = today_prague.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    if not force and db.count_daily_tickets_since("favorites", today_start_utc) > 0:
        existing = db.get_latest_daily_ticket("favorites")
        return {
            "favorites": {
                "generated": False,
                "reason": "already_generated_today",
                "ticket_id": existing["id"] if existing else None,
            }
        }

    return {
        "favorites": _generate_and_send(
            ticket_generation.generate_daily_ticket, "favorites",
            "Appka nenašla favority v pásmu 1,2-1,7, nebo s nimi nedosáhla kombinovaného kurzu 1,8.", user_id, send_telegram,
            exclude_players=exclude_set,
        ),
    }


@app.post("/admin/resend-latest-ticket")
def resend_latest_ticket(ticket_type: str = "favorites", _: None = Depends(require_admin_key)) -> dict:
    """appka pošle na Telegram UŽ vygenerovaný nejnovější tiket, bez dalšího
    generování — hodí se to, když appka tiket vygenerovala přes
    /admin/daily-tickets?send_telegram=false (např. jen na ověření) a
    chce ho pak reálně poslat, bez zbytečného duplicitního řádku navíc."""
    ticket = db.get_latest_daily_ticket(ticket_type)
    if ticket is None:
        raise HTTPException(404, "Appka žádný tiket tohohle typu nenašla.")
    send_ticket_to_telegram({**ticket, "ticket_id": ticket["id"], "ticket_type": ticket_type})
    return {"sent": True, "ticket_id": ticket["id"], "total_odds": float(ticket["total_odds"])}


class AlertRequest(BaseModel):
    message: str


@app.post("/admin/alert")
def admin_alert(body: AlertRequest, _: None = Depends(require_admin_key)) -> dict:
    """appka pošle admin alert appčinu Telegramu — appka to volá z
    `.github/workflows/daily-tickets.yml`, když appce ráno selže některý
    krok (sync/generování/settlement), ať se to appka dozví hned."""
    from ticket_telegram import send_admin_alert

    send_admin_alert(body.message)
    return {"sent": True}


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


@app.get("/admin/debug-ticket/{ticket_id}")
def debug_ticket(ticket_id: int, _: None = Depends(require_admin_key)) -> dict:
    """Dočasný diagnostický endpoint — appka ověřuje stav tiketu 20 před
    odesláním. Smaže se hned po ověření."""
    with db.get_cursor() as cur:
        cur.execute("SELECT id, status FROM tickets WHERE id = %s", (ticket_id,))
        ticket = dict(cur.fetchone())
        cur.execute(
            """
            SELECT tl.leg_result, pa.full_name AS player_a, pb.full_name AS player_b, tl.selection
            FROM ticket_legs tl
            JOIN matches m ON m.id = tl.match_id
            JOIN players pa ON pa.id = m.player_a_id
            JOIN players pb ON pb.id = m.player_b_id
            WHERE tl.ticket_id = %s
            ORDER BY tl.id
            """,
            (ticket_id,),
        )
        ticket["legs"] = [dict(r) for r in cur.fetchall()]
        return ticket


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
# Kupónové kódy — druhá cesta k předplatnému vedle placení (viz
# schema.sql, sekce 7, a db.redeem_coupon).
# ------------------------------------------------------------
class RedeemCouponRequest(BaseModel):
    code: str


class CreateCouponRequest(BaseModel):
    code: str
    days_granted: int
    max_uses: int = 1


@app.post("/coupons/redeem")
def redeem_coupon(body: RedeemCouponRequest, user_id: int = Depends(get_current_user_id)) -> dict:
    try:
        result = db.redeem_coupon(user_id, body.code)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"subscription_until": result["subscription_until"].isoformat()}


@app.post("/admin/coupons")
def create_coupon(body: CreateCouponRequest, _: None = Depends(require_admin_key)) -> dict:
    coupon = db.create_coupon(body.code, body.days_granted, body.max_uses)
    return {"id": coupon["id"], "code": coupon["code"], "days_granted": coupon["days_granted"], "max_uses": coupon["max_uses"]}


@app.post("/admin/market-threshold")
def set_market_threshold(market_code: str, min_confidence: float, _: None = Depends(require_admin_key)) -> dict:
    """appka umožní doladit market_thresholds.min_confidence bez psql
    přístupu k produkční DB — appka to potřebuje laďovat opakovaně
    podle reálných výsledků (viz ticket_builder.py)."""
    try:
        row = db.set_market_threshold_confidence(market_code, min_confidence)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"market_code": row["market_code"], "min_confidence": float(row["min_confidence"])}


# ------------------------------------------------------------
# Web s dnešním tiketem — appka to volá přímo z prohlížeče (viz
# netlify_site/tiket.html), zamčené skutečným přihlášením a aktivním
# předplatným (viz require_active_subscription výš).
# ------------------------------------------------------------
@app.get("/member/today-ticket")
def member_today_ticket(user_id: int = Depends(require_active_subscription)) -> dict:
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
    my_bet = db.get_bet_for_ticket(user_id, ticket["id"])
    return {
        "ready": True,
        "id": ticket["id"],
        "total_odds": float(ticket["total_odds"]),
        "created_at": ticket["created_at"].isoformat(),
        "legs": legs,
        "my_bet": {"odds": float(my_bet["odds"]), "stake": float(my_bet["stake"])} if my_bet else None,
    }


# ------------------------------------------------------------
# Admin — dnešní tiket bez gatingu na appčino vlastní předplatné (2026-08-27).
# Appka tohle přidala, aby si SESTERSKÁ appka (ApexSignal) mohla appčin
# denní tiket vytáhnout a nabídnout ho appce ke schválení na vlastním
# admin webu — viz /admin/daily-tickets?send_telegram=false výš (appka
# tam tiket JEN vygeneruje a uloží, appka ho sama Telegramu nepošle) a
# apexsignal-backend `/admin/external-tickets/ingest`. Stejná data jako
# /member/today-ticket, jen admin-key místo přihlášení.
# ------------------------------------------------------------
@app.get("/admin/today-ticket-detail")
def admin_today_ticket_detail(ticket_type: str = "favorites", _: None = Depends(require_admin_key)) -> dict:
    ticket = db.get_latest_daily_ticket(ticket_type)
    if ticket is None:
        return {"ready": False}

    from zoneinfo import ZoneInfo
    from ticket_telegram import selection_label

    legs = [
        {
            "match": f"{leg['player_a']} – {leg['player_b']}",
            "tourney_name": leg["tourney_name"],
            "selection": selection_label(leg["market_code"], leg["selection"], leg["line"], leg["player_a"], leg["player_b"]),
            "odds": float(leg["market_odds"]) if leg["market_odds"] is not None else None,
            "kickoff": leg["start_time"].astimezone(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d %H:%M") if leg.get("start_time") else None,
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


# ------------------------------------------------------------
# Veřejný carousel skutečných výher na appčině prodejní stránce — appka
# to nechce ručně přepisovat po každé výhře (viz courtedge_sales.html),
# proto sem appka tikety plní přímo z appčiny vlastní DB. Bez přihlášení.
# ------------------------------------------------------------
@app.get("/public/won-tickets")
def public_won_tickets() -> list[dict]:
    from ticket_telegram import selection_label

    tickets = db.get_recent_won_tickets("favorites", limit=5)
    result = []
    for t in tickets:
        legs = [
            {
                "match": f"{leg['player_a']} – {leg['player_b']}",
                "tourney_name": leg["tourney_name"],
                "winner": selection_label(leg["market_code"], leg["selection"], leg["line"], leg["player_a"], leg["player_b"]),
                "odds": float(leg["market_odds"]) if leg["market_odds"] is not None else None,
            }
            for leg in t["legs"]
        ]
        result.append({
            "id": t["id"],
            "date": t["created_at"].date().isoformat(),
            "total_odds": float(t["total_odds"]),
            "legs": legs,
        })
    return result


# ------------------------------------------------------------
# Uživatelovy vlastní vsazené tikety (viz db.save_bet/get_user_bets,
# schema.sql sekce 8) — appka drží kurz/částku, co uživatel doopravdy
# vsadil u svého bookmakera, ne appčin zobrazený kurz.
# ------------------------------------------------------------
class SaveBetRequest(BaseModel):
    ticket_id: int
    odds: float
    stake: float


@app.post("/member/save-bet")
def save_bet(body: SaveBetRequest, user_id: int = Depends(require_active_subscription)) -> dict:
    try:
        bet = db.save_bet(user_id, body.ticket_id, body.odds, body.stake)
    except ValueError as exc:
        status = 409 if "už máš uložený" in str(exc) else 404
        raise HTTPException(status, str(exc))
    return {"id": bet["id"], "ticket_id": bet["ticket_id"], "odds": float(bet["odds"]), "stake": float(bet["stake"])}


@app.get("/member/my-bets")
def my_bets(user_id: int = Depends(require_active_subscription)) -> list[dict]:
    from ticket_telegram import selection_label

    bets = db.get_user_bets(user_id)
    result = []
    for b in bets:
        odds, stake = float(b["odds"]), float(b["stake"])
        if b["ticket_status"] == "won":
            profit = round(stake * (odds - 1), 2)
        elif b["ticket_status"] == "lost":
            profit = -stake
        else:
            profit = None
        legs = [
            {
                "match": f"{leg['player_a']} – {leg['player_b']}",
                "tourney_name": leg["tourney_name"],
                "winner": selection_label(leg["market_code"], leg["selection"], leg["line"], leg["player_a"], leg["player_b"]),
                "result": leg["leg_result"],
            }
            for leg in b["legs"]
        ]
        result.append({
            "id": b["id"], "ticket_id": b["ticket_id"], "ticket_type": b["ticket_type"],
            "odds": odds, "stake": stake, "created_at": b["created_at"].isoformat(),
            "status": b["ticket_status"], "profit": profit, "legs": legs,
        })
    return result
