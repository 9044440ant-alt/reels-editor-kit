"""Этапы 1-2: приём исходника → prep.mp4 (1080x1920, 30 fps CFR, SDR) + звук.

prep.mp4       — видео без звука, рабочий исходник для рендера
raw.wav        — ОРИГИНАЛЬНЫЙ звук без обработки: именно он идёт в готовый ролик (кейс A1)
clean.wav      — обработанная копия ТОЛЬКО для распознавания речи и карты пауз
speech16k.wav  — 16 кГц моно для whisper
"""
import shutil
from .common import W, H, FPS, ReelError, ffmpeg, video_info, check_disk, jsave

AUDIO_CHAIN = ("highpass=f=80,"
               "afftdn=nr=10:nf=-40:tn=1,"
               "acompressor=threshold=0.1:ratio=3:attack=5:release=90:makeup=1.5,"
               "deesser=i=0.3")


def cover_crop(w, h):
    """scale+crop до 1080x1920 без полос: заполняем кадр и режем по центру."""
    if w * H >= h * W:  # шире, чем 9:16
        return f"scale=-2:{H}:flags=lanczos,crop={W}:{H}"
    return f"scale={W}:-2:flags=lanczos,crop={W}:{H}"


def prep_video(src, dst, info):
    base = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
            "-pix_fmt", "yuv420p", "-g", str(FPS), "-color_primaries", "bt709",
            "-color_trc", "bt709", "-colorspace", "bt709", "-movflags", "+faststart", dst]
    if info["hdr"]:
        # HDR (HLG/PQ) с айфона: тонмап через VideoToolbox, иначе в Instagram картинка блёклая.
        # Сначала scale_vt в SDR bt709 с сохранением пропорций, потом crop на CPU.
        tw, th = (W, -1) if info["w"] * H < info["h"] * W else (-1, H)
        if tw == -1:
            tw = round(info["w"] * H / info["h"] / 2) * 2
        else:
            th = round(info["h"] * W / info["w"] / 2) * 2
        vf = (f"scale_vt=w={tw}:h={th}:color_matrix=bt709:color_primaries=bt709:color_transfer=bt709,"
              f"hwdownload,format=nv12,crop={W}:{H},fps={FPS},format=yuv420p")
        try:
            ffmpeg("-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld",
                   "-i", src, "-vf", vf, *base)
            return "hdr->sdr videotoolbox"
        except ReelError:
            pass  # запасной путь ниже: без тонмапа, но с правильной разметкой
    ffmpeg("-i", src, "-map", "0:v:0", "-vf", f"{cover_crop(info['w'], info['h'])},fps={FPS},format=yuv420p", *base)
    return "sdr"


def prep_audio(src, wd):
    # raw.wav — оригинальная дорожка как есть (её и слышно в ролике): ни шумодава, ни компрессии,
    # ни нормализации громкости. Кейс A1.
    ffmpeg("-i", src, "-map", "0:a:0", "-vn", "-ar", "48000", "-c:a", "pcm_s16le", wd / "raw.wav")
    # clean.wav — только для распознавания речи и карты пауз, в видео не попадает
    ffmpeg("-i", src, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "48000", "-af", AUDIO_CHAIN, "-c:a", "pcm_s16le", wd / "clean.wav")
    ffmpeg("-i", wd / "clean.wav", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wd / "speech16k.wav")


def ingest(src, wd):
    info = video_info(src)
    if not info["has_audio"]:
        raise ReelError("в видео нет звука: монтажёр режет по речи, без звука работать не с чем")
    check_disk(wd, info["size"] * 3 + 300_000_000)
    info["video_path"] = prep_video(str(src), str(wd / "prep.mp4"), info)
    prep_audio(str(src), wd)
    from .faces import analyze
    info["placement"] = analyze(wd)
    info["source"] = str(src)
    jsave(wd / "info.json", info)
    return info
