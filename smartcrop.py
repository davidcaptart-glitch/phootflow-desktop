"""Recorte inteligente de Events (sin Qt): qué es lo destacado de cada foto y qué formato le va mejor.

Tres fuentes, todas locales y en CPU:
- Detector de objetos YOLOX-tiny (Megvii, Apache-2.0; ONNX de su release oficial, 20 MB, se descarga la primera
  vez y se comprueba su SHA-256): personas y pelota de deporte (clases 0 y 32 de COCO). ≈90 ms por foto con un
  hilo a 416 px. YOLOv5/v8/11 de Ultralytics son AGPL: descartados.
- Caras de YuNet (las que ya busca `engine.analyze`).
- Mapa de saliencia clásico (residuo espectral de Hou y Zhang en Lab + zonas enfocadas), unos milisegundos.

`analyze` guarda en la foto las cajas (`objects`, `face_boxes`), el mapa (`sal`, 48 px, en base64) y la
proporción del área útil (`aspect`): con eso `plan` rehace los recortes sin volver a abrir la foto cuando ya se
sabe el tipo de la sesión (`engine.smart_crops`). Cada tipo de sesión da peso a lo suyo: en deporte, la pelota
y los jugadores que la disputan; en bodas, la pareja; en el resto, la persona o el grupo principal, y sin nadie,
lo que destaca en el mapa de saliencia.
"""
from __future__ import annotations

import base64
import hashlib
import math
import os
import urllib.request

import cv2
import numpy as np

PERSON, BALL = "persona", "pelota"
_COCO = {0: PERSON, 32: BALL}
_MIN_SCORE = {PERSON: 0.35, BALL: 0.15}  # se guardan flojas; `plan` decide cuáles cuentan

MODEL_URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx"
MODEL_FILE = "yolox_tiny.onnx"
MODEL_SHA256 = "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"
MODEL_SIZE = 20_219_662

SAL_LONG = 48  # lado largo del mapa de saliencia guardado (≈2 KB por foto en analisis.json)


# ------------------------------------------------------------------------------------------- detector

def ensure_model(dest_dir: str, progress=None) -> str:
    """Ruta del modelo YOLOX-tiny; lo descarga y verifica (SHA-256) si falta. Lanza RuntimeError si no cuadra."""
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, MODEL_FILE)
    if os.path.exists(path) and os.path.getsize(path) == MODEL_SIZE:
        return path
    import net
    tmp = path + ".part"
    h = hashlib.sha256()
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "PhootFlow"})
    with net.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
        total = int(r.headers.get("Content-Length") or MODEL_SIZE)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != MODEL_SHA256:
        os.remove(tmp)
        raise RuntimeError("El modelo de objetos descargado no supera la verificación SHA-256")
    os.replace(tmp, path)
    return path


class Detector:
    """YOLOX con onnxruntime. Una sesión compartida: `run` admite llamadas concurrentes desde los hilos del
    análisis, y cada llamada usa un hilo (`threads`) para no pisar a los demás."""

    def __init__(self, model_path: str, threads: int = 1):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
        inp = self.sess.get_inputs()[0]
        self.name = inp.name
        self._grids(int(inp.shape[2]) if isinstance(inp.shape[2], int) else 416)

    def _grids(self, size: int) -> None:
        self.size = size
        grids, strides = [], []
        for s in (8, 16, 32):
            n = self.size // s
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), s))
        self._grid = np.concatenate(grids).astype(np.float32)
        self._stride = np.concatenate(strides).astype(np.float32)

    def detect(self, rgb01: np.ndarray) -> list:
        """[[clase, confianza, x0, y0, x1, y1], …] con las cajas normalizadas 0..1 (personas y pelotas)."""
        h, w = rgb01.shape[:2]
        r = min(self.size / h, self.size / w)
        nw, nh = max(1, int(round(w * r))), max(1, int(round(h * r)))
        im = cv2.resize(np.clip(rgb01, 0, 1), (nw, nh), interpolation=cv2.INTER_AREA)
        pad = np.full((self.size, self.size, 3), 114, np.float32)
        pad[:nh, :nw] = im[..., ::-1] * 255.0  # YOLOX: BGR 0..255 sin normalizar, relleno gris 114
        y = self.sess.run(None, {self.name: np.ascontiguousarray(pad.transpose(2, 0, 1)[None])})[0][0]
        xy = (y[:, :2] + self._grid) * self._stride
        wh = np.exp(np.clip(y[:, 2:4], -10, 10)) * self._stride
        out = []
        for cid, cls in _COCO.items():
            sc = y[:, 4] * y[:, 5 + cid]
            m = sc >= _MIN_SCORE[cls]
            if not m.any():
                continue
            boxes = np.concatenate([xy[m] - wh[m] / 2, wh[m]], 1) / r
            keep = cv2.dnn.NMSBoxes(boxes.tolist(), sc[m].tolist(), _MIN_SCORE[cls], 0.45)
            for i in np.array(keep).ravel()[:30]:
                x, yy, bw, bh = boxes[i]
                b = [max(0.0, x / w), max(0.0, yy / h), min(1.0, (x + bw) / w), min(1.0, (yy + bh) / h)]
                if b[2] > b[0] and b[3] > b[1]:
                    out.append([cls, round(float(sc[m][i]), 3)] + [round(float(v), 4) for v in b])
        return out


# ------------------------------------------------------------------------------------------- saliencia

def _spectral_residual(ch: np.ndarray) -> np.ndarray:
    f = np.fft.fft2(ch)
    amp = np.log(np.abs(f) + 1e-6)
    ph = np.angle(f)
    res = amp - cv2.blur(amp, (3, 3))
    s = np.abs(np.fft.ifft2(np.exp(res + 1j * ph))) ** 2
    return cv2.GaussianBlur(s.astype(np.float32), (0, 0), 2.5)


def saliency(rgb01: np.ndarray) -> np.ndarray:
    """Mapa 0..1 (lado largo SAL_LONG) de lo que llama la atención: contraste espectral de luz y color más
    las zonas enfocadas (en fotos de evento lo enfocado suele ser lo que importa)."""
    h, w = rgb01.shape[:2]
    gw, gh = (SAL_LONG, max(1, round(SAL_LONG * h / w))) if w >= h else (max(1, round(SAL_LONG * w / h)), SAL_LONG)
    x = np.clip(rgb01, 0, 1).astype(np.float32)
    s = 64 / max(h, w)
    small = cv2.resize(x, (max(8, round(w * s)), max(8, round(h * s))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2Lab)
    sr = sum(_spectral_residual(lab[..., i] / d) for i, d in ((0, 100.0), (1, 60.0), (2, 60.0)))
    sr = cv2.resize(sr, (gw, gh), interpolation=cv2.INTER_AREA)
    s = 512 / max(h, w)
    gray = cv2.cvtColor(cv2.resize(x, (max(8, round(w * s)), max(8, round(h * s))), interpolation=cv2.INTER_AREA),
                        cv2.COLOR_RGB2GRAY)
    lap = np.abs(cv2.Laplacian(cv2.GaussianBlur(gray, (0, 0), 0.8), cv2.CV_32F))
    focus = cv2.resize(cv2.GaussianBlur(lap, (0, 0), 6), (gw, gh), interpolation=cv2.INTER_AREA)

    def norm(a):
        a = a - np.percentile(a, 20)
        return np.clip(a / (np.percentile(a, 99) + 1e-6), 0, 1)

    yy, xx = np.mgrid[0:gh, 0:gw]
    center = np.exp(-(((xx + 0.5) / gw - 0.5) ** 2 + ((yy + 0.5) / gh - 0.5) ** 2) / 0.18)
    m = (0.5 * norm(sr) + 0.5 * norm(focus)) * (0.75 + 0.25 * center)
    return (m / (m.max() + 1e-6)).astype(np.float32)


def horizon(gray: np.ndarray) -> float:
    """Altura (0..1) de una línea horizontal larga que cruza la foto (horizonte, mar, fondo de pista), o -1."""
    h, w = gray.shape[:2]
    s = 320 / max(h, w)
    g = cv2.resize(np.asarray(gray, np.float32), (max(8, round(w * s)), max(8, round(h * s))),
                   interpolation=cv2.INTER_AREA)
    gy = np.abs(cv2.Sobel(cv2.GaussianBlur(g, (0, 0), 1.2), cv2.CV_32F, 0, 1))
    thr = max(float(np.percentile(gy, 92)), 0.02)
    frac = (gy > thr).mean(axis=1)
    frac = np.maximum(frac, np.maximum(np.roll(frac, 1), np.roll(frac, -1)))  # líneas de 2-3 px
    n = len(frac)
    lo, hi = int(0.06 * n), int(0.94 * n)
    if hi <= lo:
        return -1.0
    k = lo + int(np.argmax(frac[lo:hi]))
    if frac[k] < 0.55:
        return -1.0
    a, b = k, k  # centro de la meseta (un borde neto marca 2-3 filas)
    while a > lo and frac[a - 1] >= 0.9 * frac[k]:
        a -= 1
    while b < hi - 1 and frac[b + 1] >= 0.9 * frac[k]:
        b += 1
    k = (a + b) // 2 if b - a < n // 10 else k
    up, down = float(g[max(0, k - n // 6):max(1, k - 2)].mean()), float(g[k + 2:k + n // 6 + 2].mean())
    return round(k / n, 4) if abs(up - down) > 0.04 else -1.0


def face_yaw(row) -> float:
    """Hacia dónde gira la cara, con los puntos de YuNet: <0 mira a la izquierda de la foto, >0 a la derecha."""
    try:
        rex, lex, nx = float(row[4]), float(row[6]), float(row[8])
    except (IndexError, TypeError, ValueError):
        return 0.0
    eye = abs(lex - rex)
    return 0.0 if eye < 1e-6 else round(float(np.clip((nx - (rex + lex) / 2) / eye, -1.5, 1.5)), 3)


def encode_map(m: np.ndarray) -> str:
    """Mapa 0..1 -> «alto x ancho:base64» (uint8) para guardarlo en el JSON de la sesión."""
    q = np.clip(np.round(m * 255), 0, 255).astype(np.uint8)
    return f"{q.shape[0]}x{q.shape[1]}:" + base64.b64encode(q.tobytes()).decode("ascii")


def decode_map(s: str) -> np.ndarray | None:
    try:
        dims, data = s.split(":", 1)
        h, w = (int(v) for v in dims.split("x"))
        return np.frombuffer(base64.b64decode(data), np.uint8).reshape(h, w).astype(np.float32) / 255.0
    except (ValueError, AttributeError):
        return None


# ------------------------------------------------------------------------------------------- planificación

# Cuánto se puede acercar (área mínima del recorte) y cuánto aire se deja alrededor de lo importante
RULES = {
    "Deporte": dict(min_area=0.16, pad=0.10),
    "Boda": dict(min_area=0.30, pad=0.16),
    "Retrato / estudio": dict(min_area=0.30, pad=0.14),
    "Cumpleaños / fiesta": dict(min_area=0.30, pad=0.14),
    "Discoteca / noche": dict(min_area=0.30, pad=0.14),
    "Exterior / paisaje": dict(min_area=0.45, pad=0.20),
}
DEFAULT_RULE = dict(min_area=0.28, pad=0.14)
# los formatos más habituales desempatan a favor; 9:16 solo si de verdad le va mejor
PREFERRED = {"4:5": 0.04, "2:3": 0.04, "3:2": 0.04, "5:4": 0.03, "1:1": 0.02, "3:4": 0.01, "4:3": 0.01,
             "9:16": -0.06, "16:9": -0.02}
ORIGINAL = "Original"


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b):
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _body_of(face, persons, A):
    """Cuerpo de la persona de esa cara: su caja de persona si el detector la encontró; si no, de la cabeza a
    medio muslo como estimación (como el recorte de persona de siempre)."""
    fx, fy = (face[0] + face[2]) / 2, (face[1] + face[3]) / 2
    fh = face[3] - face[1]
    own = [p for s, p in persons if p[0] <= fx <= p[2] and p[1] <= fy <= p[3] and (p[3] - p[1]) > 1.5 * fh]
    if own:
        return min(own, key=_area)
    fw = 0.85 * fh
    return (max(0.0, face[0] - 1.1 * fw), max(0.0, face[1] - 0.6 * fh),
            min(A, face[2] + 1.1 * fw), min(1.0, face[3] + 4.5 * fh))


def _people(A, objects, scene):
    """Personas detectadas que cuentan, como (confianza, caja en unidades del alto)."""
    persons = [(o[1], (o[2] * A, o[3], o[4] * A, o[5])) for o in objects if o[0] == PERSON and o[1] >= 0.35]
    tiny = 0.12 if scene == "Deporte" else 0.18  # gente pequeña del fondo (o el conductor de un autobús): no
    return [p for p in persons if p[1][3] - p[1][1] >= tiny]


def _focus(A, faces, objects, scene):
    """Lo destacado de la foto: (caja imprescindible, cajas con peso, explicación, pelota). Cajas en unidades
    del alto (x en 0..A, y en 0..1). Devuelve caja None si no hay nadie: entonces manda la saliencia."""
    persons = _people(A, objects, scene)
    balls = [(o[1], (o[2] * A, o[3], o[4] * A, o[5])) for o in objects if o[0] == BALL]
    faces = [(f[0] * A, f[1], f[2] * A, f[3]) for f in faces]
    weights = [(0.25 * s, p) for s, p in persons] + [(1.5, f) for f in faces]
    if scene == "Deporte":
        ball = None
        for s, b in sorted(balls, key=lambda t: -t[0]):
            bc = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            near = any(_dist(bc, p) <= 0.6 * (p[3] - p[1]) for _, p in persons)
            if s >= 0.30 or (near and s >= 0.15):
                ball = b
                break
        if ball is not None and persons:
            bc = ((ball[0] + ball[2]) / 2, (ball[1] + ball[3]) / 2)
            big = max(p[3] - p[1] for _, p in persons)
            ranked = sorted(persons, key=lambda t: _dist(bc, t[1]) / (t[1][3] - t[1][1]))
            players = [p for s, p in ranked if _dist(bc, p) <= 0.6 * (p[3] - p[1]) and p[3] - p[1] >= 0.35 * big][:3]
            if not players and _dist(bc, ranked[0][1]) <= 1.5 * (ranked[0][1][3] - ranked[0][1][1]):
                players = [ranked[0][1]]
            core = _union([ball] + players)
            n = len(players)
            why = f"pelota y {n} jugador{'es' if n != 1 else ''}" if n else "pelota"
            return core, weights + [(2.0, ball)] + [(1.0, p) for p in players], why, ball
        if ball is not None:
            return ball, weights + [(2.0, ball)], "pelota", ball
        if persons:
            core, n = _main_people(persons)
            return core, weights + [(1.0, core)], "jugador principal" if n == 1 else f"{n} jugadores", None
    if scene == "Boda" and len(faces) >= 2:
        fs = sorted(faces, key=_area, reverse=True)
        a, b = fs[0], fs[1]
        fw = a[2] - a[0]
        gap = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
        if _area(b) >= 0.4 * _area(a) and gap <= 2.5 * fw:
            core = _union([_body_of(a, persons, A), _body_of(b, persons, A)])
            return core, weights + [(1.0, core), (1.5, a), (1.5, b)], "la pareja", None
    if faces:
        fs = sorted(faces, key=_area, reverse=True)
        main = [f for f in fs if _area(f) >= 0.5 * _area(fs[0])]
        if len(main) > 3:
            core = _union([_body_of(f, persons, A) for f in main])
            return core, weights + [(1.0, core)], "el grupo", None
        x0, x1 = min(f[0] for f in main), max(f[2] for f in main)
        if x1 - x0 <= 0.6 * A:
            core = _union([_body_of(f, persons, A) for f in main])
            n = len(main)
            return core, weights + [(1.0, core)], "persona principal" if n == 1 else f"{n} personas", None
        return None, weights, "", None
    if persons:
        core, n = _main_people(persons)
        return core, weights + [(1.0, core)], "persona principal" if n == 1 else f"{n} personas", None
    return None, weights, "", None


def _dist(pt, box):
    dx = max(box[0] - pt[0], 0.0, pt[0] - box[2])
    dy = max(box[1] - pt[1], 0.0, pt[1] - box[3])
    return math.hypot(dx, dy)


def _main_people(persons):
    """La persona más grande y las de tamaño parecido que tiene al lado (hasta 3)."""
    ps = sorted(persons, key=lambda t: -_area(t[1]) * t[0])
    big = ps[0][1][3] - ps[0][1][1]
    core = ps[0][1]
    n = 1
    for s, p in ps[1:]:
        if n >= 3:
            break
        if p[3] - p[1] >= 0.6 * big and _dist(((p[0] + p[2]) / 2, (p[1] + p[3]) / 2), core) <= 0.5 * big:
            core = _union([core, p])
            n += 1
    return core, n


def _importance(sal, A, weights, gw=120):
    """Mapa de importancia a ~120 px: saliencia de fondo + cajas con su peso."""
    gw, gh = (gw, max(1, round(gw / A))) if A >= 1 else (max(1, round(gw * A)), gw)
    m = 0.35 * cv2.resize(sal, (gw, gh), interpolation=cv2.INTER_LINEAR) if sal is not None \
        else np.zeros((gh, gw), np.float32)
    for wt, b in weights:
        x0, x1 = int(b[0] / A * gw), int(math.ceil(b[2] / A * gw))
        y0, y1 = int(b[1] * gh), int(math.ceil(b[3] * gh))
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] += wt
    ii = np.zeros((gh + 1, gw + 1), np.float64)
    ii[1:, 1:] = np.cumsum(np.cumsum(m, 0), 1)
    return ii, gw, gh


def _kept(ii, gw, gh, A, c):
    x0, x1 = int(round(c[0] / A * gw)), int(round(c[2] / A * gw))
    y0, y1 = int(round(c[1] * gh)), int(round(c[3] * gh))
    return (ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]) / (ii[-1, -1] + 1e-9)


def _span(lo, hi, size, limit, focus):
    """Posiciones posibles en un eje para un recorte de `size` que contenga [lo, hi] dentro de [0, limit]."""
    a, b = max(0.0, hi - size), min(lo, limit - size)
    if a > b:  # no cabe entero: centrado en el foco
        p = min(max(0.0, focus - size / 2), limit - size)
        return [p]
    return list(np.linspace(a, b, 11)) if b - a > 1e-4 else [a]


def _cuts(c, people):
    """Cuánto parte el recorte a las personas que deja a medias (0 = a nadie)."""
    n = 0.0
    for s, p in people:
        f = _inter(c, p) / max(_area(p), 1e-9)
        if 0.15 < f < 0.85:
            n += s * min(f, 1 - f) * 2
    return n


# ------------------------------------------------------------------------------------- composición
# Reglas de los artículos de composición que se pueden medir al recortar (dZoom, Format, Club de Fotografía):
# tercios, punto áureo, simetría/centrado, espacio en la dirección de la mirada, horizonte en un tercio,
# rellenar el encuadre o dejar espacio negativo, simplicidad (dejar fuera lo que distrae) y número impar.
# Las líneas guía, el ángulo de toma o el color se deciden al disparar: un recorte no los cambia.

GAZE = 0.25  # |giro| a partir del cual la cara mira claramente a un lado


class Anchor:
    """Punto que coloca la composición (la pelota, los ojos, el sujeto o lo que destaca) y hacia dónde mira."""

    def __init__(self, pt, label, look=0, frontal=False, horizon=-1.0, people=0):
        self.pt, self.label, self.look, self.frontal = pt, label, look, frontal
        self.horizon, self.people = horizon, people


def _anchor(A, faces, core, ball, why, sal, hz):
    """El punto de interés de la foto para las reglas de composición."""
    n = 0
    for word in why.split():
        if word.isdigit():
            n = int(word)
    if why.startswith("la pareja"):
        n = 2
    elif why.startswith("persona principal") or why.startswith("jugador principal"):
        n = 1
    if core is None:  # sin nadie: el centro de lo que más destaca
        if sal is None:
            return Anchor((A / 2, 0.5), "lo que destaca", horizon=hz)
        m = np.asarray(sal, np.float32)
        t = m >= np.percentile(m, 90)
        ys, xs = np.nonzero(t)
        wts = m[t] + 1e-6
        return Anchor((float(np.average(xs + 0.5, weights=wts)) / m.shape[1] * A,
                       float(np.average(ys + 0.5, weights=wts)) / m.shape[0]), "lo que destaca", horizon=hz)
    fs = [f for f in faces if core[0] - 1e-3 <= (f[0] + f[2]) / 2 * A <= core[2] + 1e-3
          and core[1] - 1e-3 <= (f[1] + f[3]) / 2 <= core[3] + 1e-3]
    big = max(((f[2] - f[0]) * (f[3] - f[1]) for f in fs), default=0.0)
    main = [f for f in fs if (f[2] - f[0]) * (f[3] - f[1]) >= 0.5 * big]
    yaws = [float(f[4]) for f in main if len(f) > 4]
    look = 0
    if yaws and all(y > GAZE for y in yaws):
        look = 1
    elif yaws and all(y < -GAZE for y in yaws):
        look = -1
    if ball is not None:
        # en la jugada manda la pelota; la mirada, la del jugador más cercano a ella
        bc = ((ball[0] + ball[2]) / 2, (ball[1] + ball[3]) / 2)
        if fs and len(fs[0]) > 4:
            near = min(fs, key=lambda f: math.hypot((f[0] + f[2]) / 2 * A - bc[0], (f[1] + f[3]) / 2 - bc[1]))
            look = 1 if near[4] > GAZE else -1 if near[4] < -GAZE else 0
        return Anchor(bc, "la pelota", look, people=n, horizon=hz)
    if main:
        ex = float(np.mean([(f[0] + f[2]) / 2 for f in main])) * A
        ey = float(np.mean([f[1] + 0.42 * (f[3] - f[1]) for f in main]))
        frontal = len(main) == 1 and bool(yaws) and abs(yaws[0]) < 0.12
        return Anchor((ex, ey), "los ojos" if len(main) <= 2 else "las caras", look, frontal, hz, n)
    return Anchor(((core[0] + core[2]) / 2, core[1] + 0.25 * (core[3] - core[1])), "el sujeto", people=n,
                  horizon=hz)


_POINTS = {(1, 1): "superior izquierdo", (2, 1): "superior derecho", (1, 2): "inferior izquierdo",
           (2, 2): "inferior derecho"}


def _gauss(d, s):
    return math.exp(-(d * d) / (2 * s * s))


def _placement(an, c):
    """Cómo cae el punto de interés en el recorte `c`: (puntuación, (regla, por qué), mirada, horizonte)."""
    cw, ch = c[2] - c[0], c[3] - c[1]
    u, v = (an.pt[0] - c[0]) / cw, (an.pt[1] - c[1]) / ch
    opts = []
    for (a, b), name in _POINTS.items():
        opts.append((_gauss(math.hypot(u - a / 3, v - b / 3), 0.07), "Regla de los tercios",
                     f"{an.label} en el punto fuerte {name}"))
        gu, gv = (0.382, 0.618)[a - 1], (0.382, 0.618)[b - 1]
        opts.append((0.92 * _gauss(math.hypot(u - gu, v - gv), 0.05), "Proporción áurea",
                     f"{an.label} en el punto áureo {name}"))
    if an.label in ("los ojos", "las caras"):  # retrato: los ojos en la línea del tercio superior
        opts.append((0.85 * _gauss(v - 1 / 3, 0.05) * _gauss(abs(u - 0.5), 0.25), "Regla de los tercios",
                     f"{an.label} sobre la línea del tercio superior"))
    if an.frontal:  # de frente: simetría, centrado con los ojos arriba
        opts.append((1.05 * _gauss(u - 0.5, 0.04) * _gauss(v - 1 / 3, 0.06), "Simetría",
                     "retrato de frente: centrado y con los ojos en el tercio superior"))
    sc, rule, why = max(opts, key=lambda t: t[0])
    gaze = None
    if an.look:  # espacio en la dirección de la mirada: al punto le sobra sitio por delante
        ahead = (1 - u) if an.look > 0 else u
        side = "derecha" if an.look > 0 else "izquierda"
        if ahead < 0.45:
            sc *= 0.35  # mira contra el borde
        elif ahead >= 0.55:
            sc = min(1.1, sc + 0.1)
            gaze = ("Espacio de mirada", f"mira a la {side} y le queda aire por ese lado")
    hz = None
    if an.horizon >= 0 and c[1] < an.horizon < c[3]:
        hv = (an.horizon - c[1]) / ch
        g = max(_gauss(hv - 1 / 3, 0.05), _gauss(hv - 2 / 3, 0.05))
        if abs(hv - 0.5) < 0.08:
            sc *= 0.7  # horizonte partiendo la foto por la mitad
        sc += 0.35 * g
        if g > 0.5:
            hz = ("Horizonte en un tercio", "en el tercio " + ("superior" if hv < 0.5 else "inferior")
                  + ", no por la mitad")
    return sc, (rule, why), gaze, hz


def explain(aspect: float, faces: list, objects: list, sal, scene: str, crop, horizon_y: float = -1.0) -> list:
    """Reglas de composición que cumple el recorte `crop` (normalizado) y por qué: [(regla, por qué), …]."""
    A = float(aspect)
    core, weights, why, ball = _focus(A, faces, objects, scene)
    c = (crop[0] * A, crop[1], crop[2] * A, crop[3])
    out = []
    if why:
        out.append(("Centro de interés", f"se prioriza {why}"))
    an = _anchor(A, faces, core, ball, why, sal, horizon_y)
    sc, place, gaze, hz = _placement(an, c)
    if sc >= 0.45:
        out.append(place)
    if gaze:
        out.append(gaze)
    if hz:
        out.append(hz)
    r = (c[2] - c[0]) / (c[3] - c[1])
    cropped = _area(c) < 0.97 * A
    if core is not None:
        fill = _inter(core, c) / max(_area(c), 1e-9)
        if fill >= 0.45:
            out.append(("Rellenar el encuadre", f"el sujeto ocupa el {round(100 * min(1.0, fill))} % de la foto"))
        elif fill <= 0.18:
            out.append(("Espacio negativo", "el sujeto pequeño con aire alrededor da calma y contexto"))
        if sal is not None and cropped:
            ii, gw, gh = _importance(sal, A, [])
            inner = (max(c[0], core[0]), max(c[1], core[1]), min(c[2], core[2]), min(c[3], core[3]))
            tot = 1.0 - _kept(ii, gw, gh, A, core)          # lo que destaca fuera del sujeto…
            left = _kept(ii, gw, gh, A, c) - _kept(ii, gw, gh, A, inner)
            if tot > 0.05 and (tot - left) / tot >= 0.3:     # …y cuánto de eso queda fuera del recorte
                out.append(("Simplicidad", f"deja fuera el {round(100 * (tot - left) / tot)} % de lo que distraía"))
        if an.people == 3:
            out.append(("Número impar", "tres personas: un grupo impar se ve más natural"))
        ch0, cw0 = core[3] - core[1], core[2] - core[0]
        if cropped and r < 0.95:
            out.append(("Orientación vertical", "el sujeto es más alto que ancho" if ch0 >= cw0
                        else "sigue la vertical de la escena"))
        elif cropped and r > 1.05:
            out.append(("Orientación horizontal", "la escena se extiende a lo ancho" if cw0 >= ch0
                        else "deja ver el entorno a los lados"))
    elif cropped:
        out.append(("Simplicidad", "sin personas: recorta las zonas vacías y deja lo que destaca"))
    if not cropped:
        out.insert(1 if why else 0, ("Sin recortar", "la toma ya encuadra bien lo importante"))
    return out


def summary(rules: list) -> str:
    """«Regla: por qué · …» en una línea (para la revisión y el informe)."""
    return " · ".join(f"{r}: {w}" for r, w in rules)


def _crop_for(core, A, r, rule, ii, gw, gh, focus, people=(), an=None):
    """Recorte de proporción r (ancho/alto) que contiene el núcleo con aire, colocado donde más importancia guarda."""
    cw0, ch0 = core[2] - core[0], core[3] - core[1]
    pad = rule["pad"] * max(cw0, ch0)
    ch = max(ch0 + 2.5 * pad, (cw0 + 2 * pad) / r)
    cw = ch * r
    if cw * ch < rule["min_area"] * A:
        s = math.sqrt(rule["min_area"] * A / (cw * ch))
        cw, ch = cw * s, ch * s
    if cw > A:
        cw, ch = A, A / r
    if ch > 1:
        ch, cw = 1.0, r
    xs = _span(core[0] - pad, core[2] + pad, cw, A, focus[0])
    if len(xs) == 1 and cw >= cw0 + 2 * pad - 1e-6:
        xs = _span(core[0], core[2], cw, A, focus[0])
    # arriba: aire sobre la cabeza; si no cabe todo, se sacrifica lo de abajo (pies) antes que la cabeza
    ys = _span(core[1] - 1.5 * pad, core[3] + pad, ch, 1.0, focus[1])
    if len(ys) == 1 and ch < ch0 + 2.5 * pad:
        ys = [min(max(0.0, core[1] - 0.06 * ch), 1.0 - ch)]
    best, bv = None, -1.0
    for x in xs:
        for y in ys:
            c = (x, y, x + cw, y + ch)
            off = abs((x + cw / 2) - focus[0]) / A + abs((y + ch / 2) - focus[1])
            v = _kept(ii, gw, gh, A, c) - 0.02 * off - 0.05 * _cuts(c, people)
            if an is not None:  # composición: tercios, punto áureo, simetría, mirada, horizonte
                v += 0.15 * _placement(an, c)[0]
            if v > bv:
                best, bv = c, v
    return best


def _slide(sal, A, r, an=None):
    """Sin nadie en la foto: el recorte más grande de ese formato, deslizado hacia lo que destaca (y, a igualdad,
    con lo que destaca y el horizonte en los tercios)."""
    ii, gw, gh = _importance(sal, A, [], gw=160)
    if r > A:
        cw, ch = A, A / r
        ys = np.linspace(0, 1 - ch, 41)
        cands = [(0.0, y, A, y + ch) for y in ys]
    else:
        cw, ch = r, 1.0
        xs = np.linspace(0, A - cw, 41)
        cands = [(x, 0.0, x + cw, 1.0) for x in xs]
    vals = [_kept(ii, gw, gh, A, c) - 1e-3 * abs(((c[0] + c[2]) / 2 - A / 2) + ((c[1] + c[3]) / 2 - 0.5))
            + (0.03 * _placement(an, c)[0] if an is not None else 0.0) for c in cands]
    k = int(np.argmax(vals))
    return cands[k], float(_kept(ii, gw, gh, A, cands[k]))


def plan(aspect: float, faces: list, objects: list, sal: np.ndarray | None, scene: str,
         ratios: dict, horizon_y: float = -1.0) -> tuple[dict, dict, str, str]:
    """Recortes por formato, su puntuación, el mejor y por qué (texto corto para la revisión).

    `aspect`: ancho/alto del área útil. `faces` y `objects`: cajas normalizadas (x0, y0, x1, y1) — objetos con
    clase y confianza delante, como los da `Detector.detect`; las caras pueden llevar su giro (`face_yaw`) de
    quinto valor. `ratios`: nombre -> (ancho, alto). `horizon_y`: altura del horizonte (`horizon`) o -1.
    Cada recorte se coloca con las reglas de composición (`_placement`); `explain` dice cuáles y por qué.
    Recortes normalizados 0..1 sobre el área útil, como `Photo.crops`."""
    A = float(aspect)
    crops = {ORIGINAL: (0.0, 0.0, 1.0, 1.0)}
    scores = {ORIGINAL: 1.0}
    core, weights, why, ball = _focus(A, faces, objects, scene)
    people = _people(A, objects, scene)
    norm = lambda c: tuple(round(float(v), 4) for v in (c[0] / A, c[1], c[2] / A, c[3]))  # noqa: E731
    if core is None:
        # nadie: como siempre, el recorte más grande de cada formato hacia lo que destaca; solo se propone
        # si quita sobre todo zona vacía (≤ 20 %) sin perder lo importante
        an = _anchor(A, faces, None, None, "", sal, horizon_y)
        for name, (rw, rh) in ratios.items():
            r = rw / rh
            if abs(r - A) < 0.01:
                continue
            c, f = _slide(sal, A, r, an)
            crops[name] = norm(c)
            area = _area(c) / A
            scores[name] = round(f ** 4 / math.sqrt(max(area, 0.80)), 3) if f >= 0.90 else round(0.5 * f, 3)
        best = max(scores, key=scores.get)
        if scores[best] < 1.03:
            best = ORIGINAL
        return crops, scores, best, "lo que destaca" if best != ORIGINAL else ""
    rule = RULES.get(scene, DEFAULT_RULE)
    if scene != "Deporte":  # a quien está pegado al sujeto no se le parte: entra entero (en deporte, no: la jugada)
        for _ in range(3):
            grown = [p for s, p in people if 0.3 < _inter(core, p) / max(_area(p), 1e-9) < 0.999]
            if not grown:
                break
            core = _union([core] + grown)
    core = (max(0.0, core[0]), max(0.0, core[1]), min(A, core[2]), min(1.0, core[3]))
    ii, gw, gh = _importance(sal, A, weights)
    fb = ball if ball is not None else core
    focus = ((fb[0] + fb[2]) / 2 * 0.5 + (core[0] + core[2]) / 2 * 0.5, (core[1] + core[3]) / 2)
    cw0, ch0 = core[2] - core[0], core[3] - core[1]
    pad = rule["pad"] * max(cw0, ch0)
    ra = (cw0 + 2 * pad) / (ch0 + 2.5 * pad)
    an = _anchor(A, faces, core, ball, why, sal, horizon_y)

    def score(c, r, name):
        cover = _inter(c, core) / max(_area(core), 1e-9)
        kept = _kept(ii, gw, gh, A, c)
        suit = min(ra, r) / max(ra, r)
        ar = _area(c) / A
        comp = min(1.0, _placement(an, c)[0])
        return (cover ** 3 * (0.50 * suit + 0.27 * kept + 0.13 * (1 - ar) + 0.10 * comp)
                + PREFERRED.get(name, 0.0) - 0.04 * _cuts(c, people))

    scores[ORIGINAL] = round(float(score((0.0, 0.0, A, 1.0), A, ORIGINAL)) + 0.03, 3)
    for name, (rw, rh) in ratios.items():
        r = rw / rh
        c = _crop_for(core, A, r, rule, ii, gw, gh, focus, people, an)
        crops[name] = norm(c)
        scores[name] = round(float(score(c, r, name)), 3)
    best = max(scores, key=scores.get)
    if best != ORIGINAL:
        b = crops[best]
        if (b[2] - b[0]) * (b[3] - b[1]) > 0.88:  # casi la foto entera: no merece la pena recortar
            best = ORIGINAL
    return crops, scores, best, why
