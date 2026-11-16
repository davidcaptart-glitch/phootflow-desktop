"""Motor de PhootFlow: análisis, descarte, sugerencia de formato, revelado y exportación."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import queue
import re
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import zipfile
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps, TiffImagePlugin

import luts
import styles

try:
    import rawpy
except Exception:  # pragma: no cover - se informa en la interfaz
    rawpy = None
try:
    import exifread
except Exception:  # pragma: no cover
    exifread = None

RAW_EXT = {".arw", ".cr2", ".cr3", ".nef", ".raf", ".dng", ".orf", ".rw2", ".srw", ".pef"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# nombre -> (ancho, alto)
RATIOS = {
    "4:5": (4, 5), "5:4": (5, 4),
    "2:3": (2, 3), "3:2": (3, 2),
    "3:4": (3, 4), "4:3": (4, 3),
    "5:7": (5, 7), "7:5": (7, 5),
    "9:16": (9, 16), "16:9": (16, 9),
    "1:1": (1, 1),
}
SIZES = {"max": None, "2048": 2048, "1080": 1080}  # 2048 = lado largo, 1080 = ancho
NR_LEVELS = {"Suave": 0.6, "Normal": 1.0, "Fuerte": 1.5}
SENS = {"Baja": 0.30, "Normal": 0.45, "Alta": 0.60}
SHARP_ABS_MIN = 0.008  # por debajo de esto no hay nada nítido en la imagen
EV_MAX = 5.0            # tope de subida de exposición
EV_HOPELESS = 7.5       # a partir de aquí la foto se considera irrecuperable
FACE_TARGET = 0.32      # luminancia lineal a la que se lleva la piel de las caras
EYES_CLOSED = 0.35      # por debajo, ojos cerrados (escala 0..1 de Photo.eyes_open)

# Salidas propias (DSC0001_4x5_max.jpg, DSC0001_original_1080.jpg…) y copias editadas: no se reeditan
OWN_OUTPUT = re.compile(r"((_bn)?_(original|\d+x\d+)_(max|2048|1080)|_editada|_edited)$", re.I)
SOFTWARE = "PhootFlow"


def asset(name: str) -> str:
    """Ruta a un recurso de assets/, tanto en desarrollo como dentro del .exe de PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


_SRGB_ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
_TLS = threading.local()  # los detectores de OpenCV no son seguros entre hilos: uno por hilo
_KEEP: list = []  # y no deben destruirse al terminar el hilo (OpenCV 4.x en Windows se cae): se guardan aquí


@dataclass
class Settings:
    nr: str = "Normal"
    straighten: bool = True
    ev_comp: float = 0.0
    cull_focus: bool = True
    cull_exposure: bool = True
    cull_dupes: bool = True
    sensitivity: str = "Normal"
    ratios: list = field(default_factory=lambda: ["4:5"])
    export_original_ratio: bool = True
    sizes: list = field(default_factory=lambda: ["max", "1080"])
    quality: int = 95
    move_discards: bool = True
    archive_originals: bool = True
    keep_exif: bool = True
    write_xmp: bool = False
    workers: int = 2
    style: dict = field(default_factory=dict)  # parámetros del estilo (styles.PRESETS o aprendido)
    style_name: str = ""
    bw: bool = False        # además, versión en blanco y negro (carpeta ByN)
    bw_style: str = "Editorial"  # estilo de B/N de la sesión (BW_STYLES); cada foto puede tener el suyo
    look: str = "Natural"       # look de color de la sesión (COLOR_LOOKS); cada foto puede tener el suyo
    cache_dir: str = ""     # vistas previas en disco (no en memoria): imprescindible con miles de fotos
    resume: bool = True     # al exportar, saltar las fotos que ya se terminaron en una pasada anterior
    uniform: bool = True    # una sola curva y color para toda la sesión + exposición igualada
    camera_look: bool = True  # partir del aspecto del JPEG que lleva el RAW (contraste y color de la cámara)
    cull_eyes: bool = False   # descartar (no solo avisar) fotos con ojos cerrados claros


@dataclass
class Photo:
    path: str
    extras: list = field(default_factory=list)  # JPG que acompaña al RAW
    name: str = ""
    iso: int = 0
    when: dt.datetime | None = None
    ev: float = 0.0
    ev_raw: float = 0.0
    clipped: float = 0.0
    angle: float = 0.0
    sharp: float = 0.0
    dhash: int = 0
    faces: int = 0
    crops: dict = field(default_factory=dict)  # ratio -> (x0, y0, x1, y1) normalizado sobre el área útil
    scores: dict = field(default_factory=dict)
    best: str = "Original"
    keep: bool = True
    reason: str = ""
    thumb: bytes = b""
    preview: bytes = b""        # vista previa grande (sin recortar, ya girada) si no hay caché en disco
    preview_path: str = ""      # … o su ruta en la caché
    preview_ev: float = 0.0     # exposición con la que se generó la vista previa
    preview_angle: float = 0.0
    feats: dict = field(default_factory=dict)  # rasgos para detectar el tipo de sesión
    warn: str = ""              # avisos que no descartan (p. ej. ojos quizá cerrados)
    ev_auto: float = 0.0        # exposición automática de la foto sola (antes de igualar la sesión)
    face_lin: float = 0.0       # luminancia lineal de la piel a 0 EV (0 = sin caras)
    lum_med: float = 0.0        # mediana de luminancia lineal a 0 EV
    face_sharp: float = 0.0     # nitidez de la cara principal
    eyes_open: float = 1.0      # 0 = ojos cerrados … 1 = abiertos (la peor de las caras grandes)
    noise_y: float = 0.0        # ruido medido (luminancia / color) en la imagen de análisis
    noise_c: float = 0.0
    tone_q: list = field(default_factory=list)  # 64 cuantiles de L (Lab) del revelado de análisis
    cam_q: list = field(default_factory=list)   # … y del JPEG de la cámara llevado al mismo brillo
    chroma: float = 0.0         # croma mediana del revelado de análisis
    cam_chroma: float = 0.0     # … y del JPEG de la cámara
    ab: list = field(default_factory=list)      # mediana a, b del revelado de análisis
    scene_ai: dict = field(default_factory=dict)  # probabilidad de cada tipo de evento (IA)
    lut_strength: float | None = None  # intensidad del LUT en esta foto (None = la de la sesión)
    bw_style: str = ""          # estilo de B/N de esta foto ("" = el de la sesión; BW_NONE = sin B/N)
    look: str = ""              # look de color de esta foto ("" = el de la sesión)
    meta: dict = field(default_factory=dict)  # EXIF útil para copiar a los JPG
    error: str = ""


# ---------------------------------------------------------------- utilidades

def list_photos(folder: str) -> list[Photo]:
    """Fotos de la carpeta (sin subcarpetas). Si hay RAW+JPG con el mismo nombre, manda el RAW."""
    by_stem: dict[str, dict] = {}
    for fn in sorted(os.listdir(folder)):
        p = os.path.join(folder, fn)
        stem, ext = os.path.splitext(fn)
        ext = ext.lower()
        if not os.path.isfile(p) or ext not in RAW_EXT | IMG_EXT:
            continue
        if ext not in RAW_EXT and OWN_OUTPUT.search(stem):
            continue
        d = by_stem.setdefault(stem.lower(), {"raw": None, "img": []})
        if ext in RAW_EXT and d["raw"] is None:
            d["raw"] = p
        else:
            d["img"].append(p)
    out = []
    for d in by_stem.values():
        if d["raw"]:
            out.append(Photo(path=d["raw"], extras=d["img"]))
        else:
            out.extend(Photo(path=p) for p in d["img"])
    for ph in out:
        ph.name = os.path.splitext(os.path.basename(ph.path))[0]
    return out


def read_meta(ph: Photo) -> None:
    if exifread is None:
        return
    try:
        with open(ph.path, "rb") as f:
            t = exifread.process_file(f, details=False)
        for key, tag in [("make", "Image Make"), ("model", "Image Model"), ("lens", "EXIF LensModel"),
                         ("exposure", "EXIF ExposureTime"), ("fnumber", "EXIF FNumber"),
                         ("focal", "EXIF FocalLength"), ("date", "EXIF DateTimeOriginal"),
                         ("artist", "Image Artist"), ("copyright", "Image Copyright")]:
            v = t.get(tag)
            if v is not None:
                ph.meta[key] = v.values if isinstance(v.values, list) and not isinstance(v.values, str) else str(v)
        iso = t.get("EXIF ISOSpeedRatings")
        if iso:
            ph.iso = int(str(iso).split(",")[0].strip("[] "))
            ph.meta["iso"] = ph.iso
        d = t.get("EXIF DateTimeOriginal") or t.get("Image DateTime")
        if d:
            sub = str(t.get("EXIF SubSecTimeOriginal") or "0")
            ph.when = dt.datetime.strptime(str(d), "%Y:%m:%d %H:%M:%S") + dt.timedelta(
                seconds=float("0." + "".join(c for c in sub if c.isdigit()) or 0))
    except Exception:
        pass


def is_raw(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in RAW_EXT


def load_linear(path: str, half: bool, rebuild_highlights: bool = False) -> np.ndarray:
    """Imagen en sRGB lineal float32, ya orientada.

    `rebuild_highlights`: reconstruye los canales quemados (frentes y caras con flash directo) en vez de
    mezclarlos; es más lento, así que solo se usa en las fotos que tienen zonas quemadas.
    """
    if is_raw(path):
        if rawpy is None:
            raise RuntimeError("Falta la librería rawpy para leer RAW")
        with rawpy.imread(path) as r:
            hm = rawpy.HighlightMode.Reconstruct(4) if rebuild_highlights else rawpy.HighlightMode.Blend
            kw = dict(use_camera_wb=True, no_auto_bright=True, output_bps=16, gamma=(1, 1),
                      output_color=rawpy.ColorSpace.sRGB, highlight_mode=hm)
            if half:
                rgb = r.postprocess(half_size=True, **kw)
            else:
                rgb = r.postprocess(demosaic_algorithm=rawpy.DemosaicAlgorithm.DHT,
                                    fbdd_noise_reduction=rawpy.FBDDNoiseReductionMode.Full, **kw)
        return rgb.astype(np.float32) / 65535.0
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if half and max(im.size) > 3200:
        im.thumbnail((3200, 3200), Image.LANCZOS)
    x = np.asarray(im, dtype=np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _luma(x: np.ndarray) -> np.ndarray:
    return 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]


def estimate_ev(lin: np.ndarray) -> tuple[float, float, float]:
    """(EV aplicado, EV sin limitar, fracción quemada)."""
    lum = _luma(lin[::4, ::4])
    clipped = float((lum >= 0.98).mean())
    p99 = max(float(np.percentile(lum, 99.0)), 1e-5)
    ev_raw = math.log2(0.80 / p99)
    ev = ev_raw
    med = float(np.median(lum)) * 2 ** ev
    if med > 0.30:  # no levantar escenas que ya son luminosas
        ev -= math.log2(med / 0.30)
    return float(np.clip(ev, -1.5, EV_MAX)), ev_raw, clipped


def tone(lin: np.ndarray, ev: float) -> np.ndarray:
    """Exposición + hombro suave en altas luces + curva sRGB. Devuelve 0..1."""
    x = lin * np.float32(2.0 ** ev)
    a = 0.6
    x = np.where(x < a, x, a + (1 - a) * (1 - np.exp(-(x - a) / (1 - a))))
    g = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(np.maximum(x, 1e-9), 1 / 2.4) - 0.055)
    return np.clip(g, 0, 1).astype(np.float32)


def _safe_rect(w: int, h: int, angle: float) -> tuple[int, int, int, int]:
    """Rectángulo sin bordes vacíos tras girar `angle` grados."""
    if abs(angle) < 1e-3:
        return 0, 0, w, h
    t = math.radians(abs(angle))
    mx = int(math.ceil(h * math.sin(t) / 2)) + max(2, w // 400)
    my = int(math.ceil(w * math.sin(t) / 2)) + max(2, h // 400)
    return mx, my, w - mx, h - my


def rotate(img: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 1e-3:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    r = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    x0, y0, x1, y1 = _safe_rect(w, h, angle)
    return r[y0:y1, x0:x1]


# ------------------------------------------------------------------ análisis

def detect_tilt(gray8: np.ndarray) -> float:
    """Giro (grados, convención de OpenCV) que pone verticales las líneas verticales.

    Solo se fía de las verticales, y solo si las hay a ambos lados del encuadre:
    así la convergencia por perspectiva se compensa y no se confunde con inclinación.
    """
    h, w = gray8.shape
    edges = cv2.Canny(gray8, 40, 120)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 1800, 60, minLineLength=int(0.07 * max(h, w)), maxLineGap=6)
    if lines is None:
        return 0.0
    xs, ds, ws = [], [], []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        dev = (math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180) - 90
        if abs(dev) < 8:
            xs.append((x1 + x2) / 2 / w - 0.5)
            ds.append(dev)
            ws.append(math.hypot(x2 - x1, y2 - y1))
    xs, ds, ws = np.array(xs), np.array(ds), np.array(ws)
    if len(xs) < 8 or (xs < -0.1).sum() < 3 or (xs > 0.1).sum() < 3:
        return 0.0
    # desviacion = giro + convergencia * x ; ajuste robusto (los reflejos y pliegues no cuentan)
    wt = ws.copy()
    for _ in range(6):
        A = np.stack([np.ones_like(xs), xs], 1) * wt[:, None] ** 0.5
        roll, conv = np.linalg.lstsq(A, ds * wt ** 0.5, rcond=None)[0]
        res = np.abs(ds - (roll + conv * xs))
        wt = ws / np.maximum(1.0, res / 0.7) ** 2
    if abs(conv) > 8 or np.average(res, weights=ws) > 1.5:
        return 0.0
    return round(float(roll), 2) if 0.4 <= abs(roll) <= 6 else 0.0


_POOL: dict = {}  # nombre -> queue.Queue de detectores creados en el hilo principal


def prepare_detectors(n: int) -> None:
    """Crea en el hilo principal `n` juegos de detectores para los hilos de trabajo.

    Los detectores de OpenCV no son seguros entre hilos, así que cada hilo usa el suyo; y un
    CascadeClassifier creado dentro de un hilo secundario tumba el proceso al salir (OpenCV 4.x, Windows).
    """
    for name, make in _MAKERS.items():
        q = _POOL.setdefault(name, queue.Queue())
        for _ in range(n):
            d = make()
            _KEEP.append(d)
            q.put(d)
    if EYES["ready"]:
        import eyes
        try:
            eyes.prepare(n)
        except Exception:
            EYES["ready"] = False


def _detector(name: str):
    d = getattr(_TLS, name, None)
    if d is None:
        try:
            d = _POOL[name].get_nowait()
        except (KeyError, queue.Empty):
            d = _MAKERS[name]()
            _KEEP.append(d)
        setattr(_TLS, name, d)
    return d


def _haar(file: str):
    return lambda: cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, file))


def _make_yunet():
    model = asset("face_detection_yunet_2023mar.onnx")
    if os.path.exists(model) and hasattr(cv2, "FaceDetectorYN"):
        try:
            return cv2.FaceDetectorYN.create(model, "", (320, 320), 0.7, 0.3, 5000)
        except Exception:
            pass
    return False


_MAKERS = {"yunet": _make_yunet, "haar": _haar("haarcascade_frontalface_default.xml"),
           "eye": _haar("haarcascade_eye_tree_eyeglasses.xml")}


def _faces(gray8: np.ndarray, raw: list | None = None) -> list[tuple[int, int, int, int]]:
    """Caras (x, y, w, h). YuNet aguanta mucho mejor ISO alto, perfiles y luz de discoteca que Haar.
    Si se pasa `raw`, se le añaden las filas completas de YuNet (con los puntos de ojos, nariz y boca)."""
    yunet = _detector("yunet")
    if yunet is not False:
        h, w = gray8.shape
        bgr = cv2.cvtColor(gray8, cv2.COLOR_GRAY2BGR)
        yunet.setInputSize((w, h))
        _, det = yunet.detect(bgr)
        m = 0.03 * min(h, w)
        if det is None:
            return []
        keep = [d for d in det if d[2] >= m]
        if raw is not None:
            raw.extend(keep)
        return [(int(max(0, d[0])), int(max(0, d[1])), int(d[2]), int(d[3])) for d in keep]
    return _faces_haar(gray8)


# --- ojos cerrados con MediaPipe (opcional: si no está o falta el modelo, se usa el aviso con Haar)
EYES = {"ready": False}


def setup_eyes(model_dir: str) -> bool:
    """Descarga/verifica el modelo de puntos faciales y comprueba que MediaPipe carga. Llamar una vez."""
    try:
        import eyes
        eyes.configure(model_path=eyes.ensure_model(model_dir))
        eyes._mediapipe()
        EYES["ready"] = True
    except Exception as e:  # sin red, sin mediapipe… la app sigue funcionando
        EYES["ready"], EYES["error"] = False, f"{type(e).__name__}: {e}"
    return EYES["ready"]


AI = {"ready": False, "obj": None}


def setup_scene_ai(model_dir: str, progress=None) -> bool:
    """Descarga/verifica el modelo CLIP (89 MB, la primera vez) y lo deja listo. Llamar una vez."""
    try:
        import scene_ai
        path = scene_ai.ensure_model(model_dir, progress)
        AI["obj"] = scene_ai.SceneAI(path, asset("text_embeddings.npy"), threads=2)
        AI["obj"].scores(np.zeros((224, 224, 3), np.float32))  # carga la sesión de onnxruntime
        AI["ready"] = True
    except Exception as e:
        AI["ready"], AI["error"] = False, f"{type(e).__name__}: {e}"
    return AI["ready"]


def _eyes_scores(rgb01: np.ndarray, raw: list) -> list:
    if not EYES["ready"] or not raw:
        return []
    import eyes
    try:
        return eyes.eyes_open_scores(rgb01, np.asarray(raw, np.float32))
    except Exception:
        return []


def _faces_haar(gray8: np.ndarray) -> list[tuple[int, int, int, int]]:
    face = _detector("haar")
    if face.empty():
        return []
    m = int(0.045 * min(gray8.shape))
    f = face.detectMultiScale(gray8, scaleFactor=1.1, minNeighbors=7, minSize=(m, m))
    return [tuple(int(v) for v in r) for r in f]


def _eyes_closed(gray8: np.ndarray, faces: list) -> int:
    """Caras frontales grandes en las que no se encuentra ningún ojo abierto (aviso orientativo)."""
    eye = _detector("eye")
    if eye.empty():
        return 0
    n = 0
    for (x, y, fw, fh) in faces:
        if fw < 90:  # demasiado pequeña para juzgar
            continue
        roi = gray8[y + fh // 6: y + fh // 2 + fh // 10, x: x + fw]
        if roi.size == 0:
            continue
        roi = cv2.equalizeHist(roi)
        m = max(12, fw // 8)
        if len(eye.detectMultiScale(roi, 1.1, 3, minSize=(m, m))) == 0:
            n += 1
    return n


def face_sharpness(gray: np.ndarray, faces: list) -> float:
    """Nitidez dentro de la cara más grande (0 si no hay caras), misma escala que `sharpness`."""
    if not faces:
        return 0.0
    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
    c = gray[y: y + fh, x: x + fw]
    if min(c.shape[:2]) < 40:
        return 0.0
    g = cv2.GaussianBlur(c, (0, 0), 1.0)
    lap = np.abs(cv2.Laplacian(g, cv2.CV_32F))
    rng = float(np.percentile(c, 99) - np.percentile(c, 1)) + 0.05
    return float(np.percentile(lap, 90) / rng)


def face_lum(gray: np.ndarray, faces: list) -> float:
    """Luminancia lineal de la piel (mediana ponderada por tamaño de cara) en la imagen `gray` (sRGB 0..1)."""
    vals, wts = [], []
    for (x, y, fw, fh) in faces:
        c = gray[y + fh // 5: y + 4 * fh // 5, x + fw // 5: x + 4 * fw // 5]
        if c.size:
            v = float(np.median(c))
            vals.append(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92)
            wts.append(fw * fh)
    if not vals:
        return 0.0
    order = np.argsort(vals)
    cum = np.cumsum(np.array(wts, float)[order])
    return float(np.array(vals)[order][np.searchsorted(cum, cum[-1] / 2)])


def _face_ev(gray: np.ndarray, faces: list) -> float:
    """Corrección de exposición (EV) para llevar la piel de las caras a su tono, acotada."""
    vals, wts = [], []
    for (x, y, fw, fh) in faces:
        c = gray[y + fh // 5: y + 4 * fh // 5, x + fw // 5: x + 4 * fw // 5]
        if c.size:
            v = float(np.median(c))
            vals.append(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92)
            wts.append(fw * fh)
    if not vals:
        return 0.0
    order = np.argsort(vals)
    cum = np.cumsum(np.array(wts, float)[order])
    lum = max(float(np.array(vals)[order][np.searchsorted(cum, cum[-1] / 2)]), 1e-4)
    return float(np.clip(math.log2(FACE_TARGET / lum), -0.7, 0.9))


def noise_sigma(ch: np.ndarray) -> float:
    """Desviación del ruido (método de Immerkær) en una zona central de 768 px. Misma escala que `ch`."""
    h, w = ch.shape
    c = ch[max(0, h // 2 - 384): h // 2 + 384, max(0, w // 2 - 384): w // 2 + 384].astype(np.float32)
    if min(c.shape) < 16:
        return 0.0
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    r = np.abs(cv2.filter2D(c, -1, k))[1:-1, 1:-1]
    # sin bordes ni texturas: solo el 50 % más plano de la imagen
    r = r[r <= np.percentile(r, 50)]
    return float(math.sqrt(math.pi / 2) * r.mean() / 6.0 * 1.6) if r.size else 0.0


def lab_stats(rgb01: np.ndarray) -> tuple[list, float, list]:
    """64 cuantiles de L, croma mediana y (a, b) medianos de una imagen pequeña."""
    small = rgb01
    if max(rgb01.shape[:2]) > 512:
        s = 512 / max(rgb01.shape[:2])
        small = cv2.resize(rgb01, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(np.ascontiguousarray(small, dtype=np.float32), cv2.COLOR_RGB2Lab)
    q = np.percentile(lab[..., 0], np.linspace(0, 100, 64))
    mid = (lab[..., 0] > 20) & (lab[..., 0] < 85)
    if not mid.any():
        return [round(float(x), 3) for x in q], 0.0, [0.0, 0.0]
    c = float(np.median(np.hypot(lab[..., 1], lab[..., 2])[mid]))
    return ([round(float(x), 3) for x in q], round(c, 3),
            [round(float(np.median(lab[..., 1][mid])), 3), round(float(np.median(lab[..., 2][mid])), 3)])


def camera_render(path: str, ref_lin_median: float) -> np.ndarray | None:
    """JPEG que lleva el RAW, llevado al mismo brillo medio que nuestro revelado (conserva su contraste y
    color, no su exposición, que en fotos de evento suele ser oscura)."""
    if not is_raw(path) or rawpy is None:
        return None
    try:
        with rawpy.imread(path) as r:
            th = r.extract_thumb()
        if th.format != rawpy.ThumbFormat.JPEG:
            return None
        im = Image.open(io.BytesIO(th.data))
        im.draft("RGB", (640, 640))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((512, 512))
        x = np.asarray(im, np.float32) / 255.0
        lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
        med = float(np.median(_luma(lin)))
        if med <= 1e-4:
            return None
        lin = np.clip(lin * (ref_lin_median / med), 0, 1)
        return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055).astype(np.float32)
    except Exception:
        return None


def sharpness(gray: np.ndarray) -> float:
    """Nitidez de la zona más nítida del encuadre, normalizada por contraste."""
    g = cv2.GaussianBlur(gray, (0, 0), 1.0)
    lap = np.abs(cv2.Laplacian(g, cv2.CV_32F))
    t = 48
    h, w = lap.shape
    tiles = lap[: h // t * t, : w // t * t].reshape(h // t, t, w // t, t).mean(axis=(1, 3)).ravel()
    top = np.sort(tiles)[-max(3, len(tiles) // 20):].mean()
    rng = float(np.percentile(gray, 99) - np.percentile(gray, 1)) + 0.05
    return float(top / rng)


def _dhash(gray: np.ndarray) -> int:
    s = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (s[:, 1:] > s[:, :-1]).ravel()
    return int("".join("1" if b else "0" for b in bits), 2)


def plan_crops(gray: np.ndarray, faces: list) -> tuple[dict, dict, str]:
    """Para cada formato, el recorte más grande posible colocado donde está el interés."""
    h, w = gray.shape
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    k = max(3, int(0.03 * max(h, w)))
    det = cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), k)
    bri = cv2.GaussianBlur(gray, (0, 0), k)
    interest = 0.6 * det / (det.max() + 1e-6) + 0.4 * bri / (bri.max() + 1e-6)
    interest = np.clip(interest - np.percentile(interest, 35), 0, None) ** 1.5  # el fondo vacío no cuenta
    fb = None
    peak = float(interest.max()) + 1e-6
    for (x, y, fw, fh) in faces:
        x0, y0 = max(0, x - fw // 3), max(0, y - fh // 2)
        x1, y1 = min(w, x + fw + fw // 3), min(h, y + fh + fh // 3)
        interest[y0:y1, x0:x1] += 0.6 * peak
        # torso bajo la cara: que el recorte no corte a la persona por el pecho
        interest[y1:min(h, y1 + 3 * fh), max(0, x - fw):min(w, x + 2 * fw)] += 0.25 * peak
        fb = (x0, y0, x1, y1) if fb is None else (min(fb[0], x0), min(fb[1], y0), max(fb[2], x1), max(fb[3], y1))
    total = float(interest.sum()) + 1e-6
    col = np.concatenate([[0], np.cumsum(interest.sum(axis=0))])
    row = np.concatenate([[0], np.cumsum(interest.sum(axis=1))])
    crops, scores = {"Original": (0.0, 0.0, 1.0, 1.0)}, {"Original": 1.0}
    for name, (rw, rh) in RATIOS.items():
        r = rw / rh
        if abs(r - w / h) < 0.01:
            continue
        if r > w / h:  # sobra alto: se desliza en vertical
            cw, ch = w, int(round(w / r))
            cum, free, lo, hi = row, h - ch, (fb[1], fb[3]) if fb else None, ch
        else:  # sobra ancho: se desliza en horizontal
            cw, ch = int(round(h * r)), h
            cum, free, lo, hi = col, w - cw, (fb[0], fb[2]) if fb else None, cw
        pos = np.arange(free + 1)
        kept = cum[pos + hi] - cum[pos]
        kept = kept - 1e-3 * kept.max() * np.abs(pos - free / 2) / max(free, 1)  # desempate: centrado
        faces_in = True
        if lo is not None:
            if lo[1] - lo[0] <= hi:
                a, b = max(0, lo[1] - hi), min(free, lo[0])
                pos, kept = pos[a:b + 1], kept[a:b + 1]
            else:
                faces_in = False
        p = int(pos[int(np.argmax(kept))])
        f = float(cum[p + hi] - cum[p]) / total
        area = (cw * ch) / (w * h)
        if r > w / h:
            crops[name] = (0.0, p / h, 1.0, (p + ch) / h)
        else:
            crops[name] = (p / w, 0.0, (p + cw) / w, 1.0)
        # quitar zona vacía suma (hasta un 20 %: más sería apretar demasiado); perder contenido resta mucho más
        scores[name] = round(f ** 4 / math.sqrt(max(area, 0.80)), 3) if (f >= 0.90 and faces_in) else round(0.5 * f, 3)
    best = max(scores, key=scores.get)
    if scores[best] < 1.03:
        best = "Original"
    return crops, scores, best


def analyze(ph: Photo, st: Settings) -> Photo:
    """Análisis rápido (media resolución): exposición, giro, nitidez, caras y recortes."""
    try:
        read_meta(ph)
        lin = load_linear(ph.path, half=True)
        ph.ev, ph.ev_raw, ph.clipped = estimate_ev(lin)
        ph.lum_med = float(np.median(_luma(lin[::4, ::4])))
        g = tone(lin, ph.ev)
        ycc = cv2.cvtColor(g, cv2.COLOR_RGB2YCrCb)
        ph.noise_y = noise_sigma(ycc[..., 0])
        ph.noise_c = max(noise_sigma(ycc[..., 1]), noise_sigma(ycc[..., 2]))
        del ycc
        s = 1600 / max(g.shape[:2])
        if s < 1:
            g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(g, cv2.COLOR_RGB2GRAY)
        g8 = (gray * 255).astype(np.uint8)
        ph.sharp = sharpness(gray)
        ph.dhash = _dhash(gray)
        ph.angle = detect_tilt(g8) if st.straighten else 0.0
        if ph.angle:
            g, gray = rotate(g, ph.angle), rotate(gray, ph.angle)
            g8 = (np.clip(gray, 0, 1) * 255).astype(np.uint8)
        raw_faces: list = []
        faces = _faces(g8, raw_faces)
        ph.faces = len(faces)
        # medición en las caras: en fotos de evento lo que tiene que quedar bien expuesto es la piel
        d_ev = _face_ev(gray, faces)
        ev = float(np.clip(ph.ev + d_ev + st.ev_comp, -2.5, EV_MAX + 0.5))
        if abs(ev - ph.ev) > 0.1:
            g = tone(lin, ev)
            if s < 1:
                g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            g = rotate(g, ph.angle)
            gray = cv2.cvtColor(g, cv2.COLOR_RGB2GRAY)
        ph.ev = ph.ev_auto = ev
        # piel a 0 EV (para igualar la sesión): la medida está hecha con la exposición `ev`
        fl = face_lum(gray, faces)
        ph.face_lin = fl / (2 ** ev) if fl else 0.0
        del lin
        ph.crops, ph.scores, ph.best = plan_crops(gray, faces)
        ph.warn = ""
        if EYES["ready"]:  # YuNet trabajó sobre `g8`, que es la misma imagen (ya girada) que `g`
            sc = [x for x in _eyes_scores(np.clip(g, 0, 1), raw_faces) if x is not None]
            ph.eyes_open = float(min(sc)) if sc else 1.0
            if ph.eyes_open < EYES_CLOSED:
                ph.warn = "Ojos cerrados"
        else:
            closed = _eyes_closed((np.clip(gray, 0, 1) * 255).astype(np.uint8), faces)
            ph.warn = f"Revisar: {closed} cara(s) quizá con ojos cerrados" if closed else ""
        fs = ph.face_sharp = face_sharpness(gray, faces)
        if faces and 0 < fs < 0.5 * ph.sharp:
            ph.warn = (ph.warn + " · " if ph.warn else "") + "La cara está menos nítida que el fondo"
        ph.feats = styles.scene_features(g, ph)
        if AI["ready"]:
            try:
                ph.scene_ai = AI["obj"].scores(np.clip(g, 0, 1))
            except Exception:
                ph.scene_ai = {}
        ph.tone_q, ph.chroma, ph.ab = lab_stats(g)
        if st.camera_look:
            gc = np.clip(g, 0, 1)
            cam = camera_render(ph.path, float(np.median(_luma(
                np.where(gc <= 0.04045, gc / 12.92, ((gc + 0.055) / 1.055) ** 2.4)))))
            if cam is not None:
                ph.cam_q, ph.cam_chroma, _ = lab_stats(cam)
        t = Image.fromarray((np.clip(g, 0, 1) * 255).astype(np.uint8))
        ph.preview_ev, ph.preview_angle = ph.ev, ph.angle
        if st.cache_dir:
            os.makedirs(st.cache_dir, exist_ok=True)
            ph.preview_path = os.path.join(st.cache_dir, os.path.basename(ph.path) + ".prev.jpg")
            t.save(ph.preview_path, "JPEG", quality=85)
            ph.preview = b""
        else:
            b = io.BytesIO()
            t.save(b, "JPEG", quality=85)
            ph.preview = b.getvalue()
        t.thumbnail((360, 360))
        b = io.BytesIO()
        t.save(b, "JPEG", quality=82)
        ph.thumb = b.getvalue()
    except Exception as e:  # una foto ilegible no para la sesión
        ph.error = f"{type(e).__name__}: {e}"
        ph.keep, ph.reason = False, "No se pudo leer"
    return ph


def _rolling_median(vals: list[float], window: int) -> list[float]:
    out = []
    for i in range(len(vals)):
        a, b = max(0, i - window // 2), min(len(vals), i + window // 2 + 1)
        out.append(float(np.median(vals[a:b])))
    return out


def equalize_exposure(photos: list[Photo]) -> None:
    """Iguala el brillo entre fotos de la misma parte de la sesión.

    Parte de la exposición automática de cada foto (`ev_auto`) y la acerca a la mediana de sus vecinas en el
    tiempo (ventana de 31 fotos): así la ceremonia y la fiesta pueden tener brillos distintos, pero dentro de
    cada momento todas las fotos quedan iguales. Con caras manda la piel; sin caras, el brillo medio.
    """
    ok = sorted([p for p in photos if not p.error and p.thumb],
                key=lambda p: (p.when or dt.datetime.min, p.name))
    if len(ok) < 3:
        return
    for key, gain, lim in (("face_lin", 0.85, 0.8), ("lum_med", 0.6, 0.5)):
        group = [p for p in ok if (p.face_lin > 0) == (key == "face_lin") and getattr(p, key) > 0]
        if len(group) < 3:
            continue
        out = [math.log2(getattr(p, key)) + p.ev_auto for p in group]  # brillo resultante, en EV
        target = _rolling_median(out, 31)
        for p, o, t in zip(group, out, target):
            p.ev = float(np.clip(p.ev_auto + np.clip(gain * (t - o), -lim, lim), -2.5, EV_MAX + 0.5))


def calibrate_session(photos: list[Photo], st: Settings) -> dict:
    """Ajustes ÚNICOS para toda la sesión (mismo revelado para todas las fotos).

    - Estilo aprendido de fotos acabadas: una sola curva y un solo color que llevan la sesión entera a tu
      aspecto (antes se ajustaba cada foto por separado).
    - Preset: curva y croma de la cámara (mediana de toda la sesión), a media fuerza.
    Devuelve los parámetros que se suman al estilo (`Settings.style`).
    """
    ok = [p for p in photos if not p.error and len(p.tone_q) == 64]
    if not ok:
        return {}
    if st.uniform:
        equalize_exposure(photos)
    sty = dict(styles.BASE, **(st.style or {}))
    if sty.get("cube"):
        # el LUT da el aspecto: fuera la curva y el color propios para no duplicarlos; el LUT recibe
        # imágenes de brillo igualado y contraste normal, que es para lo que están hechos
        out = {"session": True, "target_q": None, "lut": None, "chroma": 1.0, "tint": 0.0, "warmth": 0.0,
               "contrast": round(float(sty["contrast"]) * 0.5, 3), "saturation": 1.0, "black": 0.02}
        lut_review(photos, sty["cube"], float(sty.get("cube_strength", 1.0)))
        return out
    ours = np.median(np.array([p.tone_q for p in ok]), axis=0)
    ours = np.maximum.accumulate(ours + np.arange(64) * 1e-4)
    grid = np.linspace(0, 100, 256)
    out = {"session": True}
    if sty.get("target_q") is not None:
        tq = np.asarray(sty["target_q"], np.float32)
        tq64 = np.interp(np.linspace(0, 1, 64), np.linspace(0, 1, len(tq)), tq)
        k = float(sty.get("strength", 0.6))
        lut = (1 - k) * grid + k * np.interp(grid, ours, tq64)
        chroma = float(np.median([p.chroma for p in ok if p.chroma > 0] or [1.0]))
        ratio = float(np.clip((sty.get("target_chroma") or chroma) / max(chroma, 1e-3), 0.6, 1.6))
        c = 1 + k * (ratio - 1)
        a_med = float(np.median([p.ab[0] for p in ok if p.ab] or [0.0]))
        b_med = float(np.median([p.ab[1] for p in ok if p.ab] or [0.0]))
        out.update(lut=[round(float(x), 3) for x in np.maximum.accumulate(lut)], chroma=round(c, 3),
                   tint=round(float(np.clip(0.5 * k * (float(sty.get("target_a", 0)) - a_med * c), -5, 5)), 2),
                   warmth=round(float(np.clip(0.5 * k * (float(sty.get("target_b", 0)) - b_med * c), -7, 7)), 2),
                   target_q=None, contrast=0.0, black=0.0, saturation=1.0)
    elif st.camera_look:
        cams = [p for p in ok if len(p.cam_q) == 64]
        if len(cams) >= max(1, len(ok) // 3):
            cam = np.median(np.array([p.cam_q for p in cams]), axis=0)
            mine = np.median(np.array([p.tone_q for p in cams]), axis=0)
            mine = np.maximum.accumulate(mine + np.arange(64) * 1e-4)
            lut = 0.5 * grid + 0.5 * np.interp(grid, mine, cam)
            ratio = float(np.median([p.cam_chroma / p.chroma for p in cams if p.chroma > 1]) or 1.0) \
                if any(p.chroma > 1 for p in cams) else 1.0
            out.update(lut=[round(float(x), 3) for x in np.maximum.accumulate(lut)],
                       chroma=round(float(np.clip(1 + 0.5 * (ratio - 1), 0.85, 1.25)), 3),
                       contrast=round(float(sty["contrast"]) * 0.5, 3))
    return out


LUT_WARN = "Revisar LUT"


def lut_review(photos: list[Photo], cube: str, strength: float) -> int:
    """Marca las fotos a las que el LUT les sienta claramente peor que al resto de la sesión.

    Se mira la miniatura con el LUT: zonas que se queman o se empastan de más, y cambios de brillo muy
    distintos del cambio típico de la sesión. Devuelve cuántas se marcan (aviso en «Avisos»; no descarta).
    """
    rows = []
    for p in photos:
        p.warn = " · ".join(w for w in p.warn.split(" · ") if w and not w.startswith(LUT_WARN))
        if not p.thumb or p.error:
            continue
        try:
            a = np.asarray(Image.open(io.BytesIO(p.thumb)).convert("RGB"), np.float32) / 255.0
            k = strength if p.lut_strength is None else p.lut_strength
            b = luts.apply(a, cube, k)
        except Exception:
            continue
        la = cv2.cvtColor(a, cv2.COLOR_RGB2Lab)[..., 0]
        lb = cv2.cvtColor(b, cv2.COLOR_RGB2Lab)[..., 0]
        rows.append((p, float((b.max(-1) > 0.985).mean() - (a.max(-1) > 0.985).mean()),
                     float((b.max(-1) < 0.02).mean() - (a.max(-1) < 0.02).mean()),
                     float(lb.mean() - la.mean())))
    if not rows:
        return 0
    med_shift = float(np.median([r[3] for r in rows]))
    n = 0
    for p, hi, lo, shift in rows:
        why = []
        if hi > 0.03:
            why.append(f"quema {hi:.0%}")
        if lo > 0.06:
            why.append(f"empasta {lo:.0%}")
        if abs(shift - med_shift) > 10:
            why.append("brillo muy distinto al resto")
        if why:
            p.warn = (p.warn + " · " if p.warn else "") + f"{LUT_WARN}: " + ", ".join(why)
            n += 1
    return n


def cull(photos: list[Photo], st: Settings) -> None:
    """Marca los descartes. No mueve ni borra nada."""
    ok = [p for p in photos if not p.error]
    for p in ok:
        p.keep, p.reason = True, ""
    if st.cull_exposure:
        for p in ok:
            if p.clipped > 0.30:
                p.keep, p.reason = False, f"Sobreexpuesta ({p.clipped:.0%} quemado)"
            elif p.ev_raw > EV_HOPELESS:
                p.keep, p.reason = False, f"Subexpuesta sin arreglo ({p.ev_raw:+.1f} EV)"
    if st.cull_focus:
        med = float(np.median([p.sharp for p in ok])) if len(ok) >= 5 else 0.0
        thr = max(SHARP_ABS_MIN, SENS[st.sensitivity] * med)
        for p in ok:
            if p.keep and p.sharp < thr:
                p.keep, p.reason = False, "Desenfocada o movida"
    if st.cull_eyes:
        for p in ok:
            if p.keep and p.eyes_open < EYES_CLOSED:
                p.keep, p.reason = False, "Ojos cerrados"
    if st.cull_dupes:
        maxd = {"Baja": 4, "Normal": 6, "Alta": 9}[st.sensitivity]
        seq = sorted(ok, key=lambda p: (p.when or dt.datetime.min, p.name))
        med_sharp = float(np.median([p.sharp for p in ok])) or 1.0
        faced = [p.face_sharp for p in ok if p.face_sharp > 0]
        med_face = float(np.median(faced)) if faced else 1.0
        group = []

        def score(p):
            """Mejor foto de la ráfaga: cara nítida, ojos abiertos y sin quemados."""
            sh = p.face_sharp / med_face if p.face_sharp > 0 else p.sharp / med_sharp
            return sh * (0.35 + 0.65 * p.eyes_open) * (1 - min(0.5, 4 * p.clipped))

        def close(g):
            live = [p for p in g if p.keep]
            if len(live) > 1:
                best = max(live, key=score)
                burst = len({p.when for p in live if p.when}) > 1
                for p in live:
                    if p is not best:
                        p.keep = False
                        p.reason = (f"Ráfaga: la mejor es {best.name}" if burst else f"Casi duplicada de {best.name}")

        for p in seq:
            if group:
                q = group[-1]
                d_first = bin(p.dhash ^ group[0].dhash).count("1")
                d_last = bin(p.dhash ^ q.dhash).count("1")
                near = max(d_first, d_last) <= maxd
                if p.when and q.when:
                    gap = abs((p.when - q.when).total_seconds())
                    # ráfaga: disparos seguidos (≤ 1 s) del mismo encuadre, aunque el sujeto se mueva
                    burst = gap <= 1.0 and d_last <= 2 * maxd
                    near = (near and gap <= 5) or burst
                if not near:
                    close(group)
                    group = []
            group.append(p)
        close(group)


# ------------------------------------------------------------------ revelado

def _nlm(ch: np.ndarray, h: float, search: int) -> np.ndarray:
    u = (np.clip(ch, 0, 1) * 65535).astype(np.uint16)
    d = cv2.fastNlMeansDenoising(u, h=[h * 257], templateWindowSize=7, searchWindowSize=search,
                                 normType=cv2.NORM_L1)
    return d.astype(np.float32) / 65535.0


def develop(ph: Photo, st: Settings) -> np.ndarray:
    """Revelado a resolución completa: exposición, grano, contraste, nitidez, estilo y giro. RGB 0..1."""
    sty = dict(styles.BASE, **(st.style or {}))
    g = tone(load_linear(ph.path, half=False, rebuild_highlights=ph.clipped > 0.002),
             ph.ev + float(sty["ev_bias"]))
    mult = NR_LEVELS[st.nr] * float(sty["nr"])
    # grano: según el ruido MEDIDO en la foto (a resolución completa ≈ el doble que en la de análisis,
    # y corregido por la diferencia de exposición). Si no hay medida, por ISO y exposición.
    # (half_size de LibRaw toma un píxel por celda, no promedia: el ruido medido ya es el de resolución completa)
    scale = 2 ** (ph.ev - ph.ev_auto) * 255
    if ph.noise_y > 0:
        hl = float(np.clip(0.9 * ph.noise_y * scale, 0.6, 12.0)) * mult
        hc = float(np.clip(max(2.4 * hl, 1.8 * ph.noise_c * scale * mult), 1.0, 16.0))
    else:
        hl = (0.8 + 0.35 * (math.log2(max(ph.iso, 100) / 100) + max(ph.ev, 0))) * mult
        hc = min(2.8 * hl, 14.0)
    ycc = cv2.cvtColor(g, cv2.COLOR_RGB2YCrCb)
    del g
    y0 = ycc[..., 0].copy()
    h, w = y0.shape
    # color: el ruido cromático se limpia a media resolución (el ojo no lo nota y es 4x más rápido)
    for i in (1, 2):
        c = cv2.resize(ycc[..., i], (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        c = cv2.bilateralFilter(_nlm(c, hc, 15), 7, 0.03, 4)
        ycc[..., i] = cv2.resize(c, (w, h), interpolation=cv2.INTER_CUBIC)
    y = _nlm(y0, hl, 15) if hl >= 1.4 else y0
    m = np.clip((y - 0.25) / 0.35, 0, 1) * 0.22  # devuelve textura fina solo en zonas claras
    y = y * (1 - m) + y0 * m
    y = y + (y - cv2.GaussianBlur(y, (0, 0), 1.2)) * float(sty["sharpen"]) * np.clip((y - 0.12) / 0.25, 0, 1)
    blk = float(sty["black"])
    y = np.clip((np.clip(y, 0, 1) - blk) / (1 - blk), 0, 1)
    k = float(sty["contrast"])
    y = y * (1 - k) + (y * y * (3 - 2 * y)) * k
    ycc[..., 0] = y
    for i in (1, 2):
        ycc[..., i] = 0.5 + (ycc[..., i] - 0.5) * float(sty["saturation"])
    out = np.clip(cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB), 0, 1)
    del ycc
    out = styles.apply_color(out, sty)
    if sty.get("cube"):
        k = float(sty.get("cube_strength", 1.0)) if ph.lut_strength is None else float(ph.lut_strength)
        out = luts.apply(out, sty["cube"], k)
    out = apply_look(out, photo_look(ph, st))
    if sty.get("bw"):
        bws = photo_bw_style(ph, st)
        if bws != BW_NONE:
            out = bw_editorial(out, seed=ph.dhash & 0xFFFFFFFF, style=bws)
    return np.clip(rotate(out, ph.angle), 0, 1)


BW_NONE = "Sin B/N"  # valor por foto: esta foto no tiene versión en blanco y negro
_BW = dict(mix=(0.42, 0.46, 0.12), local=0.35, sharp=0.25, s=0.55, gamma=1.0, black=0.02, white=0.975,
           grain=0.010, grain_size=1.0, vignette=0.0, tone_sh=(0, 0, 0), tone_hi=(0, 0, 0))
BW_STYLES: dict[str, dict] = {
    "Editorial": dict(_BW),
    "Alto contraste": dict(_BW, mix=(0.45, 0.45, 0.10), local=0.55, s=0.9, gamma=1.05, black=0.0, white=1.0,
                           grain=0.006),
    "Suave mate": dict(_BW, mix=(0.38, 0.48, 0.14), local=0.15, s=0.2, gamma=0.95, black=0.09, white=0.94,
                       grain=0.008),
    "Película clásica": dict(_BW, mix=(0.40, 0.48, 0.12), local=0.3, s=0.65, black=0.03, white=0.97, grain=0.020,
                             grain_size=1.6),
    "Filtro rojo": dict(_BW, mix=(0.75, 0.25, 0.0), s=0.6, grain=0.012),
    "Filtro verde": dict(_BW, mix=(0.20, 0.70, 0.10), s=0.5),
    "Clave alta": dict(_BW, gamma=0.75, local=0.2, s=0.3, black=0.05, white=1.0, grain=0.006),
    "Noir": dict(_BW, gamma=1.35, s=0.85, local=0.45, black=0.0, white=0.96, vignette=0.35, grain=0.015),
    "Selenio": dict(_BW, tone_sh=(-0.010, -0.004, 0.020), tone_hi=(0.006, 0.004, 0.0)),
    "Sepia": dict(_BW, s=0.5, black=0.04, tone_sh=(0.035, 0.012, -0.025), tone_hi=(0.045, 0.022, -0.030)),
}


def bw_editorial(rgb: np.ndarray, seed: int = 0, style: str = "Editorial") -> np.ndarray:
    """Blanco y negro a partir del revelado en color (RGB 0..1, sRGB), en uno de los `BW_STYLES`.

    Todos comparten la misma receta, con distintos parámetros:
    - Mezcla de canales (como los filtros de color en B/N: rojo aclara la piel y oscurece cielos; verde al revés).
    - Contraste local de radio grande (volumen sin halos) y nitidez fina.
    - Clave (gamma) y curva en S; puntos negro y blanco (negros levantados = mate).
    - Grano de luminancia, más en medios tonos; viñeta opcional; virado opcional (selenio, sepia).
    Devuelve RGB 0..1 (neutro salvo en los estilos virados).
    """
    p = BW_STYLES.get(style) or BW_STYLES["Editorial"]
    h, w = rgb.shape[:2]
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    mr, mg, mb = p["mix"]
    y = mr * lin[..., 0] + mg * lin[..., 1] + mb * lin[..., 2]
    y = np.where(y <= 0.0031308, 12.92 * y, 1.055 * np.power(np.maximum(y, 1e-9), 1 / 2.4) - 0.055)
    y = np.clip(y, 0, 1).astype(np.float32)
    if p["gamma"] != 1.0:
        y = np.power(y, p["gamma"]).astype(np.float32)
    mid = 1 - np.abs(y - 0.5) * 2
    if p["local"]:
        base = cv2.GaussianBlur(y, (0, 0), max(4.0, 0.012 * max(h, w)))
        y = y + (y - base) * p["local"] * np.clip(mid * 1.6, 0, 1)
    if p["sharp"]:
        y = y + (y - cv2.GaussianBlur(y, (0, 0), 1.0)) * p["sharp"]
    y = np.clip(y, 0, 1)
    y = (1 - p["s"]) * y + p["s"] * (y * y * (3 - 2 * y))
    y = p["black"] + (p["white"] - p["black"]) * y
    if p["vignette"]:
        yy, xx = np.ogrid[0:h, 0:w]
        r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)) / math.sqrt(2)
        y = y * (1 - p["vignette"] * np.clip((r - 0.35) / 0.65, 0, 1) ** 2)
    if p["grain"]:
        rng = np.random.default_rng(seed)
        g = rng.standard_normal((h, w)).astype(np.float32)
        g = cv2.GaussianBlur(g, (0, 0), (0.6 + 0.0002 * max(h, w)) * p["grain_size"])
        g /= g.std() + 1e-6
        y = y + g * p["grain"] * (0.35 + np.clip(mid, 0, 1))
    y = np.clip(y, 0, 1).astype(np.float32)
    out = np.repeat(y[..., None], 3, axis=2)
    if any(p["tone_sh"]) or any(p["tone_hi"]):
        sh = ((1 - y) ** 2)[..., None] * np.array(p["tone_sh"], np.float32) * 4
        hi = (y ** 2)[..., None] * np.array(p["tone_hi"], np.float32) * 4
        out = np.clip(out + (sh + hi) * (y * (1 - y))[..., None] * 4, 0, 1)
    return out


_LK = dict(warm=0.0, tint=0.0, sat=1.0, contrast=0.0, fade=0.0, sh=(0.0, 0.0), hi=(0.0, 0.0), bright=0.0)
COLOR_LOOKS: dict[str, dict] = {
    "Natural": dict(_LK),
    "Cálido": dict(_LK, warm=5.0, tint=1.0, sat=1.03),
    "Frío": dict(_LK, warm=-5.0, tint=-0.5, sat=0.97),
    "Vivo": dict(_LK, sat=1.22, contrast=0.18),
    "Suave": dict(_LK, sat=0.92, contrast=-0.18, fade=0.02, bright=0.02),
    "Mate": dict(_LK, sat=0.88, contrast=-0.08, fade=0.07),
    "Cinematográfico": dict(_LK, contrast=0.12, sh=(-4.0, -7.0), hi=(3.0, 7.0), sat=0.95),
    "Película": dict(_LK, fade=0.045, sat=0.9, hi=(1.5, 5.0), contrast=0.05),
    "Dorado": dict(_LK, warm=8.0, tint=2.0, hi=(2.0, 6.0), sat=1.05),
    "Noche azul": dict(_LK, warm=-3.0, sh=(1.0, -9.0), contrast=0.1, sat=1.05),
}


def apply_look(rgb01: np.ndarray, name: str) -> np.ndarray:
    """Look de color sobre la imagen ya revelada (sRGB 0..1), en Lab: calidez/matiz, saturación, contraste,
    desvaído (negros levantados), tono en sombras y luces. «Natural» no cambia nada."""
    p = COLOR_LOOKS.get(name)
    if not p or p == _LK:
        return rgb01
    lab = cv2.cvtColor(np.ascontiguousarray(np.clip(rgb01, 0, 1), dtype=np.float32), cv2.COLOR_RGB2Lab)
    L = lab[..., 0] / 100.0
    if p["contrast"]:
        L = L + (L * L * (3 - 2 * L) - L) * p["contrast"] * 2
    if p["bright"]:
        L = L + p["bright"] * (1 - L)
    if p["fade"]:
        L = p["fade"] + (1 - p["fade"]) * L
    L = np.clip(L, 0, 1)
    w_sh = (1 - L) ** 2
    w_hi = L ** 2
    lab[..., 1] = lab[..., 1] * p["sat"] + p["tint"] + w_sh * p["sh"][0] + w_hi * p["hi"][0]
    lab[..., 2] = lab[..., 2] * p["sat"] + p["warm"] + w_sh * p["sh"][1] + w_hi * p["hi"][1]
    lab[..., 0] = L * 100
    return np.clip(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB), 0, 1)


def photo_look(ph: "Photo", st: "Settings") -> str:
    """Look de color de esta foto: el suyo si se eligió a mano, si no el de la sesión."""
    return ph.look or st.look or "Natural"


def photo_bw_style(ph: "Photo", st: "Settings") -> str:
    """Estilo de B/N de esta foto: el suyo si se eligió a mano, si no el de la sesión. `BW_NONE` = sin B/N."""
    return ph.bw_style or st.bw_style or "Editorial"


def _tag(ratio: str) -> str:
    return ratio.replace(":", "x")


def _rat(v):
    try:
        v = v[0] if isinstance(v, (list, tuple)) else v
        if hasattr(v, "num"):
            return TiffImagePlugin.IFDRational(int(v.num), int(v.den) or 1)
        return TiffImagePlugin.IFDRational(float(v))
    except Exception:
        return None


def build_exif(ph: Photo, version: str = "") -> bytes:
    """EXIF básico (cámara, objetivo, exposición, fecha) para los JPG exportados. Orientación ya aplicada."""
    m = ph.meta
    ex = Image.Exif()
    if not m and not is_raw(ph.path):
        try:
            with Image.open(ph.path) as src:
                ex = src.getexif()
        except Exception:
            pass
    else:
        for tag, key in [(0x010F, "make"), (0x0110, "model"), (0x013B, "artist"), (0x8298, "copyright")]:
            if key in m:
                ex[tag] = str(m[key]).strip()
        e = ex.get_ifd(0x8769)
        if "date" in m:
            e[0x9003] = e[0x9004] = str(m["date"])
        if "iso" in m:
            e[0x8827] = int(m["iso"])
        for tag, key in [(0x829A, "exposure"), (0x829D, "fnumber"), (0x920A, "focal")]:
            r = _rat(m[key]) if key in m else None
            if r is not None:
                e[tag] = r
        if "lens" in m:
            e[0xA434] = str(m["lens"]).strip()
    ex[0x0112] = 1
    ex[0x0131] = f"{SOFTWARE} {version}".strip()
    try:
        return ex.tobytes()
    except Exception:
        return b""


def _save(img: np.ndarray, path: str, quality: int, exif: bytes = b"") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    kw = {"exif": exif} if exif else {}
    tmp = path + ".part"
    Image.fromarray((img * 255 + 0.5).astype(np.uint8)).save(
        tmp, "JPEG", quality=quality, subsampling=0, optimize=True, icc_profile=_SRGB_ICC, **kw)
    os.replace(tmp, path)  # un corte a mitad nunca deja un JPG a medias


def export(ph: Photo, st: Settings, out_dir: str, version: str = "") -> list[str]:
    """Revela la foto y guarda el formato sugerido más los formatos elegidos, en cada tamaño."""
    img = develop(ph, st)
    exif = build_exif(ph, version) if st.keep_exif else b""
    H, W = img.shape[:2]
    wanted = [ph.best]
    if st.export_original_ratio and "Original" not in wanted:
        wanted.append("Original")
    wanted += [r for r in st.ratios if r in ph.crops and r not in wanted]
    written = []
    for ratio in wanted:
        x0, y0, x1, y1 = ph.crops[ratio]
        c = img[int(round(y0 * H)):int(round(y1 * H)), int(round(x0 * W)):int(round(x1 * W))]
        folder = os.path.join(out_dir, "Sugeridas") if ratio == ph.best else os.path.join(
            out_dir, "Formatos", _tag(ratio))
        for size in st.sizes:
            ch, cw = c.shape[:2]
            if size == "max":
                r = c
            else:
                px = SIZES[size]
                s = px / cw if size == "1080" else px / max(cw, ch)
                if s >= 1:
                    continue  # no se amplía
                r = cv2.resize(c, (int(round(cw * s)), int(round(ch * s))), interpolation=cv2.INTER_AREA)
                r = np.clip(r + (r - cv2.GaussianBlur(r, (0, 0), 0.6)) * 0.35, 0, 1)
            p = os.path.join(folder, f"{ph.name}_{_tag(ratio) if ratio != 'Original' else 'original'}_{size}.jpg")
            _save(r, p, st.quality, exif)
            written.append(p)
    if st.bw and not st.style.get("bw") and photo_bw_style(ph, st) != BW_NONE:  # B/N del formato sugerido
        x0, y0, x1, y1 = ph.crops[ph.best]
        c = img[int(round(y0 * H)):int(round(y1 * H)), int(round(x0 * W)):int(round(x1 * W))]
        del img
        bw = bw_editorial(c, seed=ph.dhash & 0xFFFFFFFF, style=photo_bw_style(ph, st))
        tag = _tag(ph.best) if ph.best != "Original" else "original"
        for size in st.sizes:
            ch, cw = bw.shape[:2]
            if size == "max":
                r = bw
            else:
                px = SIZES[size]
                s = px / cw if size == "1080" else px / max(cw, ch)
                if s >= 1:
                    continue
                r = cv2.resize(bw, (int(round(cw * s)), int(round(ch * s))), interpolation=cv2.INTER_AREA)
            p = os.path.join(out_dir, "ByN", f"{ph.name}_bn_{tag}_{size}.jpg")
            _save(r, p, st.quality, exif)
            written.append(p)
    return written


def xmp_sidecar(ph: Photo, st: Settings) -> str:
    """XMP de Lightroom/Camera Raw con exposición, giro, recorte sugerido y reducción de ruido.

    Se escribe junto al RAW solo si no existe ya uno (nunca se pisa el trabajo hecho en Lightroom).
    Los descartes se marcan como rechazadas (Rating -1). Devuelve la ruta, o "" si no se escribió.
    """
    if not is_raw(ph.path):
        return ""
    path = os.path.splitext(ph.path)[0] + ".xmp"
    if os.path.exists(path):
        return ""
    x0, y0, x1, y1 = ph.crops.get(ph.best, (0.0, 0.0, 1.0, 1.0))
    nr = {"Suave": 15, "Normal": 25, "Fuerte": 40}.get(st.nr, 25)
    boost = int(min(30, max(0.0, math.log2(max(ph.iso, 100) / 800)) * 8))
    has_crop = ph.best != "Original" or bool(ph.angle)
    body = f"""<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="{SOFTWARE}">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   xmp:CreatorTool="{SOFTWARE}"
   xmp:Rating="{1 if ph.keep else -1}"
   crs:Version="15.0"
   crs:ProcessVersion="11.0"
   crs:HasSettings="True"
   crs:WhiteBalance="As Shot"
   crs:Exposure2012="{ph.ev:+.2f}"
   crs:Highlights2012="-30"
   crs:Shadows2012="+15"
   crs:LuminanceSmoothing="{min(80, nr + boost)}"
   crs:ColorNoiseReduction="{min(60, 25 + boost)}"
   crs:HasCrop="{has_crop}"
   crs:CropTop="{y0:.6f}"
   crs:CropLeft="{x0:.6f}"
   crs:CropBottom="{y1:.6f}"
   crs:CropRight="{x1:.6f}"
   crs:CropAngle="{-ph.angle:.2f}"
   crs:CropConstrainToWarp="0"/>
 </rdf:RDF>
</x:xmpmeta>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


# ------------------------------------------------------------- sesiones grandes: caché y reanudación

_CACHE_SKIP = {"thumb", "preview", "keep", "reason"}


def _sig(ph: Photo, st: Settings) -> str:
    try:
        stt = os.stat(ph.path)
        base = f"{os.path.basename(ph.path)}|{stt.st_size}|{stt.st_mtime_ns}|{st.straighten}|{st.ev_comp:.2f}|v3"
    except OSError:
        return ""
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def save_session(photos: list[Photo], st: Settings, work_dir: str) -> None:
    """Guarda el análisis (sin imágenes) para no repetirlo si se cierra el programa o se reanuda."""
    os.makedirs(work_dir, exist_ok=True)
    rows = {}
    for ph in photos:
        if not ph.thumb or ph.error:
            continue
        sig = _sig(ph, st)
        if not sig:
            continue
        d = {k: v for k, v in ph.__dict__.items() if k not in _CACHE_SKIP}
        d["when"] = ph.when.isoformat() if ph.when else None
        d["meta"] = {k: str(v) if not isinstance(v, (int, float, str)) else v for k, v in ph.meta.items()}
        rows[os.path.basename(ph.path)] = {"sig": sig, "data": d}
        tp = os.path.join(work_dir, os.path.basename(ph.path) + ".thumb.jpg")
        if not os.path.exists(tp):
            with open(tp, "wb") as f:
                f.write(ph.thumb)
    tmp = os.path.join(work_dir, "analisis.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, os.path.join(work_dir, "analisis.json"))


def load_session(photos: list[Photo], st: Settings, work_dir: str) -> int:
    """Recupera el análisis guardado de las fotos que no han cambiado. Devuelve cuántas se recuperaron."""
    try:
        with open(os.path.join(work_dir, "analisis.json"), encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return 0
    n = 0
    for ph in photos:
        r = rows.get(os.path.basename(ph.path))
        tp = os.path.join(work_dir, os.path.basename(ph.path) + ".thumb.jpg")
        if not r or r["sig"] != _sig(ph, st) or not os.path.exists(tp):
            continue
        d = r["data"]
        if d.get("preview_path") and not os.path.exists(d["preview_path"]):
            continue
        for k, v in d.items():
            if hasattr(ph, k):
                setattr(ph, k, v)
        ph.when = dt.datetime.fromisoformat(d["when"]) if d.get("when") else None
        ph.crops = {k: tuple(v) for k, v in d.get("crops", {}).items()}
        with open(tp, "rb") as f:
            ph.thumb = f.read()
        n += 1
    return n


def _done_file(out_dir: str) -> str:
    return os.path.join(out_dir, ".phootflow", "exportadas.txt")


def exported_names(out_dir: str) -> set[str]:
    try:
        with open(_done_file(out_dir), encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except OSError:
        return set()


def analyze_many(photos: list[Photo], st: Settings, workers: int = 2, on_done=None, cancelled=lambda: False):
    """Analiza en paralelo con hilos (OpenCV y LibRaw sueltan el GIL). `on_done(i)` al terminar cada foto.
    Las fotos que ya traen análisis (recuperado de la caché) no se repiten."""
    def one(i):
        if not cancelled() and not photos[i].thumb:
            analyze(photos[i], st)
        return i

    if threading.current_thread() is threading.main_thread():  # si no, lo llama quien lanza el hilo (app.Main.run)
        prepare_detectors(max(1, workers))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for i in ex.map(one, range(len(photos))):
            if on_done and not cancelled():
                on_done(i)


def export_many(photos: list[Photo], st: Settings, out_dir: str, version: str = "", on_done=None,
                cancelled=lambda: False) -> int:
    """Exporta en paralelo (`st.workers` hilos). Un fallo en una foto queda en `ph.error` y no para el lote.
    `on_done(hechas, total)` tras cada foto. Devuelve el número de archivos JPG escritos."""
    done_before = exported_names(out_dir) if st.resume else set()
    lock = threading.Lock()
    os.makedirs(os.path.dirname(_done_file(out_dir)), exist_ok=True)

    def one(ph):
        key = os.path.basename(ph.path)
        if cancelled() or key in done_before:
            return 0
        try:
            n = len(export(ph, st, out_dir, version))
            if st.write_xmp:
                xmp_sidecar(ph, st)
            with lock, open(_done_file(out_dir), "a", encoding="utf-8") as f:
                f.write(key + "\n")
            return n
        except Exception as e:
            ph.error = f"{type(e).__name__}: {e}"
            return 0

    files = 0
    with ThreadPoolExecutor(max_workers=max(1, st.workers)) as ex:
        for k, n in enumerate(ex.map(one, photos), start=1):
            files += n
            if on_done:
                on_done(k, len(photos))
    return files


def move_discards(photos: list[Photo], src_dir: str) -> int:
    """Mueve los originales descartados a <carpeta>/Descartadas. Nunca borra."""
    dest = os.path.join(src_dir, "Descartadas")
    n = 0
    for ph in photos:
        if ph.keep:
            continue
        os.makedirs(dest, exist_ok=True)
        for p in [ph.path] + ph.extras:
            target = os.path.join(dest, os.path.basename(p))
            if os.path.exists(p) and not os.path.exists(target):
                shutil.move(p, target)
                n += 1
    return n


def write_report(photos: list[Photo], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "informe.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f, delimiter=";")
        wr.writerow(["archivo", "estado", "motivo", "formato_sugerido", "exposicion_ev", "giro_grados",
                     "nitidez", "iso", "caras", "aviso"])
        for p in photos:
            wr.writerow([os.path.basename(p.path), "conservada" if p.keep else "descartada",
                         p.reason or p.error, p.best, f"{p.ev:+.2f}", f"{p.angle:+.2f}", f"{p.sharp:.4f}",
                         p.iso or "", p.faces, p.warn])
    return path


def archive_originals(photos: list[Photo], out_dir: str, progress=None) -> str:
    """Guarda los originales conservados en un ZIP sin pérdida y lo verifica. No toca los originales."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "Originales.zip")
    files = [p for ph in photos if ph.keep for p in [ph.path] + ph.extras if os.path.exists(p)]
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for i, p in enumerate(files):
            z.write(p, os.path.basename(p))
            if progress:
                progress(i + 1, len(files))
    with zipfile.ZipFile(tmp) as z:
        bad = z.testzip()
        if bad or len(z.namelist()) != len(files):
            os.remove(tmp)
            raise RuntimeError(f"El ZIP de originales no pasó la verificación ({bad})")
    os.replace(tmp, path)
    return path
