"""Perfiles de cámara DCP (DNG Camera Profile, el formato de Adobe) para revelar los RAW. Sin Qt.

LibRaw solo aplica una matriz 3×3 por modelo de cámara; un DCP añade la matriz «forward» por iluminante y las
tablas de tono/saturación (`HueSatMap`, `LookTable`) que dan el color de «Adobe Standard» o de los perfiles
«Camera Matching» (Estándar, Vivo, Retrato… de la marca). Se aplican sobre el RGB de la cámara ya equilibrado
(`postprocess(output_color=raw)`) siguiendo la especificación DNG 1.6 (capítulo 6) y se devuelve sRGB lineal.
La curva tonal del perfil (`ProfileToneCurve`) no se usa: la curva base es la filmic de PhootFlow.

Perfiles: los que instala el Adobe DNG Converter o Lightroom (`%ProgramData%\\Adobe\\CameraRaw\\CameraProfiles`),
los del usuario (`%AppData%\\Adobe\\CameraRaw\\CameraProfiles`) y `%AppData%\\PhootFlow\\perfiles`.
"""
from __future__ import annotations

import os
import re
import struct
import threading

import cv2
import numpy as np

NONE = "Sin perfil (LibRaw)"
DEFAULT = "Adobe Standard"
AUTO = "Automático (el de la cámara)"  # el estilo elegido en la cámara al disparar (MakerNotes) -> su DCP

_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 7: ("B", 1), 8: ("h", 2),
          9: ("i", 4), 10: ("ii", 8), 11: ("f", 4), 12: ("d", 8)}
_ILLUM_K = {1: 5500, 2: 3500, 3: 2850, 4: 5500, 9: 5500, 10: 6500, 11: 7500, 12: 6430, 13: 5000, 14: 4150,
            15: 3450, 17: 2856, 18: 4874, 19: 6774, 20: 5503, 21: 6504, 22: 7504, 23: 5003, 24: 3200}
D50 = np.array([0.9642, 1.0, 0.8249])
XYZ_TO_PROPHOTO = np.array([[1.3459433, -0.2556075, -0.0511118],
                            [-0.5445989, 1.5081673, 0.0205351],
                            [0.0, 0.0, 1.2118128]])
PROPHOTO_TO_SRGB = np.array([[3.1338561, -1.6168667, -0.4906146],
                             [-0.9787684, 1.9161415, 0.0334540],
                             [0.0719453, -0.2289914, 1.4052427]]) @ np.linalg.inv(XYZ_TO_PROPHOTO)
_BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])


# ------------------------------------------------------------------ lectura

class Profile:
    """Un .dcp leído. Matrices 3×3 en numpy; tablas como (dims, datos float32)."""

    def __init__(self, path: str, tags: dict):
        self.path = path
        g = tags.get
        self.camera = (g(50708) or "").strip()
        self.name = (g(50936) or os.path.splitext(os.path.basename(path))[0]).strip()
        m3 = lambda t: np.array(t, float).reshape(3, 3) if t is not None and len(t) == 9 else None  # noqa: E731
        self.cm = [m3(g(50721)), m3(g(50722))]
        self.fm = [m3(g(50964)), m3(g(50965))]
        self.illum = [int((g(50778) or [0])[0]), int((g(50779) or [0])[0])]
        dims = g(50937)
        self.hsm_dims = tuple(int(v) for v in dims) if dims else None
        self.hsm = [_table(g(50938), self.hsm_dims), _table(g(50939), self.hsm_dims)]
        dims = g(50981)
        self.look_dims = tuple(int(v) for v in dims) if dims else None
        self.look = _table(g(50982), self.look_dims)
        self.hsm_srgb = int((g(51107) or [0])[0]) == 1
        self.look_srgb = int((g(51108) or [0])[0]) == 1
        self.exposure = float((g(51109) or [0.0])[0])

    @property
    def usable(self) -> bool:
        return self.cm[0] is not None or self.fm[0] is not None


def _table(data, dims):
    if data is None or not dims or len(dims) < 2:
        return None
    h, s = dims[0], dims[1]
    v = dims[2] if len(dims) > 2 and dims[2] else 1
    a = np.asarray(data, np.float32)
    if a.size != h * s * v * 3:
        return None
    return a.reshape(v, h, s, 3)


def read(path: str) -> Profile:
    with open(path, "rb") as f:
        b = f.read()
    if b[:4] == b"IIRC":
        e = "<"
    elif b[:4] == b"MMCR":
        e = ">"
    else:
        raise ValueError("no es un DCP")
    off = struct.unpack_from(e + "I", b, 4)[0]
    n = struct.unpack_from(e + "H", b, off)[0]
    tags = {}
    for i in range(n):
        tag, typ, cnt, val = struct.unpack_from(e + "HHI4s", b, off + 2 + 12 * i)
        if typ not in _TYPES:
            continue
        fmt, size = _TYPES[typ]
        total = size * cnt
        pos = struct.unpack_from(e + "I", val)[0] if total > 4 else off + 2 + 12 * i + 8
        if pos + total > len(b):
            continue
        if typ == 2:
            tags[tag] = b[pos:pos + cnt].split(b"\0")[0].decode("latin-1", "replace")
        elif typ in (5, 10):
            raw = struct.unpack_from(e + fmt[0] * (2 * cnt), b, pos)
            tags[tag] = [raw[k] / raw[k + 1] if raw[k + 1] else 0.0 for k in range(0, len(raw), 2)]
        else:
            tags[tag] = list(np.frombuffer(b, (e + {"f": "f4", "d": "f8", "I": "u4", "i": "i4", "H": "u2",
                                                    "h": "i2", "B": "u1"}[fmt]), cnt, pos)) \
                if cnt > 16 else list(struct.unpack_from(e + fmt * cnt, b, pos))
    return Profile(path, tags)


# ------------------------------------------------------------------ dónde están

def profile_dirs() -> list[str]:
    out = []
    pd = os.environ.get("ProgramData", r"C:\ProgramData")
    ad = os.environ.get("APPDATA", os.path.expanduser("~"))
    for d in (os.path.join(pd, "Adobe", "CameraRaw", "CameraProfiles"),
              os.path.join(ad, "Adobe", "CameraRaw", "CameraProfiles"),
              os.path.join(ad, "PhootFlow", "perfiles")):
        if os.path.isdir(d):
            out.append(d)
    extra = os.environ.get("PHOOTFLOW_DCP_DIR", "")
    if extra and os.path.isdir(extra):
        out.insert(0, extra)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_LOCK = threading.Lock()
_FILES: list = []                 # (ruta, ruta normalizada) de todos los .dcp
_PARSED: dict = {}                # ruta -> Profile (o None si no se puede leer)
_BY_CAMERA: dict = {}             # clave de cámara -> [Profile]


def _all_files() -> list:
    if not _FILES:
        for d in profile_dirs():
            for root, _, files in os.walk(d):
                for fn in files:
                    if fn.lower().endswith(".dcp"):
                        p = os.path.join(root, fn)
                        _FILES.append((p, _norm(os.path.relpath(p, d))))
        _FILES.append(("", ""))  # marca de «ya buscado» aunque no haya ninguno
    return [f for f in _FILES if f[0]]


def _parsed(path: str):
    if path not in _PARSED:
        try:
            _PARSED[path] = read(path)
        except Exception:
            _PARSED[path] = None
    return _PARSED[path]


def camera_keys(make: str, model: str) -> set:
    """Formas en que puede aparecer la cámara en `UniqueCameraModel` («Sony ILCE-6700», «Nikon Z 6_2»…)."""
    m, mk = _norm(model), _norm(make)
    first = _norm((make or "").split()[0]) if (make or "").split() else ""
    return {k for k in (m, first + m, mk + m) if k}


def profiles_for(make: str, model: str) -> list[Profile]:
    """Perfiles DCP de esa cámara (vacío si no hay ninguno instalado)."""
    keys = camera_keys(make, model)
    if not keys:
        return []
    ck = "|".join(sorted(keys))
    with _LOCK:
        if ck in _BY_CAMERA:
            return _BY_CAMERA[ck]
        first = _norm((make or "").split()[0]) if (make or "").split() else ""
        m = _norm(model)
        short = m[len(first):] if first and m.startswith(first) and len(m) > len(first) else m
        out = []
        for p, np_ in _all_files():
            if short in np_:  # filtro rápido por nombre; luego se confirma con la etiqueta de dentro
                pr = _parsed(p)
                if pr is not None and pr.usable and _norm(pr.camera) in keys:
                    out.append(pr)
        out.sort(key=lambda pr: (pr.name != DEFAULT, "monochrome" in pr.name.lower() or pr.name.endswith(" BW"),
                                 pr.name.lower()))
        seen, uniq = set(), []
        for pr in out:  # un mismo nombre en varias carpetas: manda el primero (el del usuario va antes)
            if pr.name not in seen:
                seen.add(pr.name)
                uniq.append(pr)
        _BY_CAMERA[ck] = uniq
        return uniq


def find(make: str, model: str, name: str) -> Profile | None:
    """El perfil `name` de esa cámara; si no lo tiene, Adobe Standard; si tampoco, None (se queda LibRaw)."""
    if not name or name == NONE:
        return None
    prs = profiles_for(make, model)
    for want in (name, DEFAULT):
        for pr in prs:
            if pr.name == want:
                return pr
    return None


# estilo de la cámara (como lo escribe en las MakerNotes) -> nombre del perfil «Camera …» de Adobe
_STYLE_ALIAS = {
    # Sony (Creative Style / Creative Look); «Off» = modo automático inteligente
    "standard": "st", "off": "st", "auto": "st", "none": "st", "portrait": "pt", "neutral": "nt", "vivid": "vv",
    "vivid2": "vv2", "film": "fl", "instant": "in", "softhighkey": "sh", "blackwhite": "bw",
    # Fujifilm (código de FilmMode ya traducido en engine)
    "provia": "proviastandard", "velvia": "velviavivid", "astia": "astiasoft", "eterna": "eternacinema",
}
_MONO = ("bw", "monochrome", "mono", "acros", "sepia", "se")
_STANDARD = ("st", "standard", "proviastandard")


def style_profile(make: str, model: str, style: str) -> Profile | None:
    """El perfil «Camera …» que corresponde al estilo de la cámara `style` (p. ej. «Standard», «VV2», «VIVID»).
    Los estilos de blanco y negro se revelan en color con el Estándar (el B/N lo hace Events aparte).
    Sin estilo reconocible: el Estándar de la cámara; si no hay perfiles de cámara, Adobe Standard."""
    prs = profiles_for(make, model)
    if not prs:
        return None
    cams = {}
    for pr in prs:
        if pr.name.startswith("Camera "):
            key = _norm(re.sub(r" v\d+$", "", pr.name[7:]))
            if key not in cams or pr.name > cams[key].name:  # «… v2» antes que el antiguo
                cams[key] = pr
    s = _norm(style)
    a = _STYLE_ALIAS.get(s, s)
    wants = [] if not s or any(x.startswith(m) for x in (s, a) for m in _MONO) else list(dict.fromkeys([s, a]))
    for want in wants + list(_STANDARD):
        if want in cams:
            return cams[want]
        if len(want) >= 4:
            hit = [k for k in cams if want in k or k in want and len(k) >= 4]
            hit = [k for k in hit if not any(k.startswith(m) for m in _MONO)]
            if hit:
                return cams[sorted(hit, key=len)[0]]
    return next((pr for pr in prs if pr.name == DEFAULT), None)


_FRIENDLY = {"ST": "Estándar", "VV": "Vívido", "VV2": "Vívido 2", "PT": "Retrato", "NT": "Neutro", "FL": "FL",
             "IN": "IN", "SH": "SH", "BW": "Blanco y negro", "Standard": "Estándar", "Vivid": "Vívido",
             "Portrait": "Retrato", "Neutral": "Neutro", "Landscape": "Paisaje", "Flat": "Plano",
             "Monochrome": "Monocromo"}


def label(name: str) -> str:
    """Nombre para la interfaz: «Camera ST» -> «Cámara · Estándar (ST)»."""
    if name in (NONE, AUTO):
        return name
    m = re.match(r"Camera (.+)$", name)
    if not m:
        return name
    rest = m.group(1)
    base = rest.split(" v")[0] if re.search(r" v\d+$", rest) else rest
    f = _FRIENDLY.get(base)
    return f"Cámara · {f} ({base})" if f and f != base else f"Cámara · {rest}"


def reset_cache() -> None:
    with _LOCK:
        _FILES.clear()
        _PARSED.clear()
        _BY_CAMERA.clear()


# ------------------------------------------------------------------ color

def _xy_to_temp(x: float, y: float) -> float:
    n = (x - 0.3320) / (0.1858 - y) if abs(0.1858 - y) > 1e-9 else 0.0
    return float(np.clip(449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33, 1500, 50000))


def _weight(pr: Profile, temp: float) -> float:
    """Peso del iluminante 1 (interpolación en 1/T, como el DNG SDK)."""
    t1, t2 = _ILLUM_K.get(pr.illum[0], 0), _ILLUM_K.get(pr.illum[1], 0)
    if not (t1 and t2) or t1 == t2 or pr.cm[1] is None and pr.fm[1] is None:
        return 1.0
    if t1 > t2:
        t1, t2, flip = t2, t1, True
    else:
        flip = False
    g = float(np.clip((1 / temp - 1 / t2) / (1 / t1 - 1 / t2), 0, 1))
    return 1 - g if flip else g


def _mix(a, b, g):
    if a is None:
        return b
    if b is None:
        return a
    return g * a + (1 - g) * b


def white_temp(pr: Profile, neutral: np.ndarray) -> float:
    """Temperatura del blanco de la toma (neutro de la cámara -> xy -> CCT), iterando con las ColorMatrix."""
    x, y = 0.3457, 0.3585
    for _ in range(30):
        t = _xy_to_temp(x, y)
        cm = _mix(pr.cm[0], pr.cm[1], _weight(pr, t))
        if cm is None:
            return 5003.0
        xyz = np.linalg.solve(cm, neutral)
        s = xyz.sum()
        if s <= 0:
            break
        nx, ny = xyz[0] / s, xyz[1] / s
        if abs(nx - x) + abs(ny - y) < 1e-7:
            x, y = nx, ny
            break
        x, y = nx, ny
    return _xy_to_temp(x, y)


def camera_to_prophoto(pr: Profile, neutral: np.ndarray) -> tuple[np.ndarray, float]:
    """Matriz del RGB de cámara ya equilibrado (blanco = 1,1,1) a ProPhoto lineal, y el peso del iluminante 1."""
    neutral = np.asarray(neutral, float) / max(float(np.max(neutral)), 1e-9)
    temp = white_temp(pr, neutral)
    g = _weight(pr, temp)
    fm = _mix(pr.fm[0], pr.fm[1], g)
    if fm is not None:
        m = fm * (D50 / (fm @ np.ones(3)))[:, None]  # (1,1,1) -> blanco D50 exacto
    else:  # sin ForwardMatrix: inversa de la ColorMatrix y adaptación de Bradford al D50
        cm = _mix(pr.cm[0], pr.cm[1], g)
        cam_to_xyz = np.linalg.inv(cm) @ np.diag(neutral)
        w = cam_to_xyz @ np.ones(3)
        w = w / w[1]
        ca = np.linalg.inv(_BRADFORD) @ np.diag((_BRADFORD @ D50) / (_BRADFORD @ w)) @ _BRADFORD
        m = ca @ cam_to_xyz
        m = m * (D50 / (m @ np.ones(3)))[:, None]
    return XYZ_TO_PROPHOTO @ m, g


def _srgb_encode(v):
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * np.power(np.maximum(v, 0), 1 / 2.4) - 0.055)


def _apply_table(pp: np.ndarray, tab: np.ndarray, srgb_val: bool) -> np.ndarray:
    """HueSatMap/LookTable sobre ProPhoto lineal (HSV del DNG: H 0..360, S, V = máx)."""
    vd, hd, sd, _ = tab.shape
    hsv = cv2.cvtColor(np.ascontiguousarray(pp, np.float32), cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # la tabla como imagen: filas = tono (con la primera repetida al final, el tono da la vuelta),
    # columnas = bloques de valor × saturación; cv2.remap interpola en bilineal
    img = np.concatenate([tab, tab[:, :1]], axis=1)            # (vd, hd+1, sd, 3)
    img = np.ascontiguousarray(img.transpose(1, 0, 2, 3).reshape(hd + 1, vd * sd, 3), np.float32)
    my = (h * (hd / 360.0)).astype(np.float32)
    mx = (np.clip(s, 0, 1) * (sd - 1)).astype(np.float32)
    rm = lambda xs: cv2.remap(img, xs, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)  # noqa: E731
    if vd > 1:
        ve = np.clip(v, 0, 1)
        if srgb_val:
            ve = _srgb_encode(ve)
        vi = (ve * (vd - 1)).astype(np.float32)
        v0 = np.minimum(np.floor(vi), vd - 2)
        f = (vi - v0)[..., None]
        a = rm(mx + v0 * sd)
        b = rm(mx + (v0 + 1) * sd)
        d = a + (b - a) * f
    else:
        d = rm(mx)
    h2 = np.mod(h + d[..., 0], 360.0)
    s2 = np.clip(s * d[..., 1], 0, 1)
    v2 = v * d[..., 2]
    return cv2.cvtColor(np.dstack([h2, s2, v2]).astype(np.float32), cv2.COLOR_HSV2RGB)


def apply(cam: np.ndarray, pr: Profile, neutral: np.ndarray, strip: int = 768) -> np.ndarray:
    """RGB de cámara equilibrado (float32, lineal) -> sRGB lineal 0..1 con el perfil `pr`. Por franjas (memoria)."""
    m, g = camera_to_prophoto(pr, neutral)
    hsm = None
    if pr.hsm[0] is not None or pr.hsm[1] is not None:
        a, b = pr.hsm
        hsm = (_mix(a, b, g) if a is not None and b is not None and a.shape == b.shape else (a if a is not None else b))
        hsm = hsm.astype(np.float32)
    gain = float(2.0 ** pr.exposure)
    m = m.astype(np.float32)
    to_srgb = PROPHOTO_TO_SRGB.astype(np.float32)
    out = np.empty_like(cam, dtype=np.float32)
    for y0 in range(0, cam.shape[0], strip):
        pp = cv2.transform(np.ascontiguousarray(cam[y0:y0 + strip], np.float32), m)
        np.maximum(pp, 0, out=pp)
        if hsm is not None:
            pp = _apply_table(pp, hsm, pr.hsm_srgb)
        if gain != 1.0:
            pp *= gain
        if pr.look is not None:
            pp = _apply_table(pp, pr.look, pr.look_srgb)
        out[y0:y0 + strip] = np.clip(cv2.transform(pp, to_srgb), 0, 1)
    return out
