"""Clasificación zero-shot del TIPO DE EVENTO de una foto con CLIP ViT-B/32 (ONNX cuantizado).

Dependencias en tiempo de ejecución: numpy, opencv-python-headless (solo para redimensionar) y onnxruntime.
El modelo de imagen (~89 MB, licencia MIT) se descarga la primera vez con `ensure_model`; los embeddings
de texto ya vienen precalculados en `text_embeddings.npy` + `text_embeddings.json` (orden de claves).

Uso:
    path = ensure_model(os.path.join(cfg_dir, "modelos"), progress=lambda done, total: ...)
    ai = SceneAI(path)                       # carga perezosa; un objeto compartido entre hilos vale
    probs = ai.scores(rgb01)                 # {"Boda": 0.71, "Deporte": 0.02, ...}
    tipo, media = SceneAI.aggregate([probs1, probs2, ...])   # tipo de la sesión
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.request

import net

import cv2
import numpy as np

MODEL_URL = ("https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/"
             "6b7c523703ae26e39be4b03774966f8571c7f523/onnx/vision_model_quantized.onnx")
MODEL_FILE = "clip-vit-b32-vision-quantized.onnx"
MODEL_SHA256 = "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"
MODEL_SIZE = 89_117_001

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(dest_dir: str, progress=None) -> str:
    """Devuelve la ruta del modelo; lo descarga y verifica (SHA-256) si falta o está corrupto.

    `progress(done_bytes, total_bytes)` se llama durante la descarga. Lanza RuntimeError si la
    suma no coincide (y borra el fichero descargado)."""
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, MODEL_FILE)
    if os.path.exists(path) and os.path.getsize(path) == MODEL_SIZE:
        return path                      # comprobado al descargarlo; no rehacer el hash en cada arranque
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
        raise RuntimeError("El modelo descargado no supera la verificación SHA-256")
    os.replace(tmp, path)
    return path


def preprocess(rgb01: np.ndarray) -> np.ndarray:
    """RGB 0..1 (sRGB, HxWx3) -> tensor 1x3x224x224 como el CLIPImageProcessor (lado corto 224 + recorte central)."""
    x = np.asarray(rgb01, dtype=np.float32)
    if x.ndim == 2:
        x = np.repeat(x[..., None], 3, axis=2)
    x = x[..., :3]
    h, w = x.shape[:2]
    s = 224 / min(h, w)
    nh, nw = max(224, round(h * s)), max(224, round(w * s))
    x = cv2.resize(x, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    y0, x0 = (nh - 224) // 2, (nw - 224) // 2
    x = np.clip(x[y0:y0 + 224, x0:x0 + 224], 0, 1)
    x = (x - _MEAN) / _STD
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None])


class SceneAI:
    """Clasificador zero-shot. Seguro con hilos: la sesión se crea una vez (con lock) y
    `InferenceSession.run` de onnxruntime admite llamadas concurrentes."""

    def __init__(self, model_path: str, emb_path: str | None = None, threads: int = 2):
        self.model_path = model_path
        emb_path = emb_path or os.path.join(_HERE, "assets", "text_embeddings.npy")
        with open(os.path.splitext(emb_path)[0] + ".json", encoding="utf-8") as fh:
            meta = json.load(fh)
        self.keys: list[str] = meta["keys"]
        self.logit_scale = float(meta.get("logit_scale", 100.0))
        t = np.load(emb_path).astype(np.float32)
        self.text = t / np.linalg.norm(t, axis=1, keepdims=True)
        self.threads = threads
        self._sess = None
        self._lock = threading.Lock()

    def _session(self):
        if self._sess is None:
            with self._lock:
                if self._sess is None:
                    import onnxruntime as ort
                    so = ort.SessionOptions()
                    so.intra_op_num_threads = self.threads
                    so.inter_op_num_threads = 1
                    so.log_severity_level = 3
                    self._sess = ort.InferenceSession(self.model_path, so, providers=["CPUExecutionProvider"])
        return self._sess

    def embed(self, rgb01: np.ndarray) -> np.ndarray:
        e = self._session().run(None, {"pixel_values": preprocess(rgb01)})[0][0]
        return e / (np.linalg.norm(e) + 1e-9)

    def scores_from_embed(self, emb: np.ndarray) -> dict[str, float]:
        z = self.logit_scale * (self.text @ emb)
        p = np.exp(z - z.max())
        p /= p.sum()
        return {k: float(v) for k, v in zip(self.keys, p)}

    def scores(self, rgb01: np.ndarray) -> dict[str, float]:
        """Probabilidad de cada tipo de evento (suman 1)."""
        return self.scores_from_embed(self.embed(rgb01))

    @staticmethod
    def aggregate(per_photo: list[dict[str, float]]) -> tuple[str, dict[str, float]]:
        """Tipo de la sesión: media de probabilidades de todas las fotos."""
        if not per_photo:
            return "", {}
        keys = list(per_photo[0])
        m = {k: float(np.mean([p[k] for p in per_photo])) for k in keys}
        return max(m, key=m.get), m
