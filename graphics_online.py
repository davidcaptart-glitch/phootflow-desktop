"""Graphics: elementos de internet (sin interfaz).

- Gráficos y stickers de Iconify (api.iconify.design): más de 200 000 iconos e ilustraciones de colecciones libres.
  Solo se usan colecciones con licencia que permite uso comercial y modificación (MIT, Apache, ISC, OFL, CC0,
  CC BY, CC BY-SA, MPL, BSD); las GPL y las animadas se descartan. El SVG se convierte en un vector editable de
  Graphics (`svg_to_vector`); si usa degradados o máscaras se rasteriza a PNG.
- Fotos de Openverse (api.openverse.org, sin clave, licencias CC de uso comercial y que permiten modificar) y de
  Pexels (licencia Pexels, con la clave gratuita del usuario).
- Fuentes de Google Fonts (OFL/Apache): el CSS de fonts.googleapis.com sirve TTF a clientes que no son navegador.

Todo lo descargado queda en `<cfg>/online/` (iconos, fotos, fuentes) y vale sin conexión. Nunca se sube nada:
solo se envía el texto buscado.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import ssl
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "PhootFlow/1.0 (+https://github.com)"
ICONIFY = "https://api.iconify.design"
OPENVERSE = "https://api.openverse.org/v1/images/"
PEXELS = "https://api.pexels.com/v1/search"
GFONTS_CSS = "https://fonts.googleapis.com/css2"

# licencias de colecciones de Iconify que permiten usar el gráfico en un diseño comercial y modificarlo
OK_LICENSES = {"MIT", "Apache-2.0", "ISC", "OFL-1.1", "CC0-1.0", "CC-BY-3.0", "CC-BY-4.0", "CC-BY-SA-3.0",
               "CC-BY-SA-4.0", "MPL-2.0", "BSD-2-Clause", "BSD-3-Clause", "Unlicense"}
# colecciones animadas (SMIL) o de spinners: en un diseño estático no tienen sentido
SKIP_PREFIXES = {"line-md", "svg-spinners", "eos-icons"}
# colecciones a color para la vista «Stickers»
STICKER_PREFIXES = ["fluent-emoji-flat", "noto", "twemoji", "openmoji", "emojione", "fxemoji", "flat-color-icons",
                    "streamline-emojis", "logos", "skill-icons", "vscode-icons", "noto-v1", "emojione-v1"]

# --------------------------------------------------------------- categorías
# (etiqueta, búsqueda en inglés) — Iconify busca en inglés; los ids fijos van en ICON_SETS
CATEGORIES = [
    ("Formas", "shape"), ("Corazones", "heart"), ("Estrellas", "star"), ("Flechas", "arrow"),
    ("Marcos", "frame"), ("Insignias", "badge"), ("Líneas", "line"), ("Bocadillos", "chat bubble"),
    ("Flores", "flower"), ("Hojas", "leaf"), ("Boda", "wedding"), ("Anillos", "ring"), ("Fiesta", "party"),
    ("Cumpleaños", "birthday"), ("Bebé", "baby"), ("Religión", "church"), ("Deporte", "sport"),
    ("Fútbol", "soccer"), ("Baloncesto", "basketball"), ("Medallas", "medal"), ("Música", "music"),
    ("Cámara", "camera"), ("Comida", "food"), ("Bebidas", "drink"), ("Viajes", "travel"), ("Naturaleza", "nature"),
    ("Animales", "animal"), ("Tiempo", "weather"), ("Navidad", "christmas"), ("Halloween", "halloween"),
    ("Coronas", "crown"), ("Brillos", "sparkle"), ("Ubicación", "location"), ("Contacto", "phone"),
]
ICON_SETS = {
    "Redes sociales": ["logos:instagram-icon", "logos:facebook", "logos:whatsapp-icon", "logos:tiktok-icon",
                       "logos:youtube-icon", "logos:telegram", "logos:pinterest", "logos:linkedin-icon",
                       "logos:google-icon", "logos:google-maps", "logos:spotify-icon", "logos:twitch",
                       "mdi:instagram", "mdi:facebook", "mdi:whatsapp", "ic:baseline-tiktok", "mdi:youtube",
                       "mdi:pinterest", "mdi:linkedin", "ri:twitter-x-fill", "mdi:email", "mdi:phone",
                       "mdi:web", "mdi:map-marker", "mdi:calendar", "mdi:clock-outline", "tabler:brand-instagram",
                       "tabler:brand-facebook", "tabler:brand-whatsapp", "tabler:brand-tiktok",
                       "tabler:brand-youtube", "tabler:mail", "tabler:phone", "tabler:world", "tabler:map-pin"],
}
PHOTO_CATEGORIES = [("Bodas", "wedding"), ("Fiesta", "party"), ("Deporte", "sport"), ("Flores", "flowers"),
                    ("Naturaleza", "nature"), ("Cielo", "sky"), ("Mar", "sea"), ("Montaña", "mountain"),
                    ("Ciudad", "city"), ("Texturas", "texture"), ("Fondos", "background"), ("Papel", "paper"),
                    ("Madera", "wood"), ("Mármol", "marble"), ("Bokeh", "bokeh"), ("Atardecer", "sunset"),
                    ("Comida", "food"), ("Niños", "children"), ("Navidad", "christmas"), ("Otoño", "autumn")]

# traducción mínima de búsquedas habituales (Iconify y Openverse responden mejor en inglés)
ES_EN = {
    "corazon": "heart", "corazones": "heart", "estrella": "star", "estrellas": "star", "flecha": "arrow",
    "flechas": "arrow", "forma": "shape", "formas": "shape", "circulo": "circle", "cuadrado": "square",
    "triangulo": "triangle", "marco": "frame", "marcos": "frame", "linea": "line", "lineas": "line",
    "flor": "flower", "flores": "flower", "hoja": "leaf", "hojas": "leaf", "arbol": "tree", "boda": "wedding",
    "bodas": "wedding", "novia": "bride", "novio": "groom", "anillo": "ring", "anillos": "ring",
    "fiesta": "party", "cumpleanos": "birthday", "tarta": "cake", "pastel": "cake", "globo": "balloon",
    "globos": "balloon", "regalo": "gift", "bebe": "baby", "nino": "child", "ninos": "children",
    "comunion": "communion", "bautizo": "baptism", "iglesia": "church", "cruz": "cross", "paloma": "dove",
    "deporte": "sport", "futbol": "soccer", "balon": "ball", "pelota": "ball", "baloncesto": "basketball",
    "tenis": "tennis", "padel": "padel", "natacion": "swimming", "ciclismo": "cycling", "bicicleta": "bicycle",
    "correr": "running", "medalla": "medal", "trofeo": "trophy", "copa": "trophy", "musica": "music",
    "nota": "music note", "guitarra": "guitar", "camara": "camera", "foto": "photo", "fotografia": "photography",
    "comida": "food", "bebida": "drink", "cafe": "coffee", "vino": "wine", "cerveza": "beer", "brindis": "toast",
    "viaje": "travel", "avion": "airplane", "coche": "car", "playa": "beach", "mar": "sea", "sol": "sun",
    "luna": "moon", "nube": "cloud", "lluvia": "rain", "nieve": "snow", "montana": "mountain",
    "naturaleza": "nature", "animal": "animal", "animales": "animal", "perro": "dog", "gato": "cat",
    "pajaro": "bird", "mariposa": "butterfly", "caballo": "horse", "navidad": "christmas", "arbol de navidad":
    "christmas tree", "nochevieja": "new year", "halloween": "halloween", "calabaza": "pumpkin", "corona": "crown",
    "brillo": "sparkle", "brillos": "sparkle", "destello": "sparkle", "ubicacion": "location", "mapa": "map",
    "telefono": "phone", "correo": "mail", "web": "web", "calendario": "calendar", "reloj": "clock",
    "casa": "home", "hogar": "home", "amor": "love", "beso": "kiss", "sonrisa": "smile", "cara": "face",
    "mano": "hand", "manos": "hands", "ojo": "eye", "persona": "person", "personas": "people", "familia": "family",
    "bocadillo": "chat bubble", "dialogo": "speech bubble", "insignia": "badge", "sello": "stamp",
    "etiqueta": "tag", "cinta": "ribbon", "lazo": "bow", "confeti": "confetti", "fuegos artificiales":
    "fireworks", "vela": "candle", "velas": "candle", "luz": "light", "bombilla": "bulb", "fuego": "fire",
    "agua": "water", "gota": "drop", "onda": "wave", "ola": "wave", "fondo": "background", "fondos":
    "background", "textura": "texture", "texturas": "texture", "papel": "paper", "madera": "wood",
    "marmol": "marble", "cielo": "sky", "atardecer": "sunset", "ciudad": "city", "otono": "autumn",
    "primavera": "spring", "verano": "summer", "invierno": "winter", "flor seca": "dried flower",
    "rosa": "rose", "girasol": "sunflower", "tulipan": "tulip", "laurel": "laurel", "rama": "branch",
    "compras": "shopping", "carrito": "cart", "dinero": "money", "precio": "price", "oferta": "sale",
    "descuento": "discount", "nuevo": "new", "check": "check", "verificado": "verified", "alerta": "alert",
}


def _ascii(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn").strip()


def to_query(q: str) -> str:
    """Búsqueda del usuario → inglés (frase entera o palabra a palabra); lo que no se conoce se deja igual."""
    a = _ascii(q)
    if a in ES_EN:
        return ES_EN[a]
    return " ".join(ES_EN.get(w, w) for w in a.split())


# --------------------------------------------------------------- red y caché

_ctx = None


def _ssl():
    global _ctx
    if _ctx is None:
        try:
            import certifi
            _ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _ctx = ssl.create_default_context()
    return _ctx


def http_get(url: str, headers: dict | None = None, timeout: float = 20.0, limit: int = 40 << 20) -> bytes:
    h = {"User-Agent": UA}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), context=_ssl(), timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Archivo demasiado grande")
    return data


def http_json(url: str, headers: dict | None = None) -> dict:
    return json.loads(http_get(url, headers).decode("utf-8"))


class Store:
    """Carpetas de caché y ajustes de los elementos online."""

    def __init__(self, cfg_dir: str):
        self.root = os.path.join(cfg_dir, "online")
        self.icons = os.path.join(self.root, "iconos")
        self.photos = os.path.join(self.root, "fotos")
        self.fonts = os.path.join(self.root, "fuentes")
        for p in (self.icons, self.photos, self.fonts):
            os.makedirs(p, exist_ok=True)
        self._lock = threading.Lock()

    # ajustes (clave de Pexels) y recientes
    def _json(self, name, default):
        try:
            with open(os.path.join(self.root, name), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _save_json(self, name, data):
        tmp = os.path.join(self.root, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(self.root, name))

    def settings(self) -> dict:
        return self._json("ajustes.json", {})

    def set_setting(self, key, value):
        with self._lock:
            s = self.settings()
            s[key] = value
            self._save_json("ajustes.json", s)

    def recent(self) -> list[dict]:
        return self._json("recientes.json", [])

    def add_recent(self, item: dict):
        with self._lock:
            r = [x for x in self.recent() if x.get("key") != item.get("key")]
            r.insert(0, item)
            self._save_json("recientes.json", r[:120])

    # iconos
    def icon_file(self, icon_id: str) -> str:
        prefix, name = icon_id.split(":", 1)
        return os.path.join(self.icons, re.sub(r"[^a-z0-9-]", "_", prefix), re.sub(r"[^a-z0-9-]", "_", name) + ".svg")

    def meta_file(self) -> str:
        return os.path.join(self.icons, "colecciones.json")


# --------------------------------------------------------------- Iconify

_collections: dict = {}


def _remember_collections(store: Store, cols: dict):
    if not cols:
        return
    with store._lock:
        if not _collections:
            _collections.update(store._json(os.path.relpath(store.meta_file(), store.root), {}))
        changed = False
        for k, v in cols.items():
            lic = (v.get("license") or {})
            info = {"name": v.get("name", k), "license": lic.get("spdx", ""), "license_title": lic.get("title", ""),
                    "author": (v.get("author") or {}).get("name", ""), "palette": bool(v.get("palette"))}
            if _collections.get(k) != info:
                _collections[k] = info
                changed = True
        if changed:
            store._save_json(os.path.relpath(store.meta_file(), store.root), _collections)


def collection_info(store: Store, prefix: str) -> dict:
    if not _collections:
        with store._lock:
            _collections.update(store._json(os.path.relpath(store.meta_file(), store.root), {}))
    return _collections.get(prefix, {})


def license_ok(info: dict) -> bool:
    return info.get("license", "") in OK_LICENSES


def search_icons(store: Store, query: str, start: int = 0, limit: int = 96, stickers: bool = False) -> dict:
    """Busca en Iconify. Devuelve {"ids": [...], "more": bool}. Filtra licencias y colecciones animadas."""
    q = to_query(query)
    params = {"query": q, "limit": max(32, min(999, start + limit)), "start": 0}
    if stickers:
        params["prefixes"] = ",".join(STICKER_PREFIXES)
    j = http_json(f"{ICONIFY}/search?" + urllib.parse.urlencode(params))
    _remember_collections(store, j.get("collections") or {})
    ids = []
    for i in j.get("icons", []):
        prefix = i.split(":", 1)[0]
        if prefix in SKIP_PREFIXES:
            continue
        if not license_ok(collection_info(store, prefix)):
            continue
        ids.append(i)
    total = int(j.get("total", len(j.get("icons", []))))
    page = ids[start:start + limit]
    return {"ids": page, "more": total >= params["limit"] and params["limit"] < 999}


def _svg_from(data: dict, name: str) -> str | None:
    icons = data.get("icons") or {}
    aliases = data.get("aliases") or {}
    ic = icons.get(name)
    extra = {}
    seen = 0
    while ic is None and name in aliases and seen < 5:
        al = aliases[name]
        extra.update({k: v for k, v in al.items() if k != "parent"})
        name = al.get("parent", "")
        ic = icons.get(name)
        seen += 1
    if ic is None:
        return None
    ic = dict(ic, **{k: v for k, v in extra.items() if k in ("width", "height", "left", "top")})
    if extra.get("rotate") or extra.get("hFlip") or extra.get("vFlip") or ic.get("rotate") or ic.get("hFlip") \
            or ic.get("vFlip"):
        return None  # con transformaciones: mejor pedir el SVG ya resuelto
    w = ic.get("width", data.get("width", 16))
    h = ic.get("height", data.get("height", 16))
    left, top = ic.get("left", data.get("left", 0)), ic.get("top", data.get("top", 0))
    body = ic.get("body", "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="{left} {top} {w} {h}">{body}</svg>')


def fetch_icons(store: Store, ids: list[str]) -> dict[str, str]:
    """SVG de varios iconos (de la caché o de Iconify, agrupados por colección)."""
    out, missing = {}, {}
    for i in ids:
        f = store.icon_file(i)
        if os.path.exists(f):
            try:
                with open(f, encoding="utf-8") as fh:
                    out[i] = fh.read()
                continue
            except Exception:
                pass
        prefix, name = i.split(":", 1)
        missing.setdefault(prefix, []).append(name)
    for prefix, names in missing.items():
        for k in range(0, len(names), 60):
            chunk = names[k:k + 60]
            try:
                data = http_json(f"{ICONIFY}/{urllib.parse.quote(prefix)}.json?icons=" +
                                 urllib.parse.quote(",".join(chunk)))
            except Exception:
                data = {}
            for n in chunk:
                svg = _svg_from(data, n) if data else None
                if svg is None:
                    try:
                        svg = http_get(f"{ICONIFY}/{urllib.parse.quote(prefix)}/{urllib.parse.quote(n)}.svg"
                                       ).decode("utf-8")
                    except Exception:
                        continue
                if "<animate" in svg or "<set " in svg:
                    continue
                iid = f"{prefix}:{n}"
                f = store.icon_file(iid)
                os.makedirs(os.path.dirname(f), exist_ok=True)
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(svg)
                out[iid] = svg
    return out


def icon_credit(store: Store, icon_id: str) -> str:
    info = collection_info(store, icon_id.split(":", 1)[0])
    parts = [info.get("name") or icon_id.split(":", 1)[0]]
    if info.get("author") and info["author"] not in parts:
        parts.append(info["author"])
    if info.get("license"):
        parts.append(info.get("license_title") or info["license"])
    return " · ".join(parts) + " (Iconify)"


def needs_attribution(lic: str) -> bool:
    lic = lic.upper()
    return "CC-BY" in lic or lic.startswith("BY")


# --------------------------------------------------------------- SVG → vector de Graphics

NAMED = {"black": "#000000", "white": "#FFFFFF", "red": "#FF0000", "green": "#008000", "blue": "#0000FF",
         "yellow": "#FFFF00", "gray": "#808080", "grey": "#808080", "orange": "#FFA500", "purple": "#800080",
         "pink": "#FFC0CB", "gold": "#FFD700", "silver": "#C0C0C0", "brown": "#A52A2A", "navy": "#000080",
         "teal": "#008080", "lime": "#00FF00", "cyan": "#00FFFF", "magenta": "#FF00FF", "maroon": "#800000",
         "olive": "#808000", "darkgray": "#A9A9A9", "lightgray": "#D3D3D3", "transparent": "none"}

INHERIT = ("fill", "stroke", "stroke-width", "fill-rule", "fill-opacity", "stroke-opacity", "color")


class Unsupported(Exception):
    pass


def _color(v: str, current: str) -> str | None:
    """Color SVG → «#RRGGBB» (None = sin pintura)."""
    v = (v or "").strip()
    lv = v.lower()
    if not v or lv == "none":
        return None
    if lv == "currentcolor":
        return current
    if lv.startswith("url("):
        raise Unsupported("degradado")
    if lv in NAMED:
        return None if NAMED[lv] == "none" else NAMED[lv]
    if lv.startswith("#"):
        h = lv[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h[:3])
        if len(h) >= 6:
            return "#" + h[:6].upper()
        raise Unsupported(v)
    m = re.match(r"rgba?\(([^)]*)\)", lv)
    if m:
        p = [x.strip() for x in m.group(1).replace("/", ",").split(",") if x.strip()]
        vals = [round(float(x[:-1]) * 2.55) if x.endswith("%") else round(float(x)) for x in p[:3]]
        return "#" + "".join(f"{max(0, min(255, c)):02X}" for c in vals)
    raise Unsupported(v)


def _num(v, default=0.0) -> float:
    if v is None:
        return default
    m = re.match(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)", str(v))
    return float(m.group(1)) if m else default


def _style(el, parent: dict) -> dict:
    st = dict(parent)
    for k in INHERIT + ("opacity", "display", "visibility"):
        if el.get(k) is not None:
            st[k] = el.get(k)
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            k = k.strip()
            if k in INHERIT or k in ("opacity", "display", "visibility"):
                st[k] = v.strip()
    # la opacidad del grupo se multiplica (aproximación: se aplica a cada hijo)
    op = _num(el.get("opacity"), 1.0) if el.get("opacity") is not None else 1.0
    m = re.search(r"(?:^|;)\s*opacity\s*:\s*([^;]+)", el.get("style") or "")
    if m:
        op = _num(m.group(1), 1.0)
    st["_op"] = parent.get("_op", 1.0) * op
    st.pop("opacity", None)
    return st


def _mat_mul(a, b):
    """Matrices afines (a, b, c, d, e, f) como en SVG: a·b."""
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (a0 * b0 + a2 * b1, a1 * b0 + a3 * b1, a0 * b2 + a2 * b3, a1 * b2 + a3 * b3,
            a0 * b4 + a2 * b5 + a4, a1 * b4 + a3 * b5 + a5)


ID = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _transform(s: str):
    m = ID
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = [float(x) for x in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            a = math.radians(v[0])
            c, s_ = math.cos(a), math.sin(a)
            t = (c, s_, -s_, c, 0, 0)
            if len(v) == 3:
                t = _mat_mul(_mat_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = _mat_mul(m, t)
    return m


class _PathReader:
    def __init__(self, d: str):
        self.d = d
        self.i = 0

    def skip(self):
        while self.i < len(self.d) and self.d[self.i] in " \t\r\n,":
            self.i += 1

    def at_end(self):
        self.skip()
        return self.i >= len(self.d)

    def has_num(self):
        self.skip()
        return self.i < len(self.d) and self.d[self.i] in "+-.0123456789"

    _NUM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")

    def num(self) -> float:
        self.skip()
        m = self._NUM.match(self.d, self.i)
        if not m:
            raise Unsupported("número")
        self.i = m.end()
        return float(m.group())

    def flag(self) -> int:
        self.skip()
        c = self.d[self.i:self.i + 1]
        if c not in ("0", "1"):
            raise Unsupported("bandera de arco")
        self.i += 1
        return int(c)


def _arc(x1, y1, rx, ry, phi, fa, fs, x2, y2):
    """Arco elíptico SVG → curvas cúbicas (apéndice F.6 de la especificación)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-9 or ry < 1e-9:
        return [(x1, y1, x2, y2, x2, y2)]
    p = math.radians(phi)
    cp, sp = math.cos(p), math.sin(p)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if fa == fs:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    n = max(1, math.ceil(abs(dt) / (math.pi / 2) - 1e-9))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    out = []
    t = t1
    for _ in range(n):
        c1, s1 = math.cos(t), math.sin(t)
        c2, s2 = math.cos(t + step), math.sin(t + step)
        e1 = (c1 - k * s1, s1 + k * c1)
        e2 = (c2 + k * s2, s2 - k * c2)
        pts = []
        for ex, ey in (e1, e2, (c2, s2)):
            X, Y = rx * ex, ry * ey
            pts += [cp * X - sp * Y + cx, sp * X + cp * Y + cy]
        out.append(tuple(pts))
        t += step
    return out


def path_segments(d: str) -> list[tuple]:
    """«d» de SVG completo (relativo, H/V/S/Q/T/A…) → segmentos absolutos M, L, C y Z."""
    r = _PathReader(d)
    segs = []
    cx = cy = sx = sy = 0.0
    last_c = None  # último punto de control (para S y T)
    last_q = None
    cmd = None
    while not r.at_end():
        ch = r.d[r.i]
        if ch.isalpha():
            cmd = ch
            r.i += 1
        elif cmd is None:
            raise Unsupported("trazado sin orden")
        rel = cmd.islower()
        C = cmd.upper()
        if C == "Z":
            segs.append(("Z",))
            cx, cy = sx, sy
            last_c = last_q = None
            cmd = None if not r.has_num() else ("l" if rel else "L")
            continue
        first = True
        while first or r.has_num():
            first = False
            ox, oy = (cx, cy) if rel else (0.0, 0.0)
            if C == "M":
                x, y = r.num() + ox, r.num() + oy
                segs.append(("M", x, y))
                cx, cy, sx, sy = x, y, x, y
                C, cmd = "L", ("l" if rel else "L")
                last_c = last_q = None
            elif C == "L":
                x, y = r.num() + ox, r.num() + oy
                segs.append(("L", x, y))
                cx, cy = x, y
                last_c = last_q = None
            elif C == "H":
                x = r.num() + (cx if rel else 0.0)
                segs.append(("L", x, cy))
                cx = x
                last_c = last_q = None
            elif C == "V":
                y = r.num() + (cy if rel else 0.0)
                segs.append(("L", cx, y))
                cy = y
                last_c = last_q = None
            elif C == "C":
                x1, y1 = r.num() + ox, r.num() + oy
                x2, y2 = r.num() + ox, r.num() + oy
                x, y = r.num() + ox, r.num() + oy
                segs.append(("C", x1, y1, x2, y2, x, y))
                cx, cy, last_c, last_q = x, y, (x2, y2), None
            elif C == "S":
                x1, y1 = (2 * cx - last_c[0], 2 * cy - last_c[1]) if last_c else (cx, cy)
                x2, y2 = r.num() + ox, r.num() + oy
                x, y = r.num() + ox, r.num() + oy
                segs.append(("C", x1, y1, x2, y2, x, y))
                cx, cy, last_c, last_q = x, y, (x2, y2), None
            elif C in ("Q", "T"):
                if C == "Q":
                    qx, qy = r.num() + ox, r.num() + oy
                else:
                    qx, qy = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
                x, y = r.num() + ox, r.num() + oy
                segs.append(("C", cx + 2 / 3 * (qx - cx), cy + 2 / 3 * (qy - cy),
                             x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), x, y))
                cx, cy, last_q, last_c = x, y, (qx, qy), None
            elif C == "A":
                rx, ry, phi = r.num(), r.num(), r.num()
                fa, fs = r.flag(), r.flag()
                x, y = r.num() + ox, r.num() + oy
                for c in _arc(cx, cy, rx, ry, phi, fa, fs, x, y):
                    segs.append(("C",) + c)
                cx, cy = x, y
                last_c = last_q = None
            else:
                raise Unsupported(f"orden {cmd}")
    return segs


def _fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def segments_d(segs, m=ID) -> str:
    a, b, c, d_, e, f = m

    def P(x, y):
        return f"{_fmt(a * x + c * y + e)} {_fmt(b * x + d_ * y + f)}"

    out = []
    for s in segs:
        if s[0] == "Z":
            out.append("Z")
        elif s[0] in "ML":
            out.append(f"{s[0]} {P(s[1], s[2])}")
        else:
            out.append(f"C {P(s[1], s[2])} {P(s[3], s[4])} {P(s[5], s[6])}")
    return " ".join(out)


def _shape_d(tag: str, el) -> str | None:
    g = lambda k, dv=0.0: _num(el.get(k), dv)  # noqa: E731
    k = 0.5522847498
    if tag == "path":
        return el.get("d") or None
    if tag == "rect":
        x, y, w, h = g("x"), g("y"), g("width"), g("height")
        if w <= 0 or h <= 0:
            return None
        rx = el.get("rx")
        ry = el.get("ry")
        rx = _num(rx if rx is not None else ry, 0.0)
        ry = _num(ry if ry is not None else el.get("rx"), 0.0)
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        if rx <= 0 or ry <= 0:
            return f"M{x} {y}H{x + w}V{y + h}H{x}Z"
        return (f"M{x + rx} {y}H{x + w - rx}A{rx} {ry} 0 0 1 {x + w} {y + ry}V{y + h - ry}"
                f"A{rx} {ry} 0 0 1 {x + w - rx} {y + h}H{x + rx}A{rx} {ry} 0 0 1 {x} {y + h - ry}"
                f"V{y + ry}A{rx} {ry} 0 0 1 {x + rx} {y}Z")
    if tag in ("circle", "ellipse"):
        cx, cy = g("cx"), g("cy")
        rx = g("r") if tag == "circle" else g("rx")
        ry = g("r") if tag == "circle" else g("ry")
        if rx <= 0 or ry <= 0:
            return None
        ox, oy = rx * k, ry * k
        return (f"M{cx - rx} {cy}C{cx - rx} {cy - oy} {cx - ox} {cy - ry} {cx} {cy - ry}"
                f"C{cx + ox} {cy - ry} {cx + rx} {cy - oy} {cx + rx} {cy}"
                f"C{cx + rx} {cy + oy} {cx + ox} {cy + ry} {cx} {cy + ry}"
                f"C{cx - ox} {cy + ry} {cx - rx} {cy + oy} {cx - rx} {cy}Z")
    if tag == "line":
        return f"M{g('x1')} {g('y1')}L{g('x2')} {g('y2')}"
    if tag in ("polyline", "polygon"):
        v = re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", el.get("points", ""))
        if len(v) < 4:
            return None
        pts = [f"{v[i]} {v[i + 1]}" for i in range(0, len(v) - 1, 2)]
        return "M" + "L".join(pts) + ("Z" if tag == "polygon" else "")
    return None


def _with_alpha(col: str, a: float) -> str:
    a = max(0.0, min(1.0, a))
    return col if a >= 0.999 else col + f"{round(a * 255):02X}"


def svg_to_vector(svg: str, color: str = "#05070F") -> dict:
    """SVG (icono) → {"viewbox": [w, h], "paths": [{fill, d, rule, stroke, stroke_w}]} en coordenadas 0..w.
    Lanza `Unsupported` con degradados, máscaras, recortes, <use>, texto o imágenes: entonces se rasteriza."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as ex:
        raise Unsupported(str(ex))

    def tag(e):
        return e.tag.split("}", 1)[-1]

    vb = [float(x) for x in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", root.get("viewBox", ""))]
    if len(vb) == 4:
        x0, y0, vw, vh = vb
    else:
        x0, y0, vw, vh = 0.0, 0.0, _num(root.get("width"), 24), _num(root.get("height"), 24)
    if vw <= 0 or vh <= 0:
        raise Unsupported("sin tamaño")
    base = {"fill": "#000000", "stroke": "none", "stroke-width": "1", "fill-rule": "nonzero", "_op": 1.0,
            "color": color}
    paths: list[dict] = []

    def walk(e, st, m):
        t = tag(e)
        if t in ("defs", "title", "desc", "metadata", "symbol"):
            return
        if t in ("use", "image", "text", "foreignObject", "pattern", "filter", "style", "switch"):
            raise Unsupported(t)
        if e.get("mask") or e.get("clip-path") or e.get("filter") or "url(" in (e.get("style") or ""):
            raise Unsupported("máscara")
        st = _style(e, st)
        if st.get("display") == "none" or st.get("visibility") == "hidden":
            return
        if e.get("transform"):
            m = _mat_mul(m, _transform(e.get("transform")))
        if t in ("svg", "g", "a"):
            for ch in e:
                walk(ch, st, m)
            return
        d = _shape_d(t, e)
        if not d:
            return
        cur = _color(st.get("color", color), color) or color
        fill = _color(st.get("fill", "#000000"), cur)
        stroke = _color(st.get("stroke", "none"), cur)
        sw = _num(st.get("stroke-width"), 1.0)
        if t in ("line", "polyline"):
            fill = None if t == "line" else fill
        if fill is None and (stroke is None or sw <= 0):
            return
        op = st.get("_op", 1.0)
        segs = path_segments(d)
        if not segs:
            return
        mm = _mat_mul((1, 0, 0, 1, -x0, -y0), m)
        sp = {"d": segments_d(segs, mm),
              "fill": _with_alpha(fill, op * _num(st.get("fill-opacity"), 1.0)) if fill else "#00000000",
              "rule": "evenodd" if st.get("fill-rule", "nonzero").strip() == "evenodd" else "nonzero"}
        if stroke and sw > 0:
            scale = math.sqrt(abs(mm[0] * mm[3] - mm[1] * mm[2])) or 1.0
            sp["stroke"] = _with_alpha(stroke, op * _num(st.get("stroke-opacity"), 1.0))
            sp["stroke_w"] = round(sw * scale, 4)
        paths.append(sp)

    walk(root, base, ID)
    if not paths:
        raise Unsupported("vacío")
    return {"viewbox": [vw, vh], "paths": paths}


def recolor_svg(svg: str, color: str) -> str:
    return re.sub(r"currentColor", color, svg)


def render_svg_png(svg: str, path: str, side: int = 1024) -> str:
    """Rasteriza un SVG (para los que no se pueden convertir a vector)."""
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    sz = r.defaultSize()
    w, h = max(1, sz.width()), max(1, sz.height())
    k = side / max(w, h)
    img = QImage(max(1, round(w * k)), max(1, round(h * k)), QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, img.width(), img.height()))
    p.end()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG")
    return path


def svg_thumb(svg: str, side: int = 96):
    """QImage cuadrada con el SVG centrado (vale fuera del hilo principal)."""
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    img = QImage(side, side, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    if not r.isValid():
        return img
    sz = r.defaultSize()
    w, h = max(1, sz.width()), max(1, sz.height())
    pad = side * 0.12
    k = (side - 2 * pad) / max(w, h)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF((side - w * k) / 2, (side - h * k) / 2, w * k, h * k))
    p.end()
    return img


# --------------------------------------------------------------- fotos

def search_photos(store: Store, query: str, page: int = 1, source: str = "openverse") -> dict:
    """{"items": [{key, source, thumb, url, w, h, author, license, page}], "more": bool}."""
    q = to_query(query)
    if source == "pexels":
        key = store.settings().get("pexels_key", "")
        if not key:
            raise ValueError("Falta la clave de Pexels")
        j = http_json(f"{PEXELS}?" + urllib.parse.urlencode({"query": q, "per_page": 40, "page": page}),
                      {"Authorization": key})
        items = [{"key": f"pexels:{p['id']}", "source": "pexels", "thumb": p["src"]["medium"],
                  "url": p["src"].get("large2x") or p["src"]["original"], "w": p["width"], "h": p["height"],
                  "author": p.get("photographer", ""), "license": "Pexels", "page": p.get("url", "")}
                 for p in j.get("photos", [])]
        return {"items": items, "more": bool(j.get("next_page"))}
    j = http_json(OPENVERSE + "?" + urllib.parse.urlencode(
        {"q": q, "license_type": "commercial,modification", "page_size": 20, "page": page}))
    items = []
    for r in j.get("results", []):
        lic = (r.get("license") or "").upper()
        if "ND" in lic or "NC" in lic:
            continue
        items.append({"key": f"openverse:{r['id']}", "source": "openverse",
                      "thumb": r.get("thumbnail") or r.get("url"), "url": r.get("url"),
                      "w": r.get("width") or 0, "h": r.get("height") or 0, "author": r.get("creator") or "",
                      "license": ("CC " + lic + " " + (r.get("license_version") or "")).strip()
                      if lic not in ("CC0", "PDM") else lic,
                      "page": r.get("foreign_landing_url") or ""})
    return {"items": items, "more": page < int(j.get("page_count") or 1)}


def photo_credit(item: dict) -> str:
    src = "Pexels" if item.get("source") == "pexels" else "Openverse"
    parts = [p for p in (item.get("author"), item.get("license")) if p]
    return " · ".join(parts + [src])


def photo_file(store: Store, item: dict) -> str:
    h = hashlib.sha1(item["key"].encode()).hexdigest()[:16]
    ext = os.path.splitext(urllib.parse.urlparse(item.get("url", "")).path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    return os.path.join(store.photos, f"{item.get('source', 'foto')}_{h}{ext}")


def download_photo(store: Store, item: dict) -> str:
    path = photo_file(store, item)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    data = http_get(item["url"], timeout=60)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    with open(path + ".json", "w", encoding="utf-8") as f:
        json.dump({k: item.get(k) for k in ("key", "source", "author", "license", "page", "url")}, f,
                  ensure_ascii=False)
    return path


def thumb_bytes(url: str) -> bytes:
    return http_get(url, timeout=20, limit=8 << 20)


# --------------------------------------------------------------- fuentes de Google Fonts

# (familia, categoría) — selección amplia de Google Fonts (todas OFL o Apache)
FONTS = [
    # Serif
    ("Playfair Display", "Serif"), ("Cormorant Garamond", "Serif"), ("Cormorant", "Serif"),
    ("Lora", "Serif"), ("Merriweather", "Serif"), ("Libre Baskerville", "Serif"), ("EB Garamond", "Serif"),
    ("Crimson Text", "Serif"), ("DM Serif Display", "Serif"), ("Bodoni Moda", "Serif"), ("Cinzel", "Serif"),
    ("Prata", "Serif"), ("Marcellus", "Serif"), ("Italiana", "Serif"), ("Gilda Display", "Serif"),
    ("Abril Fatface", "Serif"), ("Spectral", "Serif"), ("Noto Serif", "Serif"), ("Libre Caslon Text", "Serif"),
    ("Fraunces", "Serif"), ("Young Serif", "Serif"), ("Bellefair", "Serif"), ("Forum", "Serif"),
    # Sans
    ("Montserrat", "Sans"), ("Poppins", "Sans"), ("Inter", "Sans"), ("Raleway", "Sans"), ("Lato", "Sans"),
    ("Open Sans", "Sans"), ("Roboto", "Sans"), ("Nunito", "Sans"), ("Work Sans", "Sans"), ("DM Sans", "Sans"),
    ("Josefin Sans", "Sans"), ("Quicksand", "Sans"), ("Manrope", "Sans"), ("Outfit", "Sans"),
    ("Plus Jakarta Sans", "Sans"), ("Figtree", "Sans"), ("Urbanist", "Sans"), ("Jost", "Sans"),
    ("Archivo", "Sans"), ("Barlow", "Sans"), ("Barlow Condensed", "Sans"), ("Oswald", "Sans"),
    ("Kanit", "Sans"), ("Rubik", "Sans"), ("Mulish", "Sans"), ("Tenor Sans", "Sans"), ("Syne", "Sans"),
    ("Space Grotesk", "Sans"), ("League Spartan", "Sans"), ("Sora", "Sans"),
    # Manuscrita
    ("Great Vibes", "Manuscrita"), ("Dancing Script", "Manuscrita"), ("Pacifico", "Manuscrita"),
    ("Sacramento", "Manuscrita"), ("Allura", "Manuscrita"), ("Parisienne", "Manuscrita"),
    ("Alex Brush", "Manuscrita"), ("Pinyon Script", "Manuscrita"), ("Tangerine", "Manuscrita"),
    ("Satisfy", "Manuscrita"), ("Kaushan Script", "Manuscrita"), ("Caveat", "Manuscrita"),
    ("Homemade Apple", "Manuscrita"), ("Mrs Saint Delafield", "Manuscrita"), ("Italianno", "Manuscrita"),
    ("Petit Formal Script", "Manuscrita"), ("Rouge Script", "Manuscrita"), ("WindSong", "Manuscrita"),
    ("Birthstone", "Manuscrita"), ("Ephesis", "Manuscrita"), ("Corinthia", "Manuscrita"),
    ("Herr Von Muellerhoff", "Manuscrita"), ("Shadows Into Light", "Manuscrita"), ("Amatic SC", "Manuscrita"),
    ("Indie Flower", "Manuscrita"), ("Yellowtail", "Manuscrita"), ("Cookie", "Manuscrita"),
    ("Monsieur La Doulaise", "Manuscrita"),
    # Display
    ("Bebas Neue", "Display"), ("Anton", "Display"), ("Archivo Black", "Display"), ("Alfa Slab One", "Display"),
    ("Righteous", "Display"), ("Lobster", "Display"), ("Bungee", "Display"), ("Russo One", "Display"),
    ("Permanent Marker", "Display"), ("Black Ops One", "Display"), ("Teko", "Display"), ("Staatliches", "Display"),
    ("Monoton", "Display"), ("Fredoka", "Display"), ("Baloo 2", "Display"), ("Luckiest Guy", "Display"),
    ("Bangers", "Display"), ("Titan One", "Display"), ("Passion One", "Display"), ("Ultra", "Display"),
    ("Chewy", "Display"), ("Rye", "Display"), ("Graduate", "Display"), ("Big Shoulders Display", "Display"),
    # Monoespaciada
    ("JetBrains Mono", "Mono"), ("Space Mono", "Mono"), ("IBM Plex Mono", "Mono"), ("Courier Prime", "Mono"),
]


def font_dir(store: Store, family: str) -> str:
    return os.path.join(store.fonts, re.sub(r"[^A-Za-z0-9]+", "_", family).strip("_"))


def font_installed(store: Store, family: str) -> bool:
    d = font_dir(store, family)
    return os.path.isdir(d) and any(f.endswith(".ttf") for f in os.listdir(d))


def download_font(store: Store, family: str) -> list[str]:
    """Descarga las variantes normal y negrita (y cursivas si las hay) en TTF. Devuelve los archivos."""
    d = font_dir(store, family)
    if font_installed(store, family):
        return [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".ttf")]
    fam = family.replace(" ", "+")
    css = ""
    for spec in (f"{fam}:ital,wght@0,400;0,700;1,400;1,700", f"{fam}:wght@400;700", fam):
        try:
            css = http_get(f"{GFONTS_CSS}?family={spec}").decode("utf-8")
            break
        except Exception:
            continue
    urls = re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)", css)
    if not urls:
        raise ValueError(f"No se encontró «{family}» en Google Fonts")
    os.makedirs(d, exist_ok=True)
    out = []
    blocks = re.findall(r"@font-face\s*{([^}]*)}", css)
    for b in blocks:
        u = re.search(r"url\((https://[^)]+\.ttf)\)", b)
        if not u:
            continue
        style = re.search(r"font-style:\s*(\w+)", b)
        weight = re.search(r"font-weight:\s*(\d+)", b)
        name = f"{weight.group(1) if weight else '400'}{'i' if style and style.group(1) == 'italic' else ''}.ttf"
        path = os.path.join(d, name)
        if not os.path.exists(path):
            data = http_get(u.group(1), timeout=60)
            with open(path + ".part", "wb") as f:
                f.write(data)
            os.replace(path + ".part", path)
        out.append(path)
    return out


def installed_font_files(store: Store) -> list[str]:
    """Fuentes descargadas y añadidas por el usuario (no las muestras de la lista)."""
    out = []
    for root, dirs, files in os.walk(store.fonts):
        dirs[:] = [x for x in dirs if x != SAMPLES]
        out += [os.path.join(root, f) for f in files if f.lower().endswith(FONT_EXT)]
    return out


# --------------------------------------------------------------- catálogo completo (Fontsource)
# api.fontsource.org: el catálogo entero de Google Fonts (≈2 000 familias) y algunas libres más, sin clave.
# Todas con licencia libre (OFL, Apache, UFL, MIT, CC0): valen para trabajos de clientes.
FONTSOURCE = "https://api.fontsource.org/v1/fonts"
FONTSOURCE_CDN = "https://cdn.jsdelivr.net/fontsource/fonts"
FONT_CATS = {"serif": "Serif", "sans-serif": "Sans", "handwriting": "Manuscrita", "display": "Display",
             "monospace": "Mono", "other": "Display"}
FONT_EXT = (".ttf", ".otf")
SAMPLES = "_muestras"
USER_FONTS = "_mias"
CATALOG_DAYS = 7


def font_catalog(store: Store, refresh: bool = False) -> list[dict]:
    """[{family, id, cat, type, weights, styles, license}] ordenado por nombre. Se guarda una semana en disco;
    sin conexión, el guardado (aunque sea viejo) o la selección fija `FONTS`."""
    import time
    path = os.path.join(store.fonts, "catalogo.json")
    cached = None
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if not refresh and time.time() - cached.get("t", 0) < CATALOG_DAYS * 86400:
            return cached["fonts"]
    except Exception:
        pass
    try:
        raw = http_json(FONTSOURCE)
        fonts = []
        for x in raw:
            if x.get("category") == "icons" or "latin" not in (x.get("subsets") or []):
                continue
            fonts.append({"family": x["family"], "id": x["id"], "cat": FONT_CATS.get(x.get("category"), "Display"),
                          "type": x.get("type", "google"), "weights": x.get("weights") or [400],
                          "styles": x.get("styles") or ["normal"], "license": x.get("license", "")})
        try:
            fonts += fontshare_fonts()
        except Exception:
            pass
        seen, uniq = set(), []
        for f in fonts:
            k = f["family"].lower()
            if k not in seen:
                seen.add(k)
                uniq.append(f)
        fonts = sorted(uniq, key=lambda f: f["family"].lower())
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "fonts": fonts}, f, ensure_ascii=False)
        os.replace(tmp, path)
        return fonts
    except Exception:
        if cached:
            return cached["fonts"]
        return [{"family": fam, "id": re.sub(r"[^a-z0-9]+", "-", fam.lower()).strip("-"), "cat": cat,
                 "type": "google", "weights": [400, 700], "styles": ["normal"], "license": "OFL-1.1"}
                for fam, cat in FONTS]


FONTSHARE_API = "https://api.fontshare.com/v2"
FONTSHARE_CATS = {"Sans": "Sans", "Serif": "Serif", "Display": "Display", "Handwritten": "Manuscrita",
                  "Script": "Manuscrita", "Mono": "Mono", "Monospace": "Mono", "Slab": "Serif"}


def fontshare_fonts() -> list[dict]:
    """Fontshare (Indian Type Foundry): ~100 familias con la ITF Free Font License, que permite uso comercial."""
    out, page = [], 1
    while page < 6:
        d = http_json(f"{FONTSHARE_API}/fonts?limit=100&page={page}")
        for x in d.get("fonts", []):
            if x.get("script", "latin") != "latin":
                continue
            st = x.get("styles") or []
            ws = sorted({s["weight"]["number"] for s in st if not s.get("is_variable")}) or [400]
            out.append({"family": x["name"], "id": x["slug"], "cat": FONTSHARE_CATS.get(x.get("category"), "Sans"),
                        "type": "fontshare", "weights": ws,
                        "styles": ["normal"] + (["italic"] if any(s.get("is_italic") for s in st) else []),
                        "license": "ITF FFL (uso comercial)"})
        if not d.get("has_more"):
            break
        page += 1
    return out


def _fontshare_ttf(item: dict, weight: int, italic: bool) -> str:
    """URL del TTF de un peso de Fontshare (su CSS da woff2, woff y ttf; el peso cursivo es número + 1)."""
    w = weight + (1 if italic else 0)
    css = http_get(f"{FONTSHARE_API}/css?f[]={item['id']}@{w}").decode("utf-8")
    u = re.findall(r"url\('?(//cdn\.fontshare\.com/[^)']+\.ttf)", css)
    if not u:
        raise ValueError(f"Fontshare no tiene «{item['family']}» en {w}")
    return "https:" + u[0]


def _sample_variant(item: dict) -> str:
    ws = item.get("weights") or [400]
    w = 400 if 400 in ws else min(ws, key=lambda v: abs(v - 400))
    st = "normal" if "normal" in (item.get("styles") or ["normal"]) else item["styles"][0]
    return f"latin-{w}-{st}"


def font_sample(store: Store, item: dict) -> bytes:
    """TTF pequeño (solo latín, un peso) para pintar el nombre de la fuente en la lista sin instalarla."""
    d = os.path.join(store.fonts, SAMPLES)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, re.sub(r"[^a-z0-9-]", "_", item["id"]) + ".ttf")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    if item.get("type") == "fontshare":
        ws = item.get("weights") or [400]
        url = _fontshare_ttf(item, min(ws, key=lambda v: abs(v - 400)), False)
    else:
        url = f"{FONTSOURCE_CDN}/{item['id']}@latest/{_sample_variant(item)}.ttf"
    data = http_get(url, timeout=30, limit=8 << 20)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    return data


def download_font_any(store: Store, item: dict) -> list[str]:
    """Descarga una fuente del catálogo: las de Google con todos sus alfabetos (CSS de Google Fonts); las demás de
    Fontsource o Fontshare (latín), en normal, negrita y sus cursivas si existen."""
    if item.get("type", "google") == "google":
        try:
            return download_font(store, item["family"])
        except Exception:
            pass   # p. ej. retirada de Google Fonts pero aún en Fontsource
    fam = item["family"]
    d = font_dir(store, fam)
    if font_installed(store, fam):
        return [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(FONT_EXT)]
    ws = item.get("weights") or [400]
    want = sorted({min(ws, key=lambda v: abs(v - 400)), min(ws, key=lambda v: abs(v - 700))})
    styles = [s for s in ("normal", "italic") if s in (item.get("styles") or ["normal"])] or ["normal"]
    os.makedirs(d, exist_ok=True)
    out = []
    for w in want:
        for st in styles:
            path = os.path.join(d, f"{w}{'i' if st == 'italic' else ''}.ttf")
            if not os.path.exists(path):
                if item.get("type") == "fontshare":
                    url = _fontshare_ttf(item, w, st == "italic")
                else:
                    url = f"{FONTSOURCE_CDN}/{item['id']}@latest/latin-{w}-{st}.ttf"
                data = http_get(url, timeout=60)
                with open(path + ".part", "wb") as f:
                    f.write(data)
                os.replace(path + ".part", path)
            out.append(path)
    return out


# licencias de fuentes propias: «libre» (vale para clientes), «personal» (solo uso personal) o «?» (sin saber)
_PERSONAL = ("personal use", "personal-use", "non-commercial", "noncommercial", "non commercial",
             "not for commercial", "uso personal", "no comercial", "demo version", "demo font", "free for personal",
             "shareware", "donationware", "commercial license", "commercial use please", "purchase")
_FREE = ("100% free", "public domain", "dominio público", "free for commercial", "commercial use is allowed",
         "free for personal and commercial", "sil open font license", "open font license", "cc0",
         "apache license", "mit license", "free for both personal and commercial")


def detect_license(texts: list[str], names: list[str] | None = None) -> str:
    """Lo que dicen los «léeme» de un zip (DaFont los trae casi siempre) y los nombres de archivo."""
    t = " ".join(texts).lower()
    n = " ".join(names or []).lower()
    if any(k in t for k in _FREE[:7]) and not any(k in t for k in _PERSONAL[:9]):
        return "libre"
    if any(k in t for k in _PERSONAL) or re.search(r"\b(demo|personal|trial)\b", n):
        return "personal"
    if any(k in t for k in _FREE):
        return "libre"
    return "?"


def _meta_path(store: Store) -> str:
    return os.path.join(store.fonts, USER_FONTS, "licencias.json")


def font_meta(store: Store) -> dict:
    """{archivo: {"lic": libre|personal|?, "src": origen}} de las fuentes propias."""
    try:
        with open(_meta_path(store), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def set_font_meta(store: Store, path: str, **kw) -> None:
    with store._lock:
        m = font_meta(store)
        m.setdefault(os.path.basename(path), {}).update(kw)
        os.makedirs(os.path.dirname(_meta_path(store)), exist_ok=True)
        tmp = _meta_path(store) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _meta_path(store))


def zip_has_fonts(path: str) -> bool:
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.lower().endswith(FONT_EXT) and "__MACOSX" not in n for n in z.namelist())
    except Exception:
        return False


def import_fonts(store: Store, paths: list[str], src: str = "") -> list[str]:
    """Copia fuentes propias (.ttf/.otf, o un .zip como los de DaFont o Fontshare) a `fuentes/_mias` y anota su
    licencia según el «léeme». El original no se toca. Devuelve los archivos copiados."""
    import shutil
    import zipfile
    d = os.path.join(store.fonts, USER_FONTS)
    os.makedirs(d, exist_ok=True)
    out = []

    def dest(name):
        base = re.sub(r"[^\w.\- ]+", "_", os.path.basename(name)).strip() or "fuente.ttf"
        return os.path.join(d, base)
    for p in paths:
        low = p.lower()
        got, texts = [], []
        if low.endswith(FONT_EXT):
            t = dest(p)
            if os.path.abspath(t) != os.path.abspath(p):
                shutil.copyfile(p, t)
            got.append(t)
            folder = os.path.dirname(p)
            for f in os.listdir(folder) if os.path.isdir(folder) else []:
                if f.lower().endswith((".txt", ".md")) and os.path.getsize(os.path.join(folder, f)) < 200_000 \
                        and os.path.splitext(f)[0].lower() in ("readme", "leeme", "license", "licence", "info"):
                    with open(os.path.join(folder, f), encoding="utf-8", errors="replace") as fh:
                        texts.append(fh.read())
        elif low.endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    n = info.filename
                    if info.is_dir() or "__MACOSX" in n or info.file_size > 40 << 20:
                        continue
                    if n.lower().endswith((".txt", ".md", ".rtf", ".html", ".htm")) and info.file_size < 200_000:
                        texts.append(z.read(info).decode("utf-8", "replace"))
                        continue
                    if not n.lower().endswith(FONT_EXT):
                        continue
                    t = dest(n)
                    with z.open(info) as s_, open(t, "wb") as f:
                        shutil.copyfileobj(s_, f)
                    got.append(t)
        lic = detect_license(texts, [os.path.basename(x) for x in got] + [os.path.basename(p)])
        for t in got:
            set_font_meta(store, t, lic=lic, src=src or os.path.basename(p))
        out += got
    return out


# --------------------------------------------------------------- instalar en Windows (solo este usuario)

def windows_install(path: str) -> str:
    """Instala una fuente para el usuario actual (sin administrador), como «Instalar» del Explorador: copia en
    %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts y la apunta en HKCU. Así sirve también en Word, Photoshop…
    Devuelve la ruta instalada ("" si no es Windows o ya estaba)."""
    import shutil
    import sys
    if sys.platform != "win32":
        return ""
    import ctypes
    import winreg
    d = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")
    os.makedirs(d, exist_ok=True)
    t = os.path.join(d, os.path.basename(path))
    if os.path.exists(t):
        return ""
    shutil.copyfile(path, t)
    name = os.path.splitext(os.path.basename(path))[0] + (" (OpenType)" if path.lower().endswith(".otf")
                                                          else " (TrueType)")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts") as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, t)
    try:
        ctypes.windll.gdi32.AddFontResourceW(t)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 0x0002, 1000, None)  # WM_FONTCHANGE
    except Exception:
        pass
    return t


# --------------------------------------------------------------- vigilar Descargas (DaFont y otras webs)

def downloads_dir() -> str:
    """Carpeta Descargas real (puede estar movida a otro disco)."""
    import sys
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                v = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
                return os.path.expandvars(v)
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Downloads")


def new_font_downloads(store: Store, since: float) -> list[str]:
    """Zips con fuentes y .ttf/.otf llegados a Descargas desde `since` que aún no se han importado."""
    d = downloads_dir()
    done = set(store.settings().get("descargas_importadas", []))
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for f in names:
        p = os.path.join(d, f)
        low = f.lower()
        if not low.endswith(FONT_EXT + (".zip",)) or low.endswith((".crdownload", ".part", ".tmp")):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = f"{f}|{int(st.st_mtime)}|{st.st_size}"
        if st.st_mtime < since or key in done or st.st_size == 0:
            continue
        if low.endswith(".zip") and not zip_has_fonts(p):
            continue
        out.append(p)
    return out


def mark_imported(store: Store, path: str) -> None:
    st = os.stat(path)
    key = f"{os.path.basename(path)}|{int(st.st_mtime)}|{st.st_size}"
    done = store.settings().get("descargas_importadas", [])
    store.set_setting("descargas_importadas", (done + [key])[-500:])


def user_font_files(store: Store) -> list[str]:
    d = os.path.join(store.fonts, USER_FONTS)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(FONT_EXT)]


def remove_user_font(store: Store, path: str) -> None:
    """Quita una fuente propia de PhootFlow (solo la copia de `_mias`; el archivo original no se toca)."""
    d = os.path.abspath(os.path.join(store.fonts, USER_FONTS))
    if os.path.dirname(os.path.abspath(path)) == d and os.path.exists(path):
        os.remove(path)
