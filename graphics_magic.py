"""Graphics: variantes de un diseño y diseños hechos con las fotos de la sesión (sin interfaz).

- Estilos (`STYLES`, `restyle`): cambia la paleta y las fuentes de todo el diseño conservando el contraste. Cada
  color se clasifica por su papel (oscuro, claro o acento) y se lleva al del estilo, manteniendo el orden de
  claridad entre los oscuros y entre los claros. `mirror` da la vuelta a la disposición (variante de composición).
- Fotos de la sesión (`session_photos`): las de la carpeta de Events, de mejor a peor según su análisis
  (`<salida>/.phootflow/analisis.json` y la revisión), sin repetidas (dHash) y, si ya se exportaron, su versión
  editada de «Entrega/Color».
- `fill_placeholders` rellena los huecos de foto de un diseño (el hueco más grande, la mejor foto; vertical con
  vertical) y centra el encuadre en el recorte sugerido. `proposals` = plantillas rellenas; `album` = carrusel o
  álbum de varias páginas con todas las fotos elegidas.
"""
from __future__ import annotations

import colorsys
import copy
import json
import os
import random

import graphics_engine as G

# ------------------------------------------------------------------ estilos

# nombre, oscuro, claro, acento, acento 2, fuente de títulos, fuente de texto
STYLES = [
    ("Noche magenta", "#05070F", "#F4F1EC", "#F50D8A", "#7B2FF7", "Georgia", "Segoe UI"),
    ("Oro y marfil", "#2B2620", "#FBF7EF", "#C9A86A", "#8C6D3F", "Palatino Linotype", "Garamond"),
    ("Eucalipto", "#1E2B24", "#F3F1EA", "#8A9A86", "#4F6B57", "Constantia", "Segoe UI Light"),
    ("Azul marino", "#13213A", "#F5F7FA", "#2E86AB", "#F2C14E", "Bahnschrift", "Cambria"),
    ("Terracota", "#2D1B14", "#FAF3EA", "#D94F3D", "#E8A87C", "Georgia", "Corbel"),
    ("Rosa empolvado", "#3A2A2E", "#FBF2F2", "#D8A7B1", "#B76E79", "Segoe Script", "Segoe UI Light"),
    ("Deporte", "#0B0B0B", "#FFFFFF", "#D7F205", "#FF3B30", "Impact", "Bahnschrift"),
    ("Fiesta", "#1A0B2E", "#FFF8E7", "#FF7A30", "#4CC38A", "Arial Black", "Verdana"),
    ("Monocromo", "#111111", "#FFFFFF", "#7A7A7A", "#BDBDBD", "Bahnschrift SemiBold Condensed", "Segoe UI"),
    ("Lavanda", "#2A2340", "#F6F3FB", "#A57BFF", "#6CC3E6", "Candara", "Candara"),
    ("Bosque", "#102A20", "#EEF3EC", "#4CC38A", "#C9A86A", "Cambria", "Calibri"),
    ("Comunión", "#2C3E50", "#FFFFFF", "#8FB8DE", "#E9D8A6", "Palatino Linotype", "Segoe UI"),
]
STYLE_NAMES = [s[0] for s in STYLES]


def _rgb(c: str):
    c = (c or "").strip()
    if not c.startswith("#") or len(c) not in (7, 9):
        return None
    try:
        return tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)), c[7:9]
    except ValueError:
        return None


def _hex(rgb, alpha: str = "") -> str:
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02X}" for v in rgb) + alpha


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _lum(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _role(rgb) -> str:
    h, l, s = colorsys.rgb_to_hls(*rgb)
    if s > 0.35 and 0.12 < l < 0.9 and max(rgb) - min(rgb) > 0.18:
        return "accent"
    return "dark" if _lum(rgb) < 0.45 else "light"


# claves de color de cada tipo de elemento (los trazados y los degradados van aparte)
_COLOR_KEYS = ("fill", "stroke", "color", "outline")


def _walk_colors(d: dict):
    """(contenedor, clave) de cada color del diseño, en todas las páginas."""
    G.sync_page(d)
    for pg in d["pages"]:
        yield pg, "bg"
        for e in pg["elements"]:
            for k in _COLOR_KEYS:
                if isinstance(e.get(k), str):
                    yield e, k
            for k in ("grad", "shadow", "tfx"):
                sub = e.get(k)
                if isinstance(sub, dict):
                    for kk in ("c1", "c2", "color"):
                        if isinstance(sub.get(kk), str):
                            yield sub, kk
            for sp in e.get("paths", []) or []:
                for k in ("fill", "stroke"):
                    if isinstance(sp.get(k), str):
                        yield sp, k
            for r in e.get("runs", []) or []:
                if isinstance(r.get("color"), str):
                    yield r, "color"


def color_map(d: dict, style) -> dict:
    """Color actual (#RRGGBB, en mayúsculas) → color del estilo."""
    _, dark, light, acc, acc2, *_ = style
    dark, light, acc, acc2 = (_rgb(c)[0] for c in (dark, light, acc, acc2))
    use: dict[str, int] = {}
    for obj, k in _walk_colors(d):
        p = _rgb(obj[k])
        if p and p[1] != "00":
            key = obj[k][:7].upper()
            use[key] = use.get(key, 0) + 1
    out = {}
    accents = sorted((c for c in use if _role(_rgb(c)[0]) == "accent"), key=lambda c: -use[c])
    for i, c in enumerate(accents):
        out[c] = _hex(acc if i == 0 else acc2)
    for c in use:
        if c in out:
            continue
        rgb = _rgb(c)[0]
        L = _lum(rgb)
        if _role(rgb) == "dark":   # los oscuros siguen siendo oscuros, en el mismo orden
            out[c] = _hex(_mix(dark, light, min(0.3, L * 0.55)))
        else:
            out[c] = _hex(_mix(dark, light, 0.78 + min(1.0, (L - 0.45) / 0.55) * 0.22))
    return out


def restyle(d: dict, style) -> dict:
    """Aplica la paleta y las fuentes de `style` (una fila de STYLES o su nombre) a todo el diseño, en su sitio."""
    if isinstance(style, str):
        style = next(s for s in STYLES if s[0] == style)
    cmap = color_map(d, style)
    for obj, k in list(_walk_colors(d)):
        p = _rgb(obj[k])
        if p and obj[k][:7].upper() in cmap and p[1] != "00":
            obj[k] = cmap[obj[k][:7].upper()] + p[1]
    title_font, body_font = style[5], style[6]
    m = min(d["w"], d["h"])
    for pg in d["pages"]:
        for e in pg["elements"]:
            if e["type"] != "text":
                continue
            big = float(e.get("size_max") or e.get("size", 0))   # el tamaño elegido, no el ya encogido
            e["font"] = title_font if big >= 0.06 * m else body_font
            for r in e.get("runs", []) or []:
                r.pop("font", None)
            G.fit_text(e)
    G.ensure_pages(d)
    d["style"] = style[0]
    return d


def mirror(d: dict) -> dict:
    """Variante de composición: todo al otro lado (lo de la izquierda a la derecha), textos incluidos."""
    G.sync_page(d)
    W = d["w"]
    for pg in d["pages"]:
        for e in pg["elements"]:
            e["x"] = W - e["x"] - e["w"]
            if e.get("rot"):
                e["rot"] = -e["rot"]
            if e["type"] == "text" and e.get("align") in ("left", "right"):
                e["align"] = "right" if e["align"] == "left" else "left"
            if e["type"] == "image" and "fx" in e:
                e["fx"] = 1.0 - e["fx"]
            if isinstance(e.get("grad"), dict):
                e["grad"]["angle"] = (180 - e["grad"].get("angle", 90)) % 360
    G.ensure_pages(d)
    return d


def variants(d: dict, n: int = 6, seed: int | None = None) -> list[tuple[str, dict]]:
    """`n` variantes del diseño: estilos distintos al actual y, la mitad, con la composición en espejo."""
    rnd = random.Random(seed)
    names = [s for s in STYLES if s[0] != d.get("style")]
    rnd.shuffle(names)
    out = []
    for i, st in enumerate(names[:n]):
        v = copy.deepcopy(d)
        restyle(v, st)
        label = st[0]
        if i % 2 == 1:
            mirror(v)
            label += " · espejo"
        out.append((label, v))
    return out


# ------------------------------------------------------------------ fotos de la sesión

def work_dirs(folder: str) -> list[str]:
    """Dónde puede estar el análisis de Events de esta carpeta (salida por defecto «Editadas» o la propia)."""
    out = []
    for sub in ("Editadas", ""):
        w = os.path.join(folder, sub, ".phootflow")
        if os.path.isfile(os.path.join(w, "analisis.json")):
            out.append(w)
    return out


def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _hamming(a: int, b: int) -> int:
    return bin((int(a) ^ int(b)) & ((1 << 64) - 1)).count("1")


def _percentiles(vals: list[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    for r, i in enumerate(order):
        out[i] = r / max(1, len(vals) - 1)
    return out


def session_photos(folder: str) -> list[dict]:
    """Fotos de la sesión de mejor a peor: {path, thumb, name, score, keep, crop, edited}.

    Con análisis de Events: puntuación por nitidez (de la cara si la hay), ojos abiertos y caras; las descartadas
    al final; las casi iguales (dHash) detrás de su gemela. Sin análisis: las fotos de la carpeta tal cual."""
    import engine as E
    if not folder or not os.path.isdir(folder):
        return []
    wds = work_dirs(folder)
    if not wds:
        paths = []
        for sub in (os.path.join("Editadas", E.DELIVERY_DIR, E.COLOR_DIR), ""):
            dd = os.path.join(folder, sub)
            if os.path.isdir(dd):
                paths += [os.path.join(dd, n) for n in sorted(os.listdir(dd))
                          if os.path.splitext(n)[1].lower() in E.IMG_EXT | E.RAW_EXT]
        return [{"path": p, "thumb": "", "name": os.path.basename(p), "score": 0.0, "keep": True, "crop": None,
                 "edited": False} for p in paths]
    wd = wds[0]
    out_dir = os.path.dirname(wd)
    rows = _load_json(os.path.join(wd, "analisis.json"))
    review = _load_json(os.path.join(wd, "revision.json"))
    items = []
    for name, r in rows.items():
        dta = (r or {}).get("data") or {}
        if dta.get("error"):
            continue
        rv = review.get(name) or {}
        keep = bool(rv.get("keep", dta.get("keep", True)))
        stem = os.path.splitext(name)[0]
        edited = os.path.join(out_dir, E.DELIVERY_DIR, E.COLOR_DIR, stem + ".jpg")
        src = os.path.join(folder, name)
        if os.path.exists(edited):
            path, is_edit = edited, True
        elif os.path.exists(src):
            path, is_edit = src, False
        else:
            continue
        crops = dta.get("crops") or {}
        crop = (crops.get(E.CUSTOM_CROP) or crops.get(dta.get("best"))) if not is_edit else None
        th = os.path.join(wd, name + ".thumb.jpg")
        items.append({"path": path, "thumb": th if os.path.exists(th) else "", "name": name, "keep": keep,
                      "edited": is_edit, "crop": list(crop) if crop else None,
                      "_sharp": float(dta.get("face_sharp") or dta.get("sharp") or 0.0),
                      "_eyes": float(dta.get("eyes_open", 1.0) or 0.0), "_faces": int(dta.get("faces") or 0),
                      "_hash": int(dta.get("dhash") or 0)})
    if not items:
        return []
    pr = _percentiles([i["_sharp"] for i in items])
    for it, p in zip(items, pr):
        it["score"] = round(0.55 * p + 0.25 * min(1.0, it["_eyes"]) + 0.2 * (1.0 if it["_faces"] else 0.0)
                            + (0.0 if it["keep"] else -2.0) + (0.05 if it["edited"] else 0.0), 4)
    items.sort(key=lambda i: -i["score"])
    first, later = [], []
    for it in items:   # gemelas (ráfagas): detrás de la mejor
        twin = it["_hash"] and any(o["_hash"] and _hamming(it["_hash"], o["_hash"]) <= 6 for o in first)
        (later if twin else first).append(it)
    out = first + later
    for it in out:
        for k in [k for k in it if k.startswith("_")]:
            del it[k]
    return out


# ------------------------------------------------------------------ rellenar huecos

def photo_aspect(path: str, cache: G.ImageCache | None = None) -> float:
    try:
        from PIL import Image
        import engine as E
        if not E.is_raw(path):
            with Image.open(path) as im:
                w, h = im.size
                if (im.getexif() or {}).get(0x0112) in (5, 6, 7, 8):
                    w, h = h, w
        else:
            w, h = (cache.full_size(path) if cache else E.full_size(path))
        return w / h if w and h else 1.5
    except Exception:
        return 1.5


def focus(aspect_photo: float, aspect_box: float, crop) -> tuple[float, float]:
    """(fx, fy) para que el encuadre de la caja quede centrado en el recorte sugerido por Events."""
    if not crop:
        return 0.5, 0.5
    cx, cy = (crop[0] + crop[2]) / 2, (crop[1] + crop[3]) / 2
    if aspect_photo > aspect_box:   # sobra a los lados
        ww = aspect_box / aspect_photo
        return (min(1.0, max(0.0, (cx - ww / 2) / (1 - ww))) if ww < 1 else 0.5), 0.5
    hh = aspect_photo / aspect_box
    return 0.5, (min(1.0, max(0.0, (cy - hh / 2) / (1 - hh))) if hh < 1 else 0.5)


def _orient(a: float) -> int:
    return 1 if a > 1.1 else (-1 if a < 0.9 else 0)


def slots(d: dict) -> list[dict]:
    G.sync_page(d)
    return [e for pg in d["pages"] for e in pg["elements"] if e["type"] == "image" and not e.get("path")]


def fill_placeholders(d: dict, photos: list[dict], aspects: dict | None = None, use_thumbs: bool = False) -> int:
    """Rellena los huecos vacíos con `photos` (ya ordenadas de mejor a peor). Devuelve cuántos.
    `use_thumbs`: con la miniatura de Events (para las vistas previas: no revela RAW)."""
    aspects = aspects if aspects is not None else {}
    free = list(photos)
    n = 0
    for el in sorted(slots(d), key=lambda e: -(e["w"] * e["h"])):
        if not free:
            break
        ab = el["w"] / max(el["h"], 1e-6)
        pick = None
        for ph in free:
            a = aspects.setdefault(ph["path"], photo_aspect(ph["path"]))
            if _orient(ab) == 0 or _orient(a) in (0, _orient(ab)):
                pick = ph
                break
        pick = pick or free[0]
        free.remove(pick)
        a = aspects.setdefault(pick["path"], photo_aspect(pick["path"]))
        el["fx"], el["fy"] = focus(a, ab, pick.get("crop"))
        el["path"] = pick["thumb"] if use_thumbs and pick.get("thumb") else pick["path"]
        el["zoom"] = 1.0
        n += 1
    G.ensure_pages(d)
    return n


def proposals(photos: list[dict], aspects: dict | None = None, use_thumbs: bool = False) -> list[tuple[str, dict]]:
    """Plantillas con huecos de foto, rellenas con las mejores de `photos` (las que tienen más huecos primero
    cuando hay fotos de sobra)."""
    aspects = aspects if aspects is not None else {}
    out = []
    for name in G.TEMPLATES:
        d = G.design_from_preset(G.TEMPLATE_SIZE[name])
        d["name"] = name
        G.apply_template(d, name)
        k = len(slots(d))
        if not k or k > max(1, len(photos)):
            continue
        fill_placeholders(d, photos, aspects, use_thumbs)
        out.append((name, d, k))
    out.sort(key=lambda t: -min(t[2], 4))
    return [(n, d) for n, d, _ in out]


# ------------------------------------------------------------------ álbum / carrusel

ALBUMS = {"Carrusel de Instagram": "Instagram vertical 4:5", "Álbum A4 horizontal": "A4 horizontal",
          "Álbum cuadrado": "Instagram cuadrado", "Stories": "Story / Reel"}
_GROUPS = [2, 3, 1, 4, 2, 3]


def _cells(n: int, x: float, y: float, w: float, h: float, g: float) -> list[tuple]:
    """Huecos para `n` fotos (1–4) dentro de la caja (x, y, w, h) con separación `g`."""
    wide = w >= h
    if n == 1:
        return [(x, y, w, h)]
    if n == 2:
        if wide:
            a = (w - g) / 2
            return [(x, y, a, h), (x + a + g, y, a, h)]
        a = (h - g) / 2
        return [(x, y, w, a), (x, y + a + g, w, a)]
    if n == 3:
        if wide:
            a = (w - g) * 0.6
            b = (h - g) / 2
            return [(x, y, a, h), (x + a + g, y, w - a - g, b), (x + a + g, y + b + g, w - a - g, b)]
        a = (h - g) * 0.6
        b = (w - g) / 2
        return [(x, y, w, a), (x, y + a + g, b, h - a - g), (x + b + g, y + a + g, b, h - a - g)]
    a, b = (w - g) / 2, (h - g) / 2
    return [(x, y, a, b), (x + a + g, y, a, b), (x, y + b + g, a, b), (x + a + g, y + b + g, a, b)]


def album(photos: list[dict], kind: str = "Carrusel de Instagram", title: str = "", subtitle: str = "",
          style: str = "Noche magenta", aspects: dict | None = None, use_thumbs: bool = False) -> dict:
    """Diseño de varias páginas con todas las fotos: portada (la mejor a sangre con el título) y páginas de 1 a 4
    fotos, alternando composiciones. Con el estilo elegido."""
    aspects = aspects if aspects is not None else {}
    d = G.design_from_preset(ALBUMS.get(kind, kind))
    d["name"] = title or kind
    W, H = d["w"], d["h"]
    m = min(W, H) * 0.05
    g = m * 0.5
    st = next(s for s in STYLES if s[0] == style)
    pages = []
    rest = list(photos)
    if rest:   # portada
        els = [G.image_el(d, "", x=0.0, y=0.0, w=W, h=H)]
        band = G.shape_el(d, "rect", x=0.0, y=H * 0.72, w=W, h=H * 0.28, fill=st[1] + "D9")
        els.append(band)
        if title:
            t = G.text_el(d, title, "title", color=st[2], font=st[5], x=m, w=W - 2 * m)
            t["y"] = H * 0.75
            t["fit_h"] = H * 0.14
            G.fit_text(t)
            els.append(t)
            ln = G.shape_el(d, "line", x=m * 2, y=t["y"] - m * 0.4, w=min(W, H) * 0.12, h=max(2.0, m * 0.08),
                            stroke=st[3], fill=st[3])
            ln["x"] = (W - ln["w"]) / 2
            els.append(ln)
        if subtitle:
            s = G.text_el(d, subtitle, "body", color=st[2], font=st[6], x=m, w=W - 2 * m, upper=True, spacing=4.0)
            s["y"] = H * 0.91
            els.append(s)
        pages.append({"bg": st[1], "elements": els, "_photos": rest[:1]})
        rest = rest[1:]
    i = 0
    while rest:
        n = min(len(rest), _GROUPS[i % len(_GROUPS)])
        cells = _cells(n, m, m, W - 2 * m, H - 2 * m, g)
        els = [G.image_el(d, "", x=cx, y=cy, w=cw, h=ch) for cx, cy, cw, ch in cells]
        pages.append({"bg": st[2] if i % 2 == 0 else st[1], "elements": els, "_photos": rest[:n]})
        rest = rest[n:]
        i += 1
    if not pages:
        pages = [{"bg": st[2], "elements": [], "_photos": []}]
    for pg in pages:   # cada página con sus fotos (la mejor al hueco más grande, orientación a juego)
        tmp = G.new_design(W, H)
        tmp["pages"] = [{"bg": pg["bg"], "elements": pg["elements"]}]
        G.ensure_pages(tmp)
        fill_placeholders(tmp, pg.pop("_photos"), aspects, use_thumbs)
    d["pages"] = pages
    d["page"] = 0
    G.ensure_pages(d)
    d["style"] = style
    return d
