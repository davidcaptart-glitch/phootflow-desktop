"""Desenfoque de fondo («bokeh» falso) y efecto velocidad (barrido), sin Qt.

- Sujeto (`subject_lowres`): persona con la segmentación de IA de `masks.segment` (MediaPipe, la misma que el fondo
  «persona IA» de Edits); si no hay nadie, objeto con GrabCut iniciado en lo que destaca en el mapa de saliencia de
  `smartcrop.saliency`; o una máscara dada (de Studio). Se calcula a ≤1024 px y se ajusta al borde real (pelo) con un
  filtro guiado rápido: coeficientes a ≤2048 px aplicados a la luminancia a resolución completa, por franjas.
- Fondo: se rellena bajo el sujeto (pull-push) antes de desenfocar, así no queda halo. Profundidad aproximada (más
  lejos del sujeto y más arriba de sus pies = más desenfoque), 4 capas con núcleo de disco en luz lineal y las luces
  brillantes realzadas: salen discos de bokeh, no un gaussiano plano. Se trabaja a ≤1600 px y se amplía por franjas.
- Velocidad: desenfoque de movimiento direccional del fondo (dirección por la forma del sujeto o a mano) y estela
  suave detrás del sujeto; el sujeto queda nítido.

Cantidades 0..100; el radio máximo es 0.03 × lado largo × cantidad/100 (igual en la vista previa y al exportar).
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict

import cv2
import numpy as np

# niveles de la regla de sesión de Events (cantidad de 0..100) y su equivalente aproximado en diafragma
LEVELS = {"Suave": 20, "Medio": 40, "Fuerte": 65, "Muy fuerte": 100}
LEVEL_FSTOP = {"Suave": "f/4", "Medio": "f/2.8", "Fuerte": "f/1.8", "Muy fuerte": "f/1.2"}
SUBJECTS = {"auto": "Automático", "person": "Persona (IA)", "object": "Objeto (saliencia)", "mask": "Una máscara"}
R_MAX = 0.03     # radio de desenfoque máximo (fracción del lado largo) con cantidad 100
SPEED_MAX = 0.12  # longitud del barrido máxima (fracción del lado largo)
MASK_PX, REFINE_PX, WORK_PX, STRIP = 1024, 2048, 1600, 512

_CACHE: OrderedDict = OrderedDict()
_LOCK = threading.Lock()


def _resize_long(img, side):
    h, w = img.shape[:2]
    f = min(1.0, side / max(h, w))
    if f >= 1:
        return np.ascontiguousarray(img, dtype=np.float32)
    return cv2.resize(img, (max(2, round(w * f)), max(2, round(h * f))), interpolation=cv2.INTER_AREA)


def _lum(x):
    return (0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]).astype(np.float32)


def _smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


# ------------------------------------------------------------------ sujeto

def _clean(a: np.ndarray) -> np.ndarray:
    """Quita motas sueltas: solo cuentan las zonas de al menos el 3 % de la mayor."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats((a > 0.5).astype(np.uint8), 8)
    if n <= 1:
        return a * 0
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.isin(lab, 1 + np.flatnonzero(areas >= 0.03 * areas.max())).astype(np.float32)
    keep = cv2.dilate(keep, np.ones((5, 5), np.uint8))
    return (a * cv2.GaussianBlur(keep, (0, 0), 2)).astype(np.float32)


def _person(small, key):
    try:
        import masks as M
        pr = M.segment(small, key + "#bokeh" if key else "")
    except Exception:
        return None
    a = _clean(np.clip(1 - pr[..., 0], 0, 1).astype(np.float32))
    return a if (a > 0.5).mean() > 0.01 else None


def _object(small):
    """GrabCut iniciado en el rectángulo de lo que destaca en la saliencia (coches, objetos, animales)."""
    import smartcrop as SC
    h, w = small.shape[:2]
    sal = cv2.resize(SC.saliency(small), (w, h), interpolation=cv2.INTER_LINEAR)
    m = (sal > 0.45).astype(np.uint8)
    ys, xs = np.nonzero(m)
    if len(xs) < 20:
        return np.zeros((h, w), np.float32)
    x0, x1, y0, y1 = np.percentile(xs, 2), np.percentile(xs, 98), np.percentile(ys, 2), np.percentile(ys, 98)
    px, py = 0.1 * (x1 - x0), 0.1 * (y1 - y0)
    x0, y0 = int(max(1, x0 - px)), int(max(1, y0 - py))
    x1, y1 = int(min(w - 2, x1 + px)), int(min(h - 2, y1 + py))
    f = min(1.0, 640 / max(h, w))
    u8 = (np.clip(cv2.resize(small, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else small, 0, 1)
          * 255).astype(np.uint8)[..., ::-1].copy()
    mask = np.zeros(u8.shape[:2], np.uint8)
    rect = (int(x0 * f), int(y0 * f), max(2, int((x1 - x0) * f)), max(2, int((y1 - y0) * f)))
    try:
        bgd, fgd = np.zeros((1, 65)), np.zeros((1, 65))
        cv2.grabCut(u8, mask, rect, bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
        a = ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)).astype(np.float32)
    except cv2.error:
        a = np.zeros(u8.shape[:2], np.float32)
    a = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
    cov = (a > 0.5).mean()
    if cov < 0.003 or cov > 0.75:  # GrabCut no encontró nada creíble: la saliencia, suavizada
        a = _smoothstep(0.35, 0.6, sal)
    return _clean(cv2.GaussianBlur(a, (0, 0), 1.0))


def subject_lowres(img: np.ndarray, subject: str = "auto", key: str = "", alpha: np.ndarray | None = None):
    """Alfa del sujeto a ≤MASK_PX (float32 0..1). `alpha`: máscara dada (subject="mask"), de cualquier tamaño."""
    small = _resize_long(np.clip(img, 0, 1), MASK_PX)
    h, w = small.shape[:2]
    if subject == "mask" and alpha is not None:
        return cv2.resize(np.asarray(alpha, np.float32), (w, h), interpolation=cv2.INTER_AREA)
    ck = (key, subject, (h, w))
    if key:
        with _LOCK:
            if ck in _CACHE:
                return _CACHE[ck]
    a = None
    if subject in ("auto", "person"):
        a = _person(small, key)
    if a is None:
        a = _object(small)
    if key:
        with _LOCK:
            _CACHE[ck] = a
            while len(_CACHE) > 12:
                _CACHE.popitem(last=False)
    return a


def _guided_coeffs(img, low_alpha):
    """Filtro guiado rápido: coeficientes (A, B) a ≤REFINE_PX con la luminancia como guía."""
    g = _lum(_resize_long(np.clip(img, 0, 1), REFINE_PX))
    h, w = g.shape
    p = cv2.resize(low_alpha, (w, h), interpolation=cv2.INTER_LINEAR)
    r = max(2.0, 0.004 * max(h, w))
    mg, mp_ = cv2.GaussianBlur(g, (0, 0), r), cv2.GaussianBlur(p, (0, 0), r)
    cov = cv2.GaussianBlur(g * p, (0, 0), r) - mg * mp_
    var = cv2.GaussianBlur(g * g, (0, 0), r) - mg * mg
    A = cov / (var + 1e-3)
    B = mp_ - A * mg
    return cv2.GaussianBlur(A, (0, 0), r), cv2.GaussianBlur(B, (0, 0), r)


def _up_strip(src, W, H, y0, y1):
    """Filas y0..y1 de `src` ampliado a (W, H), igual que cv2.resize lineal pero por franjas."""
    h, w = src.shape[:2]
    sx, sy = w / W, h / H
    M = np.float32([[sx, 0, 0.5 * sx - 0.5], [0, sy, (y0 + 0.5) * sy - 0.5]])
    return cv2.warpAffine(src, M, (W, y1 - y0), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


def subject_alpha(img, low_alpha) -> np.ndarray:
    """Alfa a resolución completa con el borde ajustado a la foto (pelo)."""
    A, B = _guided_coeffs(img, low_alpha)
    H, W = img.shape[:2]
    out = np.empty((H, W), np.float32)
    for y0 in range(0, H, STRIP):
        y1 = min(H, y0 + STRIP)
        g = _lum(np.clip(img[y0:y1], 0, 1))
        out[y0:y1] = np.clip(_up_strip(A, W, H, y0, y1) * g + _up_strip(B, W, H, y0, y1), 0, 1)
    return out


# ------------------------------------------------------------------ relleno y profundidad

def fill_hole(img: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Pull-push: rellena `hole` (1 = falta) con el color de alrededor, de lo grueso a lo fino."""
    w = (1 - np.clip(hole, 0, 1)).astype(np.float32)
    nums, dens = [img * w[..., None]], [w]
    while min(dens[-1].shape) > 4:
        nums.append(cv2.pyrDown(nums[-1]))
        dens.append(cv2.pyrDown(dens[-1]))
    est = nums[-1].sum((0, 1)) / max(dens[-1].sum(), 1e-6)
    cur = np.broadcast_to(est, nums[-1].shape).astype(np.float32)
    for num, den in zip(reversed(nums), reversed(dens)):
        if cur.shape[:2] != den.shape:
            cur = cv2.resize(cur, (den.shape[1], den.shape[0]), interpolation=cv2.INTER_LINEAR)
        k = np.clip(den * 4, 0, 1)[..., None]
        cur = (num / np.maximum(den, 1e-6)[..., None]) * k + cur * (1 - k)
    return (img * w[..., None] + cur * (1 - w[..., None])).astype(np.float32)


def depth_map(a: np.ndarray) -> np.ndarray:
    """0.25 (junto al sujeto, a la altura de sus pies) … 1 (lejos y arriba)."""
    h, w = a.shape
    sub = (a > 0.5).astype(np.uint8)
    ys = np.nonzero(sub.any(1))[0]
    foot = float(ys[-1]) if len(ys) else float(h - 1)
    if sub.any():
        dist = cv2.distanceTransform(1 - sub, cv2.DIST_L2, 3) / (0.35 * max(h, w))
    else:
        dist = np.ones((h, w), np.float32)
    yy = np.arange(h, dtype=np.float32)[:, None]
    v = np.where(yy <= foot, (foot - yy) / max(foot, 1), 0.6 * (yy - foot) / max(h - 1 - foot, 1))
    d = 0.25 + 0.75 * np.clip(0.55 * np.clip(dist, 0, 1) + 0.45 * v, 0, 1)
    return cv2.GaussianBlur(d.astype(np.float32), (0, 0), max(2.0, 0.02 * max(h, w)))


def _disk(r: float) -> np.ndarray:
    n = int(math.ceil(r))
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1].astype(np.float32)
    k = np.clip(r + 0.5 - np.hypot(xx, yy), 0, 1)  # borde con antialias
    return k / k.sum()


def _shoulder(x):
    return np.where(x > 0.8, 0.8 + 0.2 * (1 - np.exp(-(x - 0.8) / 0.2)), x)


# ------------------------------------------------------------------ desenfoque de fondo

def _composite(img, alpha_low, bg_small):
    """Sujeto nítido (alfa refinado a resolución completa) sobre el fondo trabajado a baja resolución."""
    A, B = _guided_coeffs(img, alpha_low)
    H, W = img.shape[:2]
    out = np.empty_like(img, dtype=np.float32)
    for y0 in range(0, H, STRIP):
        y1 = min(H, y0 + STRIP)
        src = np.clip(img[y0:y1], 0, 1)
        a = np.clip(_up_strip(A, W, H, y0, y1) * _lum(src) + _up_strip(B, W, H, y0, y1), 0, 1)[..., None]
        out[y0:y1] = src * a + _up_strip(bg_small, W, H, y0, y1) * (1 - a)
    return out


def _prepare(img, subject, key, alpha):
    low = subject_lowres(img, subject, key, alpha)
    small = _resize_long(np.clip(img, 0, 1), WORK_PX)
    h, w = small.shape[:2]
    a_s = cv2.resize(low, (w, h), interpolation=cv2.INTER_LINEAR)
    grow = max(2, int(0.008 * max(h, w)))
    hole = cv2.GaussianBlur(cv2.dilate((a_s > 0.08).astype(np.float32), np.ones((2 * grow + 1,) * 2, np.uint8)),
                            (0, 0), 1.0)
    return low, small, a_s, fill_hole(small, hole)


def bokeh(img: np.ndarray, amount: float, subject: str = "auto", key: str = "", alpha: np.ndarray | None = None,
          highlights: float = 50) -> np.ndarray:
    """Fondo desenfocado con discos de bokeh y el sujeto nítido. `img`: sRGB 0..1 float32."""
    if amount <= 0:
        return img
    low, small, a_s, filled = _prepare(img, subject, key, alpha)
    h, w = small.shape[:2]
    rmax = max(1.0, R_MAX * amount / 100 * max(h, w))
    lin = np.power(filled, 2.2)
    boost = 1 + 6 * highlights / 100 * _smoothstep(0.72, 0.97, _lum(filled))
    x = (lin * boost[..., None]).astype(np.float32)
    radii = [rmax * f for f in (0.25, 0.5, 0.75, 1.0)]
    layers = [cv2.filter2D(x, -1, _disk(r), borderType=cv2.BORDER_REFLECT) if r >= 0.7 else x for r in radii]
    t = np.clip((depth_map(a_s) - 0.25) / 0.25, 0, 3)
    bg = np.zeros_like(x)
    for i in range(4):
        wgt = np.clip(1 - np.abs(t - i), 0, 1)[..., None]
        bg += layers[i] * wgt
    bg = np.power(np.clip(_shoulder(bg), 0, 1), 1 / 2.2).astype(np.float32)
    return _composite(img, low, bg)


# ------------------------------------------------------------------ velocidad (barrido)

def auto_angle(a: np.ndarray) -> float:
    """Dirección del movimiento en grados (0 = hacia la derecha, 90 = hacia arriba): el eje largo del sujeto si
    es alargado y casi horizontal; si no, horizontal. Sentido: hacia el lado con más espacio libre."""
    m = cv2.moments((a > 0.5).astype(np.uint8), True)
    if m["m00"] < 10:
        return 0.0
    h, w = a.shape
    mu20, mu02, mu11 = m["mu20"] / m["m00"], m["mu02"] / m["m00"], m["mu11"] / m["m00"]
    theta = 0.5 * math.degrees(math.atan2(2 * mu11, mu20 - mu02))  # eje mayor, con y hacia abajo
    common = math.sqrt(4 * mu11 ** 2 + (mu20 - mu02) ** 2)
    l1, l2 = (mu20 + mu02 + common) / 2, max((mu20 + mu02 - common) / 2, 1e-6)
    ang = -theta if (l1 / l2 > 1.9 and abs(theta) < 40) else 0.0
    if m["m10"] / m["m00"] > w / 2:  # el sujeto a la derecha va hacia la izquierda
        ang += 180
    return float(ang % 360)


def _line_kernel(length, ang, one_sided=False):
    n = max(1, int(math.ceil(length)))
    k = np.zeros((2 * n + 1, 2 * n + 1), np.float32)
    dx, dy = math.cos(math.radians(ang)), -math.sin(math.radians(ang))
    ts = np.linspace(0 if one_sided else -length, length, max(3, int(4 * length)))
    for t in ts:
        wgt = (1 - t / (length + 1e-6)) if one_sided else 1.0
        x, y = n + t * dx, n + t * dy
        x0, y0 = int(math.floor(x)), int(math.floor(y))
        fx, fy = x - x0, y - y0
        for xx, yy, ww in ((x0, y0, (1 - fx) * (1 - fy)), (x0 + 1, y0, fx * (1 - fy)),
                           (x0, y0 + 1, (1 - fx) * fy), (x0 + 1, y0 + 1, fx * fy)):
            if 0 <= xx < k.shape[1] and 0 <= yy < k.shape[0]:
                k[yy, xx] += ww * wgt
    return k / max(k.sum(), 1e-6)


def speed(img: np.ndarray, amount: float, angle: float | None = None, trail: float = 50, subject: str = "auto",
          key: str = "", alpha: np.ndarray | None = None) -> np.ndarray:
    """Barrido: fondo con desenfoque de movimiento (simétrico, como al seguir al sujeto con la cámara) y estela
    detrás del sujeto. `angle` None = automático. Devuelve sRGB 0..1."""
    if amount <= 0:
        return img
    low, small, a_s, filled = _prepare(img, subject, key, alpha)
    h, w = small.shape[:2]
    ang = auto_angle(a_s) if angle is None else float(angle)
    L = max(1.0, SPEED_MAX * amount / 100 * max(h, w))
    lin = np.power(filled, 2.2).astype(np.float32)
    bg = cv2.filter2D(lin, -1, _line_kernel(L, ang), borderType=cv2.BORDER_REFLECT)
    if trail > 0:  # estela: lo que hay delante (el sujeto) arrastrado hacia atrás, cada vez más tenue
        k = _line_kernel(0.6 * L, ang, one_sided=True)
        src = np.power(small, 2.2).astype(np.float32)
        cov = cv2.filter2D(a_s, -1, k, borderType=cv2.BORDER_CONSTANT)
        col = cv2.filter2D(src * a_s[..., None], -1, k, borderType=cv2.BORDER_CONSTANT) / np.maximum(cov, 1e-4)[..., None]
        ta = (np.clip(cov, 0, 1) * 0.75 * trail / 100)[..., None]
        bg = bg * (1 - ta) + col * ta
    bg = np.power(np.clip(bg, 0, 1), 1 / 2.2).astype(np.float32)
    return _composite(img, low, bg)


# ------------------------------------------------------------------ Events

def events_eligible(objects: list, face_boxes: list) -> bool:
    """¿Sujeto claro? Retratos y parejas (1–2 personas o caras de buen tamaño); no grupos grandes ni paisajes."""
    persons = [o for o in objects or [] if o and o[0] == "persona" and o[1] >= 0.5]
    big = [p for p in persons if (p[4] - p[2]) * (p[5] - p[3]) >= 0.04]
    faces = [f for f in face_boxes or [] if (f[2] - f[0]) >= 0.05]
    if len(big) >= 4 or len(faces) >= 4:
        return False
    return 1 <= len(big) <= 2 or 1 <= len(faces) <= 2
