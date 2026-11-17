"""Graphics: motor de diseño tipo Canva (sin interfaz).

- Modelo: un diseño es un dict JSON con tamaño, fondo y una lista de elementos (de abajo arriba). Las medidas van
  en píxeles del diseño; los diseños de impresión se guardan a 300 ppp y se muestran en mm.
- Un único pintor (`paint_element` / `paint_design`) dibuja el lienzo interactivo y todas las exportaciones, para
  que lo que se ve sea lo que se imprime.
- Vectorizado de fotos y logos con OpenCV + numpy (sin DLL nuevas: Smart App Control): B/N por umbral o color
  por k-medias, contornos suavizados con curvas Bézier y apilados sin rendijas.
- Exportación: PDF vectorial de imprenta (sangrado, marcas de corte, fotos a 300 ppp, RGB o CMYK), SVG y PNG/JPG.

Referencias de funciones y UX (sin código copiado): canva-clone de Davronov-Alimardon (Apache-2.0, Fabric.js),
OpenDesign de clawnify (MIT, Fabric.js) y LidoJS (sin licencia publicada: solo como referencia visual).
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import time
import uuid

import cv2
import numpy as np

MM_PER_IN = 25.4
PRINT_DPI = 300
SCREEN_DPI = 96

# (grupo, nombre, ancho, alto, unidad)
PRESETS = [
    ("Redes sociales", "Instagram cuadrado", 1080, 1080, "px"),
    ("Redes sociales", "Instagram vertical 4:5", 1080, 1350, "px"),
    ("Redes sociales", "Story / Reel", 1080, 1920, "px"),
    ("Redes sociales", "Publicación de Facebook", 1200, 630, "px"),
    ("Redes sociales", "Portada de Facebook", 1640, 624, "px"),
    ("Redes sociales", "Miniatura de YouTube", 1280, 720, "px"),
    ("Impresión", "A4 vertical", 210, 297, "mm"),
    ("Impresión", "A4 horizontal", 297, 210, "mm"),
    ("Impresión", "A5 (flyer)", 148, 210, "mm"),
    ("Impresión", "A6 (postal)", 105, 148, "mm"),
    ("Impresión", "Flyer DL", 99, 210, "mm"),
    ("Impresión", "Póster A3", 297, 420, "mm"),
    ("Impresión", "Tarjeta de visita", 85, 55, "mm"),
    ("Copias de foto", "10 × 15 cm", 100, 150, "mm"),
    ("Copias de foto", "13 × 18 cm", 130, 180, "mm"),
    ("Copias de foto", "15 × 20 cm", 150, 200, "mm"),
    ("Copias de foto", "20 × 30 cm", 200, 300, "mm"),
]

SHAPES = ("rect", "ellipse", "triangle", "star", "line")
PALETTE = ["#05070F", "#FFFFFF", "#F50D8A", "#1F2A44", "#C9A86A", "#E9E4DC", "#8A9A86", "#D94F3D", "#2E86AB", "#F2C14E"]


def mm_to_px(mm: float, dpi: float = PRINT_DPI) -> float:
    return mm / MM_PER_IN * dpi


def px_to_mm(px: float, dpi: float = PRINT_DPI) -> float:
    return px * MM_PER_IN / dpi


# ------------------------------------------------------------------ modelo

def new_id() -> str:
    return uuid.uuid4().hex[:12]


def new_design(w: float, h: float, unit: str = "px", name: str = "Diseño sin título", bg: str = "#FFFFFF") -> dict:
    """Diseño vacío. `w`/`h` en la unidad indicada (mm para impresión)."""
    dpi = PRINT_DPI if unit == "mm" else SCREEN_DPI
    if unit == "mm":
        w, h = mm_to_px(w), mm_to_px(h)
    now = time.time()
    return {"version": 1, "id": new_id(), "name": name, "w": round(float(w), 2), "h": round(float(h), 2),
            "unit": unit, "dpi": dpi, "bg": bg, "elements": [], "created": now, "modified": now}


def design_from_preset(name: str) -> dict:
    for g, n, w, h, u in PRESETS:
        if n == name:
            return new_design(w, h, u, name=n)
    raise KeyError(name)


def size_label(d: dict) -> str:
    if d.get("unit") == "mm":
        return f"{px_to_mm(d['w']):.0f} × {px_to_mm(d['h']):.0f} mm"
    return f"{d['w']:.0f} × {d['h']:.0f} px"


def base_el(kind: str, x: float, y: float, w: float, h: float, **kw) -> dict:
    el = {"id": new_id(), "type": kind, "x": float(x), "y": float(y), "w": float(w), "h": float(h), "rot": 0.0,
          "opacity": 1.0, "hidden": False, "locked": False, "name": ""}
    el.update(kw)
    return el


def text_el(d: dict, text: str, role: str = "body", **kw) -> dict:
    """Texto con tamaño relativo al diseño (título, subtítulo o cuerpo)."""
    m = min(d["w"], d["h"])
    size = {"title": 0.10, "subtitle": 0.055, "body": 0.035}.get(role, 0.035) * m
    w = d["w"] * 0.8
    el = base_el("text", (d["w"] - w) / 2, d["h"] * 0.4, w, size * 1.4, text=text, font="Segoe UI",
                 size=round(size, 1), bold=role == "title", italic=False, color="#05070F", align="center",
                 line=1.15, spacing=0.0, upper=False)
    el.update(kw)
    el["h"] = kw.get("h", text_height(el))
    return el


def shape_el(d: dict, kind: str, **kw) -> dict:
    m = min(d["w"], d["h"]) * 0.35
    w, h = (m * 1.6, max(2.0, m * 0.02)) if kind == "line" else (m, m)
    el = base_el("shape", (d["w"] - w) / 2, (d["h"] - h) / 2, w, h, shape=kind, fill="#F50D8A",
                 stroke="#05070F", stroke_w=0.0 if kind != "line" else max(2.0, m * 0.02), radius=0.0)
    el.update(kw)
    return el


def image_el(d: dict, path: str = "", aspect: float = 1.5, **kw) -> dict:
    """Foto encajada al 60 % del lienzo. Sin `path` es un hueco de plantilla («arrastra aquí una foto»)."""
    if aspect >= d["w"] / d["h"]:
        w = d["w"] * 0.6
        h = w / aspect
    else:
        h = d["h"] * 0.6
        w = h * aspect
    el = base_el("image", (d["w"] - w) / 2, (d["h"] - h) / 2, w, h, path=path, zoom=1.0, fx=0.5, fy=0.5,
                 radius=0.0, bw=False)
    el.update(kw)
    return el


def vector_el(vec: dict, x: float, y: float, w: float, h: float, **kw) -> dict:
    el = base_el("vector", x, y, w, h, viewbox=list(vec["viewbox"]), paths=copy.deepcopy(vec["paths"]))
    el.update(kw)
    return el


def qr_el(d: dict, text: str, color: str = "#05070F", bg: str = "#FFFFFF") -> dict:
    """Código QR vectorial (OpenCV, sin dependencias nuevas): módulos unidos por filas en un único trazado."""
    m = cv2.QRCodeEncoder.create().encode(text)
    if m is None or not m.size:
        raise ValueError("No se pudo crear el código QR")
    dark = m < 128
    n = dark.shape[0]
    q = 2  # zona en blanco alrededor
    parts = []
    for y in range(n):
        x = 0
        while x < n:
            if dark[y, x]:
                x0 = x
                while x < n and dark[y, x]:
                    x += 1
                parts.append(f"M {x0 + q} {y + q} L {x + q} {y + q} L {x + q} {y + q + 1} L {x0 + q} {y + q + 1} Z")
            else:
                x += 1
    s_ = n + 2 * q
    paths = [{"fill": bg, "d": f"M 0 0 L {s_} 0 L {s_} {s_} L 0 {s_} Z"}] if bg else []
    paths.append({"fill": color, "d": " ".join(parts)})
    side = min(d["w"], d["h"]) * 0.25
    el = base_el("vector", (d["w"] - side) / 2, (d["h"] - side) / 2, side, side, viewbox=[s_, s_], paths=paths,
                 qr=text)
    el["name"] = "QR  " + text[:30]
    return el


def find(d: dict, eid: str) -> dict | None:
    return next((e for e in d["elements"] if e["id"] == eid), None)


def duplicate(d: dict, el: dict, offset: float | None = None) -> dict:
    c = copy.deepcopy(el)
    c["id"] = new_id()
    off = offset if offset is not None else min(d["w"], d["h"]) * 0.03
    c["x"] += off
    c["y"] += off
    return c


def layer_name(el: dict) -> str:
    if el.get("name"):
        return el["name"]
    t = el["type"]
    if t == "text":
        s = el.get("text", "").strip().splitlines()
        return "T  " + (s[0][:28] if s else "Texto")
    if t == "image":
        return "Foto  " + (os.path.basename(el["path"]) if el.get("path") else "(hueco)")
    if t == "vector":
        n = len(el.get("paths", []))
        return f"Vector  ({n} {'color' if n == 1 else 'colores'})"
    return {"rect": "Rectángulo", "ellipse": "Círculo", "triangle": "Triángulo", "star": "Estrella",
            "line": "Línea"}.get(el.get("shape"), "Forma")


# ------------------------------------------------------------------ alineado

def bbox(el: dict) -> tuple[float, float, float, float]:
    """Caja que ocupa el elemento ya girado (x0, y0, x1, y1)."""
    cx, cy = el["x"] + el["w"] / 2, el["y"] + el["h"] / 2
    a = math.radians(el.get("rot", 0.0))
    c, s = abs(math.cos(a)), abs(math.sin(a))
    hw, hh = (el["w"] * c + el["h"] * s) / 2, (el["w"] * s + el["h"] * c) / 2
    return cx - hw, cy - hh, cx + hw, cy + hh


def align(d: dict, els: list[dict], how: str) -> None:
    """Alinea a la página (un elemento) o al conjunto (varios): left, hcenter, right, top, vcenter, bottom."""
    if not els:
        return
    if len(els) == 1:
        X0, Y0, X1, Y1 = 0.0, 0.0, d["w"], d["h"]
    else:
        bs = [bbox(e) for e in els]
        X0, Y0 = min(b[0] for b in bs), min(b[1] for b in bs)
        X1, Y1 = max(b[2] for b in bs), max(b[3] for b in bs)
    for e in els:
        x0, y0, x1, y1 = bbox(e)
        dx = dy = 0.0
        if how == "left":
            dx = X0 - x0
        elif how == "right":
            dx = X1 - x1
        elif how == "hcenter":
            dx = (X0 + X1) / 2 - (x0 + x1) / 2
        elif how == "top":
            dy = Y0 - y0
        elif how == "bottom":
            dy = Y1 - y1
        elif how == "vcenter":
            dy = (Y0 + Y1) / 2 - (y0 + y1) / 2
        e["x"] += dx
        e["y"] += dy


def distribute(els: list[dict], axis: str) -> None:
    """Reparte con el mismo hueco (3 o más elementos)."""
    if len(els) < 3:
        return
    i0, i1 = (0, 2) if axis == "h" else (1, 3)
    els = sorted(els, key=lambda e: bbox(e)[i0])
    bs = [bbox(e) for e in els]
    total = sum(b[i1] - b[i0] for b in bs)
    gap = (bs[-1][i1] - bs[0][i0] - total) / (len(els) - 1)
    pos = bs[0][i1] + gap
    for e, b in zip(els[1:-1], bs[1:-1]):
        delta = pos - b[i0]
        e["x" if axis == "h" else "y"] += delta
        pos += (b[i1] - b[i0]) + gap


def snap_lines(d: dict, others: list[dict]) -> tuple[list[float], list[float]]:
    """Líneas de imán: bordes y centro de la página y de los demás elementos."""
    xs, ys = [0.0, d["w"] / 2, d["w"]], [0.0, d["h"] / 2, d["h"]]
    for e in others:
        if e.get("hidden"):
            continue
        x0, y0, x1, y1 = bbox(e)
        xs += [x0, (x0 + x1) / 2, x1]
        ys += [y0, (y0 + y1) / 2, y1]
    return xs, ys


def snap(box: tuple, xs: list[float], ys: list[float], tol: float) -> tuple[float, float, list, list]:
    """Desplazamiento (dx, dy) que pega la caja a la línea más cercana, y las guías a dibujar."""
    x0, y0, x1, y1 = box
    best = [None, None]
    for vals, cands, k in (((x0, (x0 + x1) / 2, x1), xs, 0), ((y0, (y0 + y1) / 2, y1), ys, 1)):
        for v in vals:
            for c in cands:
                dist = abs(c - v)
                if dist <= tol and (best[k] is None or dist < abs(best[k][0])):
                    best[k] = (c - v, c)
    dx = best[0][0] if best[0] else 0.0
    dy = best[1][0] if best[1] else 0.0
    gx = [best[0][1]] if best[0] else []
    gy = [best[1][1]] if best[1] else []
    return dx, dy, gx, gy


# ------------------------------------------------------------------ plantillas

def _ph(d, x, y, w, h, **kw):
    return image_el(d, "", x=x, y=y, w=w, h=h, **kw)


def _t(d, text, role, x, y, w, **kw):
    el = text_el(d, text, role, **kw)
    el.update(x=x, y=y, w=w)
    el["h"] = text_height(el)
    return el


def tpl_post_evento(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#05070F"
    els = [_ph(d, 0, 0, W, H * 0.72),
           shape_el(d, "rect", x=0, y=H * 0.72, w=W, h=H * 0.28, fill="#05070F", stroke_w=0.0),
           shape_el(d, "rect", x=W * 0.08, y=H * 0.775, w=W * 0.12, h=max(3.0, H * 0.006), fill="#F50D8A",
                    stroke_w=0.0),
           _t(d, "Nombre del evento", "title", W * 0.08, H * 0.79, W * 0.84, color="#FFFFFF", align="left",
              font="Georgia"),
           _t(d, "Fecha · Lugar", "body", W * 0.08, H * 0.90, W * 0.84, color="#C9C9C9", align="left",
              upper=True, spacing=2.0)]
    return els


def tpl_invitacion(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#F7F3EE"
    m = min(W, H) * 0.06
    return [shape_el(d, "rect", x=m, y=m, w=W - 2 * m, h=H - 2 * m, fill="#00000000", stroke="#C9A86A",
                     stroke_w=max(2.0, m * 0.06)),
            _t(d, "Nos casamos", "subtitle", W * 0.1, H * 0.16, W * 0.8, color="#8A7350", font="Georgia",
               italic=True),
            _t(d, "Ana & Pablo", "title", W * 0.1, H * 0.25, W * 0.8, color="#2B2B2B", font="Georgia",
               bold=False),
            _ph(d, W * 0.2, H * 0.40, W * 0.6, H * 0.32, radius=min(W, H) * 0.3),
            _t(d, "Sábado 12 de septiembre · 18:00\nFinca El Olivar, Sevilla", "body", W * 0.1, H * 0.77, W * 0.8,
               color="#4A4A4A")]


def tpl_tarjeta(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#05070F"
    return [shape_el(d, "rect", x=0, y=0, w=W * 0.36, h=H, fill="#F50D8A", stroke_w=0.0),
            _ph(d, W * 0.06, H * 0.28, W * 0.24, W * 0.24, radius=W * 0.12),
            _t(d, "Tu nombre", "title", W * 0.42, H * 0.22, W * 0.54, color="#FFFFFF", align="left",
               size=round(H * 0.12, 1)),
            _t(d, "Fotografía de eventos", "body", W * 0.42, H * 0.40, W * 0.54, color="#F50D8A", align="left",
               size=round(H * 0.065, 1), upper=True, spacing=1.5),
            _t(d, "600 000 000\ncorreo@ejemplo.com\nwww.tuweb.com", "body", W * 0.42, H * 0.58, W * 0.54,
               color="#D8D8D8", align="left", size=round(H * 0.06, 1))]


def tpl_collage(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    g = min(W, H) * 0.025
    return [_ph(d, g, g, W - 2 * g, H * 0.55 - 1.5 * g),
            _ph(d, g, H * 0.55 + g / 2, (W - 3 * g) / 2, H * 0.32),
            _ph(d, W / 2 + g / 2, H * 0.55 + g / 2, (W - 3 * g) / 2, H * 0.32),
            _t(d, "Recuerdos del día", "subtitle", g, H * 0.9, W - 2 * g, color="#05070F", font="Georgia")]


def tpl_cita(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#1F2A44"
    return [_t(d, "“", "title", W * 0.1, H * 0.12, W * 0.8, color="#F50D8A", size=round(min(W, H) * 0.3, 1),
               font="Georgia"),
            _t(d, "La fotografía es la poesía\nde lo inmóvil", "subtitle", W * 0.1, H * 0.42, W * 0.8,
               color="#FFFFFF", font="Georgia", italic=True),
            _t(d, "— Tu firma", "body", W * 0.1, H * 0.72, W * 0.8, color="#C9A86A", upper=True, spacing=2.0)]


def tpl_flyer(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    return [_ph(d, 0, 0, W, H * 0.6),
            shape_el(d, "rect", x=W * 0.07, y=H * 0.54, w=W * 0.86, h=H * 0.4, fill="#05070F", stroke_w=0.0,
                     radius=min(W, H) * 0.02),
            _t(d, "SESIONES DE FOTOS", "title", W * 0.1, H * 0.58, W * 0.8, color="#FFFFFF",
               size=round(min(W, H) * 0.075, 1)),
            _t(d, "Bodas · Comuniones · Deporte · Eventos", "body", W * 0.1, H * 0.70, W * 0.8, color="#F50D8A"),
            _t(d, "Reserva tu fecha: 600 000 000", "subtitle", W * 0.1, H * 0.80, W * 0.8, color="#FFFFFF")]


TEMPLATES = {"Post de evento": tpl_post_evento, "Invitación": tpl_invitacion, "Tarjeta de visita": tpl_tarjeta,
             "Collage de 3 fotos": tpl_collage, "Cita": tpl_cita, "Flyer de servicios": tpl_flyer}
# tamaño recomendado para cada plantilla (si el diseño está vacío se usa este)
TEMPLATE_SIZE = {"Post de evento": "Instagram vertical 4:5", "Invitación": "A5 (flyer)",
                 "Tarjeta de visita": "Tarjeta de visita", "Collage de 3 fotos": "Instagram cuadrado",
                 "Cita": "Instagram cuadrado", "Flyer de servicios": "A5 (flyer)"}


def apply_template(d: dict, name: str) -> None:
    """Sustituye los elementos por los de la plantilla, adaptada al tamaño del diseño."""
    d["elements"] = TEMPLATES[name](d)


# ------------------------------------------------------------------ guardar / abrir

def designs_dir(cfg_dir: str) -> str:
    p = os.path.join(cfg_dir, "diseños")
    os.makedirs(p, exist_ok=True)
    return p


def save(d: dict, folder: str) -> str:
    d["modified"] = time.time()
    path = os.path.join(folder, f"{d['id']}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or "elements" not in d or "w" not in d:
        raise ValueError("No es un diseño de PhootFlow")
    return d


def list_designs(folder: str) -> list[tuple[str, dict]]:
    out = []
    for n in os.listdir(folder) if os.path.isdir(folder) else []:
        if n.endswith(".json"):
            try:
                out.append((os.path.join(folder, n), load(os.path.join(folder, n))))
            except (OSError, ValueError, json.JSONDecodeError):
                pass
    return sorted(out, key=lambda t: -t[1].get("modified", 0))


# ------------------------------------------------------------------ fotos

def load_rgb(path: str, max_side: int = 0) -> np.ndarray | None:
    """Foto como RGB uint8 (los RAW se revelan con el balance de la cámara). Sin tocar el original."""
    from PIL import Image, ImageOps
    import engine as E
    try:
        if E.is_raw(path) and E.rawpy is not None:
            with E.rawpy.imread(path) as r:
                a = r.postprocess(use_camera_wb=True, output_bps=8, half_size=bool(max_side and max_side <= 3000))
            im = Image.fromarray(a)
        else:
            im = Image.open(path)
            if max_side:
                im.draft("RGB", (max_side, max_side))
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
        if max_side and max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        return np.asarray(im)
    except Exception:
        return None


def cover_box(iw: int, ih: int, ew: float, eh: float, zoom: float = 1.0, fx: float = 0.5, fy: float = 0.5):
    """Recorte de la foto (x, y, w, h en sus píxeles) que llena una caja ew × eh sin deformar."""
    ar = ew / max(eh, 1e-6)
    if iw / ih > ar:
        h = ih
        w = ih * ar
    else:
        w = iw
        h = iw / ar
    z = max(1.0, zoom)
    w, h = w / z, h / z
    x = (iw - w) * min(max(fx, 0.0), 1.0)
    y = (ih - h) * min(max(fy, 0.0), 1.0)
    return x, y, w, h


def crop_for(el: dict, rgb: np.ndarray) -> np.ndarray:
    """La parte de la foto que se ve en el elemento."""
    ih, iw = rgb.shape[:2]
    x, y, w, h = cover_box(iw, ih, el["w"], el["h"], el.get("zoom", 1.0), el.get("fx", 0.5), el.get("fy", 0.5))
    return rgb[int(y):int(round(y + h)), int(x):int(round(x + w))]


# ------------------------------------------------------------------ quitar fondo

BG_METHODS = {"auto": "Automático", "person": "Personas (IA)", "color": "Fondo liso / logo",
              "object": "Objeto (recorte inteligente)"}


def _border_stats(rgb: np.ndarray):
    h, w = rgb.shape[:2]
    b = max(1, min(h, w) // 40)
    ring = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3), rgb[:, :b].reshape(-1, 3),
                           rgb[:, -b:].reshape(-1, 3)]).astype(np.float32)
    lab = cv2.cvtColor(ring.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    return np.median(lab, 0), float(np.median(np.abs(lab - np.median(lab, 0)).sum(1)))


def _alpha_color(rgb: np.ndarray, tol: float) -> np.ndarray:
    """Fondo liso: lo que se parece al color del borde Y está conectado con el borde."""
    ref, _ = _border_stats(rgb)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    dist = np.sqrt(((lab - ref) ** 2).sum(2))
    cand = (dist < tol).astype(np.uint8)
    n, lab_cc = cv2.connectedComponents(cand, connectivity=4)
    h, w = cand.shape
    edge = np.unique(np.concatenate([lab_cc[0], lab_cc[-1], lab_cc[:, 0], lab_cc[:, -1]]))
    bg = np.isin(lab_cc, edge[edge > 0])
    # borde suave: transición según la distancia al color de fondo
    soft = np.clip((dist - tol * 0.6) / (tol * 0.8), 0, 1)
    alpha = np.where(bg, 0.0, 1.0).astype(np.float32)
    near = cv2.dilate(bg.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    alpha = np.where(near & ~bg, np.maximum(soft, 0.35), alpha)
    return alpha


def _alpha_person(rgb: np.ndarray) -> np.ndarray:
    import masks as M
    rgb01 = rgb.astype(np.float32) / 255
    probs = M.segment(rgb01)
    a = 1.0 - probs[..., 0]  # clase 0 = fondo
    return M._refine(a.astype(np.float32), rgb01)


def _alpha_object(rgb: np.ndarray) -> np.ndarray:
    """GrabCut (OpenCV): el objeto principal, suponiendo que no toca los bordes de la foto."""
    h0, w0 = rgb.shape[:2]
    s = min(1.0, 700 / max(h0, w0))
    small = cv2.resize(rgb, (max(8, round(w0 * s)), max(8, round(h0 * s))), interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    m = max(2, round(min(h, w) * 0.03))
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.setRNGSeed(0)
    cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), mask, (m, m, w - 2 * m, h - 2 * m), bgd, fgd, 5,
                cv2.GC_INIT_WITH_RECT)
    a = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1.0, 0.0).astype(np.float32)
    # quedarse con la mancha mayor y rellenar agujeros pequeños
    n, cc, st, _ = cv2.connectedComponentsWithStats((a > 0.5).astype(np.uint8))
    if n > 1:
        keep = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        a = (cc == keep).astype(np.float32)
    a = cv2.morphologyEx(a, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    a = cv2.resize(a, (w0, h0), interpolation=cv2.INTER_LINEAR)
    import masks as M
    return M._refine(a, rgb.astype(np.float32) / 255)


def remove_background(rgb: np.ndarray, method: str = "auto", tolerance: float = 18.0,
                      feather: float = 1.0) -> tuple[np.ndarray, str]:
    """Foto RGB(A) uint8 → RGBA con el fondo transparente, y el método usado.

    auto: fondo liso si el borde es de un color uniforme (logos, estudio); si no, personas con la IA (si hay
    alguien) y, si no, recorte de objeto con GrabCut.
    """
    a = np.ascontiguousarray(rgb[..., :3]).astype(np.uint8)
    used = method
    if method == "auto":
        _, spread = _border_stats(a)
        if spread < 6.0:
            used = "color"
        else:
            used = "object"
            try:
                al = _alpha_person(a)
                if (al > 0.5).mean() > 0.04:
                    used = "person"
                    alpha = al
            except Exception:
                pass
    if used == "color":
        alpha = _alpha_color(a, tolerance)
    elif used == "person" and method != "auto":
        alpha = _alpha_person(a)
    elif used == "object":
        alpha = _alpha_object(a)
    if feather > 0:
        alpha = cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), feather * max(a.shape[:2]) / 1500)
    if rgb.ndim == 3 and rgb.shape[2] == 4:
        alpha = alpha * (rgb[..., 3].astype(np.float32) / 255)
    out = np.dstack([a, np.clip(alpha * 255 + 0.5, 0, 255).astype(np.uint8)])
    return out, used


def cutout_path(cfg_dir: str, src: str, method: str) -> str:
    """Dónde guardar el recorte (PNG) de una foto: los originales no se tocan nunca."""
    import hashlib
    d = os.path.join(designs_dir(cfg_dir), "recortes")
    os.makedirs(d, exist_ok=True)
    h = hashlib.sha1(f"{os.path.normcase(os.path.abspath(src))}|{method}|{time.time()}".encode()).hexdigest()[:16]
    return os.path.join(d, f"{os.path.splitext(os.path.basename(src))[0]}_{h}.png")


def save_cutout(rgba: np.ndarray, path: str) -> str:
    from PIL import Image
    Image.fromarray(rgba, "RGBA").save(path)
    return path


# ------------------------------------------------------------------ vectorizado

def _smooth_closed(pts: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0 or len(pts) < 8:
        return pts
    r = int(max(1, round(sigma * 2)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    ext = np.concatenate([pts[-r:], pts, pts[:r]])
    out = np.stack([np.convolve(ext[:, i], k, mode="valid") for i in range(2)], 1)
    return out


def _path_d(poly: np.ndarray, curves: bool) -> str:
    """Contorno cerrado → «M … C … Z». Esquinas marcadas quedan rectas; el resto, Catmull-Rom → Bézier."""
    n = len(poly)
    if n < 3:
        return ""
    f = lambda p: f"{p[0]:.1f} {p[1]:.1f}"  # noqa: E731
    if not curves:
        return "M " + " L ".join(f(p) for p in poly) + " Z"
    prev, nxt = np.roll(poly, 1, 0), np.roll(poly, -1, 0)
    a, b = poly - prev, nxt - poly
    na, nb = np.linalg.norm(a, axis=1) + 1e-9, np.linalg.norm(b, axis=1) + 1e-9
    cosang = (a * b).sum(1) / (na * nb)
    smooth = cosang > 0.35  # giro menor de ~70°: curva; más cerrado: esquina
    tang = (nxt - prev) / 6.0
    parts = ["M " + f(poly[0])]
    for i in range(n):
        j = (i + 1) % n
        c1 = poly[i] + (tang[i] if smooth[i] else 0)
        c2 = poly[j] - (tang[j] if smooth[j] else 0)
        if not smooth[i] and not smooth[j]:
            parts.append("L " + f(poly[j]))
        else:
            parts.append(f"C {f(c1)} {f(c2)} {f(poly[j])}")
    return " ".join(parts) + " Z"


def _mask_path(mask: np.ndarray, detail: float, curves: bool) -> str:
    h, w = mask.shape
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    min_area = (0.00002 + (1 - detail) * 0.0006) * w * h
    eps = 0.35 + (1 - detail) * 2.2
    sigma = 0.6 + (1 - detail) * 1.6
    out = []
    for c in cs:
        if abs(cv2.contourArea(c)) < min_area:
            continue
        p = _smooth_closed(c[:, 0, :].astype(np.float32), sigma)
        p = cv2.approxPolyDP(p.astype(np.float32).reshape(-1, 1, 2), eps, True)[:, 0, :]
        if len(p) >= 3:
            out.append(_path_d(p.astype(np.float64), curves))
    return " ".join(s for s in out if s)


def _hex(c) -> str:
    return "#%02X%02X%02X" % tuple(int(round(float(v))) for v in c[:3])


def vectorize(rgb: np.ndarray, mode: str = "color", colors: int = 6, detail: int = 60,
              drop_bg: bool = False, curves: bool = True, max_side: int = 1000, invert: bool = False) -> dict:
    """Mapa de bits → trazados vectoriales.

    mode «bw»: tinta negra sobre lo que se parezca al fondo (umbral de Otsu; se decide qué es tinta mirando los
    bordes). mode «color»: k-medias en Lab con `colors` tintas. Las capas se apilan acumuladas (cada color cubre
    también las capas que van encima), así no quedan rendijas entre colores al imprimir o cortar.
    Devuelve {"viewbox": [w, h], "paths": [{"fill": "#RRGGBB", "d": "..."}]} de abajo arriba.
    """
    a = np.ascontiguousarray(rgb[..., :3]).astype(np.uint8)
    alpha = rgb[..., 3] if rgb.ndim == 3 and rgb.shape[2] == 4 else None
    h0, w0 = a.shape[:2]
    s = min(1.0, max_side / max(h0, w0))
    if s < 1:
        a = cv2.resize(a, (max(1, round(w0 * s)), max(1, round(h0 * s))), interpolation=cv2.INTER_AREA)
        if alpha is not None:
            alpha = cv2.resize(alpha, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    h, w = a.shape[:2]
    det = min(max(detail, 0), 100) / 100.0
    opaque = alpha > 127 if alpha is not None else np.ones((h, w), bool)
    border = np.zeros((h, w), bool)
    bw_ = max(1, min(h, w) // 50)
    border[:bw_], border[-bw_:], border[:, :bw_], border[:, -bw_:] = True, True, True, True

    layers: list[tuple[str, np.ndarray]] = []  # (color, máscara) de abajo arriba, sin acumular
    if mode == "bw":
        g = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
        g = cv2.GaussianBlur(g, (0, 0), 0.6 + (1 - det) * 1.2)
        _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        dark = th == 0
        ink = dark if dark[border & opaque].mean() < 0.5 else ~dark
        if invert:
            ink = ~ink
        ink &= opaque
        if not drop_bg:
            layers.append(("#FFFFFF", np.ones((h, w), bool)))
        layers.append(("#000000", ink))
    else:
        k = int(min(max(colors, 2), 24))
        src = cv2.bilateralFilter(a, 7, 40 + (1 - det) * 60, 7)
        lab = cv2.cvtColor(src, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
        sel = opaque.reshape(-1)
        samp = lab[sel]
        rng = np.random.default_rng(0)
        if len(samp) > 60000:
            samp = samp[rng.choice(len(samp), 60000, replace=False)]
        k = min(k, max(1, len(np.unique(samp[:5000], axis=0))))
        cv2.setRNGSeed(0)
        _, _, centers = cv2.kmeans(samp, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5),
                                   3, cv2.KMEANS_PP_CENTERS)
        d2 = ((lab[:, None, :] - centers[None, :, :]) ** 2).sum(2)
        labels = d2.argmin(1).astype(np.uint8).reshape(h, w)
        labels = cv2.medianBlur(labels, 3 if det > 0.5 else 5)
        rgbc = cv2.cvtColor(centers.reshape(1, -1, 3).astype(np.uint8), cv2.COLOR_LAB2RGB)[0]
        areas = np.bincount(labels[opaque].ravel(), minlength=k)
        bvals = labels[border & opaque]
        bg = int(np.bincount(bvals, minlength=k).argmax()) if bvals.size else int(areas.argmax())
        order = [bg] + [int(i) for i in np.argsort(-areas) if int(i) != bg and areas[i] > 0]
        for i in order:
            if drop_bg and i == bg:
                continue
            layers.append((_hex(rgbc[i]), (labels == i) & opaque))
    # acumular: cada capa incluye las de encima (sin rendijas); la primera capa sin hueco = rectángulo completo
    paths = []
    acc = np.zeros((h, w), bool)
    cum = []
    for col, m in reversed(layers):
        acc = acc | m
        cum.append((col, acc.copy()))
    cum.reverse()
    for col, m in cum:
        if not m.any():
            continue
        if m.all():
            dpath = f"M 0 0 L {w} 0 L {w} {h} L 0 {h} Z"
        else:
            dpath = _mask_path(m, det, curves)
        if dpath:
            paths.append({"fill": col, "d": dpath})
    return {"viewbox": [w, h], "paths": paths}


def vector_svg(vec: dict, width: str = "", height: str = "") -> str:
    """SVG independiente de un vectorizado (para plotter, vinilo, imprenta…)."""
    vw, vh = vec["viewbox"]
    size = f' width="{width}" height="{height}"' if width else ""
    body = "\n".join(f'  <path fill="{p["fill"]}" fill-rule="evenodd" d="{p["d"]}"/>' for p in vec["paths"])
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg"{size} '
            f'viewBox="0 0 {vw} {vh}">\n{body}\n</svg>\n')


_TOK = re.compile(r"[MLCZ]|-?\d+(?:\.\d+)?")


def parse_path(d: str):
    """«M x y C … L … Z» (lo que escribe `vectorize`) → QPainterPath con relleno par-impar."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainterPath
    p = QPainterPath()
    p.setFillRule(Qt.OddEvenFill)
    toks = _TOK.findall(d)
    i, cmd = 0, "M"
    while i < len(toks):
        t = toks[i]
        if t in "MLCZ":
            cmd = t
            i += 1
            if t == "Z":
                p.closeSubpath()
            continue
        n = 6 if cmd == "C" else 2
        v = [float(x) for x in toks[i:i + n]]
        if len(v) < n:
            break
        if cmd == "M":
            p.moveTo(v[0], v[1])
            cmd = "L"
        elif cmd == "L":
            p.lineTo(v[0], v[1])
        else:
            p.cubicTo(v[0], v[1], v[2], v[3], v[4], v[5])
        i += n
    return p


# ------------------------------------------------------------------ trazados editables (tipo Illustrator)
# Un nodo es {"p": [x, y], "i": [x, y], "o": [x, y]}: el punto de anclaje y sus manecillas de entrada y salida
# (iguales a "p" si es una esquina). Un subtrazado es {"nodes": [...], "closed": bool}.

def nodes_to_d(subs: list[dict]) -> str:
    f = lambda q: f"{q[0]:.2f} {q[1]:.2f}"  # noqa: E731
    out = []
    for s in subs:
        ns = s["nodes"]
        if not ns:
            continue
        parts = ["M " + f(ns[0]["p"])]
        segs = list(zip(ns, ns[1:])) + ([(ns[-1], ns[0])] if s.get("closed") and len(ns) > 1 else [])
        for a, b in segs:
            if a["o"] == a["p"] and b["i"] == b["p"]:
                parts.append("L " + f(b["p"]))
            else:
                parts.append(f"C {f(a['o'])} {f(b['i'])} {f(b['p'])}")
        if s.get("closed"):
            parts.append("Z")
        out.append(" ".join(parts))
    return " ".join(out)


def d_to_nodes(d: str) -> list[dict]:
    toks = _TOK.findall(d)
    subs, cur, i, cmd = [], None, 0, "M"
    while i < len(toks):
        t = toks[i]
        if t in "MLCZ":
            cmd = t
            i += 1
            if t == "Z" and cur:
                cur["closed"] = True
                ns = cur["nodes"]
                if len(ns) > 1 and abs(ns[0]["p"][0] - ns[-1]["p"][0]) < 1e-6 and \
                        abs(ns[0]["p"][1] - ns[-1]["p"][1]) < 1e-6:
                    ns[0]["i"] = ns[-1]["i"]
                    ns.pop()
            continue
        n = 6 if cmd == "C" else 2
        v = [float(x) for x in toks[i:i + n]]
        if len(v) < n:
            break
        i += n
        if cmd == "M":
            cur = {"nodes": [{"p": v[:2], "i": v[:2], "o": v[:2]}], "closed": False}
            subs.append(cur)
            cmd = "L"
        elif cmd == "L" and cur:
            cur["nodes"].append({"p": v[:2], "i": v[:2], "o": v[:2]})
        elif cur:
            cur["nodes"][-1]["o"] = v[:2]
            cur["nodes"].append({"p": v[4:6], "i": v[2:4], "o": v[4:6]})
    return subs


def qpath_to_d(path) -> str:
    """QPainterPath → «M L C Z» (para guardar el resultado del Buscatrazos)."""
    from PySide6.QtGui import QPainterPath
    out, i, n, start = [], 0, path.elementCount(), None
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.ElementType.MoveToElement:
            if start is not None:
                out.append("Z")
            out.append(f"M {e.x:.2f} {e.y:.2f}")
            start = (e.x, e.y)
            i += 1
        elif e.type == QPainterPath.ElementType.LineToElement:
            out.append(f"L {e.x:.2f} {e.y:.2f}")
            i += 1
        else:
            c1, c2, pt = path.elementAt(i), path.elementAt(i + 1), path.elementAt(i + 2)
            out.append(f"C {c1.x:.2f} {c1.y:.2f} {c2.x:.2f} {c2.y:.2f} {pt.x:.2f} {pt.y:.2f}")
            i += 3
    if start is not None:
        out.append("Z")
    return " ".join(out)


def path_el(d: dict, subs: list[dict], fill: str = "#F50D8A", stroke: str = "#05070F", stroke_w: float = 0.0,
            closed_fill: bool = True) -> dict | None:
    """Trazado de la pluma (en coordenadas del diseño) → elemento vector con caja ajustada."""
    pts = [q for s in subs for nd in s["nodes"] for q in (nd["p"], nd["i"], nd["o"])]
    if not pts:
        return None
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    x0, y0 = min(xs), min(ys)
    w, h = max(1.0, max(xs) - x0), max(1.0, max(ys) - y0)
    loc = [{"closed": s.get("closed", False), "nodes": [{k: [nd[k][0] - x0, nd[k][1] - y0] for k in "pio"}
                                                         for nd in s["nodes"]]} for s in subs]
    anyclosed = any(s.get("closed") for s in subs)
    sp = {"fill": fill if (anyclosed and closed_fill) else "#00000000", "d": nodes_to_d(loc),
          "stroke": stroke, "stroke_w": stroke_w if (stroke_w or not anyclosed) else 0.0}
    if not anyclosed and not sp["stroke_w"]:
        sp["stroke_w"] = max(2.0, min(d["w"], d["h"]) * 0.004)
    return base_el("vector", x0, y0, w, h, viewbox=[w, h], paths=[sp])


def element_qpath(el: dict):
    """Silueta del elemento en coordenadas del diseño (para el Buscatrazos y las máscaras)."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath, QTransform
    if el["type"] == "vector":
        vw, vh = el.get("viewbox", [1, 1])
        p = QPainterPath()
        p.setFillRule(Qt_OddEven())
        for sp in el.get("paths", []):
            if (sp.get("fill") or "").endswith("00") and len(sp.get("fill", "")) == 9:
                continue
            p.addPath(_qpath(sp["d"]))
        p = QTransform.fromScale(el["w"] / max(vw, 1e-6), el["h"] / max(vh, 1e-6)).map(p)
    elif el["type"] == "shape":
        p = _shape_path(el)
    elif el["type"] == "text":
        p = text_outline(el)
    else:
        p = QPainterPath()
        p.addRect(QRectF(0, 0, el["w"], el["h"]))
    return element_transform(el).map(p)


def text_outline(el: dict):
    """Texto → contornos (como «Crear contornos» de Illustrator): la imprenta no necesita la fuente."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainterPath, QTransform
    p = QPainterPath()
    p.setFillRule(Qt.WindingFill)
    lays, _ = _text_layout(el)
    for lay in lays:
        for run in lay.glyphRuns():
            raw = run.rawFont()
            for gi, pos in zip(run.glyphIndexes(), run.positions()):
                p.addPath(QTransform.fromTranslate(pos.x(), pos.y()).map(raw.pathForGlyph(gi)))
    return p


def outline_text_el(el: dict) -> dict:
    """Elemento de texto → vector con las mismas medidas y color."""
    from PySide6.QtCore import Qt
    path = text_outline(el)
    path.setFillRule(Qt.WindingFill)
    path = path.simplified()  # resuelve solapes de letras (OddEven no los rellenaría bien)
    d_ = qpath_to_d(path)
    paths = []
    ow = float(el.get("outline_w", 0.0))
    sh = el.get("shadow")
    if sh:  # sombra y contorno se conservan, en el mismo orden en que se pintan
        from PySide6.QtGui import QTransform
        sd = qpath_to_d(QTransform.fromTranslate(sh.get("dx", 0.0), sh.get("dy", 0.0)).map(path))
        paths.append({"fill": sh.get("color", "#00000080"), "d": sd, "stroke": sh.get("color", "#00000080"),
                      "stroke_w": ow})
    if ow > 0:
        paths.append({"fill": "#00000000", "d": d_, "stroke": el.get("outline", "#000000"), "stroke_w": ow})
    paths.append({"fill": el.get("color", "#000000"), "d": d_})
    v = base_el("vector", el["x"], el["y"], el["w"], el["h"], viewbox=[el["w"], el["h"]], rot=el.get("rot", 0.0),
                opacity=el.get("opacity", 1.0), paths=paths)
    v["name"] = "Contornos: " + el.get("text", "").strip().split("\n")[0][:20]
    return v


def Qt_OddEven():
    from PySide6.QtCore import Qt
    return Qt.OddEvenFill


PATHFINDER = {"union": "Unir", "subtract": "Restar delantero", "intersect": "Intersecar", "exclude": "Excluir"}


def pathfinder(els: list[dict], op: str) -> dict | None:
    """Buscatrazos: combina las siluetas (de abajo arriba) en un único vector con el color del de abajo."""
    if len(els) < 2:
        return None
    paths = [element_qpath(e).simplified() for e in els]
    res = paths[0]
    for q in paths[1:]:
        if op == "union":
            res = res.united(q)
        elif op == "subtract":
            res = res.subtracted(q)
        elif op == "intersect":
            res = res.intersected(q)
        else:
            res = res.united(q).subtracted(res.intersected(q))
    r = res.boundingRect()
    if r.isEmpty():
        return None
    from PySide6.QtGui import QTransform
    res = QTransform.fromTranslate(-r.x(), -r.y()).map(res)
    base = els[0]
    fill = base.get("fill") if base["type"] == "shape" else \
        (base.get("paths") or [{}])[0].get("fill", "#05070F") if base["type"] == "vector" else "#05070F"
    return base_el("vector", r.x(), r.y(), r.width(), r.height(), viewbox=[r.width(), r.height()],
                   paths=[{"fill": fill or "#05070F", "d": qpath_to_d(res)}], grad=base.get("grad"))


# ------------------------------------------------------------------ pintado (lienzo y exportación)

class ImageCache:
    """Fotos ya cargadas como QImage, por (ruta, lado máximo, B/N)."""

    def __init__(self, limit: int = 40):
        from collections import OrderedDict
        self.d = OrderedDict()
        self.limit = limit
        self.sizes = {}

    def get(self, path: str, max_side: int, bw: bool = False, look: str = ""):
        from PySide6.QtGui import QImage
        key = (path, max_side, bw, look)
        if key in self.d:
            self.d.move_to_end(key)
            return self.d[key]
        a = load_rgb(path, max_side)
        if a is None:
            return None
        if look and look != "Natural":
            import engine as E
            rgb = E.apply_look(a[..., :3].astype(np.float32) / 255, look)
            rgb = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)
            a = np.concatenate([rgb, a[..., 3:]], 2) if a.shape[2] == 4 else rgb
        if bw:
            g = cv2.cvtColor(a[..., :3], cv2.COLOR_RGB2GRAY)
            g = np.stack([g] * 3, 2)
            a = np.concatenate([g, a[..., 3:]], 2) if a.shape[2] == 4 else g
        a = np.ascontiguousarray(a)
        hh, ww, ch = a.shape
        fmt = QImage.Format_RGBA8888 if ch == 4 else QImage.Format_RGB888
        img = QImage(a.data, ww, hh, ch * ww, fmt).copy()
        self.d[key] = img
        while len(self.d) > self.limit:
            self.d.popitem(last=False)
        return img

    def full_size(self, path: str) -> tuple[int, int]:
        if path not in self.sizes:
            try:
                import engine as E
                self.sizes[path] = E.full_size(path)
            except Exception:
                a = load_rgb(path)
                self.sizes[path] = (a.shape[1], a.shape[0]) if a is not None else (0, 0)
        return self.sizes[path]


_PATHS: dict = {}


def _qpath(d: str):
    p = _PATHS.get(d)
    if p is None:
        if len(_PATHS) > 400:
            _PATHS.clear()
        p = _PATHS[d] = parse_path(d)
    return p


def qcolor(s: str):
    """#RRGGBB o #RRGGBBAA → QColor."""
    from PySide6.QtGui import QColor
    s = (s or "#000000").strip()
    if len(s) == 9:
        c = QColor(s[:7])
        c.setAlpha(int(s[7:9], 16))
        return c
    return QColor(s)


def text_font(el: dict, scale: float = 1.0):
    from PySide6.QtGui import QFont
    f = QFont(el.get("font", "Segoe UI"))
    f.setPixelSize(max(1, int(round(el.get("size", 24) * scale))))
    f.setBold(bool(el.get("bold")))
    f.setItalic(bool(el.get("italic")))
    if el.get("spacing"):
        f.setLetterSpacing(QFont.AbsoluteSpacing, el["spacing"] * el.get("size", 24) * scale / 20)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def _text_value(el):
    t = el.get("text", "")
    return t.upper() if el.get("upper") else t


def _align_flag(el):
    from PySide6.QtCore import Qt
    return {"left": Qt.AlignLeft, "right": Qt.AlignRight, "justify": Qt.AlignJustify}.get(el.get("align"),
                                                                                         Qt.AlignHCenter)


def _text_layout(el, scale=1.0):
    """Maquetado del texto con interlineado; devuelve (QTextLayout por párrafo, altura total)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QFontMetricsF, QTextLayout, QTextOption
    font = text_font(el, scale)
    fm = QFontMetricsF(font)
    width = max(1.0, el["w"] * scale)
    lh = fm.height() * el.get("line", 1.15)
    opt = QTextOption(_align_flag(el))
    opt.setWrapMode(QTextOption.WordWrap)
    lays, y = [], 0.0
    for para in _text_value(el).split("\n"):
        lay = QTextLayout(para, font)
        lay.setTextOption(opt)
        lay.beginLayout()
        while True:
            ln = lay.createLine()
            if not ln.isValid():
                break
            ln.setLineWidth(width)
            ln.setPosition(QPointF(0, y + (lh - fm.height()) / 2))
            y += lh
        lay.endLayout()
        lays.append(lay)
    return lays, y


def text_height(el: dict) -> float:
    try:
        return max(_text_layout(el)[1], el.get("size", 24) * 0.5)
    except Exception:
        return el.get("size", 24) * el.get("line", 1.15) * max(1, el.get("text", "").count("\n") + 1)


def _shape_path(el):
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QPainterPath, QPolygonF
    w, h = el["w"], el["h"]
    p = QPainterPath()
    k = el.get("shape", "rect")
    if k == "ellipse":
        p.addEllipse(QRectF(0, 0, w, h))
    elif k == "triangle":
        p.addPolygon(QPolygonF([QPointF(w / 2, 0), QPointF(w, h), QPointF(0, h)]))
        p.closeSubpath()
    elif k == "star":
        pts = []
        for i in range(10):
            r = 0.5 if i % 2 == 0 else 0.2
            a = -math.pi / 2 + i * math.pi / 5
            pts.append(QPointF(w / 2 + math.cos(a) * r * w, h / 2 + math.sin(a) * r * h * 1.05 + h * 0.03))
        p.addPolygon(QPolygonF(pts))
        p.closeSubpath()
    elif k == "line":
        p.moveTo(0, h / 2)
        p.lineTo(w, h / 2)
    else:
        r = min(el.get("radius", 0.0), w / 2, h / 2)
        p.addRoundedRect(QRectF(0, 0, w, h), r, r)
    return p


def make_brush(fill, grad, rect):
    """Relleno plano o degradado lineal {"c1", "c2", "angle"} (como en Illustrator/Canva)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QBrush, QLinearGradient
    if grad and grad.get("c1") and grad.get("c2"):
        a = math.radians(grad.get("angle", 90.0))
        c = rect.center()
        r = (abs(math.cos(a)) * rect.width() + abs(math.sin(a)) * rect.height()) / 2
        g = QLinearGradient(QPointF(c.x() - math.cos(a) * r, c.y() - math.sin(a) * r),
                            QPointF(c.x() + math.cos(a) * r, c.y() + math.sin(a) * r))
        g.setColorAt(0, qcolor(grad["c1"]))
        g.setColorAt(1, qcolor(grad["c2"]))
        return QBrush(g)
    return QBrush(qcolor(fill or "#00000000"))


def paint_element(p, el: dict, cache: ImageCache, editing: bool = False, img_px: float = 1.0,
                  max_img: int = 1600) -> None:
    """Dibuja un elemento en sus coordenadas locales (0, 0, w, h); quien llama pone la posición y el giro.
    `img_px` = píxeles de salida por píxel de diseño (para pedir las fotos a la resolución justa)."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPainterPath, QPen
    w, h = el["w"], el["h"]
    if el.get("hidden") or w <= 0 or h <= 0:
        return
    p.save()
    p.setOpacity(p.opacity() * float(el.get("opacity", 1.0)))
    t = el["type"]
    if t == "shape":
        path = _shape_path(el)
        sw = float(el.get("stroke_w", 0.0))
        if el.get("shape") == "line":
            pen = QPen(qcolor(el.get("stroke") if sw else el.get("fill")), max(sw, 1.0))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
        else:
            p.setPen(QPen(qcolor(el.get("stroke")), sw, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin) if sw > 0
                     else Qt.NoPen)
            p.setBrush(make_brush(el.get("fill"), el.get("grad"), QRectF(0, 0, w, h)))
        p.drawPath(path)
    elif t == "text":
        lays, _ = _text_layout(el)
        sh = el.get("shadow")
        ow = float(el.get("outline_w", 0.0))
        if sh or ow > 0:
            path = text_outline(el)
            if sh:
                # sombra dura y desplazada (vectorial: se imprime igual que se ve)
                p.save()
                p.translate(sh.get("dx", 0.0), sh.get("dy", 0.0))
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(sh.get("color", "#00000080")))
                p.drawPath(path)
                if ow > 0:
                    p.strokePath(path, QPen(qcolor(sh.get("color", "#00000080")), ow, Qt.SolidLine, Qt.RoundCap,
                                            Qt.RoundJoin))
                p.restore()
            if ow > 0:
                p.strokePath(path, QPen(qcolor(el.get("outline", "#000000")), ow, Qt.SolidLine, Qt.RoundCap,
                                        Qt.RoundJoin))
        p.setPen(qcolor(el.get("color")))
        for lay in lays:
            lay.draw(p, QPointF(0, 0))
    elif t == "image":
        if el.get("clip") and el["clip"] != "rect":
            clip = _shape_path({"shape": el["clip"], "w": w, "h": h})
        else:
            clip = QPainterPath()
            r = min(el.get("radius", 0.0), w / 2, h / 2)
            clip.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.setClipPath(clip, Qt.IntersectClip)
        img = None
        if el.get("path") and os.path.exists(el["path"]):
            want = int(min(max_img, max(64, max(w, h) * img_px * max(1.0, el.get("zoom", 1.0)) * 1.15)))
            # pedir por escalones para reutilizar la caché
            step = 1 << max(6, int(math.ceil(math.log2(want))))
            img = cache.get(el["path"], min(step, max_img), bool(el.get("bw")), el.get("look", ""))
        if img is not None and not img.isNull():
            x, y, cw, ch = cover_box(img.width(), img.height(), w, h, el.get("zoom", 1.0), el.get("fx", 0.5),
                                     el.get("fy", 0.5))
            p.setRenderHint(p.RenderHint.SmoothPixmapTransform, True)
            p.drawImage(QRectF(0, 0, w, h), img, QRectF(x, y, cw, ch))
        elif editing:
            p.fillRect(QRectF(0, 0, w, h), QColor(200, 200, 205))
            pen = QPen(QColor(150, 150, 160), max(w, h) / 300)
            p.setPen(pen)
            p.drawLine(QPointF(0, 0), QPointF(w, h))
            p.drawLine(QPointF(w, 0), QPointF(0, h))
            f = QFont("Segoe UI")
            f.setPixelSize(max(8, int(min(w, h) * 0.08)))
            p.setFont(f)
            p.setPen(QColor(80, 80, 90))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter | Qt.TextWordWrap,
                       "Arrastra aquí una foto" if el.get("path", "") == "" else "Foto no encontrada")
    elif t == "vector":
        vw, vh = el.get("viewbox", [1, 1])
        p.scale(w / max(vw, 1e-6), h / max(vh, 1e-6))
        for sp in el.get("paths", []):
            path = _qpath(sp["d"])
            p.setBrush(make_brush(sp.get("fill"), el.get("grad") if len(el.get("paths", [])) == 1 else None,
                                  QRectF(0, 0, vw, vh)))
            sw = float(sp.get("stroke_w", 0.0))
            if sw > 0:
                # grosor en unidades del viewbox: escala con el elemento
                pen = QPen(qcolor(sp.get("stroke", "#000000")), sw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                pen.setCosmetic(False)
                p.setPen(pen)
            else:
                p.setPen(Qt.NoPen)
            p.drawPath(path)
    p.restore()


def element_transform(el: dict):
    from PySide6.QtGui import QTransform
    t = QTransform()
    t.translate(el["x"] + el["w"] / 2, el["y"] + el["h"] / 2)
    t.rotate(el.get("rot", 0.0))
    t.translate(-el["w"] / 2, -el["h"] / 2)
    return t


def paint_design(p, d: dict, cache: ImageCache, img_px: float = 1.0, bleed: float = 0.0,
                 max_img: int = 8000, elements: list | None = None) -> None:
    """Fondo (que llena también el sangrado) y elementos, en coordenadas de diseño."""
    from PySide6.QtCore import QRectF
    p.fillRect(QRectF(-bleed, -bleed, d["w"] + 2 * bleed, d["h"] + 2 * bleed), qcolor(d.get("bg", "#FFFFFF")))
    for el in elements if elements is not None else d["elements"]:
        p.save()
        p.setTransform(element_transform(el), True)
        paint_element(p, el, cache, editing=False, img_px=img_px, max_img=max_img)
        p.restore()


def bleed_elements(d: dict, bleed: float) -> list[dict]:
    """Los elementos que tocan el borde se alargan hasta el sangrado (como hace una imprenta con un fondo a sangre)."""
    out = []
    tol = max(1.0, min(d["w"], d["h"]) * 0.002)
    for el in d["elements"]:
        e = copy.deepcopy(el)
        if el["type"] in ("image", "shape", "vector") and abs(el.get("rot", 0.0)) < 0.01 \
                and el.get("shape") != "line" and not (el["type"] == "shape" and el.get("radius", 0) > 0):
            x0, y0, x1, y1 = el["x"], el["y"], el["x"] + el["w"], el["y"] + el["h"]
            if abs(x0) <= tol:
                x0 = -bleed
            if abs(y0) <= tol:
                y0 = -bleed
            if abs(x1 - d["w"]) <= tol:
                x1 = d["w"] + bleed
            if abs(y1 - d["h"]) <= tol:
                y1 = d["h"] + bleed
            if el["type"] == "vector" and (x1 - x0, y1 - y0) != (el["w"], el["h"]):
                pass  # un vector no se estira: deformaría el dibujo
            else:
                e.update(x=x0, y=y0, w=x1 - x0, h=y1 - y0)
        out.append(e)
    return out


def image_warnings(d: dict, cache: ImageCache, dpi_min: float = 200) -> list[str]:
    """Fotos que se imprimirían por debajo de `dpi_min` ppp."""
    out = []
    for el in d["elements"]:
        if el["type"] != "image" or not el.get("path") or not os.path.exists(el["path"]):
            continue
        iw, ih = cache.full_size(el["path"])
        if not iw:
            continue
        x, y, cw, ch = cover_box(iw, ih, el["w"], el["h"], el.get("zoom", 1.0))
        inches = px_to_mm(el["w"], d.get("dpi", PRINT_DPI)) / MM_PER_IN
        dpi = cw / max(inches, 1e-6)
        if dpi < dpi_min:
            out.append(f"{os.path.basename(el['path'])}: {dpi:.0f} ppp")
    return out


def export_raster(d: dict, path: str, cache: ImageCache, scale: float = 1.0, quality: int = 92) -> str:
    """PNG o JPG (por la extensión) a `scale` veces el tamaño del diseño."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    W, H = max(1, round(d["w"] * scale)), max(1, round(d["h"] * scale))
    jpg = path.lower().endswith((".jpg", ".jpeg"))
    img = QImage(W, H, QImage.Format_RGB32 if jpg else QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.white if jpg else Qt.transparent)
    dpm = round(d.get("dpi", SCREEN_DPI) * scale / MM_PER_IN * 1000)
    img.setDotsPerMeterX(dpm)
    img.setDotsPerMeterY(dpm)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    p.scale(scale, scale)
    paint_design(p, d, cache, img_px=scale)
    p.end()
    if not img.save(path, "JPG" if jpg else "PNG", quality if jpg else -1):
        raise OSError(f"No se pudo guardar {path}")
    return path


def export_svg(d: dict, path: str, cache: ImageCache) -> str:
    from PySide6.QtCore import QRect, QRectF, QSize
    from PySide6.QtGui import QPainter
    from PySide6.QtSvg import QSvgGenerator
    g = QSvgGenerator()
    g.setFileName(path)
    g.setSize(QSize(round(d["w"]), round(d["h"])))
    g.setViewBox(QRect(0, 0, round(d["w"]), round(d["h"])))
    g.setResolution(round(d.get("dpi", SCREEN_DPI)))
    g.setTitle(d.get("name", ""))
    g.setDescription("PhootFlow Graphics")
    p = QPainter(g)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    p.setClipRect(QRectF(0, 0, d["w"], d["h"]))
    paint_design(p, d, cache, img_px=1.0, max_img=2400)
    p.end()
    return path


def export_pdf(d: dict, path: str, cache: ImageCache, bleed_mm: float = 3.0, marks: bool = True,
               cmyk: bool = False, res: int = PRINT_DPI) -> str:
    """PDF de imprenta: vectorial (texto, formas y vectorizados), fotos a `res` ppp, sangrado y marcas de corte.

    CMYK: Qt convierte los colores con la fórmula simple (sin perfil ICC de la imprenta): sirve para imprentas que
    piden CMYK, pero el color exacto depende de su perfil.
    """
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
    bleed = max(0.0, bleed_mm)
    pw, ph, _ = print_layout(d, bleed, marks)  # las marcas empiezan a 3 mm del corte (o al sangrado)
    w = QPdfWriter(path)
    w.setResolution(res)
    w.setCreator("PhootFlow Graphics")
    w.setTitle(d.get("name", ""))
    if cmyk and hasattr(w, "setColorModel"):
        w.setColorModel(QPdfWriter.ColorModel.CMYK)
    w.setPageLayout(QPageLayout(QPageSize(QSizeF(pw, ph), QPageSize.Millimeter, "", QPageSize.ExactMatch),
                                QPageLayout.Portrait, QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter))
    p = QPainter(w)
    _paint_print(p, d, cache, bleed, marks, cmyk, res)
    p.end()
    return path


def print_layout(d: dict, bleed_mm: float, marks: bool) -> tuple[float, float, float]:
    """(ancho, alto, margen) de la hoja del PDF en mm: corte + sangrado + zona de marcas."""
    dpi = d.get("dpi", PRINT_DPI)
    bleed = max(0.0, bleed_mm)
    margin = bleed + (8.0 if marks else 0.0)
    return px_to_mm(d["w"], dpi) + 2 * margin, px_to_mm(d["h"], dpi) + 2 * margin, margin


def print_proof(d: dict, cache: ImageCache, bleed_mm: float = 3.0, marks: bool = True, cmyk: bool = False,
                res: int = 100):
    """La hoja del PDF de imprenta como imagen (prueba en pantalla): mismo pintor, otra resolución."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    pw, ph, _ = print_layout(d, bleed_mm, marks)
    img = QImage(round(pw / MM_PER_IN * res), round(ph / MM_PER_IN * res), QImage.Format_RGB32)
    img.fill(Qt.white)
    p = QPainter(img)
    _paint_print(p, d, cache, max(0.0, bleed_mm), marks, cmyk, res)
    p.end()
    return img


def _paint_print(p, d, cache, bleed, marks, cmyk, res):
    """Pinta la hoja de imprenta en un dispositivo de `res` puntos por pulgada."""
    from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen
    dpi = d.get("dpi", PRINT_DPI)
    tw, th = px_to_mm(d["w"], dpi), px_to_mm(d["h"], dpi)
    pw, ph, margin = print_layout(d, bleed, marks)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    dev_per_mm = res / MM_PER_IN
    k = res / dpi  # píxeles del PDF por píxel del diseño
    p.save()
    p.translate(margin * dev_per_mm, margin * dev_per_mm)
    p.scale(k, k)
    bpx = mm_to_px(bleed, dpi)
    p.setClipRect(QRectF(-bpx, -bpx, d["w"] + 2 * bpx, d["h"] + 2 * bpx))
    paint_design(p, d, cache, img_px=k, bleed=bpx, max_img=12000,
                 elements=bleed_elements(d, bpx) if bleed > 0 else None)
    p.restore()
    if marks:
        pen = QPen(QColor(0, 0, 0), 0.25 / 72 * res)  # 0,25 pt de registro
        p.setPen(pen)
        off = max(bleed, 3.0)
        ln = 5.0
        for cx in (margin, margin + tw):
            for cy in (margin, margin + th):
                sx = -1 if cx == margin else 1
                sy = -1 if cy == margin else 1
                # marca horizontal y vertical en cada esquina, fuera del sangrado
                p.drawLine(QLineF(QPointF((cx + sx * off) * dev_per_mm, cy * dev_per_mm),
                                  QPointF((cx + sx * (off + ln)) * dev_per_mm, cy * dev_per_mm)))
                p.drawLine(QLineF(QPointF(cx * dev_per_mm, (cy + sy * off) * dev_per_mm),
                                  QPointF(cx * dev_per_mm, (cy + sy * (off + ln)) * dev_per_mm)))
        f = p.font()
        f.setPixelSize(int(2.0 * dev_per_mm))
        p.setFont(f)
        info = f"{d.get('name', '')} · {tw:.0f}×{th:.0f} mm · sangrado {bleed:g} mm · {'CMYK' if cmyk else 'RGB'}"
        p.drawText(QRectF((margin + 6) * dev_per_mm, 0.5 * dev_per_mm, (pw - 2 * margin) * dev_per_mm,
                          (margin - bleed - 1) * dev_per_mm), Qt.AlignLeft | Qt.AlignVCenter, info)


def thumbnail(d: dict, cache: ImageCache, side: int = 240):
    """QImage pequeña para «Mis diseños»."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    s = side / max(d["w"], d["h"])
    img = QImage(max(1, round(d["w"] * s)), max(1, round(d["h"] * s)), QImage.Format_RGB32)
    img.fill(Qt.white)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    p.scale(s, s)
    paint_design(p, d, cache, img_px=s, max_img=512)
    p.end()
    return img
