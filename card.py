"""Картинки бота: баннер для /start и карточка итогов розыгрыша.

Солик нарисован на чёрном, поэтому кладётся на фон смешиванием «экран»:
чёрное исчезает само, мех и подсветка остаются — без вырезания по маске,
от которого в глазах и между усами остаются дыры.
"""

from __future__ import annotations

import io
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import config

ASSETS = config.BASE_DIR / "assets"
W, H = 1280, 720
WHITE = (243, 245, 248)
GREY = (141, 148, 158)
DIM = (92, 99, 110)


@lru_cache(maxsize=None)
def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "fonts" / f"Unbounded-{weight}.ttf"), size)


@lru_cache(maxsize=1)
def _cat() -> Image.Image:
    cat = Image.open(ASSETS / "solik.png").convert("RGB")
    side = int(H * 1.02)
    cat = cat.resize((side, side), Image.LANCZOS)
    # Фон исходника не чисто чёрный: при «экране» он проступает светлым
    # прямоугольником. Срезаем уровень чёрного и гасим края к нулю.
    cat = cat.point(lambda v: max(0, int((v - 16) * 1.07)))
    fade = Image.new("L", (side, side), 255)
    draw = ImageDraw.Draw(fade)
    edge = int(side * 0.14)
    for i in range(edge):
        draw.line((i, 0, i, side), fill=int(255 * i / edge))
    return Image.composite(cat, Image.new("RGB", (side, side)), fade)


def _background(seed: int) -> Image.Image:
    """Графит с мягкими пятнами боке, как на баннере солика."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H), (8, 9, 11))
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((W * 0.45, -H * 0.3, W * 1.25, H * 1.1), fill=38)
    img = Image.composite(Image.new("RGB", (W, H), (36, 39, 45)), img,
                          glow.filter(ImageFilter.GaussianBlur(160)))
    dots = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(dots)
    for _ in range(26):
        x, y, r = rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(5, 18)
        tone = rnd.randint(28, 70)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(tone, tone, tone + 4))
    dots = dots.filter(ImageFilter.GaussianBlur(4))
    return ImageChops.screen(img, dots)


def _sparkle(draw: ImageDraw.ImageDraw, x: float, y: float, size: float) -> None:
    draw.line((x - size, y, x + size, y), fill=WHITE, width=2)
    draw.line((x, y - size, x, y + size), fill=WHITE, width=2)
    draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=WHITE)


def _fit(text: str, weight: str, size: int, width: int) -> ImageFont.FreeTypeFont:
    """Самый крупный кегль, при котором строка влезает в ширину."""
    while size > 20:
        font = _font(weight, size)
        if font.getlength(text) <= width:
            return font
        size -= 4
    return _font(weight, size)


def _compose(title: str, lines: list[str], seed: int) -> bytes:
    img = _background(seed)
    cat = _cat()
    layer = Image.new("RGB", (W, H), (0, 0, 0))
    layer.paste(cat, (W - cat.width + 60, H - cat.height + 70))
    img = ImageChops.screen(img, layer)

    draw = ImageDraw.Draw(img)
    left, column = 84, 560
    draw.text((left, 92), "S O L U T I O N S", font=_font("Bold", 20), fill=DIM)
    font = _fit(title, "Black", 112, column)
    draw.text((left, 150), title, font=font, fill=WHITE)
    y = 150 + font.getbbox(title)[3] + 44
    for i, line in enumerate(lines):
        line_font = _fit(line, "Bold", 34 if i == 0 else 26, column)
        draw.text((left, y), line, font=line_font, fill=WHITE if i == 0 else GREY)
        y += line_font.getbbox(line)[3] + 20
    draw.text((left, H - 92), "солик)", font=_font("Black", 30), fill=WHITE)
    _sparkle(draw, W * 0.53, H * 0.2, 14)
    _sparkle(draw, W * 0.93, H * 0.62, 10)

    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def banner() -> bytes:
    return _compose("розыгрыши", ["честный рандом", "посты, подписки и итоги — сам"], 7)


def results(winners: int, players: int, giveaway_id: int) -> bytes:
    import texts

    head = f"{winners} {texts.plural(winners, 'победитель', 'победителя', 'победителей')}"
    tail = f"из {players} {texts.plural(players, 'участника', 'участников', 'участников')}"
    return _compose("итоги", [head, tail, f"розыгрыш #{giveaway_id}"], giveaway_id)
