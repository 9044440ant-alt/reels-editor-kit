"""Где лицо в кадре → куда ставить субтитры и куда целить зум (Apple Vision через Swift-бинарь)."""
import json, subprocess
import numpy as np
from .common import ASSETS, H, ROOT, jload, jsave

BIN = ASSETS / "bin" / "faces"
SRC = ROOT / "reels" / "faces.swift"
CAP_MIN, CAP_MAX = 1150, 1420     # центр субтитров: не выше груди, не ниже зоны подписи Instagram
DEFAULT = {"cap_y": 1250, "focus": [0.5, 0.40], "face_found": False}


def ensure_bin():
    if not BIN.exists() or BIN.stat().st_mtime < SRC.stat().st_mtime:
        BIN.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(SRC), "-o", str(BIN)], check=True, capture_output=True)
    return BIN


def detect(video, step=1.0):
    r = subprocess.run([str(ensure_bin()), str(video), str(step)], capture_output=True, text=True, timeout=300)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []


def placement(samples):
    faces = [s["face"] for s in samples if s.get("face")]
    if len(faces) < max(1, len(samples) // 4):
        return dict(DEFAULT)
    f = np.array(faces)
    chin = float(np.percentile(f[:, 1] + f[:, 3], 80)) * H
    # при зуме 1.12 вокруг лица подбородок уезжает вниз примерно на 12% расстояния от точки фокуса
    cy = float(np.median(f[:, 1] + f[:, 3] / 2))
    chin += max(0.0, chin - cy * H) * 0.12
    target = chin + 30 + 80
    cx = float(np.clip(np.median(f[:, 0] + f[:, 2] / 2), 0.3, 0.7))
    return {"cap_y": int(np.clip(target, CAP_MIN, CAP_MAX)), "focus": [round(cx, 3), round(float(np.clip(cy, 0.25, 0.55)), 3)],
            "face_found": True, "chin_px": int(chin), "cap_over_chin": target > CAP_MAX}


def analyze(wd):
    try:
        samples = detect(wd / "prep.mp4")
    except Exception as e:  # распознавание лица — улучшение, не условие: без него работаем по умолчанию
        samples, err = [], str(e)
    pl = placement(samples)
    jsave(wd / "faces.json", {"samples": samples, "placement": pl})
    return pl


def load(wd):
    p = wd / "faces.json"
    return jload(p)["placement"] if p.exists() else dict(DEFAULT)
