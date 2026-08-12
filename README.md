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
| `.github/workflows/daily-tickets.yml` | Denní automatizace (sync → tiket → settlement), viz níže |

## Produktový model: appka negeneruje na vyžádání

Na rozdíl od ApexSignalu appka NEMÁ žádné klientské tlačítko "vygeneruj
tiket" (tokeny, neomezený tarif) — CourtEdge je čistě pasivní odběr:
appka jednou denně sama, na appčino pozadí, vygeneruje DVA tikety
(gemový a na výherce zápasu, viz "Dva tikety denně" níže) a rozešle
je broadcastem do appčina Telegram kanálu. Zákazník appku nijak
neovládá, jen ji odebírá.

## Denní automatizace

`.github/workflows/daily-tickets.yml` appka spouští PLNĚ automatizovaně
(žádné manuální schválení mezi kroky, stejný princip appka zvolila u
ApexSignalu), denně v 06:00 UTC:
1. sync ATP zápasů/kurzů, 2. sync WTA zápasů/kurzů, 3. vygenerování +
odeslání denního tiketu na Telegram, 4. vyhodnocení dohraných tiketů
(`if: always()`, aby appka settlement nepřeskočila, i kdyby dnešní
tiket appka nevygenerovala).

Appka potřebuje tyhle GitHub Actions repo secrets (Settings → Secrets
and variables → Actions), appka je NEUKLÁDÁ do kódu:
- `API_BASE_URL` — appčina URL na Renderu (např. `https://courtedge-backend.onrender.com`)
- `ADMIN_TASK_KEY` — STEJNÁ hodnota jako appčin env var na Renderu

Appka workflow nechává zapnutý i BEZ nasazeného backendu — do doby, než
appka dostane `API_BASE_URL`/`ADMIN_TASK_KEY`, poběží denně a bude
appce hlásit selhání (červený běh v GitHub Actions). To appka bere jako
záměr (appka na to nechce přidávat žádnou zapínací pojistku navíc) —
jakmile appka backend nasadí a secrets doplní, běhy appce začnou
procházet bez dalšího zásahu.

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

**Appka tohle živě otestovala proti reálnému API** (viz `api_tennis_ingest.py`,
`api_tennis_sync.py`) — reálný import zápasů, přepočet Elo/ace-rate,
sync nadcházejících zápasů s kurzy (23 zápasů, 758 kurzových řádků
z jednoho volání) i automatický settlement dohraných zápasů fungují
proti živému API.

**Appka navíc otestovala CELÝ řetězec naostro, end-to-end** — appka si
lokálně postavila skutečnou PostgreSQL, nahrála oba historické snapshoty
(`scripts/load_snapshot_into_db.py`, bez jediného API volání), rozjela
appku (`uvicorn backend_api:app`) a přes reálné HTTP endpointy postupně
zavolala `/admin/sync-api-tennis` (ATP i WTA, živá data z Cincinnati),
`/admin/daily-tickets` (appka reálně vygenerovala a uložila tiket, kurz
2,03 — appka správně vzala 2 zápasy s velkým Elo gapem a vsadila na
"pod" gemy) a `/admin/settle-all-pending`. Appka navíc ověřila i
vyrenderování obrázku tiketu (`ticket_telegram.render_ticket`) na
reálných datech. Jediné dva kroky appka nemohla ověřit živě, protože
appka na ně nemá přístup: skutečné odeslání na Telegram (chybí
`TELEGRAM_BOT_TOKEN`) a platba přes Stripe.

Appka při tomhle testu narazila a opravila dvě skutečné chyby, co by se
jinak projevily až na produkci:
- `matches.round` byl `VARCHAR(10)`, ale api-tennis.com appce dává
  popisný text jako `"ATP Montreal - Quarter-finals"`, ne krátký kód
  jako Sackmann/TML formát — appka sloupec rozšířila na `VARCHAR(100)`.
- `market_models.estimate_total_aces` počítal s `float`, ale psycopg2
  appce vrací `NUMERIC` sloupce (ace rate) jako `decimal.Decimal` —
  appka to sjednotila na hranici DB/výpočtu v `ticket_generation.py`.

## Kalibrace modelů (2026-08-11)

U reálného vygenerovaného tiketu appka ukázala 98% jistotu na trh
"gemy" u zápasu, kde appčin model výherce dal na TEN SAMÝ zápas jen
71 % — velký rozpor u stejného zápasu byl signál, že appčiny konstanty
v `market_models.py` (nikdy nekalibrované, jen hrubý odhad) jsou
pravděpodobně mimo realitu. Appka to ověřila **walk-forward backtestem**
(`scripts/backtest_calibration.py`) — appka prošla 6184 skutečných
zápasů (ATP+WTA, 2025-08 až 2026-08) CHRONOLOGICKY a u každého počítala
predikci JEN z toho, co appka věděla PŘED zápasem (žádné nakukování
dopředu), teprve pak appka zápas použila na update ratingu.

**Výsledek appku potvrdil:**
- **Výherce (Elo)** appka nemusela měnit — Brier score 0,227, appka je
  v hlavním pásmu 40–70 % rozumně kalibrovaná.
- **Gemy appka byla skoro dvakrát přehnaně sebevědomá** — appka měla
  natvrdo rozptyl 4,2 gemu, realita je 6,8–9,0 podle povrchu. Appka
  taky měla citlivost na Elo gap čtyřikrát vyšší, než appka doopravdy
  je.
- **Esa appka byla ještě víc mimo** — appka počítala s Poissonovým
  rozdělením (rozptyl appka rovná průměru), skutečný rozptyl je 6,7×
  vyšší.

Appka `market_models.py` přeladila na naměřené hodnoty (viz docstring
modulu pro přesná čísla) a rovnou to ověřila zpětně na stejných
backtestových datech: systematická odchylka u gemů klesla z +2,1 na
+0,5 gemu, u es téměř na nulu (+0,004), a pokrytí (kolik skutečných
výsledků padne do appčina predikovaného intervalu nejistoty) teď sedí
blízko teoretickému očekávání normálního rozdělení (~75 % v rámci
±1 směrodatné odchylky, dřív appka reálně netušila, jak moc mimo je).
Na tom samém reálném tiketu z minula (vsadil appka "pod gemy" u dvou
zápasů s velkým rozdílem v síle) appka teď dostane 83 % a 69 % jistoty
— pořád vysoká, ale mnohem realističtější než původních 98 % a 89 %.

Appka cestou taky opravila datovou chybu — `api_tennis_ingest.py`
appka VŠECHNY zápasy značila jako `best_of=3`, i Grand Slamy (které
se hrají na bo5), což zkreslilo hlavně povrch tráva (Wimbledon je
jediný velký grass turnaj a appka ho počítala jako kratší bo3 zápas,
i když měl reálně víc gemů kvůli bo5 formátu).

Appka **nepřepočítala tenhle konkrétní backtest po opravě** best_of
detekce — stálo by to appku další hodiny stahování z api-tennis.com.
Číslo pro povrch TRÁVA v `BASELINE_GAMES` je proto appka odhad
konzervativnější, než appka syrová naměřená data — až appka příště
poběží backtest, měla by ho přepočítat na čistá data.

### Vyčištění žebříčku a nejistota ratingu (2026-08-11, druhé kolo)

Appka narazila živě na dvě další zkreslení, den po prvním kole
kalibrace:

1. **Jeden zápas vyplnil žebříček desítkami "kandidátů".** Appka pro
   trh gemy nabízela kandidáta na KAŽDOU dostupnou hranici kurzu — u
   jednoho hodně jednostranného zápasu appka takhle dostala 8+ variant
   stejného signálu (různé hranice), což appku uvedlo v omyl, že má
   spoustu jistých tipů. Appka to vyřešila novou funkcí
   `ticket_builder.select_candidates()` — appka nechá jen nejjistějšího
   kandidáta z každého zápasu a vyřadí kurzy pod 1,15 (tam appka nemá
   žádnou výhodu, bookmaker to vidí skoro stejně jistě).
2. **Appka věřila hráčům z kvalifikací stejně jako appka ostříleným
   hráčům**, pokud měli podobný rating — i když má appka o hráči s 15
   zápasy mnohem míň dat než o hráči s 300 zápasy. Appka přidala
   `elo_model.rating_confidence()`/`combined_confidence()`
   (Glicko-inspirované): appka odhad appky posouvá blíž k 50 % (u
   výherce) nebo appka nafoukne rozptyl (u appky gemů/es), pokud
   appka o některém hráči nemá dost dat. Appka ověřila živě — hráč s
   20 zápasy appce klesl z 69 % na 59,8 % jistoty, hráč s 45 zápasy z
   89 % na 81 %.

### Tiket bez pevného pásma kurzu (2026-08-11, třetí kolo)

Appka dřív stavěla tiket vždy o 2 výběrech, s kombinovaným kurzem
2,00–3,00. Reálný test proti Tipsportu ukázal problém: appčiny
nejjistější tipy měly kurz blízko 1,00–1,20 (bookmaker appce dával
skoro stejnou jistotu). Appka aby se vešla do pásma 2,00–3,00, musela
místo nejjistějších tipů brát méně jisté, jen aby appka dostala
vyšší kurz.

Appka pásmo zrušila. Nová pravidla v `ticket_builder.py`:

- Appka bere 1 až 3 nejjistější kandidáty (`MIN_TICKET_LEGS` = 1,
  `MAX_TICKET_LEGS` = 3), každý z jiného zápasu.
- Appka nekontroluje výsledný kombinovaný kurz. Jistota appce jde
  před kurzem.
- Appka pořád vyřazuje kurzy pod 1,15 (viz `select_candidates()`
  výše) — tam appka nemá žádnou výhodu.

Appka zvedla i DB constraint v `schema.sql` (`tickets.total_odds`
appka jen kontroluje `> 1.0`, ne pásmo) a Telegram render appka umí
sklonit počet výběrů v češtině (1 výběr / 2–4 výběry / 5+ výběrů).

### H2H a signál únavy/odpočinku (2026-08-11, čtvrté kolo)

Appka dosud počítala jen s Elo ratingem — neznala vzájemnou historii
dvou konkrétních hráčů ani to, jestli je někdo z nich unavený nebo
naopak po dlouhé pauze. Appka to teď doplnila přes nový modul
`head_to_head.py`, který volá api-tennis.com metodu `get_H2H`
(appka ji předtím nepoužívala) — appka jedním voláním na zápas dostane
vzájemnou historii OBOU hráčů i poslední odehrané zápasy KAŽDÉHO z
nich zvlášť.

Appka z toho počítá tři různé úpravy:

1. **H2H poměr výher** — appka appčin Elo odhad posune blíž k tomu, jak
   spolu tihle dva hráči hráli dřív. Váha roste s počtem vzájemných
   zápasů, ale appka ji shora omezuje na 35 % (`H2H_MAX_WEIGHT`) — pár
   zápasů appce na jistotu nestačí.
2. **Únava** — appka spočítá, kolik zápasů každý hráč odehrál za
   posledních 7 dní, a rozdíl appka promítne jako Elo penalizaci
   unavenějšího hráče (`FATIGUE_ELO_PER_EXTRA_MATCH`).
3. **Dlouhá pauza** (45+ dní bez zápasu) — appka tady NEVÍ, jestli je
   hráč lepší nebo horší než appčin rating říká, takže appka jen
   sníží důvěru (posun blíž k 50 % u výherce, širší rozptyl u
   gemů/es) — stejný princip jako appčina nejistota ratingu výše.

Appka volání dělá ŽIVĚ při generování tiketu, obalené v try/except
(`ticket_generation._fetch_form_safe`) — když api-tennis.com
nedostupnost/limit appce zrovna ten zápas neumožní, appka tiket
postaví i tak, jen bez H2H/únava úpravy pro daný zápas.

Appka konstanty (`FATIGUE_ELO_PER_EXTRA_MATCH`, `LONG_LAYOFF_DAYS`,
`LAYOFF_CONFIDENCE_MULTIPLIER`) jsou zatím hrubý odhad — appka je
zatím neměla na čem zpětně otestovat (backtest_calibration.py appka
zatím nemá zdroj H2H/rozpisu zápasů v historickém CSV formátu).

**Update 2026-08-12:** appka dostala platný `APITENNIS_KEY` a ověřila
`get_H2H` živě — appka vrací skutečná data přesně v appka zdokumentovaném
tvaru (např. appka dohledala reálnou dvojici s H2H historií 3-4 ze 7
vzájemných zápasů). Appka logiku i živé volání teď považuje za ověřené.

### Analýza prvních reálných tiketů (2026-08-12)

Appka poslala uživateli 6 tiketů v průběhu 11.–12. 8. Uživatel appce
nahlásil: vyhrál jen první tiket, ten appka poslala PŘED kalibrací
(19:33). Appka si sehnala `APITENNIS_KEY` a dohledala skutečné
výsledky přes api-tennis.com. Tabulka appčiných reálných predikcí
proti realitě (všech 6 zápasů bylo hard/bo3):

| Zápas | Appka tipla | Jistota appky | Skutečný počet gemů | Trefeno |
|---|---|---|---|---|
| Damm – Sakellaridis | pod 25,5 | 69–89 % | 22 | ano |
| Merida Aguilar – Tien | pod 28,5/29,5/30,5 | 81–98 % | 17 | ano |
| Korneeva – Lepchenko | nad 18,0 | 72 % | 16 | ne |
| Shimabukuro – Dellien | nad 17,0/17,5 | 73–76 % | 16 | ne |
| Salkova – Blinkova | nad 19,0 | 69 % | 19 | ne (přesně na hraně) |
| Trungelliti – Ofner | pod 25,5 | 65 % | 32 | ne, velký omyl |

Appka z toho zjistila dvě různé věci a opravila appku obě:

**1. Appka kombinovala moc legů.** Appka bere 1 až 3 nejjistější picky
a násobí jejich kurz, ale appka MUSÍ trefit VŠECHNY legy, aby tiket
vyhrál — appčina kombinovaná jistota (součin jistoty legů) appce u
appčiných 6 tiketů klesla z 87 % (tiket #1, přehnaně sebevědomý starý
model) až na 40 % (tiket #6, 3 legy po kalibraci). Appka přidala do
`ticket_builder.build_ticket()` `MIN_COMBINED_PROBABILITY = 0.45` —
appka přestane přidávat další leg, jakmile by appku kombinovaná
jistota stáhl pod tuhle hranici. První leg appka vezme vždycky, i
kdyby byl sám pod ní.

**2. Appčin baseline pro gemy byl moc vysoko.** appka "nad" tipy
netrefila 0 ze 4, "pod" tipy trefila 5 z 6 (jediný omyl byl
Trungelliti–Ofner, plný třísetový zápas na 32 gemů). Appka snížila
`BASELINE_GAMES[("hard", 3)]` v `market_models.py` z 22,9 na 21,5.

Appka POCTIVĚ přiznává: 6 zápasů je proti appčinu backtestu na 6184
zápasech statisticky zanedbatelný vzorek — čistě podle váhy dat by se
baseline měl posunout o zlomek bodu, ne o 1,4 gemu. Appka tenhle
větší posun udělala na výslovné přání uživatele, ne z appčina úsudku
o síle důkazu. Appka doporučuje přepočítat znovu, až appka bude mít
desítky reálných tiketů, ne jednotky.

### Kurz vs. riziko (2026-08-12)

Uživatel appce řekl, že kurz 1,60 je malý — chtěl kurz 1,9 až 3,0.
Appka to ověřila na dnešních skutečných datech. Appčiny nejjistější
picky dneska měly jistotu 84–88 %. Kurz na ně byl ale jen 1,16–1,20
— bookmaker je viděl stejně jistě jako appka.

Aby appka dosáhla kombinovaného kurzu 1,9–3,0, musela by dát do
tiketu 5 legů. Kombinovaná jistota by klesla na ~48 % — přesně tam,
kde appka prohrála 5 z 6 prvních tiketů (viz sekce výše).

Appka dala uživateli na výběr tři cesty (jít naplno na kurz / zůstat
u jistoty / střední cesta). Uživatel vybral **střední cestu**:

- `MAX_TICKET_LEGS`: 3 → 4
- `MIN_COMBINED_PROBABILITY` (`ticket_builder.py`): 0,45 → 0,40

Na dnešních datech to appce dalo kurz 1,61 → 1,87. Kombinovaná
jistota zůstala 56,8 % — nad appčinou původní podlahou 45 %. Appka
celé pásmo 1,9–3,0 netrefila, ale přiblížila se. Je to vědomě vyšší
riziko prohry, než appka měla před touhle změnou.

### Dva tikety denně (2026-08-12)

Uživatel appce navrhl jiné řešení stejného problému: místo jednoho
tiketu appka radši pošle rovnou dva. Appka porovnala appčiny dva
nejjistější trhy dneška:

- **Gemy** — jistota 84–88 % na leg, ale kurz jen 1,16–1,20 (appka i
  trh vidí jednostranný zápas stejně jistě).
- **Výherce zápasu** — jistota jen 62,6 % na leg, ale kurz 1,60–1,78
  (appka nikdy neumí odhadnout, kdo vyhraje, tak jistě jako appka umí
  odhadnout gemy jednostranného zápasu).

Appka appku rozdělila na DVA samostatné tikety (`ticket_type` v
`tickets` — `'games'` / `'winner'`), appka je posílá oba:

- `ticket_generation.generate_daily_tickets()` nahradilo
  `generate_daily_ticket()` — appka kandidáty spočítá JEDNOU (včetně
  H2H volání na api-tennis.com) a postaví z nich OBA tikety, ne
  dvakrát zvlášť.
- Gemový tiket appka staví ze stejné podlahy jako appka měla dřív
  (`MIN_COMBINED_PROBABILITY` = 0,40).
- Appka pro tiket na výherce přidala NOVOU, nižší podlahu
  (`MIN_COMBINED_PROBABILITY_WINNER` = 0,35) — appka trh výherce má
  bezpečnostní práh 0,62 (viz `market_thresholds`), takže appka dva
  favorité těsně nad prahem sami dají kombinovanou jistotu kolem
  38 % — appka vyšší podlahu by appce tenhle typ tiketu skoro nikdy
  nesestavila.
- `/admin/daily-tickets` appka teď posílá OBA tikety na Telegram,
  každý s vlastní popiskou ("GEMY" / "VÝHERCI" v
  `ticket_telegram.py`).

Ověřeno živě: appka dnešní data appce dala gemový tiket (4 legy, kurz
1,87, jistota 56,8 %) a tiket na výherce (2 legy, kurz 2,85, jistota
39,2 %) — appka oba appka úspěšně uložila a vyrenderovala.

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
- **Modely appka JE zkalibrovala** (viz nová sekce "Kalibrace modelů"
  níže) — appka měla `GAMES_STD_DEV`/`GAMES_ELO_GAP_SENSITIVITY`/
  Poissonův předpoklad na esa výrazně mimo realitu, teď appka je
  přeladila na měřená čísla. Samotné PRAHY v `market_thresholds`
  (0.62 / 0.58 / 0.58) appka zatím nechala — appka je teprve musí
  optimalizovat na přesnost/výtěžnost VLASTNÍCH tiketů (ne jen
  dílčích predikcí), až appka bude mít historii reálně poslaných
  tiketů, ne jen backtest jednotlivých zápasů.
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
7. **`API_BASE_URL` + `ADMIN_TASK_KEY` jako GitHub Actions repo secrets**
   (Settings → Secrets and variables → Actions) — bez nich appčin denní
   cron (`.github/workflows/daily-tickets.yml`) poběží, ale bude
   selhávat. Viz sekce "Denní automatizace" výše.

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
