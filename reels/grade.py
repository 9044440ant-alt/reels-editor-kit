"""Лёгкая кинематографическая цветокоррекция.

Собирает таблицу цветов (.cube LUT) и отдаёт её ffmpeg — это быстро и одинаково для всех кадров.
Референс — кадры дорогой рекламы: тёплый мягкий свет, янтарные блики,
холодно-бирюзовые тени, приподнятый чёрный, аккуратный контраст, приглушённая насыщенность.

Жёсткое условие: КОЖА НЕ ЖЕЛТИТ. Тёплый сдвиг идёт в блики и общую атмосферу, а оттенки в диапазоне
кожи возвращаются к исходному тону.
"""
import numpy as np
from .common import ASSETS

PRESETS = {
    # мягкий: чуть теплее и контрастнее, атмосфера почти как в оригинале
    "soft": {"contrast": 0.07, "sat": 0.97, "shadow_tint": (-0.006, 0.003, 0.014),
             "high_tint": (0.014, 0.006, -0.010)},
    # киношный: холодные тени, янтарные света, приглушённый фон
    "cine": {"contrast": 0.12, "sat": 0.90, "skin_sat": 1.06, "gain": (1.05, 1.0, 0.95),
             "lift": (0.010, 0.018, 0.034), "shadow_tint": (-0.016, 0.004, 0.030),
             "high_tint": (0.030, 0.010, -0.022), "midtone": -0.03},
    # сильный: тот же характер, но заметнее
    "strong": {"contrast": 0.15, "sat": 0.86, "skin_sat": 1.08, "gain": (1.07, 1.0, 0.93),
               "lift": (0.012, 0.022, 0.044), "shadow_tint": (-0.022, 0.005, 0.042),
               "high_tint": (0.042, 0.014, -0.030), "midtone": -0.05},
}

LOOK = {
    "lift": (0.012, 0.016, 0.028),    # приподнятый чёрный с холодком (плёночная тень)
    "gain": (1.045, 1.005, 0.965),    # тепло в светах
    "gamma": (1.0, 1.005, 1.015),     # средние тона чуть холоднее светов
    "contrast": 0.10,                  # мягкая S-кривая
    "pivot": 0.46,
    "sat": 0.94,                       # общая насыщенность чуть ниже
    "skin_sat": 1.04,                  # кожа остаётся живой
    "skin_keep": 0.75,                 # насколько возвращаем исходный оттенок кожи (0..1)
    "shadow_tint": (-0.010, 0.004, 0.020),
    "high_tint": (0.020, 0.008, -0.014),
    "midtone": 0.0,                    # общий сдвиг средних тонов (минус = глубже, киношнее)
}
SIZE = 33


def _rgb_to_hsv(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(-1), rgb.min(-1)
    d = mx - mn + 1e-9
    h = np.where(mx == r, (g - b) / d % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) / 6.0
    return np.stack([h % 1.0, d / (mx + 1e-9), mx], -1)


def _hsv_to_rgb(hsv):
    h, s, v = hsv[..., 0] * 6, hsv[..., 1], hsv[..., 2]
    i = np.floor(h).astype(int) % 6
    f = h - np.floor(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    out = np.stack([
        np.choose(i, [v, q, p, p, t, v]),
        np.choose(i, [t, v, v, q, p, p]),
        np.choose(i, [p, p, t, v, v, q])], -1)
    return out


def apply_look(rgb, k=1.0, look=None):
    """rgb в 0..1 → грейд. k — сила (0 = исходник, 1 = полный)."""
    L = {**LOOK, **(look or {})}
    src = rgb.copy()
    x = np.clip(rgb, 0, 1)
    lift, gain, gamma = np.array(L["lift"]), np.array(L["gain"]), np.array(L["gamma"])
    x = np.clip(lift + x * (1 - lift) * gain, 0, 1) ** (1 / gamma)
    # мягкая S-кривая вокруг точки опоры
    c = L["contrast"]
    x = np.clip(x + c * np.sin(np.pi * (x - L["pivot"])) * (1 - np.abs(x - L["pivot"])), 0, 1)
    if L["midtone"]:
        luma0 = (x * np.array([0.2126, 0.7152, 0.0722])).sum(-1, keepdims=True)
        x = np.clip(x + L["midtone"] * 4 * luma0 * (1 - luma0), 0, 1)   # трогаем середину, не крайности
    luma = (x * np.array([0.2126, 0.7152, 0.0722])).sum(-1, keepdims=True)
    x = np.clip(x + np.array(L["shadow_tint"]) * (1 - luma) ** 2 + np.array(L["high_tint"]) * luma ** 2, 0, 1)
    # насыщенность + защита кожи: оттенок кожи (~15-50° по кругу) возвращаем к исходному
    hsv, hsv0 = _rgb_to_hsv(x), _rgb_to_hsv(np.clip(src, 0, 1))
    skin = np.exp(-((hsv0[..., 0] - 0.075) / 0.055) ** 2) * np.clip(hsv0[..., 1] * 3.2, 0, 1)
    hsv[..., 1] *= L["sat"] * (1 - skin) + L["skin_sat"] * skin
    dh = (hsv0[..., 0] - hsv[..., 0] + 0.5) % 1.0 - 0.5
    hsv[..., 0] = (hsv[..., 0] + dh * skin * L["skin_keep"]) % 1.0
    out = np.clip(_hsv_to_rgb(hsv), 0, 1)
    return np.clip(src + (out - src) * k, 0, 1)


def cube_path(preset="cine", k=1.0):
    return ASSETS / "luts" / f"look_{preset}_{int(round(k * 100)):03d}.cube"


def ensure_cube(preset="cine", k=1.0, size=SIZE):
    """Создаёт .cube один раз и возвращает путь. Пресет none или нулевая сила → грейд выключен."""
    if k <= 0 or preset in (None, "none", "off"):
        return None
    p = cube_path(preset, k)
    if p.exists():
        return p
    g = np.linspace(0, 1, size)
    b, gr, r = np.meshgrid(g, g, g, indexing="ij")   # порядок .cube: быстрее всего меняется R
    grid = np.stack([r, gr, b], -1).reshape(-1, 3)
    out = apply_look(grid, k, PRESETS.get(preset))
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        f.write(f"# reels-editor look {preset}, сила {k}\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n")
        for v in out:
            f.write(f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
    return p


def vf(preset="cine", k=1.0):
    """Кусок фильтра для ffmpeg (пусто, если грейд выключен)."""
    p = ensure_cube(preset, k)
    return f"lut3d=file='{p}'" if p else ""
