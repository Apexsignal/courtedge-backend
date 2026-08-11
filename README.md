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
| `data_ingest.py` | Import historických zápasů (CSV, Sackmann/TML formát) → Elo/ace-rate přepočet |
| `odds_provider.py` | Klient pro the-odds-api.com (tenisové kurzy) |
| `odds_sync.py` | Spáruje kurzy z the-odds-api s appčinou `matches`/`players` tabulkou |
| `ticket_generation.py` | Spojuje Elo + market modely + ticket_builder + DB do jednoho "vygeneruj tiket" volání |
| `settlement.py` | Vyhodnocení tiketů po odehrání zápasů |
| `ticket_telegram.py` | Render tiketu jako JPG + odeslání do Telegramu |
| `backend_api.py` | FastAPI aplikace, routuje HTTP na moduly výše |
| `render.yaml` | Render.com Blueprint (web service + Postgres) |

## ⚠️ KRITICKÉ ZJIŠTĚNÍ — zdroj historických dat (NEVYŘEŠENO)

Předchozí session nechala otevřenou otázku, jestli `github.com/JeffSackmann/tennis_atp`
ještě existuje. **Tahle session to ověřila: NEEXISTUJE.** Repo (a stejně
tak `tennis_wta`) vrací 404 přes anonymní git proxy i přes jsdelivr CDN
zrcadlo — zatímco jiné repo stejného vlastníka (`tennis_MatchChartingProject`)
funguje normálně, takže nejde o omezení appčiny sítě, repo bylo
smazané/přejmenované/zprivátněné.

**Appka našla funkční náhradu se stejným sloupcovým formátem:**
`github.com/Tennismylife/TML-Database` — aktivně udržovaná, historie
1968 až dnes (appka ověřila živě klonováním, obsahuje i zápasy z
letošního srpna 2026), přesně stejné sloupce jako Sackmannův formát
(`w_ace`, `l_ace`, `w_SvGms`, `l_SvGms`, `surface`, `score`...) —
`data_ingest.py` je na tenhle formát navržený.

**ALE:** TML-Database je licencovaná **Creative Commons
Non-Commercial Share Alike** a jeho README výslovně říká:
> "Redistribution, commercial use, or selling of the raw database
> without permission from TennisMyLife and/or the ATP may violate
> copyright or terms of use... All data usage is non-commercial unless
> explicitly permitted."

CourtEdge je komerční produkt (placené předplatné přes Stripe) — appka
proto **NEBALÍ žádná historická data do tohohle repozitáře** a
`data_ingest.py` na TML-Database ani na cokoliv jiného pevně nenavazuje,
jen čte CSV ve známém formátu z lokální cesty (`HISTORICAL_DATA_SOURCE`).

**Tohle je rozhodnutí, které appka nemůže udělat sama — potřebuje ho
uživatel:**
1. Napsat TennisMyLife (kontakt přes GitHub repo appky výše, nebo
   `stats.tennismylife.org`) a zeptat se na komerční licenci/svolení.
2. Najít placený, komerčně licencovaný zdroj (Sportradar, RapidAPI
   tenisová API, SportsData.io, případně `api-tennis.com`, jehož klíč
   appka podle staršího kontextu má, ale je potřeba ověřit, jestli
   funguje a co licenčně dovoluje).
3. Použít Kaggle dataset "ATP Tennis 2000–2026 Daily update"
   (`kaggle.com/datasets/dissfya/atp-tennis-2000-2023daily-pull`) —
   appka jeho licenci NEOVĚŘILA, je potřeba zkontrolovat před použitím.

Dokud se tohle nevyřeší, appka nemá funkční Elo/ace-rate ratingy a
NEMĚLA by generovat tikety naostro.

## Chybějící zdroj pro settlement (vyhodnocení zápasů)

Appka zatím nemá automatizovaný zdroj VÝSLEDKŮ zápasů hned po skončení
(vítěz, celkový počet gemů, celkový počet es) — historické datasety se
aktualizují se zpožděním, the-odds-api dává jen kurzy před zápasem.
`settlement.py` proto zatím počítá jen s RUČNÍM zadáním výsledku přes
`POST /admin/match-result` — plnou automatizaci appka doplní, až se
najde vhodné API (kandidáti: stejný zdroj jako pro historii, pokud
nabízí i live/final skóre; nebo samostatné live-score API).

## Další otevřené věci

- **the-odds-api.com nemá bookmaker trh na esa** — appka na trh
  `total_aces` počítá jen vlastní pravděpodobnost, bez tržního kurzu.
  `ticket_builder.py` takový leg do tiketu nezahrne (potřebuje
  `market_odds`), dokud appka nenajde zdroj kurzu na esa (broker/API
  s "player props" trhy by mohl mít).
- **the-odds-api nedává povrch kurtu (surface)** — appka ho odhaduje
  z názvu turnaje (`odds_sync.py`, `TOURNEY_SURFACE_HINTS`), s
  fallbackem na `hard`. Pro přesnost by appka měla mít admin endpoint
  na ruční opravu povrchu u konkrétního zápasu (zatím chybí).
- **Prahy jistoty v `market_thresholds`** (0.62 / 0.58 / 0.58) jsou jen
  startovní odhad — appka je musí kalibrovat na backtestu, jakmile
  bude mít vyřešený zdroj historických dat.
- **`api-tennis.com`** — starší kontext appky uváděl klíč jako rozbitý
  ("Wrong login credentials"). Appka na něj v aktuální implementaci
  nespoléhá (viz `data_ingest.py`, čte lokální CSV, ne živé API), ale
  stálo by za to ověřit, jestli by nemohl posloužit jako komerčně
  čistší zdroj settlement dat.

## Manuální kroky (appka je udělat nemůže — potřebuje uživatele)

1. **Registrace domény `courtedge.cz`.**
2. **Nový Telegram bot přes @BotFather** — vlastní jméno, nesouvisející
   s ApexSignalem. Token → `TELEGRAM_BOT_TOKEN` env var.
3. **Nový, samostatný Stripe účet** — `STRIPE_SECRET_KEY`,
   `STRIPE_WEBHOOK_SECRET`.
4. **Nový Render web service + samostatná PostgreSQL DB** — `render.yaml`
   je připravený jako Blueprint, appka na Render dashboard nemá přístup
   (žádný Render API token v session).
5. **Vlastní the-odds-api.com klíč** — NEsdílet s ApexSignalem
   (`ODDSAPI_KEY`).
6. **Rozhodnutí o zdroji historických/settlement dat** — viz sekce výše,
   nejdůležitější blokující rozhodnutí před ostrým spuštěním.

## Lokální vývoj

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # vyplnit hodnoty
# založit lokální Postgres a spustit schema.sql
psql "$DATABASE_URL" -f schema.sql
uvicorn backend_api:app --reload
```

Import historických dat (po vyřešení licenční otázky výše):
```bash
curl -X POST http://localhost:8000/admin/ingest-historical-data \
  -H "X-Admin-Key: $ADMIN_TASK_KEY" -H "Content-Type: application/json" \
  -d '{"tour": "atp", "source_dir": "/cesta/k/csv/slozce"}'
```
