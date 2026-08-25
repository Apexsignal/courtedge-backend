"""
ticket_telegram.py — vyrenderování tiketu jako JPG "sázenky" a odeslání
do Telegramu (appčin VLASTNÍ bot, viz README — nesouvisí s ApexSignalem).

Fonty appka bere z `assets/fonts/` (nakopírované do repozitáře, DejaVu
Sans — volně šiřitelný font), aby appka nezávisela na fontech
nainstalovaných v kontejneru na Renderu.
"""
import io
import json
import os
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageFont
from zoneinfo import ZoneInfo

PRAGUE_TZ = ZoneInfo("Europe/Prague")

# appka 2026-08-25 na přání uživatele přidala k tiketu tlačítko na web
# (karusel reálných výher, viz netlify_site/) — web appka schválně
# nedělá povinným krokem (uživatel: "Web neni nutny krok"), tiket appka
# pořád posílá celý přímo do Telegramu, odkaz je jen navíc.
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://courtedge-tenis-tipy.netlify.app")

MARKET_LABELS_CS = {
    "match_winner": "Výherce zápasu",
    "total_games": "Počet gemů",
    "total_aces": "Počet es",
}

TICKET_TYPE_LABELS_CS = {
    "favorites": "FAVORITÉ",
    "games": "GEMY",
}

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLED_FONT_DIR = os.path.join(_THIS_DIR, "assets", "fonts")
_SYSTEM_FONT_DIR = "/usr/share/fonts/truetype/dejavu"

FONT_DIR = _BUNDLED_FONT_DIR if os.path.isdir(_BUNDLED_FONT_DIR) else _SYSTEM_FONT_DIR
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

WIDTH = 900
PADDING = 32
BG = (18, 22, 30)
CARD_BG = (28, 34, 46)
ACCENT = (255, 176, 32)
TEXT = (235, 238, 242)
SUBTEXT = (150, 160, 175)
GREEN = (86, 214, 130)
LINE = (48, 56, 70)


def _kickoff_local(start_time_utc: datetime) -> str:
    try:
        return start_time_utc.astimezone(PRAGUE_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(start_time_utc)


def selection_label(market_code: str, selection: str, line: float | None, player_a: str, player_b: str) -> str:
    if market_code == "match_winner":
        return player_a if selection == "player_a" else player_b
    if line is None:
        return selection.capitalize()
    verb = "Nad" if selection == "over" else "Pod"
    return f"{verb} {line}"


def build_ticket_caption(ticket: dict) -> str:
    total_odds = ticket.get("total_odds", 0)
    ticket_type = ticket.get("ticket_type")
    type_suffix = f" ({TICKET_TYPE_LABELS_CS[ticket_type].lower()})" if ticket_type in TICKET_TYPE_LABELS_CS else ""
    line_warning = (
        "\n\nHranice gemů se u tvého bookmakera může lišit — než klikneš, ověř si ji."
        if ticket_type == "games" else ""
    )
    return (
        f"🎾 CourtEdge tiket{type_suffix} · kurz {total_odds:.2f}\n\n"
        "Appka jen doporučuje — sázku si klikáš sám, kde chceš.\n\n"
        "Není to jistota. 18+, sázej jen to, co si můžeš dovolit prohrát."
        f"{line_warning}"
    )


def _pluralize_vyber(n: int) -> str:
    """Appka skloňuje počet výběrů — 1 výběr, 2-4 výběry, 5+ výběrů."""
    if n == 1:
        return "1 výběr"
    if 2 <= n <= 4:
        return f"{n} výběry"
    return f"{n} výběrů"


def wrap(draw, text, font, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_ticket(ticket: dict) -> Image.Image:
    f_title = ImageFont.truetype(FONT_BOLD, 34)
    f_h2 = ImageFont.truetype(FONT_BOLD, 22)
    f_body = ImageFont.truetype(FONT_REGULAR, 20)
    f_small = ImageFont.truetype(FONT_REGULAR, 16)
    f_odds = ImageFont.truetype(FONT_BOLD, 22)

    legs = ticket.get("legs", [])
    row_h = 92
    header_h = 120
    footer_h = 90
    height = header_h + len(legs) * row_h + footer_h

    img = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(img)

    ticket_type = ticket.get("ticket_type")
    subtitle = "TENISOVÝ TIKET"
    if ticket_type in TICKET_TYPE_LABELS_CS:
        subtitle = f"TENISOVÝ TIKET · {TICKET_TYPE_LABELS_CS[ticket_type]}"

    draw.text((PADDING, PADDING), "CourtEdge", font=f_title, fill=ACCENT)
    draw.text((PADDING, PADDING + 44), subtitle, font=f_h2, fill=TEXT)

    total_odds = ticket.get("total_odds", 0)
    draw.text(
        (WIDTH - PADDING, PADDING + 10), f"kurz {total_odds:.2f}",
        font=f_title, fill=GREEN, anchor="ra",
    )

    y = header_h
    draw.line([(PADDING, y - 10), (WIDTH - PADDING, y - 10)], fill=LINE, width=2)

    for i, leg in enumerate(legs):
        row_top = y + i * row_h
        card_rect = [PADDING, row_top + 6, WIDTH - PADDING, row_top + row_h - 6]
        draw.rounded_rectangle(card_rect, radius=12, fill=CARD_BG)

        matchup = f"{leg.get('player_a', '')} – {leg.get('player_b', '')}"
        matchup_lines = wrap(draw, matchup, f_body, WIDTH - 2 * PADDING - 180)
        ty = row_top + 14
        for line in matchup_lines[:2]:
            draw.text((PADDING + 20, ty), line, font=f_body, fill=TEXT)
            ty += 26

        tourney = f"{leg.get('tourney_name', '')} · {_kickoff_local(leg['start_time']) if leg.get('start_time') else ''}".strip()
        draw.text((PADDING + 20, row_top + row_h - 32), tourney, font=f_small, fill=SUBTEXT)

        leg_odds = leg.get("market_odds") or 0
        draw.text(
            (WIDTH - PADDING - 20, row_top + 14), f"{leg_odds:.2f}",
            font=f_odds, fill=GREEN, anchor="ra",
        )
        prob = leg.get("model_probability", 0) * 100
        market_txt = MARKET_LABELS_CS.get(leg.get("market_code"), leg.get("market_code", ""))
        pick_txt = selection_label(
            leg.get("market_code"), leg.get("selection"), leg.get("line"),
            leg.get("player_a", ""), leg.get("player_b", ""),
        )
        draw.text(
            (WIDTH - PADDING - 20, row_top + 38), market_txt,
            font=f_small, fill=SUBTEXT, anchor="ra",
        )
        draw.text(
            (WIDTH - PADDING - 20, row_top + 60), f"{pick_txt} ({prob:.0f}%)",
            font=f_small, fill=SUBTEXT, anchor="ra",
        )

    footer_y = height - footer_h + 20
    draw.line([(PADDING, footer_y - 14), (WIDTH - PADDING, footer_y - 14)], fill=LINE, width=2)
    draw.text(
        (PADDING, footer_y), f"{_pluralize_vyber(len(legs))} · vygenerováno {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        font=f_body, fill=TEXT,
    )

    watermark_text = f"CourtEdge · #{ticket.get('ticket_id')}" if ticket.get("ticket_id") else "CourtEdge"
    img = add_watermark(img, watermark_text)

    return img


def add_watermark(base_img: Image.Image, text: str) -> Image.Image:
    """Jemný opakující se diagonální vodoznak — nezabrání sdílení, ale
    kdo obrázek přeposílá dál, je z něj dohledatelný."""
    tile = Image.new("RGBA", (320, 160), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tile)
    tfont = ImageFont.truetype(FONT_REGULAR, 16)
    tdraw.text((10, 70), text, font=tfont, fill=(255, 255, 255, 34))
    tile = tile.rotate(24, expand=True)

    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    for y in range(0, base_img.height, tile.height):
        for x in range(0, base_img.width, tile.width):
            overlay.alpha_composite(tile, (x, y))

    return Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB")


def send_admin_alert(message: str, bot_token: str = None, chat_id: str = None) -> dict:
    """appka pošle prostý textový alert appčinu adminovi (stejný chat jako
    denní tiket) — appka to používá, když appce ráno selže krok v
    `.github/workflows/daily-tickets.yml`, ať appka o tom ví hned, ne
    až se zákazník zeptá, proč mu nepřišel tiket."""
    token = bot_token or os.environ["TELEGRAM_BOT_TOKEN"]
    chat = chat_id or os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat, "text": message}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def send_ticket_to_telegram(ticket: dict, bot_token: str = None, chat_id: str = None) -> dict:
    """Vyrenderuje tiket a rovnou ho pošle do Telegramu. Token/chat_id appka
    vezme z argumentů, jinak z proměnných prostředí TELEGRAM_BOT_TOKEN /
    TELEGRAM_CHAT_ID (appčin VLASTNÍ bot)."""
    token = bot_token or os.environ["TELEGRAM_BOT_TOKEN"]
    chat = chat_id or os.environ["TELEGRAM_CHAT_ID"]

    img = render_ticket(ticket)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    buf.seek(0)

    # appka 2026-08-25 přesměrovala tlačítko z appčiny homepage na
    # `/tiket.html` — tam appka dnešní tiket doopravdy ukazuje, zamčené
    # skutečným přihlášením a aktivním předplatným (viz backend_api.py
    # `/member/today-ticket`); homepage má jen historii výher pro
    # přesvědčení nových lidí.
    reply_markup = {"inline_keyboard": [[{"text": "🌐 Tiket na webu", "url": f"{WEBSITE_URL}/tiket.html"}]]}

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    resp = requests.post(
        url,
        data={
            "chat_id": chat,
            "caption": build_ticket_caption(ticket),
            "reply_markup": json.dumps(reply_markup),
        },
        files={"photo": ("ticket.jpg", buf, "image/jpeg")},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
