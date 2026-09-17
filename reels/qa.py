"""Этап 7: проверка итога — раскадровка, громкость, длительность, чёрные кадры."""
import re
from .common import ffmpeg, run, video_info


def contact_sheet(video, out, every=1.0, cols=6):
    info = video_info(video)
    n = max(1, int(info["duration"] / every))
    rows = (n + cols - 1) // cols
    ffmpeg("-i", video, "-vf", f"fps=1/{every},scale=216:384,tile={cols}x{rows}:padding=4:color=white",
           "-frames:v", "1", out)
    return out


def loudness(video):
    r = run(["ffmpeg", "-hide_banner", "-i", video, "-af", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    return {"lufs": float(i.group(1)) if i else None, "true_peak": float(tp.group(1)) if tp else None}


def black_frames(video):
    r = run(["ffmpeg", "-hide_banner", "-i", video, "-vf", "blackdetect=d=0.2:pix_th=0.08", "-an", "-f", "null", "-"],
            capture_output=True, text=True)
    return re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)


def dirty_cuts(wd):
    """Склейки, попавшие на звучащий голос (разрез посреди слова = слышимый щелчок/обрубок)."""
    from .common import jload
    en, tl = jload(wd / "energy.json"), jload(wd / "timeline.json")
    db, thr, hop = en["db"], en["thr"], en["hop"]
    bad = []
    for s in tl["segments"]:
        for edge, t in (("начало", s["src_s"]), ("конец", s["src_e"])):
            i = int(t / hop)
            here = min(db[max(0, i - 1):i + 2] or [thr])
            local_min = min(db[max(0, i - 6):i + 7] or [thr])
            # грязная = громко И не в провале между слогами (провал в беглой речи — нормальная точка склейки)
            if here > thr and here > local_min + 6:
                bad.append({"t_src": t, "edge": edge, "db": round(here, 1), "valley_db": round(local_min, 1)})
    return bad


def check(video, wd):
    info = video_info(video)
    res = {"duration": round(info["duration"], 2), "size_mb": round(info["size"] / 1e6, 1),
           "w": info["w"], "h": info["h"], **loudness(video), "black": black_frames(video)}
    res["sheet"] = str(contact_sheet(video, wd / "qa_sheet.jpg"))
    problems = []
    if (res["w"], res["h"]) != (1080, 1920):
        problems.append(f"размер {res['w']}x{res['h']}, нужен 1080x1920")
    # громкость больше не выравниваем (кейс A1) — цифра только для сведения
    if res["true_peak"] is not None and res["true_peak"] > 0:
        problems.append(f"пик {res['true_peak']} dBFS — клиппинг был уже в оригинале")
    if res["black"]:
        problems.append(f"чёрные кадры: {res['black']}")
    res["dirty_cuts"] = dirty_cuts(wd)
    if res["dirty_cuts"]:
        problems.append(f"склеек по живому голосу: {len(res['dirty_cuts'])} — проверить на слух")
    res["problems"] = problems
    return res
