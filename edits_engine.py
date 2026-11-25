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
  "portrait"   retoque de retrato automático en todas las caras (ver PORTRAIT_DEFAULTS): dientes, ojos, color
               del iris, labios, colorete, igualar el tono de la piel y volumen (dodge and burn guiado)
  "background" mode: blur|bw|color|transparent, blur, color
  "image"      path, width (% del ancho del encuadre), x, y, anchor, angle, flip, cut (quitar fondo) y, con la
               transformación libre, quad: 4 esquinas [[x, y], ...] (sup-izq, sup-der, inf-der, inf-izq) que
               mandan sobre posición, tamaño y giro. Va en su sitio de la pila: los ajustes de encima la afectan.
  "paint"      strokes: [{"c": "#rrggbb", "r", "h" (dureza 0..1), "o" (opacidad 0..1), "erase": bool,
               "pts": [[x, y, presión], ...]}]: pincel y borrador (la presión de la tableta cambia el grosor).
  "rays"       rayos de luz: x, y (0..1 del encuadre, el foco), color, amount, length, spread, angle, count,
               seed; se suman a la imagen en modo Trama.
  "particles"  partículas (fx_engine): nieve, lluvia, confeti, pétalos, bokeh, destellos, polvo dorado o corazones;
               p = {kind, density, size, depth, angle, motion, opacity, colors, seed} (como Real Snow).
  "artistic"   filtro artístico (fx_engine): art = acuarela, óleo, cómic, cómic al óleo, tinta, lápiz, lápiz de
               color o pop art; amount, detail. Como cualquier capa, admite máscara y opacidad.
  "speed"      velocidad (bokeh.speed): sujeto nítido y el resto barrido con estela; amount, auto, angle, trail,
               detect = auto | person | object.
  "group"      grupo de capas: las capas con "parent": id del grupo se componen juntas y el grupo les aplica su
               visibilidad, opacidad, modo de fusión y máscara. En la lista, el grupo va encima de sus capas.
  Una capa de ajuste, imagen o pintura con "clip": True se recorta a la imagen o pintura de debajo (máscara de
  recorte): un ajuste así solo cambia esa capa, y una imagen o pintura así solo se ve dentro de ella.
  Las imágenes y pinturas aceptan efectos de capa en "fx": {"shadow": {...}, "glow": {...}, "blur": {...},
  "motion": {...}} (cada uno con "on"); ver FX_DEFAULTS.
- Capas superpuestas (siempre encima y sobre la foto ya recortada): "text" y "logo".

Las posiciones de las capas de imagen (x, y, quad) son 0..1 del encuadre (la foto recortada), como el texto;
los trazos, máscaras y selecciones, 0..1 de la foto sin recortar.

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
KINDS = {"liquify": "Licuar", "retouch": "Retoque", "skin": "Piel", "light": "Ojos y luz", "portrait": "Retrato",
         "background": "Fondo", "text": "Texto", "logo": "Logo", "image": "Imagen", "adjust": "Ajuste",
         "paint": "Pintura", "group": "Grupo", "rays": "Rayos de luz", "particles": "Partículas",
         "artistic": "Filtro artístico", "speed": "Velocidad", "enhance": "Mejorar con IA"}
OVERLAY = ("text", "logo")
CLIPPABLE = ("adjust", "image", "paint")  # lo que puede ir recortado a una imagen o pintura
CLIP_BASES = ("image", "paint")  # capas con píxeles propios: base de una máscara de recorte
IMAGE_EXT = tuple(sorted(E.IMG_EXT))  # JPEG, PNG, TIFF, WebP, BMP, AVIF y HEIF (si está pillow-heif)
CUTS = {"none": "No quitar el fondo", "color": "Logo o fondo liso (por color)",
        "object": "Objeto o producto (automático)", "person": "Persona (IA)"}
PIXEL_ORDER = ("liquify", "retouch", "enhance", "skin", "light", "portrait", "background", "adjust", "artistic", "image", "paint",
               "rays", "particles", "speed", "group")
# efectos de capa (imagen y pintura). Tamaños y distancias en % del lado largo; ángulos en grados
FX_DEFAULTS = {"shadow": {"on": False, "color": "#000000", "opacity": 60, "angle": 120, "distance": 1.0,
                          "size": 1.5},
               "glow": {"on": False, "color": "#ffd890", "opacity": 70, "size": 3.0},
               "blur": {"on": False, "amount": 0.4},
               "motion": {"on": False, "amount": 2.0, "angle": 0}}  # orden por defecto al crear capas
# retoque de retrato automático (0..100 salvo `lips`, -100..100); los colores, "" = sin cambio
PORTRAIT_DEFAULTS = {"teeth": 40, "eyes": 35, "whites": 30, "iris_color": "", "iris_amount": 60, "lips": 10,
                     "lip_color": "", "lip_amount": 50, "blush": 0, "even": 25, "sculpt": 30, "undereye": 30}
# capas de retrato guardadas antes de que existiera un regulador: valen 0 (no cambian al abrirlas)
PORTRAIT_OLD = {"undereye": 0}
PORTRAIT_LABELS = {"teeth": "Blanquear dientes", "eyes": "Iris: luz y nitidez", "whites": "Blanco de los ojos",
                   "iris_amount": "Color del iris", "lips": "Intensidad de labios", "lip_amount": "Color de labios",
                   "blush": "Colorete", "even": "Igualar tono de piel", "sculpt": "Volumen (dodge and burn)",
                   "undereye": "Ojeras"}
ANCHORS = {"lt": (0, 0), "mt": (0.5, 0), "rt": (1, 0), "lm": (0, 0.5), "mm": (0.5, 0.5), "rm": (1, 0.5),
           "lb": (0, 1), "mb": (0.5, 1), "rb": (1, 1)}
FORMATS = {"jpg": "JPG", "png": "PNG (con transparencia)", "webp": "WebP (web y redes, transparencia)",
           "avif": "AVIF (web moderna, aún menos peso)", "tif": "TIFF 16 bits", "tifc": "TIFF CMYK para imprenta",
           "pdf": "PDF para imprenta (300 ppp)", "gif": "GIF"}
# clave → (extensión, sufijo del nombre)
FORMAT_EXT = {"tifc": ("tif", "-CMYK")}


def format_ext(fmt: str) -> tuple[str, str]:
    return FORMAT_EXT.get(fmt, (fmt, ""))


# ------------------------------------------------------------------ documento

def new_doc(base: dict | None = None) -> dict:
    return {"version": DOC_VERSION, "base": base, "layers": [], "_events_override": False}


def new_layer(kind: str, **kw) -> dict:
    p = {"liquify": dict(strokes=[]),
         "retouch": dict(ops=[]),
         "skin": dict(amount=55, texture=45, auto=True, strokes=[]),
         "light": dict(strokes=[]),
         "portrait": dict(PORTRAIT_DEFAULTS),
         "background": dict(mode="blur", blur=40, color="#ffffff", strokes=[], detect="person", key_color=None,
                            tol=12, soft=8),
         "text": dict(text="© PhootFlow", font="Arial", size=3.0, color="#ffffff", x=0.97, y=0.965, anchor="rb",
                      shadow=True),
         "logo": dict(path="", width=16.0, x=0.97, y=0.965, anchor="rb", knockout=False, tol=12),
         "image": dict(path="", width=40.0, x=0.5, y=0.5, anchor="mm", cut="none", tol=12, angle=0.0,
                       flip=False),
         "adjust": dict(adj="curves", p={}),
         "paint": dict(strokes=[]),
         "group": dict(collapsed=False),
         "rays": dict(x=0.5, y=0.15, color="#fff1cf", amount=60, length=70, spread=70, angle=90, count=24,
                      seed=7),
         "particles": dict(p=_fx().particle_defaults("snow")),
         "artistic": dict(art="watercolor", amount=100.0, detail=50.0),
         "speed": dict(amount=50.0, auto=True, angle=0.0, trail=50.0, detect="auto"),
         "enhance": dict(detail=True, faces=True, light=False, amount=60.0)}[kind]
    p.update(kw)
    lay = {"id": uuid.uuid4().hex[:8], "kind": kind, "name": KINDS[kind], "visible": True, "opacity": 100,
           "blend": "normal", **p}
    if kind == "adjust":
        import edits_adjust as A
        lay["p"] = dict(A.defaults(lay["adj"]), **(lay.get("p") or {}))
        lay["name"] = A.ADJUSTMENTS[lay["adj"]][0]
    return lay


def normalize(doc: dict) -> dict:
    """Texto y logo siempre al final de la lista (encima); el resto conserva su orden. Los documentos de antes
    tenían las imágenes entre las superpuestas."""
    L = doc.get("layers", [])
    groups = {l["id"] for l in L if l.get("kind") == "group"}
    for l in L:
        if l.get("parent") and (l["parent"] not in groups or l.get("kind") in OVERLAY + ("group",)):
            l.pop("parent")  # grupo borrado (o capa que no puede ir en un grupo)
    kids: dict = {}
    for l in L:
        if l.get("parent"):
            kids.setdefault(l["parent"], []).append(l)
    out = []
    for l in L:  # las capas de cada grupo, seguidas y justo debajo de él
        if l.get("parent"):
            continue
        if l.get("kind") == "group":
            out.extend(kids.get(l["id"], []))
        out.append(l)
    doc["layers"] = [l for l in out if l.get("kind") not in OVERLAY] + [l for l in out if l.get("kind") in OVERLAY]
    return doc


def add_layer(doc: dict, kind: str, above: str | None = None, **kw) -> dict:
    """Añade la capa en su sitio natural: las de imagen por debajo de las superpuestas. Con `above` (id de una
    capa que no sea texto ni logo), justo encima de ella, como en Photoshop."""
    lay = new_layer(kind, **kw)
    layers = doc["layers"]
    ref = next((i for i, l in enumerate(layers) if l["id"] == above), None) if above else None
    if kind not in OVERLAY and ref is not None and layers[ref]["kind"] not in OVERLAY:
        layers.insert(ref + 1, lay)
        if layers[ref].get("parent") and kind != "group":
            lay["parent"] = layers[ref]["parent"]  # encima de una capa de un grupo: dentro del grupo
    elif kind in OVERLAY:
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
    return normalize(d)


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
    # con reducción de ruido como el automático de Studio (a tamaño completo el ruido se nota mucho más)
    return SE.new_settings(exposure=round(float(ev), 2), highlights=-25 if clip > 0.01 else -10, shadows=15,
                           nr_luma=25, nr_color=35)


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


def selection_mask(shape, spec: dict | None, img: np.ndarray | None = None, key: str = "") -> np.ndarray:
    """Alfa 0..1 de una selección (o de la máscara de una capa), en coordenadas 0..1 de la foto sin recortar.

    spec = {"shapes": [{"op": add|sub|inter, "type": rect|ellipse|lasso|subject|wand|part|all, "box": [x0, y0, x1, y1],
            "pts": [[x, y], ...], (varita) "pt": [x, y], "tol", "contiguous"}],"feather": radio del calado (fracción del lado largo), "invert": bool,
            "strokes": [[x, y, r, ±1], ...] (pincel de máscara)}
    """
    h, w = shape[:2]
    a = np.zeros((h, w), np.float32)
    if not spec:
        return np.ones((h, w), np.float32)
    for sh in spec.get("shapes", []):
        m = np.zeros((h, w), np.uint8)
        t = sh.get("type")
        if t == "all":
            m[:] = 1
        elif t in ("rect", "ellipse"):
            x0, y0, x1, y1 = sh["box"]
            p0 = (int(round(min(x0, x1) * (w - 1))), int(round(min(y0, y1) * (h - 1))))
            p1 = (int(round(max(x0, x1) * (w - 1))), int(round(max(y0, y1) * (h - 1))))
            if t == "rect":
                cv2.rectangle(m, p0, p1, 1, -1)
            else:
                c = ((p0[0] + p1[0]) // 2, (p0[1] + p1[1]) // 2)
                cv2.ellipse(m, c, (max(1, (p1[0] - p0[0]) // 2), max(1, (p1[1] - p0[1]) // 2)), 0, 0, 360, 1, -1,
                            cv2.LINE_AA)
        elif t == "lasso" and len(sh.get("pts", [])) >= 3:
            cv2.fillPoly(m, [np.round(_px(sh["pts"], w, h)).astype(np.int32)], 1, cv2.LINE_AA)
        elif t == "wand" and img is not None:
            m = None
            sa = wand_mask(img, sh)
        elif t == "part" and img is not None:
            m = None
            sa = part_mask(img, sh.get("part", "lips"), key)
        elif t == "subject" and img is not None:
            pr = person_probs(img, key)
            sub = (1 - pr[..., 0]) if pr is not None else object_alpha(img)
            m = None
            sa = np.clip(_refine(np.clip(sub, 0, 1).astype(np.float32), img), 0, 1)
        if m is not None:
            sa = m.astype(np.float32)
        op = sh.get("op", "add")
        a = np.maximum(a, sa) if op == "add" else (a * (1 - sa) if op == "sub" else np.minimum(a, sa))
    a = paint_mask(a, spec.get("strokes", [])) if spec.get("strokes") else a
    f = float(spec.get("feather", 0)) * max(h, w)
    if f > 0.5:
        a = cv2.GaussianBlur(a, (0, 0), f / 2)
    if spec.get("invert"):
        a = 1 - a
    return np.clip(a, 0, 1).astype(np.float32)


# partes que se seleccionan solas (IA): "part" de una forma de selección o de una máscara de capa
PARTS = {"lips": "Labios", "teeth": "Dientes", "eyes": "Ojos", "iris": "Iris", "brows": "Cejas",
         "beard": "Barba y bigote", "hair": "Pelo", "face": "Piel de la cara", "skin": "Piel (cara y cuerpo)",
         "clothes": "Ropa", "person": "Persona", "background": "Fondo"}


def part_mask(img: np.ndarray, part: str, key: str = "") -> np.ndarray:
    """Máscara automática de una parte de la cara o de la persona (vacía si no hay modelo o no hay caras)."""
    try:
        import masks as M
        return np.clip(M.part_alpha(np.ascontiguousarray(img, dtype=np.float32), part, key), 0, 1)
    except Exception:
        return np.zeros(img.shape[:2], np.float32)


WAND_PX = 1024  # la varita trabaja a este tamaño: la misma selección en la vista y al exportar


def wand_mask(img: np.ndarray, sh: dict) -> np.ndarray:
    """Varita mágica / bote de pintura: los píxeles de color parecido al del punto `pt` (0..1 de la foto sin
    recortar). `tol` 0..100; `contiguous`: solo la zona conectada (si no, todos los de ese color en la foto)."""
    h, w = img.shape[:2]
    f = min(1.0, WAND_PX / max(h, w))
    sm = cv2.resize(img, (max(2, int(w * f)), max(2, int(h * f))), interpolation=cv2.INTER_AREA) if f < 1 else img
    sm8 = (np.clip(sm, 0, 1) * 255 + 0.5).astype(np.uint8)
    sm8 = cv2.GaussianBlur(sm8, (3, 3), 0)  # sin ruido: la varita no se corta por el grano
    H, W = sm8.shape[:2]
    x, y = sh.get("pt", [0.5, 0.5])
    px, py = int(min(max(x, 0), 1) * (W - 1)), int(min(max(y, 0), 1) * (H - 1))
    tol = float(sh.get("tol", 32)) * 2.55 * 0.5
    if sh.get("contiguous", True):
        mm = np.zeros((H + 2, W + 2), np.uint8)
        cv2.floodFill(sm8.copy(), mm, (px, py), (0, 0, 0), (tol,) * 3, (tol,) * 3,
                      4 | cv2.FLOODFILL_FIXED_RANGE | cv2.FLOODFILL_MASK_ONLY | (255 << 8))
        m = mm[1:-1, 1:-1].astype(np.float32) / 255.0
    else:
        d = np.abs(sm8.astype(np.int16) - sm8[py, px].astype(np.int16)).max(-1)
        m = (d <= tol).astype(np.float32)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))  # tapa agujeritos sueltos
    if f < 1:
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(m, 0, 1).astype(np.float32)


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

def _op_mask(img: np.ndarray, op: dict, r: float) -> np.ndarray:
    """Zona de un retoque: el trazo de pincel o, en «Rellenar según contenido», la selección."""
    if op.get("sel"):
        return (selection_mask(img.shape, op["sel"], img) > 0.5).astype(np.float32)
    h, w = img.shape[:2]
    return stroke_mask(img.shape, _px(op["pts"], w, h), r)


def find_source(img: np.ndarray, op: dict) -> list[float] | None:
    """Busca el mejor origen para un trazo del corrector / relleno (como el «quitar» de Lightroom).

    Compara el anillo que rodea la zona a tapar con el mismo anillo desplazado en muchas direcciones y
    distancias, y se queda con el que mejor encaja (textura y tono), evitando que el origen pise la zona.
    Devuelve [dx, dy] como fracción de ancho y alto.
    """
    h, w = img.shape[:2]
    side = max(h, w)
    r = max(1.5, float(op["r"]) * side)
    hard = _op_mask(img, op, r)
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


# ------------------------------------------------------------------ borrado con IA (LaMa)
# LaMa (Suvorov et al., Samsung AI; Apache-2.0), exportado a ONNX por Carve (Apache-2.0). Entrada fija 512×512.
LAMA_URL = "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
LAMA_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"
LAMA_FILE = "lama_fp32.onnx"
LAMA_MB = 208
_LAMA = {"session": None, "lock": None, "dir": ""}


def lama_path() -> str:
    d = _LAMA["dir"]
    if not d:
        try:
            import masks as M
            d = M._CFG.get("model_dir") or ""
        except Exception:
            d = ""
    if not d:
        d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PhootFlow", "modelos")
    return os.path.join(d, LAMA_FILE)


def lama_ready() -> bool:
    p = lama_path()
    return os.path.exists(p) and os.path.getsize(p) > 200_000_000


def ensure_lama(progress=None) -> str:
    """Descarga el modelo de borrado la primera vez (208 MB), comprobando su SHA-256."""
    p = lama_path()
    if lama_ready():
        return p
    import urllib.request
    import net
    os.makedirs(os.path.dirname(p), exist_ok=True)
    resp = net.urlopen(urllib.request.Request(LAMA_URL, headers={"User-Agent": "PhootFlow"}), 60)
    total = int(resp.headers.get("Content-Length") or LAMA_MB * 1_000_000)
    h = hashlib.sha256()
    done = 0
    with open(p + ".part", "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != LAMA_SHA256:
        os.remove(p + ".part")
        raise RuntimeError("El modelo de borrado no supera la verificación SHA-256")
    os.replace(p + ".part", p)
    return p


def _lama_session():
    import threading
    if _LAMA["lock"] is None:
        _LAMA["lock"] = threading.Lock()
    with _LAMA["lock"]:
        if _LAMA["session"] is None:
            import onnxruntime as ort
            _LAMA["session"] = ort.InferenceSession(lama_path(), providers=["CPUExecutionProvider"])
    return _LAMA["session"]


def lama_inpaint(crop: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Rellena `hole` (0/1) en `crop` (sRGB 0..1) con LaMa a 512 px y lo devuelve al tamaño de `crop`."""
    h, w = hole.shape
    a = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_AREA).transpose(2, 0, 1)[None].astype(np.float32)
    m = (cv2.resize(hole.astype(np.float32), (512, 512), interpolation=cv2.INTER_LINEAR) > 0.3)
    m = cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(np.float32)[None, None]
    out = _lama_session().run(None, {"image": np.clip(a, 0, 1), "mask": m})[0][0].transpose(1, 2, 0)
    if out.max() > 2.0:  # el modelo devuelve 0..255
        out = out / 255.0
    return cv2.resize(np.clip(out, 0, 1).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)


def _apply_lama(img: np.ndarray, op: dict, hard: np.ndarray, r: float) -> np.ndarray:
    """Relleno según contenido con IA: LaMa pone la estructura y el tono (a 512 px) y, a resolución mayor,
    la textura fina sale del mejor parche de la propia foto, para que no quede blando."""
    h, w = img.shape[:2]
    bb = _bbox(hard, 0)
    if bb is None:
        return img
    x0, y0, x1, y1 = bb
    # contexto alrededor: un cuadrado ~2 veces la zona (LaMa necesita ver lo que rodea al agujero)
    side = int(min(max(w, h), max(256, max(x1 - x0, y1 - y0) * 2.0 + 4 * r)))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    X0 = int(min(max(0, cx - side / 2), max(0, w - side)))
    Y0 = int(min(max(0, cy - side / 2), max(0, h - side)))
    X1, Y1 = min(w, X0 + side), min(h, Y0 + side)
    crop, hole = img[Y0:Y1, X0:X1], hard[Y0:Y1, X0:X1]
    res = lama_inpaint(crop, hole)
    scale = max(X1 - X0, Y1 - Y0) / 512.0
    if scale > 1.3:
        # LaMa trabaja a 512 px: al ampliar queda liso. Se le devuelve el grano de la foto midiendo el ruido
        # del entorno del agujero (por canal) y poniendo el mismo dentro, con el mismo tamaño de grano.
        ring = (cv2.dilate(hole, np.ones((3, 3), np.uint8), iterations=max(2, int(r * 0.5))) > 0) & (hole < 0.5)
        hp = crop - cv2.GaussianBlur(crop, (0, 0), 1.2)
        if ring.sum() > 50:
            want = hp[ring].std(0)
            have = (res - cv2.GaussianBlur(res, (0, 0), 1.2))[hole > 0.5].std(0) if (hole > 0.5).any() else 0
            add = np.sqrt(np.maximum(want ** 2 - np.asarray(have) ** 2, 0)).astype(np.float32)
            if float(add.max()) > 1e-4:
                seed = int(abs(hash((round(float(op["r"]), 5), str(op["pts"][:2])))) % (2 ** 31))
                g = np.random.default_rng(seed).standard_normal(res.shape).astype(np.float32)
                g = g - cv2.GaussianBlur(g, (0, 0), 1.2)
                g /= g.std() + 1e-6
                res = np.clip(res + g * add, 0, 1)
    feather = max(1.0, r * 0.25)
    grow = cv2.dilate(hole, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=max(1, int(feather)))
    a = np.maximum(hole, _soft(grow, feather))[..., None]
    out = img.copy()
    out[Y0:Y1, X0:X1] = crop * (1 - a) + res * a
    return out


def _apply_op(img: np.ndarray, op: dict) -> np.ndarray:
    h, w = img.shape[:2]
    side = max(h, w)
    r = max(1.0, float(op["r"]) * side)
    tool = op.get("tool", "heal")
    hard = _op_mask(img, op, r)
    if tool == "fill" and op.get("ai", True) and lama_ready():
        try:
            return _apply_lama(img, op, hard, r)
        except Exception:
            pass  # sin IA (modelo dañado, poca memoria): relleno clásico
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
    freq = op.get("freq", "both")
    if tool == "clone":
        res = src
    elif tool == "heal" and freq in ("color", "texture"):
        # como la separación de frecuencias a mano: «color» trae el tono del origen y conserva los poros del
        # destino (rojeces, ojeras); «textura» trae los poros del origen y conserva el tono (arrugas, poros)
        fs = max(0.8, 0.0012 * max(h, w))
        lo_s, lo_d = _lowpass(src, fs), _lowpass(dst, fs)
        res = (dst - lo_d + lo_s) if freq == "color" else (src - lo_s + lo_d)
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
    a = skin_mask(img, lay, key)
    # solo en la caja de la piel, con margen para los filtros (en una foto de evento, mucho menos que la foto)
    bb = _bbox(a, int(45 * k) + 16)
    if bb is None:
        return img
    x0, y0, x1, y1 = bb
    reg = np.ascontiguousarray(img[y0:y1, x0:x1])
    smooth = np.stack([SE.guided(np.ascontiguousarray(reg[..., c]), 7.0 * k, 0.0025) for c in range(3)], -1)
    fine = reg - cv2.GaussianBlur(reg, (0, 0), max(0.6, 1.1 * k))
    target = smooth + fine * texture
    out = img.copy()
    out[y0:y1, x0:x1] = np.clip(reg + (target - reg) * (a[y0:y1, x0:x1, None] * amount), 0, 1)
    return out


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


# ------------------------------------------------------------------ retoque de retrato automático

# dodge and burn guiado: manchas suaves en coordenadas de la cara (s: anchos de cara desde el centro de los
# ojos; t: distancias ojos-boca hacia la barbilla), tamaño en anchos de cara y signo (+ aclarar, - oscurecer).
# Luz donde la cara la recibe (frente, tabique, pómulos, barbilla, bajo la ceja) y sombra donde se hunde
# (bajo el pómulo, lados de la nariz, sienes), como en el retoque a mano.
SCULPT = [(0.0, -0.95, 0.20, 1.0), (0.0, 0.30, 0.055, 0.9), (0.0, 0.62, 0.05, 0.6), (0.0, 1.62, 0.09, 0.8),
          (-0.26, 0.32, 0.09, 0.8), (0.26, 0.32, 0.09, 0.8), (-0.17, -0.38, 0.055, 0.6), (0.17, -0.38, 0.055, 0.6),
          (-0.33, 0.98, 0.10, -1.0), (0.33, 0.98, 0.10, -1.0), (-0.10, 0.45, 0.04, -0.7), (0.10, 0.45, 0.04, -0.7),
          (-0.45, -0.42, 0.09, -0.6), (0.45, -0.42, 0.09, -0.6)]


def _blobs(shape, pts, spots, x0=0, y0=0) -> np.ndarray:
    """Suma de manchas gaussianas con signo en los ejes de una cara (en un recorte que empieza en x0, y0)."""
    import masks as M
    h, w = shape[:2]
    ax = M.face_axes(pts)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    out = np.zeros((h, w), np.float32)
    for s_, t_, size, sign in spots:
        c = ax["E"] + ax["u"] * ax["fw"] * s_ + ax["v"] * ax["d"] * t_ - np.array([x0, y0], np.float32)
        sig = max(1.0, size * ax["fw"])
        out += sign * np.exp(-((xx - c[0]) ** 2 + (yy - c[1]) ** 2) / (2 * sig * sig))
    return out


def _lab(x):
    return cv2.cvtColor(np.ascontiguousarray(np.clip(x, 0, 1), dtype=np.float32), cv2.COLOR_RGB2Lab)


def _rgb(lab):
    return np.clip(cv2.cvtColor(np.ascontiguousarray(lab, dtype=np.float32), cv2.COLOR_Lab2RGB), 0, 1)


def _hex_lab(c: str) -> np.ndarray:
    return _lab(_hex(c).reshape(1, 1, 3))[0, 0]


# párpado inferior de cada ojo en la malla facial (de la comisura exterior a la interior)
LOWER_LIDS = ([33, 7, 163, 144, 145, 153, 154, 155, 133], [263, 249, 390, 373, 374, 380, 381, 382, 362])


def undereye_alpha(shape, pts, x0=0, y0=0) -> np.ndarray:
    """Zona de las ojeras de una cara: una media luna bajo cada párpado inferior, más honda en el centro
    (hasta ~1/3 de la distancia ojos-boca), con el borde muy suave."""
    import masks as M
    h, w = shape[:2]
    ax = M.face_axes(pts)
    a = np.zeros((h, w), np.float32)
    off = np.array([x0, y0], np.float32)
    for lid in LOWER_LIDS:
        top = pts[lid] - off
        n = len(lid)
        depth = 0.06 + 0.26 * np.sin(np.linspace(0, np.pi, n))  # en distancias ojos-boca
        bot = top + ax["v"][None, :] * (ax["d"] * depth)[:, None]
        poly = np.concatenate([top, bot[::-1]]) + ax["v"] * ax["d"] * 0.04  # un poco por debajo de las pestañas
        cv2.fillPoly(a, [np.round(poly).astype(np.int32)], 1.0, cv2.LINE_AA)
    return cv2.GaussianBlur(a, (0, 0), max(1.0, 0.06 * ax["d"])) if a.any() else a


def _undereye(lab: np.ndarray, shape, faces, x0, y0, eyes: np.ndarray, k: float) -> None:
    """Ojeras: la piel bajo el ojo se acerca a la luz y el color del pómulo de esa cara (solo se aclara lo que
    está más oscuro que él), como el dodge suave más «Solo color» del retoque a mano. Cambia `lab`."""
    L, A_, B_ = lab[..., 0], lab[..., 1], lab[..., 2]
    shield = np.clip(1 - cv2.dilate(eyes, np.ones((3, 3), np.uint8), iterations=2) * 1.5, 0, 1)
    for f in faces:
        u = undereye_alpha(shape, f, x0, y0) * shield
        if u.max() < 0.05:
            continue
        cheek = np.clip(_blobs(shape, f, [(-0.24, 0.62, 0.07, 1.0), (0.24, 0.62, 0.07, 1.0)], x0, y0), 0, 1)
        wsum = float(cheek.sum())
        if wsum < 1:
            continue
        ref = [float((ch * cheek).sum() / wsum) for ch in (L, A_, B_)]
        wgt = u * k
        L += np.maximum(ref[0] - L, 0) * wgt * 0.75
        A_ += (ref[1] - A_) * wgt * 0.6
        B_ += (ref[2] - B_) * wgt * 0.6


def apply_portrait(img: np.ndarray, lay: dict, key: str = "") -> np.ndarray:
    """Retoque de retrato en todas las caras, sin pincel: dientes, ojos, iris, labios, colorete, tono de la piel
    y volumen. Las zonas salen de la malla facial y de la segmentación de la persona (IA local)."""
    p = dict(PORTRAIT_DEFAULTS, **PORTRAIT_OLD)
    p.update({k: lay[k] for k in PORTRAIT_DEFAULTS if k in lay})
    try:
        import masks as M
        faces = M.faces_scaled(np.ascontiguousarray(img, dtype=np.float32), key)
    except Exception:
        return img
    faces = [f for f in faces if len(f) >= 468]
    if not faces:
        return img
    h, w = img.shape[:2]
    # todo se calcula en la caja de las caras (con margen para el cuello y la frente)
    xs = np.concatenate([f[:, 0] for f in faces])
    ys = np.concatenate([f[:, 1] for f in faces])
    fwm = max(float(np.ptp(f[:, 0])) for f in faces)
    x0, y0 = max(0, int(xs.min() - 0.5 * fwm)), max(0, int(ys.min() - 0.6 * fwm))
    x1, y1 = min(w, int(xs.max() + 0.5 * fwm)), min(h, int(ys.max() + 0.5 * fwm))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return img
    crop = img[y0:y1, x0:x1]
    cache: dict = {}

    def part(name):
        if name not in cache:
            cache[name] = part_mask(img, name, key)[y0:y1, x0:x1]
        return cache[name]

    lab = _lab(crop)
    L, A_, B_ = lab[..., 0], lab[..., 1], lab[..., 2]
    skin = None
    if p["even"] > 0 or p["sculpt"] > 0 or p["blush"] > 0:
        skin = part("face").copy()
        if skin.max() < 0.05:  # sin modelo de segmentación: el óvalo de la cara
            skin = np.zeros(crop.shape[:2], np.float32)
            for f in faces:
                cv2.fillPoly(skin, [np.round(f[M.FACE_OVAL] - [x0, y0]).astype(np.int32)], 1.0, cv2.LINE_AA)
            skin = cv2.GaussianBlur(skin, (0, 0), max(1.0, 0.02 * fwm))
        feat = np.maximum(np.maximum(part("eyes"), part("brows")), part("lips"))
        it = max(1, int(0.01 * fwm))
        feat = cv2.GaussianBlur(cv2.dilate(feat, np.ones((3, 3), np.uint8), iterations=it), (0, 0), float(it))
        skin = np.clip(skin * (1 - feat), 0, 1)
    if p["even"] > 0:  # rojeces y manchas de color: el color de la piel se acerca a su media de alrededor
        sig = max(2.0, 0.035 * fwm)
        wl = cv2.GaussianBlur(skin, (0, 0), sig) + 1e-4
        ww = cv2.GaussianBlur(skin, (0, 0), sig * 3) + 1e-4
        k = skin * (p["even"] / 100.0) * 0.8
        for ch in (A_, B_):
            loc = cv2.GaussianBlur(ch * skin, (0, 0), sig) / wl
            wide = cv2.GaussianBlur(ch * skin, (0, 0), sig * 3) / ww
            ch -= (loc - wide) * k
    if p["sculpt"] > 0:
        dmap = np.zeros(crop.shape[:2], np.float32)
        for f in faces:
            dmap += _blobs(crop.shape, f, SCULPT, x0, y0)
        mid = np.clip(1 - np.abs(L / 50.0 - 1) ** 2, 0, 1)
        L += np.clip(dmap, -1, 1) * skin * mid * (p["sculpt"] / 100.0) * 9.0
    if p["blush"] > 0:
        cheeks = np.zeros(crop.shape[:2], np.float32)
        for f in faces:
            cheeks += _blobs(crop.shape, f, [(-0.25, 0.72, 0.11, 1.0), (0.25, 0.72, 0.11, 1.0)], x0, y0)
        wgt = np.clip(cheeks, 0, 1) * skin * (p["blush"] / 100.0)
        A_ += wgt * 14.0
        B_ += wgt * 3.0
        L -= wgt * 2.0
    if p["undereye"] > 0:
        _undereye(lab, crop.shape, faces, x0, y0, part("eyes"), p["undereye"] / 100.0)
    if p["teeth"] > 0:
        t = part("teeth") * np.clip((L - 35) / 20, 0, 1) * (p["teeth"] / 100.0)
        A_ -= A_ * 0.45 * t
        B_ -= np.maximum(B_, 0) * 0.75 * t
        L += (np.minimum(100, L * 1.08 + 2) - L) * t
    if p["whites"] > 0 or p["eyes"] > 0 or p["iris_color"]:
        iris = part("iris")
        if p["whites"] > 0:
            ring = cv2.dilate(iris, np.ones((3, 3), np.uint8), iterations=max(1, int(0.006 * fwm)))
            wh = np.clip(part("eyes") - ring, 0, 1) * np.clip((L - 40) / 20, 0, 1) * (p["whites"] / 100.0)
            A_ -= A_ * 0.6 * wh  # venas y rojez fuera
            B_ -= np.maximum(B_, 0) * 0.5 * wh
            L += (100 - L) * 0.12 * wh
        if p["iris_color"]:
            tgt = _hex_lab(p["iris_color"])
            wi = iris * np.clip((L - 12) / 15, 0, 1) * (p["iris_amount"] / 100.0)  # la pupila no se tiñe
            A_ += (tgt[1] * 0.7 - A_) * wi
            B_ += (tgt[2] * 0.7 - B_) * wi
        if p["eyes"] > 0:
            e = iris * (p["eyes"] / 100.0)
            L += (100 - L) * 0.18 * e * np.clip((L - 8) / 20, 0, 1)
            sig = max(0.6, 0.004 * fwm)
            L += (L - cv2.GaussianBlur(L, (0, 0), sig)) * 1.2 * e
            A_ *= 1 + 0.25 * e
            B_ *= 1 + 0.25 * e
    if p["lips"] or p["lip_color"]:
        lips = part("lips")
        if p["lips"]:
            k = 1 + lips * (p["lips"] / 100.0) * 0.6
            A_ *= k
            B_ *= k
        if p["lip_color"]:
            tgt = _hex_lab(p["lip_color"])
            wl = lips * (p["lip_amount"] / 100.0)
            A_ += (tgt[1] - A_) * wl
            B_ += (tgt[2] - B_) * wl
            L += (tgt[0] - L) * wl * 0.25
    np.clip(L, 0, 100, out=L)
    out = img.copy()
    out[y0:y1, x0:x1] = _rgb(lab)
    return out


# ------------------------------------------------------------------ imperfecciones automáticas

BLEMISH_MAX = 30  # como mucho, por cara (las más marcadas)


def find_blemishes(img: np.ndarray, faces: list, skin: np.ndarray, max_n: int = BLEMISH_MAX) -> list:
    """Granos, puntos y rojeces pequeñas en la piel de cada cara: manchas redondeadas, más oscuras o más rojas
    que la piel de alrededor (diferencia de gaussianas en L y a* de Lab), entre ~0,2 % y 1,6 % del ancho de la
    cara. `skin` (0..1, tamaño de `img`) es dónde se buscan. Devuelve [(x, y, radio del pincel), ...] en píxeles."""
    import masks as M
    h, w = img.shape[:2]
    out = []
    for f in faces:
        fw = M.face_axes(f)["fw"]
        pad = 0.1 * fw
        x0, y0 = max(0, int(f[:, 0].min() - pad)), max(0, int(f[:, 1].min() - pad))
        x1, y1 = min(w, int(f[:, 0].max() + pad) + 1), min(h, int(f[:, 1].max() + pad) + 1)
        if x1 - x0 < 16 or y1 - y0 < 16:
            continue
        s = skin[y0:y1, x0:x1]
        m = s > 0.6
        if m.sum() < 200:
            continue
        lab = _lab(img[y0:y1, x0:x1])
        s_small, s_big = max(0.7, 0.004 * fw), max(3.0, 0.03 * fw)
        L, A_ = lab[..., 0], lab[..., 1]
        dark = cv2.GaussianBlur(L, (0, 0), s_big) - cv2.GaussianBlur(L, (0, 0), s_small)
        red = cv2.GaussianBlur(A_, (0, 0), s_small) - cv2.GaussianBlur(A_, (0, 0), s_big)

        def z(x):  # en desviaciones robustas de la propia piel (el ruido y los poros no cuentan)
            med = float(np.median(x[m]))
            return (x - med) / (float(np.median(np.abs(x[m] - med))) * 1.48 + 0.15)

        score = np.maximum(z(dark), 0.8 * z(red))
        cand = (score > 4.0) & m & ((dark > 2.0) | (red > 3.0))
        n, lbl, st, cen = cv2.connectedComponentsWithStats(cand.astype(np.uint8), connectivity=8)
        r_min, r_max = max(0.8, 0.002 * fw), 0.016 * fw
        found = []
        for i in range(1, n):
            bx, by, bw_, bh_ = st[i, :4]
            sub = lbl[by:by + bh_, bx:bx + bw_] == i
            sc = score[by:by + bh_, bx:bx + bw_]
            peak = float(sc[sub].max())
            core = sub & (sc >= 0.5 * peak)  # el tamaño se mide a media altura (una mancha muy marcada no crece)
            r = float(np.sqrt(core.sum() / np.pi))
            ys, xs = np.nonzero(core)
            cw, ch = int(np.ptp(xs)) + 1, int(np.ptp(ys)) + 1
            if not r_min <= r <= r_max or max(cw, ch) > 3 * min(cw, ch):
                continue  # ni ruido ni manchas grandes (lunares, sombras); nada alargado (arrugas, pelos)
            found.append((peak, float(cen[i][0]) + x0, float(cen[i][1]) + y0, max(2.0, r * 1.9)))
        found.sort(reverse=True)
        out += [(x, y, r) for _, x, y, r in found[:max_n]]
    return out


def blemish_skin(img: np.ndarray, faces: list, key: str = "") -> np.ndarray:
    """Dónde buscar imperfecciones: la piel de la cara (IA; sin modelo, el óvalo de la malla) sin ojos, cejas,
    labios ni fosas nasales, con margen."""
    import masks as M
    h, w = img.shape[:2]
    oval = np.zeros((h, w), np.float32)
    feat = np.zeros((h, w), np.uint8)
    for f in faces:
        fw = M.face_axes(f)["fw"]
        ov = np.zeros((h, w), np.uint8)
        cv2.fillPoly(ov, [np.round(f[M.FACE_OVAL]).astype(np.int32)], 1)
        ov = cv2.erode(ov, np.ones((3, 3), np.uint8), iterations=max(1, int(0.02 * fw)))
        oval = np.maximum(oval, ov.astype(np.float32))
        one = np.zeros((h, w), np.uint8)
        for part in ("eyes", "brows", "lips"):
            for poly in M.FACE_PARTS[part][1]:
                cv2.fillPoly(one, [np.round(f[poly]).astype(np.int32)], 1)
        for i in (98, 327, 2):  # fosas nasales y base de la nariz
            cv2.circle(one, tuple(int(v) for v in np.round(f[i])), max(2, int(0.06 * fw)), 1, -1)
        one = cv2.dilate(one, np.ones((3, 3), np.uint8), iterations=max(1, int(0.035 * fw)))
        feat = np.maximum(feat, one)
    seg = part_mask(img, "face", key)
    skin = oval * (seg if seg.max() > 0.3 else 1.0)
    return skin * (1 - feat.astype(np.float32))


def blemish_ops(img: np.ndarray, key: str = "", faces: list | None = None) -> list:
    """Operaciones del corrector (como si se hubieran pintado) para las imperfecciones de todas las caras, con
    su origen ya buscado. Vacío si no hay caras."""
    if faces is None:
        try:
            import masks as M
            faces = [f for f in M.faces_scaled(np.ascontiguousarray(img, dtype=np.float32), key) if len(f) >= 468]
        except Exception:
            return []
    if not faces:
        return []
    h, w = img.shape[:2]
    side = max(h, w)
    ops = []
    for x, y, r in find_blemishes(img, faces, blemish_skin(img, faces, key)):
        op = {"tool": "heal", "r": round(r / side, 5), "pts": [[round(x / (w - 1), 5), round(y / (h - 1), 5)]],
              "src": None, "hard": 0.35, "auto": True}
        op["src"] = find_source(img, op)
        ops.append(op)
    return ops


UNDEREYE_HINT = 6.0   # ojeras marcadas: la zona bajo el ojo, estos puntos de L (Lab) más oscura que el pómulo
TEETH_HINT = 16.0     # dientes amarillentos: b* (Lab) mediano de los dientes por encima de esto


def retouch_hints(img: np.ndarray, key: str = "", faces: list | None = None) -> dict:
    """Lo que pide retoque en las caras de la foto, medido como lo corregiría el retoque automático:
    {"faces", "blemishes", "undereye" (diferencia de L con el pómulo), "teeth" (b* de los dientes o 0),
    "texts": [...]}. Rápido (malla facial en caché): se calcula al abrir la foto."""
    import masks as M
    if faces is None:
        try:
            faces = [f for f in M.faces_scaled(np.ascontiguousarray(img, dtype=np.float32), key) if len(f) >= 468]
        except Exception:
            faces = []
    h = {"faces": len(faces), "blemishes": 0, "undereye": 0.0, "teeth": 0.0, "texts": []}
    if not faces:
        return h
    h["blemishes"] = len(find_blemishes(img, faces, blemish_skin(img, faces, key)))
    lab = _lab(img)
    L = lab[..., 0]
    shape = img.shape[:2]
    for f in faces:
        u = undereye_alpha(shape, f)
        cheek = np.clip(_blobs(shape, f, [(-0.24, 0.62, 0.07, 1.0), (0.24, 0.62, 0.07, 1.0)]), 0, 1)
        if u.sum() > 4 and cheek.sum() > 4:
            h["undereye"] = max(h["undereye"], float((L * cheek).sum() / cheek.sum() - (L * u).sum() / u.sum()))
    t = part_mask(img, "teeth", key) > 0.5
    t &= L > 45  # lo claro de la boca (los dientes, no el hueco)
    if t.sum() > 30:
        h["teeth"] = float(np.median(lab[..., 2][t]))
    if h["blemishes"]:
        h["texts"].append(f"{h['blemishes']} granos o rojeces")
    if h["undereye"] >= UNDEREYE_HINT:
        h["texts"].append("ojeras marcadas")
    if h["teeth"] >= TEETH_HINT:
        h["texts"].append("dientes amarillentos")
    return h


# «Retocar retratos» de Events: las mismas capas que pone el retoque en un clic (sin pelo ni enfoque)
AUTO_SKIN = {"amount": 45, "texture": 55}


def auto_portrait(img: np.ndarray, key: str = "") -> np.ndarray:
    """Piel por separación de frecuencias + retrato automático (volumen, ojeras, dientes, ojos, labios) sobre una
    imagen ya revelada (sRGB 0..1). Sin caras, la piel sola apenas cambia nada y el retrato no hace nada."""
    img = np.ascontiguousarray(img, dtype=np.float32)
    img = apply_skin(img, new_layer("skin", **AUTO_SKIN), key)
    return apply_portrait(img, new_layer("portrait"), key)


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


def image_patch(shape, lay: dict, frame=None):
    """Capa de logo o de imagen (arrastrada desde el Explorador) solo en su caja: recorte de fondo, tamaño, giro,
    volteo y transformación libre. `frame` = (x, y, ancho, alto) en píxeles del encuadre dentro de `shape` (por
    defecto, toda la imagen): posiciones y tamaños se miden sobre él.
    Devuelve (RGBA float del trozo visible, x0, y0 del trozo, caja completa (x0, y0, x1, y1)) o None."""
    from PIL import Image
    p = lay.get("path") or ""
    if not os.path.isfile(p):
        return None
    cut = lay.get("cut") or ("color" if lay.get("knockout") else "none")
    im = _cutout(p, cut, lay.get("tol", 12))
    if im is None:
        return None
    H, W = shape[:2]
    fx, fy, fw, fh = frame if frame is not None else (0, 0, W, H)
    if lay.get("quad"):
        return _warp_patch(im, (H, W), [(fx + qx * fw, fy + qy * fh) for qx, qy in lay["quad"]], lay.get("flip"))
    tw = max(4, int(round(float(lay.get("width", 16)) / 100.0 * fw)))
    th = max(1, int(round(im.height * tw / im.width)))
    im = im.resize((tw, th), Image.LANCZOS)
    if lay.get("flip"):
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if lay.get("angle"):
        im = im.rotate(float(lay["angle"]), Image.BICUBIC, expand=True)
        tw, th = im.size
    x, y = _place(fw, fh, tw, th, lay)
    x, y = x + int(fx), y + int(fy)
    arr = np.asarray(im, np.float32) / 255.0
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + tw), min(H, y + th)
    if x1 <= x0 or y1 <= y0:
        return None
    return np.ascontiguousarray(arr[y0 - y:y1 - y, x0 - x:x1 - x]), x0, y0, (x, y, x + tw, y + th)


def image_rgba(shape, lay: dict, frame=None) -> tuple[np.ndarray, tuple] | None:
    """La capa de imagen en un RGBA del tamaño de `shape` y su caja completa (x0, y0, x1, y1) en píxeles."""
    r = image_patch(shape, lay, frame)
    if r is None:
        return None
    patch, x0, y0, box = r
    out = np.zeros((shape[0], shape[1], 4), np.float32)
    out[y0:y0 + patch.shape[0], x0:x0 + patch.shape[1]] = patch
    return out, box


def _warp_patch(im, shape, quad, flip=False):
    """La imagen (PIL RGBA) deformada en perspectiva hasta las 4 esquinas `quad` (píxeles de `shape`), solo en
    la caja de las esquinas. Mismo formato que `image_patch`."""
    H, W = shape
    q = np.float32(quad)
    bx0, by0 = np.floor(q.min(0)).astype(int)
    bx1, by1 = np.ceil(q.max(0)).astype(int)
    x0, y0, x1, y1 = max(0, int(bx0)), max(0, int(by0)), min(W, int(bx1) + 1), min(H, int(by1) + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    # se reduce antes al tamaño de destino (sin dientes de sierra al encoger) y se deforma premultiplicada
    side = max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3]), 4.0)
    f = min(1.0, side / im.width)
    arr = np.asarray(im, np.float32) / 255.0
    if f < 1:
        arr = cv2.resize(arr, (max(2, int(im.width * f)), max(2, int(im.height * f))), interpolation=cv2.INTER_AREA)
    if flip:
        arr = arr[:, ::-1]
    h, w = arr.shape[:2]
    pre = np.ascontiguousarray(np.dstack([arr[..., :3] * arr[..., 3:4], arr[..., 3]]))
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), q - np.float32([x0, y0]))
    out = cv2.warpPerspective(pre, M, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                              borderValue=(0, 0, 0, 0))
    a = out[..., 3:4]
    out[..., :3] = np.where(a > 1e-4, out[..., :3] / np.maximum(a, 1e-4), 0)
    return np.clip(out, 0, 1).astype(np.float32), x0, y0, (int(bx0), int(by0), int(bx1), int(by1))


def _warp_rgba(im, shape, quad, flip=False):
    r = _warp_patch(im, shape, quad, flip)
    H, W = shape
    out = np.zeros((H, W, 4), np.float32)
    if r is None:
        return out, tuple(int(v) for v in (*np.float32(quad).min(0), *np.float32(quad).max(0)))
    patch, x0, y0, box = r
    out[y0:y0 + patch.shape[0], x0:x0 + patch.shape[1]] = patch
    return out, box


def image_quad(shape, lay: dict) -> list:
    """Las 4 esquinas actuales de una capa de imagen (0..1 del encuadre de tamaño `shape`), para empezar a
    transformarla libremente desde donde está."""
    if lay.get("quad"):
        return [[float(x), float(y)] for x, y in lay["quad"]]
    im = _cutout(lay.get("path") or "", "none", 12)
    H, W = shape[:2]
    iw, ih = (im.width, im.height) if im is not None else (4, 3)
    tw = float(lay.get("width", 16)) / 100.0 * W
    th = ih * tw / iw
    a = np.deg2rad(float(lay.get("angle", 0)))
    c, s_ = np.cos(a), np.sin(a)
    bw, bh = abs(tw * c) + abs(th * s_), abs(tw * s_) + abs(th * c)  # caja del giro (como `expand=True`)
    x, y = _place(W, H, bw, bh, lay)
    cx, cy = x + bw / 2, y + bh / 2
    pts = []
    for dx, dy in ((-tw / 2, -th / 2), (tw / 2, -th / 2), (tw / 2, th / 2), (-tw / 2, th / 2)):
        # PIL gira en sentido antihorario con ángulos positivos (con la y hacia abajo)
        pts.append([round(float(cx + dx * c + dy * s_) / W, 5), round(float(cy - dx * s_ + dy * c) / H, 5)])
    return pts


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
            "image": bool(l.get("path")), "adjust": True, "paint": bool(l.get("strokes")),
            "group": True, "rays": l.get("amount", 0) > 0,
            "particles": float((l.get("p") or {}).get("density", 0)) > 0,
            "artistic": bool(l.get("art")) and float(l.get("amount", 0)) > 0,
            "speed": float(l.get("amount", 0)) > 0,
            "enhance": float(l.get("amount", 0)) > 0 and any(l.get(x) for x in ("detail", "faces", "light")),
            }.get(k, False)


# ------------------------------------------------------------------ pintura (pincel y borrador)

_PAINT_CACHE: dict = {}


def _stroke_alpha(H, W, st):
    """Alfa de un trazo de pincel: toques redondos a lo largo del trazo (la presión cambia el radio), sin
    acumularse dentro del mismo trazo (como la opacidad del pincel de Photoshop). (alfa, x0, y0) de su caja."""
    side = max(H, W)
    r = max(0.5, float(st.get("r", 0.01)) * side)
    h = min(1.0, max(0.0, float(st.get("h", 0.0))))
    pts = [(float(q[0]) * (W - 1), float(q[1]) * (H - 1), float(q[2]) if len(q) > 2 else 1.0)
           for q in st.get("pts") or []]
    if not pts:
        return None
    pad = int(r * 1.3) + 3
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
    x1, y1 = min(W, int(max(xs)) + pad + 1), min(H, int(max(ys)) + pad + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    m = np.zeros((y1 - y0, x1 - x0), np.uint8)
    core = 0.55 + 0.45 * h  # el borde suave sale del desenfoque: el núcleo, más pequeño
    step = max(0.75, r * 0.12)
    dabs = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = int(np.hypot(b[0] - a[0], b[1] - a[1]) / step)
        for k in range(1, n + 1):
            t = k / (n + 1)
            dabs.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t))
        dabs.append(b)
    for x, y, pr in dabs:
        rad = max(0.5, r * core * max(0.05, min(1.0, pr)))
        cv2.circle(m, (int(round((x - x0) * 16)), int(round((y - y0) * 16))), int(round(rad * 16)), 255, -1,
                   cv2.LINE_AA, 4)
    a = m.astype(np.float32) / 255.0
    if h < 0.98:
        sig = max(0.3, r * (1 - h) * 0.33)
        a = cv2.GaussianBlur(a, (0, 0), sig)
        peak = 1.0 - np.exp(-((r * core) ** 2) / (2 * sig * sig))  # centro de un toque tras desenfocar
        a = np.minimum(1.0, a / max(0.05, peak))
    if st.get("b", "round") != "round":
        a = _brush_texture(a, st, r, side, dabs, x0, y0)
    return a * min(1.0, max(0.0, float(st.get("o", 1.0)))), x0, y0


BRUSHES = {"round": "Redondo", "watercolor": "Acuarela", "oil": "Óleo", "charcoal": "Carboncillo",
           "spray": "Aerógrafo (spray)", "marker": "Rotulador"}


def _noise(shape, cell: float, seed: int) -> np.ndarray:
    """Ruido suave 0..1 con granos de `cell` píxeles (se genera pequeño y se amplía: igual a cualquier tamaño)."""
    h, w = shape
    gh, gw = max(2, int(h / max(cell, 0.5)) + 2), max(2, int(w / max(cell, 0.5)) + 2)
    n = np.random.default_rng(seed).random((gh, gw), dtype=np.float32)
    return cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC if cell > 1 else cv2.INTER_AREA)


def _brush_texture(a: np.ndarray, st: dict, r: float, side: int, dabs, x0, y0) -> np.ndarray:
    """Pinceles de medios naturales sobre el alfa de un trazo: acuarela (borde más oscuro y papel), óleo (cerdas
    en la dirección del trazo), carboncillo (grano del papel), aerógrafo (puntitos) y rotulador (canto duro)."""
    b = st.get("b")
    seed = int(_sig(st.get("pts", [])[:3], b)[:8], 16)
    px = side / 2000.0  # «píxel» de referencia: el mismo dibujo en la vista y al exportar
    if b == "watercolor":
        soft = cv2.GaussianBlur(a, (0, 0), max(0.5, r * 0.25))
        edge = np.clip(a - cv2.GaussianBlur(a, (0, 0), max(0.5, r * 0.12)), 0, 1)
        paper = _noise(a.shape, 3 * px, seed) * 0.6 + _noise(a.shape, 14 * px, seed + 1) * 0.4
        return np.clip((soft * 0.55 + edge * 1.6) * (0.65 + 0.5 * paper), 0, 1)
    if b == "oil":
        if len(dabs) > 1:
            dx, dy = dabs[-1][0] - dabs[0][0], dabs[-1][1] - dabs[0][1]
            ang = float(np.degrees(np.arctan2(-dy, dx)))
        else:
            ang = 0.0
        n = _noise(a.shape, 1.5 * px, seed)
        streak = cv2.filter2D(n, -1, _motion_kernel(max(3.0, r * 1.2), ang))
        streak = (streak - streak.min()) / max(1e-6, float(streak.max() - streak.min()))
        hard = np.clip((a - 0.25) / 0.5, 0, 1)
        return np.clip(hard * (0.55 + 0.7 * streak), 0, 1)
    if b == "charcoal":
        n = _noise(a.shape, 1.2 * px, seed) * 0.7 + _noise(a.shape, 6 * px, seed + 1) * 0.3
        return np.clip((a * 1.25 - (1 - n) * 0.9), 0, 1)
    if b == "spray":
        dots = (_noise(a.shape, 0.9 * px, seed) < a * 0.45).astype(np.float32)
        return np.clip(dots * 0.9 + cv2.GaussianBlur(a, (0, 0), max(0.5, r * 0.3)) * 0.12, 0, 1)
    if b == "marker":
        return np.clip((a - 0.35) / 0.3, 0, 1) * 0.85
    return a


def paint_rgba(shape, lay: dict) -> np.ndarray | None:
    """Capa de pintura RGBA float (sin premultiplicar). Con caché incremental: al pintar un trazo nuevo solo
    se dibuja ese trazo encima de los anteriores."""
    strokes = lay.get("strokes") or []
    if not strokes:
        return None
    H, W = shape[:2]
    ck = (lay.get("id"), H, W)
    sigs = [_sig(st) for st in strokes]
    hit = _PAINT_CACHE.get(ck)
    if hit is not None and len(hit[0]) <= len(sigs) and hit[0] == sigs[:len(hit[0])]:
        pre, start = hit[1].copy(), len(hit[0])
    else:
        pre, start = np.zeros((H, W, 4), np.float32), 0
    for st in strokes[start:]:
        r = _stroke_alpha(H, W, st)
        if r is None:
            continue
        a, x0, y0 = r
        sub = pre[y0:y0 + a.shape[0], x0:x0 + a.shape[1]]
        a3 = a[..., None]
        if st.get("erase"):
            sub *= 1 - a3
        else:
            c = _hex(st.get("c", "#000000")).astype(np.float32)
            sub[..., :3] = c * a3 + sub[..., :3] * (1 - a3)  # premultiplicado: «encima»
            sub[..., 3:] = a3 + sub[..., 3:] * (1 - a3)
    _PAINT_CACHE.pop(ck, None)
    _PAINT_CACHE[ck] = (sigs, pre)
    while len(_PAINT_CACHE) > 6:
        _PAINT_CACHE.pop(next(iter(_PAINT_CACHE)))
    out = pre.copy()
    a = out[..., 3:]
    out[..., :3] = np.where(a > 1e-5, out[..., :3] / np.maximum(a, 1e-5), 0)
    return np.clip(out, 0, 1)


def paint_patch(shape, lay: dict):
    """La pintura solo en la caja de lo pintado: (RGBA, x0, y0, caja) o None."""
    full = paint_rgba(shape, lay)
    if full is None:
        return None
    ys, xs = np.nonzero(full[::4, ::4, 3] > 0)
    if not len(xs):
        return None
    H, W = shape[:2]
    x0, y0 = max(0, int(xs.min()) * 4 - 4), max(0, int(ys.min()) * 4 - 4)
    x1, y1 = min(W, int(xs.max()) * 4 + 8), min(H, int(ys.max()) * 4 + 8)
    return np.ascontiguousarray(full[y0:y1, x0:x1]), x0, y0, (x0, y0, x1, y1)


def layer_patch(shape, lay: dict, frame=None):
    """Píxeles propios de una capa de imagen o de pintura, solo en su caja: (RGBA, x0, y0, caja) o None."""
    return paint_patch(shape, lay) if lay["kind"] == "paint" else image_patch(shape, lay, frame)


def layer_rgba(shape, lay: dict, frame=None) -> np.ndarray | None:
    """Píxeles propios de una capa de imagen o de pintura (RGBA del tamaño de `shape`)."""
    if lay["kind"] == "paint":
        return paint_rgba(shape, lay)
    r = image_rgba(shape, lay, frame)
    return r[0] if r else None


def _frame(shape, box) -> tuple:
    """(x, y, ancho, alto) en píxeles del encuadre `box` (0..1) dentro de la imagen sin recortar."""
    hh, ww = shape[:2]
    x0, y0, x1, y1 = box if box is not None else (0.0, 0.0, 1.0, 1.0)
    return int(x0 * ww), int(y0 * hh), max(1, int(x1 * ww) - int(x0 * ww)), max(1, int(y1 * hh) - int(y0 * hh))


def _layer_alpha(img, lay, rgba, key, sl=None):
    """Alfa de una capa de imagen: el suyo, por la opacidad y por su máscara de capa (si la tiene). `sl`: el
    trozo de la imagen al que corresponde `rgba`."""
    a = rgba[..., 3] * (float(lay.get("opacity", 100)) / 100.0)
    if lay.get("mask"):
        m = selection_mask(img.shape, lay["mask"], img, key)
        a = a * (m[sl] if sl is not None else m)
    return a.astype(np.float32)


def fx_of(lay: dict) -> dict:
    """Los efectos activos de una capa, completados con los valores por defecto."""
    out = {}
    for k, v in (lay.get("fx") or {}).items():
        if k in FX_DEFAULTS and v and v.get("on"):
            out[k] = dict(FX_DEFAULTS[k], **v)
    return out


def _fx_margin(fx: dict, side: int) -> int:
    """Cuánto pueden salirse los efectos de la caja de la capa (píxeles)."""
    m = 0.0
    if "shadow" in fx:
        m = max(m, (fx["shadow"]["distance"] + 3 * fx["shadow"]["size"]) / 100 * side)
    if "glow" in fx:
        m = max(m, 3 * fx["glow"]["size"] / 100 * side)
    if "blur" in fx:
        m = max(m, 3 * fx["blur"]["amount"] / 100 * side)
    if "motion" in fx:
        m = max(m, fx["motion"]["amount"] / 100 * side)
    return int(m) + 2


def _motion_kernel(length: float, angle: float) -> np.ndarray:
    n = max(3, int(length) | 1)
    k = np.zeros((n, n), np.float32)
    c = n // 2
    a = np.deg2rad(angle)
    cv2.line(k, (int(round(c - np.cos(a) * c)), int(round(c + np.sin(a) * c))),
             (int(round(c + np.cos(a) * c)), int(round(c - np.sin(a) * c))), 1.0, 1, cv2.LINE_AA)
    return k / max(1e-6, k.sum())


def _fx_blur(rgb, a, fx, side):
    """Desenfoque gaussiano y de movimiento de la propia capa (premultiplicado: sin halos oscuros)."""
    if "blur" not in fx and "motion" not in fx:
        return rgb, a
    pre = np.dstack([rgb * a[..., None], a])
    if "blur" in fx:
        pre = cv2.GaussianBlur(pre, (0, 0), max(0.3, fx["blur"]["amount"] / 100 * side))
    if "motion" in fx:
        pre = cv2.filter2D(pre, -1, _motion_kernel(fx["motion"]["amount"] / 100 * side, fx["motion"]["angle"]))
    a = np.clip(pre[..., 3], 0, 1)
    rgb = np.where(a[..., None] > 1e-5, pre[..., :3] / np.maximum(a[..., None], 1e-5), 0)
    return np.clip(rgb, 0, 1).astype(np.float32), a.astype(np.float32)


def _fx_under(sub, a, fx, side, o):
    """Sombra paralela y resplandor exterior, por debajo de la capa (con la opacidad de la capa)."""
    if "shadow" in fx:
        f = fx["shadow"]
        d = f["distance"] / 100 * side
        ang = np.deg2rad(f["angle"])
        M = np.float32([[1, 0, -np.cos(ang) * d], [0, 1, np.sin(ang) * d]])  # luz desde `angle`: sombra opuesta
        sh = cv2.warpAffine(a, M, (a.shape[1], a.shape[0]), borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        if f["size"] > 0:
            sh = cv2.GaussianBlur(sh, (0, 0), max(0.3, f["size"] / 100 * side))
        sh = (sh * f["opacity"] / 100 * o)[..., None]
        sub = sub * (1 - sh) + _hex(f["color"]) * sh
    if "glow" in fx:
        f = fx["glow"]
        g = cv2.GaussianBlur(a, (0, 0), max(0.3, f["size"] / 100 * side))
        g = np.clip(g * 1.6, 0, 1)
        g = (g * f["opacity"] / 100 * o)[..., None]
        sub = 1 - (1 - sub) * (1 - _hex(f["color"]) * g)  # Trama: solo aclara
    return sub


def _embed(rc, cx0, cy0, x0, y0, h, w):
    """Pega el trozo `rc` (en cx0, cy0 de la imagen) en un trozo vacío de h × w que empieza en x0, y0."""
    out = np.zeros((h, w, 4), np.float32)
    ax0, ay0 = max(x0, cx0), max(y0, cy0)
    ax1, ay1 = min(x0 + w, cx0 + rc.shape[1]), min(y0 + h, cy0 + rc.shape[0])
    if ax1 > ax0 and ay1 > ay0:
        out[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0] = rc[ay0 - cy0:ay1 - cy0, ax0 - cx0:ax1 - cx0]
    return out


def harmonize(rgb: np.ndarray, a: np.ndarray, img: np.ndarray, box, amount: float) -> np.ndarray:
    """Armonizar: acerca la luz y el color de una capa pegada a los del fondo que la rodea (transferencia de
    medias y contrastes en Lab, como Reinhard et al. 2001, a medias para no teñir de más). `box`: x0, y0, w, h
    del trozo `rgb` dentro de `img`."""
    k = min(1.0, max(0.0, amount / 100.0))
    sel = a > 0.5
    if k <= 0 or sel.sum() < 16:
        return rgb
    H, W = img.shape[:2]
    x0, y0, w, h = box
    px, py = int(w * 0.6) + 8, int(h * 0.6) + 8  # el entorno: la caja de la capa ampliada
    X0, Y0, X1, Y1 = max(0, x0 - px), max(0, y0 - py), min(W, x0 + w + px), min(H, y0 + h + py)
    env = img[Y0:Y1, X0:X1]
    keep = np.ones(env.shape[:2], bool)
    keep[y0 - Y0:y0 - Y0 + h, x0 - X0:x0 - X0 + w] &= ~sel  # fuera de la propia capa
    step = max(1, int(np.sqrt(env.shape[0] * env.shape[1] / 60000.0)))
    bg = env[::step, ::step][keep[::step, ::step]]
    if len(bg) < 16:
        return rgb
    lab_bg = cv2.cvtColor(bg.reshape(-1, 1, 3).astype(np.float32), cv2.COLOR_RGB2LAB).reshape(-1, 3)
    lab = cv2.cvtColor(np.clip(rgb, 0, 1).astype(np.float32), cv2.COLOR_RGB2LAB)
    fg = lab[sel]
    mf, sf = fg.mean(0), fg.std(0) + 1e-3
    mb, sb = lab_bg.mean(0), lab_bg.std(0) + 1e-3
    # luminosidad: media y contraste hacia el fondo; color: la dominante del fondo, sin copiar su saturación
    ratio = np.clip(sb / sf, 0.6, 1.5)
    ratio[1:] = np.clip(ratio[1:], 0.8, 1.2)
    target_m = np.array([mf[0] + (mb[0] - mf[0]) * 0.7, mf[1] + (mb[1] - mf[1]) * 0.55,
                         mf[2] + (mb[2] - mf[2]) * 0.55], np.float32)
    out = (lab - mf) * (1 + (ratio - 1) * 0.7) + target_m
    out = lab + (out - lab) * k
    out[..., 0] = np.clip(out[..., 0], 0, 100)
    return np.clip(cv2.cvtColor(out.astype(np.float32), cv2.COLOR_LAB2RGB), 0, 1).astype(np.float32)


def apply_image_group(img: np.ndarray, base: dict, clipped: list, frame, key: str = ""):
    """Capa de imagen o pintura `base` con las capas recortadas a ella (`clipped`, de abajo arriba) y sus efectos,
    compuesta sobre `img`. Solo se trabaja en la caja de la capa (más lo que se salgan los efectos).
    Devuelve (imagen, (alfa del trozo, trozo) | None si no hay nada)."""
    import edits_adjust as A
    pb = layer_patch(img.shape, base, frame)
    if pb is None:
        return img, None
    rgba, x0, y0, _ = pb
    H, W = img.shape[:2]
    side = max(H, W)
    fx = fx_of(base)
    if fx:  # los efectos se salen de la caja: se amplía el trozo
        m = _fx_margin(fx, side)
        nx0, ny0 = max(0, x0 - m), max(0, y0 - m)
        nx1, ny1 = min(W, x0 + rgba.shape[1] + m), min(H, y0 + rgba.shape[0] + m)
        rgba = _embed(rgba, x0, y0, nx0, ny0, ny1 - ny0, nx1 - nx0)
        x0, y0 = nx0, ny0
    h, w = rgba.shape[:2]
    sl = (slice(y0, y0 + h), slice(x0, x0 + w))
    rgb = np.ascontiguousarray(rgba[..., :3])
    if float(base.get("harmonize", 0)) > 0:
        rgb = harmonize(rgb, rgba[..., 3], img, (x0, y0, w, h), float(base["harmonize"]))
    for c in clipped:
        if not _layer_active(c):
            continue
        if c["kind"] == "adjust":
            out = A.apply(rgb, c.get("adj", "curves"), c.get("p") or {})
            if c.get("blend", "normal") != "normal":
                out = np.clip(A.blend(rgb, out, c["blend"]), 0, 1)
            mo = float(c.get("opacity", 100)) / 100.0
            if c.get("mask"):
                mo = (selection_mask(img.shape, c["mask"], img, key)[sl] * mo)[..., None]
            rgb = rgb * (1 - mo) + out * mo
        elif c["kind"] in CLIP_BASES:
            rc = layer_patch(img.shape, c, frame)
            if rc is None:
                continue
            rc = _embed(rc[0], rc[1], rc[2], x0, y0, h, w)
            ac = _layer_alpha(img, c, rc, key, sl)[..., None]
            top = rc[..., :3]
            if c.get("blend", "normal") != "normal":
                top = np.clip(A.blend(rgb, top, c["blend"]), 0, 1)
            rgb = rgb * (1 - ac) + top * ac
    a_raw = rgba[..., 3]
    rgb, a_raw = _fx_blur(rgb, a_raw, fx, side)
    o = float(base.get("opacity", 100)) / 100.0
    a = a_raw * o
    if base.get("mask"):
        a = a * selection_mask(img.shape, base["mask"], img, key)[sl]
    sub = img[sl]
    if "shadow" in fx or "glow" in fx:
        sub = _fx_under(sub, a_raw if not base.get("mask") else a / max(o, 1e-6), fx, side, o)
    top = rgb
    if base.get("blend", "normal") != "normal":
        top = np.clip(A.blend(sub, rgb, base["blend"]), 0, 1)
    out = img.copy()
    out[sl] = sub * (1 - a[..., None]) + top * a[..., None]
    if "shadow" in fx or "glow" in fx:  # la sombra y el resplandor también tapan lo transparente
        a = np.maximum(a, np.clip(np.abs(sub - img[sl]).max(-1) * 4, 0, 1))
    return out, (a.astype(np.float32), sl)


# ------------------------------------------------------------------ rayos de luz

def rays_alpha(shape, lay: dict, frame=None) -> np.ndarray:
    """Intensidad (0..1) de los rayos de luz: haces que salen del foco (x, y del encuadre) en un abanico."""
    H, W = shape[:2]
    fx, fy, fw, fh = frame if frame is not None else (0, 0, W, H)
    cx, cy = fx + float(lay.get("x", 0.5)) * fw, fy + float(lay.get("y", 0.15)) * fh
    side = max(H, W)
    f = 4 if side > 1200 else 2  # se calcula a menor tamaño (es suave) y se amplía
    hs, ws = max(2, H // f), max(2, W // f)
    yy, xx = np.mgrid[0:hs, 0:ws].astype(np.float32)
    dx, dy = xx * f - cx, yy * f - cy
    r = np.hypot(dx, dy) / side
    th = np.degrees(np.arctan2(-dy, dx))  # 0 = derecha, 90 = arriba
    rng = np.random.default_rng(int(lay.get("seed", 7)))
    n = max(1, int(lay.get("count", 24)))
    spread = max(1.0, float(lay.get("spread", 70)))
    ang = float(lay.get("angle", 90)) + 180  # hacia dónde caen: el contrario de donde está la luz
    centers = ang + (rng.random(n) - 0.5) * spread
    widths = spread / n * (0.25 + rng.random(n) * 0.9)
    gains = 0.35 + rng.random(n) * 0.65
    v = np.zeros_like(r)
    for c, wd, g in zip(centers, widths, gains):
        d = (th - c + 180) % 360 - 180
        v = np.maximum(v, g * np.exp(-0.5 * (d / max(0.2, wd * 0.5)) ** 2))
    d = (th - ang + 180) % 360 - 180
    fan = np.clip(1 - np.abs(d) / (spread / 2 + 1e-6), 0, 1) ** 0.6 if spread < 359 else 1.0
    length = max(0.02, float(lay.get("length", 70)) / 100)
    fall = np.exp(-r / (length * 0.45)) * np.clip(r / 0.01, 0, 1)
    core = np.exp(-(r / 0.035) ** 2)  # el brillo del foco
    a = np.clip((v * fan * fall + core * 0.8) * float(lay.get("amount", 60)) / 100 * 1.6, 0, 1)
    return cv2.resize(a.astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)


def apply_rays(img: np.ndarray, lay: dict, frame=None) -> np.ndarray:
    a = rays_alpha(img.shape, lay, frame)[..., None]
    return (1 - (1 - img) * (1 - _hex(lay.get("color", "#fff1cf")) * a)).astype(np.float32)  # Trama


def _fx():
    import fx_engine as FX
    return FX


def apply_particles(img: np.ndarray, lay: dict, frame=None) -> np.ndarray:
    """Partículas en el encuadre (frame = x, y, w, h de la foto recortada dentro de la imagen sin recortar)."""
    FX = _fx()
    H, W = img.shape[:2]
    fx, fy, fw, fh = frame if frame is not None else (0, 0, W, H)
    p = dict(lay.get("p") or {})
    p.update(x0=fx / W, y0=fy / H, x1=(fx + fw) / W, y1=(fy + fh) / H)
    rgba = FX.particles_rgba(H, W, p)
    return FX.over(img, rgba, FX.PARTICLE_BLEND.get(p.get("kind"), "normal"))


def apply_pixel_layer(img: np.ndarray, lay: dict, key: str = "", frame=None):
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
    elif k == "adjust":
        import edits_adjust as A
        out = A.apply(img, lay.get("adj", "curves"), lay.get("p") or {}, frame)
    elif k == "portrait":
        out = apply_portrait(img, lay, key)
    elif k == "rays":
        out = apply_rays(img, lay, frame)
    elif k == "particles":
        out = apply_particles(img, lay, frame)
    elif k == "speed":  # barrido: sujeto nítido, fondo con movimiento y estela (bokeh.py)
        import bokeh as BK
        out = BK.speed(img, float(lay.get("amount", 50)), None if lay.get("auto", True) else float(lay.get("angle", 0)),
                       float(lay.get("trail", 50)), lay.get("detect", "auto"), key)
    elif k == "enhance":  # Mejorar con IA (fx_engine.enhance): enfocar y limpiar, caras y aclarar
        out = E.enhance_photo(np.clip(img, 0, 1).astype(np.float32),
                              {x: lay.get(x) for x in ("detail", "faces", "light", "amount")})
    elif k == "artistic":
        out = _fx().artistic(img, lay.get("art", "watercolor"), float(lay.get("amount", 100)),
                             float(lay.get("detail", 50)))
    else:
        return img, None
    if lay.get("blend", "normal") != "normal":
        import edits_adjust as A
        out = np.clip(A.blend(img, out, lay["blend"]), 0, 1)
    o = float(lay.get("opacity", 100)) / 100.0
    m = selection_mask(img.shape, lay["mask"], img, key)[..., None] * o if lay.get("mask") else o
    if lay.get("mask") or o < 1:
        out = img * (1 - m) + out * m
        if alpha is not None:
            mm = m[..., 0] if isinstance(m, np.ndarray) else m
            alpha = 1 - mm * (1 - alpha)
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

    LIMIT = 10

    def __init__(self):
        self.key = ""
        self.base_sig = ""
        self.stack: list = []  # [(firma acumulada, imagen, alfa)], de la menos a la más usada
        self.focus = None      # id de la capa que se está editando: se guarda siempre lo que entra en ella
        self._pin = None

    def pixel_stage(self, base: np.ndarray, layers: list, key: str = "", upto: int | None = None, box=None):
        """Imagen sin recortar tras las capas de imagen visibles (hasta `upto`, excluida). (img, alfa).
        `box`: el recorte, para colocar las capas de imagen sobre el encuadre."""
        frame = _frame(base.shape, box)
        bsig = _sig(key, base.shape, float(base[::97, ::97].sum()), lama_ready(), frame)
        if bsig != self.base_sig:
            self.base_sig, self.stack = bsig, []
        img, alpha = base, None
        sig = bsig
        pix = [l for l in layers if l["kind"] not in OVERLAY]
        if upto is not None:
            pix = pix[:upto]
        img, alpha, _ = self._run(_nodes(pix), img, alpha, sig, frame, key)
        return img, alpha

    def _cached(self, sig):
        for i, c in enumerate(self.stack):
            if c[0] == sig:
                self.stack.append(self.stack.pop(i))  # usada: pasa al final (la última en salir)
                return c
        return None

    def _push(self, sig, img, alpha):
        self.stack.append((sig, img, alpha))
        while len(self.stack) > self.LIMIT:
            i = next((j for j, c in enumerate(self.stack) if c[0] != self._pin), 0)
            self.stack.pop(i)

    def _sigs(self, nodes, sig):
        """Firmas acumuladas tras cada nodo (sin calcular píxeles), con el mismo recorrido que `_run`."""
        out, i = [], 0
        while i < len(nodes):
            node = nodes[i]
            if isinstance(node, tuple):
                i += 1
                if _layer_active(node[0]):
                    sig = _sig(sig, node[0], _active_tree(node[1]))
                out.append((i, sig, node[0]["id"]))
                continue
            j = i + 1
            if node["kind"] in CLIP_BASES:
                while j < len(nodes) and not isinstance(nodes[j], tuple) and nodes[j].get("clip") \
                        and nodes[j]["kind"] in CLIPPABLE:
                    j += 1
            ids = [g["id"] for g in nodes[i:j]]
            if _layer_active(node):
                sig = _sig(sig, [g for g in nodes[i:j] if _layer_active(g)])
            i = j
            out.append((i, sig, ids))
        return out

    def _run(self, nodes, img, alpha, sig, frame, key):
        """Compone una lista de capas y grupos (de abajo arriba). (img, alfa, firma). Empieza en la composición
        guardada más alta que siga valiendo."""
        steps = self._sigs(nodes, sig)
        start = 0
        for k in range(len(steps) - 1, -1, -1):
            hit = self._cached(steps[k][1])
            if hit is not None:
                img, alpha, sig, start = hit[1], hit[2], steps[k][1], steps[k][0]
                break
        # lo que entra en la capa elegida no se tira de la caché (al arrastrar un control solo se rehace ella)
        if self.focus:
            for k, (_, _, ids) in enumerate(steps):
                if self.focus in (ids if isinstance(ids, list) else [ids]):
                    if k > 0:
                        self._pin = steps[k - 1][1]
                    break
        i = start
        while i < len(nodes):
            node = nodes[i]
            if isinstance(node, tuple):  # grupo: (capa del grupo, [nodos de dentro])
                grp, kids = node
                i += 1
                if not _layer_active(grp):
                    continue
                gsig = _sig(sig, grp, _active_tree(kids))
                below, below_a = img, alpha
                res, res_a, _ = self._run(kids, img, alpha, sig, frame, key)
                img, alpha = _mix_group(below, below_a, res, res_a, grp, key)
                sig = gsig
                self._push(sig, img, alpha)
                continue
            lay = node
            j = i + 1
            if lay["kind"] in CLIP_BASES:  # con las capas recortadas a ella
                while j < len(nodes) and not isinstance(nodes[j], tuple) and nodes[j].get("clip") \
                        and nodes[j]["kind"] in CLIPPABLE:
                    j += 1
            group, i = nodes[i:j], j
            if not _layer_active(lay):
                continue  # una base oculta oculta también lo recortado a ella
            sig = _sig(sig, [g for g in group if _layer_active(g)])
            if lay["kind"] in CLIP_BASES:
                img, pa = apply_image_group(img, lay, group[1:], frame, key)
                if pa is not None and alpha is not None:  # la capa tapa lo transparente
                    a, sl = pa
                    alpha = alpha.copy()
                    alpha[sl] = alpha[sl] + a * (1 - alpha[sl])
            else:
                img, a = apply_pixel_layer(img, lay, key, frame)
                if a is not None:
                    alpha = a if alpha is None else alpha * a
            self._push(sig, img, alpha)
        return img, alpha, sig

    def render(self, base: np.ndarray, doc: dict, box, key: str = ""):
        img, alpha = self.pixel_stage(base, doc.get("layers", []), key, box=box)
        return finish(img, alpha, doc, box)


def _nodes(pix: list) -> list:
    """Lista plana de capas (de abajo arriba) a nodos: capas sueltas y (grupo, [nodos]) en el sitio del grupo.
    Las capas de un grupo se componen donde está el grupo, aunque no estén seguidas en la lista."""
    groups = {l["id"] for l in pix if l["kind"] == "group"}
    kids: dict = {}
    for l in pix:
        if l.get("parent") in groups and l["kind"] != "group":
            kids.setdefault(l["parent"], []).append(l)
    out = []
    for l in pix:
        if l["kind"] == "group":
            out.append((l, kids.get(l["id"], [])))
        elif l.get("parent") not in groups:
            out.append(l)
    return out


def _active_tree(nodes):
    return [(_layer_active(n[0]) and n[0], _active_tree(n[1])) if isinstance(n, tuple) else n
            for n in nodes if isinstance(n, tuple) or _layer_active(n)]


def _mix_group(below, below_a, res, res_a, grp, key):
    """El grupo sobre lo de debajo: modo de fusión, opacidad y máscara del grupo."""
    import edits_adjust as A
    if grp.get("blend", "normal") != "normal":
        res = np.clip(A.blend(below, res, grp["blend"]), 0, 1)
    m = float(grp.get("opacity", 100)) / 100.0
    if grp.get("mask"):
        m = selection_mask(below.shape, grp["mask"], below, key) * m
    if not isinstance(m, np.ndarray) and m >= 1.0:
        return res, res_a
    m3 = m[..., None] if isinstance(m, np.ndarray) else m
    img = (below * (1 - m3) + res * m3).astype(np.float32)
    if res_a is None and below_a is None:
        return img, None
    ba = np.ones(below.shape[:2], np.float32) if below_a is None else below_a
    ra = np.ones(below.shape[:2], np.float32) if res_a is None else res_a
    return img, (ba * (1 - m) + ra * m).astype(np.float32)


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
        top = rgba[..., :3]
        if lay.get("blend", "normal") != "normal":
            import edits_adjust as A
            top = np.clip(A.blend(img, top, lay["blend"]), 0, 1)
        img = img * (1 - a) + top * a
        if alpha is not None:
            alpha = np.maximum(alpha, a[..., 0])
    return np.clip(img, 0, 1).astype(np.float32), alpha


def compose(base: np.ndarray, doc: dict, box, key: str = ""):
    """Composición completa sin caché: (sRGB 0..1 recortada, alfa | None)."""
    return Compositor().render(base, doc, box, key)


def layer_input(comp: Compositor, base: np.ndarray, doc: dict, layer: dict, key: str = "", box=None) -> np.ndarray:
    """Imagen que entra en `layer` (todas las capas de imagen de debajo aplicadas)."""
    pix = [l for l in doc["layers"] if l["kind"] not in OVERLAY]
    i = next((j for j, l in enumerate(pix) if l["id"] == layer["id"]), len(pix))
    return comp.pixel_stage(base, doc["layers"], key, upto=i, box=box)[0]


def layer_boxes(doc: dict, shape, box) -> dict:
    """{id: (x0, y0, x1, y1)} de las capas de imagen, texto y logo, en 0..1 del encuadre (para la vista)."""
    hh, ww = shape[:2]
    fx, fy, fw, fh = _frame(shape, box)
    out = {}
    for lay in doc.get("layers", []):
        if lay["kind"] not in ("image", "text", "logo", "rays") or not _layer_active(lay):
            continue
        if lay["kind"] == "rays":  # el foco, como una caja pequeña
            x, y = float(lay.get("x", 0.5)), float(lay.get("y", 0.15))
            out[lay["id"]] = (x - 0.02, y - 0.02 * fw / fh, x + 0.02, y + 0.02 * fw / fh)
        elif lay["kind"] == "image":
            r = image_patch((hh, ww), lay, (fx, fy, fw, fh))
            if r:
                x0, y0, x1, y1 = r[3]
                out[lay["id"]] = ((x0 - fx) / fw, (y0 - fy) / fh, (x1 - fx) / fw, (y1 - fy) / fh)
        else:
            r = overlay_rgba((fh, fw), lay)
            if r:
                x0, y0, x1, y1 = r[1]
                out[lay["id"]] = (x0 / fw, y0 / fh, x1 / fw, y1 / fh)
    return out


# ------------------------------------------------------------------ lienzo en blanco

CANVAS_PRESETS = [("Instagram vertical (4:5)", 1080, 1350), ("Instagram cuadrado", 1080, 1080),
                  ("Historia / Reel (9:16)", 1080, 1920), ("Horizontal 3:2 (6000 × 4000)", 6000, 4000),
                  ("Vertical 2:3 (4000 × 6000)", 4000, 6000), ("Full HD (1920 × 1080)", 1920, 1080),
                  ("A4 a 300 ppp", 2480, 3508), ("Póster 50 × 70 cm a 200 ppp", 3937, 5512)]


def canvases_dir(folder: str) -> str:
    return os.path.join(edits_dir(folder), "lienzos")


def list_canvases(folder: str) -> list:
    d = canvases_dir(folder)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".png"))


def new_canvas(folder: str, width: int, height: int, color: str = "#ffffff", name: str = "") -> str:
    """Crea un lienzo en blanco (PNG de un color) en `<carpeta>/.phootflow/edits/lienzos/` para montar encima.
    Se abre en Edits como cualquier foto; no aparece en Events."""
    from PIL import Image
    d = canvases_dir(folder)
    os.makedirs(d, exist_ok=True)
    base = "".join(ch for ch in (name or "Montaje").strip() if ch not in '\\/:*?"<>|') or "Montaje"
    p, k = os.path.join(d, base + ".png"), 2
    while os.path.exists(p):
        p, k = os.path.join(d, f"{base} {k}.png"), k + 1
    rgb = tuple(int(v * 255 + 0.5) for v in _hex(color))
    Image.new("RGB", (max(16, int(width)), max(16, int(height))), rgb).save(p)
    return p


# ------------------------------------------------------------------ exportar

def upscale(img: np.ndarray, factor: int = 2, alpha: np.ndarray | None = None):
    """Aumento de escala sin IA: Lanczos y luego se recupera el borde (máscara de enfoque solo sobre la luminosidad,
    limitada para que no salgan halos) y una pizca de grano fino para que no parezca plástico."""
    f = int(max(1, factor))
    if f == 1:
        return img, alpha
    h, w = img.shape[:2]
    FX = _fx()
    if FX.upscaler_ready():  # con IA (Real-ESRGAN) si el modelo está: bordes nítidos sin dientes de sierra
        big = FX.upscale(np.clip(img, 0, 1).astype(np.float32), float(f), True)
        if alpha is not None:
            alpha = np.clip(cv2.resize(alpha, (w * f, h * f), interpolation=cv2.INTER_CUBIC), 0, 1)
        return big, alpha
    big = np.empty((h * f, w * f, 3), np.float32)
    rng = np.random.default_rng(5)
    band, ov = 384, 12  # por franjas: una foto de 24 Mpx ampliada no cabe entera en memoria varias veces
    for r0 in range(0, h, band):
        a0, a1 = max(0, r0 - ov), min(h, r0 + band + ov)
        part = cv2.resize(img[a0:a1], (w * f, (a1 - a0) * f), interpolation=cv2.INTER_LANCZOS4)
        y = (0.2126 * part[..., 0] + 0.7152 * part[..., 1] + 0.0722 * part[..., 2]).astype(np.float32)
        d1 = y - cv2.GaussianBlur(y, (0, 0), 0.6 * f)
        d2 = y - cv2.GaussianBlur(y, (0, 0), 1.6 * f)
        det = np.clip(d1 * 0.9 + d2 * 0.35, -0.08, 0.08)
        g = rng.standard_normal(y.shape, dtype=np.float32)
        det += (g - cv2.GaussianBlur(g, (0, 0), 1.0)) * 0.004
        part = np.clip(part + det[..., None], 0, 1)
        s0 = (r0 - a0) * f
        n = (min(h, r0 + band) - r0) * f
        big[r0 * f:r0 * f + n] = part[s0:s0 + n]
    if alpha is not None:
        alpha = np.clip(cv2.resize(alpha, (w * f, h * f), interpolation=cv2.INTER_CUBIC), 0, 1)
    return big, alpha


def render_final(folder: str, photo_path: str, doc: dict, long_side: int | None = None, scale: int = 1):
    """La foto revelada con todas sus capas a tamaño completo: (RGB float 0–1, alfa float o None). `scale` 2
    la amplía al doble (Aumento de escala)."""
    s = base_settings(folder, photo_path, doc)
    key = photo_path + "#full"
    base = render_base(photo_path, s, full=True, key=key)
    img, alpha = compose(base, doc, crop_box(s), key)
    if scale > 1:
        img, alpha = upscale(img, scale, alpha)
    if long_side and max(img.shape[:2]) > long_side:
        f = long_side / max(img.shape[:2])
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        if alpha is not None:
            alpha = cv2.resize(alpha, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA)
    return img, alpha


def export(folder: str, photo_path: str, doc: dict, out_path: str, fmt: str = "jpg", quality: int = 95,
           long_side: int | None = None, scale: int = 1) -> str:
    import export_formats as EF
    img, alpha = render_final(folder, photo_path, doc, long_side, scale)
    if fmt == "jpg" and alpha is not None:
        fmt = "png"  # un JPG no guarda transparencia: se exporta en PNG
        out_path = os.path.splitext(out_path)[0] + ".png"
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
    if fmt == "tif":  # 16 bits, con transparencia si la hay
        EF.save_raster(img, out_path, alpha, dpi=300, bits16=True)
        return out_path
    EF.save_raster(img, out_path, alpha, quality=quality, dpi=300, cmyk=fmt == "tifc",
                   icc=E._SRGB_ICC if fmt in ("png", "webp") else None)
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


def events_todo(photos, folder: str) -> list:
    """Fotos aprobadas con trabajo de Edits para Events (las que exporta `export_for_events`)."""
    docs = pending_events(folder)
    return [p for p in photos if p.keep and os.path.normcase(p.path) in docs]


def export_for_events(photos, folder: str, out_dir: str, sizes, quality: int, progress=None) -> int:
    """Exporta las fotos aprobadas con retoque de Edits en «Entrega/Color»: sustituye a la edición normal."""
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
                       E.delivery_path(out_dir, p.name, size, False, sizes), "jpg", quality, side)
                n += 1
            except Exception as e:
                p.error = f"{type(e).__name__}: {e}"
    return n


def snapshot(doc: dict) -> dict:
    return copy.deepcopy(doc)
