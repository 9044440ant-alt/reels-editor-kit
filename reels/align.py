"""Выравнивание слов по реальному звуку.

Whisper даёт два разных таймкода слова (обычный и DTW), и оба врут по-своему, иногда на секунду.
Поэтому: 1) режем звук на фразы по паузам (энергия — это факт), 2) раскладываем слова по фразам
динамикой (слово попадает во фразу, ближайшую к любому из его таймкодов, порядок сохраняется),
3) внутри фразы делим время пропорционально длине слов и подтягиваем границы к провалам громкости.
"""
import numpy as np

HOP = 0.01
MIN_PAUSE = 0.18     # пауза короче — не граница фразы
MIN_VOICE = 0.06     # голос короче — щелчок, не фраза


def phrases(v, db, thr):
    runs, i, n = [], 0, len(v)
    while i < n:
        if v[i]:
            j = i
            while j < n and v[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    merged = []
    for a, b in runs:
        if merged and (a - merged[-1][1]) * HOP < MIN_PAUSE:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a * HOP, b * HOP) for a, b in merged if (b - a) * HOP >= MIN_VOICE]


def dist(t, a, b):
    return 0.0 if a <= t <= b else min(abs(t - a), abs(t - b))


SEC_PER_LETTER = 0.065   # русская речь ~15 букв/с
SEC_PER_WORD = 0.05
RATE_W = 0.8             # вес штрафа за неправдоподобный темп
MAX_BLOCK = 60


def assign(words, ph):
    """Сегментационная DP: каждой фразе достаётся непрерывный блок слов (может быть пустым).
    Цена блока = близость таймкодов слов к фразе + правдоподобие темпа речи в этой фразе.
    Пустая фраза = «голос без слов» (ээ/мм/вдох): дёшево, если короткая, дорого, если длинная."""
    n, m = len(words), len(ph)
    INF = 1e9
    dcost = np.array([[min(dist(c, a, b) for c in w["cands"]) for (a, b) in ph] for w in words])
    pre = np.vstack([np.zeros(m), np.cumsum(dcost, axis=0)])          # pre[k][j] = сумма цен слов 0..k-1 во фразе j
    letters = np.concatenate([[0], np.cumsum([weight(w["w"]) - 1.5 for w in words])])
    dp = np.full((m + 1, n + 1), INF)
    back = np.zeros((m + 1, n + 1), dtype=int)
    dp[0][0] = 0.0
    for j in range(m):
        a, b = ph[j]
        dur = b - a
        empty = 0.15 + max(0.0, dur - 0.45) * 3
        for i in range(n + 1):
            if dp[j][i] >= INF:
                continue
            # пустая фраза
            if dp[j][i] + empty < dp[j + 1][i]:
                dp[j + 1][i] = dp[j][i] + empty
                back[j + 1][i] = i
            for k in range(i + 1, min(n, i + MAX_BLOCK) + 1):
                exp = (letters[k] - letters[i]) * SEC_PER_LETTER + (k - i) * SEC_PER_WORD
                rate = RATE_W * abs(np.log(max(dur, 0.05) / exp))
                c = dp[j][i] + (pre[k][j] - pre[i][j]) + rate
                if c < dp[j + 1][k]:
                    dp[j + 1][k] = c
                    back[j + 1][k] = i
    out = [0] * n
    k = n
    for j in range(m, 0, -1):
        i = back[j][k]
        for q in range(i, k):
            out[q] = j - 1
        k = i
    return out


def valley(db, t, win=0.12):
    """Самая тихая точка рядом с t — туда ставим границу слова."""
    a, b = max(0, int((t - win) / HOP)), min(len(db) - 1, int((t + win) / HOP))
    if b <= a:
        return t
    seg = np.asarray(db[a:b + 1])
    k = int(np.argmin(seg + np.abs(np.arange(len(seg)) - (len(seg) // 2)) * 0.15))  # при равенстве — ближе к t
    return (a + k) * HOP


def weight(w):
    letters = sum(ch.isalnum() for ch in w)
    return max(2, letters) + 1.5


def align(raw, db, thr):
    """raw: [{"w", "cands": [t1, t2]}] → [{"w", "s", "e", "phrase"}]"""
    v = np.asarray(db) > thr
    ph = phrases(v, db, thr)
    if not ph or not raw:
        return [], ph
    idx = assign(raw, ph)
    out = []
    for j, (a, b) in enumerate(ph):
        ws = [k for k, pj in enumerate(idx) if pj == j]
        if not ws:
            continue
        wts = [weight(raw[k]["w"]) for k in ws]
        tot = sum(wts)
        t = a
        bounds = [a]
        for wt in wts[:-1]:
            t += (b - a) * wt / tot
            bounds.append(t)
        bounds.append(b)
        # внутренние границы — к ближайшему провалу громкости
        for q in range(1, len(bounds) - 1):
            bounds[q] = min(max(valley(db, bounds[q]), bounds[q - 1] + 0.06), b - 0.06 * (len(bounds) - 1 - q))
        for q, k in enumerate(ws):
            out.append({"w": raw[k]["w"], "p": raw[k]["p"], "s": round(bounds[q], 3), "e": round(bounds[q + 1], 3),
                        "phrase": j})
    return out, ph
