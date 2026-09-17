"""Точное время слов: Vosk (Kaldi) даёт таймкоды ±30 мс, whisper — лучший текст. Склеиваем.

1. Vosk распознаёт речь свободно (без словаря — со словарём время слов съезжало).
2. Слова двух расшифровок сопоставляются нечётко (Нидлман-Вунш по сходству букв: «настолько»~«насколько»).
3. Совпавшие слова whisper получают время Vosk. Несовпавшие («30%» против «тридцать процентов»)
   раскладываются между соседями-якорями пропорционально длине, границы — к провалам громкости.
"""
import json, os, re, wave
from difflib import SequenceMatcher
from .align import valley, weight

from .common import CONFIG
MODEL_DIR = os.path.expanduser(CONFIG["vosk_model"])


def available():
    if not os.path.isdir(MODEL_DIR):
        return False
    try:
        import vosk  # noqa: F401
        return True
    except Exception:
        return False


def norm(w):
    return re.sub(r"[^а-яa-z0-9]", "", w.lower().replace("ё", "е"))


def vosk_words(wav16, vocab=None):
    import vosk
    vosk.SetLogLevel(-1)
    model = vosk.Model(MODEL_DIR)
    with wave.open(str(wav16)) as wf:
        rate = wf.getframerate()
        rec = vosk.KaldiRecognizer(model, rate, json.dumps(vocab, ensure_ascii=False)) if vocab \
            else vosk.KaldiRecognizer(model, rate)
        rec.SetWords(True)
        out = []
        while True:
            data = wf.readframes(8000)
            if not data:
                break
            if rec.AcceptWaveform(data):
                out += json.loads(rec.Result()).get("result", [])
        out += json.loads(rec.FinalResult()).get("result", [])
    return [{"w": r["word"], "s": r["start"], "e": r["end"], "conf": r.get("conf", 1)} for r in out if r["word"] != "[unk]"]


def _runs(v, a, b, hop=0.01):
    """Отрезки звучания внутри [a, b]: провалы короче 80 мс склеиваются, щелчки короче 50 мс выкидываются."""
    runs, f, f1 = [], int(a / hop), int(b / hop)
    while f < f1:
        if 0 <= f < len(v) and v[f]:
            g = f
            while g < f1 and g < len(v) and v[g]:
                g += 1
            if runs and (f - runs[-1][1]) * hop < 0.08:
                runs[-1][1] = g
            else:
                runs.append([f, g])
            f = g
        else:
            f += 1
    return [(x * hop, y * hop) for x, y in runs if (y - x) * hop >= 0.05]


def _spread(ws, a, b, db, v):
    """Неопознанные Vosk слова ставятся только на звучащие отрезки между соседями.
    Отрезков не меньше, чем слов → каждому слову свой отрезок (берём самые длинные, лишние — это «ээ»/вдох).
    Отрезков меньше → слова делят звучащие кадры пропорционально длине."""
    wts = [weight(w["w"]) for w in ws]
    runs = _runs(v, a, b) if v is not None else []
    if runs and len(runs) >= len(ws):
        pick = sorted(sorted(range(len(runs)), key=lambda r: runs[r][1] - runs[r][0], reverse=True)[:len(ws)])
        return [{**w, "s": round(runs[r][0], 3), "e": round(runs[r][1], 3), "anchor": False} for w, r in zip(ws, pick)]
    hop = 0.01
    frames = [f for (x, y) in runs for f in range(int(x / hop), int(y / hop))]
    if len(frames) < 3 * len(ws):  # голоса почти нет — равномерно по всему отрезку
        frames = list(range(int(a / hop), max(int(b / hop), int(a / hop) + 3 * len(ws))))
    tot, cum, out = sum(wts), 0.0, []
    for q, w in enumerate(ws):
        f0 = frames[min(len(frames) - 1, int(cum / tot * len(frames)))]
        cum += wts[q]
        f1 = frames[min(len(frames) - 1, int(cum / tot * len(frames)) - 1)] + 1
        # слово не перепрыгивает паузу: конец — не дальше конца его отрезка звучания
        run_end = next((y for (x, y) in runs if x - 1e-6 <= f0 * hop < y), f1 * hop)
        out.append({**w, "s": round(f0 * hop, 3), "e": round(max(min(f1 * hop, run_end), f0 * hop + 0.05), 3),
                    "anchor": False})
    return out


def nw_pairs(a, b, min_sim=0.5, gap=-0.35):
    """Глобальное выравнивание двух списков слов (Нидлман-Вунш) с нечётким сходством:
    «настолько»~«насколько», «копят»~«купит», «как»~«так». Возвращает [(i, j)] с порядком."""
    n, m = len(a), len(b)
    NEG = -1e9
    sim = [[SequenceMatcher(None, x, y).ratio() if x and y else 0.0 for y in b] for x in a]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    bt = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0], bt[i][0] = dp[i - 1][0] + gap, 1
    for j in range(1, m + 1):
        dp[0][j], bt[0][j] = dp[0][j - 1] + gap, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sc = sim[i - 1][j - 1]
            diag = dp[i - 1][j - 1] + (2 * sc - 0.6 if sc >= min_sim else NEG)
            up, left = dp[i - 1][j] + gap, dp[i][j - 1] + gap
            best = max(diag, up, left)
            dp[i][j] = best
            bt[i][j] = 0 if best == diag else (1 if best == up else 2)
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        if bt[i][j] == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif bt[i][j] == 1:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def align(raw, wav16, db, thr=None):
    """raw: слова whisper [{"w", "p"}] → [{"w", "p", "s", "e", "anchor"}]; None, если Vosk недоступен."""
    if not available() or not raw:
        return None
    # Свободное распознавание, без словаря: со словарём из слов whisper Vosk ставил слова не туда
    # (слово съезжало на ~2 с) — время у собственных слов Vosk надёжнее.
    vw = vosk_words(wav16)
    if not vw:
        return None
    a = [norm(w["w"]) for w in raw]
    b = [norm(w["w"]) for w in vw]
    t = [None] * len(raw)
    for i, j in nw_pairs(a, b):
        t[i] = (vw[j]["s"], vw[j]["e"])
    out = []
    n = len(raw)
    v = (db > thr) if thr is not None else None
    i = 0
    while i < n:
        if t[i]:
            s, e = t[i]
            out.append({**raw[i], "s": s, "e": e, "anchor": True})
            i += 1
            continue
        j = i
        while j < n and not t[j]:
            j += 1
        lo = out[-1]["e"] if out else max(0.0, (t[j][0] if j < n else 0.0) - 0.6)
        hi = t[j][0] if j < n else lo + 0.35 * (j - i) + 0.3
        inside = [x for x in vw if x["s"] >= lo - 0.01 and x["e"] <= hi + 0.01]
        # 1) похожие слова («как» ~ «так», «копят» ~ «купит») получают время Vosk — по порядку
        fixed, p = {}, 0
        for k in range(i, j):
            for q in range(p, len(inside)):
                if SequenceMatcher(None, norm(raw[k]["w"]), norm(inside[q]["w"])).ratio() >= 0.5:
                    fixed[k] = (inside[q]["s"], inside[q]["e"])
                    p = q + 1
                    break
        # 2) остальные — только на звучащие участки между соседями, пропорционально длине слова
        k = i
        while k < j:
            if k in fixed:
                s, e = fixed[k]
                out.append({**raw[k], "s": s, "e": e, "anchor": True})
                k += 1
                continue
            m = k
            while m < j and m not in fixed:
                m += 1
            a = out[-1]["e"] if out else lo
            b = fixed[m][0] if m < j else hi
            out += _spread(raw[k:m], a, b, db, v)
            k = m
        i = j
    # страховка: время не убывает
    for k in range(1, len(out)):
        if out[k]["s"] < out[k - 1]["s"] + 0.02:
            out[k]["s"] = out[k - 1]["s"] + 0.02
        if out[k]["e"] < out[k]["s"] + 0.04:
            out[k]["e"] = out[k]["s"] + 0.04
    matched = sum(1 for x in t if x)
    return out, {"vosk_words": len(vw), "matched": matched, "match_ratio": round(matched / n, 2)}
