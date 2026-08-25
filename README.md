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

1. **H2H poměr výher** — appka Elo odhad posune blíž k tomu, jak
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

Appka denní tiket rozdělila na DVA samostatné tikety (`ticket_type`
v `tickets` — `'games'` / `'winner'`) a posílá oba:

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

### Tiket na zítra místo na dnešek — oprava (2026-08-12)

Uživatel appce upozornil, že appčin tiket obsahoval zápasy na ZÍTRA,
ne na dnešek. Příčina: appka syncuje zápasy 3 dny dopředu
(`api_tennis_sync.sync_upcoming_matches`), ale appčina
`db.get_pending_matches()` neměla žádný horní limit na datum — appka
brala nejjistější picky ze VŠECH naplánovaných zápasů, ne jen z
dneška. Dnešní zápasy z větší části už začaly nebo skončily, takže
appce v žebříčku zbyly hlavně zítřejší.

Appka přidala `DAILY_TICKET_WINDOW_HOURS = 24` (`db.py`) —
`get_pending_matches()` teď appka omezí na zápasy v následujících
24 hodinách. Ověřeno živě: appka žebříček zápasů appce vzrostl z 6
na 31, a appčiny nové tikety appka postavila hlavně z dnešních
zápasů (jeden leg appce přesáhl do zítřejšího rána, appka to bere
jako v pořádku — pořád je to appka to samé okno 24 hodin, ne
"cokoliv naplánované").

### Favorité místo gemů (2026-08-12)

Uživatel appce poslal screenshot reálné sázenky — appka do ní nahodil
appčin gemový tiket. Appčina hranice byla 29,5 gemů, bookmaker ale na
sázence nabízel 26,5–27,5 gemů. Nebyla to appka stejná sázka za jinou
cenu — byla to JINÁ sázka (přísnější, hůř trefitelná). Appka to
přepočítala appčiným modelem na skutečnou hranici bookmakera:
appčina kombinovaná jistota klesla z 64,9 % (appčina hranice) na
44,1 % (bookmakerova hranice).

Trh výherce zápasu (match_winner) tenhle problém nemá — appka tam
žádná hranice není, je to appka stejná sázka u kteréhokoli bookmakera.
Uživatel appku požádal appku přesunout denní tikety čistě na favority:

- appka posílá DVA tikety, oba jen z trhu výherce zápasu
- appka nechá jen kandidáty s kurzem v pásmu **1,3–2,0** na leg
  (příliš jistý favorit pod 1,3 appce nedává výhodu, nad 2,0 už není
  favorit)
- appka přidává favority podle jistoty, dokud kombinovaný kurz
  nepřesáhne minimum **1,8**
- MAX_TICKET_LEGS zůstává 4, appka se ho ale skoro nikdy nedotkne —
  u favoritů appka kombinovaný kurz 1,8+ obvykle najde už na 1–2 legách
- druhý tiket appka staví ze ZBÝVAJÍCÍCH zápasů, ať appka dva tikety
  nikdy nesdílí appka stejný zápas

Appka novou logiku implementovala v `ticket_builder.build_favorites_ticket()`.
Starou logiku (`build_ticket`, gemový trh v `generate_daily_tickets`)
appka nesmazala, jen ji přestala volat pro denní broadcast — je to
funkční, reálně kalibrovaná práce, appka se k ní může vrátit, kdyby
někdy našla zdroj kurzů na gemy odpovídající reálným bookmakerům.

Ověřeno živě: appka dnešní den měla jen 1 kandidáta na výherce
(Virtanen, kurz 1,78) — to samo o sobě nedosáhlo na 1,8, takže appka
oba tikety vrátila jako None. Appka logiku ověřila i na širším okně
(72 hodin) — tam appka měla 2 kandidáty (Snigur 1,60, Virtanen 1,78)
a správně postavila tiket s kurzem 2,848.

### Skreč vyřazovala skoro všechny favority — zkrácení okna (2026-08-12)

Appka zjistila, proč měla jen 1 favorita. Bezpečnostní filtr appku
vyřazoval, pokud měl skreč za posledních 12 měsíců KTERÝKOLI z hráčů
— i soupeř favorita, i kdyby to bylo před 11 měsíci. Appka to
změřila na dnešních datech: appka měla 10 favoritů nad 62% jistotou,
appka filtr vyřadil 9 z nich kvůli skreči.

Uživatel appce řekl: vyřaď jen skreč z posledních 2 měsíců. Appka to
implementovala:

- `data_ingest.py`: nová konstanta `RETIREMENT_WINDOW_DAYS = 60`,
  appka ji počítá odděleně od `matches_played_12mo` (ten zůstává na
  365 dnech).
- appka přejmenovala sloupec `recent_retirements_12mo` na
  `recent_retirements_60d` (schema.sql, db.py, data_ingest.py,
  scripts/load_snapshot_into_db.py) — starý název appce lhal o tom,
  co appka číslo znamená.

Appčina databáze měla staré počty skreče, spočítané na 12měsíčním
okně. Appka nepřepočítávala celý historický import znovu — trvalo by
to hodiny. Místo toho appka udělala cílený přepočet jen pro hráče v
dnešních zápasech. Appka stáhla fixtures za posledních 60 dní
(7denní okna, 18 volání na api-tennis.com) a aktualizovala jejich
`recent_retirements_60d` podle skutečných dat.

Po týhle opravě appka dostala OBA tikety (kurz 2,42 a 2,33) místo
appka None.

### Jeden pick na tiket (2026-08-13)

Oba tikety z předchozí sekce appka druhý den prohrála. Uživatel appce
poslal skutečné výsledky, appka je porovnala s modelem na širším
vzorku (5 favoritů, ne jen 2). Appka trefila 3 z 5 (60 %) — appčina
průměrná jistota u nich byla 67 %. Na jednotlivých pickách appka
model appce vypadal v pořádku.

Problém byl v kombinaci. Appka spočítala appčinu vlastní kombinovanou
jistotu u obou tiketů předem — appka byla 52 % a 40 %. Appka musela
trefit VŠECHNY legy najednou, takže appka kombinovaná šance na výhru
byla vždycky nižší než appčina jistota jednotlivého picku.

Uživatel appce řekl: chci co nejpřesnější tipy, appka favorit vyhraje.
Appka to zjednodušila:

- `ticket_builder.build_favorites_ticket()` teď vrátí tiket s JEDNÍM
  nejjistějším favoritem v pásmu 1,3–2,0, žádné kombinování.
- Jistota picku appce teď přímo odpovídá šanci vyhrát celý tiket.
- Appka ztratila pevnou podlahu kurzu 1,8 — kurz vyjde, jaký vyjde
  (dnes 1,45 a 1,37).

Appka poctivě přiznává: nemůže slíbit vyšší úspěšnost, než appka sama
řekla u toho konkrétního picku (60–75 % podle případu). Appka jen
přestala appku zhoršovat kombinováním.

### Zpátky na dva tipy (2026-08-13, druhé kolo)

Uživatel hned poté řekl, ať appka vrátí JEDEN tiket se dvěma
nejjistějšími tipy dohromady — ne dva tikety po jednom picku.
Appka poslechla:

- `generate_daily_tickets()` appka nahradila `generate_daily_ticket()`
  — appka teď vrátí jeden tiket, ne dva.
- `build_favorites_ticket()` dostala parametr `num_legs`
  (`ticket_builder.DAILY_TICKET_LEGS = 2`) — appka vezme dva
  nejjistější favority z RŮZNÝCH zápasů v pásmu kurzu appka na leg
  (appka pásmo appka zpřísnila hned další den, viz sekce níže) a
  spojí je do jednoho tiketu.

Appka tady znovu připomíná varování výš: kombinovaný tiket musí
trefit OBA legy. Šance na výhru celého tiketu je proto nižší než
jistota lepšího picku samotného.

### Přísnější výběr favoritů (2026-08-13, třetí kolo)

Uživatel appce řekl, že dnešní tiket je lepší než včerejší. Zeptal
se, jestli appka výběr může zpřísnit ještě víc.

Appka to ověřila na dnešních 5 kandidátech. Top 2 picky (Shelton
75,4 %, Draper 74,0 %) zůstaly stejné, ať appka zpřísní, nebo ne.
Appka tedy mohla zpřísnit bez ztráty dnešního tiketu.

Dvě změny:

- appčina vlastní jistota (`market_thresholds.min_confidence` pro
  `match_winner`): appka z 0,62 na 0,65.
- appka `FAVORITE_MAX_LEG_ODDS` (`ticket_builder.py`): appka z 2,0
  na 1,7.

Kurz 1,7 znamená, že trh odhaduje hráči zhruba 59% šanci. Kurz 2,0
jen 50 %. Appka radši bere picky, kde hráče vidí jistě i trh, ne jen
appčin model.

### Reálné výsledky — 13.–15. 8. 2026

Appka svoje tikety průběžně kontroluje na živých datech
z api-tennis.com, ne jen na papíře.

**13. 8. — tiket č. 19** (Draper 74,0 % + Shelton 75,4 %, kurz 1,987):
Shelton vyhrál. Draper prohrál s Landalucem (outsider, appka mu dala
kurz jen 1,35–1,38 na trhu). Appka musí trefit oba legy, takže tiket
prohrál i přes jednu výhru.

**14.–15. 8. — 9 favoritů appky v příštím okně, appka je jen
sledovala, bez sázky:**

| Zápas | Favorit | Jistota | Výsledek |
|---|---|---|---|
| Faria – Brooksby | Brooksby | 70,6 % | prohrál |
| Medjedovic – Trungelliti | Medjedovic | 75,3 % | prohrál (appka vyřadila kvůli skreči) |
| Kovacevic – Khachanov | Khachanov | 72,7 % | prohrál (appka vyřadila kvůli skreči) |
| Merida Aguilar – Cilic | Merida Aguilar | 55,5 % | vyhrál |
| Stoiana – Valentova | Valentova | 63,7 % | vyhrál |
| Stephens – Kraus | Stephens | 60,6 % | vyhrál |
| Kenin – Lys | Lys | 58,3 % | prohrál |
| Marozsan – Zheng | Marozsan | 58,9 % | prohrál |
| Putintseva – Samsonova | Putintseva | 54,2 % | prohrál |

Jediný kandidát, co appce prošel filtrem na skutečný tiket (jistota
nad 65 %, kurz 1,3–1,7), byl Brooksby — a prohrál. Zajímavé je, že
appčin filtr na skreč correctně vyřadil oba silnější favority
(Medjedovic, Khachanov) — oba taky prohráli.

Z 9 favoritů appka vyhrála jen 3 (33 %), i když appčiny jistoty byly
55–75 %. Malý vzorek (jeden den), appka to dál sleduje.

**Souhrn zatím (13.–15. 8., ticket 19 + tahle dávka):** 4 výhry
z 11 sledovaných favoritů. Appka pokračuje ve sledování, ať appka
vidí, jestli jde o výkyv, nebo o problém v appčině kalibraci.

### Reálné výsledky — 15.–16. 8. 2026

Širší vzorek než minule — 28 favoritů appky z jednoho dne (15. 8.),
ne jen dva z tiketu. Appka je jen sledovala, bez sázky (dva zápasy
ještě nedohrané, appka je z výsledku vynechala):

| Zápas | Favorit | Jistota | Výsledek |
|---|---|---|---|
| Zverev – Norrie | Zverev | 85,9 % | vyhrál |
| Djokovic – Tirante | Djokovic | 85,8 % | prohrál |
| Pegula – Waltert | Pegula | 83,5 % | vyhrál |
| Rakhimova – Sakkari | Sakkari | 78,4 % | vyhrál |
| Shnaider – Maria | Shnaider | 78,1 % | vyhrál |
| Hanfmann – Fils | Fils | 74,7 % | vyhrál |
| Sonmez – Anisimova | Anisimova | 74,2 % | vyhrál |
| Vallejo – Vacherot | Vacherot | 73,6 % | prohrál |
| Navarro – Kalinina | Navarro | 73,2 % | vyhrál |
| Ostapenko – Frech | Ostapenko | 73,2 % | prohrál |
| Parry – Mertens | Mertens | 71,0 % | prohrál |
| Bellucci – Mensik | Mensik | 69,6 % | vyhrál |
| Atmane – Etcheverry | Etcheverry | 69,2 % | prohrál |
| Cirstea – Bartunkova | Cirstea | 65,9 % | vyhrál |
| Zhang – Day | Zhang | 64,8 % | vyhrál |
| Kecmanovic – Cobolli | Cobolli | 64,2 % | vyhrál |
| Struff – Tabilo | Tabilo | 63,1 % | vyhrál |
| Bucsa – Chwalinska | Bucsa | 62,9 % | prohrál |
| Fery – Duckworth | Duckworth | 62,4 % | prohrál |
| McNally – Kalinskaya | Kalinskaya | 62,1 % | vyhrál |
| Jodar – Shapovalov | Shapovalov | 60,5 % | prohrál |
| Paul – Hurkacz | Paul | 59,7 % | vyhrál |
| Lehecka – Berrettini | Lehecka | 59,7 % | vyhrál |
| Halys – De Minaur | De Minaur | 58,6 % | vyhrál |
| Arnaldi – Landaluce | Arnaldi | 56,1 % | prohrál |
| Blockx – Navone | Navone | 55,0 % | prohrál |

**16 výher, 10 proher — 61,5 %,** při průměrné appčině jistotě
68,7 %. Mnohem blíž appčinu vlastnímu odhadu než 14.–15. 8. (61,5 %
vs. 33 %) — appka to bere jako potvrzení, že ten předchozí den byl
spíš výkyv, ne problém appčiny kalibrace.

**Souhrn celkem (13.–16. 8.):** 20 výher z 37 sledovaných favoritů
(54,1 %). Appka pokračuje ve sledování.

**Appčin gemový tiket z 15. 8.** (Zverev + Halys, appčina hranice
29,5 gemů na oba): podle appčiny vlastní hranice appka vyhrála
(Zverev 27 gemů, Halys 29 gemů — oba pod 29,5). Uživatel ale reálně
sázel u Tipsportu na hranici 28,5 — tam appka na Halysovi prohrála
(29 gemů je nad 28,5). Přesně tohle appčino varování na tiketu řeší:
appka dá nejjistější appčinu hranici, ale konkrétní bookmaker může
nabídnout jinou — a i půl gemu dokáže leg rozhodnout.

### Dolní hranice kurzu snížena na 1,2 (2026-08-15)

Uživatel chtěl na tiketu Navarro (kurz 1,58) i Zvereva (kurz 1,25)
zároveň. Zverev byl pod appčiným tehdejším minimem 1,3, appka ho proto
sama nenabídla.

Uživatel appce řekl, ať minimum sníží natrvalo. Appka `FAVORITE_MIN_LEG_ODDS`
snížila z 1,3 na 1,2 (`ticket_builder.py`). Pásmo je teď 1,2-1,7.

Kombinovaný tiket Navarro + Zverev: kurz 1,975 (1,58 × 1,25),
kombinovaná jistota 62,9 % (0,732 × 0,859).

### Vedlejší tiket na gemy (2026-08-15)

Uživatel poslal screenshot skutečného Tipsport tiketu — 4 sázky na
trh gemů, všechny vyhrané. Appka to ověřila na appčiných vlastních
datech: hranice (26,5 a 27,5) i kurz appce seděly skoro přesně se
skutečným bookmakerem. Starý mismatch (appka 29,5 vs. Tipsport 26,5),
kvůli kterému appka trh gemů předtím opustila, teda nebyl univerzální
problém — jen platil u jednoho konkrétního zápasu.

Uživatel chtěl každý den DVA tikety — appka teď posílá hlavní na
favority (`generate_daily_ticket`) a vedlejší na gemy
(`generate_games_ticket`), oba v 8:00 (`ticket_builder.build_games_ticket`).
Appka je generuje a posílá nezávisle — pokud appka nenajde kandidáta
na jeden z nich, druhý appka pošle stejně.

Gemový tiket potřeboval vlastní pásmo kurzu (`GAMES_MIN_LEG_ODDS`
1,25, `GAMES_MAX_LEG_ODDS` 1,7) — appka pro jeden zápas dostane
kandidáty na desítky hranic najednou, a bez pásma by brala tu
nejširší (appce nejjistější), ale s kurzem skoro 1,10 — tam appka
nemá výhodu. S pásmem bere hranici blízko té, co appka ověřila na
Tipsportu.

Appka na gemový tiket dává varování přímo v Telegram zprávě — hranice
se u konkrétního bookmakera může lišit, appka to negarantuje. Appka
tomu zatím věří jen na vzorku 4 zápasů, sleduje to dál.

### Čas začátku zápasu appka posílala o 2,5 hodiny napřed (2026-08-15)

Uživatel poslal screenshot appčina gemového tiketu na Tipsportu. Časy
zápasů appce neseděly s tím, co appka posílala v Telegramu — appka to
ověřila na dvou zápasech:

| Zápas | Appka (před opravou) | Tipsport (skutečnost) |
|---|---|---|
| Halys – De Minaur | 22:00 | 19:20 |
| Zverev – Norrie | 04:30 | 02:10 |

Příčina: appka `event_time` z api-tennis.com brala jako UTC, uložila
ji tak do DB, a při zobrazení ještě přičetla +2h na pražský čas
(`ticket_telegram.py`, `_kickoff_local`). Ve skutečnosti appka
`event_time` dostává už ve středoevropském čase — appka si tím čas
posunula dvakrát.

Oprava (`api_tennis_sync.py`, `_parse_start_time`): appka `event_time`
teď nejdřív lokalizuje na Europe/Prague a teprve pak ho převede na
skutečné UTC pro uložení. Appka to ověřila opakovaným syncem — nové
časy sedí na 20-40 minut (běžný posun v pořadí zápasů), ne na 2,5
hodiny.

Appka tenhle problém měla otevřený jako neověřenou otázku od začátku
appčina vývoje (viz starší verze README) — appka to konečně mohla
ověřit díky reálnému screenshotu od uživatele.

### Dynamický počet legů — kurz vždy aspoň 1,8 (2026-08-17)

Uživatel dva dny po sobě chtěl vyšší kurz (1,9+), ale appka měla
pevně 2 legy — někdy to stačilo na kurz přes 1,9, jindy (jako
17. 8., kdy byli nejjistější favorité zároveň nejlevnější na trhu)
appka na 2 legy nedala ani 1,6.

Uživatel řekl, ať appka vždycky dosáhne kurzu aspoň 1,8, a počet legů
si sama doladí — někdy 2 zápasy, někdy 3.

`build_favorites_ticket` a `build_games_ticket` appka přepsala:
místo pevného počtu legů appka bere kandidáty od nejjistějšího a
přidává je, dokud kombinovaný kurz nedosáhne `DAILY_TICKET_MIN_ODDS`
/ `GAMES_TICKET_MIN_ODDS` (1,8), nebo appka nedojde na
`MAX_TICKET_LEGS` (4). Víc legů, než appka na 1,8 potřebuje, appka
nepřidává — čím víc legů, tím nižší kombinovaná jistota.

Ověřeno na datech ze 17. 8.: hlavní tiket appce vyšel na 3 legy
(kurz 1,876), gemový tiket taky na 3 (kurz 2,081) — oba dny předtím
appka měla jen 2.

### Práh jistoty snížen zpátky na 0,60 (2026-08-19)

19. 8. appka na hlavní tiket našla jen JEDNOHO kandidáta (Rybakina,
77,5 %, kurz 1,43) — dva další, co by se hodili (Mensik, Pegula),
appka vyřadila kvůli nedávné skreči soupeře, a Zverev (61,8 %, kurz
1,35) neprošel appčiným prahem jistoty 65 %.

Uživatel chtěl vždycky dva tipy s kurzem aspoň 1,9 na tiketu. Appka
zjistila, že práh 65 % (zvednutý z 62 % 2026-08-13, viz "Přísnější
výběr favoritů") appce zbytečně bral kandidáty jako Zverev, co by
appce daly kombinovaný kurz přes cíl appky.

Uživatel snížení potvrdil. `market_thresholds.min_confidence` appka
snížila na 0,60 (schema.sql i běžící DB). Ověřeno: Rybakina (1,43) +
Zverev (1,35) = kurz 1,931.

### Challenger jako doplněk hlavního touru (2026-08-20)

20. 8. bylo Cincinnati ve čtvrtfinále — na celém ATP+WTA touru zbylo
jen 8 zápasů, appka na hlavní tiket nenašla ani jednoho kandidáta.
Uživatel se zeptal, jestli dnes nehraje ještě někdo jiný — appka bez
filtru na typ turnaje zjistila, že běží desítky Challenger a ITF
zápasů zároveň, jen appka je dřív nikdy neukazovala.

Appka ověřila, že api-tennis.com má pro Challenger (`event_type_key`
281 muži, 272 ženy) STEJNOU strukturu jako pro hlavní tour — historii,
statistiky (esa atd.) i kurzy (Home/Away, Over/Under gemů). ITF appka
zatím nepřidávala (o úroveň níž, appka to nechává jako další krok,
kdyby appka Challenger nestačil).

Appka Challenger napojila do STEJNÉHO hráčského poolu jako hlavní
tour — appka nezavádí nový `tour`, jen v `api_tennis_provider.py`
rozšířila `event_type_key` na seznam (hlavní + Challenger) pro každý
tour. Zápas appka pozná podle `event_type_type` z odpovědi a dá mu
`tourney_level='C'` — appka tím konečně naplnila `K_FACTOR_BY_LEVEL['C']`
v `elo_model.py`, co appka měla připravený, ale nikdy nedostal
data.

Appka spustila celý historický přepočet znovu (2022-01-01 až dnes,
appka to musela kvůli zachování celé appčiny historie, ne jen
posledních pár měsíců):

| | před | po |
|---|---|---|
| ATP zápasů | 21 388 | 68 232 |
| ATP hráčů | 1 637 | 3 734 |
| WTA zápasů | 20 697 | 27 876 |
| WTA hráčů | 1 613 | 1 898 |

Ověřeno na 20. 8.: appka měla po Challengeru **11 kandidátů** na
hlavní tiket místo 0. Hlavní tiket appka postavila (Safiullin 67,9 % +
Fearnley 67,1 %, kurz 1,97), gemový tiket taky (kurz 2,05).

- **`recent_retirements_60d` appka má aktuální jen u hráčů z dnešních
  zápasů** (cílený přepočet, viz "Skreč vyřazovala skoro všechny
  favority" výše) — zbytek appčiny databáze (přes 3000 hráčů z
  historického importu) má pořád starou hodnotu spočítanou na
  12měsíčním okně. Appka to opraví buď při dalším plném historickém
  přepočtu, nebo appka může spustit ten samý cílený skript znovu na
  širší množinu hráčů, kdyby to appka potřebovala dřív.
- **api-tennis.com nemá bookmaker trh na esa** (stejně jako the-odds-api)
  — appka na trh `total_aces` počítá jen vlastní pravděpodobnost, bez
  tržního kurzu. `ticket_builder.py` takový leg do tiketu nezahrne
  (potřebuje `market_odds`), dokud appka nenajde zdroj kurzu na esa.
- **`tourney_level` appka rozlišuje jen Challenger vs. zbytek** (viz
  "Challenger jako doplněk hlavního touru") — Grand Slam/Masters/běžný
  Tour turnaj appka pořád nerozezná, jede na defaultní hodnotě
  K-faktoru. Vylepšit appka může přes `tournament_round`/`tournament_name`
  heuristiku, zatím appka to neřešila.
- **Modely appka JE zkalibrovala** (viz nová sekce "Kalibrace modelů"
  níže) — appka měla `GAMES_STD_DEV`/`GAMES_ELO_GAP_SENSITIVITY`/
  Poissonův předpoklad na esa výrazně mimo realitu, teď appka je
  přeladila na měřená čísla. PRAHY v `market_thresholds` (aktuálně
  0.60 / 0.58 / 0.58) appka doladila jen na `match_winner`, na
  přání uživatele a podle reálných výsledků (viz "Práh jistoty
  snížen zpátky na 0,60") — appka je teprve musí systematicky
  optimalizovat na přesnost/výtěžnost VLASTNÍCH tiketů (ne jen
  dílčích predikcí), až appka bude mít historii reálně poslaných
  tiketů, ne jen backtest jednotlivých zápasů.
- **api-tennis.com placený plán** — appka neví, jaký plán klíč uživatele
  pokrývá (Starter/Premium/Business/Ultra, $40–120/měsíc) ani jaký má
  denní request limit. Appka doporučuje ověřit v api-tennis.com
  dashboardu před rozjetím pravidelného cronu (historický bulk import
  napříč lety appka dělá po měsíčních oknech, viz `MONTH_CHUNK_DAYS`
  v `api_tennis_ingest.py`, ale u nízkého tieru by appka mohla narazit
  na denní strop).

### Reálné výsledky — 21.–22. 8. 2026

**21. 8. — oba tikety prohrály:**
- Hlavní (Fearnley + Tarvet, kurz 1,90): Tarvet vyhrál, Fearnley
  prohrál s Danielem 0:2. Appka musí trefit oba → prohra.
- Gemy (Fritz/Nakashima, Gauff/Kostyuk, Martinez/Glinka, kurz 2,45):
  Fritz–Nakashima appce dalo 32 gemů — appčina hranice byla pod 31,5,
  takže o JEDEN gem appka prohrála (appka v appčiných vlastních
  datech měla k dispozici i hranici 32,5, kde by appka vyhrála).
  Zbylé dva legy appka trefila. Appka musí trefit všechny tři → prohra.

Uživatel appce poslal reálný Tipsport screenshot na tenhle gemový
tiket. Připomíná to dřívější případ s Halysem (viz "Vedlejší tiket
na gemy") — appčina hranice gemů se u konkrétního bookmakera pořád
může lišit o půl až jeden gem, a to appku občas rozhodne.

**22. 8. — hlavní tiket vyhrál** (Cobolli/Fils + Royer/Martinez,
appčin kurz 1,74, uživatel u Tipsportu dostal 1,88): Fils vyhrál
s Cobollim 2:0, Royer vyhrál s Martinezem 2:0. Uživatel to potvrdil
reálným screenshotem, appka to ověřila i na vlastních datech.

### Statistika appky, bezpečnostní rezerva a nová kalibrace (2026-08-24)

Uživatel chtěl vidět statistiku všech reálných tiketů. Appka to
sesbírala z databáze a ověřila naživo:

- **Hlavní tiket (favority):** 5 výher / 5 proher, P/L −1,177 jednotky.
- **Gemy:** 3 výhry / 6 proher, P/L −4,124 jednotky.
- **Celkem: 8 výher / 11 proher, P/L −5,301 jednotky** (13.–23. 8.,
  appčina vlastní data z api-tennis.com).

**Rozbor gemových proher:** 4 z 6 proher appka netrefila o 2,5–5,5
gemu, ne jen o vlásek. Reakce: appka do `build_games_ticket` přidala
`GAMES_LINE_SAFETY_MARGIN` (1 gem) — místo nejjistější hranice bere
appka o krok bezpečnější, i za cenu nižšího kurzu (appka smí jít pod
`GAMES_MIN_LEG_ODDS`, dolní mez je `MIN_USABLE_ODDS`).

**Rozbor proher na favoritech:** appka rozdělila 20 legů podle pásma
jistoty. Pásmo 60–70 % sedělo nejhůř (66 % slib, 50 % realita) — appka
zvažovala zvednout `market_thresholds.min_confidence` z 0,60 na 0,68.

**Než to appka udělala, spustila nový backtest** (2022–2026,
s Challengerem, 16 906 zápasů na výherce) — a ten appku zastavil.
Model výherce appce v pásmu 60–90 % sedí dobře, dokonce mírně
podhodnocuje vlastní jistotu. Malý vzorek 8 legů byla smůla, ne
signál. Práh appka nechala na 0,60.

**Backtest ale odhalil jinou chybu.** Appčin analytický skript
(`analyze_backtest.py`) appce hlásil starou hodnotu appčina rozptylu
gemů (4,2) — appka ji ale už 11. 8. přeladila na 6,8/7,24/8,98 podle
povrchu. Skript srovnával proti zastaralému číslu, appka si to špatně
vyložila jako novou chybu. Po opravě srovnání appka zjistila jediný
reálný rozdíl: appčina hranice gemů na tvrdém povrchu (21,5) je moc
nízko, čerstvá data ukazují 22,7. Tuhle appka 12. 8. snížila z 22,9
podle jen 6 prvních tiketů — appka to vrátila zpátky nahoru (viz
market_models.py, "Baseline vrácen zpět").

### Odkaz na web u Telegram tiketu + zamčená stránka s dnešním tiketem (2026-08-25)

Uživatel chtěl k dennímu tiketu v Telegramu přidat odkaz na web —
appka posílá tiket pořád celý přímo do Telegramu (web zůstává
nepovinný krok), tlačítko jen navíc míří na `netlify_site/tiket.html`.

**Zamykání appka postavila na skutečném účtu, ne na jednom sdíleném
klíči.** První verze měla jeden `X-Member-Key` pro všechny platící,
uživatel ale chtěl rozlišit jednotlivé lidi — platící zákazníky a
lidi, co dostanou kód na zkoušku zdarma. Řešení:

- existující registrace/přihlášení (`/auth/register`, `/auth/login`) —
  appka je měla hotové z dřívějška, jen nevyužité na webu,
- nový **kupónový systém**: tabulky `coupon_codes` a
  `coupon_redemptions` (viz `schema.sql`). Kód se vygeneruje přes
  `POST /admin/coupons` (`X-Admin-Key`, tělo `code`/`days_granted`/
  `max_uses`) a uživatel ho uplatní přes `POST /coupons/redeem`
  (přihlášený, tělo `{code}`) — appka mu posune
  `users.subscription_until` o `days_granted` dní od pozdějšího z
  `now()` nebo dosavadního konce předplatného, ať kód nezkrátí
  aktivní předplatné,
- `/member/today-ticket` teď žádá platné přihlášení A aktivní
  `subscription_until` (`require_active_subscription`), ne starý
  sdílený klíč.

appka to živě otestovala (lokální backend + Playwright): registrace →
bez předplatného appka vrátí 402 → uplatnění kódu → tiket se zobrazí →
odhlášení → opětovné přihlášení, předplatné zůstává aktivní. Cestou
appka narazila na chybu — nové tabulky vznikly pod `postgres`
superuserem, ne appčiným provozním účtem `courtedge`, appce chyběla
práva (`GRANT ALL ... TO courtedge`) — appka to opravila hned.

**Pořád chybí:** appka `MEMBER_ACCESS_KEY` z konfigurace úplně
odstranila (zamykání teď jede jen přes účty), backend ale pořád nemá
nikde živé nasazení, takže `/tiket.html` nikomu nic nezobrazí, dokud
appka projekt nedostane na Render (viz "Manuální kroky" níže, bod 8 o
migraci kupónových tabulek).

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
8. **Aplikovat migraci `coupon_codes`/`coupon_redemptions`** na appčinu
   produkční DB. Samostatný migrační skript appka zatím nemá — nový
   nasazený projekt spustí `schema.sql` celé (obě tabulky už uvnitř
   jsou), existující DB stačí pustit poslední dva `CREATE TABLE` bloky
   ze `schema.sql` ručně přes `psql "$DATABASE_URL"`. Viz "Zamčená
   webová stránka s dnešním tiketem" níže.

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
