"""Graphics al estilo Canva: caché de fotos, ajustes, reflujo, paletas de marca, animaciones y vídeo (sin red ni
fotos reales)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import graphics_engine as G  # noqa: E402
import graphics_magic as M  # noqa: E402


def _photo(w=1200, h=800):
    p = os.path.join(tempfile.mkdtemp(), "f.jpg")
    y, x = np.mgrid[0:h, 0:w]
    cv2.imwrite(p, np.dstack([x % 256, y % 256, (x + y) % 256]).astype(np.uint8))
    return p


class CacheTest(unittest.TestCase):
    def test_base_is_reused_for_sizes_and_filters(self):
        c = G.ImageCache()
        p = _photo()
        a = c.get(p, 512)
        self.assertEqual(max(a.width(), a.height()), 512)
        n = len(c.base)
        b = c.get(p, 256, bw=True)
        self.assertEqual(len(c.base), n)   # otro tamaño y B/N: de la misma base, sin volver a leer
        self.assertTrue(b.pixelColor(10, 10).red() == b.pixelColor(10, 10).green() == b.pixelColor(10, 10).blue())

    def test_async_returns_nearest_then_notifies(self):
        import threading
        c = G.ImageCache()
        p = _photo()
        c.get(p, 256)
        done = threading.Event()
        c.notify = done.set
        img = c.get(p, 1024, wait=False)
        self.assertEqual(max(img.width(), img.height()), 256)   # lo que había, mientras llega lo pedido
        self.assertTrue(done.wait(20))
        self.assertEqual(max(c.get(p, 1024, wait=False).width(), 1), 1024)

    def test_adjust_rgb(self):
        a = np.full((20, 20, 3), 128, np.uint8)
        self.assertGreater(G.adjust_rgb(a, {"bright": 50}).mean(), 140)
        v = G.adjust_rgb(a, {"vignette": 100})
        self.assertLess(v[0, 0].mean(), v[10, 10].mean())


class ReflowTest(unittest.TestCase):
    def test_story_keeps_anchors(self):
        d = G.design_from_preset("Instagram vertical 4:5")
        G.apply_template(d, "Post de evento")
        G.reflow(d, 1080, 1920)
        self.assertEqual((d["w"], d["h"]), (1080.0, 1920.0))
        band = [e for e in d["elements"] if e["type"] == "shape"][0]
        self.assertAlmostEqual(band["y"] + band["h"], 1920, 0)   # la banda sigue pegada abajo
        img = [e for e in d["elements"] if e["type"] == "image"][0]
        self.assertEqual(img["y"], 0.0)
        self.assertGreater(img["h"], 1300)   # la foto de arriba crece con la página


class BrandTest(unittest.TestCase):
    def test_palette_style(self):
        st = M.style_from_palette("Mía", ["#C9A86A", "#1E2B24", "#F3F1EA"])
        self.assertEqual(st[1], "#1E2B24")
        self.assertEqual(st[2], "#F3F1EA")
        self.assertEqual(st[3], "#C9A86A")
        d = G.design_from_preset("Instagram vertical 4:5")
        G.apply_template(d, "Post de evento")
        fonts = [e["font"] for e in d["elements"] if e["type"] == "text"]
        M.restyle(d, st)   # sin fuentes: solo colores
        self.assertEqual(fonts, [e["font"] for e in d["elements"] if e["type"] == "text"])
        self.assertLess(M._lum(M._rgb(d["bg"])[0]), 0.25)   # el fondo oscuro, con el oscuro de la paleta
        self.assertIn("#C9A86A", M.design_palette(d))      # y el acento, el de la paleta


class AnimationTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(G.anim_state("", 0, 0, 100, 100), (1.0, 0.0, 0.0, 1.0))
        op, dx, dy, s = G.anim_state("rise", 0, 0.0, 100, 100)
        self.assertEqual(op, 0.0)
        self.assertGreater(dy, 0)
        self.assertEqual(G.anim_state("zoom", 2, 5.0, 100, 100), (1.0, 0.0, 0.0, 1.0))

    def test_video_and_gif(self):
        d = G.new_design(320, 240, bg="#202020")
        d["elements"].append(G.shape_el(d, "rect", fill="#FF0000", anim="slide"))
        G.sync_page(d)
        d["pages"][0]["dur"] = 1.0
        self.assertTrue(G.animated(d))
        c = G.ImageCache()
        first = G.render_frame(G.page_views(d)[0], c, 1.0, 0.0)
        last = G.render_frame(G.page_views(d)[0], c, 1.0, 0.9)
        self.assertNotEqual(first.pixelColor(160, 120).red(), last.pixelColor(160, 120).red())
        out = tempfile.mkdtemp()
        v = G.export_video(d, os.path.join(out, "a.mp4"), c, 1.0, 10)
        cap = cv2.VideoCapture(v)
        self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 10)
        cap.release()
        g = G.export_animation(d, os.path.join(out, "a.gif"), c)
        self.assertTrue(os.path.getsize(g) > 0)


if __name__ == "__main__":
    unittest.main()
