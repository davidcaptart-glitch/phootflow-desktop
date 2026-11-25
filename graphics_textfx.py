"""Graphics: efectos de texto (como el panel «Efectos» de Canva) y estilos de texto prefabricados.

Un texto lleva como mucho un efecto en `el["tfx"] = {"type": ..., parámetros}`; los parámetros van de 0 a 100
y son relativos al tamaño de letra, así el efecto se ve igual al escalar el texto. Se combinan con el color o el
degradado del texto (`el["grad"]`), la curva y el contorno/sombra de siempre (`outline_w`, `shadow`).

`layers(el)` describe el texto como capas (relleno, trazo, desenfoque) en coordenadas locales: la misma lista
sirve para pintar (`paint`) y para «Crear contornos» (`vector_paths`, sin las capas desenfocadas).
"""
from __future__ import annotations

import json
import math
from collections import OrderedDict

import numpy as np

# tipo: (etiqueta, parámetros por defecto)
FX = OrderedDict([
    ("none", ("Ninguno", {})),
    ("shadow", ("Sombra", {"offset": 50, "dir": 45, "blur": 0, "alpha": 40, "color": "#000000"})),
    ("lift", ("Elevar", {"amount": 50})),
    ("echo", ("Eco", {"offset": 50, "dir": 45, "color": ""})),
    ("outline", ("Contorno", {"thick": 50, "color": "#000000"})),
    ("background", ("Fondo", {"round": 50, "spread": 50, "alpha": 100, "color": "#FFD84D"})),
    ("splice", ("Desalineado", {"thick": 50, "offset": 50, "dir": 45, "color": "#9AA0FF"})),
    ("hollow", ("Hueco", {"thick": 50})),
    ("neon", ("Neón", {"amount": 50})),
    ("glitch", ("Distorsión", {"offset": 30, "dir": 0, "pair": "cian-magenta"})),
    ("glow", ("Resplandor", {"blur": 50, "alpha": 80, "color": "#FFE066"})),
    ("extrude", ("3D", {"depth": 50, "dir": 45, "color": "#05070F"})),
])

# parámetro: (etiqueta, mínimo, máximo)
PARAMS = {
    "offset": ("Distancia", 0, 100), "dir": ("Dirección", -180, 180), "blur": ("Desenfoque", 0, 100),
    "alpha": ("Opacidad", 0, 100), "thick": ("Grosor", 1, 100), "amount": ("Intensidad", 0, 100),
    "round": ("Redondez", 0, 100), "spread": ("Margen", 0, 100), "depth": ("Profundidad", 1, 100),
}
PAIRS = OrderedDict([
    ("cian-magenta", ("#00E5FF", "#FF2BD6")), ("azul-rojo", ("#2F6BFF", "#FF3B3B")),
    ("verde-violeta", ("#2BFF88", "#8A2BFF")), ("amarillo-azul", ("#FFD60A", "#3A86FF")),
])

# estilos prefabricados: cambian fuente, color y efecto a la vez (las fuentes son de Google Fonts y se
# descargan al aplicarlo). «color» claro sobre fondo claro se ve en la miniatura sobre gris.
STYLES = [
    ("Neón rosa", {"font": "Tilt Neon", "color": "#FF4FD8", "tfx": {"type": "neon", "amount": 60}}),
    ("Televisión", {"font": "Rubik Glitch", "color": "#111111", "tfx": {"type": "glitch", "offset": 40, "dir": 0,
                                                                         "pair": "cian-magenta"}}),
    ("Retro 70", {"font": "Righteous", "color": "#F6A400", "tfx": {"type": "extrude", "depth": 40, "dir": 45,
                                                                     "color": "#C2410C"}}),
    ("Futurista", {"font": "Orbitron", "color": "#B7FF3C", "bold": True, "tfx": {"type": "neon", "amount": 45}}),
    ("Risografía", {"font": "Bungee", "color": "#FF3D7F", "tfx": {"type": "splice", "thick": 35, "offset": 45,
                                                                    "dir": 45, "color": "#2B59FF"}}),
    ("Oeste", {"font": "Rye", "color": "#B5651D", "tfx": {"type": "shadow", "offset": 35, "dir": 45, "blur": 0,
                                                           "alpha": 100, "color": "#3B1F0E"}}),
    ("Grafiti", {"font": "Permanent Marker", "color": "#FFFFFF", "tfx": {"type": "outline", "thick": 70,
                                                                          "color": "#111111"}}),
    ("Cómic", {"font": "Bangers", "color": "#FFD200", "tfx": {"type": "extrude", "depth": 35, "dir": 45,
                                                               "color": "#111111"}}),
    ("Oro", {"font": "Cinzel", "bold": True, "color": "#D4A73A",
             "grad": {"c1": "#F9E7A1", "c2": "#B07D16", "angle": 90}, "tfx": {"type": "lift", "amount": 40}}),
    ("Boda", {"font": "Great Vibes", "color": "#8C6A4F", "tfx": {"type": "shadow", "offset": 20, "dir": 60,
                                                                  "blur": 40, "alpha": 25, "color": "#000000"}}),
    ("Deporte", {"font": "Bebas Neue", "italic": True, "upper": True, "color": "#FFFFFF",
                 "tfx": {"type": "extrude", "depth": 45, "dir": 30, "color": "#E11D48"}}),
    ("Etiqueta", {"font": "Montserrat", "bold": True, "color": "#FFFFFF",
                  "tfx": {"type": "background", "round": 70, "spread": 45, "alpha": 100, "color": "#F50D8A"}}),
    ("Pizarra", {"font": "Caveat", "color": "#F4F1E8", "tfx": {"type": "background", "round": 15, "spread": 70,
                                                                "alpha": 100, "color": "#23332E"}}),
    ("Eco vintage", {"font": "Abril Fatface", "color": "#05070F", "tfx": {"type": "echo", "offset": 40, "dir": 45,
                                                                          "color": "#F50D8A"}}),
    ("Hueco", {"font": "Syne", "bold": True, "color": "#7C3AED", "tfx": {"type": "hollow", "thick": 45}}),
    ("Atardecer", {"font": "Pacifico", "color": "#FF7A18", "grad": {"c1": "#FFB347", "c2": "#C2185B", "angle": 90},
                   "tfx": {"type": "shadow", "offset": 25, "dir": 45, "blur": 30, "alpha": 35, "color": "#5A0B2E"}}),
    ("Brillo", {"font": "Playfair Display", "bold": True, "color": "#FFFFFF",
                "tfx": {"type": "glow", "blur": 60, "alpha": 90, "color": "#FFC94D"}}),
    ("Sello", {"font": "Black Ops One", "color": "#C62828", "tfx": {"type": "hollow", "thick": 30}}),
]

_KEYS = ("text", "font", "size", "bold", "italic", "spacing", "line", "w", "upper", "align", "curve", "color",
         "grad", "tfx", "shadow", "outline", "outline_w", "runs", "list")
_layers_cache: OrderedDict = OrderedDict()
_blur_cache: OrderedDict = OrderedDict()


def params(el: dict) -> dict:
    t = (el.get("tfx") or {}).get("type", "none")
    p = dict(FX.get(t, FX["none"])[1])
    p.update({k: v for k, v in (el.get("tfx") or {}).items() if k != "type"})
    p["type"] = t if t in FX else "none"
    return p


def active(el: dict) -> bool:
    """¿Hace falta el pintor de efectos? (si no, el texto se dibuja con el maquetado normal, más rápido)."""
    return (params(el)["type"] != "none" or bool(el.get("grad")) or bool(el.get("shadow"))
            or float(el.get("outline_w", 0.0)) > 0 or abs(float(el.get("curve", 0.0))) >= 1)


def has_blur(el: dict) -> bool:
    return any(L["k"] == "blur" for L in layers(el))


def _key(el):
    return json.dumps({k: el.get(k) for k in _KEYS}, sort_keys=True, default=str)


def _col(s, alpha_mul=1.0):
    import graphics_engine as G
    c = G.qcolor(s)
    c.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha_mul)))
    return c


def _hex(c) -> str:
    return "#%02X%02X%02X%02X" % (c.red(), c.green(), c.blue(), c.alpha())


def _mix_white(s, k):
    c = _col(s)
    from PySide6.QtGui import QColor
    return QColor(int(c.red() + (255 - c.red()) * k), int(c.green() + (255 - c.green()) * k),
                  int(c.blue() + (255 - c.blue()) * k), c.alpha())


def _moved(path, dx, dy):
    from PySide6.QtGui import QTransform
    return QTransform.fromTranslate(dx, dy).map(path)


def _vec(p, scale):
    a = math.radians(float(p.get("dir", 45)))
    d = float(p.get("offset", 50)) * scale
    return math.cos(a) * d, math.sin(a) * d


def _line_boxes(el, path, pad_x, pad_y, rr):
    """Fondo por línea (como Canva): un rectángulo redondeado tras cada línea, unidos en un solo trazado."""
    import graphics_engine as G
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QPainterPath
    bg = QPainterPath()
    bg.setFillRule(Qt.WindingFill)
    rects = []
    if abs(float(el.get("curve", 0.0))) >= 1:
        rects.append(path.boundingRect())
    else:
        lays, _ = G._text_layout(el)
        for lay in lays:
            for i in range(lay.lineCount()):
                r = lay.lineAt(i).naturalTextRect()
                if r.width() > 0.5:
                    rects.append(r)
    for r in rects:
        r = QRectF(r).adjusted(-pad_x, -pad_y, pad_x, pad_y)
        rad = min(rr * r.height() / 2, r.width() / 2)
        bg.addRoundedRect(r, rad, rad)
    return bg.simplified()


def layers(el: dict) -> list[dict]:
    """Capas de dibujo, de atrás adelante: {"k": "fill"|"stroke"|"blur", "path", "color"/"brush", ...}."""
    key = _key(el)
    hit = _layers_cache.get(key)
    if hit is not None:
        _layers_cache.move_to_end(key)
        return hit
    import graphics_engine as G
    from PySide6.QtCore import QRectF, Qt
    path = G.text_outline(el)
    path.setFillRule(Qt.WindingFill)
    s = float(el.get("size", 24))
    p = params(el)
    t = p["type"]
    col = el.get("color", "#000000")
    out: list[dict] = []
    main_brush = G.make_brush(col, el.get("grad"), QRectF(0, 0, el["w"], el["h"]))
    main = {"k": "fill", "path": path, "brush": main_brush, "color": col}

    if t == "background":
        pad_x = float(p["spread"]) * s * 0.006
        pad_y = float(p["spread"]) * s * 0.0022
        out.append({"k": "fill", "path": _line_boxes(el, path, pad_x, pad_y, float(p["round"]) / 100),
                    "color": _hex(_col(p["color"], float(p["alpha"]) / 100))})
    elif t == "shadow":
        dx, dy = _vec(p, s * 0.003)
        c = _hex(_col(p["color"], float(p["alpha"]) / 100))
        r = float(p["blur"]) * s * 0.003
        if r > 0.3:
            out.append({"k": "blur", "path": path, "color": c, "r": r, "dx": dx, "dy": dy})
        else:
            out.append({"k": "fill", "path": _moved(path, dx, dy), "color": c})
    elif t == "lift":
        a = float(p["amount"])
        out.append({"k": "blur", "path": path, "color": _hex(_col("#000000", min(0.8, 0.12 + a * 0.006))),
                    "r": s * (0.03 + a * 0.0012), "dx": 0.0, "dy": s * (0.03 + a * 0.0006)})
    elif t == "echo":
        dx, dy = _vec(p, s * 0.003)
        c = p.get("color") or col
        for k, a in ((2, 0.3), (1, 0.55)):
            out.append({"k": "fill", "path": _moved(path, dx * k, dy * k), "color": _hex(_col(c, a))})
    elif t == "glitch":
        dx, dy = _vec(p, s * 0.003)
        c1, c2 = PAIRS.get(p.get("pair"), PAIRS["cian-magenta"])
        out.append({"k": "fill", "path": _moved(path, -dx, -dy), "color": c1})
        out.append({"k": "fill", "path": _moved(path, dx, dy), "color": c2})
    elif t == "splice":
        dx, dy = _vec(p, s * 0.003)
        out.append({"k": "fill", "path": _moved(path, dx, dy), "color": p["color"]})
    elif t == "outline":
        out.append({"k": "stroke", "path": path, "color": p["color"], "w": float(p["thick"]) * s * 0.004})
    elif t == "neon":
        a = 0.5 + float(p["amount"]) / 100
        out.append({"k": "blur", "path": path, "color": _hex(_col(col, 0.85)), "r": s * 0.12 * a, "dx": 0, "dy": 0})
        out.append({"k": "blur", "path": path, "color": _hex(_col(col)), "r": s * 0.035 * a, "dx": 0, "dy": 0})
        main = {"k": "fill", "path": path, "color": _hex(_mix_white(col, 0.25 + float(p["amount"]) * 0.003))}
    elif t == "glow":
        c = _hex(_col(p["color"], float(p["alpha"]) / 100))
        r = max(0.5, float(p["blur"]) * s * 0.003)
        out.append({"k": "blur", "path": path, "color": c, "r": r, "dx": 0, "dy": 0})
        out.append({"k": "blur", "path": path, "color": c, "r": r * 0.35, "dx": 0, "dy": 0})
    elif t == "extrude":
        depth = float(p["depth"]) * s * 0.003
        step = max(0.6, s * 0.006)
        n = max(1, min(60, int(depth / step)))
        a = math.radians(float(p.get("dir", 45)))
        for i in range(n, 0, -1):
            d = depth * i / n
            out.append({"k": "fill", "path": _moved(path, math.cos(a) * d, math.sin(a) * d), "color": p["color"]})

    # contorno y sombra «de siempre» (propiedades sueltas, anteriores a los efectos)
    ow = float(el.get("outline_w", 0.0))
    sh = el.get("shadow")
    if sh:
        out.append({"k": "fill", "path": _moved(path, sh.get("dx", 0.0), sh.get("dy", 0.0)),
                    "color": sh.get("color", "#00000080")})
        if ow > 0:
            out.append({"k": "stroke", "path": _moved(path, sh.get("dx", 0.0), sh.get("dy", 0.0)),
                        "color": sh.get("color", "#00000080"), "w": ow})
    if ow > 0:
        out.append({"k": "stroke", "path": path, "color": el.get("outline", "#000000"), "w": ow})

    if t == "hollow":
        out.append({"k": "stroke", "path": path, "color": col, "w": float(p["thick"]) * s * 0.0012})
    elif t == "splice":
        out.append({"k": "stroke", "path": path, "color": col, "w": float(p["thick"]) * s * 0.0012})
    else:
        out.append(main)

    _layers_cache[key] = out
    while len(_layers_cache) > 64:
        _layers_cache.popitem(last=False)
    return out


def extent(el: dict):
    """Rectángulo que ocupa el texto con sus efectos (coordenadas locales)."""
    from PySide6.QtCore import QRectF
    r = QRectF(0, 0, el["w"], el["h"])
    for L in layers(el):
        b = L["path"].boundingRect()
        if L["k"] == "blur":
            m = L["r"] * 3
            b = b.adjusted(-m + L["dx"], -m + L["dy"], m + L["dx"], m + L["dy"])
        elif L["k"] == "stroke":
            b = b.adjusted(-L["w"] / 2, -L["w"] / 2, L["w"] / 2, L["w"] / 2)
        r = r.united(b)
    return r


def margin(el: dict) -> float:
    """Cuánto sobresalen los efectos de la caja del texto (para el área que repinta el lienzo)."""
    try:
        r = extent(el)
    except Exception:
        return 0.0
    return max(0.0, -r.left(), -r.top(), r.right() - el["w"], r.bottom() - el["h"])


def _blur_image(key, path, color, r, s):
    """Silueta desenfocada a la resolución del dispositivo (`s` píxeles por unidad de diseño)."""
    ck = (key, color, round(r, 3), s)
    hit = _blur_cache.get(ck)
    if hit is not None:
        _blur_cache.move_to_end(ck)
        return hit
    import cv2
    import graphics_engine as G
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    m = r * 3 + 2
    rect = path.boundingRect().adjusted(-m, -m, m, m)
    big = max(rect.width(), rect.height()) * s
    if big > 3000:   # tope de memoria: más grande no se nota en un desenfoque
        s *= 3000 / big
    w, h = max(1, int(math.ceil(rect.width() * s))), max(1, int(math.ceil(rect.height() * s)))
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing, True)
    q.scale(s, s)
    q.translate(-rect.left(), -rect.top())
    q.fillPath(path, QColor(255, 255, 255))
    q.end()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
    a = cv2.GaussianBlur(arr[..., 3].astype(np.float32), (0, 0), max(0.5, r * s / 2))
    c = G.qcolor(color)
    out = np.empty((h, w, 4), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = c.blue(), c.green(), c.red()
    out[..., 3] = np.clip(a * (c.alpha() / 255.0), 0, 255).astype(np.uint8)
    qi = QImage(out.data, w, h, w * 4, QImage.Format_ARGB32).copy()
    res = (qi, rect)
    _blur_cache[ck] = res
    while len(_blur_cache) > 32:
        _blur_cache.popitem(last=False)
    return res


def paint(p, el: dict) -> None:
    """Pinta el texto con sus efectos en coordenadas locales (quien llama pone posición, giro y opacidad)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPen
    t = p.worldTransform()
    s = math.sqrt(abs(t.m11() * t.m22() - t.m12() * t.m21())) or 1.0
    s = 2 ** (round(math.log2(s) * 4) / 4)   # escalones: al hacer zoom no se rehace a cada paso
    key = _key(el)
    p.save()
    p.setRenderHint(p.RenderHint.Antialiasing, True)
    for L in layers(el):
        if L["k"] == "blur":
            img, rect = _blur_image(key, L["path"], L["color"], L["r"], s)
            p.setRenderHint(p.RenderHint.SmoothPixmapTransform, True)
            p.drawImage(rect.translated(L["dx"], L["dy"]), img)
        elif L["k"] == "stroke":
            p.strokePath(L["path"], QPen(_col(L["color"]), L["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        else:
            p.fillPath(L["path"], L.get("brush") or _col(L["color"]))
    p.restore()


def vector_paths(el: dict) -> list[dict]:
    """Capas → trazados de un elemento vectorial (las desenfocadas se pierden: no son vectoriales)."""
    import graphics_engine as G
    out = []
    for L in layers(el):
        if L["k"] == "blur":
            continue
        path = L["path"].simplified()
        d = G.qpath_to_d(path)
        if L["k"] == "stroke":
            out.append({"fill": "#00000000", "d": d, "stroke": L["color"], "stroke_w": L["w"]})
        else:
            fill = L["color"]
            if L.get("brush") is not None and el.get("grad"):
                fill = el["grad"].get("c1", fill)
            out.append({"fill": fill, "d": d})
    return out


def apply_style(el: dict, style: dict) -> None:
    """Aplica un estilo prefabricado: lo que el estilo no dice se quita (degradado, negrita…)."""
    for k in ("grad", "bold", "italic", "upper", "shadow"):
        el.pop(k, None)
    el["outline_w"] = 0.0
    for k, v in style.items():
        el[k] = json.loads(json.dumps(v))


def preview(el_props: dict, w: int = 84, h: int = 84, bg: str = "", text: str = "Ag"):
    """Miniatura de un efecto o estilo pintada con el mismo motor (fondo oscuro si la letra es clara)."""
    import graphics_engine as G
    from PySide6.QtGui import QImage, QPainter
    if not bg:
        c = G.qcolor(el_props.get("color", "#7C3AED"))
        bg = "#1B1E2B" if (0.3 * c.redF() + 0.59 * c.greenF() + 0.11 * c.blueF()) > 0.72 else "#F1F2F6"
    el = {"type": "text", "text": text, "font": "Segoe UI", "size": h * 0.5, "bold": True, "color": "#7C3AED",
          "align": "center", "line": 1.0, "w": w * 0.9, "h": h * 0.6, "x": 0, "y": 0}
    el.update(json.loads(json.dumps(el_props)))
    el["size"] = h * 0.5
    el["h"] = G.text_height(el)
    img = QImage(w * 2, h * 2, QImage.Format_ARGB32_Premultiplied)
    img.fill(G.qcolor(bg))
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing, True)
    q.scale(2, 2)
    q.translate((w - el["w"]) / 2, (h - el["h"]) / 2)
    paint(q, el)
    q.end()
    img.setDevicePixelRatio(2)
    return img
