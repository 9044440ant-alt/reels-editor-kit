"""Этап 6: рендер. Кадры собираются в Python (зум, вставки, субтитры, плашка), звук режется в numpy.

Звук: clean.wav → нарезка по сегментам с микро-фейдами → SFX → loudnorm −14 LUFS → final.wav
Видео: prep.mp4 → кадры сегментов (ffmpeg-ридер на сегмент) → композит → libx264
"""
import json, subprocess, wave
import numpy as np
from PIL import Image
from .common import W, H, FPS, ReelError, ffmpeg, jload, run
from .captions import Captioner, HookTitle
from .graphics import Insert, VideoReader
from . import sfx, faces, grade as grademod

SR = 48000
FADE = 0.012
ZOOM_IN = 1.12          # «панч» на склейке
PUSH = 0.025            # медленный наезд внутри сегмента
FOCUS = (0.5, 0.40)     # точка зума: лицо в вертикальном селфи обычно на 35-45% высоты
MIN_ZOOM_GAP = 1.2      # не дёргать зум чаще, чем раз в 1.2 с


def read_wav(p):
    """Возвращает (сэмплы [n, каналы], частота)."""
    with wave.open(str(p)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        ch = w.getnchannels()
        return x.reshape(-1, ch) if ch > 1 else x.reshape(-1, 1), w.getframerate()


def write_wav(p, x, sr=SR):
    x = np.clip(np.atleast_2d(x.T).T if x.ndim > 1 else x.reshape(-1, 1), -1, 1)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(x.shape[1]); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).ravel().tobytes())


def zoom_plan(segs):
    plan, level, last = [], 1.0, -9.0
    for k, s in enumerate(segs):
        if k > 0 and s["out_s"] - last >= MIN_ZOOM_GAP:
            level = ZOOM_IN if level == 1.0 else 1.0
            last = s["out_s"]
        plan.append(level)
    return plan


def build_audio(wd, tl):
    """Оригинальный звук, нарезанный по склейкам. Никакой обработки: ни шумодава, ни компрессии,
    ни выравнивания громкости (кейс A1). Только фейд 12 мс на стыках,
    иначе на склейке слышен щелчок. Эффекты (вжух/щелчок) добавляются, только если включены в edl."""
    src = wd / "raw.wav" if (wd / "raw.wav").exists() else wd / "clean.wav"
    x, sr = read_wav(src)
    fade = np.linspace(0, 1, int(sr * FADE), dtype=np.float32)[:, None]
    parts = []
    for s in tl["segments"]:
        a = x[int(round(s["src_s"] * sr)):int(round(s["src_e"] * sr))].copy()
        n = min(len(fade), len(a) // 2)
        if n:
            a[:n] *= fade[:n]
            a[-n:] *= fade[:n][::-1]
        parts.append(a)
    voice = np.concatenate(parts) if parts else np.zeros((0, x.shape[1]), dtype=np.float32)
    total = int(round(tl["duration"] * sr))
    if len(voice) < total:
        voice = np.vstack([voice, np.zeros((total - len(voice), voice.shape[1]), dtype=np.float32)])
    voice = voice[:total]

    if tl.get("sfx"):
        fx = sfx.ensure()
        wh, _ = read_wav(fx["whoosh"])
        pp, _ = read_wav(fx["pop"])

        def put(clip, t, gain):
            i = int(t * sr)
            if 0 <= i < len(voice):
                seg = clip[: len(voice) - i]
                voice[i:i + len(seg)] += seg * gain
        for ins in tl["inserts"]:
            put(wh, max(0.0, ins["t"] - 0.22), 10 ** (-17 / 20))
        last_pop = -9
        for w in tl["words"]:
            if w.get("emph") and w["s"] - last_pop >= 2.0:
                put(pp, w["s"], 10 ** (-23 / 20))
                last_pop = w["s"]
    out = wd / "final.wav"
    write_wav(out, voice, sr)
    return out


def zoom(img, z, focus=FOCUS):
    if abs(z - 1.0) < 0.002:
        return img
    cw, ch = W / z, H / z
    cx = min(max(focus[0] * W, cw / 2), W - cw / 2)
    cy = min(max(focus[1] * H, ch / 2), H - ch / 2)
    return img.resize((W, H), Image.BILINEAR, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))


def render(wd, out_name="final.mp4", style=None, accent="lime", limit=None, audio=True):
    tl = jload(wd / "timeline.json")
    style = style or tl.get("style", "C")
    g = tl.get("grade") or {}
    grade_vf = grademod.vf(g.get("preset", "cine"), float(g.get("k", 1.0)))
    dur = tl["duration"] if not limit else min(limit, tl["duration"])
    audio_p = build_audio(wd, tl) if audio else None
    pl = faces.load(wd)
    cap = Captioner(tl["words"], style, accent, cap_y=pl["cap_y"])
    hook = HookTitle(tl.get("hook_title"), tl.get("hook_hold", 3.5), accent, tl["duration"])
    inserts = [Insert(i, accent) for i in tl["inserts"]]
    zplan = zoom_plan(tl["segments"])

    out = wd / out_name
    enc = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if audio_p:
        enc += ["-i", str(audio_p), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "256k", "-shortest"]
    enc += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-maxrate", "16M", "-bufsize", "32M",
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-movflags", "+faststart", str(out)]
    writer = subprocess.Popen(enc, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    n_total = int(round(dur * FPS))
    n = 0
    state = {"last": None}

    def emit(fr, z, focus):
        nonlocal n
        t = n / FPS
        img = zoom(fr, z, focus)
        text_card = False
        for ins in inserts:
            if ins.active(t):
                img = ins.apply(img, t)
                text_card = text_card or ins.kind in ("card", "counter")
            elif t >= ins.t0 + ins.dur:
                ins.done()
        # на текстовой карточке и счётчике субтитры прячем: два текста одновременно спорят за глаз
        c = None if text_card else cap.render(t)
        if c:
            band, y0 = c
            img.paste(band.convert("RGB"), (0, y0), band)
        hk = hook.render(t)
        if hk:
            him, hx, hy = hk
            img.paste(him.convert("RGB"), (hx, hy), him)
        writer.stdin.write(img.tobytes())
        n += 1

    try:
        z_last = 1.0
        for k, seg in enumerate(tl["segments"]):
            nf = int(round((seg["src_e"] - seg["src_s"]) * FPS))
            rd = VideoReader(wd / "prep.mp4", seg["src_s"], nf, grade=grade_vf)
            z0 = zplan[k]
            for j in range(nf):
                if n >= n_total:
                    break
                fr = rd.read()
                if fr is None:
                    raise ReelError(f"не прочитался кадр сегмента {k}")
                state["last"] = fr
                z_last = z0 + PUSH * j / max(nf, 1)
                emit(fr, z_last, pl["focus"])
            rd.close()
            if n >= n_total:
                break
        # хвост после последнего слова: стоп-кадр спикера под финальной вставкой
        while n < n_total and state["last"] is not None:
            emit(state["last"], z_last, pl["focus"])
        writer.stdin.close()
        err = writer.stderr.read().decode("utf-8", "replace")
        if writer.wait() != 0:
            raise ReelError(f"кодирование упало: {err[-800:]}")
    finally:
        for ins in inserts:
            ins.done()
        if writer.poll() is None:
            writer.kill()
    return out, n
