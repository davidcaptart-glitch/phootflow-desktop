"""Máscaras de Studio (ajustes locales, como en Lightroom).

Una máscara es un diccionario guardado en los ajustes de la foto (`settings["masks"]`):

    {"name": "Caras", "type": "face", "invert": False, "amount": 100,
     "params": {...según el tipo...}, "adj": {"exposure": 0.3, "shadows": 20, ...}}

Tipos:
- "radial"  params: cx, cy, rx, ry (0..1 sobre la foto), feather (0..100)
- "linear"  params: x0, y0, x1, y1 (de lleno a nada), 0..1
- "brush"   params: strokes = [[x, y, r], ...] (0..1; r relativo al lado largo), feather
- "luma"    params: lo, hi (0..100 de luminosidad), feather
- "color"   params: hue (0..360), range (grados), sat_min (0..100)
- IA (MediaPipe selfie_multiclass, Apache-2.0): "person", "face", "skin", "hair", "clothes", "background"

Todas devuelven un alfa float32 0..1 del tamaño de la imagen (sin girar ni recortar). Los ajustes se
aplican sobre la imagen ya revelada (sRGB 0..1) y se funden con el alfa.
"""
from __future__ import annotations

import hashlib
import os
import threading
import urllib.request

import cv2
import numpy as np

import net

SEG_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/"
           "float32/latest/selfie_multiclass_256x256.tflite")
SEG_SHA256 = "c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0"
SEG_FILE = "selfie_multiclass_256x256.tflite"
# clases del modelo: 0 fondo, 1 pelo, 2 piel del cuerpo, 3 piel de la cara, 4 ropa, 5 otros (accesorios)
AI_TYPES = {"person": "Persona", "face": "Piel de la cara", "skin": "Piel (cara y cuerpo)", "hair": "Pelo",
            "clothes": "Ropa", "background": "Fondo"}
# partes de la cara con los puntos de MediaPipe Face Landmarker (índices de la malla facial de 478 puntos)
FACE_PARTS = {
    "eyes": ("Ojos", [[33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246],
                      [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466]]),
    "iris": ("Iris", [[469, 470, 471, 472], [474, 475, 476, 477]]),
    "brows": ("Cejas", [[70, 63, 105, 66, 107, 55, 65, 52, 53, 46], [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]]),
    "lips": ("Labios", [[61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]]),
    "teeth": ("Dientes", [[78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191]]),
}
OTHER_TYPES = {"sky": "Cielo (aprox.)"}
MANUAL_TYPES = {"radial": "Radial", "linear": "Lineal", "brush": "Pincel", "luma": "Rango de luz",
                "color": "Rango de color"}
ADJ = [("exposure", "Exposición", -3, 3, 0.05, 0), ("contrast", "Contraste", -100, 100, 1, 0),
       ("highlights", "Altas luces", -100, 100, 1, 0), ("shadows", "Sombras", -100, 100, 1, 0),
       ("temp", "Temperatura", -100, 100, 1, 0), ("tint", "Matiz", -100, 100, 1, 0),
       ("saturation", "Saturación", -100, 100, 1, 0), ("clarity", "Claridad", -100, 100, 1, 0),
       ("sharpness", "Nitidez", -100, 100, 1, 0)]

_CFG = {"model_dir": ""}
_TLS = threading.local()
_KEEP: list = []
_SEG_CACHE: dict = {}
_LOCK = threading.Lock()


def _close_all():
    """Cierra los detectores de MediaPipe antes de que Python desmonte el intérprete (si no, protestan)."""
    for d in _KEEP:
        try:
            d.close()
        except Exception:
            pass
    _KEEP.clear()


import atexit  # noqa: E402

atexit.register(_close_all)


def new_mask(kind: str) -> dict:
    p = {"radial": dict(cx=0.5, cy=0.45, rx=0.25, ry=0.3, feather=60),
         "linear": dict(x0=0.5, y0=0.0, x1=0.5, y1=0.45),
         "brush": dict(strokes=[], feather=50),
         "luma": dict(lo=60, hi=100, feather=15),
         "color": dict(hue=25, range=30, sat_min=15)}.get(kind, {})
    name = (AI_TYPES.get(kind) or (FACE_PARTS[kind][0] if kind in FACE_PARTS else None)
            or OTHER_TYPES.get(kind) or MANUAL_TYPES.get(kind, kind))
    return {"name": name, "type": kind, "invert": False, "amount": 100, "params": p,
            "adj": {k: v for k, _, _, _, _, v in ADJ}}


# ------------------------------------------------------------------ IA: segmentación de personas

def configure(model_dir: str) -> None:
    _CFG["model_dir"] = model_dir


def ensure_model(model_dir: str | None = None) -> str:
    d = model_dir or _CFG["model_dir"]
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, SEG_FILE)
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        return path
    data = net.urlopen(urllib.request.Request(SEG_URL, headers={"User-Agent": "PhootFlow"}), 60).read()
    if hashlib.sha256(data).hexdigest() != SEG_SHA256:
        raise RuntimeError("El modelo de segmentación no supera la verificación SHA-256")
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    return path


def _segmenter():
    seg = getattr(_TLS, "seg", None)
    if seg is None:
        import eyes  # reutiliza la carga de mediapipe sin matplotlib
        mp, BaseOptions, vision = eyes._mediapipe()
        seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=ensure_model()), output_confidence_masks=True,
            output_category_mask=False))
        _TLS.seg = seg
        _KEEP.append(seg)
    return seg


def segment(rgb01: np.ndarray, key: str = "") -> np.ndarray:
    """Probabilidad de cada clase (H, W, 6), con caché por foto y tamaño."""
    ck = (key, rgb01.shape[:2])
    with _LOCK:
        if key and ck in _SEG_CACHE:
            return _SEG_CACHE[ck]
    import eyes
    mp = eyes._mediapipe()[0]
    img = mp.Image(image_format=mp.ImageFormat.SRGB,
                   data=np.ascontiguousarray((np.clip(rgb01, 0, 1) * 255).astype(np.uint8)))
    res = _segmenter().segment(img)
    probs = np.stack([m.numpy_view().reshape(rgb01.shape[:2]) for m in res.confidence_masks], -1).astype(np.float32)
    if key:
        with _LOCK:
            _SEG_CACHE[ck] = probs
            while len(_SEG_CACHE) > 8:
                _SEG_CACHE.pop(next(iter(_SEG_CACHE)))
    return probs


_LM_CACHE: dict = {}


def _landmarker():
    lm = getattr(_TLS, "lm", None)
    if lm is None:
        import eyes
        mp, BaseOptions, vision = eyes._mediapipe()
        lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=eyes._cfg["model_path"]),
            running_mode=vision.RunningMode.IMAGE, num_faces=30,
            min_face_detection_confidence=0.3, min_face_presence_confidence=0.3))
        _TLS.lm = lm
        _KEEP.append(lm)
    return lm


def face_points(rgb01: np.ndarray, key: str = "") -> list:
    """Puntos faciales (478 por cara, en píxeles) de todas las caras de la foto, con caché."""
    ck = (key, rgb01.shape[:2])
    with _LOCK:
        if key and ck in _LM_CACHE:
            return _LM_CACHE[ck]
    import eyes
    import engine as E
    mp = eyes._mediapipe()[0]
    img8 = np.ascontiguousarray((np.clip(rgb01, 0, 1) * 255).astype(np.uint8))
    h, w = img8.shape[:2]
    # el detector del landmarker no ve caras pequeñas: se localizan con YuNet y se trabaja en un recorte
    boxes = E._faces(cv2.cvtColor(img8, cv2.COLOR_RGB2GRAY))
    faces = []
    for (x, y, fw, fh) in boxes:
        side = int(max(fw, fh) * 1.8)
        cx, cy = x + fw / 2, y + fh / 2
        x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        x1, y1 = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
        crop = img8[y0:y1, x0:x1]
        if min(crop.shape[:2]) < 24:
            continue
        s = 384 / max(crop.shape[:2])
        big = np.ascontiguousarray(cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC))
        res = _landmarker().detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=big))
        for lms in res.face_landmarks[:1]:
            faces.append(np.array([[x0 + p.x * big.shape[1] / s, y0 + p.y * big.shape[0] / s] for p in lms],
                                  np.float32))
    if key:
        with _LOCK:
            _LM_CACHE[ck] = faces
            while len(_LM_CACHE) > 8:
                _LM_CACHE.pop(next(iter(_LM_CACHE)))
    return faces


def _face_part(base: np.ndarray, part: str, key: str) -> np.ndarray:
    h, w = base.shape[:2]
    a = np.zeros((h, w), np.float32)
    for pts in face_points(base, key):
        if len(pts) < 478 and part == "iris":
            continue
        size = float(np.ptp(pts[:, 0])) if len(pts) else 10
        for poly in FACE_PARTS[part][1]:
            cv2.fillPoly(a, [np.round(pts[poly]).astype(np.int32)], 1.0, cv2.LINE_AA)
        a_blur = max(0.6, size * 0.012)
    return cv2.GaussianBlur(a, (0, 0), a_blur) if a.any() else a


def _sky(base: np.ndarray) -> np.ndarray:
    """Cielo aproximado (sin IA): claro o azulado, poca textura, conectado con el borde de arriba."""
    h, w = base.shape[:2]
    hsv = cv2.cvtColor(np.ascontiguousarray(base, dtype=np.float32), cv2.COLOR_RGB2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    blue = np.clip(1 - np.abs(hue - 215) / 45, 0, 1) * np.clip(sat * 3, 0, 1)
    bright = np.clip((val - 0.55) / 0.3, 0, 1) * np.clip(1 - sat * 2.5, 0, 1)
    gray = cv2.cvtColor(np.ascontiguousarray(base, dtype=np.float32), cv2.COLOR_RGB2GRAY)
    tex = cv2.GaussianBlur(np.abs(cv2.Laplacian(gray, cv2.CV_32F)), (0, 0), 3)
    smooth = np.clip(1 - tex / 0.03, 0, 1)
    cand = np.maximum(blue, bright) * smooth
    seed = (cand > 0.4).astype(np.uint8)
    n, lab = cv2.connectedComponents(seed)
    top = set(np.unique(lab[: max(2, h // 20)])) - {0}
    keep = np.isin(lab, list(top)).astype(np.float32) if top else np.zeros((h, w), np.float32)
    return cv2.GaussianBlur(keep * np.clip(cand * 1.5, 0, 1), (0, 0), max(1.5, 0.004 * max(h, w)))


def _refine(alpha: np.ndarray, guide: np.ndarray) -> np.ndarray:
    """Ajusta el borde de la máscara de IA a los bordes de la foto (filtro guiado)."""
    g = (0.2126 * guide[..., 0] + 0.7152 * guide[..., 1] + 0.0722 * guide[..., 2]).astype(np.float32)
    r = max(2.0, 0.004 * max(alpha.shape))
    mean_g, mean_a = cv2.GaussianBlur(g, (0, 0), r), cv2.GaussianBlur(alpha, (0, 0), r)
    cov = cv2.GaussianBlur(g * alpha, (0, 0), r) - mean_g * mean_a
    var = cv2.GaussianBlur(g * g, (0, 0), r) - mean_g * mean_g
    a = cov / (var + 1e-3)
    b = mean_a - a * mean_g
    return np.clip(cv2.GaussianBlur(a, (0, 0), r) * g + cv2.GaussianBlur(b, (0, 0), r), 0, 1)


# ------------------------------------------------------------------ construir el alfa

def build(m: dict, base: np.ndarray, key: str = "") -> np.ndarray:
    """Alfa 0..1 (H, W) de la máscara `m` sobre la imagen `base` (sRGB 0..1, sin girar ni recortar)."""
    h, w = base.shape[:2]
    t, p = m.get("type"), m.get("params", {})
    yy, xx = None, None

    def grid():
        nonlocal yy, xx
        if yy is None:
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            yy /= max(h - 1, 1)
            xx /= max(w - 1, 1)
        return yy, xx

    if t == "radial":
        y, x = grid()
        d = np.hypot((x - p["cx"]) / max(p["rx"], 1e-3), (y - p["cy"]) / max(p["ry"], 1e-3) * (h / w) * (w / h))
        f = max(0.02, p.get("feather", 50) / 100)
        a = 1 - np.clip((d - (1 - f)) / f, 0, 1)
        a = a * a * (3 - 2 * a)
    elif t == "linear":
        y, x = grid()
        vx, vy = p["x1"] - p["x0"], p["y1"] - p["y0"]
        L2 = vx * vx + vy * vy + 1e-9
        tt = ((x - p["x0"]) * vx + (y - p["y0"]) * vy) / L2
        a = 1 - np.clip(tt, 0, 1)
        a = a * a * (3 - 2 * a)
    elif t == "brush":
        a = np.zeros((h, w), np.float32)
        side = max(h, w)
        for st in p.get("strokes", []):
            x, y, r = st[:3]
            val = 0.0 if len(st) > 3 and st[3] < 0 else 1.0  # trazos de borrar
            cv2.circle(a, (int(x * (w - 1)), int(y * (h - 1))), max(1, int(r * side)), val, -1, cv2.LINE_AA)
        f = p.get("feather", 50) / 100
        if f > 0:
            rr = np.mean([s[2] for s in p["strokes"]]) * side if p.get("strokes") else 4
            a = cv2.GaussianBlur(a, (0, 0), max(0.5, rr * f * 0.6))
    elif t == "luma":
        L = cv2.cvtColor(np.ascontiguousarray(base, dtype=np.float32), cv2.COLOR_RGB2Lab)[..., 0]
        lo, hi, f = p["lo"], p["hi"], max(1.0, p.get("feather", 15))
        a = np.clip((L - (lo - f)) / f, 0, 1) * np.clip(((hi + f) - L) / f, 0, 1)
        a = cv2.GaussianBlur(a.astype(np.float32), (0, 0), max(1.0, 0.002 * max(h, w)))
    elif t == "color":
        hsv = cv2.cvtColor(np.ascontiguousarray(base, dtype=np.float32), cv2.COLOR_RGB2HSV)
        dh = np.abs(((hsv[..., 0] - p["hue"]) + 180) % 360 - 180)
        rng = max(5.0, p.get("range", 30))
        a = np.clip(1 - (dh - rng * 0.6) / (rng * 0.4), 0, 1) * np.clip((hsv[..., 1] * 100 - p.get("sat_min", 15)) / 15, 0, 1)
        a = cv2.GaussianBlur(a.astype(np.float32), (0, 0), max(1.0, 0.002 * max(h, w)))
    elif t in AI_TYPES:
        pr = segment(base, key)
        a = {"person": 1 - pr[..., 0], "face": pr[..., 3], "skin": pr[..., 2] + pr[..., 3], "hair": pr[..., 1],
             "clothes": pr[..., 4], "background": pr[..., 0]}[t]
        a = _refine(np.clip(a, 0, 1).astype(np.float32), base)
    elif t in FACE_PARTS:
        a = _face_part(base, t, key)
    elif t == "sky":
        a = _sky(base)
    else:
        a = np.zeros((h, w), np.float32)
    if m.get("invert"):
        a = 1 - a
    return np.clip(a * (m.get("amount", 100) / 100), 0, 1).astype(np.float32)


# ------------------------------------------------------------------ aplicar los ajustes locales

def _lin(x):
    return np.where(x <= 0.04045, x / 12.92, ((np.clip(x, 0, None) + 0.055) / 1.055) ** 2.4)


def _srgb(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def adjust(y: np.ndarray, adj: dict) -> np.ndarray:
    """La imagen entera con los ajustes de la máscara (luego se funde con su alfa)."""
    out = y
    if adj.get("exposure") or adj.get("temp") or adj.get("tint"):
        lin = _lin(out)
        if adj.get("exposure"):
            lin = lin * (2 ** adj["exposure"])
        if adj.get("temp") or adj.get("tint"):
            t, g = adj.get("temp", 0) / 100, adj.get("tint", 0) / 100
            gains = np.array([2 ** (0.35 * t), 2 ** (-0.25 * g), 2 ** (-0.35 * t)], np.float32)
            lin = lin * (gains / (0.2126 * gains[0] + 0.7152 * gains[1] + 0.0722 * gains[2]))
        a = 0.85
        lin = np.where(lin < a, lin, a + (1 - a) * (1 - np.exp(-(lin - a) / (1 - a))))  # hombro suave
        out = _srgb(lin).astype(np.float32)
    lum = 0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2]
    d = np.zeros_like(lum)
    if adj.get("contrast"):
        d += ((lum * lum * (3 - 2 * lum)) - lum) * adj["contrast"] / 100
    if adj.get("highlights"):
        w = np.clip((lum - 0.45) / 0.55, 0, 1) ** 1.5
        d += w * 0.35 * adj["highlights"] / 100
    if adj.get("shadows"):
        w = np.clip(1 - lum / 0.55, 0, 1) ** 1.5
        d += w * 0.35 * adj["shadows"] / 100
    side = max(out.shape[:2])
    if adj.get("clarity"):
        base = cv2.GaussianBlur(lum, (0, 0), max(2.0, 0.012 * side))
        d += (lum - base) * 0.9 * adj["clarity"] / 100
    if adj.get("sharpness"):
        base = cv2.GaussianBlur(lum, (0, 0), max(0.6, 0.0012 * side))
        d += (lum - base) * 1.4 * adj["sharpness"] / 100
    if d.any():
        out = out + d[..., None]
    if adj.get("saturation"):
        g = (0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2])[..., None]
        out = g + (out - g) * (1 + adj["saturation"] / 100)
    return np.clip(out, 0, 1).astype(np.float32)


def apply_all(y: np.ndarray, masks: list, key: str = "") -> np.ndarray:
    """Aplica todas las máscaras activas, en orden, sobre `y` (sRGB 0..1)."""
    for m in masks or []:
        adj = m.get("adj", {})
        if not any(adj.values()) or not m.get("enabled", True):
            continue
        try:
            a = build(m, y, key)
        except Exception:
            continue
        if not a.any():
            continue
        y = y + (adjust(y, adj) - y) * a[..., None]
    return np.clip(y, 0, 1).astype(np.float32)


def overlay(alpha: np.ndarray, rgb01: np.ndarray) -> np.ndarray:
    """Vista de la máscara: rojo semitransparente encima de la foto (como Lightroom)."""
    red = np.array([0.96, 0.05, 0.54], np.float32)  # magenta PhootFlow
    return np.clip(rgb01 * (1 - 0.55 * alpha[..., None]) + red * 0.55 * alpha[..., None], 0, 1)

