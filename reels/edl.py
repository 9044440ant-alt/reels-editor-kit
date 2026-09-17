"""Этап 4: план монтажа (edl.json) → таймлайн (timeline.json).

edl.json пишу я (Claude) по docs/EDIT_RULES.md, опираясь на view(). Формат:
{
  "drop": [[i, j], ...],        # диапазоны слов (включительно), которые вырезаем: паразиты, дубли, мусор
  "hook_move": [i, j] | null,   # фраза, которую ставим в самое начало
  "hook_title": "ТЕКСТ" | null, # плашка-заголовок сверху
  "hook_hold": 3.5,             # сколько секунд держать плашку (0 = весь ролик)
  "emphasis": [i, ...],         # слова-акценты (цвет + щелчок)
  "inserts": [{"word": i, "dur": 2.0, "kind": "video|image|counter|card", "src": "...", "text": "...", "value": 0}],
  "fix": {"48": "копят"},       # правка текста слова в субтитрах (ошибки распознавания)
  "tail": 0,                    # секунд после последнего слова (финальная вставка держится, голос молчит)
  "sfx": false,                 # звуковые эффекты (по умолчанию выключены: звук не трогаем)
  "grade": {"preset": "cine", "k": 1.0},   # цветокоррекция: soft | cine | strong | none, k — сила 0..1
  "style": "C",
  "max_pause": 0.3, "pad_in": 0.06, "pad_out": 0.09
}
"""
import re
from .common import CONFIG, FPS, ReelError, jload, jsave

FILLER_TOKENS = {"э", "ээ", "эээ", "эм", "эмм", "мм", "ммм", "м", "а-а", "э-э", "хм"}
FILLER_WORDS = {"ну", "вот", "короче", "типа", "как бы", "в общем", "это самое", "так сказать", "значит", "собственно"}
NUM_RE = re.compile(r"\d|%|\$|€|₽|тыс|млн|млрд|миллион|тысяч|процент")

# Режим по умолчанию — минимальная обрезка (кейс A2):
# режем только паузы и растянутые «ааа/эээ», содержание не трогаем.
DEFAULTS = {"drop": [], "hook_move": None, "hook_title": None, "hook_hold": 3.5, "emphasis": [],
            "inserts": [], "fix": {}, "tail": 0, "sfx": False, "style": CONFIG["style"],
            "grade": dict(CONFIG["grade"]),
            "max_pause": 0.3, "pad_in": 0.06, "pad_out": 0.09, "min_orphan_voice": 0.12}


def norm(w):
    return re.sub(r"[^\wа-яё-]", "", w.lower())


def auto_edl(words):
    """Черновик плана = то, что и нужно по умолчанию: чистые «ээ/мм» вон, остальное остаётся.
    Вырезать содержание (повторы, вступление, «ну/вот/короче») и переставлять фразы — только если
    автор попросил про конкретный ролик."""
    drop = [[w["i"], w["i"]] for w in words if norm(w["w"]) in FILLER_TOKENS]
    emph = [w["i"] for w in words if NUM_RE.search(w["w"].lower())]
    return {**DEFAULTS, "drop": drop, "emphasis": emph}


def view(words, gaps):
    """Текст для чтения: [индекс]слово, паузы и «голос без слов» помечены."""
    gap_after = {g["after"]: g for g in gaps}
    out, line = [], []
    for w in words:
        tag = ""
        n = norm(w["w"])
        if n in FILLER_TOKENS or n in FILLER_WORDS:
            tag = "°"
        if w["p"] < 0.5:
            tag += "?"
        line.append(f"[{w['i']}]{w['w']}{tag}")
        g = gap_after.get(w["i"])
        if g:
            mark = f"  ⏸{g['dur']}с" + (f" 🗣{g['voice']}с" if g["voice"] >= 0.15 else "")
            line.append(mark)
            if g["dur"] >= 0.6:
                out.append(f"{w['e']:6.2f} | " + " ".join(line))
                line = []
    if line:
        out.append(f"{words[-1]['e']:6.2f} | " + " ".join(line))
    return "\n".join(out) + "\n\n° паразит  ? неуверенно распознано  ⏸ пауза  🗣 голос без слов (ээ/мм)"


def _dropped(edl, n):
    d = set()
    for a, b in edl.get("drop", []):
        if a > b or a < 0 or b >= n:
            raise ReelError(f"edl.drop: неверный диапазон [{a}, {b}] (слов {n})")
        d.update(range(a, b + 1))
    return d


def cut_point(db, thr, hop, a, b, prefer):
    """Точка склейки в окне [a, b]. Если prefer уже в тишине — оставляем. Если громко — ближайший тихий кадр,
    а если тишины в окне нет — самый тихий кадр (провал между слогами в беглой речи)."""
    if db is None or b <= a:
        return prefer
    i0, i1 = max(0, int(a / hop)), min(len(db) - 1, int(b / hop))
    if i1 <= i0:
        return prefer
    ip = min(max(int(prefer / hop), 0), len(db) - 1)
    if db[ip] < thr:
        return prefer
    quiet = [q for q in range(i0, i1 + 1) if db[q] < thr]
    if quiet:
        return min(quiet, key=lambda q: abs(q - ip)) * hop
    return min(range(i0, i1 + 1), key=lambda q: db[q]) * hop


def compile_timeline(edl, words, src_duration, energy=None, gaps=None):
    e = {**DEFAULTS, **edl}
    n = len(words)
    if not n:
        raise ReelError("в видео не распознано ни одного слова")
    dropped = _dropped(e, n)
    order = [w["i"] for w in words if w["i"] not in dropped]
    if e["hook_move"]:
        a, b = e["hook_move"]
        hook = [i for i in order if a <= i <= b]
        order = hook + [i for i in order if not (a <= i <= b)]
    if not order:
        raise ReelError("после вырезок не осталось ни одного слова")

    # растянутые «ааа/эээ» между словами: голос есть, слов нет — рвём на них сегмент и выкидываем
    orphan_after = {g["after"] for g in (gaps or []) if g.get("voice", 0) >= e["min_orphan_voice"]}
    # группируем в сегменты: подряд идущие слова без большой паузы
    groups, cur = [], [order[0]]
    for prev, i in zip(order, order[1:]):
        if i == prev + 1 and words[i]["s"] - words[prev]["e"] <= e["max_pause"] and prev not in orphan_after:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)

    segs, t_out, words_out = [], 0.0, []
    emph = set(e["emphasis"])
    fix = {int(k): v for k, v in e["fix"].items()}
    for g in groups:
        first, last = words[g[0]], words[g[-1]]
        lo = words[g[0] - 1]["e"] + 0.02 if g[0] > 0 else 0.0
        hi = words[g[-1] + 1]["s"] - 0.02 if g[-1] + 1 < n else src_duration
        s = max(lo, first["s"] - e["pad_in"], 0.0)
        en = min(hi, last["e"] + e["pad_out"], src_duration)
        if energy:  # склейка по живому голосу → сдвигаем в самый тихий кадр рядом с границей слова
            db, thr, hop = energy["db"], energy["thr"], energy["hop"]
            # окно поиска тихой точки: ±80 мс вокруг границы слова, чуть заходя за соседа (там обычно и есть провал)
            s = cut_point(db, thr, hop, max(lo - 0.08, first["s"] - 0.12), first["s"] + 0.05, s)
            en = cut_point(db, thr, hop, last["e"] - 0.08, min(hi + 0.08, last["e"] + 0.12), en)
        # границы по сетке кадров: иначе за 40 склеек звук уезжает от картинки.
        # Из двух соседних кадров берём тот, где тише, чтобы привязка не вернула склейку на звук.
        def snap(t):
            import math
            c = [math.floor(t * FPS) / FPS, math.ceil(t * FPS) / FPS]
            if not energy:
                return round(t * FPS) / FPS
            dbv, hp = energy["db"], energy["hop"]
            return min(c, key=lambda x: (dbv[min(len(dbv) - 1, int(x / hp))], abs(x - t)))
        s, en = max(0.0, snap(s)), min(snap(en), src_duration)
        if en - s < 0.12:
            continue
        for i in g:
            w = words[i]
            words_out.append({"i": i, "w": fix.get(i, w["w"]), "s": round(t_out + w["s"] - s, 3),
                              "e": round(t_out + w["e"] - s, 3), "emph": i in emph})
        segs.append({"src_s": round(s, 3), "src_e": round(en, 3), "out_s": round(t_out, 3),
                     "words": [g[0], g[-1]]})
        t_out += en - s

    speech_end = t_out
    t_out += float(e["tail"])
    # вставки: привязка к слову → время на выходе
    by_i = {w["i"]: w for w in words_out}
    inserts = []
    for ins in e["inserts"]:
        w = by_i.get(ins.get("word"))
        if w is None:
            continue  # слово вырезано — вставку пропускаем, ролик не падаем
        inserts.append({**ins, "t": w["s"], "dur": min(float(ins.get("dur", 2.0)), t_out - w["s"])})
    return {"segments": segs, "words": words_out, "duration": round(t_out, 3), "speech_end": round(speech_end, 3),
            "inserts": inserts, "sfx": bool(e["sfx"]), "grade": e["grade"],
            "hook_title": e["hook_title"], "hook_hold": e["hook_hold"], "style": e["style"]}


def build(wd):
    words, info = jload(wd / "words.json"), jload(wd / "info.json")
    edl_p = wd / "edl.json"
    if not edl_p.exists():
        jsave(edl_p, auto_edl(words))
    en_p = wd / "energy.json"
    tl = compile_timeline(jload(edl_p), words, info["duration"], jload(en_p) if en_p.exists() else None)
    jsave(wd / "timeline.json", tl)
    return tl
