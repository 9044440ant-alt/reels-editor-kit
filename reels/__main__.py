"""CLI монтажёра.

  python3 -m reels doctor                      # проверить, что всё установлено
  python3 -m reels new <видео> --name <слаг>   # приём + расшифровка + черновой план, печатает текст
  python3 -m reels view <папка>                # текст с индексами слов для плана монтажа
  python3 -m reels render <папка> [--style C] [--accent lime] [--limit 8] [--out final.mp4]
  python3 -m reels styles <папка> [--sec 8]    # три стиля субтитров на живом кадре
  python3 -m reels cover <папка> --headline "СТРОКА|СТРОКА" --box "ПЛАШКА" [--at 5]
  python3 -m reels clean <папка>               # удалить промежуточные файлы
  # только если включён Telegram (config.json → "telegram": true):
  python3 -m reels tg-login                    # один раз: вход в свой Telegram
  python3 -m reels fetch [--list] [--id N]     # видео из «Избранного» → <work_dir>/inbox
  python3 -m reels deliver <папка>             # обложки и ролик себе в «Избранное»
"""
import argparse, datetime, json, pathlib, subprocess, sys
from .common import CONFIG, CONFIG_DIR, ROOT, WHISPER_MODEL, WORK_ROOT, ReelError, jload, jsave
from . import edl as edlm


def wd_of(x):
    p = pathlib.Path(x).expanduser()
    if not p.is_absolute() and not p.exists():
        p = WORK_ROOT / x
    if not p.exists():
        raise ReelError(f"нет рабочей папки {p}")
    return p


def need_telegram():
    if not CONFIG.get("telegram"):
        raise ReelError("Telegram выключен: в ~/.config/reels-editor/config.json поставь \"telegram\": true")


def cmd_fetch(a):
    need_telegram()
    args = [sys.executable, "-m", "reels.tg_fetch", "--out", str(WORK_ROOT / "inbox")]
    if a.list:
        args.append("--list")
    if a.id:
        args += ["--id", str(a.id)]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip()[-500:])
    return r.returncode


def cmd_new(a):
    from .ingest import ingest
    from .transcribe import transcribe
    src = pathlib.Path(a.src).expanduser()
    if not src.exists():
        raise ReelError(f"нет файла {src}")
    name = a.name or f"{datetime.date.today():%Y-%m-%d}_{src.stem[:24]}"
    wd = WORK_ROOT / name
    wd.mkdir(parents=True, exist_ok=True)
    info = ingest(src, wd)
    print(f"приём: {info['w']}x{info['h']} {info['fps']:.2f}fps {info['duration']:.1f}с hdr={info['hdr']} → {info['video_path']}")
    tr = transcribe(wd)
    print(f"расшифровка: слов {tr['words']}, выравнивание {tr['align']}, «голос без слов» {tr['orphan_voice']}")
    tl = edlm.build(wd)
    print(f"черновик: {len(tl['segments'])} сегментов, {tl['duration']:.1f}с из {info['duration']:.1f}с")
    print(f"папка: {wd}\n")
    print(edlm.view(jload(wd / "words.json"), jload(wd / "gaps.json")))


def cmd_view(a):
    wd = wd_of(a.wd)
    print(edlm.view(jload(wd / "words.json"), jload(wd / "gaps.json")))


def cmd_render(a):
    from .render import render
    from .qa import check
    wd = wd_of(a.wd)
    tl = edlm.build(wd)
    out, n = render(wd, a.out, a.style, a.accent, a.limit)
    res = check(out, wd)
    print(json.dumps({"out": str(out), "frames": n, "timeline_s": tl["duration"], **res}, ensure_ascii=False, indent=1))


def cmd_styles(a):
    from .render import render
    from .common import ffmpeg
    wd = wd_of(a.wd)
    edlm.build(wd)
    outs = []
    for st in ("A", "B", "C"):
        out, _ = render(wd, f"style_{st}.mp4", st, a.accent, a.sec, audio=True)
        outs.append(str(out))
    print(json.dumps(outs, ensure_ascii=False))


def cmd_deliver(a):
    """Отправка в Telegram «Избранное». Порядок важен: сначала лёгкие обложки (уходят за секунды),
    потом ролик. Лёгкая HEVC-копия ~18 МБ пережимается только если её ещё нет или она устарела
    (кейс A11: раньше каждая выдача начиналась с двухминутного пережатия, и файлы приходили с опозданием)."""
    from .common import ffmpeg, video_info
    import time
    wd = wd_of(a.wd)
    src = wd / "final.mp4"
    if not src.exists():
        raise ReelError("нет final.mp4 — сначала render")
    if not a.no_send:
        need_telegram()

    def send(path, *extra):
        r = subprocess.run([sys.executable, "-m", "reels.tg_send", str(path), *extra], cwd=ROOT,
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip()[-400:], flush=True)
        return r.returncode

    # 1. обложки — первыми: маленькие, владелец сразу видит материал
    for name, cap, doc in ([] if a.no_send else (("cover_overlay.png", "Обложка-оверлей (прозрачный PNG)", True),
                           ("cover_preview.jpg", "Готовая обложка (кадр + текст)", True))):
        if (wd / name).exists():
            if send(wd / name, *(["--document"] if doc else []), "--caption", cap):
                return 1

    # 2. лёгкая копия ролика
    light = wd / "final_tg.mp4"
    if not light.exists() or light.stat().st_mtime < src.stat().st_mtime:
        t0 = time.time()
        dur = video_info(src)["duration"]
        vb = int(18.5 * 8e6 / dur - 192000)
        for pas in (1, 2):
            out = ["-an", "-f", "null", "/dev/null"] if pas == 1 else \
                ["-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(light)]
            ffmpeg("-i", src, "-c:v", "libx265", "-preset", "medium", "-b:v", str(vb), "-tag:v", "hvc1",
                   "-x265-params", f"pass={pas}:stats={wd / 'x265.log'}:log-level=error", *out, cwd=str(wd))
        for f in wd.glob("x265.log*"):
            f.unlink()
        print(f"лёгкая копия: {light.stat().st_size/1e6:.1f} МБ за {time.time()-t0:.0f} с", flush=True)
    else:
        print(f"лёгкая копия готова заранее: {light.stat().st_size/1e6:.1f} МБ", flush=True)
    if a.no_send:
        return 0
    return send(light, "--caption", a.caption or f"Монтаж: {wd.name}") or 0


def cmd_cover(a):
    """Прозрачный оверлей + готовая обложка на чистом кадре (без вшитых субтитров, с грейдом)."""
    from .cover import make
    from .common import ffmpeg
    from . import grade
    wd = wd_of(a.wd)
    prep = wd / "prep.mp4"
    if not prep.exists():
        raise ReelError("нет prep.mp4 (удалён командой clean?) — обложку собирать до очистки")
    lines = [x.strip() for x in a.headline.split("|") if x.strip()]
    make(lines, a.box.strip(), str(wd / "cover_overlay.png"))
    g = CONFIG["grade"]
    vf = grade.vf(g.get("preset", "cine"), float(g.get("k", 1.0)))
    ffmpeg("-ss", str(a.at), "-i", prep, "-frames:v", "1", *(["-vf", vf] if vf else []), wd / "cover_frame.png")
    from PIL import Image
    bg = Image.open(wd / "cover_frame.png").convert("RGBA")
    bg.alpha_composite(Image.open(wd / "cover_overlay.png"))
    bg.convert("RGB").save(wd / "cover_preview.jpg", quality=92)
    (wd / "cover_frame.png").unlink()
    print(f"обложка: {wd / 'cover_overlay.png'} (прозрачная) и {wd / 'cover_preview.jpg'} (готовая)")


def cmd_tg_login(a):
    import asyncio
    from .tg import login
    print(asyncio.run(login()))


def cmd_doctor(a):
    """Проверка окружения: что установлено, чего не хватает."""
    import shutil as sh
    ok = True

    def row(name, good, hint=""):
        nonlocal ok
        ok = ok and good
        print(f"{'✅' if good else '❌'} {name}" + ("" if good else f"  →  {hint}"))

    row("macOS", sys.platform == "darwin", "нужен Mac: поиск лица и HDR работают на инструментах macOS")
    row("ffmpeg", bool(sh.which("ffmpeg")), "brew install ffmpeg")
    row("whisper-cli", bool(sh.which("whisper-cli")), "brew install whisper-cpp")
    row(f"модель whisper ({WHISPER_MODEL.name})", WHISPER_MODEL.exists(), "см. INSTALL.md, шаг 3")
    row("swiftc (Xcode Command Line Tools)", bool(sh.which("swiftc")), "xcode-select --install")
    for mod, pip in (("numpy", "numpy"), ("PIL", "pillow"), ("vosk", "vosk")):
        try:
            __import__(mod)
            row(f"python: {pip}", True)
        except ImportError:
            row(f"python: {pip}", False, f"python3 -m pip install --user {pip}")
    from .vosk_align import MODEL_DIR
    row("модель Vosk", pathlib.Path(MODEL_DIR).is_dir(), "см. INSTALL.md, шаг 4")
    row("шрифт Unbounded", (ROOT / "assets/fonts/Unbounded.ttf").exists(), "файл должен быть в assets/fonts")
    row(f"рабочая папка {WORK_ROOT}", True)
    if CONFIG.get("telegram"):
        try:
            __import__("telethon")
            row("python: telethon", True)
        except ImportError:
            row("python: telethon", False, "python3 -m pip install --user telethon")
        from .tg import ENV, SESSION
        row("ключи Telegram", ENV.exists(), f"создай {ENV} (INSTALL.md, шаг Telegram)")
        row("вход в Telegram", pathlib.Path(str(SESSION) + ".session").exists(), "python3 -m reels tg-login")
    else:
        print("ℹ️  Telegram выключен (config.json → telegram: false)")
    print("\nВсё готово." if ok else "\nЕсть что доустановить (строки с ❌).")
    return 0 if ok else 1


def cmd_clean(a):
    wd = wd_of(a.wd)
    junk = ["prep.mp4", "clean.wav", "raw.wav", "speech16k.wav", "mix.wav", "final.wav", "energy.json",
            "preview_final.mp4"]
    freed = 0
    for j in junk + [p.name for p in wd.glob("style_*.mp4")]:
        p = wd / j
        if p.exists():
            freed += p.stat().st_size
            p.unlink()
    print(f"удалено {freed/1e6:.0f} МБ промежуточных файлов")


def main():
    p = argparse.ArgumentParser(prog="reels")
    sp = p.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("fetch"); f.add_argument("--list", action="store_true"); f.add_argument("--id", type=int)
    n = sp.add_parser("new"); n.add_argument("src"); n.add_argument("--name")
    v = sp.add_parser("view"); v.add_argument("wd")
    r = sp.add_parser("render"); r.add_argument("wd"); r.add_argument("--style")
    r.add_argument("--accent", default=CONFIG["accent"])
    r.add_argument("--limit", type=float); r.add_argument("--out", default="final.mp4")
    s = sp.add_parser("styles"); s.add_argument("wd"); s.add_argument("--accent", default=CONFIG["accent"]); s.add_argument("--sec", type=float, default=8)
    c = sp.add_parser("clean"); c.add_argument("wd")
    cv = sp.add_parser("cover"); cv.add_argument("wd"); cv.add_argument("--headline", required=True)
    cv.add_argument("--box", default=""); cv.add_argument("--at", type=float, default=5.0)
    sp.add_parser("tg-login"); sp.add_parser("doctor")
    dv = sp.add_parser("deliver"); dv.add_argument("wd"); dv.add_argument("--caption", default="")
    dv.add_argument("--no-send", action="store_true")
    a = p.parse_args()
    try:
        return {"fetch": cmd_fetch, "new": cmd_new, "view": cmd_view, "render": cmd_render,
                "styles": cmd_styles, "clean": cmd_clean, "deliver": cmd_deliver, "cover": cmd_cover,
                "tg-login": cmd_tg_login, "doctor": cmd_doctor}[a.cmd](a) or 0
    except ReelError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
