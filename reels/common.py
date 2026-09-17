"""Общие вещи: пути, запуск ffmpeg, ffprobe, рабочая папка ролика."""
import json, os, pathlib, shutil, subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
CONFIG_DIR = pathlib.Path(os.path.expanduser("~/.config/reels-editor"))

# Настройки по умолчанию; переопределяются в ~/.config/reels-editor/config.json
DEFAULTS = {
    "work_dir": "~/Movies/Монтаж",
    "whisper_model": "~/.cache/whisper-cpp/ggml-large-v3-turbo.bin",
    "vosk_model": "~/.cache/vosk/vosk-model-small-ru-0.22",
    "language": "ru",
    "style": "C",            # A | B | C — стиль субтитров
    "accent": "lime",        # lime | yellow | "#RRGGBB"
    "size_k": 0.9,           # масштаб субтитров
    "grade": {"preset": "cine", "k": 1.0},
    "telegram": False,       # отправка готового ролика себе в Telegram «Избранное»
}


def load_config():
    cfg = dict(DEFAULTS)
    p = CONFIG_DIR / "config.json"
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg


CONFIG = load_config()
WORK_ROOT = pathlib.Path(os.path.expanduser(os.getenv("REELS_WORK_DIR", CONFIG["work_dir"])))
WHISPER_MODEL = pathlib.Path(os.path.expanduser(CONFIG["whisper_model"]))

W, H, FPS = 1080, 1920, 30


class ReelError(Exception):
    """Понятная ошибка для человека: печатается без трейсбека."""


def run(cmd, **kw):
    kw.setdefault("check", True)
    try:
        return subprocess.run([str(c) for c in cmd], **kw)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"")[-2000:]
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        raise ReelError(f"{cmd[0]} упал (код {e.returncode}): {err}") from None


def ffmpeg(*args, **kw):
    kw.setdefault("capture_output", True)
    return run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], **kw)


def probe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
              capture_output=True).stdout
    return json.loads(out)


def video_info(path):
    d = probe(path)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    if not v:
        raise ReelError(f"в файле нет видеодорожки: {path}")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rot = int(sd["rotation"])
    if abs(rot) in (90, 270):
        w, h = h, w
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    r_num, r_den = (v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    rfps = float(r_num) / float(r_den or 1) if float(r_den or 1) else fps
    return {
        "w": w, "h": h, "fps": fps, "vfr": abs(fps - rfps) > 0.5,
        "duration": float(d["format"].get("duration", 0)),
        "hdr": v.get("color_transfer") in ("arib-std-b67", "smpte2084"),
        "transfer": v.get("color_transfer"), "codec": v.get("codec_name"),
        "has_audio": a is not None, "size": int(d["format"].get("size", 0)),
    }


def check_disk(path, need_bytes):
    free = shutil.disk_usage(path).free
    if free < need_bytes:
        raise ReelError(f"мало места на диске: свободно {free/1e9:.1f} ГБ, нужно ~{need_bytes/1e9:.1f} ГБ. "
                        f"Освободи место и запусти снова.")


def workdir(name):
    d = WORK_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def jload(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def jsave(p, obj):
    pathlib.Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
