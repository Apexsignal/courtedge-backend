#!/usr/bin/env python3
"""
Příprava podkladů pro „digitální stírací kartičky“ – vše se řídí site/config.json.

Co skript dělá:
  1. Zmenší a zkomprimuje fotky z photos-src/ do site/img/ (max 1200 px, WebP + JPEG).
     Fotky se hledají podle názvu bez přípony (IMG_2140.jpeg → IMG_2140.*).
  2. Vytvoří náhled pro WhatsApp/Facebook (site/img/og.jpg, 1200×630) a ikonu (icon-180.png).
  3. Zapíše Open Graph meta tagy do site/index.html (mezi <!-- OG:START --> a <!-- OG:END -->).
  4. Pokud je v configu vyplněné "siteUrl", vygeneruje do print/:
       - qr.png                QR kód na odkaz
       - pozvanka-a6.png       tisknutelná pozvánka A6 (300 dpi)
       - pozvanka-a6.pdf       totéž jako PDF ve formátu A6

Použití:
  pip install pillow qrcode
  python3 tools/prepare.py              # vše
  python3 tools/prepare.py --skip-images
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
IMG = SITE / "img"
SRC = ROOT / "photos-src"
PRINT = ROOT / "print"
FONTS = Path(__file__).resolve().parent / "fonts"

MAX_SIDE = 1200
NAVY = (31, 37, 71)
GOLD = (184, 146, 63)
GOLD_LIGHT = (212, 175, 90)
LILAC = (243, 236, 243)
MUTED = (91, 95, 122)
PINK = (231, 169, 181)
WHITE = (255, 255, 255)


def stem(name):
    return re.sub(r"\.[A-Za-z0-9]+$", "", name or "")


def find_source(name):
    s = stem(name)
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".webp", ".JPG", ".JPEG", ".PNG"):
        p = SRC / (s + ext)
        if p.exists():
            return p
    matches = [p for p in SRC.glob(s + ".*")]
    return matches[0] if matches else None


def open_photo(path, crop_top=0.0):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im).convert("RGB")
    if crop_top and crop_top > 0:
        w, h = im.size
        im = im.crop((0, int(h * crop_top), w, h))
    return im


def save_web(im, base):
    im = im.copy()
    im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    im.save(IMG / f"{base}.jpg", "JPEG", quality=80, optimize=True, progressive=True)
    im.save(IMG / f"{base}.webp", "WEBP", quality=76, method=6)
    return im


def cover_crop(im, size, focus_y=0.35):
    """Ořízne obrázek na požadovaný poměr stran (jako CSS object-fit: cover)."""
    tw, th = size
    w, h = im.size
    scale = max(tw / w, th / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = int(max(0, min(nh - th, (nh - th) * focus_y)))
    return im.crop((left, top, left + tw, top + th))


def parse_position_y(pos, default=0.35):
    m = re.search(r"(\d+(?:\.\d+)?)%\s*$", pos or "")
    if m:
        return float(m.group(1)) / 100
    if pos and pos.strip().endswith("top"):
        return 0.0
    if pos and pos.strip().endswith("bottom"):
        return 1.0
    return default


def print_focus(cfg):
    """Svislé ohnisko (0 = nahoře, 1 = dole) pro ořez coveru na šířku (OG náhled, pozvánka)."""
    c = cfg.get("cover", {})
    if c.get("printFocusY") is not None:
        return float(c["printFocusY"])
    return parse_position_y(c.get("position"), 0.3)


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def gold_text(base, xy, text, fnt):
    """Vykreslí text se zlatým svislým přechodem."""
    draw = ImageDraw.Draw(base)
    xy = (int(round(xy[0])), int(round(xy[1])))
    bbox = tuple(int(round(v)) for v in draw.textbbox(xy, text, font=fnt))
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    top, mid, bot = (228, 198, 119), GOLD, (143, 110, 42)
    for y in range(h):
        t = y / max(1, h - 1)
        if t < 0.55:
            k = t / 0.55
            c = tuple(int(top[i] + (mid[i] - top[i]) * k) for i in range(3))
        else:
            k = (t - 0.55) / 0.45
            c = tuple(int(mid[i] + (bot[i] - mid[i]) * k) for i in range(3))
        gd.line([(0, y), (w, y)], fill=c)
    mask = Image.new("L", base.size, 0)
    ImageDraw.Draw(mask).text(xy, text, font=fnt, fill=255)
    mask = mask.crop(bbox)
    base.paste(grad, (bbox[0], bbox[1]), mask)
    return bbox


def wrap(draw, text, fnt, max_w):
    words, lines, line = text.split(), [], ""
    for w in words:
        test = (line + " " + w).strip()
        if draw.textlength(test, font=fnt) <= max_w:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
def process_images(cfg):
    IMG.mkdir(parents=True, exist_ok=True)
    cover_cfg = cfg.get("cover", {})
    items = [("cover", cover_cfg.get("photo"), float(cover_cfg.get("cropTop") or 0))]
    for c in cfg["cards"]:
        items.append((c.get("id"), c.get("photo"), float(c.get("cropTop") or 0)))

    cover_img = None
    for ident, name, crop in items:
        src = find_source(name)
        if not src:
            print(f"  ! chybí zdrojová fotka pro {ident}: {name} (hledám v {SRC})")
            continue
        im = open_photo(src, crop)
        out = save_web(im, stem(name))
        kb = (IMG / f"{stem(name)}.webp").stat().st_size // 1024
        print(f"  ✓ {name:16s} → {out.size[0]}×{out.size[1]}  ({kb} kB webp)")
        if ident == "cover":
            cover_img = im
    return cover_img


def make_og_and_icon(cfg, cover_img):
    if cover_img is None:
        p = IMG / (stem(cfg["cover"]["photo"]) + ".jpg")
        if not p.exists():
            print("  ! chybí cover pro OG náhled")
            return
        cover_img = Image.open(p).convert("RGB")
    fy = print_focus(cfg)
    og = cover_crop(cover_img, (1200, 630), fy)
    og.save(IMG / "og.jpg", "JPEG", quality=82, optimize=True, progressive=True)
    print("  ✓ og.jpg 1200×630")

    icon = Image.new("RGB", (180, 180), LILAC)
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle((10, 10, 170, 170), radius=36, fill=WHITE, outline=GOLD_LIGHT, width=4)
    f = font("YesevaOne-Regular.ttf", 92)
    text = str(cfg.get("age", "♥"))
    bbox = d.textbbox((0, 0), text, font=f)
    x = (180 - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = (180 - (bbox[3] - bbox[1])) / 2 - bbox[1]
    gold_text(icon, (x, y), text, f)
    icon.save(IMG / "icon-180.png", optimize=True)
    print("  ✓ icon-180.png")


def write_og_tags(cfg):
    index = SITE / "index.html"
    src = index.read_text(encoding="utf-8")
    site_url = (cfg.get("siteUrl") or "").rstrip("/")
    img_url = f"{site_url}/img/og.jpg" if site_url else "img/og.jpg"
    title = html.escape(cfg.get("ogTitle") or cfg.get("title") or "")
    desc = html.escape(cfg.get("ogDescription") or cfg.get("subtitle") or "")
    lines = [
        "<!-- OG:START (generováno nástrojem tools/prepare.py z config.json) -->",
        f"  <title>{title}</title>",
        f'  <meta name="description" content="{desc}">',
        '  <meta property="og:type" content="website">',
        f'  <meta property="og:title" content="{title}">',
        f'  <meta property="og:description" content="{desc}">',
    ]
    if site_url:
        lines.append(f'  <meta property="og:url" content="{html.escape(site_url)}/">')
    lines += [
        f'  <meta property="og:image" content="{html.escape(img_url)}">',
        '  <meta property="og:image:type" content="image/jpeg">',
        '  <meta property="og:image:width" content="1200">',
        '  <meta property="og:image:height" content="630">',
        '  <meta property="og:locale" content="cs_CZ">',
        '  <meta name="twitter:card" content="summary_large_image">',
        "  <!-- OG:END -->",
    ]
    new = re.sub(r"<!-- OG:START.*?<!-- OG:END -->", "\n".join(lines), src, flags=re.S)
    index.write_text(new, encoding="utf-8")
    print(f"  ✓ OG tagy v index.html (og:image = {img_url})")


def make_qr(url, box=20):
    import qrcode
    from qrcode.constants import ERROR_CORRECT_Q

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_Q, box_size=box, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color=NAVY, back_color=WHITE).convert("RGB")


def make_invitation(cfg, url):
    PRINT.mkdir(parents=True, exist_ok=True)
    qr = make_qr(url)
    qr.save(PRINT / "qr.png", optimize=True)
    print(f"  ✓ print/qr.png  ({url})")

    # A6 = 105 × 148 mm, při 300 dpi = 1240 × 1748 px
    W, H = 1240, 1748
    M = 72
    card = Image.new("RGB", (W, H), LILAC)
    d = ImageDraw.Draw(card)

    # jemný zlatý rámeček
    d.rounded_rectangle((28, 28, W - 29, H - 29), radius=44, outline=GOLD_LIGHT, width=4)

    # fotka
    cover = Image.open(IMG / (stem(cfg["cover"]["photo"]) + ".jpg")).convert("RGB")
    ph_w, ph_h = W - 2 * M, 860
    fy = print_focus(cfg)
    photo = cover_crop(cover, (ph_w, ph_h), fy)
    card.paste(photo, (M, M), rounded_mask((ph_w, ph_h), 40))

    # „36“ + nadpis
    y0 = M + ph_h + 46
    age = str(cfg.get("age", ""))
    title = cfg.get("title", "")
    subtitle = cfg.get("subtitle", "")
    f_age = font("YesevaOne-Regular.ttf", 210)
    f_title = font("YesevaOne-Regular.ttf", 86)
    age_w = d.textlength(age, font=f_age) if age else 0
    gap = 36 if age else 0
    max_title_w = W - 2 * M - age_w - gap
    while d.textlength(title, font=f_title) > max_title_w and f_title.size > 40:
        f_title = font("YesevaOne-Regular.ttf", f_title.size - 2)
    f_sub = font("Poppins-Regular.ttf", 38)

    if age:
        abox = d.textbbox((0, 0), age, font=f_age)
        gold_text(card, (M - abox[0], y0 - abox[1]), age, f_age)
        age_h = abox[3] - abox[1]
    else:
        age_h = 160
    tx = M + age_w + gap
    tbox = d.textbbox((0, 0), title, font=f_title)
    sbox = d.textbbox((0, 0), subtitle, font=f_sub)
    block_h = (tbox[3] - tbox[1]) + 22 + (sbox[3] - sbox[1])
    ty = y0 + (age_h - block_h) / 2
    d.text((tx - tbox[0], ty - tbox[1]), title, font=f_title, fill=NAVY)
    d.text((tx - sbox[0], ty + (tbox[3] - tbox[1]) + 22 - sbox[1]), subtitle, font=f_sub, fill=MUTED)

    # QR blok
    qy = y0 + age_h + 54
    box_h = H - M - qy
    d.rounded_rectangle((M, qy, W - M, qy + box_h), radius=36, fill=WHITE)
    q_size = box_h - 56
    qimg = qr.resize((q_size, q_size), Image.NEAREST)
    card.paste(qimg, (M + 28, qy + 28))

    text_x = M + 28 + q_size + 40
    text_w = W - M - 36 - text_x
    f_inv = font("YesevaOne-Regular.ttf", 60)
    inv = cfg.get("invitationText", "Naskenuj a setři si svůj dárek")
    lines = wrap(d, inv, f_inv, text_w)
    while len(lines) > 4 and f_inv.size > 36:
        f_inv = font("YesevaOne-Regular.ttf", f_inv.size - 4)
        lines = wrap(d, inv, f_inv, text_w)
    f_small = font("Poppins-Regular.ttf", 28)
    small = wrap(d, "Otevři fotoaparát v telefonu a namiř ho na kód.", f_small, text_w)
    line_h = int(f_inv.size * 1.18)
    small_h = int(f_small.size * 1.4) * len(small)
    total_h = line_h * len(lines) + 24 + small_h
    ty = qy + (box_h - total_h) / 2
    for ln in lines:
        d.text((text_x, ty), ln, font=f_inv, fill=NAVY)
        ty += line_h
    ty += 24
    d.line((text_x, ty - 10, text_x + 90, ty - 10), fill=GOLD_LIGHT, width=4)
    ty += 8
    for ln in small:
        d.text((text_x, ty), ln, font=f_small, fill=MUTED)
        ty += int(f_small.size * 1.4)

    card.save(PRINT / "pozvanka-a6.png", dpi=(300, 300), optimize=True)
    card.save(PRINT / "pozvanka-a6.pdf", "PDF", resolution=300.0)
    print("  ✓ print/pozvanka-a6.png + .pdf (A6, 300 dpi)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-images", action="store_true", help="nezpracovávat znovu fotky")
    args = ap.parse_args()

    cfg = json.loads((SITE / "config.json").read_text(encoding="utf-8"))
    print("Fotky:")
    cover_img = None if args.skip_images else process_images(cfg)
    print("Náhled a ikona:")
    make_og_and_icon(cfg, cover_img)
    write_og_tags(cfg)
    url = (cfg.get("siteUrl") or "").strip()
    if url:
        print("Tiskoviny:")
        make_invitation(cfg, url if url.endswith("/") else url + "/")
    else:
        print("  (siteUrl v config.json je prázdné – QR a pozvánku vygeneruju až po nasazení)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
