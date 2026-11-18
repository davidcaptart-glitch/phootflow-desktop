"""Herramientas de Edits inspiradas en Photoshop: Claridad, Borrar neblina, Granulado, Volver a iluminar, looks,
relleno de color, varita / bote de pintura, parche, armonizar, pinceles artísticos y aumento de escala.
Solo imágenes sintéticas."""
import os
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import edits_adjust as A  # noqa: E402
import edits_engine as EE  # noqa: E402


def scene(h=400, w=600, seed=0):
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.float32)
    img[:] = np.linspace(0.15, 0.85, w, dtype=np.float32)[None, :, None]
    img[h // 4:h // 2, w // 3:w // 2] = [0.85, 0.15, 0.2]
    return np.clip(img + rng.normal(0, 0.02, img.shape).astype(np.float32), 0, 1)


class NewAdjustments(unittest.TestCase):
    def test_all_registered_and_finite(self):
        img = scene()
        for k in ("clarity", "dehaze", "grain", "relight", "look", "solid"):
            self.assertIn(k, A.ORDER)
            out = A.apply(img, k, A.defaults(k))
            self.assertEqual(out.shape, img.shape)
            self.assertTrue(np.isfinite(out).all() and out.min() >= 0 and out.max() <= 1, k)
        for name in A.LOOKS:
            out = A.apply(img, "look", {"look": name, "amount": 100})
            self.assertGreater(float(np.abs(out - img).mean()), 0.005, name)
        self.assertTrue(np.allclose(A.apply(img, "look", {"look": "golden", "amount": 0}), img, atol=1e-5))

    def test_clarity_adds_local_contrast(self):
        img = scene()
        y = A.lum(img)
        hp = lambda x: float(np.abs(x - cv2.GaussianBlur(x, (0, 0), 6)).mean())  # noqa: E731
        up = A.lum(A.apply(img, "clarity", {"amount": 80, "radius": 2.0}))
        down = A.lum(A.apply(img, "clarity", {"amount": -80, "radius": 2.0}))
        self.assertGreater(hp(up), hp(y))
        self.assertLess(hp(down), hp(y))

    def test_dehaze_restores_contrast(self):
        img = scene()
        hazy = img * 0.5 + 0.45  # neblina: todo hacia un gris claro
        clear = A.apply(hazy, "dehaze", {"amount": 80})
        self.assertGreater(float(clear.std()), float(hazy.std()) * 1.2)
        more = A.apply(img, "dehaze", {"amount": -80})
        self.assertLess(float(more.std()), float(img.std()))

    def test_grain_same_look_at_any_size(self):
        flat = np.full((300, 450, 3), 0.5, np.float32)
        p = dict(A.defaults("grain"), amount=60)
        small = A.apply(flat, "grain", p) - 0.5
        big = A.apply(cv2.resize(flat, (900, 600)), "grain", p) - 0.5
        self.assertTrue(np.array_equal(A.apply(flat, "grain", p), A.apply(flat, "grain", p)))  # determinista
        down = cv2.resize(big, (450, 300), interpolation=cv2.INTER_AREA)
        cc = np.corrcoef(down[..., 0].ravel(), small[..., 0].ravel())[0, 1]
        self.assertGreater(cc, 0.6)  # el mismo dibujo de grano a 2 tamaños

    def test_relight_brightens_near_the_light(self):
        img = np.full((300, 400, 3), 0.4, np.float32)
        out = A.apply(img, "relight", dict(A.defaults("relight"), x=0.2, y=0.5, ambient=0, color="#ffffff"))
        self.assertGreater(float(out[150, 80].mean()), float(out[150, 380].mean()) + 0.1)
        self.assertGreater(float(out[150, 80].mean()), 0.45)

    def test_solid(self):
        out = A.apply(scene(), "solid", {"color": "#ff0000"})
        self.assertTrue(np.allclose(out[0, 0], [1, 0, 0]))


class Selections(unittest.TestCase):
    def test_wand_contiguous_and_global(self):
        img = np.full((300, 400, 3), 0.2, np.float32)
        img[50:150, 50:150] = [0.9, 0.1, 0.1]
        img[200:280, 250:380] = [0.9, 0.1, 0.1]
        one = EE.selection_mask(img.shape, {"shapes": [{"type": "wand", "pt": [0.25, 0.33], "tol": 20}]}, img)
        both = EE.selection_mask(img.shape, {"shapes": [{"type": "wand", "pt": [0.25, 0.33], "tol": 20,
                                                         "contiguous": False}]}, img)
        self.assertAlmostEqual(float((one > 0.5).sum()), 100 * 100, delta=600)
        self.assertAlmostEqual(float((both > 0.5).sum()), 100 * 100 + 80 * 130, delta=900)

    def test_bucket_is_a_masked_color_layer(self):
        img = np.full((300, 400, 3), 0.2, np.float32)
        img[50:150, 50:150] = 0.9
        doc = EE.new_doc()
        lay = EE.add_layer(doc, "adjust", adj="solid", p={"color": "#00ff00"})
        lay["mask"] = {"shapes": [{"type": "wand", "pt": [0.25, 0.33], "tol": 20, "op": "add"}], "feather": 0}
        out, _ = EE.compose(img, doc, [0.0, 0.0, 1.0, 1.0])
        self.assertTrue(np.allclose(out[100, 100], [0, 1, 0], atol=0.02))
        self.assertTrue(np.allclose(out[250, 300], 0.2, atol=0.02))


class PatchAndHarmonize(unittest.TestCase):
    def test_patch_covers_with_source(self):
        img = np.full((300, 400, 3), 0.5, np.float32)
        img[100:140, 100:140] = 0.05  # mancha a tapar
        sel = {"shapes": [{"type": "rect", "box": [0.23, 0.31, 0.37, 0.49], "op": "add"}], "feather": 0}
        op = {"tool": "heal", "r": 0.01, "pts": [], "sel": sel, "src": [0.35, 0.0]}
        out = EE.apply_retouch(img, {"ops": [op]})
        self.assertAlmostEqual(float(out[120, 120].mean()), 0.5, delta=0.06)

    def test_harmonize_moves_toward_background(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "amarillo.png")
            cv2.imwrite(p, np.full((80, 80, 3), (40, 230, 230), np.uint8))  # BGR: amarillo
            bg = np.zeros((400, 600, 3), np.float32) + np.array([0.1, 0.15, 0.45], np.float32)
            outs = []
            for h in (0, 100):
                doc = EE.new_doc()
                EE.add_layer(doc, "image", path=p, width=20, x=0.5, y=0.5, harmonize=h)
                outs.append(EE.compose(bg, doc, [0.0, 0.0, 1.0, 1.0])[0][200, 300])
            dist = lambda c: float(np.abs(c - bg[0, 0]).sum())  # noqa: E731
            self.assertLess(dist(outs[1]), dist(outs[0]) * 0.75)


class Brushes(unittest.TestCase):
    def test_textures(self):
        base = {"r": 0.03, "h": 0.3, "o": 1, "c": "#ff0000", "pts": [[0.2, 0.3, 1], [0.5, 0.5, 1], [0.8, 0.4, 1]]}
        ref, _, _ = EE._stroke_alpha(600, 900, base)
        for b in EE.BRUSHES:
            if b == "round":
                continue
            a, _, _ = EE._stroke_alpha(600, 900, dict(base, b=b))
            self.assertEqual(a.shape, ref.shape)
            self.assertGreater(float(a.max()), 0.3, b)
            self.assertGreater(float(np.abs(a - ref).mean()), 0.005, b)
            again, _, _ = EE._stroke_alpha(600, 900, dict(base, b=b))
            self.assertTrue(np.array_equal(a, again), b)


class Upscale(unittest.TestCase):
    def test_double(self):
        img = scene(203, 301)
        big, alpha = EE.upscale(img, 2, np.ones(img.shape[:2], np.float32))
        self.assertEqual(big.shape, (406, 602, 3))
        self.assertEqual(alpha.shape, (406, 602))
        self.assertLess(float(np.abs(cv2.resize(big, (301, 203), interpolation=cv2.INTER_AREA) - img).mean()), 0.02)


class Gui(unittest.TestCase):
    def test_new_tools_in_view(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import edits
        with tempfile.TemporaryDirectory() as cfg:
            v = edits.EditsView(cfg)
            for key in ("wand", "patch", "bucket", "brush"):
                v.set_tool(key)
                self.assertEqual(v.tool, key)
            v.set_tool("brush")
            self.assertEqual(v.opt_mode.count(), len(edits.BRUSH_MODES))
            self.assertEqual(v.size.count(), 4)
            v.shutdown()


if __name__ == "__main__":
    unittest.main()
