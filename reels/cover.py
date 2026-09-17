#!/usr/bin/env python3
"""
Генератор обложек-оверлеев для Reels.

Выход: PNG 1080x1920 RGBA — прозрачный верх + вшитый градиент затемнения снизу,
белый жирный заголовок ЗАГЛАВНЫМИ + жёлтая плашка. Его можно наложить на своё фото
прямо в Instagram или склеить с кадром (python3 -m reels cover собирает оба варианта).

Пример:
  python3 -m reels.cover --headline "ЛОГОТИП СТОИЛ|ИМ ДИРЕКТОРА" \
                     --box "СКАНДАЛ · 27 ИЮЛЯ" --out cover.png

Canva AI для текста НЕ использовать - врёт кириллицу.
"""

import argparse
import pathlib
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1080, 1920
# Unbounded — тот же шрифт, что в субтитрах роликов. Arial остаётся запасным.
FONT_PATH = str(pathlib.Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Unbounded.ttf")
FONT_FALLBACK = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# ЕДИНЫЙ кегль заголовка для ВСЕХ обложек — чтобы в ленте размер не прыгал.
# Не подгонять под длину строки: длинный заголовок переносится по словам.
# Unbounded шире Arial, поэтому единый кегль заголовка меньше прежнего 84.
FONT_SIZE = 66
BOX_SIZE = 34

WHITE = (255, 255, 255, 255)
YELLOW = (245, 197, 24, 255)
DARK = (20, 20, 20, 255)


def font(size, weight="ExtraBold"):
    try:
        f = ImageFont.truetype(FONT_PATH, size)
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
        return f
    except OSError:
        return ImageFont.truetype(FONT_FALLBACK, size)


def gradient(start_y=740, max_a=225, gamma=1.25):
    """Затемнение снизу вверх: прозрачно выше start_y, плотно у самого низа."""
    col = []
    for y in range(H):
        if y < start_y:
            col.append(0)
        else:
            t = (y - start_y) / (H - start_y)
            col.append(int(max_a * (t ** gamma)))
    mask = Image.new("L", (1, H))
    mask.putdata(col)
    mask = mask.resize((W, H))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    layer.putalpha(mask)
    return layer


def wrap(lines, f, draw, maxw):
    """Перенос по словам под ФИКСИРОВАННЫЙ кегль. Кегль не меняется никогда:
    единый размер во всех обложках, чтобы лента не прыгала."""
    out = []
    for raw in lines:
        words = raw.split()
        if not words:
            continue
        cur = words[0]
        for w in words[1:]:
            probe = cur + " " + w
            if draw.textlength(probe, font=f) <= maxw:
                cur = probe
            else:
                out.append(cur)
                cur = w
        out.append(cur)
    return out


def make(headline_lines, box_text, out, bottom=1600):
    img = Image.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 0)), gradient())
    draw = ImageDraw.Draw(img)

    # ФИКСИРОВАННЫЙ кегль. Длинные строки переносятся, не мельчают.
    size = FONT_SIZE
    f = font(size)
    maxw = W - 150
    headline_lines = wrap(headline_lines, f, draw, maxw)

    line_h = int(size * 1.34)   # у Unbounded высокие буквы, строкам нужен воздух
    bf = font(BOX_SIZE, "Bold")
    box_h = (bf.getbbox(box_text)[3] - bf.getbbox(box_text)[1]) + 36 if box_text else 0

    block_h = len(headline_lines) * line_h + (36 + box_h if box_text else 0)
    top = bottom - block_h

    # мягкая тень под заголовком - читаемость на любом фото
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    y = top
    for l in headline_lines:
        w = draw.textlength(l, font=f)
        sd.text(((W - w) / 2, y), l, font=f, fill=(0, 0, 0, 210))
        y += line_h
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(9)))

    draw = ImageDraw.Draw(img)
    y = top
    for l in headline_lines:
        w = draw.textlength(l, font=f)
        draw.text(((W - w) / 2, y), l, font=f, fill=WHITE)
        y += line_h

    if box_text:
        y += 36
        bw = draw.textlength(box_text, font=bf)
        padx = 34
        boxw = bw + padx * 2
        bx = (W - boxw) / 2
        draw.rounded_rectangle([bx, y, bx + boxw, y + box_h], radius=14, fill=YELLOW)
        tb = bf.getbbox(box_text)
        draw.text((bx + padx, y + (box_h - (tb[3] - tb[1])) / 2 - tb[1]),
                  box_text, font=bf, fill=DARK)

    img.save(out)
    print(f"saved {out} {img.size}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--headline", required=True,
                   help="Строки заголовка через | (например 'ЛОГОТИП СТОИЛ|ИМ ДИРЕКТОРА')")
    p.add_argument("--box", default="", help="Текст жёлтой плашки")
    p.add_argument("--out", required=True, help="Путь к выходному PNG")
    p.add_argument("--bottom", type=int, default=1600, help="Нижняя граница текстового блока")
    a = p.parse_args()
    lines = [s.strip() for s in a.headline.split("|") if s.strip()]
    make(lines, a.box.strip(), a.out, a.bottom)


if __name__ == "__main__":
    main()
