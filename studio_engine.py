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
DEFAULTS.update({"bw": False, "crop": [0.0, 0.0, 1.0, 1.0], "crop_ratio": "Original", "lut": "", "lut_strength": 100,
                 "rating": 0, "flag": 0, "profile": "Neutro"})


def new_settings(**kw) -> dict:
    s = copy.deepcopy(DEFAULTS)
    s.update(kw)
    return s


# ------------------------------------------------------------------ utilidades de color

def _srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


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

def tone_stage(lin: np.ndarray, s: dict) -> np.ndarray:
    """Luz (lineal → sRGB perceptual 0..1): BB, exposición, blancos/negros, altas luces/sombras, contraste."""
    x = lin * _wb_gains(s["temp"], s["tint"])[None, None, :]
    x = x * np.float32(2.0 ** s["exposure"])
    # altas luces y sombras: ajuste local con una base suavizada de la luminancia (sin halos grandes)
    lum = np.maximum(E._luma(x), 1e-6)
    if s["highlights"] or s["shadows"]:
        lg = np.log2(lum)
        small = cv2.resize(lg, None, fx=0.125, fy=0.125, interpolation=cv2.INTER_AREA)
        base = cv2.resize(cv2.GaussianBlur(small, (0, 0), 6), (lg.shape[1], lg.shape[0]),
                          interpolation=cv2.INTER_LINEAR)
        hi = _smooth(base, -2.5, 0.0)   # zonas claras
        sh = 1 - _smooth(base, -6.0, -2.5)  # zonas oscuras
        d = (s["highlights"] / 100.0) * 1.6 * hi + (s["shadows"] / 100.0) * 1.8 * sh
        x = x * (2 ** d)[..., None]
    # hombro suave (como el motor de Events) y paso a sRGB
    a = 0.6 + 0.2 * (s["whites"] / 100.0)
    y = np.where(x < a, x, a + (1 - a) * (1 - np.exp(-(x - a) / max(1 - a, 1e-3))))
    y = _srgb(y)
    # blancos y negros: puntos extremos
    w = 1.0 - 0.12 * (s["whites"] / 100.0)
    b = -0.06 * (s["blacks"] / 100.0)
    y = (y - b) / max(w - b, 1e-3)
    # contraste: curva S alrededor del gris medio perceptual
    k = s["contrast"] / 100.0
    if k:
        yc = np.clip(y, 0, 1)
        sc = yc * yc * (3 - 2 * yc)
        y = y + (sc - yc) * k * (1.0 if k > 0 else 0.8)
    # curva tonal paramétrica (cuatro regiones)
    if any(s[c] for c in ("curve_highlights", "curve_lights", "curve_darks", "curve_shadows")):
        yy = np.clip(y, 0, 1)
        bump = lambda c, w0: np.exp(-((yy - c) ** 2) / (2 * w0 * w0))  # noqa: E731
        y = y + 0.12 * (s["curve_shadows"] / 100 * bump(0.12, 0.09) + s["curve_darks"] / 100 * bump(0.35, 0.11)
                        + s["curve_lights"] / 100 * bump(0.62, 0.11) + s["curve_highlights"] / 100 * bump(0.88, 0.09))
    return np.clip(y, 0, 1).astype(np.float32)


def presence_stage(y: np.ndarray, s: dict) -> np.ndarray:
    """Textura, claridad y neblina sobre sRGB 0..1."""
    h, w = y.shape[:2]
    scale = max(h, w) / 2048.0
    lum = (0.2126 * y[..., 0] + 0.7152 * y[..., 1] + 0.0722 * y[..., 2]).astype(np.float32)
    if s["dehaze"]:
        k = s["dehaze"] / 100.0
        dark = cv2.erode(y.min(-1), np.ones((15, 15), np.uint8))
        air = float(np.percentile(lum, 99.5))
        t = np.clip(1 - 0.85 * k * dark / max(air, 1e-3), 0.25, 1.6)[..., None] if k > 0 else 1 - 0.3 * k
        y = np.clip((y - air * (1 - t)) / t, 0, 1) if k > 0 else np.clip(y * (1 + 0.3 * k) - 0.3 * k * air, 0, 1)
        lum = (0.2126 * y[..., 0] + 0.7152 * y[..., 1] + 0.0722 * y[..., 2]).astype(np.float32)
    d = np.zeros_like(lum)
    if s["clarity"]:
        base = cv2.GaussianBlur(lum, (0, 0), 18 * scale + 1)
        mid = 1 - np.abs(lum - 0.5) * 2
        d += (lum - base) * (s["clarity"] / 100.0) * 0.9 * np.clip(mid * 1.5, 0, 1)
    if s["texture"]:
        base = cv2.GaussianBlur(lum, (0, 0), 3 * scale + 0.6)
        d += (lum - base) * (s["texture"] / 100.0) * 1.2
    if d.any():
        y = np.clip(y + d[..., None], 0, 1)
    return y


def color_stage(y: np.ndarray, s: dict) -> np.ndarray:
    """Intensidad, saturación, HSL por bandas y gradación de color (en HSV/Lab sobre sRGB 0..1)."""
    hsl_on = any(s[f"hsl_{b}_{p}"] for b, _, _ in HSL_BANDS for p in "hsl")
    if s["vibrance"] or s["saturation"] or hsl_on:
        hsv = cv2.cvtColor(np.clip(y, 0, 1).astype(np.float32), cv2.COLOR_RGB2HSV)  # H 0..360
        hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        sat = sat * (1 + s["saturation"] / 100.0)
        if s["vibrance"]:
            v = s["vibrance"] / 100.0
            skin = _hue_weights(hue, 25, 30) * 0.5  # protege la piel, como Lightroom
            sat = sat * (1 + v * (1 - np.clip(sat, 0, 1)) * (1 - skin))
        if hsl_on:
            dh, ds, dl = np.zeros_like(hue), np.zeros_like(hue), np.zeros_like(hue)
            for b, _, c in HSL_BANDS:
                ph, ps, pl = s[f"hsl_{b}_h"], s[f"hsl_{b}_s"], s[f"hsl_{b}_l"]
                if ph or ps or pl:
                    wgt = _hue_weights(hue, c) * np.clip(sat * 3, 0, 1)
                    dh += wgt * ph / 100.0 * 30
                    ds += wgt * ps / 100.0
                    dl += wgt * pl / 100.0
            hue = (hue + dh) % 360
            sat = sat * (1 + ds)
            val = val * (1 + 0.5 * dl)
        hsv = np.stack([hue, np.clip(sat, 0, 1), np.clip(val, 0, 1)], -1).astype(np.float32)
        y = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    if any(s[k] for k in ("grade_sh_sat", "grade_mid_sat", "grade_hi_sat")):
        lum = 0.2126 * y[..., 0] + 0.7152 * y[..., 1] + 0.0722 * y[..., 2]
        bal = s["grade_balance"] / 100.0 * 0.25
        blend = 0.15 + 0.35 * s["grade_blend"] / 100.0
        w_sh = 1 - _smooth(lum, 0.0, 0.45 + bal + blend * 0.3)
        w_hi = _smooth(lum, 0.55 + bal - blend * 0.3, 1.0)
        w_mid = np.clip(1 - w_sh - w_hi, 0, 1)
        out = y.copy()
        for wgt, hk, sk in ((w_sh, "grade_sh_hue", "grade_sh_sat"), (w_mid, "grade_mid_hue", "grade_mid_sat"),
                            (w_hi, "grade_hi_hue", "grade_hi_sat")):
            if s[sk]:
                tint = cv2.cvtColor(np.array([[[s[hk], 1.0, 1.0]]], np.float32), cv2.COLOR_HSV2RGB)[0, 0]
                tint = tint - tint.mean()
                out = out + wgt[..., None] * tint[None, None, :] * (s[sk] / 100.0) * 0.25
        y = out
    return np.clip(y, 0, 1).astype(np.float32)


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


def render(lin: np.ndarray, s: dict, seed: int = 0, crop: bool = True) -> np.ndarray:
    """Revelado completo. `lin`: sRGB lineal float32 (de engine.load_linear). Devuelve sRGB 0..1."""
    s = dict(DEFAULTS, **s)
    y = tone_stage(lin, s)
    y = presence_stage(y, s)
    y = color_stage(y, s)
    y = detail_stage(y, s)
    if s.get("lut"):
        try:
            y = L.apply(y, s["lut"], s.get("lut_strength", 100) / 100.0)
        except Exception:
            pass
    if s["bw"]:
        y = E.bw_editorial(y, seed=seed)
    y = effects_stage(y, s, seed)
    if s["angle"]:
        y = E.rotate(y, s["angle"])
    if crop and s["crop"] != [0.0, 0.0, 1.0, 1.0]:
        x0, y0, x1, y1 = s["crop"]
        hh, ww = y.shape[:2]
        y = y[int(y0 * hh):max(int(y0 * hh) + 1, int(y1 * hh)), int(x0 * ww):max(int(x0 * ww) + 1, int(x1 * ww))]
    return np.clip(y, 0, 1).astype(np.float32)


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


def export(photo_path: str, s: dict, out_path: str, quality: int = 95, long_side: int | None = None) -> str:
    lin = E.load_linear(photo_path, half=False, rebuild_highlights=True)
    img = render(lin, s, seed=hash(os.path.basename(photo_path)) & 0xFFFF)
    if long_side and max(img.shape[:2]) > long_side:
        f = long_side / max(img.shape[:2])
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    ph = E.Photo(path=photo_path, name=os.path.splitext(os.path.basename(photo_path))[0])
    E.read_meta(ph)
    E._save(np.clip(img, 0, 1), out_path, quality, E.build_exif(ph, "studio"))
    return out_path
