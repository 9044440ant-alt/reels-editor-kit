"""Этап 3: пословная расшифровка + карта речевой энергии.

words.json: [{"i", "w", "s", "e", "p", "voiced"}] — время в секундах исходника.
energy.json: {"hop": 0.01, "db": [...], "floor": dB, "thr": dB}
gaps.json: паузы между словами; где в паузе звучит голос без слов — кандидат «ээ/мм».
"""
import json, re, wave
import numpy as np
from .common import CONFIG, WHISPER_MODEL, ReelError, run, jsave

# Подсказка, чтобы whisper не «причёсывал» речь и писал паразитов как есть.
PROMPT = "Ээ, ну, мм, короче, как бы, типа, вот. Эм, в общем, это самое."
# Типовые галлюцинации whisper на тишине/музыке.
HALLUCINATIONS = re.compile(r"(редактор субтитров|корректор|субтитры (сделал|создавал)|продолжение следует|"
                            r"спасибо за просмотр|подписывайтесь на канал|dimatorzok)", re.I)
HOP = 0.01


def energy_map(wav_path):
    with wave.open(str(wav_path)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    n = int(sr * HOP)
    frames = x[: len(x) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6)
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 95))
    # порог голоса: выше пола на треть динамики, но не ниже floor+8
    thr = max(floor + 8, floor + (peak - floor) * 0.35)
    return db, floor, thr


def voiced_ratio(db, thr, s, e):
    a, b = int(s / HOP), max(int(s / HOP) + 1, int(e / HOP))
    seg = db[a:b]
    return float((seg > thr).mean()) if len(seg) else 0.0


def onset(v, t, back=0.3, fwd=0.35):  # не используется после перехода на align.py, оставлено для отладки
    """Начало слова по голосу: DTW опаздывает на 0.1-0.15 с. Идём назад, пока звучит; если тишина — вперёд до голоса."""
    i = min(max(int(t / HOP), 0), len(v) - 1)
    if v[i]:
        lim = max(0, i - int(back / HOP))
        # идём назад через короткие провалы громкости внутри слова (до 50 мс)
        while i > lim and v[max(0, i - 5):i].any():
            i -= 1
        return i * HOP
    lim = min(len(v) - 1, i + int(fwd / HOP))
    j = i
    while j < lim and not v[j]:
        j += 1
    return j * HOP if v[j] else None


def whisper_words(wav16, out_base):
    """Слова из токенов whisper с DTW-таймкодами (точнее обычных; DTW требует -nfa)."""
    if not WHISPER_MODEL.exists():
        raise ReelError(f"нет модели whisper: {WHISPER_MODEL}")
    # whisper недетерминирован: при повторном прогоне текст может чуть отличаться и индексы слов в edl.json
    # поедут. Поэтому готовую расшифровку переиспользуем; перераспознать = удалить whisper.json.
    import os
    if not os.path.exists(f"{out_base}.json"):
        run(["whisper-cli", "-m", WHISPER_MODEL, "-f", wav16, "-l", CONFIG["language"], "-ojf", "-nfa", "-dtw", "large.v3.turbo",
             "-of", out_base, "-np", "--prompt", PROMPT], capture_output=True)
    d = json.loads(open(f"{out_base}.json", encoding="utf-8").read())
    words = []
    for seg in d["transcription"]:
        for tok in seg.get("tokens", []):
            txt = tok["text"]
            if txt.startswith("[_") or not txt.strip():
                continue
            t_off = tok["offsets"]["from"] / 1000
            t_dtw = tok.get("t_dtw", -1)
            cands = [t_off] + ([t_dtw / 100] if t_dtw is not None and t_dtw >= 0 else [])
            if txt.startswith(" ") or not words:
                words.append({"w": txt.strip(), "t": cands[-1], "cands": cands, "ps": [tok["p"]]})
            elif re.fullmatch(r"[\W_]+", txt.strip()):
                words[-1]["w"] += txt.strip()
            else:
                words[-1]["w"] += txt
                words[-1]["ps"].append(tok["p"])
    for w in words:
        w["p"] = round(float(np.mean(w.pop("ps"))), 3)
    return words


def transcribe(wd):
    from .align import align, phrases
    from . import vosk_align
    db, floor, thr = energy_map(wd / "clean.wav")
    raw = [w for w in whisper_words(wd / "speech16k.wav", wd / "whisper") if re.search(r"\w", w["w"])]
    va = vosk_align.align(raw, wd / "speech16k.wav", db, thr)
    if va:  # точное время от Vosk
        words, stats = va
        ph = phrases(db > thr, db, thr)
        for w in words:
            w["phrase"] = next((j for j, (a, b) in enumerate(ph) if a - 0.05 <= w["s"] <= b + 0.05), -1)
        method = f"vosk ({stats['match_ratio']:.0%} слов совпало)"
    else:  # запасной путь: раскладка по паузам и темпу речи
        words, ph = align(raw, db, thr)
        method = "по громкости (Vosk недоступен)"
    for w in words:
        w["voiced"] = round(voiced_ratio(db, thr, w["s"], w["e"]), 2)
    # Галлюцинации whisper на тишине («Продолжение следует», «Субтитры сделал…»): слова, которые
    # не подтвердил Vosk и в которых нет голоса. Кейс T8.
    # Режем только с краёв: в середине «а», «в» бывают настоящими, просто тихими и короткими
    # (однажды фраза едва не потеряла два настоящих слова).
    if va and words:
        def junk(w):
            return not w.get("anchor") and w["voiced"] < 0.1
        dropped = []
        while words and junk(words[-1]):
            dropped.insert(0, words.pop()["w"])
        while words and junk(words[0]):
            dropped.append(words.pop(0)["w"])
        if dropped:
            print(f"выброшены выдуманные слова (нет голоса): {' '.join(dropped)}")
    if HALLUCINATIONS.search(" ".join(x["w"] for x in words)) and len(words) < 12:
        words = []
    for i, w in enumerate(words):
        w["i"] = i
    used = {w["phrase"] for w in words}
    # «голос без слов»: фраза, в которую не попало ни одного слова — обычно ээ/мм/вдох/смешок
    orphan = [{"s": a, "e": b} for j, (a, b) in enumerate(ph) if j not in used]
    gaps = []
    for a, b in zip(words, words[1:]):
        g = b["s"] - a["e"]
        if g >= 0.12:
            voice = sum(max(0.0, min(o["e"], b["s"]) - max(o["s"], a["e"])) for o in orphan)
            gaps.append({"after": a["i"], "s": a["e"], "e": b["s"], "dur": round(g, 2), "voice": round(voice, 2)})
    jsave(wd / "words.json", words)
    jsave(wd / "gaps.json", gaps)
    jsave(wd / "energy.json", {"hop": HOP, "floor": floor, "thr": thr, "db": [round(float(x), 1) for x in db]})
    return {"words": len(words), "raw_words": len(raw), "phrases": len(ph), "orphan_voice": len(orphan),
            "floor": floor, "thr": thr, "align": method}
