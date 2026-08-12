-- ============================================================
-- CourtEdge — databázové schéma
-- Cílová platforma: PostgreSQL 14+
--
-- Rozsah appky je záměrně úzký: JEN tenis (ATP + WTA), JEN 3 trhy
-- (výherce zápasu, over/under gemů, over/under es), tikety o 2 výběrech.
-- Žádné live/in-match statistiky (na rozdíl od ApexSignalu) — appka
-- pracuje čistě s pre-match daty.
-- ============================================================

CREATE TYPE tour_type            AS ENUM ('atp', 'wta');
CREATE TYPE surface_type         AS ENUM ('hard', 'clay', 'grass', 'carpet');
CREATE TYPE match_status_type    AS ENUM ('scheduled', 'finished', 'walkover', 'cancelled');
CREATE TYPE ticket_status_type   AS ENUM ('pending', 'won', 'lost', 'void');
CREATE TYPE market_code_type     AS ENUM ('match_winner', 'total_games', 'total_aces');

-- ------------------------------------------------------------
-- 1. UŽIVATELÉ
-- ------------------------------------------------------------
CREATE TABLE users (
    id                  BIGSERIAL PRIMARY KEY,
    email               VARCHAR(255) UNIQUE NOT NULL,
    password_hash       VARCHAR(255) NOT NULL,
    subscription_tier   VARCHAR(20) NOT NULL DEFAULT 'free',
    subscription_until  TIMESTAMPTZ,
    telegram_chat_id    VARCHAR(64),               -- appka sem uloží chat_id, jakmile si ho uživatel v botovi spáruje
    stripe_customer_id  VARCHAR(64),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at       TIMESTAMPTZ
);

-- ------------------------------------------------------------
-- 2. HRÁČI — jádro pro Elo rating a market modely (gemy/esa)
-- ------------------------------------------------------------
CREATE TABLE players (
    id                      BIGSERIAL PRIMARY KEY,
    external_id             VARCHAR(50),            -- ID ze zdrojového datasetu (Sackmann/TML player id)
    tour                    tour_type NOT NULL,
    full_name               VARCHAR(150) NOT NULL,
    hand                    CHAR(1),                -- 'R' | 'L'
    country                 VARCHAR(5),
    birth_date              DATE,

    -- Elo rating — celkový + surface-specific (viz elo_model.py).
    -- Appka drží JEN aktuální hodnotu, ne historii — pro re-výpočet appka
    -- vždy přepočítá celý dataset od nuly (viz data_ingest.py), historie
    -- ratingů appce k ničemu není.
    elo_overall             NUMERIC(7,2) NOT NULL DEFAULT 1500,
    elo_hard                NUMERIC(7,2) NOT NULL DEFAULT 1500,
    elo_clay                NUMERIC(7,2) NOT NULL DEFAULT 1500,
    elo_grass               NUMERIC(7,2) NOT NULL DEFAULT 1500,
    elo_carpet               NUMERIC(7,2) NOT NULL DEFAULT 1500,

    matches_played_total    INTEGER NOT NULL DEFAULT 0,
    matches_played_12mo     INTEGER NOT NULL DEFAULT 0,   -- appka na tohle váže bezpečnostní filtr min. odehraných zápasů

    -- Ace rate na servisní hru, per surface — vstup pro market_models.py (trh es).
    ace_rate_hard            NUMERIC(6,4),           -- es / servisní hra
    ace_rate_clay            NUMERIC(6,4),
    ace_rate_grass           NUMERIC(6,4),
    ace_rate_carpet          NUMERIC(6,4),

    recent_retirements_12mo INTEGER NOT NULL DEFAULT 0,   -- bezpečnostní filtr — vyřazuje hráče s nedávnou historií skreče
    last_match_date          DATE,

    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (external_id, tour)
);
CREATE INDEX idx_players_tour_name ON players (tour, full_name);

-- ------------------------------------------------------------
-- 3. ZÁPASY
-- ------------------------------------------------------------
CREATE TABLE matches (
    id                  BIGSERIAL PRIMARY KEY,
    external_id         VARCHAR(100),               -- ID z odds API (the-odds-api event id) NEBO historického datasetu
    tour                tour_type NOT NULL,
    tourney_name        VARCHAR(150),
    surface             surface_type,
    tourney_level        VARCHAR(10),                -- G (Grand Slam) | M (Masters) | A (ATP/WTA Tour) | D (Davis/Fed Cup)
    round                VARCHAR(100),               -- R128, R64, ..., F -- api-tennis.com dává popisný text (např. "ATP Montreal - Quarter-finals"), ne krátký kód jako Sackmann/TML formát, appka to zjistila živě při prvním reálném syncu
    best_of              SMALLINT DEFAULT 3,
    start_time            TIMESTAMPTZ NOT NULL,
    status                match_status_type NOT NULL DEFAULT 'scheduled',

    player_a_id           BIGINT REFERENCES players(id),
    player_b_id           BIGINT REFERENCES players(id),

    -- Výsledek — appka je plní po zápase, ze settlement zdroje (viz README,
    -- otevřená otázka: zdroj pro settlement gemů/es po zápase).
    winner_id              BIGINT REFERENCES players(id),
    score                  VARCHAR(50),
    total_games             SMALLINT,                -- součet odehraných gemů obou hráčů (pro settlement trhu total_games)
    total_aces               SMALLINT,                -- součet es obou hráčů (pro settlement trhu total_aces)
    retirement               BOOLEAN NOT NULL DEFAULT false,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (external_id, tour)
);
CREATE INDEX idx_matches_status_start ON matches (status, start_time);

-- ------------------------------------------------------------
-- 4. TRŽNÍ KURZY — snapshoty z the-odds-api, appka je používá jen pro
--    dopočet výsledného kurzu tiketu, NE pro filtrování kandidátů
--    (viz "confidence ranking" princip v ticket_builder.py).
-- ------------------------------------------------------------
CREATE TABLE odds_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    match_id        BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    market_code     market_code_type NOT NULL,
    selection       VARCHAR(50) NOT NULL,           -- 'player_a' | 'player_b' | 'over' | 'under'
    line             NUMERIC(5,1),                   -- hranice pro total_games / total_aces (např. 22.5); NULL pro match_winner
    bookmaker        VARCHAR(50),
    odds_decimal      NUMERIC(6,3) NOT NULL,
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_odds_match_market ON odds_snapshots (match_id, market_code, fetched_at DESC);

-- ------------------------------------------------------------
-- 5. TIKETY — appka staví tikety o 1 až 4 výběrech (viz ticket_builder.py,
--    appka dřív měla pevné pásmo kurzu 2,00-3,00, appka to zrušila —
--    jistota kandidátů appce vyjde přednější než konkrétní kurz).
--
--    Appka 2026-08-12 začala posílat DVA tikety denně (viz ticket_type)
--    — appka zjistila živě, že appčin trh gemů má u skutečného
--    bookmakera jinou hranici, než appka appce ukazuje (jiná sázka, ne
--    jen jiná cena), zatímco trh výherce zápasu žádnou hranici nemá.
--    Appka proto appčin denní tiket přesunula čistě na favority
--    (match_winner, kurz 1,3-2,0 na leg, kombinovaný kurz 1,8+) —
--    appka posílá DVA takové tikety z různých zápasů, ne jeden gemový
--    a jeden na výherce (viz README, "Favorité místo gemů").
-- ------------------------------------------------------------
CREATE TABLE tickets (
    id                  BIGSERIAL PRIMARY KEY,
    user_id              BIGINT REFERENCES users(id),   -- NULL = appčin vlastní denní tiket (broadcast)
    ticket_type            VARCHAR(20) NOT NULL DEFAULT 'favorites' CHECK (ticket_type IN ('games', 'winner', 'favorites')),
    total_odds            NUMERIC(6,3) NOT NULL CHECK (total_odds > 1.0),
    status                ticket_status_type NOT NULL DEFAULT 'pending',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at              TIMESTAMPTZ
);

CREATE TABLE ticket_legs (
    id                  BIGSERIAL PRIMARY KEY,
    ticket_id            BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    match_id              BIGINT NOT NULL REFERENCES matches(id),
    market_code            market_code_type NOT NULL,
    selection               VARCHAR(50) NOT NULL,
    line                     NUMERIC(5,1),
    model_probability         NUMERIC(5,4) NOT NULL,      -- appčin vlastní odhad (confidence), zdroj řazení
    market_odds               NUMERIC(6,3),                -- kurz použitý pro dopočet kombinovaného kurzu tiketu
    leg_result                VARCHAR(10),                  -- 'won' | 'lost' | 'void' po vyhodnocení
    UNIQUE (ticket_id, match_id, market_code)
);
CREATE INDEX idx_ticket_legs_ticket ON ticket_legs (ticket_id);

-- ------------------------------------------------------------
-- 6. KONFIGURACE PRAHŮ — appka drží prahy jistoty v DB, ne natvrdo
--    v kódu, ať se dají kalibrovat bez nasazení (viz README, hodnoty
--    je potřeba ještě ověřit na reálných datech).
-- ------------------------------------------------------------
CREATE TABLE market_thresholds (
    market_code             market_code_type PRIMARY KEY,
    min_confidence            NUMERIC(5,4) NOT NULL,      -- práh minimální model_probability na leg
    min_matches_played_12mo    INTEGER NOT NULL DEFAULT 10  -- obdoba MIN_GAMES_PLAYED_FOR_FORM_SENSITIVE_MARKETS
);

INSERT INTO market_thresholds (market_code, min_confidence, min_matches_played_12mo) VALUES
    ('match_winner', 0.62, 10),
    ('total_games',  0.58, 10),
    ('total_aces',   0.58, 10);
-- Appka 2026-08-11 spustila walk-forward backtest (scripts/backtest_calibration.py,
-- 6184 zápasů ATP+WTA) a přeladila appčiny KONSTANTY v market_models.py
-- (appčiny modely byly předtím výrazně přehnaně sebevědomé — viz docstring
-- tam) — appčiny pravděpodobnosti appka teď dávají mnohem realističtější
-- čísla, takže appka tenhle práh 0,58 klidně splní. Appka ale samotné
-- PRAHY (kde appka řekne "beru/neberu") appka ještě nezkoušela optimalizovat
-- na přesnost/výtěžnost tiketů — appka to udělá, až appka bude mít
-- historii appčiných VLASTNÍCH tiketů, ne jen appčiných dílčích predikcí.
