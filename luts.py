"""LUT de color (.cube) asociados a cada tipo de sesión.

Se guardan en %APPDATA%/PhootFlow/luts/<tipo>/ (y luts/_comunes/ para todos los tipos). Admite .cube 3D
(LUT_3D_SIZE) y 1D (LUT_1D_SIZE) con DOMAIN_MIN/DOMAIN_MAX, sobre imágenes sRGB 0..1.
"""
from __future__ import annotations

import os
import re
import shutil
import threading

import numpy as np

COMMON = "_comunes"
_CACHE: dict[str, dict] = {}
_LOCK = threading.Lock()


def folder_name(kind: str) -> str:
    return re.sub(r"[^\w\- ]", "_", kind).strip() or COMMON


def luts_dir(cfg_dir: str, kind: str = COMMON) -> str:
    return os.path.join(cfg_dir, "luts", folder_name(kind))


def available(cfg_dir: str, kind: str) -> list[tuple[str, str]]:
    """[(nombre visible, ruta)] de los LUT del tipo `kind` y de los comunes."""
    out = []
    for k, prefix in ((kind, ""), (COMMON, "Común: ")):
        d = luts_dir(cfg_dir, k)
        if k and os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(".cube"):
                    out.append((prefix + os.path.splitext(f)[0], os.path.join(d, f)))
        if kind == COMMON:
            break
    return out


def import_luts(cfg_dir: str, kind: str, paths: list[str]) -> list[str]:
    """Copia los .cube a la carpeta del tipo. Comprueba que se pueden leer antes de copiarlos."""
    d = luts_dir(cfg_dir, kind)
    os.makedirs(d, exist_ok=True)
    done = []
    for p in paths:
        load(p)  # lanza ValueError si no es un .cube válido
        dest = os.path.join(d, os.path.basename(p))
        shutil.copy2(p, dest)
        done.append(dest)
    return done


def load(path: str) -> dict:
    """Lee un .cube. Devuelve {'size', 'table', 'dmin', 'dmax', 'is3d'} (con caché)."""
    with _LOCK:
        if path in _CACHE:
            return _CACHE[path]
    size, is3d, dmin, dmax, rows = 0, True, np.zeros(3, np.float32), np.ones(3, np.float32), []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            u = t.upper()
            if u.startswith("TITLE"):
                continue
            if u.startswith("LUT_3D_SIZE"):
                size, is3d = int(t.split()[1]), True
            elif u.startswith("LUT_1D_SIZE"):
                size, is3d = int(t.split()[1]), False
            elif u.startswith("DOMAIN_MIN"):
                dmin = np.array([float(x) for x in t.split()[1:4]], np.float32)
            elif u.startswith("DOMAIN_MAX"):
                dmax = np.array([float(x) for x in t.split()[1:4]], np.float32)
            elif u.startswith(("LUT_1D_INPUT_RANGE", "LUT_3D_INPUT_RANGE")):
                lo, hi = (float(x) for x in t.split()[1:3])
                dmin, dmax = np.full(3, lo, np.float32), np.full(3, hi, np.float32)
            elif re.match(r"^[-+0-9.eE]", t):
                vals = t.split()
                if len(vals) >= 3:
                    rows.append([float(v) for v in vals[:3]])
    if not size:
        raise ValueError(f"{os.path.basename(path)}: no es un .cube válido (falta LUT_3D_SIZE / LUT_1D_SIZE)")
    need = size ** 3 if is3d else size
    if len(rows) < need:
        raise ValueError(f"{os.path.basename(path)}: tiene {len(rows)} filas y harían falta {need}")
    table = np.asarray(rows[:need], np.float32)
    if is3d:  # en .cube el rojo varía más rápido: índice [b, g, r]
        table = table.reshape(size, size, size, 3)
    lut = {"size": size, "table": table, "dmin": dmin, "dmax": dmax, "is3d": is3d}
    with _LOCK:
        _CACHE[path] = lut
    return lut


_DENSE: dict[str, np.ndarray] = {}
DENSE_MIN_PIXELS = 2_000_000  # a partir de aquí compensa la tabla de 256³ (fotos a resolución completa)


def _dense(path: str) -> np.ndarray:
    """Tabla 256x256x256 (uint8, ~50 MB) con el LUT ya interpolado para cada color de 8 bits. Se calcula
    una vez por LUT (unos segundos) y luego aplicar el LUT a una foto de 26 MP cuesta ~1 s en vez de ~11 s."""
    with _LOCK:
        t = _DENSE.get(path)
    if t is not None:
        return t
    g = np.arange(256, dtype=np.float32) / 255.0
    t = np.empty((256, 256, 256, 3), np.uint8)  # [r, g, b]
    for r in range(0, 256, 16):
        rr, gg, bb = np.meshgrid(g[r:r + 16], g, g, indexing="ij")
        block = np.stack([rr, gg, bb], -1).reshape(-1, 256, 3)
        t[r:r + 16] = (_trilinear(block, path, 1.0) * 255 + 0.5).astype(np.uint8).reshape(-1, 256, 256, 3)
    with _LOCK:
        _DENSE.clear()  # una sola tabla en memoria: en una sesión se usa un único LUT
        _DENSE[path] = t
    return t


def apply(rgb01: np.ndarray, path: str, strength: float = 1.0, chunk_rows: int = 256) -> np.ndarray:
    """Aplica el LUT. Imágenes grandes: tabla densa de 8 bits (rápido; el resultado se guarda en JPEG de
    8 bits de todos modos). Pequeñas (miniaturas, vistas previas): interpolación trilineal exacta."""
    if not path or strength <= 0:
        return rgb01
    if rgb01.shape[0] * rgb01.shape[1] >= DENSE_MIN_PIXELS:
        t = _dense(path).reshape(-1, 3)
        out = np.empty_like(rgb01, dtype=np.float32)
        for y0 in range(0, rgb01.shape[0], 512):
            src = np.clip(rgb01[y0:y0 + 512], 0, 1)
            q = (src * 255 + 0.5).astype(np.int32)
            res = t[(q[..., 0] << 16) | (q[..., 1] << 8) | q[..., 2]].astype(np.float32) / 255.0
            out[y0:y0 + 512] = src + (res - src) * strength if strength < 1 else res
        return out
    return _trilinear(rgb01, path, strength, chunk_rows)


def _trilinear(rgb01: np.ndarray, path: str, strength: float, chunk_rows: int = 256) -> np.ndarray:
    lut = load(path)
    n, tab = lut["size"], lut["table"]
    out = np.empty_like(rgb01, dtype=np.float32)
    span = np.maximum(lut["dmax"] - lut["dmin"], 1e-6)
    for y0 in range(0, rgb01.shape[0], chunk_rows):
        x = np.clip((rgb01[y0:y0 + chunk_rows].astype(np.float32) - lut["dmin"]) / span, 0, 1) * (n - 1)
        if not lut["is3d"]:
            grid = np.arange(n, dtype=np.float32)
            res = np.stack([np.interp(x[..., c], grid, tab[:, c]) for c in range(3)], -1)
        else:
            i0 = np.minimum(np.floor(x).astype(np.int32), n - 2)
            f = x - i0
            r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
            fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
            c = lambda db, dg, dr: tab[b0 + db, g0 + dg, r0 + dr]  # noqa: E731
            c00 = c(0, 0, 0) * (1 - fr) + c(0, 0, 1) * fr
            c01 = c(0, 1, 0) * (1 - fr) + c(0, 1, 1) * fr
            c10 = c(1, 0, 0) * (1 - fr) + c(1, 0, 1) * fr
            c11 = c(1, 1, 0) * (1 - fr) + c(1, 1, 1) * fr
            res = (c00 * (1 - fg) + c01 * fg) * (1 - fb) + (c10 * (1 - fg) + c11 * fg) * fb
        src = rgb01[y0:y0 + chunk_rows]
        out[y0:y0 + chunk_rows] = src + (res - src) * strength if strength < 1 else res
    return np.clip(out, 0, 1)


def write_cube(path: str, size: int, fn) -> None:
    """Escribe un .cube 3D a partir de una función rgb(Nx3)->rgb(Nx3). Útil para pruebas y para exportar."""
    g = np.linspace(0, 1, size, dtype=np.float32)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    rgb = np.stack([r, gg, b], -1).reshape(-1, 3)
    res = np.clip(fn(rgb), 0, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"TITLE \"PhootFlow\"\nLUT_3D_SIZE {size}\n")
        for row in res:
            f.write(f"{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n")
