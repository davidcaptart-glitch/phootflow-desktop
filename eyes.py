"""Detector de ojos cerrados con MediaPipe Face Landmarker (blendshapes eyeBlinkLeft/Right).

Uso:
    import eyes
    eyes.configure(model_path=eyes.ensure_model(dir_modelos), pkgs_dir=None)   # opcional
    eyes.prepare(n_hilos)              # en el hilo principal (opcional, como prepare_detectors)
    scores = eyes.eyes_open_scores(rgb01, faces_yunet)   # en cualquier hilo
    cerrados = [s for s in scores if s is not None and s < eyes.THRESHOLD]

`faces_yunet` es la salida cruda de cv2.FaceDetectorYN.detect (N x 15) en coordenadas de `rgb01`.
Puntuación: 0 = cerrados, 1 = abiertos; None si la cara es pequeña o el landmarker no la encuentra.

Dependencias: numpy, opencv (headless vale), mediapipe>=0.10.21 (se usa solo `mediapipe.tasks`,
que va por ctypes a libmediapipe.dll), absl-py, flatbuffers. matplotlib/sounddevice/opencv-contrib
NO hacen falta: se instalan con `pip install --no-deps mediapipe absl-py flatbuffers`.
"""
from __future__ import annotations

import hashlib
import os
import queue
import sys
import threading
import types

import cv2
import numpy as np

import net

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
MODEL_NAME = "face_landmarker.task"

THRESHOLD = 0.35      # score < THRESHOLD -> ojos cerrados (0.45 = más agresivo: añade miradas abajo y risas)
MIN_FACE_PX = 40      # ancho YuNet mínimo (px en la imagen que se pasa) para opinar
CROP = 256            # lado del recorte que ve el landmarker
MARGIN = 1.7          # lado del recorte = MARGIN * max(w, h) de la cara
# Filtros de calidad (devuelven None: «no se puede juzgar»)
MIN_FRONTAL = 0.2     # distancia entre ojos / tamaño de cara (perfil < ~0.2)
MIN_CONTRAST = 0.15   # p95-p5 de luminancia en la franja de los ojos (0..1)
MIN_SHARP = 0.010     # |Laplaciano| medio / contraste en la franja de los ojos

_cfg = {"model_path": os.environ.get("PHOOTFLOW_FACE_LANDMARKER", ""), "pkgs_dir": None}
_POOL: "queue.Queue[EyeScorer]" = queue.Queue()
_TLS = threading.local()
_KEEP: list = []
_mp_lock = threading.Lock()
_mp = None


# ------------------------------------------------------------------ modelo / configuración

def ensure_model(dest_dir: str) -> str:
    """Descarga (si falta) y verifica face_landmarker.task (3.6 MB, Apache-2.0). Devuelve la ruta."""
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, MODEL_NAME)
    if os.path.exists(path) and _sha256(path) == MODEL_SHA256:
        return path
    tmp = path + ".part"
    with net.urlopen(MODEL_URL, timeout=60) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    if _sha256(tmp) != MODEL_SHA256:
        os.remove(tmp)
        raise RuntimeError("face_landmarker.task: SHA-256 no coincide")
    os.replace(tmp, path)
    return path


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def configure(model_path: str | None = None, pkgs_dir: str | None = None) -> None:
    """Ruta del .task y, opcionalmente, carpeta extra de paquetes (p. ej. una --target de pip)."""
    if model_path:
        _cfg["model_path"] = model_path
    if pkgs_dir:
        _cfg["pkgs_dir"] = pkgs_dir


def _mediapipe():
    """Importa mediapipe.tasks una sola vez (perezoso). Sin matplotlib: se sustituye por un módulo vacío
    porque solo lo usa `drawing_utils`, que aquí no se llama."""
    global _mp
    with _mp_lock:
        if _mp is None:
            if _cfg["pkgs_dir"] and _cfg["pkgs_dir"] not in sys.path:
                sys.path.insert(0, _cfg["pkgs_dir"])
            os.environ.setdefault("GLOG_minloglevel", "2")
            os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
            stubs = []
            for n in ("matplotlib", "matplotlib.pyplot"):
                if n not in sys.modules:
                    try:
                        __import__(n)
                    except ImportError:
                        sys.modules[n] = types.ModuleType(n)
                        stubs.append(n)
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            _mp = (mp, BaseOptions, vision)
        return _mp


# ------------------------------------------------------------------ detector

class EyeScorer:
    """Un FaceLandmarker. NO es seguro entre hilos: un objeto por hilo (ver get_scorer/prepare)."""

    def __init__(self, model_path: str | None = None):
        mp, BaseOptions, vision = _mediapipe()
        path = model_path or _cfg["model_path"]
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"Falta {MODEL_NAME} (usa ensure_model/configure): {path!r}")
        self._mp = mp
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=path),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=3,
            min_face_detection_confidence=0.3,
            min_face_presence_confidence=0.3,
            output_face_blendshapes=True)
        self._lm = vision.FaceLandmarker.create_from_options(opts)

    def close(self):
        self._lm.close()

    def _crop(self, img8: np.ndarray, f: np.ndarray, margin: float, want_m: bool = False):
        """Recorte cuadrado CROPxCROP centrado en la cara y girado para dejar los ojos horizontales."""
        x, y, w, h = (float(v) for v in f[:4])
        rex, rey, lex, ley = (float(v) for v in f[4:8])  # ojo derecho (a la izquierda en la imagen), izquierdo
        ang = np.degrees(np.arctan2(ley - rey, lex - rex))
        cx, cy = x + w / 2, y + h / 2
        s = CROP / (margin * max(w, h))
        m = cv2.getRotationMatrix2D((cx, cy), ang, s)
        m[0, 2] += CROP / 2 - cx
        m[1, 2] += CROP / 2 - cy
        interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
        crop = cv2.warpAffine(img8, m, (CROP, CROP), flags=interp, borderMode=cv2.BORDER_REPLICATE)
        return (crop, m) if want_m else crop

    @staticmethod
    def _clahe(c: np.ndarray) -> np.ndarray:
        """Contraste local en luminancia: rescata caras oscuras/planas de discoteca (sube la detección ~15 %)."""
        lab = cv2.cvtColor(c, cv2.COLOR_RGB2LAB)
        lab[..., 0] = cv2.createCLAHE(2.0, (4, 4)).apply(lab[..., 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    def _detect(self, crop: np.ndarray, eyes_xy: np.ndarray, tol: float):
        """Blendshapes de la cara del recorte cuyos ojos caen donde YuNet dijo (descarta vecinas)."""
        res = self._lm.detect(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop)))
        best, bd = None, tol
        for lms, bsh in zip(res.face_landmarks, res.face_blendshapes):
            # 468/473 = centros de iris (el orden da igual: se prueban las dos asignaciones)
            pts = np.array([[lms[468].x, lms[468].y], [lms[473].x, lms[473].y]], np.float32) * CROP
            d = min(float(np.hypot(*(pts - eyes_xy).T).max()), float(np.hypot(*(pts[::-1] - eyes_xy).T).max()))
            if d < bd:
                best, bd = bsh, d
        return best

    @staticmethod
    def _quality(crop: np.ndarray, ey: np.ndarray, face_px: float) -> dict:
        """Frontalidad, contraste y nitidez en la franja de los ojos del recorte sin retocar."""
        iod = float(np.hypot(*(ey[1] - ey[0])))
        cy = int(ey[:, 1].mean())
        hy, hx = max(4, int(0.12 * face_px)), int(0.45 * face_px)
        g = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        band = g[max(0, cy - hy): cy + hy, max(0, CROP // 2 - hx): CROP // 2 + hx].astype(np.float32)
        if band.size < 16:
            return {"frontal": 0.0, "contrast": 0.0, "sharp": 0.0}
        lo, hi = np.percentile(band, (5, 95))
        lap = np.abs(cv2.Laplacian(cv2.GaussianBlur(band, (0, 0), 1.0), cv2.CV_32F))
        return {"frontal": iod / face_px, "contrast": float(hi - lo) / 255,
                "sharp": float(lap.mean() / (hi - lo + 5))}

    def score_face(self, img8: np.ndarray, f: np.ndarray, detail: bool = False):
        if f[2] < MIN_FACE_PX:
            return None
        plain, m0 = self._crop(img8, f, MARGIN, want_m=True)
        e0 = np.array([[f[4], f[5], 1], [f[6], f[7], 1]], np.float32) @ m0.T.astype(np.float32)
        q = self._quality(plain, e0, CROP / MARGIN)
        if q["frontal"] < MIN_FRONTAL or q["contrast"] < MIN_CONTRAST or q["sharp"] < MIN_SHARP:
            return None
        bsh = None
        for margin, clahe in ((MARGIN, True), (MARGIN, False), (2.4, True)):
            crop, m = self._crop(img8, f, margin, want_m=True)
            ey = np.array([[f[4], f[5], 1], [f[6], f[7], 1]], np.float32) @ m.T.astype(np.float32)
            iod = float(np.hypot(*(ey[1] - ey[0])))
            if clahe:
                crop = self._clahe(crop)
            face_px = CROP / margin
            bsh = self._detect(crop, ey, max(0.6 * iod, 0.25 * face_px))
            if bsh is not None:
                break
        if bsh is None:
            return None
        bs = {c.category_name: c.score for c in bsh}
        bl, br = bs.get("eyeBlinkLeft", 0.0), bs.get("eyeBlinkRight", 0.0)
        # media de los dos ojos con algo más de peso al más abierto: un ojo tapado/girado no condena la cara
        lo, hi = sorted((bl, br))
        closed = 0.65 * lo + 0.35 * hi
        score = float(np.clip(1.0 - closed, 0.0, 1.0))
        if detail:
            return score, dict(q, blinkL=bl, blinkR=br, crop=crop,
                               smile=(bs.get("mouthSmileLeft", 0) + bs.get("mouthSmileRight", 0)) / 2,
                               squint=(bs.get("eyeSquintLeft", 0) + bs.get("eyeSquintRight", 0)) / 2)
        return score

    def scores(self, rgb01: np.ndarray, faces_yunet: np.ndarray) -> list:
        if faces_yunet is None or len(faces_yunet) == 0:
            return []
        img8 = (np.clip(rgb01[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8)
        return [self.score_face(img8, np.asarray(f, np.float32)) for f in faces_yunet]


# ------------------------------------------------------------------ hilos

def prepare(n: int, model_path: str | None = None) -> None:
    """Crea `n` detectores (llamar desde el hilo principal, como engine.prepare_detectors)."""
    for _ in range(n):
        d = EyeScorer(model_path)
        _KEEP.append(d)
        _POOL.put(d)


def get_scorer() -> EyeScorer:
    """Detector del hilo actual: toma uno del pool o lo crea perezosamente."""
    d = getattr(_TLS, "scorer", None)
    if d is None:
        try:
            d = _POOL.get_nowait()
        except queue.Empty:
            d = EyeScorer()
            _KEEP.append(d)
        _TLS.scorer = d
    return d


def eyes_open_scores(rgb01: np.ndarray, faces_yunet: np.ndarray) -> list[float]:
    """Por cara: 0 = ojos cerrados … 1 = abiertos; None si la cara es demasiado pequeña / no se encuentra."""
    return get_scorer().scores(rgb01, faces_yunet)
