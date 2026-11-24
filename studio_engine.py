"""Motor de revelado de PhootFlow Studio: ajustes no destructivos tipo Lightroom.

Cada foto tiene un diccionario de ajustes (`DEFAULTS` da la forma y los valores neutros). `render()` aplica
los ajustes a una imagen sRGB lineal (float32) y devuelve sRGB 0..1. Se usa igual para la vista previa
(proxy de ~2048 px, interactivo) y para exportar a resolución completa.

Los ajustes se guardan junto a la sesión en `<carpeta>/.phootflow/studio/<foto>.json` (los originales no se
tocan nunca).
"""
from __future__ import annotations

import copy
import json
import math
import os

import cv2
import numpy as np

import engine as E
import luts as L
import styles as S

# ------------------------------------------------------------------ controles

# (clave, etiqueta, mínimo, máximo, paso, neutro) agrupados por panel, en el orden de Lightroom
PANELS: list[tuple[str, list[tuple[str, str, float, float, float, float]]]] = [
    ("Básico", [
        ("temp", "Temperatura", -100, 100, 1, 0), ("tint", "Matiz", -100, 100, 1, 0),
        ("exposure", "Exposición", -5, 5, 0.05, 0), ("contrast", "Contraste", -100, 100, 1, 0),
        ("highlights", "Altas luces", -100, 100, 1, 0), ("shadows", "Sombras", -100, 100, 1, 0),
        ("whites", "Blancos", -100, 100, 1, 0), ("blacks", "Negros", -100, 100, 1, 0),
    ]),
    ("Presencia", [
        ("texture", "Textura", -100, 100, 1, 0), ("clarity", "Claridad", -100, 100, 1, 0),
        ("dehaze", "Eliminar neblina", -100, 100, 1, 0),
        ("vibrance", "Intensidad", -100, 100, 1, 0), ("saturation", "Saturación", -100, 100, 1, 0),
    ]),
    ("Curva tonal", [
        ("curve_highlights", "Iluminaciones", -100, 100, 1, 0), ("curve_lights", "Claros", -100, 100, 1, 0),
        ("curve_darks", "Oscuros", -100, 100, 1, 0), ("curve_shadows", "Sombras", -100, 100, 1, 0),
    ]),
    ("Gradación de color", [
        ("grade_sh_hue", "Sombras · tono", 0, 360, 1, 0), ("grade_sh_sat", "Sombras · saturación", 0, 100, 1, 0),
        ("grade_mid_hue", "Medios · tono", 0, 360, 1, 0), ("grade_mid_sat", "Medios · saturación", 0, 100, 1, 0),
        ("grade_hi_hue", "Luces · tono", 0, 360, 1, 0), ("grade_hi_sat", "Luces · saturación", 0, 100, 1, 0),
        ("grade_blend", "Fusión", 0, 100, 1, 50), ("grade_balance", "Equilibrio", -100, 100, 1, 0),
    ]),
    ("Detalle", [
        ("sharpen", "Nitidez", 0, 150, 1, 40), ("sharpen_radius", "Radio", 0.5, 3.0, 0.1, 1.0),
        ("nr_luma", "Reducción de ruido", 0, 100, 1, 0), ("nr_color", "Ruido de color", 0, 100, 1, 25),
    ]),
    ("Efectos", [
        ("vignette", "Viñeta", -100, 100, 1, 0), ("grain", "Grano", 0, 100, 1, 0),
    ]),
    ("Geometría", [
        ("angle", "Enderezar", -45, 45, 0.1, 0),
    ]),
]
HSL_BANDS = [("red", "Rojo", 0), ("orange", "Naranja", 30), ("yellow", "Amarillo", 60), ("green", "Verde", 120),
             ("aqua", "Aguamarina", 180), ("blue", "Azul", 225), ("purple", "Púrpura", 270),
             ("magenta", "Magenta", 315)]

DEFAULTS: dict = {k: v for _, ctrls in PANELS for k, *_rest, v in [(c[0], c[5]) for c in ctrls]}
DEFAULTS.update({f"hsl_{b}_{p}": 0 for b, _, _ in HSL_BANDS for p in ("h", "s", "l")})
# Color selectivo: lo elegido (colores y/o una máscara) se queda en color y el resto pasa a B/N
SPLASH_MODES = {"color": "Los colores elegidos", "mask": "Lo que marque una máscara",
                "both": "Los colores elegidos, dentro de una máscara"}
SPLASH_CTRLS = [("splash_range", "Amplitud del color", 5, 90, 1, 20), ("splash_soft", "Suavidad del borde", 0, 100, 1, 45),
                ("splash_sat", "Saturación de lo que queda", -50, 100, 1, 0),
                ("splash_amount", "Resto en B/N", 0, 100, 1, 100)]
DEFAULTS.update({"splash": False, "splash_hues": [], "splash_mode": "color", "splash_mask": 0, "splash_skin": True})
DEFAULTS.update({c[0]: c[5] for c in SPLASH_CTRLS})
DEFAULTS.update({"masks": [], "bw_style": "Editorial", "bw": False, "crop": [0.0, 0.0, 1.0, 1.0], "crop_ratio": "Original", "lut": "", "lut_strength": 100,
                 "rating": 0, "flag": 0, "profile": "Neutro"})


def new_settings(**kw) -> dict:
    s = copy.deepcopy(DEFAULTS)
    s.update(kw)
    return s


# ------------------------------------------------------------------ utilidades de color

def _srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


_SRGB_LUT = None  # se rellena abajo: codificación sRGB tabulada (65536 niveles lineales)


def _lin(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.04045, x / 12.92, ((np.clip(x, 0, None) + 0.055) / 1.055) ** 2.4)


def _wb_gains(temp: float, tint: float) -> np.ndarray:
    """Temperatura/matiz relativos al «como se disparó» (-100..100) → multiplicadores RGB lineales.
    +temp = más cálido (≈ bajar la temperatura de la luz que se corrige ~ 2 stops de ámbar/azul en ±100)."""
    t, g = temp / 100.0, tint / 100.0
    r = 2 ** (0.45 * t)
    b = 2 ** (-0.45 * t)
    gr = 2 ** (-0.30 * g)  # +matiz = más magenta (menos verde)
    gains = np.array([r, gr, b], np.float32)
    return gains / (0.2126 * gains[0] + 0.7152 * gains[1] + 0.0722 * gains[2])


def _smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def _hue_weights(h: np.ndarray, center: float, width: float = 45.0) -> np.ndarray:
    d = np.abs(((h - center) + 180) % 360 - 180)
    return np.clip(1 - d / width, 0, 1) ** 2 * (3 - 2 * np.clip(1 - d / width, 0, 1))


# ------------------------------------------------------------------ revelado

# Fórmulas portadas de LightCraft (https://github.com/storytold/lightcraft, Apache-2.0, © sus autores):
# crates/pipeline/src/{finish.rs, tone.rs, local.rs, colorops.rs, curve.rs}. Adaptadas a numpy y a sRGB lineal.

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],   # sRGB lineal → LMS (Ottosson)
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]], np.float32)
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],  # LMS^(1/3) → OkLab
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]], np.float32)
_M1i, _M2i = np.linalg.inv(_M1).astype(np.float32), np.linalg.inv(_M2).astype(np.float32)


def to_oklab(lin):
    return np.cbrt(lin @ _M1.T) @ _M2.T


def from_oklab(lab):
    return (lab @ _M2i.T) ** 3 @ _M1i.T


def _oklch_hue_of_hsv(deg: float) -> float:
    rgb = cv2.cvtColor(np.array([[[deg, 1.0, 1.0]]], np.float32), cv2.COLOR_HSV2RGB)[0, 0]
    lab = to_oklab(_lin(rgb)[None, :])[0]
    return float(math.atan2(lab[2], lab[1]))


_BAND_H = [_oklch_hue_of_hsv(c) for _, _, c in HSL_BANDS]
_SKIN_H = _oklch_hue_of_hsv(25)


def _wrap(x):
    return (x + math.pi) % (2 * math.pi) - math.pi


def _smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def _gauss(x, sigma):
    """Desenfoque gaussiano rápido (en pequeño y vuelta) para planos de baja frecuencia."""
    if sigma < 4:
        return cv2.GaussianBlur(x, (0, 0), sigma)
    f = 4.0 / sigma
    small = cv2.resize(x, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 4.0)
    return cv2.resize(small, (x.shape[1], x.shape[0]), interpolation=cv2.INTER_LINEAR)


def guided(p, sigma, eps):
    """Filtro guiado auto-guiado (He et al.) con ventanas gaussianas: suaviza sin cruzar bordes."""
    m = _gauss(p, sigma)
    var = np.maximum(_gauss(p * p, sigma) - m * m, 0)
    a = var / (var + eps)
    b = m - a * m
    return _gauss(a, sigma) * p + _gauss(b, sigma)


def _tonemap_lut(contrast, whites, blacks, n=4096):
    """Curva base filmic (Reinhard extendido + rodilla), sobre ev = log2(Y/0.18). `tone.rs:241-284`.
    Contraste de partida +0,35: la curva de LightCraft es más plana que la de Lightroom (su Auto pone +68)."""
    c = contrast / 100 + 0.35
    w, b = whites / 100, blacks / 100
    ev = np.linspace(-14, 10, n)
    slope = 1 + 0.55 * c if c >= 0 else 1 + 0.4 * c
    wl = 0.18 * 2 ** (2.9 - 1.6 * w)
    pre = 1 + 0.18 / wl
    y = 0.18 * 2 ** (ev * slope) * pre
    rh = lambda v: v * (1 + v / wl ** 2) / (1 + v)  # noqa: E731
    knee = wl * 2 ** -0.5
    ok = rh(knee)
    ks = (1 + knee * (2 + knee) / wl ** 2) / (1 + knee) ** 2
    cr = ks * knee / (1 - ok)
    o = np.where(y <= knee, rh(y), 1 - (1 - ok) / (1 + cr * (y / knee - 1)))
    o = np.minimum(o, 1)
    if b < 0:
        k, e = -0.035 * b, 0.004
        soft = lambda v: ((v - k) + np.sqrt((v - k) ** 2 + e * e)) / 2  # noqa: E731
        o = (soft(o) - soft(0)) / (soft(1) - soft(0))
    elif b > 0:
        k = 0.03 * b
        o = k + (1 - k) * o
    return ev.astype(np.float32), np.clip(o, 0, 1).astype(np.float32)


_SRGB_LUT = _srgb(np.linspace(0, 1, 65536, dtype=np.float32)).astype(np.float32)


def _param_curve_lut(s, n=4096):
    """Curva paramétrica (cuatro regiones, cosenos alzados, monótona). `curve.rs:32-62`."""
    x = np.linspace(0, 1, n, dtype=np.float32)
    d = np.zeros_like(x)
    for a0, b0, amt in ((0, .25, s["curve_shadows"]), (.25, .5, s["curve_darks"]), (.5, .75, s["curve_lights"]),
                        (.75, 1, s["curve_highlights"])):
        if amt:
            ctr, half = (a0 + b0) / 2, (b0 - a0) * 0.75 + 0.05
            t = np.clip((x - ctr) / half, -1, 1)
            d += amt / 100 * 0.22 * (0.5 + 0.5 * np.cos(np.pi * t))
    return np.maximum.accumulate(np.clip(x + d * 4 * x * (1 - x), 0, 1))


def tone_stage(lin: np.ndarray, s: dict) -> np.ndarray:
    """Luz: BB, exposición, tono local (altas luces, sombras, claridad, textura) y curva base.
    Devuelve sRGB lineal de pantalla (0..1) para la etapa de color."""
    h, w = lin.shape[:2]
    ppl = max(h, w)
    c = lin * _wb_gains(s["temp"], s["tint"])[None, None, :]
    Y = np.maximum(E._luma(c), 1e-6)
    l = np.log2(Y / 0.18).astype(np.float32)  # plano log sin exposición (finish.rs: los planos no la llevan)
    ev = float(s["exposure"])
    delta = np.zeros_like(l)
    if s["highlights"] or s["shadows"]:
        base = guided(cv2.medianBlur(l, 3), max(1.0, 0.015 * ppl), 0.35)
        B = base + ev
        ws = 1 - _smoothstep(-4.8, 0.3, B)
        wh = _smoothstep(-1.0, 2.8, B)
        delta += (s["shadows"] / 100) * 1.7 * ws * np.sqrt(ws) + (s["highlights"] / 100) * 1.7 * wh
    if s["clarity"]:
        base = guided(l, max(1.0, 0.012 * ppl), 0.8)
        mid = 1 - np.clip(np.abs(l + ev + 1.0) / 4.0, 0, 1)  # más en medios tonos
        delta += (s["clarity"] / 100) * 0.9 * np.clip(l - base, -1.5, 1.5) * (0.35 + 0.65 * mid)
    if s["texture"]:
        fine = l - cv2.GaussianBlur(l, (0, 0), max(0.6, 0.0018 * ppl))
        delta += (s["texture"] / 100) * 1.1 * np.clip(fine, -1, 1) * 0.6
    gain = (2 ** (ev + delta)).astype(np.float32)
    c = c * gain[..., None]
    if s["dehaze"]:
        k = s["dehaze"] / 100
        dark = _gauss(c.min(-1), max(1.0, 0.02 * ppl))
        air = max(float(np.percentile(E._luma(c[::4, ::4]), 99.5)), 1e-3)
        if k > 0:
            t = np.maximum(1 - 0.9 * k * dark / air, 0.12)[..., None]
            c = np.maximum((c - air * (1 - t)) / t, 0)
        else:
            c = c * (1 + 0.35 * k) - 0.35 * k * air
    # curva base sobre la luminancia, conservando las proporciones RGB (finish.rs:544-557)
    xs, ys = _tonemap_lut(s["contrast"], s["whites"], s["blacks"])
    Yc = np.maximum(E._luma(c), 1e-9)
    idx = np.clip((np.log2(Yc / 0.18) + 14.0) * ((len(ys) - 1) / 24.0), 0, len(ys) - 1)
    o = ys[(idx + 0.5).astype(np.int32)]  # tabla de 4096 puntos en 24 EV: ~0,006 EV por paso
    d = c * (o / Yc)[..., None]
    mx = d.max(-1)
    t = np.clip((mx - 1) / np.maximum(mx - o, 1e-6), 0, 1)[..., None]  # desatura hacia blanco lo que se sale
    d = d + (o[..., None] - d) * np.where(mx[..., None] > 1, t, 0)
    return np.clip(d, 0, 1).astype(np.float32)


def presence_stage(y: np.ndarray, s: dict) -> np.ndarray:
    """(Claridad, textura y neblina ya van en tone_stage, como en LightCraft.)"""
    return y


def color_stage(d: np.ndarray, s: dict) -> np.ndarray:
    """Color en OkLCh sobre lineal de pantalla: mezclador HSL → intensidad → saturación → gradación.
    Devuelve sRGB codificado 0..1 con la curva paramétrica aplicada."""
    hsl_on = any(s[f"hsl_{b}_{p}"] for b, _, _ in HSL_BANDS for p in "hsl")
    grade_on = any(s[k] for k in ("grade_sh_sat", "grade_mid_sat", "grade_hi_sat"))
    if s["vibrance"] or s["saturation"] or hsl_on or grade_on:
        lab = to_oklab(np.maximum(d, 0))
        L, A, Bc = lab[..., 0], lab[..., 1], lab[..., 2]
        C = np.hypot(A, Bc)
        H = np.arctan2(Bc, A)
        if hsl_on:  # partición de la unidad con coseno alzado entre bandas vecinas (colorops.rs:32-49)
            order = np.argsort(_BAND_H)
            cs = np.array(_BAND_H)[order]
            ext = np.concatenate([cs, [cs[0] + 2 * math.pi]])
            hh = (H - cs[0]) % (2 * math.pi) + cs[0]
            idx = np.clip(np.searchsorted(ext, hh, side="right") - 1, 0, len(cs) - 1)
            t = (hh - ext[idx]) / (ext[idx + 1] - ext[idx])
            sm = 0.5 - 0.5 * np.cos(np.pi * t)
            vals = {pp: np.array([s[f"hsl_{HSL_BANDS[o][0]}_{pp}"] for o in order], np.float32) / 100 for pp in "hsl"}
            nxt = (idx + 1) % len(cs)
            dh = (1 - sm) * vals["h"][idx] + sm * vals["h"][nxt]
            ds = (1 - sm) * vals["s"][idx] + sm * vals["s"][nxt]
            dl = (1 - sm) * vals["l"][idx] + sm * vals["l"][nxt]
            cw = np.minimum(C / 0.12, 1)
            H = H + dh * 0.5 * cw
            C = C * np.maximum(0, 1 + ds)
            L = L + dl * 0.18 * cw * np.sqrt(np.maximum(L, 0.05))
        if s["vibrance"]:
            v = s["vibrance"] / 100
            low = 1 - np.clip(C / 0.22, 0, 1)
            skin = 1 - 0.6 * np.exp(-(_wrap(H - _SKIN_H) / 0.35) ** 2) if v > 0 else 1.0
            C = C * np.maximum(0, 1 + v * low ** 2 * skin * 1.2)
        if s["saturation"]:
            C = C * max(0.0, 1 + s["saturation"] / 100)
        A, Bc = C * np.cos(H), C * np.sin(H)
        if grade_on:  # colorops.rs:252-266
            m = 0.5 - 0.25 * s["grade_balance"] / 100
            width = 0.15 + 0.5 * s["grade_blend"] / 100
            w_sh = 1 - _smoothstep(m - width, m + 0.25 * width, L)
            w_hi = _smoothstep(m - 0.25 * width, m + width, L)
            w_mid = np.maximum(0, 1 - w_sh - w_hi)
            for wt, hk, sk in ((w_sh, "grade_sh_hue", "grade_sh_sat"), (w_mid, "grade_mid_hue", "grade_mid_sat"),
                               (w_hi, "grade_hi_hue", "grade_hi_sat")):
                if s[sk]:
                    hh = _oklch_hue_of_hsv(s[hk])
                    k = s[sk] / 100 * 0.09
                    A = A + wt * k * math.cos(hh)
                    Bc = Bc + wt * k * math.sin(hh)
        d = np.clip(from_oklab(np.stack([L, A, Bc], -1)), 0, None)
        mx = d.max(-1, keepdims=True)
        d = np.where(mx > 1, d / mx, d)  # gamut simple
    y = _SRGB_LUT[(np.clip(d, 0, 1) * (len(_SRGB_LUT) - 1) + 0.5).astype(np.int32)]
    if any(s[c] for c in ("curve_highlights", "curve_lights", "curve_darks", "curve_shadows")):
        lut = _param_curve_lut(s)
        y = lut.astype(np.float32)[(np.clip(y, 0, 1) * (len(lut) - 1) + 0.5).astype(np.int32)]
    return np.clip(y, 0, 1).astype(np.float32)


# Los 18 presets originales de LightCraft (crates/engine/src/presets.rs, Apache-2.0), en controles de PhootFlow.
# Temperatura en K → deslizador relativo: (K − 5500) / 40 (aprox.).
def _p(**kw):
    return kw


LIGHTCRAFT_PRESETS = {
    "Warm Glow": _p(temp=43, tint=8, contrast=8, shadows=12, vibrance=18),
    "Cool Morning": _p(temp=2, tint=-4, highlights=-20, vibrance=10, grade_sh_hue=215, grade_sh_sat=12),
    "Teal & Orange": _p(grade_sh_hue=195, grade_sh_sat=28, grade_hi_hue=35, grade_hi_sat=22, grade_balance=10,
                        vibrance=12, contrast=14),
    "Vivid Pop": _p(vibrance=40, saturation=8, contrast=18, whites=10, blacks=-10, clarity=12),
    "Soft Pastel": _p(contrast=-25, highlights=-30, shadows=35, blacks=20, saturation=-18, vibrance=10,
                      curve_shadows=25),
    "Faded Matte": _p(curve_shadows=40, curve_highlights=-15, contrast=-10, saturation=-12, grain=18),
    "Warm Film": _p(temp=35, tint=5, curve_shadows=22, grade_hi_hue=45, grade_hi_sat=15, grain=22, vignette=-12),
    "Muted Film": _p(saturation=-25, vibrance=5, contrast=10, curve_shadows=18, grain=15),
    "High Contrast B&W": _p(bw=True, contrast=45, whites=20, blacks=-25, clarity=20),
    "Soft B&W": _p(bw=True, contrast=-15, shadows=25, curve_shadows=15),
    "Selenium Tone": _p(bw=True, contrast=20, grade_sh_hue=285, grade_sh_sat=18, grade_hi_hue=40, grade_hi_sat=10),
    "Crisp Landscape": _p(highlights=-45, shadows=30, contrast=12, clarity=22, dehaze=12, texture=15, vibrance=25),
    "Golden Hour": _p(temp=52, tint=12, highlights=-35, shadows=20, grade_hi_hue=38, grade_hi_sat=25, vignette=-15),
    "Blue Hour Boost": _p(temp=-8, tint=6, shadows=25, hsl_blue_s=25, hsl_purple_s=15),
    "Soft Skin": _p(texture=-25, clarity=-10, contrast=-5, shadows=15, hsl_orange_s=-8, hsl_orange_l=10),
    "Bright & Airy": _p(contrast=-15, highlights=-40, shadows=40, whites=15, vibrance=8, saturation=-8),
    "Moody": _p(contrast=20, highlights=-30, blacks=-15, saturation=-20, grade_sh_hue=200, grade_sh_sat=15,
                vignette=-30),
    "Cinematic": _p(curve_shadows=20, curve_highlights=-10, grade_sh_hue=190, grade_sh_sat=25, grade_hi_hue=30,
                    grade_hi_sat=18, contrast=15, vignette=-20),
}


def detail_stage(y: np.ndarray, s: dict) -> np.ndarray:
    h, w = y.shape[:2]
    scale = max(h, w) / 2048.0
    if s["nr_color"] or s["nr_luma"]:
        ycc = cv2.cvtColor(y, cv2.COLOR_RGB2YCrCb)
        if s["nr_color"]:
            r = int(2 + 6 * s["nr_color"] / 100 * scale) * 2 + 1
            for i in (1, 2):
                ycc[..., i] = cv2.bilateralFilter(ycc[..., i], r, 0.05 + 0.1 * s["nr_color"] / 100, r)
        if s["nr_luma"]:
            hval = 1.0 + 9.0 * s["nr_luma"] / 100.0
            ycc[..., 0] = E._nlm(ycc[..., 0], hval, 11 if scale < 1.5 else 15)
        y = np.clip(cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB), 0, 1)
    if s["sharpen"]:
        rad = s["sharpen_radius"] * scale
        lum = 0.2126 * y[..., 0] + 0.7152 * y[..., 1] + 0.0722 * y[..., 2]
        det = lum - cv2.GaussianBlur(lum, (0, 0), max(0.3, rad))
        y = np.clip(y + (det * s["sharpen"] / 100.0 * 1.2)[..., None], 0, 1)
    return y


def effects_stage(y: np.ndarray, s: dict, seed: int = 0) -> np.ndarray:
    h, w = y.shape[:2]
    if s["vignette"]:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)) / math.sqrt(2)
        f = 1 + (s["vignette"] / 100.0) * 0.8 * _smooth(r, 0.35, 1.0)
        y = np.clip(y * f[..., None], 0, 1)
    if s["grain"]:
        rng = np.random.default_rng(seed)
        g = cv2.GaussianBlur(rng.standard_normal((h, w)).astype(np.float32), (0, 0), 0.7 * max(h, w) / 2048 + 0.3)
        g /= g.std() + 1e-6
        lum = 0.2126 * y[..., 0] + 0.7152 * y[..., 1] + 0.0722 * y[..., 2]
        y = np.clip(y + (g * 0.04 * s["grain"] / 100.0 * (1 - np.abs(lum - 0.5) * 1.2))[..., None], 0, 1)
    return y


def hue_of(rgb01) -> tuple[float, float]:
    """(tono OkLCh en grados, croma) de un color sRGB 0..1: lo que guarda el cuentagotas del color selectivo."""
    lab = to_oklab(_lin(np.asarray(rgb01, np.float32).reshape(1, 3)))[0]
    return float(math.degrees(math.atan2(lab[2], lab[1])) % 360), float(math.hypot(lab[1], lab[2]))


def hue_rgb(deg: float) -> tuple[int, int, int]:
    """Color de muestra (sRGB 0..255) de un tono OkLCh, para pintar los colores elegidos en la interfaz."""
    h = math.radians(deg)
    d = from_oklab(np.array([[0.68, 0.15 * math.cos(h), 0.15 * math.sin(h)]], np.float32))
    d = np.clip(d / max(1.0, float(d.max())), 0, 1)
    return tuple(int(round(v * 255)) for v in _srgb(d)[0])


def band_hue(hsv_deg: float) -> float:
    """Tono OkLCh (grados) de un tono HSV: el de cada botón de color (rojo, naranja…)."""
    return math.degrees(_oklch_hue_of_hsv(hsv_deg)) % 360


def splash_alpha(y: np.ndarray, s: dict, key: str = "") -> np.ndarray:
    """Alfa 0..1 de lo que se queda en color: los tonos elegidos (con amplitud y suavidad, sin grises ni blancos)
    y/o una máscara de Studio."""
    import masks as M
    s = dict(DEFAULTS, **s)
    mode = s.get("splash_mode") or "color"
    h, w = y.shape[:2]
    a = None
    if mode in ("color", "both"):
        a = np.zeros((h, w), np.float32)
        hues = [float(x) for x in s.get("splash_hues") or []]
        if hues:
            lab = to_oklab(_lin(np.clip(y, 0, 1)))
            C = np.hypot(lab[..., 1], lab[..., 2])
            H = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360
            rng = float(s["splash_range"])
            soft = float(s["splash_soft"]) / 100
            inner, outer = rng * (1 - 0.6 * soft), rng * (1 + 0.8 * soft) + 2
            for hh in hues:
                d = np.abs((H - hh + 180) % 360 - 180)
                a = np.maximum(a, 1 - _smoothstep(inner, outer, d))
            a *= _smoothstep(0.018, 0.018 + 0.03 * (0.4 + soft), C)  # los grises y blancos no tienen color
            if s.get("splash_skin", True):  # la piel (tono 40–65°, poco saturada) pasa a B/N aunque se elija rojo
                skin = np.exp(-((((H - 54) + 180) % 360 - 180) / 13) ** 2) * (1 - _smoothstep(0.105, 0.145, C))
                a *= 1 - skin
            r = max(1.0, max(h, w) / 1400)
            a = np.clip(guided(a.astype(np.float32), 2.5 * r, 0.01), 0, 1)  # sin ruido ni bordes dentados
    if mode in ("mask", "both"):
        ms = s.get("masks") or []
        i = int(s.get("splash_mask") or 0)
        if 0 <= i < len(ms):
            try:
                ma = M.build(ms[i], y, key)
            except Exception:
                ma = np.ones((h, w), np.float32)
        else:
            ma = np.zeros((h, w), np.float32) if mode == "mask" else np.ones((h, w), np.float32)
        a = ma if a is None else a * ma
    return np.clip(a, 0, 1).astype(np.float32)


def splash_stage(y: np.ndarray, s: dict, seed: int = 0, key: str = "", view: bool = False) -> np.ndarray:
    """Color selectivo: B/N (el estilo elegido) en todo menos en lo seleccionado. `view` = ver la selección."""
    a = splash_alpha(y, s, key)
    keep = y
    if s.get("splash_sat"):
        lab = to_oklab(_lin(np.clip(y, 0, 1)))
        lab[..., 1:] *= max(0.0, 1 + s["splash_sat"] / 100)
        d = np.clip(from_oklab(lab), 0, None)
        keep = np.clip(_srgb(d / np.maximum(1, d.max(-1, keepdims=True))), 0, 1).astype(np.float32)
    bw = E.bw_editorial(y, seed=seed, style=s.get("bw_style") or "Editorial")
    k = float(s.get("splash_amount", 100)) / 100
    rest = y * (1 - k) + bw * k
    out = keep * a[..., None] + rest * (1 - a[..., None])
    if view:  # lo que se queda, a todo color; lo que pasa a B/N, tapado en magenta
        import masks as M
        out = M.overlay(1 - a, out)
    return out.astype(np.float32)


def render(lin: np.ndarray, s: dict, seed: int = 0, crop: bool = True, key: str = "",
           show_mask: int | None = None) -> np.ndarray:
    """Revelado completo. `lin`: sRGB lineal float32 (de engine.load_linear). Devuelve sRGB 0..1.
    `key` identifica la foto (caché de las máscaras de IA); `show_mask`: índice de máscara a superponer."""
    import masks as M
    s = dict(DEFAULTS, **s)
    y = tone_stage(lin, s)
    y = presence_stage(y, s)
    y = color_stage(y, s)
    y = detail_stage(y, s)
    if s.get("masks"):
        y = M.apply_all(y, s["masks"], key)
    if s.get("lut"):
        try:
            y = L.apply(y, s["lut"], s.get("lut_strength", 100) / 100.0)
        except Exception:
            pass
    if s.get("splash"):  # color selectivo: manda sobre el B/N de toda la foto
        y = splash_stage(y, s, seed, key, view=bool(s.get("_splash_view")))
    elif s["bw"]:
        y = E.bw_editorial(y, seed=seed, style=s.get("bw_style") or "Editorial")
    y = effects_stage(y, s, seed)
    if show_mask is not None and 0 <= show_mask < len(s.get("masks") or []):
        y = M.overlay(M.build(s["masks"][show_mask], y, key), y)
    if s["angle"]:
        y = E.rotate(y, s["angle"])
    if crop and s["crop"] != [0.0, 0.0, 1.0, 1.0]:
        hh, ww = y.shape[:2]
        y = _cut(y, fit_ratio(s["crop"], s.get("crop_ratio"), ww, hh))
    return np.clip(y, 0, 1).astype(np.float32)


def _cut(y: np.ndarray, rect) -> np.ndarray:
    x0, y0, x1, y1 = rect
    hh, ww = y.shape[:2]
    return y[int(y0 * hh):max(int(y0 * hh) + 1, int(y1 * hh)), int(x0 * ww):max(int(x0 * ww) + 1, int(x1 * ww))]


def fit_ratio(crop, name, w: int, h: int) -> list:
    """Recorte ajustado a su proporción exacta en píxeles de una foto w×h, sin salirse y con el mismo centro.

    El recorte se guarda en fracciones; si después cambia el giro, la foto girada cambia de proporción y el
    recorte dejaría de ser 4:5, 16:9… Solo toca proporciones «a:b» (Libre, Personalizado y Original se respetan).
    """
    crop = list(crop)
    if not name or ":" not in str(name) or w <= 0 or h <= 0:
        return crop
    try:
        a, b = str(name).split(":")
        v = float(a) / float(b)
    except ValueError:
        return crop
    x0, y0, x1, y1 = crop
    cw, ch = (x1 - x0) * w, (y1 - y0) * h
    if cw <= 0 or ch <= 0:
        return crop
    cur = cw / ch
    if abs(cur - 1 / v) < abs(cur - v):
        v = 1 / v  # la misma proporción en vertical
    if abs(cur - v) / v < 0.005:
        return crop
    nw, nh = (ch * v, ch) if cur > v else (cw, cw / v)
    cx, cy = (x0 + x1) / 2 * w, (y0 + y1) / 2 * h
    return [(cx - nw / 2) / w, (cy - nh / 2) / h, (cx + nw / 2) / w, (cy + nh / 2) / h]


def export_events(ph: E.Photo, s: dict, st: E.Settings, out_dir: str, version: str = "") -> list[str]:
    """Exporta una foto retocada en Studio igual que Events: su recorte de Studio en «Entrega/Color» (o en
    «Blanco y negro» si en Studio es B/N), los demás formatos en «Formatos» y la versión B/N en «Entrega»."""
    lin = E.load_linear(ph.path, half=False, rebuild_highlights=True)
    full = render(lin, s, seed=hash(os.path.basename(ph.path)) & 0xFFFF, crop=False, key=ph.path + "#full")
    del lin
    H, W = full.shape[:2]
    exif = E.build_exif(ph, version) if st.keep_exif else b""
    own = s.get("crop_ratio") or "Original"
    main = _cut(full, fit_ratio(s["crop"], own, W, H))
    written = E.save_delivery(main, out_dir, ph.name, bool(s.get("bw")), st, exif)
    wanted = ["Original"] if st.export_original_ratio and s["crop"] != [0.0, 0.0, 1.0, 1.0] else []
    wanted += [r for r in st.ratios if r in ph.crops and r != own and r not in wanted]
    for r in wanted:
        rect = [0.0, 0.0, 1.0, 1.0] if r == "Original" else fit_ratio(ph.crops[r], r, W, H)
        tag = "original" if r == "Original" else E._tag(r)
        written += E.save_sizes(_cut(full, rect), os.path.join(out_dir, E.FORMATS_DIR, E._tag(r)),
                                f"{ph.name}_{tag}", st, exif)
    style = E.photo_bw_style(ph, st)
    if st.bw and not s.get("bw") and not st.style.get("bw") and style != E.BW_NONE:
        del full
        bw = E.bw_editorial(main, seed=ph.dhash & 0xFFFFFFFF, style=style)
        written += E.save_delivery(bw, out_dir, ph.name, True, st, exif, sharpen=False)
    return written


# ------------------------------------------------------------------ auto y puentes con Events

def auto_settings(ph: E.Photo) -> dict:
    """Ajuste automático de partida a partir del análisis de Events (exposición en caras, giro, recorte)."""
    s = new_settings(exposure=round(float(ph.ev), 2), angle=round(float(ph.angle), 1))
    if ph.crops and ph.best in ph.crops:
        s["crop"] = list(ph.crops[ph.best])
        s["crop_ratio"] = ph.best
    s["highlights"] = -25 if ph.clipped > 0.01 else -10
    s["shadows"] = 15
    s["nr_luma"] = int(np.clip(ph.noise_y * 255 * 6, 0, 60)) if ph.noise_y else 15
    s["nr_color"] = 35
    return s


def from_style(base: dict, style: dict) -> dict:
    """Convierte un preset/estilo de Events en ajustes de Studio (aproximado)."""
    s = dict(base)
    sty = dict(S.BASE, **style)
    s["contrast"] = int(np.clip((sty["contrast"] - 0.28) * 250, -100, 100))
    s["saturation"] = int(np.clip((sty["saturation"] - 1.0) * 200, -100, 100))
    s["temp"] = int(np.clip(sty.get("warmth", 0) * 6, -100, 100))
    s["exposure"] = round(s["exposure"] + float(sty.get("ev_bias", 0)), 2)
    s["bw"] = bool(sty.get("bw"))
    return s


# ------------------------------------------------------------------ guardado

def settings_dir(folder: str) -> str:
    return os.path.join(folder, ".phootflow", "studio")


def load(folder: str, photo_path: str) -> dict | None:
    p = os.path.join(settings_dir(folder), os.path.basename(photo_path) + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            return dict(DEFAULTS, **json.load(f))
    except (OSError, ValueError):
        return None


def save(folder: str, photo_path: str, s: dict) -> None:
    d = settings_dir(folder)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, os.path.basename(photo_path) + ".json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    os.replace(p + ".tmp", p)


def user_presets_dir(cfg_dir: str) -> str:
    return os.path.join(cfg_dir, "presets_studio")


def list_presets(cfg_dir: str) -> dict[str, dict]:
    out = {}
    d = user_presets_dir(cfg_dir)
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        out[f[:-5]] = json.load(fh)
                except (OSError, ValueError):
                    pass
    return out


PRESET_KEYS_EXCLUDED = {"crop", "crop_ratio", "angle", "rating", "flag", "exposure"}


def save_preset(cfg_dir: str, name: str, s: dict) -> str:
    d = user_presets_dir(cfg_dir)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "".join(c if c.isalnum() or c in " -_" else "_" for c in name) + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in s.items() if k not in PRESET_KEYS_EXCLUDED}, f, ensure_ascii=False, indent=1)
    return p


def export(photo_path: str, s: dict, out_path: str, quality: int = 95, long_side: int | None = None,
           upscale: float = 1.0, progress=None) -> str:
    """Revela y guarda. `upscale` > 1 amplía con IA (Real-ESRGAN, fx_engine) para copias grandes."""
    lin = E.load_linear(photo_path, half=False, rebuild_highlights=True)
    img = render(lin, s, seed=hash(os.path.basename(photo_path)) & 0xFFFF, key=photo_path + "#full")
    if upscale and upscale > 1:
        import fx_engine as FX
        if not FX.upscaler_ready():
            FX.ensure_upscaler()
        img = FX.upscale(np.clip(img, 0, 1).astype(np.float32), upscale, True, progress)
    if long_side and max(img.shape[:2]) > long_side:
        f = long_side / max(img.shape[:2])
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    ph = E.Photo(path=photo_path, name=os.path.splitext(os.path.basename(photo_path))[0])
    E.read_meta(ph)
    E._save(np.clip(img, 0, 1), out_path, quality, E.build_exif(ph, "studio"))
    return out_path
