"""Вставки поверх ролика: видео (B-roll), картинка/скриншот, счётчик-цифра, текстовая карточка."""
import re, subprocess
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from .common import W, H, FPS
from .captions import font, ease_out, ease_back, parse_color, DARK, WHITE


class VideoReader:
    """Отдаёт кадры клипа 1080x1920 по одному; если клип короче вставки — держит последний кадр."""

    def __init__(self, path, start=0.0, frames=None, grade=""):
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},fps={FPS}"
              + (f",{grade}" if grade else "") + ",format=rgb24")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(path), "-an",
               "-vf", vf]
        if frames:
            cmd += ["-frames:v", str(frames)]
        cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=W * H * 3 * 2)
        self.last = None

    def read(self):
        buf = self.p.stdout.read(W * H * 3) if self.p else b""
        if len(buf) == W * H * 3:
            self.last = Image.frombuffer("RGB", (W, H), buf, "raw", "RGB", 0, 1).copy()
        return self.last

    def close(self):
        if self.p:
            self.p.stdout.close()
            self.p.wait()
            self.p = None


def dim(frame, k=0.4):
    return ImageEnhance.Brightness(frame).enhance(k)


def fmt_num(v, decimals=0):
    """Русская запись: пробел между разрядами, запятая перед дробной частью (1,5, а не 1.5)."""
    return f"{v:,.{decimals}f}".replace(",", " ").replace(".", ",")


class Insert:
    def __init__(self, spec, accent="lime"):
        self.spec = spec
        self.t0 = float(spec["t"])
        self.dur = float(spec["dur"])
        self.kind = spec.get("kind", "card")
        self.acc = parse_color(accent)
        self.reader = None
        self.img = None
        if self.kind == "image":
            self.img = Image.open(spec["src"]).convert("RGB")

    def active(self, t):
        return self.t0 <= t < self.t0 + self.dur

    def apply(self, frame, t):
        lt = t - self.t0
        return getattr(self, "_" + self.kind)(frame, lt)

    def done(self):
        if self.reader:
            self.reader.close()
            self.reader = None

    # полноэкранный клип: влетает с лёгким зумом, уходит растворением
    def _video(self, frame, lt):
        if self.reader is None:
            self.reader = VideoReader(self.spec["src"], float(self.spec.get("src_start", 0)))
        clip = self.reader.read()
        if clip is None:
            return frame
        s = 1.10 - 0.10 * ease_out(lt / 0.25)
        if s > 1.001:
            cw, ch = W / s, H / s
            clip = clip.resize((W, H), Image.BILINEAR, box=((W - cw) / 2, (H - ch) / 2, (W + cw) / 2, (H + ch) / 2))
        a = min(ease_out(lt / 0.08), ease_out((self.dur - lt) / 0.12))
        return Image.blend(frame, clip, a) if a < 1 else clip

    # картинка/скриншот: карточкой на размытом фоне, медленный наезд
    def _image(self, frame, lt):
        bg = dim(frame.filter(ImageFilter.GaussianBlur(18)), 0.45)
        im = self.img
        maxw, maxh = 940, 1180
        sc = min(maxw / im.width, maxh / im.height)
        sc *= (0.9 + 0.1 * ease_back(lt / 0.3)) * (1 + 0.03 * lt / max(self.dur, 0.1))
        iw, ih = int(im.width * sc), int(im.height * sc)
        card = im.resize((iw, ih), Image.LANCZOS)
        mask = Image.new("L", (iw, ih), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, iw, ih], radius=28, fill=255)
        x, y = (W - iw) // 2, 820 - ih // 2
        sh = Image.new("L", (W, H), 0)
        ImageDraw.Draw(sh).rounded_rectangle([x, y + 24, x + iw, y + ih + 24], radius=28, fill=170)
        bg.paste((0, 0, 0), (0, 0), sh.filter(ImageFilter.GaussianBlur(26)))
        bg.paste(card, (x, y), mask)
        a = ease_out(lt / 0.12) * ease_out((self.dur - lt) / 0.12)
        return Image.blend(frame, bg, a) if a < 1 else bg

    # счётчик: цифра набегает до значения, спикер затемнён
    def _counter(self, frame, lt):
        out = dim(frame, 0.38)
        d = ImageDraw.Draw(out)
        val = float(self.spec.get("value", 0))
        dec = int(self.spec.get("decimals", 0))
        cur = val * ease_out(lt / 0.9)
        txt = f"{self.spec.get('prefix', '')}{fmt_num(cur, dec)}{self.spec.get('suffix', '')}"
        size = 210
        while size > 80 and font(size, "Black").getlength(txt) > W - 160:
            size -= 10
        f = font(size, "Black")
        s = 0.8 + 0.2 * ease_back(lt / 0.25)
        fs = font(max(40, int(size * s)), "Black")
        tw = fs.getlength(txt)
        d.text(((W - tw) / 2, 640), txt, font=fs, fill=self.acc)
        label = (self.spec.get("text") or "").upper()
        if label:
            lf = font(52, "Bold")
            for k, line in enumerate(_wrap(label, lf, W - 200)):
                lw = lf.getlength(line)
                d.text(((W - lw) / 2, 640 + size * 1.15 + k * 66), line, font=lf, fill=WHITE)
        a = min(ease_out(lt / 0.1), ease_out((self.dur - lt) / 0.14))
        return Image.blend(frame, out, a) if a < 1 else out

    # текстовая карточка: крупная мысль, *слово* — цветом
    def _card(self, frame, lt):
        out = dim(frame, 0.35)
        d = ImageDraw.Draw(out)
        raw = (self.spec.get("text") or "").upper()
        size = 92
        while size > 44 and max(font(size, "ExtraBold").getlength(w.replace("*", "")) for w in raw.split()) > W - 160:
            size -= 4
        f = font(size, "ExtraBold")
        lh = int(size * 1.2)
        lines = _wrap(raw, f, W - 160)
        y = 820 - len(lines) * lh // 2
        shown = ease_out(lt / 0.35)
        for k, line in enumerate(lines):
            la = min(1.0, max(0.0, shown * len(lines) - k))
            if la <= 0:
                continue
            x = (W - f.getlength(line.replace("*", ""))) / 2
            for part in re.split(r"(\*[^*]+\*)", line):
                if not part:
                    continue
                col = self.acc if part.startswith("*") else WHITE
                p = part.strip("*")
                d.text((x, y + (1 - la) * 20), p, font=f, fill=col)
                x += f.getlength(p)
            y += lh
        a = min(ease_out(lt / 0.1), ease_out((self.dur - lt) / 0.14))
        return Image.blend(frame, out, a) if a < 1 else out


def _wrap(text, f, maxw):
    lines, cur = [], ""
    for w in text.split():
        tt = (cur + " " + w).strip()
        if f.getlength(tt.replace("*", "")) <= maxw:
            cur = tt
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines
