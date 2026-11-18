"""Formatos de exportación de Graphics y Edits: imagen, animación y vectorizado para imprenta (sin red)."""
import os
import re
import sys
import tempfile
import unittest
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from PIL import Image  # noqa: E402

import export_formats as EF  # noqa: E402
import graphics_engine as G  # noqa: E402


def _img(w=120, h=80, alpha=False):
    a = np.zeros((h, w, 4 if alpha else 3), np.uint8)
    a[..., 0] = np.linspace(0, 255, w)[None, :]
    a[: h // 2, :, 1] = 200
    a[h // 4: 3 * h // 4, w // 4: 3 * w // 4, 2] = 255
    if alpha:
        a[..., 3] = 255
        a[:10, :10, 3] = 0
    return a


def _design():
    d = G.new_design(400, 300, name="Prueba")
    d["elements"].append(G.shape_el(d, "rect", x=40, y=40, w=200, h=120, fill="#E11D48"))
    d["elements"].append(G.text_el(d, "Hola", "title"))
    return d


class RasterTest(unittest.TestCase):
    def test_all_raster_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ext in EF.RASTER:
                for alpha in (False, True):
                    p = os.path.join(tmp, f"a{int(alpha)}.{ext}")
                    EF.save_raster(_img(alpha=alpha), p, quality=85, dpi=300)
                    self.assertTrue(os.path.getsize(p) > 100, ext)
                    self.assertFalse(os.path.exists(p + ".part"))
                    if ext != "pdf":
                        with Image.open(p) as im:
                            self.assertEqual(im.size, (120, 80), ext)
            with Image.open(os.path.join(tmp, "a1.png")) as im:
                self.assertEqual(im.mode, "RGBA")
                self.assertAlmostEqual(im.info["dpi"][0], 300, delta=1)
            p = os.path.join(tmp, "c.tif")
            EF.save_raster(_img(), p, cmyk=True)
            with Image.open(p) as im:
                self.assertEqual(im.mode, "CMYK")
            p = os.path.join(tmp, "h.tif")
            EF.save_raster(_img().astype(np.float32) / 255, p, bits16=True)
            import cv2
            self.assertEqual(cv2.imread(p, cv2.IMREAD_UNCHANGED).dtype, np.uint16)

    def test_animation(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = [_img(), _img()[::-1].copy(), _img(alpha=True)]
            for ext in EF.ANIMATED:
                p = EF.save_animation(frames, os.path.join(tmp, "a." + ext), 400)
                with Image.open(p) as im:
                    self.assertEqual(getattr(im, "n_frames", 1), 3, ext)
            with self.assertRaises(ValueError):
                EF.save_animation(frames, os.path.join(tmp, "a.png"))


class VectorTest(unittest.TestCase):
    def setUp(self):
        self.vec = G.vectorize(_img(alpha=False), mode="color", colors=3)
        self.assertTrue(self.vec["paths"])

    def test_parse_and_polygons(self):
        subs = EF.parse_d("M 0 0 L 10 0 C 10 5 5 10 0 10 Z M 2 2 L 3 2 L 3 3 Z")
        self.assertEqual(len(subs), 2)
        self.assertEqual(subs[0][2][0], "C")
        polys = EF.polygons("M 0 0 L 10 0 C 10 5 5 10 0 10 Z")
        self.assertGreater(len(polys[0]), 4)
        self.assertEqual(EF.rgb_to_cmyk(0, 0, 0), (0.0, 0.0, 0.0, 1.0))
        c, m, y, k = EF.rgb_to_cmyk(1, 0, 0)
        self.assertAlmostEqual(m, 1) and self.assertAlmostEqual(y, 1)

    def test_vector_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            # SVG con tamaño real
            p = EF.save_vector(self.vec, os.path.join(tmp, "v.svg"), 100.0, title="Logo & co")
            import xml.etree.ElementTree as ET
            root = ET.parse(p).getroot()
            self.assertEqual(root.get("width"), "100.000mm")
            self.assertGreater(len(root.findall("{http://www.w3.org/2000/svg}path")), 0)
            # PDF: tamaño en puntos, trazados, sin imágenes; CMYK con el operador k
            for cmyk in (False, True):
                p = EF.save_vector(self.vec, os.path.join(tmp, f"v{int(cmyk)}.pdf"), 100.0, cmyk)
                with open(p, "rb") as f:
                    data = f.read()
                self.assertTrue(data.startswith(b"%PDF-1.4"))
                box = [float(x) for x in re.search(rb"/MediaBox \[([\d. ]+)\]", data).group(1).split()]
                self.assertAlmostEqual(box[2], 100 / 25.4 * 72, delta=0.5)
                self.assertNotIn(b"/Image", data)
                stream = zlib.decompress(re.search(rb"stream\n(.*)\nendstream", data, re.S).group(1)).decode()
                self.assertIn("\nf*\n", stream)
                self.assertIn(" k\n" if cmyk else " rg\n", stream)
                # tabla xref coherente: cada desplazamiento apunta a «n 0 obj»
                xref = int(re.search(rb"startxref\n(\d+)", data).group(1))
                offs = [int(x) for x in re.findall(rb"(\d{10}) 00000 n", data[xref:])]
                for i, o in enumerate(offs, 1):
                    self.assertTrue(data[o:].startswith(f"{i} 0 obj".encode()))
            p = EF.save_vector(self.vec, os.path.join(tmp, "v.eps"), 50.0, True)
            with open(p) as f:
                txt = f.read()
            self.assertTrue(txt.startswith("%!PS-Adobe-3.0 EPSF-3.0"))
            self.assertIn("setcmykcolor", txt)
            self.assertIn("eofill", txt)
            p = EF.save_vector(self.vec, os.path.join(tmp, "v.dxf"), 100.0)
            with open(p) as f:
                txt = f.read().split("\n")
            self.assertIn("POLYLINE", txt)
            self.assertEqual(txt[-2], "EOF")
            # vértices dentro de los 100 mm de ancho
            vx = [float(txt[i + 1]) for i in range(len(txt) - 1) if txt[i] == "10" and "VERTEX" in txt[i - 4:i]]
            self.assertTrue(vx and max(vx) <= 100.01 and min(vx) >= -0.01)
            with self.assertRaises(ValueError):
                EF.save_vector(self.vec, os.path.join(tmp, "v.ai"))


class GraphicsExportTest(unittest.TestCase):
    def test_design_formats(self):
        cache = G.ImageCache()
        d = _design()
        with tempfile.TemporaryDirectory() as tmp:
            for ext in ("png", "jpg", "webp", "avif", "tif", "gif", "bmp"):
                p = G.export_raster(d, os.path.join(tmp, "d." + ext), cache, 0.5)
                with Image.open(p) as im:
                    self.assertEqual(im.size, (200, 150), ext)
            p = G.export_raster(d, os.path.join(tmp, "c.tif"), cache, 1.0, cmyk=True)
            with Image.open(p) as im:
                self.assertEqual(im.mode, "CMYK")
            G.add_page(d)
            d["elements"].append(G.shape_el(d, "ellipse", x=10, y=10, w=100, h=100, fill="#2563EB"))
            p = G.export_animation(d, os.path.join(tmp, "anim.gif"), cache, 0.5, 300)
            with Image.open(p) as im:
                self.assertEqual(im.n_frames, 2)
            p = G.export_image_pdf(d, os.path.join(tmp, "img.pdf"), cache)
            with open(p, "rb") as f:
                self.assertEqual(len(re.findall(rb"/Type\s*/Page(?!s)", f.read())), 2)

    def test_design_vectorized(self):
        cache = G.ImageCache()
        d = G.new_design(148, 210, unit="mm", name="Cartel")
        d["elements"].append(G.shape_el(d, "rect", x=100, y=100, w=900, h=600, fill="#E11D48"))
        self.assertAlmostEqual(G.design_width_mm(d), 148, delta=0.5)
        with tempfile.TemporaryDirectory() as tmp:
            for ext in ("svg", "pdf", "eps", "dxf"):
                p = G.export_vectorized(d, os.path.join(tmp, "v." + ext), cache, cmyk=True, colors=3,
                                        max_side=500)
                self.assertGreater(os.path.getsize(p), 100, ext)
            with open(os.path.join(tmp, "v.pdf"), "rb") as f:
                data = f.read()
            box = [float(x) for x in re.search(rb"/MediaBox \[([\d. ]+)\]", data).group(1).split()]
            self.assertAlmostEqual(box[2], 148 / 25.4 * 72, delta=1.0)
            self.assertNotIn(b"/Image", data)


if __name__ == "__main__":
    unittest.main()
