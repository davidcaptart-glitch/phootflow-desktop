"""Formatos de salida comunes a Graphics y Edits (sin interfaz).

- Imagen: JPG, PNG, WebP, AVIF, TIFF (LZW, 8/16 bits, CMYK opcional), GIF, BMP y PDF de una foto.
- Animación: GIF y WebP animados (las páginas de un diseño como fotogramas).
- Vectorizado para imprenta: el resultado de `graphics_engine.vectorize` ({"viewbox", "paths"}) a SVG, PDF, EPS
  y DXF, todo trazados (sin mapas de bits): la imprenta, el plotter de vinilo o el láser lo escalan sin perder.
  El PDF y el EPS se escriben a mano (sin Qt) para poder dar las tintas en CMYK de verdad (operador `k`).
"""
from __future__ import annotations

import os
import re
import zlib

import numpy as np
from PIL import Image

MM_PER_IN = 25.4
PT_PER_MM = 72.0 / MM_PER_IN

# extensión → (nombre en el diálogo, descripción corta)
RASTER = {
    "jpg": ("JPG", "Foto, la más compatible"),
    "png": ("PNG", "Sin pérdida, con transparencia"),
    "webp": ("WebP", "Web y redes: pesa poco, con transparencia"),
    "avif": ("AVIF", "Web moderna: aún menos peso"),
    "tif": ("TIFF", "Imprenta: sin pérdida, LZW, CMYK opcional"),
    "gif": ("GIF", "256 colores; animado si hay varias páginas"),
    "bmp": ("BMP", "Mapa de bits sin comprimir"),
    "pdf": ("PDF", "La imagen dentro de un PDF al tamaño de impresión"),
}
ANIMATED = ("gif", "webp")
VECTOR = {
    "svg": ("SVG", "Web, Illustrator, Inkscape, Canva"),
    "pdf": ("PDF vectorial", "Imprenta: trazados, CMYK opcional"),
    "eps": ("EPS", "Imprentas y rotulistas que lo piden (PostScript)"),
    "dxf": ("DXF", "Plotter de corte, vinilo, láser, CNC"),
}


# ------------------------------------------------------------------ imagen

def _to_uint8(img: np.ndarray) -> np.ndarray:
    if img.dtype == np.uint8:
        return img
    if img.dtype == np.uint16:
        return (img >> 8).astype(np.uint8)
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)


def _pil(img: np.ndarray, alpha: np.ndarray | None = None) -> Image.Image:
    """RGB (float 0–1, uint8 o uint16) + alfa opcional → imagen de Pillow de 8 bits."""
    a = _to_uint8(img[..., :3])
    if alpha is None and img.ndim == 3 and img.shape[2] == 4:
        alpha = img[..., 3]
    if alpha is not None:
        al = _to_uint8(alpha if alpha.ndim == 2 else alpha[..., 0])
        if al.min() < 255:
            return Image.fromarray(np.dstack([a, al]), "RGBA")
    return Image.fromarray(np.ascontiguousarray(a), "RGB")


def _flatten(im: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    if im.mode != "RGBA":
        return im.convert("RGB")
    out = Image.new("RGB", im.size, bg)
    out.paste(im, mask=im.split()[3])
    return out


def save_raster(img: np.ndarray, path: str, alpha: np.ndarray | None = None, quality: int = 92,
                dpi: float = 300.0, cmyk: bool = False, bits16: bool = False, exif: bytes = b"",
                icc: bytes | None = None) -> str:
    """Guarda una imagen en el formato de la extensión de `path` (ver RASTER).

    Lo que no admite transparencia (JPG, BMP, PDF, TIFF CMYK) se aplana sobre blanco. Escribe en un temporal y
    lo cambia al final: un corte a medias no deja un archivo roto.
    """
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    ext = {"jpeg": "jpg", "tiff": "tif"}.get(ext, ext)
    if ext not in RASTER:
        raise ValueError(f"Formato no admitido: .{ext}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    d = (float(dpi), float(dpi))
    kw = {}
    if ext == "tif" and bits16 and not cmyk:
        # 16 bits por canal (RGB): Pillow no los escribe; se usa OpenCV
        import cv2
        src = img[..., :3]
        if src.dtype == np.uint8:
            data = src.astype(np.uint16) * 257
        elif src.dtype == np.uint16:
            data = src
        else:
            data = (np.clip(src, 0, 1) * 65535 + 0.5).astype(np.uint16)
        bgr = np.ascontiguousarray(data[..., ::-1])
        if alpha is not None:
            al = alpha.astype(np.float32) / (255.0 if alpha.dtype == np.uint8 else 1.0)
            bgr = np.dstack([bgr, (np.clip(al, 0, 1) * 65535 + 0.5).astype(np.uint16)])
        ok, buf = cv2.imencode(".tif", bgr)
        if not ok:
            raise OSError(f"No se pudo guardar {path}")
        with open(tmp, "wb") as f:
            f.write(buf.tobytes())
        os.replace(tmp, path)
        return path
    im = _pil(img, alpha)
    if ext == "jpg":
        im = _flatten(im)
        kw = dict(quality=int(quality), subsampling=0, optimize=True, dpi=d)
        if exif:
            kw["exif"] = exif
        fmt = "JPEG"
    elif ext == "png":
        kw = dict(optimize=True, dpi=d)
        fmt = "PNG"
    elif ext == "webp":
        kw = dict(quality=int(quality), method=5, lossless=quality >= 100)
        fmt = "WEBP"
    elif ext == "avif":
        kw = dict(quality=int(quality))
        fmt = "AVIF"
    elif ext == "tif":
        if cmyk:
            im = _flatten(im).convert("CMYK")
        kw = dict(compression="tiff_lzw", dpi=d)
        fmt = "TIFF"
    elif ext == "gif":
        im = _gif_frame(im)
        fmt = "GIF"
    elif ext == "bmp":
        im = _flatten(im)
        fmt = "BMP"
    else:  # pdf
        im = _flatten(im)
        if cmyk:
            im = im.convert("CMYK")
        kw = dict(resolution=float(dpi))
        fmt = "PDF"
    if icc and fmt in ("JPEG", "PNG", "TIFF", "WEBP") and im.mode in ("RGB", "RGBA"):
        kw["icc_profile"] = icc
    im.save(tmp, fmt, **kw)
    os.replace(tmp, path)
    return path


def _gif_frame(im: Image.Image) -> Image.Image:
    """RGBA → paleta de 255 colores + 1 transparente (si hay transparencia)."""
    if im.mode == "RGBA" and im.split()[3].getextrema()[0] < 128:
        a = im.split()[3]
        p = _flatten(im).quantize(255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
        p.paste(255, mask=a.point(lambda v: 255 if v < 128 else 0))
        p.info["transparency"] = 255
        return p
    return _flatten(im).quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)


def save_animation(frames: list[np.ndarray], path: str, duration_ms: int = 1500, loop: int = 0,
                   quality: int = 90) -> str:
    """Varias imágenes (RGB o RGBA) → GIF o WebP animado. `loop` 0 = repetir siempre."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in ANIMATED:
        raise ValueError("La animación se guarda en GIF o WebP")
    if not frames:
        raise ValueError("No hay fotogramas")
    ims = [_pil(f) for f in frames]
    size = ims[0].size
    ims = [i if i.size == size else i.resize(size, Image.LANCZOS) for i in ims]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    if ext == "gif":
        ims = [_gif_frame(i) for i in ims]
        ims[0].save(tmp, "GIF", save_all=True, append_images=ims[1:], duration=int(duration_ms), loop=int(loop),
                    disposal=2, optimize=False)
    else:
        ims[0].save(tmp, "WEBP", save_all=True, append_images=ims[1:], duration=int(duration_ms), loop=int(loop),
                    quality=int(quality), method=4)
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------ vectorizado

_TOK = re.compile(r"[MLCZ]|-?\d+(?:\.\d+)?(?:e-?\d+)?")


def parse_d(d: str) -> list[list[tuple]]:
    """«M x y L x y C … Z» (absoluto, lo que escribe `vectorize`) → subtrazados [("M",x,y),("L",x,y),("C",…6)…]."""
    subs: list[list[tuple]] = []
    cur: list[tuple] = []
    toks = _TOK.findall(d)
    i, cmd = 0, "M"
    while i < len(toks):
        t = toks[i]
        if t in "MLCZ":
            cmd = t
            i += 1
            if t == "Z":
                if cur:
                    subs.append(cur)
                cur = []
            continue
        n = 6 if cmd == "C" else 2
        if i + n > len(toks):
            break
        v = tuple(float(x) for x in toks[i:i + n])
        i += n
        if cmd == "M":
            if cur:
                subs.append(cur)
            cur = [("M",) + v]
            cmd = "L"  # pares tras M son líneas (como en SVG)
        else:
            cur.append((cmd,) + v)
    if cur:
        subs.append(cur)
    return subs


def _rgb(hexcol: str) -> tuple[float, float, float]:
    h = hexcol.lstrip("#")
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def rgb_to_cmyk(r: float, g: float, b: float) -> tuple[float, float, float, float]:
    """Conversión simple (sin perfil ICC). Para el color exacto, la imprenta aplica su perfil."""
    k = 1 - max(r, g, b)
    if k >= 0.9999:
        return 0.0, 0.0, 0.0, 1.0
    return (1 - r - k) / (1 - k), (1 - g - k) / (1 - k), (1 - b - k) / (1 - k), k


def _size_pt(vec: dict, width_mm: float | None) -> tuple[float, float, float]:
    """(ancho, alto en puntos, escala unidades-del-viewbox → puntos)."""
    vw, vh = vec["viewbox"]
    if width_mm and width_mm > 0:
        s = width_mm * PT_PER_MM / vw
    else:
        s = 72.0 / 96.0  # sin tamaño: 1 unidad = 1 px a 96 ppp
    return vw * s, vh * s, s


def _ps_paths(vec: dict, s: float, H: float, cmyk: bool, eps: bool) -> str:
    """Operadores PDF/PostScript de los trazados (eje y hacia arriba)."""
    out = []
    fmt = lambda v: f"{v:.3f}".rstrip("0").rstrip(".")  # noqa: E731
    X = lambda x: fmt(x * s)  # noqa: E731
    Y = lambda y: fmt(H - y * s)  # noqa: E731
    for p in vec["paths"]:
        r, g, b = _rgb(p["fill"])
        if cmyk:
            c, m, y, k = rgb_to_cmyk(r, g, b)
            out.append(f"{fmt(c)} {fmt(m)} {fmt(y)} {fmt(k)} " + ("setcmykcolor" if eps else "k"))
        else:
            out.append(f"{fmt(r)} {fmt(g)} {fmt(b)} " + ("setrgbcolor" if eps else "rg"))
        if eps:
            out.append("newpath")
        for sub in parse_d(p["d"]):
            for seg in sub:
                if seg[0] == "M":
                    out.append(f"{X(seg[1])} {Y(seg[2])} " + ("moveto" if eps else "m"))
                elif seg[0] == "L":
                    out.append(f"{X(seg[1])} {Y(seg[2])} " + ("lineto" if eps else "l"))
                else:
                    out.append(f"{X(seg[1])} {Y(seg[2])} {X(seg[3])} {Y(seg[4])} {X(seg[5])} {Y(seg[6])} " +
                               ("curveto" if eps else "c"))
            out.append("closepath" if eps else "h")
        out.append("eofill" if eps else "f*")
    return "\n".join(out) + "\n"


def vector_pdf(vec: dict, path: str, width_mm: float | None = None, cmyk: bool = False, title: str = "") -> str:
    W, H, s = _size_pt(vec, width_mm)
    content = zlib.compress(_ps_paths(vec, s, H, cmyk, eps=False).encode("ascii"))
    t = "".join(c if 32 <= ord(c) < 127 and c not in "()\\" else "?" for c in title) or "PhootFlow"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W:.3f} {H:.3f}] /TrimBox [0 0 {W:.3f} {H:.3f}] "
        f"/Contents 4 0 R /Resources << >> >>",
        None,  # contenido (binario)
        f"<< /Title ({t}) /Creator (PhootFlow) /Producer (PhootFlow) >>",
    ]
    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(buf))
        if o is None:
            buf += f"{i} 0 obj\n<< /Length {len(content)} /Filter /FlateDecode >>\nstream\n".encode()
            buf += content + b"\nendstream\nendobj\n"
        else:
            buf += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        buf += f"{o:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    _write(path, bytes(buf))
    return path


def vector_eps(vec: dict, path: str, width_mm: float | None = None, cmyk: bool = False, title: str = "") -> str:
    W, H, s = _size_pt(vec, width_mm)
    t = "".join(c for c in title if 32 <= ord(c) < 127) or "PhootFlow"
    head = ("%!PS-Adobe-3.0 EPSF-3.0\n"
            f"%%BoundingBox: 0 0 {int(np.ceil(W))} {int(np.ceil(H))}\n"
            f"%%HiResBoundingBox: 0 0 {W:.3f} {H:.3f}\n"
            f"%%Title: {t}\n%%Creator: PhootFlow\n%%LanguageLevel: 2\n%%Pages: 1\n%%EndComments\n"
            "%%Page: 1 1\ngsave\n")
    body = _ps_paths(vec, s, H, cmyk, eps=True)
    _write(path, (head + body + "grestore\nshowpage\n%%EOF\n").encode("ascii"))
    return path


def vector_svg(vec: dict, path: str, width_mm: float | None = None, title: str = "") -> str:
    vw, vh = vec["viewbox"]
    size = f' width="{width_mm:.3f}mm" height="{width_mm * vh / vw:.3f}mm"' if width_mm else \
        f' width="{vw}" height="{vh}"'
    esc = lambda x: x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    body = "\n".join(f'  <path fill="{p["fill"]}" fill-rule="evenodd" d="{p["d"]}"/>' for p in vec["paths"])
    txt = (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg"{size} '
           f'viewBox="0 0 {vw} {vh}">\n  <title>{esc(title or "PhootFlow")}</title>\n{body}\n</svg>\n')
    _write(path, txt.encode("utf-8"))
    return path


def _bezier(p0, p1, p2, p3, n):
    t = np.linspace(0, 1, n + 1)[1:, None]
    p0, p1, p2, p3 = (np.array(v, float) for v in (p0, p1, p2, p3))
    return ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * t * t * p2 + t ** 3 * p3


def polygons(d: str, tol: float = 0.5) -> list[np.ndarray]:
    """Subtrazados de `d` como polígonos cerrados (las curvas, aplanadas con error ≲ `tol` unidades)."""
    out = []
    for sub in parse_d(d):
        pts = []
        cur = None
        for seg in sub:
            if seg[0] in "ML":
                cur = (seg[1], seg[2])
                pts.append(cur)
            else:
                c1, c2, e = (seg[1], seg[2]), (seg[3], seg[4]), (seg[5], seg[6])
                L = np.hypot(c1[0] - cur[0], c1[1] - cur[1]) + np.hypot(c2[0] - c1[0], c2[1] - c1[1]) + \
                    np.hypot(e[0] - c2[0], e[1] - c2[1])
                n = int(min(64, max(2, np.sqrt(L / max(tol, 1e-3)))))
                pts.extend(map(tuple, _bezier(cur, c1, c2, e, n)))
                cur = e
        if len(pts) >= 3:
            out.append(np.array(pts, float))
    return out


def _aci(r: float, g: float, b: float) -> int:
    """Color más cercano de los 7 básicos de AutoCAD (lo que entienden todos los programas de corte)."""
    base = {1: (1, 0, 0), 2: (1, 1, 0), 3: (0, 1, 0), 4: (0, 1, 1), 5: (0, 0, 1), 6: (1, 0, 1), 7: (0, 0, 0),
            8: (0.5, 0.5, 0.5), 9: (0.75, 0.75, 0.75)}
    return min(base, key=lambda k: sum((a - c) ** 2 for a, c in zip(base[k], (r, g, b))))


def vector_dxf(vec: dict, path: str, width_mm: float | None = None, skip_white: bool = True) -> str:
    """DXF R12 en milímetros: una capa por color (C_RRGGBB) con polilíneas cerradas, eje y hacia arriba.

    Las capas del vectorizado van acumuladas (cada color cubre lo de encima); para cortar vinilo eso es lo que
    se quiere (una pieza por color, sin rendijas). El blanco de fondo se omite si `skip_white`.
    """
    vw, vh = vec["viewbox"]
    s = (width_mm / vw) if width_mm else (MM_PER_IN / 96.0)
    H = vh * s
    lines = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$INSUNITS", "70", "4",
             "9", "$EXTMIN", "10", "0", "20", "0", "9", "$EXTMAX", "10", f"{vw * s:.3f}", "20", f"{H:.3f}",
             "0", "ENDSEC"]
    layers = []
    for p in vec["paths"]:
        name = "C_" + p["fill"].lstrip("#").upper()
        if name not in [n for n, _ in layers]:
            layers.append((name, _aci(*_rgb(p["fill"]))))
    lines += ["0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(layers))]
    for n, c in layers:
        lines += ["0", "LAYER", "2", n, "70", "0", "62", str(c), "6", "CONTINUOUS"]
    lines += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for p in vec["paths"]:
        if skip_white and p["fill"].upper() in ("#FFFFFF",) and len(vec["paths"]) > 1:
            continue
        name = "C_" + p["fill"].lstrip("#").upper()
        for poly in polygons(p["d"], tol=0.05 / max(s, 1e-6)):
            lines += ["0", "POLYLINE", "8", name, "66", "1", "70", "1", "10", "0", "20", "0", "30", "0"]
            for x, y in poly:
                lines += ["0", "VERTEX", "8", name, "10", f"{x * s:.4f}", "20", f"{H - y * s:.4f}", "30", "0"]
            lines += ["0", "SEQEND", "8", name]
    lines += ["0", "ENDSEC", "0", "EOF"]
    _write(path, ("\n".join(lines) + "\n").encode("ascii"))
    return path


def save_vector(vec: dict, path: str, width_mm: float | None = None, cmyk: bool = False, title: str = "") -> str:
    """Guarda un vectorizado en el formato de la extensión (svg, pdf, eps, dxf)."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext == "svg":
        return vector_svg(vec, path, width_mm, title)
    if ext == "pdf":
        return vector_pdf(vec, path, width_mm, cmyk, title)
    if ext in ("eps", "ps"):
        return vector_eps(vec, path, width_mm, cmyk, title)
    if ext == "dxf":
        return vector_dxf(vec, path, width_mm)
    raise ValueError(f"Formato vectorial no admitido: .{ext}")


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


# ------------------------------------------------------------------ Qt

def qimage_to_array(img) -> np.ndarray:
    """QImage → array RGBA uint8 (copia)."""
    from PySide6.QtGui import QImage
    q = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = q.width(), q.height()
    a = np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
    return a.copy()
