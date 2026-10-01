# Digitální stírací kartičky 💛

Statický web (HTML + CSS + vanilla JS, bez backendu a bez buildu). Obdarovaný otevře odkaz,
setře prstem zlatá políčka a zážitky si „uplatní“ zprávou přes WhatsApp nebo SMS.
**Veškerý obsah je v `site/config.json`.** Pro jiného člověka stačí vyměnit config a fotky.

## Struktura

```
stiraci-karticky/
├── netlify.toml          # Netlify: publikuje složku site/
├── site/                 # ← to, co se nasazuje
│   ├── index.html        # kostra + OG meta tagy (generuje tools/prepare.py)
│   ├── styles.css
│   ├── app.js            # stírání, listování, stav v localStorage, uplatnění, konfety
│   ├── config.json       # VŠECHNY texty, telefon, kartičky, fotky
│   ├── robots.txt
│   └── img/              # zmenšené fotky (WebP + JPEG), og.jpg, icon-180.png  (mimo git)
├── photos-src/           # originální fotky (mimo git)
├── print/                # qr.png, pozvanka-a6.png, pozvanka-a6.pdf  (mimo git)
└── tools/
    ├── prepare.py        # fotky → web, OG náhled + tagy, QR, pozvánka A6
    └── fonts/            # Yeseva One + Poppins (OFL) pro tiskoviny
```

## config.json

| klíč | význam |
|---|---|
| `recipient`, `age`, `title`, `subtitle` | úvodní obrazovka |
| `greeting`, `message`, `validity`, `from` | obrazovka s přáním |
| `giverPhone` | číslo dárce ve formátu `+420…`. Dokud obsahuje `X`, WhatsApp/SMS se otevře bez příjemce. |
| `siteUrl` | výsledná adresa webu (OG tagy, QR) |
| `ogTitle`, `ogDescription` | náhled odkazu ve WhatsAppu |
| `storageKey` | klíč v localStorage. Pro nového člověka ho změň. |
| `claimMessage` | text zprávy, `{title}` = název kartičky |
| `jokerClaimMessage` | text zprávy u žolíku, `{wish}` = napsané přání |
| `cover.photo`, `cover.cropTop`, `cover.position`, `cover.printFocusY` | úvodní fotka. `cropTop: 0.15` ořízne horních 15 % (screenshot), `position` = CSS object-position, `printFocusY` = ohnisko ořezu pro OG/pozvánku (0 = nahoře) |
| `cards[]` | `id`, `title`, `subtitle`, `photo`, volitelně `"type": "joker"`, `"main": true`, `photoPosition` |

## Nová verze pro jiného člověka

1. Fotky dej do `photos-src/` (názvy jako v configu, přípona nevadí).
2. Uprav `site/config.json` (texty, telefon, `siteUrl`, nový `storageKey`).
3. `pip install pillow qrcode && python3 tools/prepare.py`
4. Nasaď složku `site/` (Netlify: `netlify deploy --prod --dir site`, nebo přetáhni ZIP složky `site/`).

## Testování a reset

- Lokálně: `python3 -m http.server 8000 --directory site` → http://localhost:8000
- Reset stavu (setřené, uplatněné, přání): přidej k adrese `?reset=1`.
- Na kartičce lze políčko odkrýt i klávesou Enter (přístupnost).
