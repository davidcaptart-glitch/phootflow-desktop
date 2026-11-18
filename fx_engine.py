"""Efectos «de plugin» de PhootFlow, sin interfaz (numpy + OpenCV + onnxruntime).

Ideas tomadas de plugins conocidos de Photoshop, con código y fórmulas propias:

- Ampliar con IA (como Blow Up): Real-ESRGAN General x4v3 (Xintao Wang et al., BSD-3-Clause), exportado a ONNX
  por Qualcomm AI Hub (BSD-3-Clause). Entrada fija de 128 × 128: se trabaja por bloques con solape. Sin el
  modelo, ampliación clásica Lanczos + enfoque.
- Partículas (como Real Snow): nieve, lluvia, confeti, pétalos, bokeh, destellos y polvo dorado. Capa RGBA.
- Filtros artísticos (Watercolor, Comic Oil Paint, Ink Flow…): acuarela, óleo, cómic, cómic al óleo, tinta
  que fluye, lápiz, lápiz de color y pop art (trama de puntos).
- Texturas (Texture Anarchy / Filter Forge): nubes, mármol, madera, piedra, papel, grunge, hormigón, metal
  cepillado, plasma, cáusticas, anarquía (ruido deformado de colores) y galaxia.
- Efectos (Eye Candy): cromo, oro, fuego, rayos, hielo y neón, en imagen RGBA.
- Bordes artísticos: rasgado, grunge, pincel, quemado y tinta (alfa para el borde de una foto).

Todas las medidas son relativas al lado largo, para que la vista previa y la exportación den lo mismo.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
import zipfile

import cv2
import numpy as np

# ------------------------------------------------------------------ utilidades


def _rng(seed) -> np.random.Generator:
    return np.random.default_rng(int(seed) & 0xFFFFFFFF)


def _hex(c: str) -> np.ndarray:
    c = (c or "#ffffff").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255.0


def _gray(img: np.ndarray) -> np.ndarray:
    return (0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]).astype(np.float32)


def _u8(img: np.ndarray) -> np.ndarray:
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)


def _f(img8: np.ndarray) -> np.ndarray:
    return img8.astype(np.float32) / 255.0


def value_noise(h: int, w: int, cell: float, seed: int) -> np.ndarray:
    """Ruido suave 0..1 con granos de `cell` píxeles."""
    gh, gw = max(2, int(h / max(cell, 0.5)) + 3), max(2, int(w / max(cell, 0.5)) + 3)
    n = _rng(seed).random((gh, gw), dtype=np.float32)
    big = cv2.resize(n, (int(gw * cell), int(gh * cell)) if cell > 1 else (w, h),
                     interpolation=cv2.INTER_CUBIC if cell > 1 else cv2.INTER_AREA)
    return np.clip(big[:h, :w], 0, 1) if big.shape[0] >= h and big.shape[1] >= w else \
        cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)


def fbm(h: int, w: int, scale: float, seed: int, octaves: int = 5, gain: float = 0.5) -> np.ndarray:
    """Ruido fractal 0..1 (suma de octavas). `scale` = tamaño del grano más grande en píxeles."""
    out = np.zeros((h, w), np.float32)
    amp, tot, cell = 1.0, 0.0, max(1.0, scale)
    for o in range(octaves):
        out += value_noise(h, w, cell, seed + o * 7919) * amp
        tot += amp
        amp *= gain
        cell = max(1.0, cell / 2)
    out /= tot
    lo, hi = np.percentile(out, (1, 99))
    return np.clip((out - lo) / max(1e-6, float(hi - lo)), 0, 1).astype(np.float32)


def _ramp(t: np.ndarray, stops: list[tuple[float, str]]) -> np.ndarray:
    """Degradado de colores: t 0..1 → RGB según las paradas [(pos, "#hex"), …]."""
    xs = np.array([s[0] for s in stops], np.float32)
    cs = np.stack([_hex(s[1]) for s in stops])
    return np.stack([np.interp(t, xs, cs[:, i]) for i in range(3)], -1).astype(np.float32)


# ------------------------------------------------------------------ ampliar con IA

UPSCALE_URL = ("https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/"
               "real_esrgan_general_x4v3/releases/v0.66.0/real_esrgan_general_x4v3-onnx-float.zip")
UPSCALE_SHA256 = "fc094329b24a00c936ebc1cc4c51bc59633e7dc36f4a553344342171cbd3c7cf"
UPSCALE_MB = 5
UPSCALE_DIR = "real_esrgan_general_x4v3"
UPSCALE_FILE = "real_esrgan_general_x4v3.onnx"
TILE, PAD = 128, 12
_UP = {"session": None, "lock": threading.Lock(), "dir": ""}


def models_dir() -> str:
    d = _UP["dir"]
    if not d:
        try:
            import masks as M
            d = M._CFG.get("model_dir") or ""
        except Exception:
            d = ""
    return d or os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PhootFlow", "modelos")


def configure(model_dir: str) -> None:
    _UP["dir"] = model_dir


def upscaler_path() -> str:
    return os.path.join(models_dir(), UPSCALE_DIR, UPSCALE_FILE)


def upscaler_ready() -> bool:
    p = upscaler_path()
    return os.path.exists(p) and os.path.exists(os.path.splitext(p)[0] + ".data")


def ensure_upscaler(progress=None) -> str:
    """Descarga el modelo de ampliación la primera vez (≈5 MB), comprobando su SHA-256."""
    if upscaler_ready():
        return upscaler_path()
    import urllib.request
    import net
    d = models_dir()
    os.makedirs(d, exist_ok=True)
    zp = os.path.join(d, UPSCALE_DIR + ".zip.part")
    resp = net.urlopen(urllib.request.Request(UPSCALE_URL, headers={"User-Agent": "PhootFlow"}), 60)
    total = int(resp.headers.get("Content-Length") or UPSCALE_MB * 1_000_000)
    h = hashlib.sha256()
    done = 0
    with open(zp, "wb") as f:
        while True:
            chunk = resp.read(1 << 18)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != UPSCALE_SHA256:
        os.remove(zp)
        raise RuntimeError("El modelo de ampliación no supera la verificación SHA-256")
    out = os.path.join(d, UPSCALE_DIR)
    os.makedirs(out, exist_ok=True)
    with zipfile.ZipFile(zp) as z:
        for info in z.infolist():
            name = os.path.basename(info.filename)
            if name.endswith((".onnx", ".data")):  # solo los dos ficheros del modelo, sin rutas del zip
                with z.open(info) as src, open(os.path.join(out, name), "wb") as dst:
                    dst.write(src.read())
    os.remove(zp)
    return upscaler_path()


def _session():
    with _UP["lock"]:
        if _UP["session"] is None:
            import onnxruntime as ort
            _UP["session"] = ort.InferenceSession(upscaler_path(), providers=["CPUExecutionProvider"])
    return _UP["session"]


def upscale_tiles(h: int, w: int) -> int:
    s = TILE - 2 * PAD
    return math.ceil(h / s) * math.ceil(w / s)


def upscale_seconds(h: int, w: int) -> float:
    """Tiempo aproximado con IA (≈0,11 s por bloque en un portátil)."""
    return upscale_tiles(h, w) * 0.11


def _esrgan_x4(rgb: np.ndarray, progress=None, cancel=None) -> np.ndarray:
    sess = _session()
    H, W = rgb.shape[:2]
    S = TILE - 2 * PAD
    ny, nx = math.ceil(H / S), math.ceil(W / S)
    pad = cv2.copyMakeBorder(np.clip(rgb, 0, 1).astype(np.float32), PAD, ny * S - H + PAD, PAD, nx * S - W + PAD,
                             cv2.BORDER_REFLECT_101)
    out = np.zeros((ny * S * 4, nx * S * 4, 3), np.float32)
    k, n = 0, ny * nx
    for i in range(ny):
        for j in range(nx):
            if cancel and cancel():
                raise RuntimeError("Cancelado")
            t = pad[i * S:i * S + TILE, j * S:j * S + TILE].transpose(2, 0, 1)[None]
            y = sess.run(None, {"image": np.ascontiguousarray(t)})[0][0].transpose(1, 2, 0)
            out[i * S * 4:(i + 1) * S * 4, j * S * 4:(j + 1) * S * 4] = y[PAD * 4:(PAD + S) * 4, PAD * 4:(PAD + S) * 4]
            k += 1
            if progress and (k % 8 == 0 or k == n):
                progress(k, n)
    return np.clip(out[:H * 4, :W * 4], 0, 1)


def upscale_classic(img: np.ndarray, factor: float) -> np.ndarray:
    h, w = img.shape[:2]
    big = cv2.resize(img.astype(np.float32), (int(round(w * factor)), int(round(h * factor))),
                     interpolation=cv2.INTER_LANCZOS4)
    blur = cv2.GaussianBlur(big, (0, 0), max(0.6, factor * 0.5))
    return np.clip(big + (big - blur) * 0.35, 0, 1)


def upscale(img: np.ndarray, factor: float = 2.0, ai: bool = True, progress=None, cancel=None) -> np.ndarray:
    """Amplía `img` (float 0..1, RGB o RGBA) por `factor` (1..4). Con IA si el modelo está descargado; el alfa
    se amplía con Lanczos. Para ×2 y ×3 se amplía ×4 con la IA y se reduce con área (más nítido que ×2 directo)."""
    factor = float(min(max(factor, 1.0), 4.0))
    rgb = img[..., :3].astype(np.float32)
    h, w = rgb.shape[:2]
    tw, th = int(round(w * factor)), int(round(h * factor))
    if ai and upscaler_ready():
        big = _esrgan_x4(rgb, progress, cancel)
        if (tw, th) != (big.shape[1], big.shape[0]):
            big = cv2.resize(big, (tw, th), interpolation=cv2.INTER_AREA)
    else:
        big = upscale_classic(rgb, factor)
    if img.shape[2] == 4:
        a = cv2.resize(img[..., 3].astype(np.float32), (tw, th), interpolation=cv2.INTER_LANCZOS4)
        big = np.dstack([big, np.clip(a, 0, 1)])
    return np.clip(big, 0, 1).astype(np.float32)


# ------------------------------------------------------------------ partículas (nieve, confeti…)

PARTICLES = {"snow": "Nieve", "rain": "Lluvia", "confetti": "Confeti", "petals": "Pétalos", "bokeh": "Bokeh",
             "sparkles": "Destellos", "dust": "Polvo dorado", "hearts": "Corazones"}
# los de luz se ven mejor en modo Trama
PARTICLE_BLEND = {"bokeh": "screen", "sparkles": "screen", "dust": "screen"}
PALETTES = {"confetti": ["#ff4fa0", "#ffd23f", "#3ec5ff", "#7cf29c", "#b77bff", "#ffffff"],
            "petals": ["#ffc2d1", "#ff9fb8", "#fff0f3"],
            "bokeh": ["#ffd9a0", "#ffb36b", "#fff3d6", "#ff9ec4"],
            "sparkles": ["#fff6d8", "#ffe08a"], "dust": ["#ffe7a3", "#ffd36e"], "hearts": ["#ff4f7a", "#ff9fb8"],
            "snow": ["#ffffff"], "rain": ["#dfe8f5"]}


def particle_defaults(kind: str = "snow") -> dict:
    return dict(kind=kind, density=50.0, size=50.0, depth=60.0, angle=8.0 if kind != "rain" else 12.0,
                motion=10.0 if kind != "rain" else 70.0, opacity=90.0, colors=list(PALETTES.get(kind, ["#ffffff"])),
                seed=7, x0=0.0, y0=0.0, x1=1.0, y1=1.0)


def _heart(r: float) -> np.ndarray:
    t = np.linspace(0, 2 * np.pi, 40)
    x = 16 * np.sin(t) ** 3
    y = -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))
    return np.stack([x, y], 1) * (r / 17.0)


def particles_rgba(h: int, w: int, p: dict) -> np.ndarray:
    """Capa RGBA float (sin premultiplicar) de partículas. Se dibuja en 3 planos de profundidad: lejos (pequeño,
    algo borroso), medio y cerca (grande y muy desenfocado), como una foto real con poca profundidad de campo."""
    kind = p.get("kind", "snow")
    side = max(h, w)
    rng = _rng(int(p.get("seed", 7)) * 131 + sum(map(ord, kind)))  # hash() cambia en cada arranque
    dens = float(p.get("density", 50)) / 100.0
    size = 0.3 + float(p.get("size", 50)) / 100.0 * 1.7
    depth = float(p.get("depth", 60)) / 100.0
    ang = math.radians(float(p.get("angle", 8)))
    motion = float(p.get("motion", 10)) / 100.0
    cols = [_hex(c) for c in (p.get("colors") or PALETTES.get(kind, ["#ffffff"]))]
    x0, y0 = float(p.get("x0", 0)) * w, float(p.get("y0", 0)) * h
    x1, y1 = float(p.get("x1", 1)) * w, float(p.get("y1", 1)) * h
    area = max(1.0, (x1 - x0) * (y1 - y0)) / (side * side)
    base_n = {"snow": 900, "rain": 1400, "confetti": 420, "petals": 160, "bokeh": 70, "sparkles": 90, "dust": 700,
              "hearts": 90}.get(kind, 500)
    acc = np.zeros((h, w, 3), np.float32)
    alpha = np.zeros((h, w), np.float32)
    planes = [(0.45, 0.25 * depth, 0.55), (1.0, 0.6 * depth, 0.3), (2.6, 2.2 * depth, 0.15)]  # escala, desenfoque, parte
    for sc, blur, share in planes:
        n = int(base_n * dens * share * area * (1.0 if kind != "bokeh" else 1.4)) + (1 if dens > 0 else 0)
        L = np.zeros((h, w), np.float32)
        C = np.zeros((h, w, 3), np.float32)
        K = np.zeros((h, w), np.float32)  # cobertura de C (para recuperar el color puro)
        r0 = side * 0.0035 * size * sc
        for _ in range(n):
            cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
            col = cols[int(rng.integers(len(cols)))]
            r = r0 * rng.uniform(0.6, 1.4)
            a = rng.uniform(0.55, 1.0)
            if kind in ("snow", "dust"):
                rr = r * (0.5 if kind == "dust" else 1.0)
                cv2.circle(L, (int(cx), int(cy)), max(1, int(rr)), a, -1, cv2.LINE_AA)
                cv2.circle(C, (int(cx), int(cy)), max(1, int(rr)), col.tolist(), -1, cv2.LINE_AA)
                cv2.circle(K, (int(cx), int(cy)), max(1, int(rr)), 1.0, -1, cv2.LINE_AA)
            elif kind == "rain":
                ln = r * 18 * (0.6 + motion)
                dx, dy = math.sin(ang) * ln, math.cos(ang) * ln
                pt0, pt1 = (int(cx), int(cy)), (int(cx + dx), int(cy + dy))
                th = max(1, int(r * 0.35))
                cv2.line(L, pt0, pt1, a * 0.55, th, cv2.LINE_AA)
                cv2.line(C, pt0, pt1, col.tolist(), th, cv2.LINE_AA)
                cv2.line(K, pt0, pt1, 1.0, th, cv2.LINE_AA)
            elif kind in ("confetti", "petals", "hearts"):
                rot = rng.uniform(0, 180)
                if kind == "confetti":
                    if rng.random() < 0.7:
                        box = cv2.boxPoints(((cx, cy), (r * 2.6, r * 1.1 * rng.uniform(0.3, 1.0)), rot))
                        pts = [np.round(box).astype(np.int32)]
                    else:
                        pts = None
                        cv2.circle(L, (int(cx), int(cy)), max(1, int(r)), a, -1, cv2.LINE_AA)
                        cv2.circle(C, (int(cx), int(cy)), max(1, int(r)), col.tolist(), -1, cv2.LINE_AA)
                        cv2.circle(K, (int(cx), int(cy)), max(1, int(r)), 1.0, -1, cv2.LINE_AA)
                elif kind == "petals":
                    pts = [cv2.ellipse2Poly((int(cx), int(cy)), (max(1, int(r * 2.2)), max(1, int(r * 1.2))),
                                            int(rot), 0, 360, 12)]
                else:
                    hp = _heart(r * 1.6)
                    cs, sn = math.cos(math.radians(rot * 0.25 - 22)), math.sin(math.radians(rot * 0.25 - 22))
                    hp = hp @ np.array([[cs, sn], [-sn, cs]], np.float32)
                    pts = [np.round(hp + [cx, cy]).astype(np.int32)]
                if pts is not None:
                    shade = col * rng.uniform(0.8, 1.05)  # unos más oscuros: dan volumen
                    cv2.fillPoly(L, pts, a, cv2.LINE_AA)
                    cv2.fillPoly(C, pts, np.clip(shade, 0, 1).tolist(), cv2.LINE_AA)
                    cv2.fillPoly(K, pts, 1.0, cv2.LINE_AA)
            elif kind == "bokeh":
                rr = max(2, int(r * 6))
                cv2.circle(L, (int(cx), int(cy)), rr, a * 0.35, -1, cv2.LINE_AA)
                cv2.circle(L, (int(cx), int(cy)), rr, a * 0.55, max(1, rr // 9), cv2.LINE_AA)  # borde más vivo
                cv2.circle(C, (int(cx), int(cy)), rr, col.tolist(), -1, cv2.LINE_AA)
                cv2.circle(K, (int(cx), int(cy)), rr, 1.0, -1, cv2.LINE_AA)
            elif kind == "sparkles":
                rot = rng.uniform(0, 180)
                ln = r * 7
                for k_, f_ in ((0, 1.0), (90, 1.0), (45, 0.45), (135, 0.45)):
                    t_ = math.radians(k_ + rot * 0.1)
                    d_ = (math.cos(t_) * ln * f_, math.sin(t_) * ln * f_)
                    cv2.line(L, (int(cx - d_[0]), int(cy - d_[1])), (int(cx + d_[0]), int(cy + d_[1])), a * 0.8,
                             max(1, int(r * 0.25)), cv2.LINE_AA)
                cv2.circle(L, (int(cx), int(cy)), max(1, int(r * 0.9)), a, -1, cv2.LINE_AA)
                cv2.circle(C, (int(cx), int(cy)), int(ln), col.tolist(), -1, cv2.LINE_AA)
                cv2.circle(K, (int(cx), int(cy)), int(ln), 1.0, -1, cv2.LINE_AA)
        sig = blur * side * 0.004 + (side * 0.0004 if kind in ("snow", "dust") else 0)
        if motion > 0 and kind not in ("rain", "bokeh", "sparkles"):
            k = _motion_kernel(max(3.0, motion * side * 0.012 * sc), 90 - math.degrees(ang))
            L, C, K = cv2.filter2D(L, -1, k), cv2.filter2D(C, -1, k), cv2.filter2D(K, -1, k)
        if sig > 0.4:
            L, C, K = (cv2.GaussianBlur(X, (0, 0), sig) for X in (L, C, K))
        if kind in ("dust", "sparkles"):  # halo de luz
            g = cv2.GaussianBlur(L, (0, 0), max(1.0, side * 0.004 * sc))
            L = np.clip(L + g * 0.8, 0, 1)
            gs_ = max(1.0, side * 0.004 * sc)
            C, K = C + cv2.GaussianBlur(C, (0, 0), gs_), K + cv2.GaussianBlur(K, (0, 0), gs_)
        Lc = np.clip(L, 0, 1)
        pure = C / np.maximum(K, 1e-4)[..., None]  # color sin el oscurecido del antialias ni del desenfoque
        acc = acc * (1 - Lc[..., None]) + np.clip(pure, 0, 1) * Lc[..., None]
        alpha = alpha + Lc * (1 - alpha)
    rgb = acc / np.maximum(alpha[..., None], 1e-4)
    op = float(p.get("opacity", 90)) / 100.0
    return np.dstack([np.clip(rgb, 0, 1), np.clip(alpha * op, 0, 1)]).astype(np.float32)


def _motion_kernel(length: float, angle_deg: float) -> np.ndarray:
    n = int(max(3, round(length))) | 1
    k = np.zeros((n, n), np.float32)
    c = n // 2
    t = math.radians(angle_deg)
    dx, dy = math.cos(t) * c, -math.sin(t) * c
    cv2.line(k, (int(round(c - dx)), int(round(c - dy))), (int(round(c + dx)), int(round(c + dy))), 1.0, 1,
             cv2.LINE_AA)
    return k / max(1e-6, k.sum())


def over(base: np.ndarray, rgba: np.ndarray, blend: str = "normal") -> np.ndarray:
    """Funde una capa RGBA sobre `base` RGB (0..1). blend: normal o screen."""
    a = rgba[..., 3:4]
    top = rgba[..., :3]
    if blend == "screen":
        top = 1 - (1 - base) * (1 - top)
    return np.clip(base * (1 - a) + top * a, 0, 1).astype(np.float32)


# ------------------------------------------------------------------ filtros artísticos

ARTISTIC = {"watercolor": "Acuarela", "oil": "Óleo", "comic": "Cómic", "comic_oil": "Cómic al óleo",
            "ink": "Tinta que fluye", "pencil": "Lápiz", "pencil_color": "Lápiz de color", "pop": "Pop art (puntos)"}


def _paper(h, w, side, seed, strength=0.08):
    n = value_noise(h, w, side * 0.0015 + 1, seed) * 0.6 + fbm(h, w, side * 0.05, seed + 3, 4) * 0.4
    return 1 - strength + n * strength


def _edges_ink(gray: np.ndarray, side: int, thick: float = 1.0) -> np.ndarray:
    """Líneas de tinta (1 = papel, 0 = tinta) a partir de los bordes de la imagen."""
    g8 = _u8(gray)
    k = max(3, int(side * 0.004 * thick)) | 1
    g8 = cv2.medianBlur(g8, k)
    bs = max(3, int(side * 0.009)) | 1
    e = cv2.adaptiveThreshold(g8, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, bs, 6)
    e = cv2.morphologyEx(e, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return cv2.GaussianBlur(_f(e), (0, 0), max(0.5, side * 0.0006))


def _quantize(img: np.ndarray, k: int, seed: int = 1) -> np.ndarray:
    h, w = img.shape[:2]
    f = min(1.0, 320 / max(h, w))
    sm = cv2.resize(img, (max(2, int(w * f)), max(2, int(h * f))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(np.ascontiguousarray(sm, dtype=np.float32), cv2.COLOR_RGB2Lab).reshape(-1, 3)
    cv2.setRNGSeed(seed)
    _, _, cent = cv2.kmeans(lab, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5), 2,
                            cv2.KMEANS_PP_CENTERS)
    full = cv2.cvtColor(np.ascontiguousarray(img, dtype=np.float32), cv2.COLOR_RGB2Lab).reshape(-1, 3)
    out = np.empty_like(full)
    for i in range(0, len(full), 1 << 18):  # por trozos: no reservar una matriz enorme
        ch = full[i:i + (1 << 18)]
        d = ((ch[:, None, :] - cent[None]) ** 2).sum(-1)
        out[i:i + len(ch)] = cent[d.argmin(1)]
    return np.clip(cv2.cvtColor(out.reshape(h, w, 3), cv2.COLOR_Lab2RGB), 0, 1)


def _oil_base(img: np.ndarray, side: int, detail: float) -> np.ndarray:
    """Pinceladas planas: filtro de desplazamiento de media (agrupa colores) a resolución de trabajo."""
    h, w = img.shape[:2]
    f = min(1.0, 1400 / max(h, w))
    sm = cv2.resize(img, (max(2, int(w * f)), max(2, int(h * f))), interpolation=cv2.INTER_AREA) if f < 1 else img
    sp = max(3, int(max(sm.shape[:2]) * (0.012 - 0.008 * detail)))
    ms = cv2.pyrMeanShiftFiltering(_u8(sm), sp, 28)
    ms = cv2.edgePreservingFilter(ms, flags=cv2.RECURS_FILTER, sigma_s=40, sigma_r=0.25)
    out = _f(ms)
    if f < 1:
        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_CUBIC)
    return out


def _brush_relief(base: np.ndarray, side: int, seed: int, strength: float) -> np.ndarray:
    """Relieve de pintura (impasto): trazos de ruido orientados por la dirección de los bordes + luz rasante."""
    h, w = base.shape[:2]
    g = _gray(base)
    gs = cv2.GaussianBlur(g, (0, 0), max(1.0, side * 0.003))
    gx, gy = cv2.Sobel(gs, cv2.CV_32F, 1, 0), cv2.Sobel(gs, cv2.CV_32F, 0, 1)
    ang = (np.degrees(np.arctan2(gy, gx)) + 90) % 180  # las pinceladas siguen los contornos
    n = value_noise(h, w, max(1.0, side * 0.0012), seed)
    L = max(5.0, side * 0.012)
    streak = np.zeros_like(n)
    bins = 8
    q = np.round(ang / (180 / bins)).astype(np.int32) % bins
    for b in range(bins):
        m = q == b
        if m.any():
            streak[m] = cv2.filter2D(n, -1, _motion_kernel(L, b * 180 / bins))[m]
    streak = (streak - streak.mean()) / (streak.std() + 1e-6)
    hgt = cv2.GaussianBlur(streak, (0, 0), 0.8) * 0.5 + gs * 2.0
    rel = cv2.filter2D(hgt, -1, np.array([[-1, -1, 0], [-1, 0, 1], [0, 1, 1]], np.float32))
    return np.clip(base * (1 + rel[..., None] * 0.08 * strength), 0, 1)


def artistic(img: np.ndarray, kind: str, amount: float = 100.0, detail: float = 50.0, seed: int = 3) -> np.ndarray:
    """Filtro artístico sobre `img` RGB 0..1 (o RGBA: el alfa se conserva). amount 0..100 mezcla con la original;
    detail 0..100: más o menos detalle (pinceladas más finas, más líneas…)."""
    alpha = img[..., 3:4] if img.ndim == 3 and img.shape[2] == 4 else None
    src = np.clip(img[..., :3], 0, 1).astype(np.float32)
    h, w = src.shape[:2]
    side = max(h, w)
    det = float(min(max(detail, 0), 100)) / 100.0
    s8 = _u8(src)
    if kind == "watercolor":
        ss = 30 + 70 * (1 - det)
        st = _f(cv2.stylization(s8, sigma_s=float(ss), sigma_r=0.45))
        flat = _f(cv2.edgePreservingFilter(_u8(st), flags=cv2.RECURS_FILTER, sigma_s=60, sigma_r=0.4))
        g = _gray(flat)
        pool = np.clip(g - cv2.GaussianBlur(g, (0, 0), max(1.0, side * 0.004)), -1, 0)  # pigmento en los bordes
        bleed = cv2.GaussianBlur(flat, (0, 0), max(1.0, side * 0.002))
        wet = flat * 0.6 + bleed * 0.4
        wash = 1 - (1 - wet) * 0.85  # transparencia: aclarar como la acuarela
        out = wash * (1 + pool[..., None] * 2.2) * _paper(h, w, side, seed, 0.1)[..., None]
        hsv = cv2.cvtColor(np.clip(out, 0, 1).astype(np.float32), cv2.COLOR_RGB2HSV)
        hsv[..., 1] *= 1.12
        out = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    elif kind == "oil":
        out = _brush_relief(_oil_base(src, side, det), side, seed, 1.0)
        hsv = cv2.cvtColor(out.astype(np.float32), cv2.COLOR_RGB2HSV)
        hsv[..., 1] = np.clip(hsv[..., 1] * 1.15, 0, 1)
        out = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    elif kind in ("comic", "comic_oil"):
        if kind == "comic":
            flat = _f(cv2.bilateralFilter(s8, 9, 60, 9))
            flat = _quantize(flat, 6 + int(det * 6), seed)
        else:
            flat = _brush_relief(_oil_base(src, side, det), side, seed, 0.7)
        hsv = cv2.cvtColor(flat.astype(np.float32), cv2.COLOR_RGB2HSV)
        hsv[..., 1] = np.clip(hsv[..., 1] * 1.35, 0, 1)
        hsv[..., 2] = np.clip((hsv[..., 2] - 0.5) * 1.15 + 0.53, 0, 1)
        flat = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        ink = _edges_ink(_gray(src), side, 1.6 - det)
        g = _gray(flat)
        dots = _halftone(g, side * 0.006) if kind == "comic" else 1.0
        shade = np.where(g < 0.38, dots, 1.0) if kind == "comic" else 1.0
        out = flat * ink[..., None] * (np.asarray(shade, np.float32)[..., None] if kind == "comic" else 1.0)
    elif kind == "ink":
        g = _gray(src)
        flow = fbm(h, w, side * 0.08, seed, 4) - 0.5
        flow2 = fbm(h, w, side * 0.08, seed + 9, 4) - 0.5
        amp = side * (0.006 + 0.02 * (1 - det))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        gw = cv2.remap(g, xx + flow * amp, yy + flow2 * amp, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        tone = cv2.GaussianBlur(gw, (0, 0), max(0.8, side * 0.0015))
        local = cv2.GaussianBlur(tone, (0, 0), max(2.0, side * 0.03))
        thr = local * 0.8 + 0.06 + (fbm(h, w, side * 0.02, seed + 2, 3) - 0.5) * 0.08  # contra el tono de alrededor
        ink = np.clip((tone - thr) / 0.06 + 0.5, 0, 1)  # tinta muy contrastada con bordes que fluyen
        ink = np.maximum(ink, np.clip((tone - 0.25) / 0.2, 0, 1) * 0.0 + (tone > 0.72))  # lo claro queda papel
        wash = np.clip(tone * 1.2, 0, 1)
        lines = _edges_ink(g, side, 1.2)
        paper = _paper(h, w, side, seed, 0.06)
        v = np.minimum(ink * 0.7 + wash * 0.3, lines) * paper
        splat = (fbm(h, w, side * 0.01, seed + 5, 3) > 0.97).astype(np.float32) * (g < 0.35)
        v = np.clip(v - cv2.GaussianBlur(splat, (0, 0), 1.0) * 0.6, 0, 1)
        out = np.stack([v * 0.98 + 0.02, v * 0.96 + 0.03, v * 0.92 + 0.06], -1)  # tinta sepia oscura sobre papel
    elif kind == "pencil":
        g8, _ = cv2.pencilSketch(s8, sigma_s=float(30 + 40 * det), sigma_r=0.08, shade_factor=0.045)
        tone = 0.55 + 0.45 * np.clip(_gray(src) * 1.25, 0, 1)  # el sombreado del lápiz además de las líneas
        out = np.repeat((_f(g8) * tone)[..., None], 3, 2) * _paper(h, w, side, seed, 0.06)[..., None]
    elif kind == "pencil_color":
        _, c8 = cv2.pencilSketch(s8, sigma_s=float(30 + 40 * det), sigma_r=0.08, shade_factor=0.04)
        wash = 1 - (1 - src) * 0.45  # color suave debajo, como lápices de color sobre papel
        out = _f(c8) * wash * _paper(h, w, side, seed, 0.06)[..., None]
    elif kind == "pop":
        flat = _quantize(_f(cv2.bilateralFilter(s8, 9, 70, 9)), 5, seed)
        hsv = cv2.cvtColor(flat.astype(np.float32), cv2.COLOR_RGB2HSV)
        hsv[..., 1] = np.clip(hsv[..., 1] * 1.6, 0, 1)
        flat = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        dots = _halftone(_gray(flat), side * (0.004 + 0.006 * (1 - det)))
        out = flat * (0.35 + 0.65 * dots[..., None]) * _edges_ink(_gray(src), side, 1.4)[..., None]
    else:
        out = src
    a = float(min(max(amount, 0), 100)) / 100.0
    out = np.clip(src * (1 - a) + np.clip(out, 0, 1) * a, 0, 1).astype(np.float32)
    return np.concatenate([out, alpha], 2) if alpha is not None else out


def _halftone(g: np.ndarray, cell: float) -> np.ndarray:
    """Trama de puntos a 45°: 1 = papel, 0 = tinta; los puntos crecen en las sombras."""
    h, w = g.shape
    cell = max(3.0, cell)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u = (xx + yy) / (cell * math.sqrt(2))
    v = (xx - yy) / (cell * math.sqrt(2))
    d = np.hypot(u - np.round(u), v - np.round(v))  # 0 en el centro de la celda .. 0.707
    r = np.sqrt(np.clip(1 - g, 0, 1)) * 0.6
    return np.clip((d - r) * cell * 0.9 + 0.5, 0, 1)


# ------------------------------------------------------------------ texturas

TEXTURES = {"clouds": "Nubes", "marble": "Mármol", "wood": "Madera", "stone": "Piedra", "paper": "Papel",
            "grunge": "Grunge", "concrete": "Hormigón", "brushed": "Metal cepillado", "plasma": "Plasma",
            "caustics": "Cáusticas (agua)", "anarchy": "Anarquía (psicodélico)", "galaxy": "Galaxia"}
TEXTURE_COLORS = {"clouds": ("#4a7fd6", "#ffffff"), "marble": ("#f4f1ec", "#5a5a66"), "wood": ("#5b3418", "#c58a4f"),
                  "stone": ("#4f4c48", "#b3aea6"), "paper": ("#efe7d6", "#c9b99a"), "grunge": ("#1b1b1f", "#8a8378"),
                  "concrete": ("#7d7c79", "#b9b7b2"), "brushed": ("#6d727a", "#e2e5ea"),
                  "plasma": ("#ff4fa0", "#3ec5ff"), "caustics": ("#0b4f7a", "#b9f2ff"),
                  "anarchy": ("#ff4fa0", "#7cf29c"), "galaxy": ("#090418", "#b77bff")}


def texture(kind: str, w: int, h: int, c1: str | None = None, c2: str | None = None, scale: float = 50.0,
            detail: float = 50.0, seed: int = 1) -> np.ndarray:
    """Textura RGB 0..1 de w × h. scale 1..100: tamaño del dibujo; detail 0..100: octavas y contraste."""
    d1, d2 = TEXTURE_COLORS.get(kind, ("#000000", "#ffffff"))
    A, B = _hex(c1 or d1), _hex(c2 or d2)
    side = max(w, h)
    sc = side * (0.02 + float(scale) / 100.0 * 0.5)
    oc = 3 + int(float(detail) / 100.0 * 4)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    def mix(t):
        t = np.clip(t, 0, 1)[..., None]
        return A * (1 - t) + B * t

    if kind == "clouds":
        out = mix(fbm(h, w, sc, seed, oc) ** 1.2)
    elif kind == "marble":
        n = fbm(h, w, sc, seed, oc)
        v = np.abs(np.sin((xx / sc * 2.5 + yy / sc * 1.2 + n * 6.0) * math.pi * 0.5))
        veins = 1 - np.clip(v ** 0.18, 0, 1)
        out = mix(veins * 0.9 + fbm(h, w, sc * 0.3, seed + 1, 3) * 0.1)
    elif kind == "wood":
        n = fbm(h, w, sc * 0.8, seed, oc)
        cx, cy = w * 0.5, -h * 0.8
        r = np.hypot((xx - cx) * 0.25, yy - cy) / (sc * 0.12) + n * 4
        ring = (np.sin(r * math.pi) * 0.5 + 0.5) ** 1.6
        grain = value_noise(h, w, 1.0 + side * 0.001, seed + 4)
        grain = cv2.filter2D(grain, -1, _motion_kernel(side * 0.04, 0))
        out = mix(ring * 0.75 + (grain - 0.5) * 0.5 + 0.15)
    elif kind == "stone":
        n = fbm(h, w, sc, seed, oc)
        cells = cv2.distanceTransform((value_noise(h, w, sc * 0.25, seed + 2) > 0.62).astype(np.uint8) * 255,
                                      cv2.DIST_L2, 3)
        cells = cells / max(1e-6, cells.max())
        speck = value_noise(h, w, 1.5, seed + 3)
        out = mix(n * 0.55 + cells * 0.25 + speck * 0.2)
    elif kind == "paper":
        fib = value_noise(h, w, 1.2, seed)
        fib = cv2.filter2D(fib, -1, _motion_kernel(side * 0.01, 30)) * 0.5 + \
            cv2.filter2D(fib, -1, _motion_kernel(side * 0.01, 120)) * 0.5
        out = mix(fbm(h, w, sc, seed + 1, oc) * 0.25 + (fib - 0.5) * 0.6 + 0.2)
    elif kind == "grunge":
        n = fbm(h, w, sc * 0.6, seed, oc)
        spl = (fbm(h, w, sc * 0.15, seed + 1, 4) > 0.7).astype(np.float32)
        scr = np.zeros((h, w), np.float32)
        rng = _rng(seed)
        for _ in range(int(40 + detail)):
            x0, y0 = rng.uniform(0, w), rng.uniform(0, h)
            a = rng.uniform(0, math.pi)
            ln = rng.uniform(0.05, 0.3) * side
            cv2.line(scr, (int(x0), int(y0)), (int(x0 + math.cos(a) * ln), int(y0 + math.sin(a) * ln)),
                     rng.uniform(0.3, 1.0), 1, cv2.LINE_AA)
        out = mix(np.clip(n * 0.7 + spl * 0.25 + scr * 0.5, 0, 1))
    elif kind == "concrete":
        n = fbm(h, w, sc, seed, oc) * 0.5 + value_noise(h, w, 1.2, seed + 1) * 0.3
        pores = (value_noise(h, w, 2.0, seed + 2) > 0.93).astype(np.float32)
        out = mix(np.clip(n - cv2.GaussianBlur(pores, (0, 0), 0.8) * 0.6 + 0.2, 0, 1))
    elif kind == "brushed":
        n = value_noise(h, w, 1.0, seed)
        n = cv2.filter2D(n, -1, _motion_kernel(side * 0.15, 0))
        n = (n - n.mean()) / (n.std() + 1e-6) * 0.12
        sheen = 0.5 + 0.35 * np.cos((xx / w - 0.3) * math.pi * 2)
        out = mix(np.clip(sheen + n, 0, 1))
    elif kind == "plasma":
        n = fbm(h, w, sc, seed, oc)
        t = np.sin(xx / sc * 3 + n * 6) + np.sin(yy / sc * 2.3 - n * 5) + np.sin((xx + yy) / sc * 1.7)
        out = mix(t / 6 + 0.5)
    elif kind == "caustics":
        q = fbm(h, w, sc * 0.5, seed, oc)
        v = np.abs(np.sin(q * 18.0))
        lines = np.clip(1 - v * 3.0, 0, 1) ** 1.5
        out = mix(lines * 0.85 + q * 0.15)
    elif kind == "anarchy":
        q1 = fbm(h, w, sc, seed, oc)
        q2 = fbm(h, w, sc, seed + 5, oc)
        wx = cv2.remap(fbm(h, w, sc * 0.7, seed + 9, oc), xx + (q1 - 0.5) * sc * 2, yy + (q2 - 0.5) * sc * 2,
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        hue = (wx * 2.0 + q1 * 0.5) % 1.0
        hsv = np.dstack([hue * 360, np.full_like(hue, 0.85), 0.55 + 0.45 * np.sin(wx * 12) ** 2]).astype(np.float32)
        rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        out = rgb * 0.75 + mix(wx) * 0.25
    elif kind == "galaxy":
        neb = fbm(h, w, sc, seed, oc) ** 2.2
        neb2 = fbm(h, w, sc * 0.6, seed + 3, oc) ** 3
        base = A * (1 - neb[..., None]) + B * neb[..., None]
        base = base + np.array([0.2, 0.55, 1.0], np.float32) * neb2[..., None] * 0.6
        stars = (value_noise(h, w, 1.0, seed + 7) > 0.997).astype(np.float32)
        stars = np.clip(stars + cv2.GaussianBlur(stars, (0, 0), 1.2) * 3, 0, 1)
        out = base + stars[..., None]
    else:
        out = mix(fbm(h, w, sc, seed, oc))
    return np.clip(out, 0, 1).astype(np.float32)


# ------------------------------------------------------------------ efectos (tipo Eye Candy)

EFFECTS = {"chrome": "Cromo", "gold": "Oro", "fire": "Fuego", "lightning": "Rayos", "ice": "Hielo",
           "neon": "Neón"}


def shape_alpha_from_text(text: str, w: int, h: int, font: str = "Arial Black") -> np.ndarray:
    """Alfa 0..1 con un texto dibujado al tamaño de la caja (para los efectos sobre texto)."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    img = QImage(w, h, QImage.Format_Grayscale8)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    f = QFont(font)
    f.setPixelSize(max(8, int(h * 0.72)))
    p.setFont(f)
    fm = p.fontMetrics()
    tw = max(1, fm.horizontalAdvance(text))
    if tw > w * 0.92:
        f.setPixelSize(max(8, int(f.pixelSize() * w * 0.92 / tw)))
        p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, text)
    p.end()
    ptr = img.constBits()
    a = np.frombuffer(ptr, np.uint8, img.bytesPerLine() * h).reshape(h, img.bytesPerLine())[:, :w]
    return a.astype(np.float32) / 255.0


def effect(kind: str, alpha: np.ndarray, seed: int = 1, color: str | None = None) -> np.ndarray:
    """Efecto sobre una forma (alfa 0..1, p. ej. un texto): devuelve RGBA 0..1 del mismo tamaño. Fuego, rayos y
    neón salen fuera de la forma (llevan margen dentro de la propia caja)."""
    h, w = alpha.shape
    side = max(h, w)
    a = np.clip(alpha, 0, 1).astype(np.float32)
    if kind in ("chrome", "gold", "ice"):
        # bisel: altura = distancia al borde suavizada → normales → reflejo de un «entorno» de franjas
        dist = cv2.distanceTransform((a > 0.5).astype(np.uint8), cv2.DIST_L2, 5)
        bev = max(2.0, side * 0.012)
        hgt = np.clip(dist / bev, 0, 1)
        hgt = cv2.GaussianBlur(hgt, (0, 0), max(0.8, bev * 0.3))
        gx, gy = cv2.Sobel(hgt, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(hgt, cv2.CV_32F, 0, 1, ksize=3)
        nz = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
        ny_ = gy * nz
        yy = np.mgrid[0:h, 0:w][0].astype(np.float32) / max(1, h - 1)
        refl = yy * 0.9 + ny_ * 1.4
        env = 0.5 + 0.5 * np.sin(refl * math.pi * 2.2 + 0.6)
        light = np.clip(0.55 + (-gx - gy) * nz * 0.8, 0, 1.2)
        if kind == "chrome":
            stops = [(0.0, "#1d2330"), (0.35, "#8e9aab"), (0.5, "#ffffff"), (0.62, "#4b5566"), (1.0, "#e9eef5")]
        elif kind == "gold":
            stops = [(0.0, "#5a3a08"), (0.35, "#c8902a"), (0.55, "#fff1b8"), (0.7, "#a8701a"), (1.0, "#ffd774")]
        else:
            stops = [(0.0, "#2a6f9e"), (0.4, "#9fe3ff"), (0.6, "#ffffff"), (1.0, "#6cc7f0")]
        rgb = _ramp(env, stops) * light[..., None]
        spec = np.clip((light - 0.95) * 4, 0, 1)[..., None]
        rgb = np.clip(rgb + spec * 0.5, 0, 1)
        if kind == "ice":
            frost = value_noise(h, w, 1.3, seed)
            rgb = np.clip(rgb * (0.85 + 0.25 * frost[..., None]), 0, 1)
            a = a * 0.9
        return np.dstack([rgb, a]).astype(np.float32)
    if kind == "fire":
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        n = fbm(h, w, side * 0.06, seed, 5)
        rise = side * 0.2
        src = cv2.GaussianBlur(a, (0, 0), max(1.0, side * 0.004))
        flame = np.zeros_like(a)
        for k in range(1, 9):  # la llama sube desde la forma y se deshilacha
            off = rise * k / 8
            m = cv2.remap(src, xx + (n - 0.5) * side * 0.03 * k / 8, yy + off, cv2.INTER_LINEAR, borderValue=0)
            flame = np.maximum(flame, m * (1 - k / 9))
        heat = np.clip(flame * (0.5 + 1.1 * n) + a * 0.62, 0, 1)
        heat = cv2.GaussianBlur(heat, (0, 0), max(0.8, side * 0.0025))
        rgb = _ramp(heat, [(0.0, "#2b0000"), (0.3, "#c81e00"), (0.6, "#ff8a00"), (0.85, "#ffe066"), (1.0, "#fffbe6")])
        return np.dstack([rgb, np.clip(heat * 1.6, 0, 1)]).astype(np.float32)
    if kind == "lightning":
        rng = _rng(seed)
        bolt = np.zeros((h, w), np.float32)
        edge = np.argwhere(cv2.Canny(_u8(a), 50, 150) > 0)
        for _ in range(12 if len(edge) else 0):
            y0, x0 = edge[int(rng.integers(len(edge)))]
            ang = rng.uniform(0, 2 * math.pi)
            _bolt(bolt, float(x0), float(y0), ang, side * rng.uniform(0.06, 0.16), side, rng, 0)
        glow = cv2.GaussianBlur(bolt, (0, 0), max(1.0, side * 0.006))
        core = np.clip(bolt + a * 0.0, 0, 1)
        val = np.clip(core + glow * 2.2, 0, 1)
        base = _hex(color or "#7fd4ff")
        rgb = np.clip(base * glow[..., None] * 2.2 + core[..., None], 0, 1)
        body = a[..., None]
        rgb = rgb * (1 - body) + (np.array([0.9, 0.97, 1.0], np.float32) * 0.6 + base * 0.4) * body
        return np.dstack([rgb, np.clip(np.maximum(val, a), 0, 1)]).astype(np.float32)
    if kind == "neon":
        base = _hex(color or "#ff4fa0")
        edge = np.clip(cv2.morphologyEx(a, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) * 1.5, 0, 1)
        tube = cv2.GaussianBlur(cv2.dilate(edge, np.ones((max(2, int(side * 0.004)),) * 2, np.uint8)), (0, 0), 1.0)
        g1 = cv2.GaussianBlur(tube, (0, 0), max(1.0, side * 0.006))
        g2 = cv2.GaussianBlur(tube, (0, 0), max(2.0, side * 0.02))
        core = np.clip(tube * 1.4, 0, 1)
        rgb = np.clip(base * (g1 * 1.6 + g2 * 1.2)[..., None] + core[..., None] * 0.9, 0, 1)
        return np.dstack([rgb, np.clip(core + g1 * 1.2 + g2 * 0.8, 0, 1)]).astype(np.float32)
    return np.dstack([np.ones((h, w, 3), np.float32), a])


def _bolt(dst, x, y, ang, length, side, rng, depth):
    seg = max(3.0, side * 0.012)
    steps = int(length / seg)
    th = max(1, int(side * 0.0025 / (1 + depth)))
    for _ in range(steps):
        ang += rng.normal(0, 0.45)
        nx, ny = x + math.cos(ang) * seg, y + math.sin(ang) * seg
        cv2.line(dst, (int(x), int(y)), (int(nx), int(ny)), 1.0 / (1 + depth * 0.6), th, cv2.LINE_AA)
        if depth < 2 and rng.random() < 0.12:
            _bolt(dst, nx, ny, ang + rng.normal(0, 0.9), length * 0.4, side, rng, depth + 1)
        x, y = nx, ny


# ------------------------------------------------------------------ bordes artísticos

EDGES = {"torn": "Papel rasgado", "grunge": "Grunge", "brush": "Pincel", "burn": "Quemado", "ink": "Tinta",
         "soft": "Difuminado"}


def edge_alpha(h: int, w: int, kind: str, amount: float = 50.0, seed: int = 1) -> np.ndarray:
    """Alfa 0..1 para el borde de una foto. amount 0..100: cuánto se come hacia dentro."""
    side = max(h, w)
    m = max(2.0, side * (0.01 + float(amount) / 100.0 * 0.08))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.minimum(np.minimum(xx, w - 1 - xx), np.minimum(yy, h - 1 - yy))  # distancia al borde
    if kind == "soft":
        t = np.clip(d / m, 0, 1)
        return (t * t * (3 - 2 * t)).astype(np.float32)
    if kind == "torn":
        n = fbm(h, w, side * 0.03, seed, 6) - 0.5
        t = (d + n * m * 1.2) / m
        a = np.clip((t - 0.5) * side * 0.01 + 0.5, 0, 1)
        fib = np.clip((t - 0.35) * 6, 0, 1) * (1 - a)  # fibras blancas del papel al rasgar
        return np.clip(a + fib * 0.85, 0, 1).astype(np.float32)
    if kind == "grunge":
        n = fbm(h, w, side * 0.015, seed, 6)
        t = d / m + (n - 0.5) * 1.6
        return np.clip((t - 0.6) * 4, 0, 1).astype(np.float32)
    if kind == "brush":
        n = value_noise(h, w, 1.0, seed)
        nh = cv2.filter2D(n, -1, _motion_kernel(side * 0.05, 0))
        nv = cv2.filter2D(n, -1, _motion_kernel(side * 0.05, 90))
        dx = np.minimum(xx, w - 1 - xx)
        dy = np.minimum(yy, h - 1 - yy)
        jag = np.where(dx < dy, (nv - 0.5), (nh - 0.5)) * 9
        t = d / m + jag
        return np.clip((t - 0.7) * 3, 0, 1).astype(np.float32)
    if kind == "burn":
        n = fbm(h, w, side * 0.04, seed, 5)
        t = d / m + (n - 0.5) * 1.2
        return np.clip((t - 0.3) * 2.5, 0, 1).astype(np.float32)
    if kind == "ink":
        n = fbm(h, w, side * 0.02, seed, 5)
        t = d / m + (n - 0.5) * 2.4
        a = np.clip((t - 0.8) * 10, 0, 1)
        return cv2.GaussianBlur(a.astype(np.float32), (0, 0), 0.7)
    return np.ones((h, w), np.float32)


def burn_tint(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Para el borde «Quemado»: oscurece y tuesta lo que está cerca del borde."""
    near = np.clip(1 - (alpha - 0.0) * 1.6, 0, 1)[..., None] * (alpha[..., None] > 0)
    toast = np.array([0.35, 0.18, 0.05], np.float32)
    return np.clip(rgb * (1 - near * 0.85) + toast * near * 0.6, 0, 1)


def apply_edge(img: np.ndarray, kind: str, amount: float = 50.0, seed: int = 1) -> np.ndarray:
    """Devuelve RGBA con el borde artístico aplicado (el alfa existente se respeta)."""
    h, w = img.shape[:2]
    a = edge_alpha(h, w, kind, amount, seed)
    rgb = img[..., :3].astype(np.float32)
    if kind == "burn":
        rgb = burn_tint(rgb, a)
        a = np.clip(a * 1.4, 0, 1)
    old = img[..., 3] if img.shape[2] == 4 else np.ones((h, w), np.float32)
    return np.dstack([rgb, a * old]).astype(np.float32)


# ------------------------------------------------------------------ doble exposición

def double_exposure(base: np.ndarray, overlay: np.ndarray, subject: np.ndarray, amount: float = 100.0,
                    mode: str = "screen", bw: bool = False, fade: float = 40.0) -> np.ndarray:
    """Doble exposición clásica: el retrato (`subject` = alfa de la persona) se rellena con `overlay` en modo
    Trama/Aclarar y el fondo se lava a blanco. base/overlay RGB 0..1 del mismo tamaño."""
    a = np.clip(subject, 0, 1)[..., None]
    b = base.astype(np.float32)
    o = overlay.astype(np.float32)
    if bw:
        g = _gray(b)[..., None]
        b = np.repeat(g, 3, 2)
    if mode == "lighten":
        mix_ = np.maximum(b, o)
    elif mode == "multiply":
        mix_ = b * o
    else:
        mix_ = 1 - (1 - b) * (1 - o)
    k = float(amount) / 100.0
    inside = b * (1 - k) + mix_ * k
    # el fondo se queda claro y la foto superpuesta se desvanece por fuera de la silueta
    f = float(fade) / 100.0
    bg = np.ones_like(b) * (1 - f * 0.3) + o * f * 0.3
    soft = cv2.GaussianBlur(a, (0, 0), max(1.0, max(b.shape[:2]) * 0.004))
    soft = soft[..., None] if soft.ndim == 2 else soft
    return np.clip(inside * soft + bg * (1 - soft), 0, 1).astype(np.float32)
