"""
db.py — přístup k PostgreSQL databázi (psycopg2, syrové SQL, žádné ORM
— stejný přístup jako u ApexSignalu, appka tohoto rozsahu ORM nepotřebuje).

Appka drží jedno globální connection pooling přes `psycopg2.pool`
(jednoduché, appka běží jako jedna instance na Renderu).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL není nastavená.")
        _pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=database_url)
    return _pool


@contextmanager
def get_cursor(commit: bool = False) -> Iterator[psycopg2.extras.RealDictCursor]:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ------------------------------------------------------------
# Uživatelé
# ------------------------------------------------------------
def create_user(email: str, password_hash: str) -> dict:
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING *",
            (email.strip().lower(), password_hash),
        )
        return dict(cur.fetchone())


def get_user_by_email(email: str) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE email = %s", (email.strip().lower(),))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def touch_last_login(user_id: int) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user_id,))


# ------------------------------------------------------------
# Kupónové kódy — druhá cesta k předplatnému vedle placení (viz
# schema.sql, sekce 7).
# ------------------------------------------------------------
def create_coupon(code: str, days_granted: int, max_uses: int = 1, expires_at: Optional[datetime] = None) -> dict:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO coupon_codes (code, days_granted, max_uses, expires_at)
            VALUES (%s, %s, %s, %s) RETURNING *
            """,
            (code.strip().upper(), days_granted, max_uses, expires_at),
        )
        return dict(cur.fetchone())


def redeem_coupon(user_id: int, code: str) -> dict:
    """Appka ověří a uplatní kód v jedné transakci: `uses_count` zvedne
    podmíněným UPDATE (atomicky, žádné dvě souběžné žádosti nevyčerpají
    limit dvakrát), zápisem do `coupon_redemptions` zabrání stejnému
    uživateli uplatnit tentýž kód podruhé (UNIQUE constraint), a
    nakonec posune `subscription_until` — od pozdějšího z `now()` nebo
    dosavadního konce předplatného, ne od dneška, ať prodloužení
    aktivní předplatné nezkrátí. Chybu appka hlásí jako `ValueError`
    se srozumitelným důvodem, ne jako syrovou DB výjimku."""
    normalized = code.strip().upper()
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE coupon_codes
            SET uses_count = uses_count + 1
            WHERE code = %s AND uses_count < max_uses
              AND (expires_at IS NULL OR expires_at > now())
            RETURNING *
            """,
            (normalized,),
        )
        coupon = cur.fetchone()
        if coupon is None:
            raise ValueError("Kód appka nenašla, nebo je už vyčerpaný/prošlý.")
        coupon = dict(coupon)

        try:
            cur.execute(
                "INSERT INTO coupon_redemptions (coupon_id, user_id) VALUES (%s, %s)",
                (coupon["id"], user_id),
            )
        except psycopg2.IntegrityError:
            raise ValueError("Tenhle kód jsi už jednou uplatnil/a.")

        cur.execute(
            """
            UPDATE users
            SET subscription_until = GREATEST(COALESCE(subscription_until, now()), now())
                                      + make_interval(days => %s),
                subscription_tier = 'active'
            WHERE id = %s
            RETURNING subscription_until
            """,
            (coupon["days_granted"], user_id),
        )
        return dict(cur.fetchone())


# ------------------------------------------------------------
# Hráči (Elo, ace rate, bezpečnostní čítače) — viz data_ingest.py
# ------------------------------------------------------------
def upsert_player(record) -> None:
    """`record` je data_ingest.PlayerRecord, appka ho ukládá po přepočtu historie."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO players (
                external_id, tour, full_name,
                elo_overall, elo_hard, elo_clay, elo_grass, elo_carpet,
                matches_played_total, matches_played_12mo,
                ace_rate_hard, ace_rate_clay, ace_rate_grass, ace_rate_carpet,
                recent_retirements_60d, last_match_date, updated_at
            ) VALUES (
                %(external_id)s, %(tour)s, %(full_name)s,
                %(elo_overall)s, %(elo_hard)s, %(elo_clay)s, %(elo_grass)s, %(elo_carpet)s,
                %(matches_played_total)s, %(matches_played_12mo)s,
                %(ace_rate_hard)s, %(ace_rate_clay)s, %(ace_rate_grass)s, %(ace_rate_carpet)s,
                %(recent_retirements_60d)s, %(last_match_date)s, now()
            )
            ON CONFLICT (external_id, tour) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                elo_overall = EXCLUDED.elo_overall,
                elo_hard = EXCLUDED.elo_hard,
                elo_clay = EXCLUDED.elo_clay,
                elo_grass = EXCLUDED.elo_grass,
                elo_carpet = EXCLUDED.elo_carpet,
                matches_played_total = EXCLUDED.matches_played_total,
                matches_played_12mo = EXCLUDED.matches_played_12mo,
                ace_rate_hard = EXCLUDED.ace_rate_hard,
                ace_rate_clay = EXCLUDED.ace_rate_clay,
                ace_rate_grass = EXCLUDED.ace_rate_grass,
                ace_rate_carpet = EXCLUDED.ace_rate_carpet,
                recent_retirements_60d = EXCLUDED.recent_retirements_60d,
                last_match_date = EXCLUDED.last_match_date,
                updated_at = now()
            """,
            {
                "external_id": record.external_id,
                "tour": record.tour,
                "full_name": record.full_name,
                "elo_overall": record.elo_overall,
                "elo_hard": record.elo_hard,
                "elo_clay": record.elo_clay,
                "elo_grass": record.elo_grass,
                "elo_carpet": record.elo_carpet,
                "matches_played_total": record.matches_played_total,
                "matches_played_12mo": record.matches_played_12mo,
                "ace_rate_hard": record.ace_rate_hard,
                "ace_rate_clay": record.ace_rate_clay,
                "ace_rate_grass": record.ace_rate_grass,
                "ace_rate_carpet": record.ace_rate_carpet,
                "recent_retirements_60d": record.recent_retirements_60d,
                "last_match_date": record.last_match_date,
            },
        )


def ensure_player_stub(external_id: str, tour: str, full_name: str) -> dict:
    """Appka zajistí, že hráč v DB existuje, i když pro něj ještě nemá
    spočítaný Elo/ace-rate (např. nový/qualifier hráč z nadcházejícího
    zápasu, co appka ještě v historickém importu neviděla) — appka mu
    dá výchozí rating 1500 (viz schema.sql defaults) a ten se doplní,
    až na něj appka příště spustí `data_ingest`/`api_tennis_ingest`."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO players (external_id, tour, full_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (external_id, tour) DO UPDATE SET full_name = EXCLUDED.full_name
            RETURNING *
            """,
            (external_id, tour, full_name),
        )
        return dict(cur.fetchone())


def get_player_by_external_id(external_id: str, tour: str) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM players WHERE external_id = %s AND tour = %s", (external_id, tour))
        row = cur.fetchone()
        return dict(row) if row else None


def find_player_by_name(full_name: str, tour: str) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM players WHERE tour = %s AND full_name ILIKE %s LIMIT 1",
            (tour, full_name.strip()),
        )
        row = cur.fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------
# Prahy trhů (market_thresholds)
# ------------------------------------------------------------
def get_market_thresholds() -> dict[str, dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM market_thresholds")
        return {row["market_code"]: dict(row) for row in cur.fetchall()}


# ------------------------------------------------------------
# Zápasy (nadcházející, s kurzy — appčin pracovní seznam pro generování)
# ------------------------------------------------------------
def upsert_upcoming_match(match: dict) -> dict:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO matches (
                external_id, tour, tourney_name, surface, tourney_level,
                round, best_of, start_time, status, player_a_id, player_b_id
            ) VALUES (
                %(external_id)s, %(tour)s, %(tourney_name)s, %(surface)s, %(tourney_level)s,
                %(round)s, %(best_of)s, %(start_time)s, 'scheduled', %(player_a_id)s, %(player_b_id)s
            )
            ON CONFLICT (external_id, tour) DO UPDATE SET
                start_time = EXCLUDED.start_time,
                status = CASE WHEN matches.status = 'scheduled' THEN EXCLUDED.status ELSE matches.status END
            RETURNING *
            """,
            match,
        )
        return dict(cur.fetchone())


def get_match_by_external_id(external_id: str, tour: str) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM matches WHERE external_id = %s AND tour = %s", (external_id, tour))
        row = cur.fetchone()
        return dict(row) if row else None


DAILY_TICKET_WINDOW_HOURS = 24
# Appka appku 2026-08-12 přidala, protože appčin denní tiket appce
# vytáhl zápasy na ZÍTRA místo na dnešek — appka totiž syncuje zápasy
# 3 dny dopředu (viz api_tennis_sync.py), a appka bez tohohle okna
# brala nejjistější picky ze VŠECH naplánovaných zápasů, ne jen z
# nejbližší appka doby. Appka radši omezí appčin výběr na následujících
# 24 hodin, ať appka "dnešní tiket" fakt znamená zápasy na dnešek.


def get_pending_matches(hours_ahead: int = DAILY_TICKET_WINDOW_HOURS) -> list[dict]:
    """Appka rovnou JOINuje jména a Elo/ace-rate obou hráčů — ticket_builder.py
    i ticket_telegram.py je potřebují a appka nechce N+1 dotazy navíc."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT m.*,
                   pa.external_id AS a_external_id,
                   pa.full_name AS player_a_name, pa.elo_overall AS a_elo_overall,
                   pa.elo_hard AS a_elo_hard, pa.elo_clay AS a_elo_clay, pa.elo_grass AS a_elo_grass, pa.elo_carpet AS a_elo_carpet,
                   pa.ace_rate_hard AS a_ace_rate_hard, pa.ace_rate_clay AS a_ace_rate_clay,
                   pa.ace_rate_grass AS a_ace_rate_grass, pa.ace_rate_carpet AS a_ace_rate_carpet,
                   pa.matches_played_12mo AS a_matches_played_12mo, pa.matches_played_total AS a_matches_played_total,
                   pa.recent_retirements_60d AS a_recent_retirements,
                   pb.external_id AS b_external_id,
                   pb.full_name AS player_b_name, pb.elo_overall AS b_elo_overall,
                   pb.elo_hard AS b_elo_hard, pb.elo_clay AS b_elo_clay, pb.elo_grass AS b_elo_grass, pb.elo_carpet AS b_elo_carpet,
                   pb.ace_rate_hard AS b_ace_rate_hard, pb.ace_rate_clay AS b_ace_rate_clay,
                   pb.ace_rate_grass AS b_ace_rate_grass, pb.ace_rate_carpet AS b_ace_rate_carpet,
                   pb.matches_played_12mo AS b_matches_played_12mo, pb.matches_played_total AS b_matches_played_total,
                   pb.recent_retirements_60d AS b_recent_retirements
            FROM matches m
            JOIN players pa ON pa.id = m.player_a_id
            JOIN players pb ON pb.id = m.player_b_id
            WHERE m.status = 'scheduled'
              AND m.start_time > now()
              AND m.start_time < now() + make_interval(hours => %s)
            ORDER BY m.start_time
            """,
            (hours_ahead,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_player_by_id(player_id: int) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM players WHERE id = %s", (player_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def record_match_result(match_id: int, winner_id: int, score: str, total_games: int, total_aces: int, retirement: bool) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE matches SET status = 'finished', winner_id = %s, score = %s,
                total_games = %s, total_aces = %s, retirement = %s
            WHERE id = %s
            """,
            (winner_id, score, total_games, total_aces, retirement, match_id),
        )


# ------------------------------------------------------------
# Kurzy
# ------------------------------------------------------------
def save_odds_snapshot(match_id: int, market_code: str, selection: str, line: Optional[float], bookmaker: str, odds_decimal: float) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO odds_snapshots (match_id, market_code, selection, line, bookmaker, odds_decimal)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (match_id, market_code, selection, line, bookmaker, odds_decimal),
        )


def get_latest_odds(match_id: int, market_code: str) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (selection, line) *
            FROM odds_snapshots
            WHERE match_id = %s AND market_code = %s
            ORDER BY selection, line, fetched_at DESC
            """,
            (match_id, market_code),
        )
        return [dict(r) for r in cur.fetchall()]


# ------------------------------------------------------------
# Tikety
# ------------------------------------------------------------
def save_ticket(user_id: Optional[int], total_odds: float, legs: list[dict], ticket_type: str = "games") -> dict:
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO tickets (user_id, total_odds, ticket_type) VALUES (%s, %s, %s) RETURNING *",
            (user_id, total_odds, ticket_type),
        )
        ticket = dict(cur.fetchone())
        for leg in legs:
            cur.execute(
                """
                INSERT INTO ticket_legs (ticket_id, match_id, market_code, selection, line, model_probability, market_odds)
                VALUES (%(ticket_id)s, %(match_id)s, %(market_code)s, %(selection)s, %(line)s, %(model_probability)s, %(market_odds)s)
                """,
                {**leg, "ticket_id": ticket["id"]},
            )
        return ticket


def get_pending_tickets() -> list[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM tickets WHERE status = 'pending'")
        return [dict(r) for r in cur.fetchall()]


def get_ticket_legs(ticket_id: int) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT tl.*, m.status AS match_status, m.winner_id, m.player_a_id, m.player_b_id,
                   m.total_games AS match_total_games, m.total_aces AS match_total_aces,
                   m.retirement AS match_retirement
            FROM ticket_legs tl
            JOIN matches m ON m.id = tl.match_id
            WHERE tl.ticket_id = %s
            """,
            (ticket_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def set_ticket_status(ticket_id: int, status: str) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE tickets SET status = %s, settled_at = now() WHERE id = %s", (status, ticket_id))


def set_leg_result(leg_id: int, result: str) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE ticket_legs SET leg_result = %s WHERE id = %s", (result, leg_id))


def get_users_tickets(user_id: int) -> list[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM tickets WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        return [dict(r) for r in cur.fetchall()]


def get_latest_daily_ticket(ticket_type: str = "favorites") -> Optional[dict]:
    """Appka vrátí appčin nejnovější VLASTNÍ (user_id IS NULL) tiket daného
    typu i s legy a jmény hráčů — appka to používá pro zamčenou webovou
    stránku s dnešním tiketem (viz backend_api.py, `/member/today-ticket`),
    ne pro appčin admin/telegram flow (ten legy dostává z paměti, ne z DB)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT * FROM tickets
            WHERE ticket_type = %s AND user_id IS NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (ticket_type,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        ticket = dict(row)
        cur.execute(
            """
            SELECT tl.market_code, tl.selection, tl.line, tl.market_odds,
                   pa.full_name AS player_a, pb.full_name AS player_b,
                   m.tourney_name, m.start_time
            FROM ticket_legs tl
            JOIN matches m ON m.id = tl.match_id
            JOIN players pa ON pa.id = m.player_a_id
            JOIN players pb ON pb.id = m.player_b_id
            WHERE tl.ticket_id = %s
            ORDER BY tl.id
            """,
            (ticket["id"],),
        )
        ticket["legs"] = [dict(r) for r in cur.fetchall()]
        return ticket
