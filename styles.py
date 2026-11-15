"""Estilos de PhootFlow: presets por tipo de evento, detección automática del tipo y estilos aprendidos.

Un estilo es un diccionario de parámetros que usa `engine.develop`:
  ev_bias      EV que se suma a la exposición automática
  contrast     peso de la curva en S (0 = plano, 0.5 = muy contrastado)
  black        punto negro (0.035 = el de siempre; más alto = negros más densos)
  saturation   multiplicador de croma
  warmth/tint  desplazamiento en Lab de b (cálido +) y a (magenta +)
  sharpen      nitidez de detalle
  nr           multiplicador de la reducción de grano
  bw           True: el resultado principal sale en blanco y negro editorial
  lut          (aprendidos) curva de luminosidad L 0..100 de 256 puntos
  chroma       (aprendidos) multiplicador de croma medido en tus ediciones

Los estilos aprendidos se guardan como JSON en %APPDATA%/PhootFlow/estilos/.
"""
from __future__ import annotations

import json
import os
import re

import cv2
import numpy as np
from PIL import Image, ImageOps

AUTO = "Automático (detectar)"
BASE = {"ev_bias": 0.0, "contrast": 0.28, "black": 0.035, "saturation": 1.08, "warmth": 0.0, "tint": 0.0,
        "sharpen": 0.55, "nr": 1.0, "bw": False}

PRESETS = {
    "Boda": dict(BASE, ev_bias=0.15, contrast=0.20, black=0.025, saturation=1.02, warmth=1.5, sharpen=0.45,
                 nr=0.9),
    "Cumpleaños / fiesta": dict(BASE),
    "Discoteca / noche": dict(BASE, ev_bias=-0.10, contrast=0.32, black=0.05, saturation=1.14, sharpen=0.50,
                              nr=1.25),
    "Deporte": dict(BASE, contrast=0.32, black=0.04, saturation=1.10, sharpen=0.75, nr=0.9),
    "Retrato / estudio": dict(BASE, ev_bias=0.05, contrast=0.22, saturation=1.0, warmth=1.0, sharpen=0.40),
    "Exterior / paisaje": dict(BASE, contrast=0.30, saturation=1.15, sharpen=0.60, nr=0.8),
}


def styles_dir(cfg_dir: str) -> str:
    return os.path.join(cfg_dir, "estilos")


def learned(cfg_dir: str) -> dict[str, dict]:
    out = {}
    d = styles_dir(cfg_dir)
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        s = json.load(fh)
                    out["Mi estilo: " + s.get("name", f[:-5])] = dict(BASE, **s.get("params", {}))
                except (OSError, ValueError):
                    pass
    return out


def all_styles(cfg_dir: str) -> dict[str, dict]:
    return {**PRESETS, **learned(cfg_dir)}


# ------------------------------------------------------------------ detección del tipo de sesión

def scene_features(rgb: np.ndarray, ph) -> dict:
    """Rasgos baratos de una foto ya revelada a baja resolución (RGB 0..1)."""
    small = cv2.resize(rgb, (320, int(320 * rgb.shape[0] / rgb.shape[1])), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor((np.clip(small, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
    h, s, v = hsv[..., 0], hsv[..., 1] / 255, hsv[..., 2] / 255
    exp = _num(ph.meta.get("exposure"))
    return {
        "lights": float(((v > 0.55) & (s > 0.6)).mean()),             # focos de colores saturados
        "green": float(((h > 33) & (h < 85) & (s > 0.25) & (v > 0.2)).mean()),
        "white": float(((v > 0.8) & (s < 0.12)).mean()),              # vestidos, manteles, estudio
        "dark": float((v < 0.12).mean()),
        "sat": float(s.mean()),
        "exposure": exp or 0.0,
        "focal": _num(ph.meta.get("focal")) or 0.0,
        "iso": float(ph.iso or 0),
        "ev_raw": float(ph.ev_raw),
        "faces": float(ph.faces),
    }


def _num(v) -> float | None:
    if v is None:
        return None
    t = str(v).strip("[] ").split(",")[0]
    try:
        if "/" in t:
            a, b = t.split("/")
            return float(a) / float(b)
        return float(t)
    except (ValueError, ZeroDivisionError):
        return None


def scene_scores(f: dict) -> dict[str, float]:
    """Puntuación de cada tipo para una foto. Heurística: EXIF + contenido."""
    sc = {k: 0.0 for k in PRESETS}
    fast = 0 < f["exposure"] <= 1 / 500
    tele = f["focal"] >= 85
    dark_scene = f["ev_raw"] > 2.0 or f["iso"] >= 3200
    sc["Discoteca / noche"] += 2.0 * dark_scene + 25 * f["lights"] + 1.5 * (f["dark"] > 0.45)
    sc["Deporte"] += 2.0 * fast + 1.0 * tele + 6 * f["green"] - 0.4 * max(0, f["faces"] - 3)
    sc["Boda"] += 12 * f["white"] + 0.8 * (not dark_scene) + 0.5 * (f["faces"] >= 2)
    sc["Cumpleaños / fiesta"] += 1.2 * (f["iso"] >= 1600) + 0.8 * (f["faces"] >= 2) + 0.5 * (not fast)
    sc["Retrato / estudio"] += 1.5 * (f["faces"] == 1) + 6 * f["white"] * (f["faces"] >= 1) + 0.5 * (f["iso"] <= 800)
    sc["Exterior / paisaje"] += 1.5 * (f["faces"] == 0) + 0.8 * (f["iso"] <= 400) + 4 * f["green"] + 2 * f["sat"]
    return sc


def detect_scene(photos) -> tuple[str, dict[str, float]]:
    """Tipo de sesión por mayoría ponderada de todas las fotos analizadas."""
    total = {k: 0.0 for k in PRESETS}
    n = 0
    for p in photos:
        if getattr(p, "feats", None):
            for k, v in scene_scores(p.feats).items():
                total[k] += v
            n += 1
    if not n:
        return "Cumpleaños / fiesta", total
    best = max(total, key=total.get)
    return best, {k: round(v / n, 2) for k, v in total.items()}


# ------------------------------------------------------------------ aplicar un estilo

def apply_color(rgb: np.ndarray, style: dict) -> np.ndarray:
    """Calidez, tinte, croma y curva aprendida (en Lab). RGB 0..1 -> RGB 0..1."""
    lut = style.get("lut")
    target = style.get("target_q")
    chroma = float(style.get("chroma", 1.0))
    warm, tint = float(style.get("warmth", 0.0)), float(style.get("tint", 0.0))
    if lut is None and target is None and abs(warm) < 1e-3 and abs(tint) < 1e-3 and abs(chroma - 1) < 1e-3:
        return rgb
    lab = cv2.cvtColor(np.ascontiguousarray(rgb, dtype=np.float32), cv2.COLOR_RGB2Lab)
    if lut is not None:
        lut = np.asarray(lut, np.float32)
        lab[..., 0] = np.interp(lab[..., 0], np.linspace(0, 100, len(lut)), lut).astype(np.float32)
    if target is not None:  # estilo aprendido solo de fotos acabadas: acercar la distribución, sin forzarla
        k = float(style.get("strength", 0.6))
        sub = lab[::8, ::8]
        src_q = np.percentile(sub[..., 0], np.linspace(0, 100, 256)) + np.arange(256) * 1e-6
        tq = np.asarray(target, np.float32)
        lab[..., 0] = ((1 - k) * lab[..., 0] + k * np.interp(lab[..., 0], src_q, tq)).astype(np.float32)
        c = np.hypot(sub[..., 1], sub[..., 2])
        mid = (sub[..., 0] > 20) & (sub[..., 0] < 85)
        if style.get("target_chroma") and mid.any():
            ratio = np.clip(style["target_chroma"] / max(float(np.median(c[mid])), 1e-3), 0.6, 1.6)
            chroma *= 1 + k * (ratio - 1)
            tint += k * (float(style.get("target_a", 0)) - float(np.median(sub[..., 1][mid])) * chroma)
            warm += k * (float(style.get("target_b", 0)) - float(np.median(sub[..., 2][mid])) * chroma)
            tint, warm = float(np.clip(tint, -12, 12)), float(np.clip(warm, -15, 15))
    lab[..., 1] = lab[..., 1] * chroma + tint
    lab[..., 2] = lab[..., 2] * chroma + warm
    return np.clip(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB), 0, 1)


# ------------------------------------------------------------------ aprender un estilo de tus ediciones

IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def find_pairs(originals_dir: str, edits_dir: str, raw_ext: set) -> list[tuple[str, str]]:
    """Empareja cada original con su edición por nombre (la edición puede llevar sufijos: DSC001-Editar.jpg)."""
    origs = {}
    for f in os.listdir(originals_dir):
        stem, ext = os.path.splitext(f)
        if ext.lower() in raw_ext | IMG_EXT:
            origs.setdefault(stem.lower(), os.path.join(originals_dir, f))
    pairs = []
    for root, _, files in os.walk(edits_dir):
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in IMG_EXT:
                continue
            m = re.match(r"[A-Za-z_]*\d+", stem)
            key = stem.lower() if stem.lower() in origs else (m.group(0).lower() if m else "")
            if key in origs and os.path.join(root, f) != origs[key]:
                pairs.append((origs[key], os.path.join(root, f)))
    return pairs


def _lab_small(rgb: np.ndarray, size: int = 900) -> np.ndarray:
    s = size / max(rgb.shape[:2])
    if s < 1:
        rgb = cv2.resize(rgb, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(np.ascontiguousarray(rgb, dtype=np.float32), cv2.COLOR_RGB2Lab)


def learn_pair(neutral_rgb: np.ndarray, edited_path: str) -> dict:
    """Compara nuestro revelado neutro con tu edición. Por distribuciones (no píxel a píxel): vale con recortes."""
    ed = np.asarray(ImageOps.exif_transpose(Image.open(edited_path)).convert("RGB"), np.float32) / 255.0
    a, b = _lab_small(neutral_rgb), _lab_small(ed)
    qs = np.linspace(0, 100, 256)
    src_q = np.percentile(a[..., 0], qs)
    dst_q = np.percentile(b[..., 0], qs)
    grid = np.linspace(0, 100, 256)
    lut = np.interp(grid, src_q + np.arange(256) * 1e-6, dst_q)
    mid_a = (a[..., 0] > 20) & (a[..., 0] < 85)
    mid_b = (b[..., 0] > 20) & (b[..., 0] < 85)
    ca = np.hypot(a[..., 1], a[..., 2])[mid_a]
    cb = np.hypot(b[..., 1], b[..., 2])[mid_b]
    bw = float(np.percentile(cb, 95)) < 4.0 if cb.size else False
    return {
        "lut": lut,
        "chroma": float(np.median(cb) / max(np.median(ca), 1e-3)) if ca.size and cb.size else 1.0,
        "tint": float(np.median(b[..., 1][mid_b]) - np.median(a[..., 1][mid_a])) if mid_a.any() and mid_b.any() else 0,
        "warmth": float(np.median(b[..., 2][mid_b]) - np.median(a[..., 2][mid_a])) if mid_a.any() and mid_b.any() else 0,
        "bw": bw,
    }


def combine(samples: list[dict]) -> dict:
    """Mediana de todas las parejas: un par raro no estropea el estilo."""
    lut = np.median(np.stack([s["lut"] for s in samples]), axis=0)
    lut = np.maximum.accumulate(np.clip(lut, 0, 100))  # monótona
    color = [s for s in samples if not s["bw"]] or samples
    bw_share = sum(s["bw"] for s in samples) / len(samples)
    return {
        "lut": [round(float(x), 3) for x in lut],
        "chroma": round(float(np.clip(np.median([s["chroma"] for s in color]), 0.3, 2.0)), 3),
        "tint": round(float(np.clip(np.median([s["tint"] for s in color]), -15, 15)), 2),
        "warmth": round(float(np.clip(np.median([s["warmth"] for s in color]), -20, 20)), 2),
        "bw": bw_share > 0.6,
        "contrast": 0.0,  # la curva aprendida ya lleva tu contraste
        "saturation": 1.0,
    }


def save_learned(cfg_dir: str, name: str, base: str, params: dict, n_pairs: int, bw_share: float = 0.0) -> str:
    d = styles_dir(cfg_dir)
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^\w\- ]", "_", name).strip() or "estilo"
    path = os.path.join(d, safe + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "base": base, "pairs": n_pairs, "params": dict(PRESETS.get(base, BASE), **params)},
                  f, ensure_ascii=False, indent=1)
    return path


def learn_style(engine, originals_dir: str, edits_dir: str, cfg_dir: str, name: str, base: str,
                progress=None, limit: int = 300) -> tuple[str, int]:
    """Aprende un estilo de una carpeta de originales y otra con tus ediciones finales."""
    pairs = find_pairs(originals_dir, edits_dir, engine.RAW_EXT)
    if not pairs:
        raise RuntimeError("No encontré parejas original ↔ edición con el mismo nombre de archivo.")
    if len(pairs) > limit:  # con unas cientos basta; se reparten por toda la sesión
        step = len(pairs) / limit
        pairs = [pairs[int(i * step)] for i in range(limit)]
    samples = []
    for i, (orig, edit) in enumerate(pairs):
        try:
            lin = engine.load_linear(orig, half=True)
            ev, _, _ = engine.estimate_ev(lin)
            samples.append(learn_pair(engine.tone(lin, ev), edit))
        except Exception:
            pass
        if progress:
            progress(i + 1, len(pairs))
    if not samples:
        raise RuntimeError("No se pudo leer ninguna pareja.")
    params = combine(samples)
    return save_learned(cfg_dir, name, base, params, len(samples)), len(samples)


def _ratio_name(w: int, h: int, ratios: dict) -> str:
    r = w / h
    best = min(ratios, key=lambda k: abs(ratios[k][0] / ratios[k][1] - r))
    rw, rh = ratios[best]
    return best if abs(rw / rh - r) < 0.02 else "Original"


def learn_from_finals(engine, finals_dir: str, cfg_dir: str, name: str, base: str, progress=None,
                      limit: int = 400) -> tuple[str, int]:
    """Aprende el «aspecto final» solo de fotos ya terminadas (sin originales).

    Guarda la distribución de luminosidad (256 cuantiles de L), croma y color medios, la proporción en B/N
    y los formatos de recorte más usados. Al revelar, cada foto se acerca a ese aspecto (`strength`).
    """
    files = [os.path.join(r, f) for r, _, fs in os.walk(finals_dir) for f in fs
             if os.path.splitext(f)[1].lower() in IMG_EXT]
    if not files:
        raise RuntimeError("No hay JPG/PNG/TIFF en esa carpeta.")
    if len(files) > limit:
        step = len(files) / limit
        files = [files[int(i * step)] for i in range(limit)]
    qs, chroma, a_med, b_med, bw, ratios = [], [], [], [], 0, {}
    for i, f in enumerate(files):
        try:
            im = ImageOps.exif_transpose(Image.open(f)).convert("RGB")
            name_r = _ratio_name(*im.size, engine.RATIOS)
            ratios[name_r] = ratios.get(name_r, 0) + 1
            lab = _lab_small(np.asarray(im, np.float32) / 255.0)
            c = np.hypot(lab[..., 1], lab[..., 2])
            mid = (lab[..., 0] > 20) & (lab[..., 0] < 85)
            if float(np.percentile(c, 95)) < 4.0:
                bw += 1
            else:
                qs.append(np.percentile(lab[..., 0], np.linspace(0, 100, 256)))
                if mid.any():
                    chroma.append(float(np.median(c[mid])))
                    a_med.append(float(np.median(lab[..., 1][mid])))
                    b_med.append(float(np.median(lab[..., 2][mid])))
        except Exception:
            pass
        if progress:
            progress(i + 1, len(files))
    n = len(qs) + bw
    if not n:
        raise RuntimeError("No se pudo leer ninguna foto.")
    params = {
        "target_q": [round(float(x), 3) for x in np.median(np.stack(qs), axis=0)] if qs else None,
        "target_chroma": round(float(np.median(chroma)), 2) if chroma else None,
        "target_a": round(float(np.median(a_med)), 2) if a_med else 0.0,
        "target_b": round(float(np.median(b_med)), 2) if b_med else 0.0,
        "strength": 0.6,
        "bw": bw / n > 0.6,
        "bw_share": round(bw / n, 2),
        "formats": sorted(ratios, key=ratios.get, reverse=True)[:3],
        "contrast": 0.15,
    }
    return save_learned(cfg_dir, name, base, params, n), n


def describe(style: dict) -> str:
    bits = []
    if style.get("lut") is not None:
        bits.append("curva aprendida")
    if abs(style.get("warmth", 0)) >= 0.5:
        bits.append("cálido" if style["warmth"] > 0 else "frío")
    c = style.get("chroma", 1.0) * style.get("saturation", 1.0)
    if abs(c - 1) > 0.05:
        bits.append(f"saturación ×{c:.2f}")
    if style.get("bw"):
        bits.append("blanco y negro")
    if style.get("ev_bias"):
        bits.append(f"{style['ev_bias']:+.2f} EV")
    return ", ".join(bits) or "neutro"

