"""Crea assets/luts/biblioteca.npz (los LUT que trae PhootFlow) a partir de las colecciones originales.

No forma parte del programa: se ejecuta a mano cuando cambia la biblioteca. Uso:

    python tools/crear_biblioteca_luts.py <carpeta con los clones>

La carpeta debe tener (ver docs/fuentes_luts.md; todas con licencia que permite redistribuirlas):
    t3mujinpack/            git clone https://github.com/t3mujinpack/t3mujinpack       (MIT)
    slaycamera-luts/        git clone https://github.com/slaycamera/luts                (CC BY 4.0)
    street-monochrome/      git clone https://github.com/sfi0zy/street-monochrome       (CC BY 4.0)
    rodaje-filtros/luts/    los .cube de la release de github.com/Dathinel/rodaje-filtros (CC0 1.0)

Todos se remuestrean a 33³ (los HaldCLUT de t3mujinpack son de nivel 12 = 144³) y se guardan a 12 bits
(`luts.pack_table`) en un único .npz comprimido (~5 MB). Cambio respecto al original: solo el formato, la
precisión y la resolución de la rejilla (lo exige CC BY: indicar si hubo cambios).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import luts as L  # noqa: E402

N = 33
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "luts")

T3_GROUPS = {"Black and White": "Película B/N", "Color Negative": "Película negativo",
             "Color Slide": "Película diapositiva"}
RODAJE = {"alto-contraste": "Alto contraste", "blanco-y-negro": "Blanco y negro", "calido": "Cálido",
          "cian-suave": "Cian suave", "cine": "Cine", "desvaido": "Desvaído", "frio": "Frío", "noche": "Noche",
          "pastel": "Pastel", "plata": "Plata", "sepia": "Sepia", "verde-azulado-naranja": "Verde azulado y naranja",
          "vivo": "Vivo"}


def _grid() -> np.ndarray:
    g = np.linspace(0, 1, N, dtype=np.float32)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r, gg, b], -1)  # [b, g, r, rgb], como luts.load


def from_hald(path: str) -> np.ndarray:
    """HaldCLUT PNG (el rojo varía más rápido) -> tabla N³ [b, g, r] por interpolación trilineal."""
    a = np.asarray(Image.open(path).convert("RGB"), np.float32) / 255.0
    side = a.shape[0]
    level = round(side ** (1 / 3))
    n = level * level
    assert level ** 3 == side == a.shape[1], f"{path}: no es un HaldCLUT"
    tab = a.reshape(n, n, n, 3)  # [b, g, r]
    x = _grid() * (n - 1)
    i0 = np.minimum(np.floor(x).astype(np.int32), n - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    c = lambda db, dg, dr: tab[b0 + db, g0 + dg, r0 + dr]  # noqa: E731
    c00 = c(0, 0, 0) * (1 - fr) + c(0, 0, 1) * fr
    c01 = c(0, 1, 0) * (1 - fr) + c(0, 1, 1) * fr
    c10 = c(1, 0, 0) * (1 - fr) + c(1, 0, 1) * fr
    c11 = c(1, 1, 0) * (1 - fr) + c(1, 1, 1) * fr
    return (c00 * (1 - fg) + c01 * fg) * (1 - fb) + (c10 * (1 - fg) + c11 * fg) * fb


def from_cube(path: str) -> np.ndarray:
    """.cube de cualquier tamaño -> tabla N³ (se evalúa el LUT en la rejilla de N puntos)."""
    lut = L.load(path)
    if lut["is3d"] and lut["size"] == N and np.allclose(lut["dmin"], 0) and np.allclose(lut["dmax"], 1):
        return lut["table"]
    return L._trilinear(_grid().reshape(-1, N, 3), path, 1.0).reshape(N, N, N, 3)


def main(src: str) -> None:
    items: list[tuple[str, dict, np.ndarray]] = []
    hald = os.path.join(src, "t3mujinpack", "haldcluts")
    for f in sorted(os.listdir(hald)):
        if not f.startswith("t3mujinpack - ") or not f.endswith(".png"):
            continue
        _, grp, name = os.path.splitext(f)[0].split(" - ", 2)
        items.append((f"t3mujinpack/{name}", {"group": T3_GROUPS[grp], "name": name, "col": "t3mujinpack"},
                      from_hald(os.path.join(hald, f))))
    d = os.path.join(src, "street-monochrome", "lut")
    for f in sorted(os.listdir(d)):
        if f.endswith(".cube"):
            name = os.path.splitext(f)[0].replace("Street Monochrome ", "")
            items.append((f"street-monochrome/{name}", {"group": "B/N de calle", "name": name,
                                                        "col": "street-monochrome"}, from_cube(os.path.join(d, f))))
    d = os.path.join(src, "slaycamera-luts", "luts")
    for f in sorted(os.listdir(d)):
        if f.endswith(".cube"):
            name = os.path.splitext(f)[0].split("-", 2)[2].capitalize()
            items.append((f"slay/{name}", {"group": "slay", "name": name, "col": "slay"},
                          from_cube(os.path.join(d, f))))
    d = os.path.join(src, "rodaje-filtros", "luts")
    for f in sorted(os.listdir(d)):
        if f.endswith(".cube"):
            key = os.path.splitext(f)[0]
            items.append((f"rodaje/{key}", {"group": "Looks", "name": RODAJE.get(key, key), "col": "rodaje-filtros"},
                          from_cube(os.path.join(d, f))))

    arrays, meta = {}, []
    for i, (key, m, tab) in enumerate(items):
        arrays[f"t{i}"] = L.pack_table(tab)
        meta.append({"id": key, **m, "array": f"t{i}"})
    os.makedirs(OUT, exist_ok=True)
    np.savez_compressed(os.path.join(OUT, "biblioteca.npz"), **arrays)
    with open(os.path.join(OUT, "biblioteca.json"), "w", encoding="utf-8") as f:
        json.dump({"size": N, "luts": meta}, f, ensure_ascii=False, indent=1)
    print(f"{len(items)} LUT -> {OUT} ({os.path.getsize(os.path.join(OUT, 'biblioteca.npz')) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
