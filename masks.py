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
- "point"   punto de control (como U Point de Nik): params cx, cy (0..1), size (alcance 1..100 % del lado
            largo), sim (0..100: cuánto se tiene que parecer el color al del punto). Coge lo que está cerca
            y se parece en color, sin seleccionar a mano.
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
# partes calculadas a partir de la malla facial (no son un polígono fijo)
FACE_EXTRA = {"beard": "Barba"}
OTHER_TYPES = {"sky": "Cielo (aprox.)"}
# contorno de la cara de la malla de MediaPipe (en orden, empezando por la frente)
FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
             176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]
NOSE_LOW = [1, 4, 48, 64, 98, 97, 2, 326, 327, 294, 278]  # punta y aletas: los orificios no son barba
MANUAL_TYPES = {"radial": "Radial", "linear": "Lineal", "brush": "Pincel", "luma": "Rango de luz",
                "color": "Rango de color", "point": "Punto de control (U Point)"}
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
         "color": dict(hue=25, range=30, sat_min=15),
         "point": dict(cx=0.5, cy=0.5, size=25, sim=50)}.get(kind, {})
    name = (AI_TYPES.get(kind) or (FACE_PARTS[kind][0] if kind in FACE_PARTS else None)
            or FACE_EXTRA.get(kind) or OTHER_TYPES.get(kind) or MANUAL_TYPES.get(kind, kind))
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


def face_axes(pts: np.ndarray) -> dict:
    """Ejes de una cara a partir de su malla: centro de los ojos `E`, boca `M`, dirección horizontal `u` y
    vertical `v` (hacia la barbilla), ancho de la cara `fw` y distancia ojos-boca `d` (píxeles)."""
    E = (pts[33] + pts[263]) / 2
    M = (pts[61] + pts[291]) / 2
    u = pts[263] - pts[33]
    u = u / (np.linalg.norm(u) + 1e-6)
    v = np.array([-u[1], u[0]], np.float32)
    if np.dot(M - E, v) < 0:
        v = -v
    return {"E": E, "M": M, "u": u, "v": v, "fw": float(np.linalg.norm(pts[454] - pts[234])) + 1e-6,
            "d": float(np.linalg.norm(M - E)) + 1e-6}


def beard_alpha(base: np.ndarray, pts: np.ndarray, hair: np.ndarray | None = None) -> np.ndarray:
    """Barba y bigote de una cara. Zona: la parte baja de la cara (de la mitad entre ojos y boca hacia abajo,
    algo más ancha que la mandíbula para coger el volumen) menos labios y nariz, y, si hay segmentación, una
    franja bajo la barbilla para las barbas largas. Dentro, lo que parece pelo: más oscuro y menos coloreado
    que la piel de los pómulos, con textura de pelo o marcado como pelo por el modelo. Solo cuenta lo que
    toca la barbilla o el bigote (un mechón de la melena junto a la mejilla no es barba). Una cara afeitada
    da casi 0. Todo se calcula en la caja de la cara."""
    h, w = base.shape[:2]
    out = np.zeros((h, w), np.float32)
    ax = face_axes(pts)
    u, v, fw, d = ax["u"], ax["v"], ax["fw"], ax["d"]
    oval = pts[FACE_OVAL]
    c = oval.mean(0)
    grow = np.where((np.dot(oval - c, v) > 0)[:, None], 1.16, 1.04)  # la barba abulta bajo la mandíbula
    big = c + (oval - c) * grow
    chin = pts[152]
    # franja de barba larga: de la mandíbula hacia el pecho (solo con el modelo, que distingue pelo y ropa)
    jl, jr = pts[172], pts[397]
    jw = float(np.linalg.norm(jr - jl)) * 0.6
    mid = (jl + jr) / 2
    long_poly = np.array([mid - u * jw, mid + u * jw, chin + v * 2.4 * d + u * jw * 0.55,
                          chin + v * 2.4 * d - u * jw * 0.55], np.float32)  # se estrecha hacia abajo
    allp = big if long_poly is None else np.vstack([big, long_poly])
    pad = int(0.08 * fw) + 4
    x0, y0 = max(0, int(allp[:, 0].min()) - pad), max(0, int(allp[:, 1].min()) - pad)
    x1, y1 = min(w, int(allp[:, 0].max()) + pad), min(h, int(allp[:, 1].max()) + pad)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return out
    o = np.array([x0, y0], np.float32)
    ch, cw = y1 - y0, x1 - x0
    region = np.zeros((ch, cw), np.float32)
    cv2.fillPoly(region, [np.round(big - o).astype(np.int32)], 1.0, cv2.LINE_AA)
    cut = ax["E"] + v * d * 0.5 - o  # por arriba, la línea media entre ojos y boca
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    t = (xx - cut[0]) * v[0] + (yy - cut[1]) * v[1]
    region *= np.clip(t / (0.12 * d) + 0.5, 0, 1)
    hole = np.zeros((ch, cw), np.uint8)
    cv2.fillPoly(hole, [np.round(pts[FACE_PARTS["lips"][1][0]] - o).astype(np.int32)], 1, cv2.LINE_AA)
    cv2.fillConvexPoly(hole, cv2.convexHull(np.round(pts[NOSE_LOW] - o).astype(np.int32)), 1, cv2.LINE_AA)
    k = max(1, int(0.025 * fw))
    hole = cv2.dilate(hole, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    holef = cv2.GaussianBlur(hole.astype(np.float32), (0, 0), max(0.6, k * 0.6))
    region *= 1 - holef
    lab = cv2.cvtColor(np.ascontiguousarray(np.clip(base[y0:y1, x0:x1], 0, 1), dtype=np.float32),
                       cv2.COLOR_RGB2Lab)
    # piel de referencia: los pómulos altos y la frente (por encima de cualquier barba)
    r = max(2, int(0.035 * fw))
    ref = []
    for sx, sy in ((-0.24, 0.3), (0.24, 0.3), (0.0, -0.75)):
        q = ax["E"] + u * fw * sx + v * d * sy - o
        x, y = int(q[0]), int(q[1])
        patch = lab[max(0, y - r):max(0, y + r + 1), max(0, x - r):max(0, x + r + 1)].reshape(-1, 3)
        if len(patch):
            ref.append(np.median(patch, 0))
    ref = np.median(np.array(ref), 0) if ref else np.array([60, 15, 18], np.float32)
    L = lab[..., 0]
    dark = np.clip((ref[0] - L - 4.0) / 16.0, 0, 1)
    # una sombra conserva el color de la piel (croma proporcional a la luz); el pelo es más gris o más pardo
    C = np.hypot(lab[..., 1], lab[..., 2])
    cref = float(np.hypot(ref[1], ref[2]))
    cr = C / (cref * np.clip(L / max(float(ref[0]), 1.0), 0.05, 1.5) + 1e-3)
    nonskin = np.clip((0.85 - cr) / 0.35, 0, 1) if cref > 4 else np.ones_like(L)  # en B/N no hay color
    s_tex = max(0.8, 0.004 * fw)
    m1 = cv2.GaussianBlur(L, (0, 0), s_tex)
    tex = np.sqrt(np.maximum(cv2.GaussianBlur(L * L, (0, 0), s_tex) - m1 * m1, 0))
    tex = np.clip((tex - 2.0) / 6.0, 0, 1)
    hairy = np.clip(0.8 * dark * nonskin + 0.5 * tex * (0.3 + 0.7 * nonskin), 0, 1)
    hp = None
    if hair is not None:  # el modelo de segmentación suele dar la barba como pelo
        hp = np.clip(hair[y0:y1, x0:x1] * 1.2 - 0.1, 0, 1)
        hairy = np.maximum(hairy, hp)
    hairy = cv2.morphologyEx(hairy, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    a = region * np.clip(hairy * 1.4, 0, 1)
    sure = a > 0.5
    if sure.sum() > max(30, 0.002 * fw * fw):
        # barba larga: bajo la mandíbula, lo que se parece en color a la barba de la cara y tiene textura
        lz = np.zeros((ch, cw), np.float32)
        cv2.fillPoly(lz, [np.round(long_poly - o).astype(np.int32)], 1.0, cv2.LINE_AA)
        lz = cv2.GaussianBlur(lz, (0, 0), max(1.0, 0.05 * fw))
        lz *= np.clip(t / (0.12 * d) + 0.5, 0, 1) * (1 - holef) * (1 - region)
        mu, sd = lab[sure].mean(0), lab[sure].std(0)
        tol = np.maximum(sd * 1.6, [8.0, 4.0, 4.0])
        de2 = (((lab - mu) / tol) ** 2).sum(-1)
        sim = np.exp(-0.5 * de2 / 3.0)
        cand = sim * np.clip(tex * 1.3, 0, 1)  # la barba tiene mucha más textura que la camisa
        if hp is not None:
            cand = np.maximum(cand, hp)
        a = np.maximum(a, lz * np.clip(cand * 1.6, 0, 1))
    # solo lo conectado con la barbilla o el bigote
    core = np.zeros((ch, cw), np.uint8)
    cv2.circle(core, tuple(int(q) for q in np.round(chin + v * 0.05 * d - o)), max(2, int(0.12 * fw)), 1, -1)
    cv2.circle(core, tuple(int(q) for q in np.round((pts[2] + pts[0]) / 2 - o)), max(2, int(0.06 * fw)), 1, -1)
    n, lab_cc = cv2.connectedComponents((a > 0.3).astype(np.uint8), connectivity=8)
    keep = np.unique(lab_cc[(core > 0) & (lab_cc > 0)])
    conn = np.isin(lab_cc, keep).astype(np.float32)
    conn = cv2.GaussianBlur(cv2.dilate(conn, np.ones((3, 3), np.uint8), iterations=max(1, int(0.01 * fw))),
                            (0, 0), max(1.0, 0.008 * fw))
    a = a * np.clip(conn * 1.5, 0, 1)
    a = cv2.GaussianBlur(a, (0, 0), max(0.8, 0.004 * fw))
    out[y0:y1, x0:x1] = np.clip(a, 0, 1)
    return out


def faces_scaled(base: np.ndarray, key: str = "", work_px: int = 1024) -> list:
    """Mallas faciales detectadas a `work_px` y pasadas a píxeles de `base`."""
    h, w = base.shape[:2]
    f = min(1.0, work_px / max(h, w))
    small = cv2.resize(base, (max(2, int(w * f)), max(2, int(h * f))), interpolation=cv2.INTER_AREA)         if f < 1 else base
    return [p / f for p in face_points(np.ascontiguousarray(small, dtype=np.float32),
                                        key + f"#w{work_px}" if key else "")]


def part_alpha(base: np.ndarray, part: str, key: str = "", work_px: int = 1024) -> np.ndarray:
    """Máscara de una parte de la cara o de la persona al tamaño de `base`, con la detección hecha a
    `work_px` (rápida y la misma en la vista y al exportar). Los polígonos se dibujan a tamaño completo."""
    h, w = base.shape[:2]
    f = min(1.0, work_px / max(h, w))
    small = cv2.resize(base, (max(2, int(w * f)), max(2, int(h * f))), interpolation=cv2.INTER_AREA)         if f < 1 else base
    sk = key + f"#w{work_px}" if key else ""
    if part in AI_TYPES:
        pr = segment(np.ascontiguousarray(small, dtype=np.float32), sk)
        a = {"person": 1 - pr[..., 0], "face": pr[..., 3], "skin": pr[..., 2] + pr[..., 3], "hair": pr[..., 1],
             "clothes": pr[..., 4], "background": pr[..., 0]}[part]
        a = np.ascontiguousarray(np.clip(a, 0, 1), dtype=np.float32)
        if f < 1:
            a = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
        return _refine(a, base)
    faces = faces_scaled(base, key, work_px)
    a = np.zeros((h, w), np.float32)
    hair = None
    if part == "beard" and faces:
        try:
            hr = np.ascontiguousarray(segment(np.ascontiguousarray(small, dtype=np.float32), sk)[..., 1])
            hair = cv2.resize(hr, (w, h), interpolation=cv2.INTER_LINEAR) if f < 1 else hr
        except Exception:
            hair = None
    for pts in faces:
        if part == "beard":
            if len(pts) >= 468:
                a = np.maximum(a, beard_alpha(base, pts, hair))
            continue
        if part not in FACE_PARTS or (len(pts) < 478 and part == "iris"):
            continue
        m = np.zeros((h, w), np.float32)
        for poly in FACE_PARTS[part][1]:
            cv2.fillPoly(m, [np.round(pts[poly]).astype(np.int32)], 1.0, cv2.LINE_AA)
        a = np.maximum(a, cv2.GaussianBlur(m, (0, 0), max(0.6, float(np.ptp(pts[:, 0])) * 0.012)))
    return a


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
    elif t == "point":
        a = point_alpha(base, p)
    elif t in AI_TYPES:
        pr = segment(base, key)
        a = {"person": 1 - pr[..., 0], "face": pr[..., 3], "skin": pr[..., 2] + pr[..., 3], "hair": pr[..., 1],
             "clothes": pr[..., 4], "background": pr[..., 0]}[t]
        a = _refine(np.clip(a, 0, 1).astype(np.float32), base)
    elif t in FACE_PARTS:
        a = _face_part(base, t, key)
    elif t in FACE_EXTRA:
        a = part_alpha(base, t, key)
    elif t == "sky":
        a = _sky(base)
    else:
        a = np.zeros((h, w), np.float32)
    if m.get("invert"):
        a = 1 - a
    return np.clip(a * (m.get("amount", 100) / 100), 0, 1).astype(np.float32)


def point_alpha(base: np.ndarray, p: dict) -> np.ndarray:
    """Punto de control: peso espacial (gaussiana de alcance `size`) × parecido de color en Lab con el color
    medio alrededor del punto. Se calcula a 1024 px y se amplía: igual en la vista y al exportar."""
    h, w = base.shape[:2]
    f = min(1.0, 1024 / max(h, w))
    sm = cv2.resize(base, (max(2, int(w * f)), max(2, int(h * f))), interpolation=cv2.INTER_AREA) if f < 1 else base
    lab = cv2.cvtColor(np.ascontiguousarray(np.clip(sm, 0, 1), dtype=np.float32), cv2.COLOR_RGB2Lab)
    H, W = lab.shape[:2]
    px = int(min(max(p.get("cx", 0.5), 0), 1) * (W - 1))
    py = int(min(max(p.get("cy", 0.5), 0), 1) * (H - 1))
    r = max(1, int(0.006 * max(H, W)))
    ref = lab[max(0, py - r):py + r + 1, max(0, px - r):px + r + 1].reshape(-1, 3).mean(0)
    d = lab - ref
    de = np.sqrt(d[..., 0] ** 2 * 0.5 + d[..., 1] ** 2 + d[..., 2] ** 2)  # ΔE con la luz a medio peso
    sim = float(p.get("sim", 50))
    tol = 4.0 + (100.0 - sim) * 0.55  # sim 100 → solo casi el mismo color; 0 → casi todo
    col = np.clip(1.0 - (de - tol * 0.5) / (tol * 0.5 + 1e-6), 0, 1)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    side = max(H, W)
    sig = max(2.0, float(p.get("size", 25)) / 100.0 * side * 0.5)
    sp = np.exp(-(((xx - px) ** 2 + (yy - py) ** 2) / (2 * sig * sig)))
    a = cv2.GaussianBlur((col * sp).astype(np.float32), (0, 0), max(1.0, 0.003 * side))
    if f < 1:
        a = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(a, 0, 1).astype(np.float32)


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

