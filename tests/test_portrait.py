"""Retoque de retrato en Edits: capas nuevas, corrector por frecuencias y máscaras de partes (sin fotos reales)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import edits_adjust as A  # noqa: E402
import edits_engine as EE  # noqa: E402


def _img(h=240, w=320, seed=3):
    rng = np.random.default_rng(seed)
    base = np.linspace(0.2, 0.8, w, dtype=np.float32)[None, :, None].repeat(h, 0).repeat(3, 2)
    return np.clip(base + rng.normal(0, 0.03, (h, w, 3)).astype(np.float32), 0, 1)


class NewAdjustments(unittest.TestCase):
    def test_sharpen_vignette_hair(self):
        img = _img()
        for k in ("sharpen", "vignette", "hair"):
            out = A.apply(img, k, A.defaults(k), (20, 10, 280, 220))
            self.assertEqual(out.shape, img.shape)
            self.assertEqual(out.dtype, np.float32)
            self.assertGreater(float(np.abs(out - img).mean()), 1e-4, k)
            self.assertTrue(0 <= out.min() and out.max() <= 1)

    def test_vignette_darkens_corners_not_center(self):
        img = np.full((200, 300, 3), 0.6, np.float32)
        out = A.apply(img, "vignette", dict(A.defaults("vignette"), amount=-80))
        self.assertLess(out[2, 2, 0], 0.5)
        self.assertAlmostEqual(float(out[100, 150, 0]), 0.6, places=2)

    def test_sharpen_threshold_leaves_flat_skin(self):
        flat = np.full((100, 100, 3), 0.5, np.float32)
        out = A.sharpen(flat, 100, 1.0, 4.0)
        self.assertLess(float(np.abs(out - flat).max()), 1e-4)

    def test_hair_density_fills_light_gaps(self):
        img = np.full((2048, 128, 3), 0.15, np.float32)  # el radio va referido a una foto de 2048 px
        img[1022:1026, 62:66] = 0.7  # un claro entre pelos oscuros
        out = A.hair(img, 0, 0, 0, 100)
        self.assertLess(float(out[1024, 64, 0]), 0.5)


class HealFrequency(unittest.TestCase):
    def test_color_only_keeps_texture_and_takes_tone(self):
        rng = np.random.default_rng(1)
        h, w = 200, 200
        tex = rng.normal(0, 0.05, (h, w, 1)).astype(np.float32)
        img = np.clip(np.full((h, w, 3), 0.5, np.float32) + tex, 0, 1)
        img[80:120, 80:120, 0] += 0.25  # una rojez en el centro
        op = {"tool": "heal", "r": 0.06, "pts": [[0.5, 0.5]], "src": [-0.3, 0.0], "freq": "color"}
        out = EE._apply_op(img, op)
        c = (slice(92, 108), slice(92, 108))
        # el rojo se va hacia el tono del origen...
        self.assertLess(float(out[c][..., 0].mean() - out[c][..., 1].mean()), 0.08)
        # ...y la textura (poros) del destino se queda
        hp_in = img[c][..., 1] - img[c][..., 1].mean()
        hp_out = out[c][..., 1] - out[c][..., 1].mean()
        self.assertGreater(float(np.corrcoef(hp_in.ravel(), hp_out.ravel())[0, 1]), 0.5)

    def test_texture_only_keeps_tone(self):
        img = _img(200, 200)
        img[80:120, 80:120] = 0.5  # una zona lisa (sin poros)
        op = {"tool": "heal", "r": 0.06, "pts": [[0.5, 0.5]], "src": [0.3, 0.0], "freq": "texture"}
        out = EE._apply_op(img, op)
        c = (slice(95, 105), slice(95, 105))
        self.assertAlmostEqual(float(out[c].mean()), 0.5, delta=0.03)  # el tono se queda
        self.assertGreater(float(out[c].std()), float(img[c].std()) + 0.005)  # llega la textura


class PortraitLayer(unittest.TestCase):
    def test_layer_defaults_and_order(self):
        doc = EE.new_doc()
        EE.add_layer(doc, "adjust", adj="curves")
        lay = EE.add_layer(doc, "portrait")
        EE.add_layer(doc, "skin")
        kinds = [l["kind"] for l in doc["layers"]]
        self.assertEqual(kinds.index("skin") + 1, kinds.index("portrait"))
        self.assertLess(kinds.index("portrait"), kinds.index("adjust"))
        for k, v in EE.PORTRAIT_DEFAULTS.items():
            self.assertEqual(lay[k], v)
        self.assertEqual(lay["name"], "Retrato")

    def test_no_faces_no_change(self):
        img = _img()
        lay = EE.new_layer("portrait", blush=60, lip_color="#c0304a")
        out, alpha = EE.apply_pixel_layer(img, lay, "")
        self.assertIsNone(alpha)
        self.assertTrue(np.allclose(out, img))

    def test_part_selection_without_faces_is_empty(self):
        img = _img()
        for part in ("lips", "beard", "iris"):
            m = EE.selection_mask(img.shape, {"shapes": [{"type": "part", "part": part, "op": "add"}]}, img)
            self.assertEqual(m.shape, img.shape[:2])
            self.assertLess(float(m.max()), 0.05, part)

    def test_adjust_mask_by_part_composes(self):
        doc = EE.new_doc()
        lay = EE.add_layer(doc, "adjust", adj="hair")
        lay["mask"] = {"shapes": [{"type": "part", "part": "beard", "op": "add"}], "feather": 0.0}
        img = _img()
        out, _ = EE.apply_pixel_layer(img, lay, "")
        self.assertTrue(np.allclose(out, img, atol=1e-5))  # sin cara no hay barba: nada cambia

    def test_beard_and_parts_listed(self):
        import masks as M
        self.assertIn("beard", M.FACE_EXTRA)
        self.assertEqual(M.new_mask("beard")["name"], "Barba")
        for k in ("lips", "beard", "hair", "iris", "teeth"):
            self.assertIn(k, EE.PARTS)


if __name__ == "__main__":
    unittest.main()
