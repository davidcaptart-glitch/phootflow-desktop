"""Capas de ajuste y modos de fusión de Edits, como en PhotoCraft (ArtCraft) y Photoshop.

Fórmulas portadas a numpy de PhotoCraft (github.com/storytold/photocraft, crates/compose/src/adjust,
MIT OR Apache-2.0), que las midió contra Photoshop: Brillo/Contraste moderno, Niveles con su gamma con
«pie», Curvas con spline cúbico natural (canal y luego RGB), Exposición en luz lineal, Intensidad
(Vibrance) en luz lineal con protección de piel, Tono/Saturación (Luminosidad primero), Equilibrio de
color, Blanco y negro por sectores de color, Filtro de fotografía en XYZ D50, Invertir, Posterizar y Umbral.
Todo trabaja sobre sRGB 0..1 float32 (H, W, 3). Los modos de fusión siguen la especificación W3C
(Compositing and Blending), la misma que usa PhotoCraft.
"""
from __future__ import annotations

import numpy as np

LUT_SIZE = 4096

ADJUSTMENTS = {  # clave: (nombre, parámetros por defecto)
    "brightness": ("Brillo/Contraste", dict(brightness=0.0, contrast=0.0)),
    "levels": ("Niveles", dict(channel="rgb", levels={c: [0.0, 1.0, 1.0, 0.0, 1.0] for c in ("rgb", "r", "g", "b")})),
    "curves": ("Curvas", dict(channel="rgb", curves={c: [[0.0, 0.0], [1.0, 1.0]] for c in ("rgb", "r", "g", "b")})),
    "exposure": ("Exposición", dict(exposure=0.0, offset=0.0, gamma=1.0)),
    "vibrance": ("Intensidad", dict(vibrance=0.0, saturation=0.0)),
    "hue_sat": ("Tono/Saturación", dict(hue=0.0, saturation=0.0, lightness=0.0, colorize=False)),
    "color_balance": ("Equilibrio de color", dict(tone="mid", shadows=[0.0, 0.0, 0.0], mid=[0.0, 0.0, 0.0],
                                                  highlights=[0.0, 0.0, 0.0], preserve=True)),
    "black_white": ("Blanco y negro", dict(weights=[40.0, 60.0, 40.0, 60.0, 20.0, 80.0], tint=False,
                                           tint_color="#e1d3b3")),
    "photo_filter": ("Filtro de fotografía", dict(color="#ec8a00", density=25.0, preserve=True)),
    "invert": ("Invertir", dict()),
    "posterize": ("Posterizar", dict(levels=4)),
    "threshold": ("Umbral", dict(level=128)),
}
ORDER = list(ADJUSTMENTS)

BLEND_MODES = {"normal": "Normal", "multiply": "Multiplicar", "screen": "Trama", "overlay": "Superponer",
               "soft_light": "Luz suave", "hard_light": "Luz fuerte", "darken": "Oscurecer", "lighten": "Aclarar",
               "color_dodge": "Sobreexponer color", "color_burn": "Subexponer color", "difference": "Diferencia",
               "hue": "Tono", "saturation": "Saturación", "color": "Color", "luminosity": "Luminosidad"}


def defaults(kind: str) -> dict:
    import copy
    return copy.deepcopy(ADJUSTMENTS[kind][1])


# ------------------------------------------------------------------ utilidades

def _lut_apply(x: np.ndarray, lut: np.ndarray) -> np.ndarray:
    idx = np.clip(x, 0, 1) * (len(lut) - 1)
    i0 = np.floor(idx).astype(np.int32)
    i1 = np.minimum(i0 + 1, len(lut) - 1)
    f = (idx - i0).astype(np.float32)
    return (lut[i0] * (1 - f) + lut[i1] * f).astype(np.float32)


def _grid(n=LUT_SIZE) -> np.ndarray:
    return np.linspace(0, 1, n, dtype=np.float64)


def srgb_to_lin(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def lin_to_srgb(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055).astype(np.float32)


def _hex(c: str) -> np.ndarray:
    c = (c or "#ffffff").lstrip("#")
    c = c if len(c) == 6 else "ffffff"
    return np.array([int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], np.float32)


# ------------------------------------------------------------------ tono

_KNEE = 126.0 / 255.0


def _brightness_raised(x: np.ndarray, b: float) -> np.ndarray:
    s = 1.375 ** (b / 50.0)
    e = 1.0 / (1.0 + 0.084 * b)
    x0 = min(_KNEE / s, 1.0)
    secant = (1.0 - _KNEE) / max(1.0 - x0, 1e-6)
    alpha, beta = s / secant, e / secant
    hermite = alpha * alpha + beta * beta <= 9.0
    h = max(1.0 - x0, 1e-6)
    t = np.clip((x - x0) / h, 0, 1)
    if hermite:
        t2, t3 = t * t, t * t * t
        roll = (2 * t3 - 3 * t2 + 1) * _KNEE + (t3 - 2 * t2 + t) * h * s + (-2 * t3 + 3 * t2) + (t3 - t2) * h * e
    else:
        roll = 1.0 - (1.0 - _KNEE) * (1.0 - t) ** alpha
    return np.clip(np.where(x <= x0, np.minimum(_KNEE, s * x), roll), 0, 1)


def modern_contrast(v: np.ndarray, contrast: float) -> np.ndarray:
    if contrast == 0:
        return v
    k = contrast / 128.0
    end, mid = 1.0 - k, 1.0 + k

    def half(t):
        return end * 0.5 * (t ** 3 - 2 * t * t + t) + (-2 * t ** 3 + 3 * t * t) * 0.5 + mid * 0.5 * (t ** 3 - t * t)

    v = np.clip(v, 0, 1)
    return np.where(v <= 0.5, half(v / 0.5), 1.0 - half((1.0 - v) / 0.5))


def brightness_contrast_lut(brightness: float, contrast: float) -> np.ndarray:
    x = _grid()
    if brightness > 0:
        y = _brightness_raised(x, brightness)
    elif brightness < 0:
        fine = _grid(4 * LUT_SIZE)
        ys = _brightness_raised(fine, -brightness)
        y = np.interp(x, ys, fine)  # inversa de la curva elevada (monótona)
    else:
        y = x
    return np.clip(modern_contrast(y, contrast), 0, 1).astype(np.float32)


def levels_gamma(u: np.ndarray, g: float) -> np.ndarray:
    c = 1.0 / g
    if g <= 1.0:
        return np.power(u, c)
    a, q = 2.0 ** g, 2.2
    u1 = ((1.0 - c / q) / (a * (1.0 - 1.0 / q))) ** (1.0 / (1.0 - c))
    if not np.isfinite(u1) or u1 <= 0:
        return np.power(u, c)
    b = (a * u1 - u1 ** c) / u1 ** q
    if not np.isfinite(b):
        return np.power(u, c)
    return np.where(u >= u1, np.power(u, c), a * u - b * np.power(u, q))


def levels_curve(v: np.ndarray, ch) -> np.ndarray:
    in_b, gamma, in_w, out_b, out_w = ch
    u = np.clip((v - in_b) / max(in_w - in_b, 1e-6), 0, 1)
    t = levels_gamma(u, max(float(gamma), 0.01))
    return out_b + t * (out_w - out_b)


def curve_lut(points) -> np.ndarray:
    """Spline cúbico natural por los puntos (puede pasarse entre puntos, como en Photoshop)."""
    pts = sorted((float(a), float(b)) for a, b in points)
    dedup = []
    for p in pts:
        if not dedup or abs(p[0] - dedup[-1][0]) >= 1e-6:
            dedup.append(p)
    x = _grid()
    if len(dedup) < 2:
        return x.astype(np.float32)
    px = np.array([p[0] for p in dedup])
    py = np.array([p[1] for p in dedup])
    n = len(dedup)
    m2 = np.zeros(n)
    if n > 2:
        c = np.zeros(n)
        d = np.zeros(n)
        for i in range(1, n - 1):
            h0, h1 = px[i] - px[i - 1], px[i + 1] - px[i]
            a, b, cc = h0 / 6.0, (h0 + h1) / 3.0, h1 / 6.0
            r = (py[i + 1] - py[i]) / h1 - (py[i] - py[i - 1]) / h0
            den = b - a * c[i - 1]
            c[i] = cc / den
            d[i] = (r - a * d[i - 1]) / den
        for i in range(n - 2, 0, -1):
            m2[i] = d[i] - c[i] * m2[i + 1]
    i = np.clip(np.searchsorted(px, x, side="left") - 1, 0, n - 2)
    x0, x1, y0, y1 = px[i], px[i + 1], py[i], py[i + 1]
    h = x1 - x0
    A = (x1 - x) / h
    B = (x - x0) / h
    y = A * y0 + B * y1 + ((A ** 3 - A) * m2[i] + (B ** 3 - B) * m2[i + 1]) * h * h / 6.0
    y = np.where(x <= px[0], py[0], np.where(x >= px[-1], py[-1], y))
    return np.clip(y, 0, 1).astype(np.float32)


def _tone_rgb(img, master_lut, ch_luts):
    """Primero la curva de cada canal y después la maestra (como Photoshop)."""
    out = np.empty_like(img)
    for c in range(3):
        out[..., c] = _lut_apply(_lut_apply(img[..., c], ch_luts[c]), master_lut)
    return out


# ------------------------------------------------------------------ color

def rgb_to_hsl(c):
    mx, mn = c.max(-1), c.min(-1)
    l = (mx + mn) / 2
    d = mx - mn
    s = np.where(d < 1e-7, 0, np.where(l > 0.5, d / np.maximum(2 - mx - mn, 1e-7), d / np.maximum(mx + mn, 1e-7)))
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    dd = np.maximum(d, 1e-7)
    h = np.where(mx == r, np.mod((g - b) / dd, 6), np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4))
    h = np.where(d < 1e-7, 0, h / 6.0)
    return h, s, l


def _hue_rgb(h, p, q):
    def f(t):
        t = np.mod(t, 1.0)
        return np.where(t < 1 / 6, p + (q - p) * 6 * t, np.where(t < 0.5, q, np.where(t < 2 / 3, p + (q - p) * (2 / 3 - t) * 6, p)))
    return np.stack([f(h + 1 / 3), f(h), f(h - 1 / 3)], -1)


def hsl_to_rgb(h, s, l):
    q = np.where(l < 0.5, l * (1 + s), l + s - l * s)
    rgb = _hue_rgb(h, 2 * l - q, q)
    return np.where((s <= 0)[..., None], l[..., None], rgb)


def hue_saturation(img, hue, sat, light, colorize):
    s, l = sat / 100.0, light / 100.0

    def lighten(v):
        return v + (1 - v) * l if l > 0 else v * (1 + l)

    if colorize:
        _, _, ll = rgb_to_hsl(img)
        return hsl_to_rgb(np.full(ll.shape, (hue % 360) / 360.0), np.full(ll.shape, min(abs(s), 1.0)), lighten(ll))
    lc = lighten(img)
    hh, ss, ll = rgb_to_hsl(lc)
    hh = np.mod(hh + hue / 360.0, 1.0)
    ss = np.clip(ss / max(1 - s, 1 / 512) if s > 0 else ss * (1 + s), 0, 1)
    return hsl_to_rgb(hh, ss, ll)


_VBOOST = [0.5001, 0.0887, 0.4374, 0.7573, -0.8113, 0.5154, 2.8026]


def vibrance(img, vib, sat):
    v, s = vib / 100.0, sat / 100.0
    l = srgb_to_lin(img).astype(np.float64)
    if v != 0:
        mx, mn = l.max(-1), l.min(-1)
        ok = (mx > 0) & (mx - mn > 1e-9)
        mxs = np.where(ok, mx, 1)
        t = np.where(ok, 1 - mn / mxs, 0)
        shape = t * (1 - t) * (2 - t + t * t) * mx * np.maximum(1 - mx, 0)
        if v < 0:
            a = -v
            t2 = t * (1 - a / 4) * (1 - a + a * t * (1 + t) / 2)
            v2 = mx - a * shape
        else:
            c0, c1, p, q, r0, ra, rt = _VBOOST
            d = np.maximum(mx - mn, 1e-9)
            r, g, b = l[..., 0], l[..., 1], l[..., 2]
            hh = np.where(mx == r, np.mod((g - b) / d, 6), np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
            w = np.where(hh <= 30, 1, np.where(hh < 45, (45 - hh) / 15, np.where(hh < 300, 0, (hh - 300) / 60)))
            a = v * (1 - w) + 0.64 * v ** 1.6 * w
            boost = (c0 * a + c1 * a * a) * np.power(t, p) * np.power(np.maximum(1 - t, 0), q) * \
                np.exp((r0 + ra * a + rt * (1 - t)) * np.maximum(1 - mx, 0))
            t2 = np.minimum(t * (1 + boost), 1)
            v2 = mx + v / 4 * shape
        m2 = v2 * (1 - t2)
        k = (v2 - m2) / np.maximum(mx - mn, 1e-9)
        l = np.where(ok[..., None], m2[..., None] + (l - mn[..., None]) * k[..., None], l)
    if s != 0:
        g = 0.288 * l[..., 0] + 0.712 * l[..., 1]
        l = np.clip(g[..., None] + (1 + s) * (l - g[..., None]), 0, 1)
    return lin_to_srgb(l.astype(np.float32))


def _balance_curve(tones, preserve, ch):
    sh, mi, hi = [np.clip(np.asarray(t, np.float64), -100, 100) for t in tones]
    own = lambda t: t[ch]  # noqa: E731
    if preserve:
        black, white, m = sh.max() - own(sh), own(hi) - hi.min(), own(mi) - (mi.max() + mi.min()) / 2
    else:
        black, white, m = max(-own(sh), 0), max(own(hi), 0), own(mi) + (own(sh) + own(hi)) / 2
    gamma = 2.0 ** (m / 100.0)
    e = 1.0 / max(gamma, 0.01)
    b, w = black / 255.0, 1 - white / 255.0
    x = _grid()
    u = np.clip((x - b) / max(w - b, 1e-6), 0, 1)
    y = np.power(u, e)
    if gamma > 1:
        s0 = 2.0 ** gamma
        u0 = min(2 * s0 ** (1 / (e - 1)), 1.0)
        if np.isfinite(u0) and u0 > 0:
            p, d = u0 ** e, e * u0 ** (e - 1)
            t = u / u0
            knee = (t ** 3 - 2 * t * t + t) * u0 * s0 + (3 * t * t - 2 * t ** 3) * p + (t ** 3 - t * t) * u0 * d
            y = np.where(u < u0, knee, y)
    y = np.where(x >= w, 1, np.where(x <= b, 0, y))
    return np.clip(y, 0, 1).astype(np.float32)


def lum(c):
    return 0.3 * c[..., 0] + 0.59 * c[..., 1] + 0.11 * c[..., 2]


def _clip_color(c):
    l = lum(c)[..., None]
    n, x = c.min(-1, keepdims=True), c.max(-1, keepdims=True)
    c = np.where(n < 0, l + (c - l) * l / np.maximum(l - n, 1e-7), c)
    c = np.where(x > 1, l + (c - l) * (1 - l) / np.maximum(x - l, 1e-7), c)
    return c


def set_lum(c, l):
    d = (l - lum(c))[..., None]
    return _clip_color(c + d)


def _sat(c):
    return c.max(-1) - c.min(-1)


def set_sat(c, s):
    mx, mn = c.max(-1, keepdims=True), c.min(-1, keepdims=True)
    rng = np.maximum(mx - mn, 1e-7)
    out = (c - mn) / rng * s[..., None]
    return np.where((mx - mn) > 1e-7, out, 0)


def black_white(img, weights, tint=None):
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    mx, mn = img.max(-1), img.min(-1)
    mid = r + g + b - mx - mn
    w = [float(x) / 100.0 for x in weights]
    primary = np.where((r >= g) & (r >= b), w[0], np.where(g >= b, w[2], w[4]))
    secondary = np.where((b <= r) & (b <= g), w[1], np.where(r <= g, w[3], w[5]))
    gray = np.clip(mn + (mid - mn) * secondary + (mx - mid) * primary, 0, 1)
    if tint is not None:
        return np.clip(set_lum(np.broadcast_to(tint, img.shape).astype(np.float32), gray), 0, 1)
    return np.repeat(gray[..., None], 3, -1)


_SRGB_TO_XYZ_D50 = np.array([[0.4360747, 0.3850649, 0.1430804], [0.2225045, 0.7168786, 0.0606169],
                             [0.0139322, 0.0971045, 0.7141733]], np.float32)
_XYZ_D50_TO_SRGB = np.linalg.inv(_SRGB_TO_XYZ_D50).astype(np.float32)
_D50 = np.array([0.9642, 1.0, 0.8251], np.float32)


def photo_filter(img, color, density, preserve):
    d = np.clip(density / 100.0, 0, 1)
    xyz = _SRGB_TO_XYZ_D50 @ srgb_to_lin(color)
    s = 1 - d + d * xyz / _D50
    m = _XYZ_D50_TO_SRGB @ np.diag(s) @ _SRGB_TO_XYZ_D50
    f = lin_to_srgb(np.clip(srgb_to_lin(img) @ m.T, 0, 1))
    if preserve:
        f = np.clip(set_lum(f, lum(img)), 0, 1)
    return f


# ------------------------------------------------------------------ aplicar un ajuste

def apply(img: np.ndarray, kind: str, p: dict) -> np.ndarray:
    """Aplica el ajuste `kind` con los parámetros `p` a `img` (sRGB 0..1)."""
    img = np.clip(img, 0, 1).astype(np.float32)
    if kind == "brightness":
        lut = brightness_contrast_lut(float(p.get("brightness", 0)), float(p.get("contrast", 0)))
        return _lut_apply(img, lut)
    if kind == "levels":
        lv = p.get("levels", {})
        x = _grid()
        master = np.clip(levels_curve(x, lv.get("rgb", [0, 1, 1, 0, 1])), 0, 1).astype(np.float32)
        chs = [np.clip(levels_curve(x, lv.get(c, [0, 1, 1, 0, 1])), 0, 1).astype(np.float32) for c in "rgb"]
        return _tone_rgb(img, master, chs)
    if kind == "curves":
        cv = p.get("curves", {})
        return _tone_rgb(img, curve_lut(cv.get("rgb", [[0, 0], [1, 1]])),
                         [curve_lut(cv.get(c, [[0, 0], [1, 1]])) for c in "rgb"])
    if kind == "exposure":
        m = 2.0 ** float(p.get("exposure", 0))
        g = max(float(p.get("gamma", 1.0)), 0.01)
        lin = np.maximum(np.power(img, 2.2) * m + float(p.get("offset", 0)), 0)  # Photoshop: potencia 2.2
        if g != 1:
            lin = np.power(lin, 1 / g)
        return np.clip(np.power(np.clip(lin, 0, 1), 1 / 2.2), 0, 1).astype(np.float32)
    if kind == "vibrance":
        return vibrance(img, float(p.get("vibrance", 0)), float(p.get("saturation", 0)))
    if kind == "hue_sat":
        return np.clip(hue_saturation(img, float(p.get("hue", 0)), float(p.get("saturation", 0)),
                                      float(p.get("lightness", 0)), bool(p.get("colorize"))), 0, 1).astype(np.float32)
    if kind == "color_balance":
        tones = [p.get("shadows", [0, 0, 0]), p.get("mid", [0, 0, 0]), p.get("highlights", [0, 0, 0])]
        out = np.empty_like(img)
        for c in range(3):
            out[..., c] = _lut_apply(img[..., c], _balance_curve(tones, bool(p.get("preserve", True)), c))
        return out
    if kind == "black_white":
        tint = _hex(p.get("tint_color")) if p.get("tint") else None
        return black_white(img, p.get("weights", [40, 60, 40, 60, 20, 80]), tint).astype(np.float32)
    if kind == "photo_filter":
        return photo_filter(img, _hex(p.get("color")), float(p.get("density", 25)), bool(p.get("preserve", True)))
    if kind == "invert":
        return 1.0 - img
    if kind == "posterize":
        n = int(np.clip(p.get("levels", 4), 2, 255))
        x = np.round(img * 255)
        bins = np.minimum(np.floor(x * n / 256.0), n - 1)
        return (np.floor(bins * 255.0 / (n - 1)) / 255.0).astype(np.float32)
    if kind == "threshold":
        lvl = float(p.get("level", 128))
        y = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
        return np.repeat((y * 255 >= lvl).astype(np.float32)[..., None], 3, -1)
    return img


# ------------------------------------------------------------------ modos de fusión

def blend(base: np.ndarray, top: np.ndarray, mode: str) -> np.ndarray:
    """Resultado de fundir `top` sobre `base` (sin opacidad), W3C Compositing and Blending."""
    b, s = base, top
    if mode in (None, "", "normal"):
        return s
    if mode == "multiply":
        return b * s
    if mode == "screen":
        return b + s - b * s
    if mode == "overlay":
        return np.where(b <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s))
    if mode == "hard_light":
        return np.where(s <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s))
    if mode == "soft_light":
        d = np.where(b <= 0.25, ((16 * b - 12) * b + 4) * b, np.sqrt(b))
        return np.where(s <= 0.5, b - (1 - 2 * s) * b * (1 - b), b + (2 * s - 1) * (d - b))
    if mode == "darken":
        return np.minimum(b, s)
    if mode == "lighten":
        return np.maximum(b, s)
    if mode == "color_dodge":
        return np.where(b <= 0, 0, np.where(s >= 1, 1, np.minimum(1, b / np.maximum(1 - s, 1e-6))))
    if mode == "color_burn":
        return np.where(b >= 1, 1, np.where(s <= 0, 0, 1 - np.minimum(1, (1 - b) / np.maximum(s, 1e-6))))
    if mode == "difference":
        return np.abs(b - s)
    if mode == "hue":
        return set_lum(set_sat(s, _sat(b)), lum(b))
    if mode == "saturation":
        return set_lum(set_sat(b, _sat(s)), lum(b))
    if mode == "color":
        return set_lum(s, lum(b))
    if mode == "luminosity":
        return set_lum(b, lum(s))
    return s
