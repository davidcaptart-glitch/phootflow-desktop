"""PhootFlow Edits: retoque tipo Photoshop, no destructivo (motor sin interfaz).

Una foto en Edits es un documento JSON en `<carpeta>/.phootflow/edits/<foto>.json`:

    {"version": 1, "base": {...ajustes de Studio de partida...} | None, "layers": [capa, ...],
     "_events_override": False}

La imagen de partida es el revelado de Studio (si la foto tiene ajustes allí), si no `base` (la edición de
Events o un revelado automático). Encima se apilan capas paramétricas que se vuelven a calcular a cualquier
resolución (vista de 2048 px o exportación a tamaño completo):

- Capas de imagen (orden libre, coordenadas 0..1 sobre la foto revelada SIN recortar, ya girada):
  "liquify"    strokes: [{"mode": push|bloat|pucker, "r", "s", "pts": [[x, y], ...]}]
  "retouch"    ops: [{"tool": heal|clone|fill, "r", "pts", "src": [dx, dy] | None, "hard"}]
  "skin"       amount, texture, auto (caras/piel con IA), strokes [[x, y, r, ±1]] (añadir/quitar)
  "light"      strokes: [{"mode": dodge|burn|whiten, "r", "s", "pts"}] (ojeras, ojos, dientes)
  "background" mode: blur|bw|color|transparent, blur, color
- Capas superpuestas (siempre encima y sobre la foto ya recortada): "text" y "logo".

`r` es el radio relativo al lado largo; `src` es el desplazamiento del origen (fracción de ancho y alto).
Cada capa tiene `visible` y `opacity` (0..100). Nada toca el archivo original.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid

import cv2
import numpy as np

import engine as E
import studio_engine as SE

DOC_VERSION = 1
KINDS = {"liquify": "Licuar", "retouch": "Retoque", "skin": "Piel", "light": "Ojos y luz",
         "background": "Fondo", "text": "Texto", "logo": "Logo", "image": "Imagen"}
OVERLAY = ("text", "logo", "image")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp")
CUTS = {"none": "No quitar el fondo", "color": "Logo o fondo liso (por color)",
        "object": "Objeto o producto (automático)", "person": "Persona (IA)"}
PIXEL_ORDER = ("liquify", "retouch", "skin", "light", "background")  # orden por defecto al crear capas
ANCHORS = {"lt": (0, 0), "mt": (0.5, 0), "rt": (1, 0), "lm": (0, 0.5), "mm": (0.5, 0.5), "rm": (1, 0.5),
           "lb": (0, 1), "mb": (0.5, 1), "rb": (1, 1)}
FORMATS = {"jpg": "JPG", "tif": "TIFF 16 bits", "png": "PNG (con transparencia)"}


# ------------------------------------------------------------------ documento

def new_doc(base: dict | None = None) -> dict:
    return {"version": DOC_VERSION, "base": base, "layers": [], "_events_override": False}


def new_layer(kind: str, **kw) -> dict:
    p = {"liquify": dict(strokes=[]),
         "retouch": dict(ops=[]),
         "skin": dict(amount=55, texture=45, auto=True, strokes=[]),
         "light": dict(strokes=[]),
         "background": dict(mode="blur", blur=40, color="#ffffff", strokes=[], detect="person", key_color=None,
                            tol=12, soft=8),
         "text": dict(text="© PhootFlow", font="Arial", size=3.0, color="#ffffff", x=0.97, y=0.965, anchor="rb",
                      shadow=True),
         "logo": dict(path="", width=16.0, x=0.97, y=0.965, anchor="rb", knockout=False, tol=12),
         "image": dict(path="", width=40.0, x=0.5, y=0.5, anchor="mm", cut="none", tol=12, angle=0.0,
                       flip=False)}[kind]
    p.update(kw)
    return {"id": uuid.uuid4().hex[:8], "kind": kind, "name": KINDS[kind], "visible": True, "opacity": 100, **p}


def add_layer(doc: dict, kind: str, **kw) -> dict:
    """Añade la capa en su sitio natural: las de imagen por debajo de las superpuestas."""
    lay = new_layer(kind, **kw)
    layers = doc["layers"]
    if kind in OVERLAY:
        layers.append(lay)
    else:
        rank = PIXEL_ORDER.index(kind)
        i = 0
        while i < len(layers) and layers[i]["kind"] not in OVERLAY and PIXEL_ORDER.index(layers[i]["kind"]) <= rank:
            i += 1
        layers.insert(i, lay)
    return lay


def edits_dir(folder: str) -> str:
    return os.path.join(folder, ".phootflow", "edits")


def doc_path(folder: str, photo_path: str) -> str:
    return os.path.join(edits_dir(folder), os.path.basename(photo_path) + ".json")


def load(folder: str, photo_path: str) -> dict | None:
    try:
        with open(doc_path(folder, photo_path), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    d.setdefault("layers", [])
    d.setdefault("base", None)
    return d


def save(folder: str, photo_path: str, doc: dict) -> None:
    p = doc_path(folder, photo_path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(p + ".tmp", p)


def has_edits(doc: dict | None) -> bool:
    return bool(doc and any(_layer_active(l) for l in doc.get("layers", [])))


def base_settings(folder: str, photo_path: str, doc: dict | None) -> dict:
    """Ajustes de revelado de partida: los de Studio (en vivo) si existen; si no, los guardados en el documento."""
    s = SE.load(folder, photo_path)
    if s is not None:
        return s
    if doc and doc.get("base"):
        return dict(ORIGINAL) if doc["base"].get("_original") else dict(SE.DEFAULTS, **doc["base"])
    return SE.new_settings()


ORIGINAL = {"_original": True, "crop": [0.0, 0.0, 1.0, 1.0]}


def load_original(path: str, max_px: int | None = None) -> np.ndarray:
    """sRGB 0..1 de un JPG/PNG/TIFF tal cual (lo transparente, sobre blanco)."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path))
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        white = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(white, im)
    im = im.convert("RGB")
    if max_px and max(im.size) > max_px:
        im.thumbnail((max_px, max_px), Image.LANCZOS)
    return np.asarray(im, np.float32) / 255.0


def render_base(path: str, s: dict, full: bool, max_px: int | None = None, key: str = "") -> np.ndarray:
    """Imagen de partida sin recortar: el revelado `s` o, con `_original`, el archivo tal cual."""
    if s.get("_original") and not E.is_raw(path):
        return load_original(path, None if full else max_px)
    lin = E.load_linear(path, half=not full, rebuild_highlights=full)
    if max_px and not full:
        f = max_px / max(lin.shape[:2])
        if f < 1:
            lin = cv2.resize(lin, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return SE.render(lin.astype(np.float32), s, seed=hash(os.path.basename(path)) & 0xFFFF, crop=False, key=key)


def auto_base(lin: np.ndarray) -> dict:
    """Revelado automático para una foto sin Studio ni Events: exposición medida y luces recogidas."""
    ev, _, clip = E.estimate_ev(lin)
    return SE.new_settings(exposure=round(float(ev), 2), highlights=-25 if clip > 0.01 else -10, shadows=15)


# ------------------------------------------------------------------ utilidades

def _side(img) -> int:
    return max(img.shape[:2])


def _hex(c: str) -> np.ndarray:
    c = (c or "#ffffff").lstrip("#")
    if len(c) != 6:
        c = "ffffff"
    return np.array([int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], np.float32)


def _px(pts, w, h) -> np.ndarray:
    a = np.asarray(pts, np.float32).reshape(-1, 2)
    return np.stack([a[:, 0] * (w - 1), a[:, 1] * (h - 1)], 1)


def stroke_mask(shape, pts, r_px: float) -> np.ndarray:
    """Máscara dura (0/1 float32) de un trazo de pincel de radio `r_px` que pasa por `pts` (en píxeles)."""
    m = np.zeros(shape[:2], np.uint8)
    p = np.round(np.asarray(pts, np.float32)).astype(np.int32).reshape(-1, 2)
    r = max(1, int(round(r_px)))
    if len(p) == 1:
        cv2.circle(m, tuple(int(v) for v in p[0]), r, 1, -1, cv2.LINE_AA)
    else:
        cv2.polylines(m, [p], False, 1, 2 * r, cv2.LINE_AA)
        cv2.circle(m, tuple(int(v) for v in p[0]), r, 1, -1, cv2.LINE_AA)
        cv2.circle(m, tuple(int(v) for v in p[-1]), r, 1, -1, cv2.LINE_AA)
    return m.astype(np.float32)


def _soft(m: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(m, (0, 0), max(0.6, sigma)) if sigma > 0 else m


def _bbox(m: np.ndarray, pad: int):
    ys, xs = np.nonzero(m > 0.01)
    if not len(xs):
        return None
    h, w = m.shape[:2]
    return (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
            min(w, int(xs.max()) + pad + 1), min(h, int(ys.max()) + pad + 1))


def _lowpass(x: np.ndarray, sigma: float) -> np.ndarray:
    return SE._gauss(np.ascontiguousarray(x, dtype=np.float32), max(0.8, sigma))


def _inpaint_low(img: np.ndarray, hole: np.ndarray, sigma: float) -> np.ndarray:
    """Baja frecuencia del destino con el agujero rellenado desde el borde (cv2.inpaint a escala reducida)."""
    h, w = hole.shape
    f = min(1.0, 160.0 / max(h, w))
    small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img.copy()
    hs = cv2.resize(hole, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_LINEAR) if f < 1 else hole
    hm = (hs > 0.02).astype(np.uint8)
    hm = cv2.dilate(hm, np.ones((3, 3), np.uint8))
    out = np.empty_like(small)
    for c in range(3):
        # en 16 bits: la versión float32 de cv2.inpaint sesga el tono (medido: 0.46 donde debía dar 0.50)
        ch = (np.clip(small[..., c], 0, 1) * 65535 + 0.5).astype(np.uint16)
        out[..., c] = cv2.inpaint(ch, hm, 3, cv2.INPAINT_TELEA).astype(np.float32) / 65535.0
    if f < 1:
        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)
    return _lowpass(out, sigma)


# ------------------------------------------------------------------ retoque: corrector, tampón y relleno

def find_source(img: np.ndarray, op: dict) -> list[float] | None:
    """Busca el mejor origen para un trazo del corrector / relleno (como el «quitar» de Lightroom).

    Compara el anillo que rodea la zona a tapar con el mismo anillo desplazado en muchas direcciones y
    distancias, y se queda con el que mejor encaja (textura y tono), evitando que el origen pise la zona.
    Devuelve [dx, dy] como fracción de ancho y alto.
    """
    h, w = img.shape[:2]
    side = max(h, w)
    r = max(1.5, float(op["r"]) * side)
    pts = _px(op["pts"], w, h)
    hard = stroke_mask(img.shape, pts, r)
    bb = _bbox(hard, int(r * 1.2) + 2)
    if bb is None:
        return None
    x0, y0, x1, y1 = bb
    bw, bh = x1 - x0, y1 - y0
    # todo a escala reducida para que la búsqueda sea instantánea
    f = min(1.0, 48.0 / max(bw, bh))
    sm = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img
    hm = cv2.resize(hard, (sm.shape[1], sm.shape[0]), interpolation=cv2.INTER_AREA) if f < 1 else hard
    ring_full = cv2.dilate((hm > 0.05).astype(np.uint8), np.ones((5, 5), np.uint8), iterations=max(1, int(r * f * 0.6)))
    sx0, sy0 = int(x0 * f), int(y0 * f)
    sx1, sy1 = max(sx0 + 2, int(np.ceil(x1 * f))), max(sy0 + 2, int(np.ceil(y1 * f)))
    # en pequeño el redondeo puede salirse un píxel de la imagen: la caja se ajusta a lo que hay
    sx1, sy1 = min(sx1, sm.shape[1]), min(sy1, sm.shape[0])
    sx0, sy0 = min(sx0, sx1 - 1), min(sy0, sy1 - 1)
    hole = hm[sy0:sy1, sx0:sx1] > 0.05
    ring = (ring_full[sy0:sy1, sx0:sx1] > 0) & ~hole
    if ring.sum() < 4:
        ring = ~hole
    dst = sm[sy0:sy1, sx0:sx1]
    dmean = dst[ring].mean(0)
    H, W = sm.shape[:2]
    pw, ph_ = sx1 - sx0, sy1 - sy0
    span = max(pw, ph_)
    wide = op.get("tool") == "fill"
    dists = [1.1, 1.5, 2.0, 2.8, 4.0] + ([5.5, 7.5, 10.0] if wide else [])
    best, best_score = None, np.inf
    for d in dists:
        for k in range(24):
            a = 2 * np.pi * k / 24
            dx, dy = int(round(np.cos(a) * d * span)), int(round(np.sin(a) * d * span))
            tx, ty = sx0 + dx, sy0 + dy
            if tx < 0 or ty < 0 or tx + pw > W or ty + ph_ > H:
                continue
            src = sm[ty:ty + ph_, tx:tx + pw]
            # el origen no puede contener la propia zona a tapar
            if (hm[ty:ty + ph_, tx:tx + pw] > 0.05)[hole].mean() > 0.02:
                continue
            ring_err = np.mean((src[ring] - src[ring].mean(0) - (dst[ring] - dmean)) ** 2)
            tone_err = np.mean((src[ring].mean(0) - dmean) ** 2)
            inner = src[hole]
            # castiga orígenes con bordes fuertes dentro (otra mancha, un pelo, un contraste)
            busy = float(np.mean(np.abs(inner - inner.mean(0)))) if len(inner) else 0.0
            score = ring_err + 0.35 * tone_err + 0.15 * busy * busy + 1e-5 * d
            if score < best_score:
                best, best_score = (dx / f, dy / f), score
    if best is None:
        return None
    return [round(best[0] / w, 5), round(best[1] / h, 5)]


def _apply_op(img: np.ndarray, op: dict) -> np.ndarray:
    h, w = img.shape[:2]
    side = max(h, w)
    r = max(1.0, float(op["r"]) * side)
    tool = op.get("tool", "heal")
    hard = stroke_mask(img.shape, _px(op["pts"], w, h), r)
    pad = int(r * 2.5) + 4
    bb = _bbox(hard, pad)
    src_off = op.get("src")
    if bb is None or src_off is None:
        if bb is None or tool == "clone":
            return img
        src_off = [0.0, 0.0]  # sin origen válido: solo relleno desde el borde
        tool = "inpaint"
    x0, y0, x1, y1 = bb
    dx, dy = int(round(src_off[0] * w)), int(round(src_off[1] * h))
    # el origen tiene que caber en la foto: se recorta la caja a lo que cabe por los dos lados
    x0, y0 = max(x0, -dx, 0), max(y0, -dy, 0)
    x1, y1 = min(x1, w - dx, w), min(y1, h - dy, h)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return img
    m = hard[y0:y1, x0:x1]
    dst = img[y0:y1, x0:x1]
    src = img[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    hardness = float(op.get("hard", 0.5 if tool == "clone" else 0.25))
    feather = r * (1.0 - hardness) * 0.6
    if tool == "clone":
        res = src
    else:
        # corrector: textura del origen + tono y color del entorno del destino (separación de frecuencias)
        sigma = r * (0.55 if tool == "heal" else 0.4)
        low_dst = _inpaint_low(dst, m, sigma)
        if tool == "inpaint":
            res = low_dst
        else:
            res = src - _inpaint_low(src, m, sigma) + low_dst
    # el difuminado crece hacia fuera: dentro del pincel se tapa del todo
    feather = max(1.0, feather)
    grow = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=max(1, int(feather * 1.6)))
    alpha = np.maximum(m, _soft(grow, feather))
    out = img.copy()
    a = np.clip(alpha, 0, 1)[..., None]
    out[y0:y1, x0:x1] = dst * (1 - a) + np.clip(res, 0, 1) * a
    return out


def apply_retouch(img: np.ndarray, lay: dict) -> np.ndarray:
    for op in lay.get("ops", []):
        img = _apply_op(img, op)
    return img


# ------------------------------------------------------------------ IA de persona (fondo y piel)

def person_probs(img: np.ndarray, key: str = "") -> np.ndarray | None:
    """Probabilidades de MediaPipe (fondo, pelo, piel cuerpo, piel cara, ropa, otros) al tamaño de `img`.
    Se calcula a 1024 px (rápido y estable entre la vista y la exportación). None si no hay modelo."""
    try:
        import masks as M
        f = min(1.0, 1024.0 / _side(img))
        small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img
        pr = M.segment(np.ascontiguousarray(small, dtype=np.float32), key + "#edits1024" if key else "")
    except Exception:
        return None
    h, w = img.shape[:2]
    return np.stack([cv2.resize(np.ascontiguousarray(pr[..., c]), (w, h), interpolation=cv2.INTER_LINEAR)
                     for c in range(pr.shape[2])], -1)


def _refine(alpha: np.ndarray, guide: np.ndarray) -> np.ndarray:
    try:
        import masks as M
        return M._refine(np.ascontiguousarray(alpha, dtype=np.float32), guide)
    except Exception:
        return alpha


def _skin_fallback(img: np.ndarray) -> np.ndarray:
    """Tonos de piel por color (YCrCb) cuando no hay modelo de IA."""
    ycc = cv2.cvtColor(np.ascontiguousarray(img, dtype=np.float32), cv2.COLOR_RGB2YCrCb)
    cr, cb = ycc[..., 1] * 255, ycc[..., 2] * 255
    a = np.clip((cr - 133) / 8, 0, 1) * np.clip((173 - cr) / 8, 0, 1) * \
        np.clip((cb - 77) / 8, 0, 1) * np.clip((127 - cb) / 8, 0, 1)
    return cv2.GaussianBlur(a.astype(np.float32), (0, 0), max(1.5, 0.003 * _side(img)))


def skin_mask(img: np.ndarray, lay: dict, key: str = "") -> np.ndarray:
    h, w = img.shape[:2]
    a = np.zeros((h, w), np.float32)
    if lay.get("auto", True):
        pr = person_probs(img, key)
        if pr is not None:
            a = _refine(np.clip(pr[..., 3] + 0.85 * pr[..., 2], 0, 1).astype(np.float32), img)
        else:
            a = _skin_fallback(img)
    return paint_mask(a, lay.get("strokes", []))


def paint_mask(a: np.ndarray, strokes: list) -> np.ndarray:
    """Retoca una máscara con trazos de pincel [x, y, r, ±1] (1 añade, -1 quita), con borde suave."""
    if not strokes:
        return a
    h, w = a.shape
    side = max(h, w)
    a = a.copy()
    for st in strokes:
        x, y, r = st[:3]
        m = stroke_mask((h, w), [[x * (w - 1), y * (h - 1)]], r * side)
        a = a * (1 - m) if len(st) > 3 and st[3] < 0 else np.maximum(a, m)
    return np.clip(cv2.GaussianBlur(a, (0, 0), max(1.0, 0.004 * side)), 0, 1)


def apply_skin(img: np.ndarray, lay: dict, key: str = "") -> np.ndarray:
    """Suavizado de piel por separación de frecuencias: se alisa el tono (granos, rojeces, poros grandes) con
    un filtro que respeta los bordes y se devuelve la textura fina para que la piel no parezca de plástico."""
    amount = float(lay.get("amount", 55)) / 100.0
    if amount <= 0:
        return img
    texture = float(lay.get("texture", 45)) / 100.0
    k = _side(img) / 2048.0
    smooth = np.stack([SE.guided(np.ascontiguousarray(img[..., c]), 7.0 * k, 0.0025) for c in range(3)], -1)
    fine = img - cv2.GaussianBlur(img, (0, 0), max(0.6, 1.1 * k))
    target = smooth + fine * texture
    a = skin_mask(img, lay, key)[..., None] * amount
    return np.clip(img + (target - img) * a, 0, 1)


DETECTS = {"person": "Persona (IA)", "object": "Objeto o producto (automático)",
           "color": "Logo o fondo liso (por color)"}


def border_color(img: np.ndarray) -> np.ndarray:
    """Color del fondo de un logo o un producto: la mediana del borde de la imagen."""
    h, w = img.shape[:2]
    b = max(1, min(h, w) // 50)
    edge = np.concatenate([img[:b].reshape(-1, 3), img[-b:].reshape(-1, 3), img[:, :b].reshape(-1, 3),
                           img[:, -b:].reshape(-1, 3)])
    return np.median(edge, 0).astype(np.float32)


def key_alpha(img: np.ndarray, color=None, tol: float = 12, soft: float = 8, contiguous: bool = False):
    """Alfa del sujeto quitando un fondo de color liso (logos, fondos de estudio). `tol` y `soft` en ΔE (Lab).
    Con `contiguous`, solo se quita el fondo que toca el borde (el blanco de dentro de una letra se queda).
    Devuelve (alfa, color del fondo)."""
    img = np.ascontiguousarray(img, dtype=np.float32)
    bg = border_color(img) if color is None else (_hex(color) if isinstance(color, str) else
                                                   np.asarray(color, np.float32))
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2Lab)
    ref = cv2.cvtColor(bg.reshape(1, 1, 3), cv2.COLOR_RGB2Lab)[0, 0]
    d = np.sqrt(((lab - ref) ** 2).sum(-1))
    tol, soft = max(0.5, float(tol)), max(0.5, float(soft))
    # borde semitransparente: cada píxel se compara con el color puro del sujeto que tiene al lado (máximo
    # local de la distancia), así el antialias de un logo queda a medias y no con un halo del fondo
    dmax = cv2.dilate(d.astype(np.float32), np.ones((5, 5), np.uint8))
    a = np.clip((d - tol) / np.maximum(dmax - tol, soft), 0, 1).astype(np.float32)
    if contiguous:
        cand = (a < 0.999).astype(np.uint8)
        n, lab_cc = cv2.connectedComponents(cand, connectivity=8)
        touch = np.unique(np.concatenate([lab_cc[0], lab_cc[-1], lab_cc[:, 0], lab_cc[:, -1]]))
        keep_bg = np.isin(lab_cc, touch[touch > 0])
        a = np.where(keep_bg, a, 1.0).astype(np.float32)
    return a, bg


def decontaminate(img: np.ndarray, alpha: np.ndarray, bg) -> np.ndarray:
    """Quita el halo del color del fondo en los bordes semitransparentes (el blanco alrededor de un logo)."""
    a = alpha[..., None]
    fg = (img - (1 - a) * np.asarray(bg, np.float32)) / np.maximum(a, 0.05)
    edge = (a > 0.02) & (a < 0.98)
    return np.where(edge, np.clip(fg, 0, 1), img).astype(np.float32)


def object_alpha(img: np.ndarray, strokes: list | None = None) -> np.ndarray:
    """Recorte automático de un objeto o producto (GrabCut de OpenCV, sin IA ni descargas). Los trazos del
    pincel de máscara le dicen qué es sujeto (+1) y qué es fondo (-1)."""
    h, w = img.shape[:2]
    f = min(1.0, 640.0 / max(h, w))
    small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img
    sh, sw = small.shape[:2]
    u8 = np.ascontiguousarray((np.clip(small, 0, 1) * 255).astype(np.uint8)[..., ::-1])
    mask = np.zeros((sh, sw), np.uint8)
    mx, my = max(2, int(sw * 0.03)), max(2, int(sh * 0.03))
    rect = (mx, my, sw - 2 * mx, sh - 2 * my)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(u8, mask, rect, bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
        if strokes:
            for st in strokes:
                x, y, r = st[:3]
                val = cv2.GC_BGD if len(st) > 3 and st[3] < 0 else cv2.GC_FGD
                cv2.circle(mask, (int(x * (sw - 1)), int(y * (sh - 1))), max(1, int(r * max(sh, sw))), int(val), -1)
            cv2.grabCut(u8, mask, None, bgd, fgd, 3, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return np.ones((h, w), np.float32)
    a = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1.0, 0.0).astype(np.float32)
    a = cv2.GaussianBlur(a, (0, 0), 0.8)
    if f < 1:
        a = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
    return _refine(a, img)


def subject_alpha(img: np.ndarray, lay: dict, key: str = ""):
    """Alfa del sujeto según el detector de la capa Fondo. Devuelve (alfa | None, color de fondo | None)."""
    det = lay.get("detect", "person")
    strokes = lay.get("strokes", [])
    bg = None
    if det == "color":
        a, bg = key_alpha(img, lay.get("key_color"), lay.get("tol", 12), lay.get("soft", 8),
                          bool(lay.get("contiguous", False)))
    elif det == "object":
        a = object_alpha(img, strokes)
    else:
        pr = person_probs(img, key)
        if pr is None and not strokes:
            return None, None
        a = _refine(np.clip(1 - pr[..., 0], 0, 1).astype(np.float32), img) if pr is not None else \
            np.zeros(img.shape[:2], np.float32)
    # el pincel de máscara corrige lo que el detector no ve (un cartel, un ramo, una letra)
    return paint_mask(a, strokes), bg


def apply_background(img: np.ndarray, lay: dict, key: str = ""):
    """Fondo: desenfocado (sin halo del sujeto), en B/N, de un color o transparente. Devuelve (img, alfa|None)."""
    a, keyed = subject_alpha(img, lay, key)
    if a is None:
        return img, None
    if keyed is not None:
        img = decontaminate(img, a, keyed)
    mode = lay.get("mode", "blur")
    if mode == "transparent":
        return img, a
    a3 = a[..., None]
    if mode == "blur":
        sigma = max(1.0, float(lay.get("blur", 40)) / 100.0 * 0.02 * _side(img))
        wbg = (1 - a3) ** 2
        bg = _lowpass(img * wbg, sigma) / np.maximum(_lowpass(np.repeat(wbg, 3, 2), sigma), 1e-3)
        bg = np.where(_lowpass(np.repeat(wbg, 3, 2), sigma) > 1e-3, bg, _lowpass(img, sigma))
    elif mode == "bw":
        g = (0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2])[..., None]
        bg = np.repeat(g, 3, 2)
    else:
        bg = np.broadcast_to(_hex(lay.get("color", "#ffffff")), img.shape)
    return np.clip(img * a3 + bg * (1 - a3), 0, 1), None


# ------------------------------------------------------------------ ojos y luz (sobreexponer / subexponer / blanquear)

def apply_light(img: np.ndarray, lay: dict) -> np.ndarray:
    h, w = img.shape[:2]
    side = max(h, w)
    out = img
    for st in lay.get("strokes", []):
        r = max(1.0, float(st["r"]) * side)
        m = stroke_mask((h, w), _px(st["pts"], w, h), r)
        bb = _bbox(m, int(r) + 2)
        if bb is None:
            continue
        x0, y0, x1, y1 = bb
        a = _soft(m[y0:y1, x0:x1], r * 0.45)[..., None] * float(st.get("s", 0.5))
        reg = out[y0:y1, x0:x1]
        lab = cv2.cvtColor(np.ascontiguousarray(reg, dtype=np.float32), cv2.COLOR_RGB2Lab)
        mode = st.get("mode", "dodge")
        if mode == "whiten":  # dientes y blanco de los ojos: menos amarillo/rojo y algo más de luz
            lab[..., 1] *= 0.55
            lab[..., 2] = np.where(lab[..., 2] > 0, lab[..., 2] * 0.3, lab[..., 2])
            lab[..., 0] = np.minimum(100, lab[..., 0] * 1.08 + 2)
        elif mode == "burn":
            lab[..., 0] *= 0.82
        else:  # ojeras: más luz en las sombras que en las luces
            L = lab[..., 0]
            lab[..., 0] = np.minimum(100, L + (100 - L) * 0.22)
        new = np.clip(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB), 0, 1)
        out = out.copy() if out is img else out
        out[y0:y1, x0:x1] = reg * (1 - a) + new * a
    return out


# ------------------------------------------------------------------ licuar

FIELD_PX = 768  # resolución del campo de desplazamiento (se escala a la de la foto)


def liquify_field(shape, strokes: list) -> np.ndarray | None:
    """Campo de desplazamiento inverso (H', W', 2), en fracciones de ancho y alto, a FIELD_PX de lado largo."""
    if not strokes:
        return None
    h, w = shape[:2]
    f = FIELD_PX / max(h, w)
    fh, fw = max(8, int(round(h * f))), max(8, int(round(w * f)))
    gy, gx = np.mgrid[0:fh, 0:fw].astype(np.float32)
    mx, my = gx.copy(), gy.copy()
    side = max(fh, fw)
    for st in strokes:
        R = max(2.0, float(st["r"]) * side)
        s = float(st.get("s", 0.5))
        mode = st.get("mode", "push")
        pts = _px(st["pts"], fw, fh)
        # pasos regulares (un tercio del radio) para que el resultado no dependa de la velocidad del ratón
        dense = [pts[0]]
        for p in pts[1:]:
            last = dense[-1]
            n = max(1, int(np.hypot(*(p - last)) // max(1.0, R / 3)))
            dense += [last + (p - last) * t / n for t in range(1, n + 1)]
        prev = dense[0]
        for c in dense[(1 if mode == "push" else 0):]:
            x0, y0 = int(max(0, c[0] - R - 2)), int(max(0, c[1] - R - 2))
            x1, y1 = int(min(fw, c[0] + R + 3)), int(min(fh, c[1] + R + 3))
            if x1 <= x0 or y1 <= y0:
                prev = c
                continue
            qx, qy = gx[y0:y1, x0:x1], gy[y0:y1, x0:x1]
            d2 = ((qx - c[0]) ** 2 + (qy - c[1]) ** 2) / (R * R)
            wgt = np.clip(1 - d2, 0, 1) ** 2
            if mode == "push":
                sx, sy = qx - wgt * (c[0] - prev[0]) * s, qy - wgt * (c[1] - prev[1]) * s
            else:
                k = (0.12 if mode == "bloat" else -0.12) * s
                sx, sy = qx - wgt * (qx - c[0]) * k, qy - wgt * (qy - c[1]) * k
            mx[y0:y1, x0:x1] = cv2.remap(mx, sx, sy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            my[y0:y1, x0:x1] = cv2.remap(my, sx, sy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            prev = c
    return np.stack([(mx - gx) / fw, (my - gy) / fh], -1)


def apply_liquify(img: np.ndarray, lay: dict) -> np.ndarray:
    fld = liquify_field(img.shape, lay.get("strokes", []))
    if fld is None:
        return img
    h, w = img.shape[:2]
    up = cv2.resize(fld, (w, h), interpolation=cv2.INTER_LINEAR)
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
    return cv2.remap(img, gx + up[..., 0] * w, gy + up[..., 1] * h, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


# ------------------------------------------------------------------ texto y logo

_FONTS: dict | None = None


def list_fonts() -> dict:
    """{nombre visible: ruta} de las fuentes instaladas en Windows (y las del usuario)."""
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    from PIL import ImageFont
    out = {}
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith((".ttf", ".otf")):
                continue
            p = os.path.join(d, f)
            try:
                fam, sty = ImageFont.truetype(p, 12).getname()
            except Exception:
                continue
            name = fam if sty in ("Regular", "Normal", "Book", "Roman") else f"{fam} {sty}"
            out.setdefault(name, p)
    _FONTS = dict(sorted(out.items(), key=lambda kv: kv[0].lower()))
    return _FONTS


def _font(name: str, px: int):
    from PIL import ImageFont
    p = list_fonts().get(name) or list_fonts().get("Arial")
    try:
        return ImageFont.truetype(p, px) if p else ImageFont.load_default(px)
    except Exception:
        return ImageFont.load_default(px)


def _place(W, H, bw, bh, lay):
    ax, ay = ANCHORS.get(lay.get("anchor", "rb"), (1, 1))
    return int(round(lay.get("x", 0.5) * W - ax * bw)), int(round(lay.get("y", 0.5) * H - ay * bh))


def text_rgba(shape, lay: dict) -> tuple[np.ndarray, tuple] | None:
    """Capa de texto RGBA float (H, W, 4) y su caja (x0, y0, x1, y1) en píxeles."""
    from PIL import Image, ImageDraw, ImageFilter
    text = str(lay.get("text", "")).strip("\n")
    if not text.strip():
        return None
    H, W = shape[:2]
    px = max(6, int(round(float(lay.get("size", 3.0)) / 100.0 * H)))
    font = _font(lay.get("font", "Arial"), px)
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    l, t, r, b = probe.multiline_textbbox((0, 0), text, font=font, spacing=int(px * 0.2))
    bw, bh = r - l, b - t
    x, y = _place(W, H, bw, bh, lay)
    col = tuple(int(v * 255) for v in _hex(lay.get("color", "#ffffff")))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if lay.get("shadow", True):
        sh = Image.new("L", (W, H), 0)
        ImageDraw.Draw(sh).multiline_text((x - l + px * 0.04, y - t + px * 0.05), text, font=font, fill=150,
                                          spacing=int(px * 0.2))
        sh = sh.filter(ImageFilter.GaussianBlur(max(1, px * 0.08)))
        layer = Image.merge("RGBA", (Image.new("L", (W, H), 0),) * 3 + (sh,))
    txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(txt).multiline_text((x - l, y - t), text, font=font, fill=col + (255,), spacing=int(px * 0.2))
    layer = Image.alpha_composite(layer, txt)
    return np.asarray(layer, np.float32) / 255.0, (x, y, x + bw, y + bh)


_CUT_CACHE: dict = {}


def _cutout(path: str, cut: str, tol: float):
    """Imagen RGBA (PIL) del archivo, con el fondo quitado según `cut`. Con caché: GrabCut o la IA no se
    repiten en cada repintado."""
    from PIL import Image
    try:
        st = os.stat(path)
    except OSError:
        return None
    ck = (path, st.st_mtime, cut, round(float(tol), 1))
    if ck in _CUT_CACHE:
        return _CUT_CACHE[ck]
    try:
        im = Image.open(path)
        im.draft("RGB", (3000, 3000))
        im = im.convert("RGBA")
    except Exception:
        return None
    if max(im.size) > 3000:
        im.thumbnail((3000, 3000), Image.LANCZOS)
    if cut and cut != "none":
        arr = np.asarray(im, np.float32) / 255.0
        rgb = np.ascontiguousarray(arr[..., :3])
        if cut == "color":
            a, bg = key_alpha(rgb, None, tol, 8)
            rgb = decontaminate(rgb, a, bg)
        elif cut == "object":
            a = object_alpha(rgb)
        else:
            pr = person_probs(rgb, "capa:" + path)
            a = _refine(np.clip(1 - pr[..., 0], 0, 1).astype(np.float32), rgb) if pr is not None else \
                np.ones(rgb.shape[:2], np.float32)
        im = Image.fromarray((np.dstack([rgb, arr[..., 3] * a]) * 255 + 0.5).astype(np.uint8), "RGBA")
    _CUT_CACHE[ck] = im
    while len(_CUT_CACHE) > 12:
        _CUT_CACHE.pop(next(iter(_CUT_CACHE)))
    return im


def image_rgba(shape, lay: dict) -> tuple[np.ndarray, tuple] | None:
    """Capa de logo o de imagen (arrastrada desde el Explorador): recorte de fondo, tamaño, giro y volteo."""
    from PIL import Image
    p = lay.get("path") or ""
    if not os.path.isfile(p):
        return None
    cut = lay.get("cut") or ("color" if lay.get("knockout") else "none")
    im = _cutout(p, cut, lay.get("tol", 12))
    if im is None:
        return None
    H, W = shape[:2]
    tw = max(4, int(round(float(lay.get("width", 16)) / 100.0 * W)))
    th = max(1, int(round(im.height * tw / im.width)))
    im = im.resize((tw, th), Image.LANCZOS)
    if lay.get("flip"):
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if lay.get("angle"):
        im = im.rotate(float(lay["angle"]), Image.BICUBIC, expand=True)
        tw, th = im.size
    x, y = _place(W, H, tw, th, lay)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.paste(im, (x, y), im)
    return np.asarray(layer, np.float32) / 255.0, (x, y, x + tw, y + th)


logo_rgba = image_rgba


def overlay_rgba(shape, lay: dict):
    return text_rgba(shape, lay) if lay["kind"] == "text" else image_rgba(shape, lay)


def import_image(folder: str, src: str) -> str:
    """Copia una imagen arrastrada a `<carpeta>/.phootflow/edits/capas/` (la edición no depende del original)."""
    import filecmp
    import shutil
    d = os.path.join(edits_dir(folder), "capas")
    os.makedirs(d, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(src))
    dst = os.path.join(d, stem + ext.lower())
    k = 1
    while os.path.exists(dst):
        if filecmp.cmp(dst, src, shallow=False):
            return dst  # ya estaba copiada
        dst = os.path.join(d, f"{stem}_{k}{ext.lower()}")
        k += 1
    shutil.copy2(src, dst)
    return dst


# ------------------------------------------------------------------ composición

def _layer_active(l: dict) -> bool:
    if not l.get("visible", True) or l.get("opacity", 100) <= 0:
        return False
    k = l["kind"]
    return {"liquify": bool(l.get("strokes")), "retouch": bool(l.get("ops")),
            "skin": l.get("amount", 0) > 0 and (l.get("auto", True) or bool(l.get("strokes"))),
            "light": bool(l.get("strokes")), "background": True,
            "text": bool(str(l.get("text", "")).strip()), "logo": bool(l.get("path")),
            "image": bool(l.get("path"))}.get(k, False)


def apply_pixel_layer(img: np.ndarray, lay: dict, key: str = ""):
    """Aplica una capa de imagen. Devuelve (imagen, alfa nuevo | None)."""
    k = lay["kind"]
    alpha = None
    if k == "liquify":
        out = apply_liquify(img, lay)
    elif k == "retouch":
        out = apply_retouch(img, lay)
    elif k == "skin":
        out = apply_skin(img, lay, key)
    elif k == "light":
        out = apply_light(img, lay)
    elif k == "background":
        out, alpha = apply_background(img, lay, key)
    else:
        return img, None
    o = float(lay.get("opacity", 100)) / 100.0
    if o < 1:
        out = img * (1 - o) + out * o
        if alpha is not None:
            alpha = 1 - o * (1 - alpha)
    return out.astype(np.float32), alpha


def _sig(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def crop_box(s: dict) -> list:
    return list(s.get("crop") or [0.0, 0.0, 1.0, 1.0])


def apply_crop(img: np.ndarray, box) -> np.ndarray:
    if list(box) == [0.0, 0.0, 1.0, 1.0]:
        return img
    x0, y0, x1, y1 = box
    hh, ww = img.shape[:2]
    return img[int(y0 * hh):max(int(y0 * hh) + 1, int(y1 * hh)), int(x0 * ww):max(int(x0 * ww) + 1, int(x1 * ww))]


class Compositor:
    """Composición con caché por prefijo: al pintar en la capa N solo se recalcula de la N hacia arriba."""

    def __init__(self):
        self.key = ""
        self.base_sig = ""
        self.stack: list = []  # [(firma acumulada, imagen, alfa)]

    def pixel_stage(self, base: np.ndarray, layers: list, key: str = "", upto: int | None = None):
        """Imagen sin recortar tras las capas de imagen visibles (hasta `upto`, excluida). (img, alfa)."""
        bsig = _sig(key, base.shape, float(base[::97, ::97].sum()))
        if bsig != self.base_sig:
            self.base_sig, self.stack = bsig, []
        img, alpha = base, None
        sig = bsig
        pix = [l for l in layers if l["kind"] not in OVERLAY]
        if upto is not None:
            pix = pix[:upto]
        for i, lay in enumerate(pix):
            if not _layer_active(lay):
                continue
            sig = _sig(sig, lay)
            hit = next((c for c in self.stack if c[0] == sig), None)
            if hit is not None:
                img, alpha = hit[1], hit[2]
                continue
            img, a = apply_pixel_layer(img, lay, key)
            if a is not None:
                alpha = a if alpha is None else alpha * a
            self.stack.append((sig, img, alpha))
            if len(self.stack) > 12:
                self.stack.pop(0)
        return img, alpha

    def render(self, base: np.ndarray, doc: dict, box, key: str = ""):
        img, alpha = self.pixel_stage(base, doc.get("layers", []), key)
        return finish(img, alpha, doc, box)


def finish(img, alpha, doc: dict, box):
    """Recorte y capas superpuestas (texto y logo)."""
    img = apply_crop(img, box)
    if alpha is not None:
        alpha = apply_crop(alpha, box)
    for lay in doc.get("layers", []):
        if lay["kind"] not in OVERLAY or not _layer_active(lay):
            continue
        r = overlay_rgba(img.shape, lay)
        if r is None:
            continue
        rgba = r[0]
        a = rgba[..., 3:4] * float(lay.get("opacity", 100)) / 100.0
        img = img * (1 - a) + rgba[..., :3] * a
        if alpha is not None:
            alpha = np.maximum(alpha, a[..., 0])
    return np.clip(img, 0, 1).astype(np.float32), alpha


def compose(base: np.ndarray, doc: dict, box, key: str = ""):
    """Composición completa sin caché: (sRGB 0..1 recortada, alfa | None)."""
    return Compositor().render(base, doc, box, key)


def layer_input(comp: Compositor, base: np.ndarray, doc: dict, layer: dict, key: str = "") -> np.ndarray:
    """Imagen que entra en `layer` (todas las capas de imagen de debajo aplicadas)."""
    pix = [l for l in doc["layers"] if l["kind"] not in OVERLAY]
    i = next((j for j, l in enumerate(pix) if l["id"] == layer["id"]), len(pix))
    return comp.pixel_stage(base, doc["layers"], key, upto=i)[0]


# ------------------------------------------------------------------ exportar

def export(folder: str, photo_path: str, doc: dict, out_path: str, fmt: str = "jpg", quality: int = 95,
           long_side: int | None = None) -> str:
    s = base_settings(folder, photo_path, doc)
    key = photo_path + "#full"
    base = render_base(photo_path, s, full=True, key=key)
    img, alpha = compose(base, doc, crop_box(s), key)
    if fmt == "jpg" and alpha is not None:
        fmt = "png"  # un JPG no guarda transparencia: se exporta en PNG
        out_path = os.path.splitext(out_path)[0] + ".png"
    if long_side and max(img.shape[:2]) > long_side:
        f = long_side / max(img.shape[:2])
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        if alpha is not None:
            alpha = cv2.resize(alpha, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    if fmt == "jpg":
        ph = E.Photo(path=photo_path, name=os.path.splitext(os.path.basename(photo_path))[0])
        try:
            E.read_meta(ph)
            exif = E.build_exif(ph, "edits")
        except Exception:
            exif = b""
        E._save(np.clip(img, 0, 1), out_path, quality, exif)
        return out_path
    bgr = np.ascontiguousarray(img[..., ::-1])
    if alpha is not None:  # PNG y TIFF guardan la transparencia
        bgr = np.dstack([bgr, alpha])
    depth = 65535 if fmt == "tif" else 255
    data = (np.clip(bgr, 0, 1) * depth + 0.5).astype(np.uint16 if depth > 255 else np.uint8)
    tmp = out_path + ".part" + os.path.splitext(out_path)[1]
    ok, buf = cv2.imencode(os.path.splitext(out_path)[1], data)
    if not ok:
        raise RuntimeError("No se pudo codificar la imagen")
    with open(tmp, "wb") as f:
        f.write(buf.tobytes())
    os.replace(tmp, out_path)
    return out_path


def pending_events(folder: str) -> dict:
    """{ruta (normcase): documento} de las fotos retocadas en Edits para usarlas en la exportación de Events."""
    out = {}
    d = edits_dir(folder)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".json"):
                doc = load(folder, f[:-5])
                if doc and doc.get("_events_override") and has_edits(doc):
                    out[os.path.normcase(os.path.join(folder, f[:-5]))] = doc
    return out


def export_for_events(photos, folder: str, out_dir: str, sizes, quality: int, progress=None) -> int:
    """Exporta en «Sugeridas» las fotos aprobadas que tienen retoque de Edits (además de la edición normal)."""
    docs = pending_events(folder)
    todo = [p for p in photos if p.keep and os.path.normcase(p.path) in docs]
    n = 0
    for j, p in enumerate(todo):
        if progress:
            progress(j, len(todo), f"Exportando las retocadas en Edits ({j + 1}/{len(todo)})…")
        for size in sizes:
            side = None if size == "max" else int(size)
            try:
                export(folder, p.path, docs[os.path.normcase(p.path)],
                       os.path.join(out_dir, "Sugeridas", f"{p.name}_edits_{size}.jpg"), "jpg", quality, side)
                n += 1
            except Exception as e:
                p.error = f"{type(e).__name__}: {e}"
    return n


def snapshot(doc: dict) -> dict:
    return copy.deepcopy(doc)
