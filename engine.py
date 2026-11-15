"""Motor de EventEdits: análisis, descarte, sugerencia de formato, revelado y exportación."""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import os
import shutil
import zipfile
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps

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

_SRGB_ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
_FACE = None


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
        iso = t.get("EXIF ISOSpeedRatings")
        if iso:
            ph.iso = int(str(iso).split(",")[0].strip("[] "))
        d = t.get("EXIF DateTimeOriginal") or t.get("Image DateTime")
        if d:
            sub = str(t.get("EXIF SubSecTimeOriginal") or "0")
            ph.when = dt.datetime.strptime(str(d), "%Y:%m:%d %H:%M:%S") + dt.timedelta(
                seconds=float("0." + "".join(c for c in sub if c.isdigit()) or 0))
    except Exception:
        pass


def is_raw(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in RAW_EXT


def load_linear(path: str, half: bool) -> np.ndarray:
    """Imagen en sRGB lineal float32, ya orientada."""
    if is_raw(path):
        if rawpy is None:
            raise RuntimeError("Falta la librería rawpy para leer RAW")
        with rawpy.imread(path) as r:
            kw = dict(use_camera_wb=True, no_auto_bright=True, output_bps=16, gamma=(1, 1),
                      output_color=rawpy.ColorSpace.sRGB, highlight_mode=rawpy.HighlightMode.Blend)
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


def _faces(gray8: np.ndarray) -> list[tuple[int, int, int, int]]:
    global _FACE
    if _FACE is None:
        _FACE = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    if _FACE.empty():
        return []
    m = int(0.045 * min(gray8.shape))
    f = _FACE.detectMultiScale(gray8, scaleFactor=1.1, minNeighbors=7, minSize=(m, m))
    return [tuple(int(v) for v in r) for r in f]


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
        g = tone(lin, ph.ev)
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
        faces = _faces(g8)
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
        ph.ev = ev
        del lin
        ph.crops, ph.scores, ph.best = plan_crops(gray, faces)
        t = Image.fromarray((np.clip(g, 0, 1) * 255).astype(np.uint8))
        t.thumbnail((220, 220))
        b = io.BytesIO()
        t.save(b, "JPEG", quality=80)
        ph.thumb = b.getvalue()
    except Exception as e:  # una foto ilegible no para la sesión
        ph.error = f"{type(e).__name__}: {e}"
        ph.keep, ph.reason = False, "No se pudo leer"
    return ph


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
    if st.cull_dupes:
        maxd = {"Baja": 4, "Normal": 6, "Alta": 9}[st.sensitivity]
        seq = sorted(ok, key=lambda p: (p.when or dt.datetime.min, p.name))
        group = []

        def close(g):
            live = [p for p in g if p.keep]
            if len(live) > 1:
                clean = [p for p in live if p.clipped < 0.05] or live
                best = max(clean, key=lambda p: p.sharp)
                for p in live:
                    if p is not best:
                        p.keep, p.reason = False, f"Casi duplicada de {best.name}"

        for p in seq:
            if group:
                q = group[-1]
                near = all(bin(p.dhash ^ o.dhash).count("1") <= maxd for o in (group[0], q))
                if p.when and q.when:
                    near = near and abs((p.when - q.when).total_seconds()) <= 5
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
    """Revelado a resolución completa: exposición, grano, contraste, nitidez y giro. RGB 0..1."""
    g = tone(load_linear(ph.path, half=False), ph.ev)
    mult = NR_LEVELS[st.nr]
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
    y = y + (y - cv2.GaussianBlur(y, (0, 0), 1.2)) * 0.55 * np.clip((y - 0.12) / 0.25, 0, 1)
    y = np.clip((np.clip(y, 0, 1) - 0.035) / 0.965, 0, 1)
    y = y * 0.72 + (y * y * (3 - 2 * y)) * 0.28
    ycc[..., 0] = y
    for i in (1, 2):
        ycc[..., i] = 0.5 + (ycc[..., i] - 0.5) * 1.08
    out = np.clip(cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB), 0, 1)
    return np.clip(rotate(out, ph.angle), 0, 1)


def _tag(ratio: str) -> str:
    return ratio.replace(":", "x")


def _save(img: np.ndarray, path: str, quality: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray((img * 255 + 0.5).astype(np.uint8)).save(
        path, "JPEG", quality=quality, subsampling=0, optimize=True, icc_profile=_SRGB_ICC)


def export(ph: Photo, st: Settings, out_dir: str) -> list[str]:
    """Revela la foto y guarda el formato sugerido más los formatos elegidos, en cada tamaño."""
    img = develop(ph, st)
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
            _save(r, p, st.quality)
            written.append(p)
    return written


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
                     "nitidez", "iso", "caras"])
        for p in photos:
            wr.writerow([os.path.basename(p.path), "conservada" if p.keep else "descartada",
                         p.reason or p.error, p.best, f"{p.ev:+.2f}", f"{p.angle:+.2f}", f"{p.sharp:.4f}",
                         p.iso or "", p.faces])
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
