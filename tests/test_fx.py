"""Efectos «de plugin» (fx_engine): ampliar, partículas, filtros artísticos, texturas, efectos, bordes, punto de
control de Studio, retícula de Graphics y capas nuevas de Edits. Imágenes sintéticas, sin red."""
import os
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fx_engine as FX  # noqa: E402


def _img(h=120, w=180):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.dstack([0.3 + 0.5 * xx / w, 0.4 + 0.3 * yy / h, 0.8 - 0.4 * yy / h]).astype(np.float32)
    cv2.circle(a, (w // 2, h // 2), h // 4, (0.9, 0.75, 0.6), -1)
    return a


class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        FX.configure(self.tmp)  # sin modelo: ampliación clásica

    def test_upscale_classic_and_alpha(self):
        img = _img()
        big = FX.upscale(img, 2, ai=True)  # sin modelo descargado cae en la clásica
        self.assertEqual(big.shape, (240, 360, 3))
        small = cv2.resize(big, (180, 120), interpolation=cv2.INTER_AREA)
        self.assertLess(float(np.abs(small - img).mean()), 0.03)
        rgba = np.dstack([img, np.ones(img.shape[:2], np.float32)])
        self.assertEqual(FX.upscale(rgba, 3).shape, (360, 540, 4))
        self.assertFalse(FX.upscaler_ready())
        self.assertGreater(FX.upscale_tiles(1000, 1000), 64)

    def test_esrgan_tiling_with_fake_session(self):
        """El troceado con solape recompone la imagen sin costuras (sesión falsa que amplía ×4 con vecino)."""
        class Fake:
            def run(self, _o, feed):
                t = feed["image"][0].transpose(1, 2, 0)
                return [cv2.resize(t, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST).transpose(2, 0, 1)[None]]
        FX._UP["session"] = Fake()
        try:
            img = _img(150, 260)
            out = FX._esrgan_x4(img)
            self.assertEqual(out.shape, (600, 1040, 3))
            ref = cv2.resize(img, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
            self.assertLess(float(np.abs(out - ref).max()), 1e-5)
        finally:
            FX._UP["session"] = None

    def test_particles_all_kinds(self):
        for k in FX.PARTICLES:
            p = FX.particle_defaults(k)
            a = FX.particles_rgba(160, 240, p)
            self.assertEqual(a.shape, (160, 240, 4), k)
            self.assertGreater(float(a[..., 3].max()), 0.2, k)
            self.assertTrue(np.isfinite(a).all(), k)
            again = FX.particles_rgba(160, 240, p)
            self.assertTrue(np.allclose(a, again), f"{k}: la misma semilla da lo mismo")
        p = FX.particle_defaults("snow")
        p["density"] = 0
        self.assertLess(float(FX.particles_rgba(80, 80, p)[..., 3].max()), 0.6)

    def test_artistic_filters(self):
        img = _img()
        for k in FX.ARTISTIC:
            out = FX.artistic(img, k)
            self.assertEqual(out.shape, img.shape, k)
            self.assertGreater(float(np.abs(out - img).mean()), 0.01, k)
            self.assertTrue(np.isfinite(out).all(), k)
        self.assertTrue(np.allclose(FX.artistic(img, "oil", amount=0), img, atol=1e-5))
        rgba = np.dstack([img, np.full(img.shape[:2], 0.5, np.float32)])
        self.assertTrue(np.allclose(FX.artistic(rgba, "pop")[..., 3], 0.5))

    def test_textures_effects_edges(self):
        for k in FX.TEXTURES:
            t = FX.texture(k, 90, 60, seed=3)
            self.assertEqual(t.shape, (60, 90, 3), k)
            self.assertGreater(float(t.std()), 0.005, k)
        a = np.zeros((80, 160), np.float32)
        cv2.rectangle(a, (40, 25), (120, 55), 1.0, -1)
        for k in FX.EFFECTS:
            e = FX.effect(k, a)
            self.assertEqual(e.shape, (80, 160, 4), k)
            y = 25 if k == "neon" else 40  # el neón es solo el contorno
            self.assertGreater(float(e[y, 80, 3]), 0.3, k)
        for k in FX.EDGES:
            e = FX.edge_alpha(100, 150, k, 60)
            self.assertGreater(float(e[50, 75]), 0.9, k)  # el centro queda
            self.assertLess(float(e[0, 0]), 0.5, k)  # la esquina se va

    def test_double_exposure(self):
        img = _img()
        sub = np.zeros(img.shape[:2], np.float32)
        cv2.circle(sub, (90, 60), 30, 1.0, -1)
        out = FX.double_exposure(img, FX.texture("clouds", 180, 120), sub)
        self.assertGreater(float(out[2, 2].mean()), 0.8)  # fondo aclarado
        self.assertEqual(out.shape, img.shape)


class Integration(unittest.TestCase):
    def test_studio_point_mask(self):
        import masks as M
        img = np.zeros((200, 300, 3), np.float32)
        img[:100] = [0.3, 0.5, 0.9]
        img[100:] = [0.2, 0.7, 0.2]
        m = M.new_mask("point")
        m["params"].update(cx=0.2, cy=0.2, size=60, sim=60)
        a = M.build(m, img)
        self.assertGreater(float(a[40, 60]), 0.9)  # cielo cerca del punto
        self.assertLess(float(a[160, 60]), 0.05)  # el césped no

    def test_graphics_grid_and_info(self):
        import graphics_engine as G
        d = G.new_design(1000, 800)
        d["grid"] = G.new_grid(cols=2, rows=0, gutter=20, margin=[50, 50, 50, 50], vx=[123])
        xs, ys = G.grid_lines(d)
        self.assertEqual(xs, [50, 123, 490, 510, 950])
        self.assertEqual(ys, [50, 750])
        sx, _ = G.snap_lines(d, [])
        self.assertIn(490, sx)
        el = G.image_el(d, "x.jpg", art={"kind": "watercolor"}, edge={"kind": "torn"})
        info = G.layer_info(el, d)
        self.assertIn("Acuarela", info)
        self.assertIn("Papel rasgado", info)
        el["credit"] = "Ana López · CC BY"  # el crédito se guarda como texto
        self.assertIn("Crédito: Ana López · CC BY", G.layer_info(el, d))
        el["credit"] = {"author": "Ana"}
        self.assertIn("Crédito: Ana", G.layer_info(el, d))

    def test_edits_new_layers(self):
        import edits_engine as EE
        img = _img()
        doc = EE.new_doc()
        EE.add_layer(doc, "artistic", art="comic")
        lay = EE.add_layer(doc, "particles")
        lay["p"]["kind"] = "confetti"
        out, _ = EE.compose(img, doc, [0.0, 0.0, 1.0, 1.0])
        self.assertEqual(out.shape, img.shape)
        self.assertGreater(float(np.abs(out - img).mean()), 0.01)
        self.assertTrue(EE.has_edits(doc))


if __name__ == "__main__":
    unittest.main()
