# CourtEdge — backend

Česká SaaS appka na tenisové sázkařské tipy (predikce zápasů, pre-match,
žádné live sázení). Samostatný produkt, plně oddělený od ApexSignalu na
provozní úrovni (vlastní Render, vlastní DB, vlastní Stripe účet,
vlastní Telegram bot) — kód je adaptovaný z `apexsignal-backend`
(auth, rate limiting, ticket rendering/Telegram, DB přístup), ale běží
ve vlastním repu bez sdílených závislostí.

## Produktová strategie

- **Přesně 3 trhy:** výherce zápasu (moneyline), over/under gemů,
  over/under es.
- **Tiket = 2 výběry** z RŮZNÝCH zápasů, kombinovaný kurz v pásmu
  **2,00–3,00**.
- **"Confidence ranking", ne value/edge betting.** Appka spočítá vlastní
  pravděpodobnost pro každého kandidáta (Elo, surface-vážený —
  `elo_model.py`; gemy/esa odvozené z Elo gapu a ace rate —
  `market_models.py`), seřadí je čistě podle vlastní jistoty (ne podle
  porovnání s tržním kurzem) a tržní kurz použije až na dopočet
  výsledného kurzu tiketu (`ticket_builder.py`). I tenhle přístup
  dlouhodobě potřebuje, aby byl model lepší než náhoda — jinak appka
  prohrává o marži bookmakera stejně jako klasický edge přístup.
- **Bezpečnostní filtry:** vyřazení hráčů s nedávnou historií skreče,
  minimální počet odehraných zápasů pro důvěryhodný rating, práh
  minimální jistoty na leg (kalibrovatelný v DB tabulce
  `market_thresholds`, ne natvrdo v kódu).

## Struktura repozitáře

| Soubor | Účel |
|---|---|
| `schema.sql` | DB schéma (PostgreSQL 14+) |
| `auth.py`, `rate_limiter.py` | Autentizace, brute-force ochrana (adaptováno z ApexSignalu téměř 1:1) |
| `db.py` | Přístup k DB (psycopg2, syrové SQL) |
| `elo_model.py` | Surface-vážený Elo rating engine |
| `market_models.py` | Modely pro over/under gemů a es |
| `ticket_builder.py` | Confidence-ranking výběr kandidátů + stavba 2-legového tiketu |
| `api_tennis_provider.py` | Klient pro api-tennis.com — **PRIMÁRNÍ zdroj dat** (historie, nadcházející zápasy, kurzy, settlement) |
| `api_tennis_ingest.py` | Převede api-tennis fixtures → Elo/ace-rate přepočet (sdílí engine s `data_ingest.py`) |
| `api_tennis_sync.py` | Sync nadcházejících zápasů + kurzů, automatický settlement dohraných zápasů |
| `data_ingest.py` | Import historických zápasů z CSV (Sackmann/TML formát) — záložní cesta, viz licenční poznámka níže |
| `odds_provider.py`, `odds_sync.py` | Klient pro the-odds-api.com — záložní/druhý zdroj kurzů, appka primárně používá api-tennis.com |
| `ticket_generation.py` | Spojuje Elo + market modely + ticket_builder + DB do jednoho "vygeneruj tiket" volání |
| `settlement.py` | Vyhodnocení tiketů po odehrání zápasů (výsledky appka doplňuje přes `api_tennis_sync.py`) |
| `ticket_telegram.py` | Render tiketu jako JPG + odeslání do Telegramu |
| `backend_api.py` | FastAPI aplikace, routuje HTTP na moduly výše |
| `render.yaml` | Render.com Blueprint (web service + Postgres) |

## Zdroj dat — VYŘEŠENO: api-tennis.com

Předchozí otevřená otázka byla, kde appka vezme historická data pro
Elo/ace-rate a jak bude appka vyhodnocovat zápasy po skončení
(settlement). Appka postupně zjistila:

1. **`github.com/JeffSackmann/tennis_atp` opravdu neexistuje** (ověřeno
   — 404 přes anonymní git proxy i jsdelivr CDN, zatímco jiné repo
   stejného vlastníka funguje normálně).
2. **Náhrada `Tennismylife/TML-Database`** má sice stejný formát, ale je
   licencovaná **Creative Commons Non-Commercial** — appka ji NEPOUŽILA
   (CourtEdge je placený produkt). `data_ingest.py` na CSV formát toho
   typu pořád umí, appka ho nechává jako záložní cestu PRO PŘÍPAD, že
   appka jednou získá komerční svolení nebo jiný licenčně čistý CSV
   zdroj — appka na něj ale nenavazuje natvrdo, jen čte z lokální cesty.
3. **`api-tennis.com` appka ověřila živě s aktuálním klíčem uživatele —
   funguje** (starší kontext appky uváděl klíč jako rozbitý, tenhle je
   nový/opravený). Appka zjistila, že tenhle zdroj řeší VŠECHNO
   najednou:
   - `get_fixtures` appce dá dohrané zápasy se statistikami (esa,
     servisní hry, finální skóre) → appka to používá pro Elo/ace-rate
     přepočet (`api_tennis_ingest.py`) I pro automatický settlement
     (`api_tennis_sync.settle_finished_matches`).
   - `get_odds` appce dá kurzy na výherce (Home/Away) i gemy (Over/Under
     by Games in Match) od ~12 bookmakerů najednou — appka bere
     nejlepší dostupnou cenu.
   - `get_tournaments` appce dá povrch kurtu (appka ho normalizuje,
     zdroj má pár nekonzistentních hodnot jako "Hard (Indoor)").
   - VŠECHNO pod stejným `event_key`/`player_key`/`tournament_key` — na
     rozdíl od dřívějšího plánu (the-odds-api + appka domýšlí párování
     podle jmen hráčů) appka nemusí nic párovat mezi dvěma zdroji.

   **Licenční poznámka:** api-tennis.com je PLACENÁ komerční API
   služba ($40–120/měsíc, appka ověřila že trvalý free tier neexistuje,
   jen 14denní trial) — ne akademický dataset. Terms of Use appka
   přečetla a jsou ke komerčnímu použití appky mlčenlivé (ani explicitní
   povolení, ani zákaz), ale celý byznys model (placené tiery pro
   "applications and individual developers") komerční nasazení
   předpokládá. Appka doporučuje při větším provozu vyžádat od
   api-tennis.com podpory písemné potvrzení, ale jako start appka na
   tomhle zdroji staví.

**Appka tohle živě otestovala** (viz `api_tennis_ingest.py`,
`api_tennis_sync.py`) — reálný import zápasů, přepočet Elo/ace-rate,
sync nadcházejících zápasů s kurzy (23 zápasů, 758 kurzových řádků
z jednoho volání) i automatický settlement dohraných zápasů fungují
proti živému API.

## Další otevřené věci

- **api-tennis.com nemá bookmaker trh na esa** (stejně jako the-odds-api)
  — appka na trh `total_aces` počítá jen vlastní pravděpodobnost, bez
  tržního kurzu. `ticket_builder.py` takový leg do tiketu nezahrne
  (potřebuje `market_odds`), dokud appka nenajde zdroj kurzu na esa.
- **`tourney_level` appka z api-tennis.com nedostává** (Grand
  Slam/Masters/Tour rozlišení pro K-faktor Elo) — appka zatím jede na
  defaultní hodnotě K-faktoru pro všechny turnaje. Vylepšit appka může
  přes `tournament_round`/`tournament_name` heuristiku, zatím appka to
  neřešila.
- **Prahy jistoty v `market_thresholds`** (0.62 / 0.58 / 0.58) jsou jen
  startovní odhad — appka je musí kalibrovat na backtestu s reálnými
  daty (teď už appka na to má zdroj).
- **`event_date`/`event_time` appka bere jako appka je dostane, bez
  ověřené časové zóny** — appka je zatím ukládá jako appka je dostala
  (nejspíš UTC nebo lokální čas turnaje, appka to needeklarovala od
  api-tennis.com podpory). Před ostrým nasazením appka doporučuje ověřit,
  ať appka neposílá tikety s posunutým časem začátku.
- **api-tennis.com placený plán** — appka neví, jaký plán klíč uživatele
  pokrývá (Starter/Premium/Business/Ultra, $40–120/měsíc) ani jaký má
  denní request limit. Appka doporučuje ověřit v api-tennis.com
  dashboardu před rozjetím pravidelného cronu (historický bulk import
  napříč lety appka dělá po měsíčních oknech, viz `MONTH_CHUNK_DAYS`
  v `api_tennis_ingest.py`, ale u nízkého tieru by appka mohla narazit
  na denní strop).

## Manuální kroky (appka je udělat nemůže — potřebuje uživatele)

1. **Registrace domény `courtedge.cz`.**
2. **Nový Telegram bot přes @BotFather** — vlastní jméno, nesouvisející
   s ApexSignalem. Token → `TELEGRAM_BOT_TOKEN` env var.
3. **Nový, samostatný Stripe účet** — `STRIPE_SECRET_KEY`,
   `STRIPE_WEBHOOK_SECRET`.
4. **Nový Render web service + samostatná PostgreSQL DB** — `render.yaml`
   je připravený jako Blueprint, appka na Render dashboard nemá přístup
   (žádný Render API token v session).
5. **`APITENNIS_KEY` do Render env vars** — appka klíč, co jí uživatel
   dal v chatu, NIKDY neuložila do repozitáře (viz `.gitignore`,
   appka ho použila jen lokálně přes env var). Je potřeba ho vložit
   ručně do Render dashboardu.
6. *(Volitelné)* Vlastní the-odds-api.com klíč pro `ODDSAPI_KEY`, pokud
   appka má jednou používat i záložní zdroj kurzů.

## Lokální vývoj

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # vyplnit hodnoty (APITENNIS_KEY, DATABASE_URL, SECRET_KEY, ADMIN_TASK_KEY...)
# založit lokální Postgres a spustit schema.sql
psql "$DATABASE_URL" -f schema.sql
uvicorn backend_api:app --reload
```

Naplnění appky reálnými daty (v tomhle pořadí):
```bash
# 1. historický přepočet Elo/ace-rate (appka doporučuje po ročních oknech,
#    ať appka nenarazí na limit api-tennis.com plánu)
curl -X POST http://localhost:8000/admin/ingest-api-tennis \
  -H "X-Admin-Key: $ADMIN_TASK_KEY" -H "Content-Type: application/json" \
  -d '{"tour": "atp", "date_start": "2023-01-01", "date_stop": "2026-08-11"}'

# 2. sync nadcházejících zápasů + kurzů (appka spouští pravidelně, např. denně)
curl -X POST "http://localhost:8000/admin/sync-api-tennis?tour=atp&days_ahead=7" \
  -H "X-Admin-Key: $ADMIN_TASK_KEY"

# 3. vygenerování denního tiketu
curl -X POST http://localhost:8000/admin/daily-tickets \
  -H "X-Admin-Key: $ADMIN_TASK_KEY"

# 4. settlement (appka spouští pravidelně, doplní výsledky přes api-tennis.com a vyhodnotí pending tikety)
curl -X POST http://localhost:8000/admin/settle-all-pending \
  -H "X-Admin-Key: $ADMIN_TASK_KEY"
```
