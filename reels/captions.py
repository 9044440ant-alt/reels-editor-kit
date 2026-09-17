"""Субтитры и плашка хука. Рисуются Pillow по кадрам, шрифт Unbounded.

Стили:
  A — «караоке-плашка»: 2-3 слова, звучащее слово на цветной плашке, плашка переезжает по словам
  B — «крупно»: 1-2 слова крупно, каждое появление с пружинкой, акценты цветом
  C — «заливка»: слова стоят контуром и заливаются белым по мере произнесения, акценты заливаются цветом
"""
import re
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from .common import CONFIG, FONTS, W

ACCENTS = {"lime": (203, 242, 74), "yellow": (255, 214, 10)}
DARK = (12, 13, 12)
WHITE = (255, 255, 255)
SAFE_L, SAFE_R = 90, 140          # справа кнопки Instagram
BAND_H = 620
CAP_CENTER_Y = 1250               # центр субтитров: ниже лица, выше подписи Instagram (с ~1500)

# Масштаб субтитров (config.json → size_k)
SIZE_K = float(CONFIG["size_k"])
STYLE = {
    "A": {"words": 3, "chars": 16, "size": int(82 * SIZE_K), "weight": "ExtraBold", "upper": True},
    "B": {"words": 2, "chars": 11, "size": int(116 * SIZE_K), "weight": "Black", "upper": True},
    "C": {"words": 4, "chars": 20, "size": int(72 * SIZE_K), "weight": "Bold", "upper": True},
}


def parse_color(c):
    """lime | yellow | #RRGGBB | (r, g, b) → (r, g, b)."""
    if isinstance(c, tuple):
        return c
    if isinstance(c, str) and c.startswith("#") and len(c) == 7:
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    return ACCENTS.get(c, ACCENTS["lime"])


@lru_cache(maxsize=64)
def font(size, weight="Bold", family="Unbounded.ttf"):
    f = ImageFont.truetype(str(FONTS / family), size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def clean(w, upper):
    w = w.replace("—", "-").replace("–", "-").replace("«", "").replace("»", "").replace('"', "")
    w = re.sub(r"[.,;:…]+$", "", w)
    return w.upper() if upper else w


def ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_back(x, k=1.9):
    x = min(max(x, 0.0), 1.0)
    return 1 + (k + 1) * (x - 1) ** 3 + k * (x - 1) ** 2


# ---------- разбиение на фразы ----------

FUNC = {"в", "во", "на", "за", "к", "ко", "с", "со", "о", "об", "у", "и", "а", "но", "по", "из", "от", "до",
        "для", "не", "ни", "что", "как", "же", "бы", "то", "при", "под", "над", "без", "через", "про", "это"}
LONG = 11   # слово из 11+ букв идёт отдельной фразой: иначе шрифт ужимается до мелкого


def _plain(w):
    return re.sub(r"[^\wё-]", "", w.lower())


def make_cues(words, style):
    st = STYLE[style]
    cues, cur = [], []
    for k, w in enumerate(words):
        long_w = len(_plain(w["w"])) >= LONG
        if cur and long_w:  # длинное слово начинает новую фразу
            cues.append(cur)
            cur = []
        cur.append(w)
        nxt = words[k + 1] if k + 1 < len(words) else None
        chars = sum(len(x["w"]) + 1 for x in cur)
        brk = (nxt is None or len(cur) >= st["words"] or long_w
               or re.search(r"[.!?;:,]$", w["w"])
               or (nxt["s"] - w["e"]) > 0.35
               or len(_plain(nxt["w"])) >= LONG
               or chars + len(nxt["w"]) > st["chars"] * (2 if style != "B" else 1)
               or (w["e"] - cur[0]["s"]) > 1.8)
        if brk:
            # служебное слово в конце фразы переносим к следующему («копят | на отдых»)
            if nxt is not None and len(cur) > 1 and _plain(cur[-1]["w"]) in FUNC \
                    and not re.search(r"[.!?;:,]$", cur[-1]["w"]) and (nxt["s"] - cur[-1]["e"]) <= 0.35:
                carry = cur.pop()
                cues.append(cur)
                cur = [carry]
                continue
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    out = []
    for k, c in enumerate(cues):
        s = c[0]["s"] - 0.04
        nxt_s = cues[k + 1][0]["s"] - 0.04 if k + 1 < len(cues) else None
        e = c[-1]["e"] + 0.25
        if nxt_s is not None and (nxt_s - c[-1]["e"]) < 0.6:
            e = nxt_s
        out.append({"s": s, "e": e, "words": c})
    return out


# ---------- раскладка ----------

def layout(cue, style):
    st = STYLE[style]
    size = st["size"]
    maxw = W - SAFE_L - SAFE_R
    texts = [clean(w["w"], st["upper"]) for w in cue["words"]]
    while True:
        f = font(size, st["weight"])
        sp = f.getlength(" ")
        ws = [f.getlength(t) for t in texts]
        full = sum(ws) + sp * (len(ws) - 1)
        if max(ws) <= maxw:
            if full <= maxw:
                split = len(texts)
            else:
                # две строки: разрез, при котором самая длинная строка короче всего
                best = None
                for k in range(1, len(texts)):
                    a = sum(ws[:k]) + sp * (k - 1)
                    b = sum(ws[k:]) + sp * (len(ws) - k - 1)
                    if a <= maxw and b <= maxw and (best is None or max(a, b) < best[0]):
                        best = (max(a, b), k)
                split = best[1] if best else None
            if split is not None:
                idx = list(range(len(texts)))
                lines = [[(k, texts[k], ws[k]) for k in idx[:split]]]
                if split < len(texts):
                    lines.append([(k, texts[k], ws[k]) for k in idx[split:]])
                break
        size -= 6
        if size < 36:
            lines = [[(k, t, f.getlength(t)) for k, t in enumerate(texts)]]
            break
    asc, desc = f.getmetrics()
    lh = int((asc + desc) * 1.12)
    total_h = lh * len(lines)
    y = (BAND_H - total_h) // 2
    boxes = [None] * len(texts)
    cx = SAFE_L + maxw / 2
    for ln in lines:
        lw = sum(t[2] for t in ln) + sp * (len(ln) - 1)
        x = cx - lw / 2
        for k, t, tw in ln:
            boxes[k] = {"x": x, "y": y, "w": tw, "h": asc + desc, "t": t}
            x += tw + sp
        y += lh
    return f, boxes, size


# ---------- рендер ----------

class Captioner:
    def __init__(self, words, style="A", accent="lime", cap_y=CAP_CENTER_Y):
        self.style = style
        self.cap_y = cap_y
        self.acc = parse_color(accent)
        self.cues = make_cues(words, style)
        self._lay = {}
        self._static = {}

    def _get_layout(self, ci):
        if ci not in self._lay:
            self._lay[ci] = layout(self.cues[ci], self.style)
        return self._lay[ci]

    def _shadow(self, ci):
        """Мягкая тёмная подложка под текстом: читаемость на светлом фоне."""
        key = ("sh", ci)
        if key not in self._static:
            f, boxes, size = self._get_layout(ci)
            m = Image.new("L", (W, BAND_H), 0)
            d = ImageDraw.Draw(m)
            for b in boxes:
                d.text((b["x"], b["y"]), b["t"], font=f, fill=255, stroke_width=max(4, size // 7), stroke_fill=255)
            m = m.filter(ImageFilter.GaussianBlur(size // 4))
            m = m.point(lambda v: int(v * 0.62))
            sh = Image.new("RGBA", (W, BAND_H), DARK + (0,))
            sh.putalpha(m)
            self._static[key] = sh
        return self._static[key]

    def cue_at(self, t):
        for ci, c in enumerate(self.cues):
            if c["s"] <= t < c["e"]:
                return ci
        return None

    def render(self, t):
        """Возвращает (RGBA-полоса, y0) или None, если в момент t субтитра нет."""
        ci = self.cue_at(t)
        if ci is None:
            return None
        cue = self.cues[ci]
        f, boxes, size = self._get_layout(ci)
        band = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
        band.alpha_composite(self._shadow(ci))
        d = ImageDraw.Draw(band)
        words = cue["words"]
        active = None
        for k, w in enumerate(words):
            if w["s"] - 0.03 <= t:
                active = k
        stroke = max(3, size // 16)
        getattr(self, "_draw_" + self.style)(band, d, f, boxes, words, active, t, size, stroke)
        # появление фразы: подъём на 14 px и проявление за 0.1 с
        a = ease_out((t - cue["s"]) / 0.1)
        y0 = self.cap_y - BAND_H // 2 + int((1 - a) * 14)
        if a < 1:
            alpha = band.getchannel("A").point(lambda v: int(v * a))
            band.putalpha(alpha)
        return band, y0

    # --- A: караоке-плашка
    def _draw_A(self, band, d, f, boxes, words, active, t, size, stroke):
        pad_x, pad_y = size * 0.2, size * 0.16
        # границы плашки по строке (верх заглавной — низ базовой линии), не по метрикам шрифта
        cap_top, cap_bot = f.getbbox("ЁЙH")[1], f.getbbox("H")[3]

        def rect(b):
            return (b["x"] - pad_x, b["y"] + cap_top - pad_y, b["x"] + b["w"] + pad_x, b["y"] + cap_bot + pad_y)

        on_plate = False
        if active is not None:
            b = boxes[active]
            prog = ease_out((t - words[active]["s"]) / 0.09)
            r1 = rect(b)
            if active > 0 and abs(boxes[active - 1]["y"] - b["y"]) < 1:
                r0 = rect(boxes[active - 1])  # та же строка: плашка переезжает
                r = [r0[k] + (r1[k] - r0[k]) * prog for k in range(4)]
            else:  # новая строка или первое слово: плашка раскрывается на месте
                cx, cy = (r1[0] + r1[2]) / 2, (r1[1] + r1[3]) / 2
                sc = 0.6 + 0.4 * ease_back(min(1.0, (t - words[active]["s"]) / 0.12))
                r = [cx - (r1[2] - r1[0]) / 2 * sc, cy - (r1[3] - r1[1]) / 2 * sc,
                     cx + (r1[2] - r1[0]) / 2 * sc, cy + (r1[3] - r1[1]) / 2 * sc]
            d.rounded_rectangle(r, radius=int(size * 0.18), fill=self.acc + (255,))
            on_plate = prog >= 0.999  # слово темнеет, только когда плашка целиком под ним
        for k, b in enumerate(boxes):
            if k == active and on_plate:
                d.text((b["x"], b["y"]), b["t"], font=f, fill=DARK)
            else:
                col = self.acc if (words[k].get("emph") and k != active) else WHITE
                d.text((b["x"], b["y"]), b["t"], font=f, fill=col, stroke_width=stroke, stroke_fill=DARK)

    # --- B: крупно с пружинкой
    def _draw_B(self, band, d, f, boxes, words, active, t, size, stroke):
        layer = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for k, b in enumerate(boxes):
            if active is None or k > active:
                continue
            col = self.acc if words[k].get("emph") else WHITE
            ld.text((b["x"], b["y"]), b["t"], font=f, fill=col, stroke_width=stroke + 3, stroke_fill=DARK)
        s = ease_back((t - words[0]["s"]) / 0.16)
        s = 0.72 + 0.28 * s
        if abs(s - 1) > 0.005:
            nw, nh = int(W * s), int(BAND_H * s)
            layer = layer.resize((nw, nh), Image.BILINEAR)
            tmp = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
            tmp.alpha_composite(layer.crop((max(0, (nw - W) // 2), max(0, (nh - BAND_H) // 2),
                                            max(0, (nw - W) // 2) + min(W, nw), max(0, (nh - BAND_H) // 2) + min(BAND_H, nh))),
                                (max(0, (W - nw) // 2), max(0, (BAND_H - nh) // 2)))
            layer = tmp
        band.alpha_composite(layer)

    # --- C: контур → заливка по мере произнесения
    def _draw_C(self, band, d, f, boxes, words, active, t, size, stroke):
        for k, b in enumerate(boxes):
            # контур: обводка белым, внутренность прозрачная
            hollow = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
            hd = ImageDraw.Draw(hollow)
            hd.text((b["x"], b["y"]), b["t"], font=f, fill=WHITE + (70,), stroke_width=3, stroke_fill=WHITE + (255,))
            band.alpha_composite(hollow)
            w = words[k]
            dur = max(0.12, w["e"] - w["s"])
            prog = ease_out((t - w["s"]) / dur)
            if prog <= 0:
                continue
            col = self.acc if w.get("emph") else WHITE
            fill = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
            fd = ImageDraw.Draw(fill)
            fd.text((b["x"], b["y"]), b["t"], font=f, fill=col)
            cut = int(b["x"] + b["w"] * prog) + 1
            if cut < W:
                fill.paste((0, 0, 0, 0), (cut, 0, W, BAND_H))
            band.alpha_composite(fill)


# ---------- плашка хука ----------

class HookTitle:
    def __init__(self, text, hold=3.5, accent="lime", total=None):
        self.text = (text or "").replace("—", "-").upper()
        self.hold = hold if hold else (total or 9999)
        self.acc = parse_color(accent)
        self.img = self._build() if self.text else None

    def _build(self):
        size = 60
        maxw = 880
        while size > 34:
            f = font(size, "ExtraBold")
            words, lines, cur = self.text.split(), [], ""
            for w in words:
                tt = (cur + " " + w).strip()
                if f.getlength(tt) <= maxw:
                    cur = tt
                else:
                    lines.append(cur)
                    cur = w
            lines.append(cur)
            if len(lines) <= 2:
                break
            size -= 4
        asc, desc = f.getmetrics()
        lh = int((asc + desc) * 1.05)
        pad = int(size * 0.45)
        widths = [f.getlength(l) for l in lines]
        bw = int(max(widths) + pad * 2)
        bh = int(lh * len(lines) + pad * 1.3)
        img = Image.new("RGBA", (bw + 40, bh + 40), (0, 0, 0, 0))
        sh = Image.new("L", img.size, 0)
        ImageDraw.Draw(sh).rounded_rectangle([20, 26, 20 + bw, 26 + bh], radius=18, fill=150)
        img.paste((0, 0, 0, 255), (0, 0), sh.filter(ImageFilter.GaussianBlur(12)))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([20, 20, 20 + bw, 20 + bh], radius=18, fill=self.acc + (255,))
        y = 20 + pad * 0.65
        for l, lw in zip(lines, widths):
            d.text((20 + (bw - lw) / 2, y), l, font=f, fill=DARK)
            y += lh
        return img

    def render(self, t):
        if not self.img or t > self.hold:
            return None
        a_in = ease_out(t / 0.22)
        a_out = ease_out((self.hold - t) / 0.22)
        a = min(a_in, a_out)
        img = self.img
        if a < 1:
            img = img.copy()
            img.putalpha(img.getchannel("A").point(lambda v: int(v * a)))
        x = (W - img.width) // 2
        y = 230 - int((1 - a_in) * 40)
        return img, x, y
