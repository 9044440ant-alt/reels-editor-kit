"""Синтез звуковых эффектов (без лицензий и скачиваний): «вжух» и щелчок."""
import wave
import numpy as np
from .common import ASSETS

SR = 48000


def _save(path, x):
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def whoosh(dur=0.5, seed=7):
    rng = np.random.default_rng(seed)
    n = int(SR * dur)
    noise = rng.standard_normal(n)
    spec = np.fft.rfft(noise)
    f = np.fft.rfftfreq(n, 1 / SR)
    # полосовой фильтр с центром, который едет вверх: имитация пролёта
    out = np.zeros(n)
    hop = 1024
    for k in range(0, n, hop):
        pos = k / n
        fc = 300 + 3500 * pos ** 1.6
        band = np.exp(-((np.log(f + 1) - np.log(fc)) ** 2) / 0.35)
        seg = np.fft.irfft(spec * band, n)[k:k + hop]
        out[k:k + len(seg)] = seg
    t = np.linspace(0, 1, n)
    env = np.sin(np.pi * t ** 0.7) ** 2
    out = out * env
    return out / (np.abs(out).max() + 1e-9) * 0.9


def pop(dur=0.07):
    n = int(SR * dur)
    t = np.arange(n) / SR
    freq = 1400 * np.exp(-t * 35) + 380
    phase = 2 * np.pi * np.cumsum(freq) / SR
    x = np.sin(phase) * np.exp(-t * 60)
    x[:48] *= np.linspace(0, 1, 48)
    return x / np.abs(x).max() * 0.8


def ensure():
    d = ASSETS / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    paths = {"whoosh": d / "whoosh.wav", "pop": d / "pop.wav"}
    if not paths["whoosh"].exists():
        _save(paths["whoosh"], whoosh())
    if not paths["pop"].exists():
        _save(paths["pop"], pop())
    return paths
