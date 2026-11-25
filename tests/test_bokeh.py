"""Desenfoque de fondo y velocidad (bokeh.py) con imágenes sintéticas, sin IA (sujeto por máscara dada)."""
import unittest

import numpy as np

import bokeh as B


def _scene(h=300, w=450):
    rng = np.random.default_rng(1)
    img = (0.25 + 0.1 * rng.random((h, w, 3))).astype(np.float32)
    img[40:44, 60:64] = 1.0  # una luz puntual en el fondo
    a = np.zeros((h, w), np.float32)
    a[120:260, 190:260] = 1.0  # sujeto: rectángulo rojo
    img[a > 0] = (0.8, 0.1, 0.1)
    return img, a


class BokehTest(unittest.TestCase):
    def test_subject_sharp_background_blurred(self):
        img, a = _scene()
        out = B.bokeh(img, 80, subject="mask", alpha=a)
        self.assertEqual(out.shape, img.shape)
        self.assertLess(np.abs(out[150:230, 200:250] - img[150:230, 200:250]).max(), 0.03)
        self.assertLess(out[0:100, 300:440].std(), img[0:100, 300:440].std() * 0.5)

    def test_no_halo(self):
        img, a = _scene()
        out = B.bokeh(img, 100, subject="mask", alpha=a)
        ring = out[180:200, 270:285]  # fondo junto al sujeto: sin rojo arrastrado
        self.assertLess(ring[..., 0].mean() - ring[..., 1].mean(), 0.05)

    def test_near_plane_wall(self):
        """Fondo tan nítido como el sujeto y pegado a él (una pared) -> casi sin desenfoque; fondo que ya estaba
        desenfocado en la foto -> desenfoque normal."""
        import cv2
        h, w = 300, 450
        xx = np.arange(w)[None, :]
        wall = np.repeat(np.where((xx // 6) % 2 == 0, 0.75, 0.2), h, 0).astype(np.float32)
        # sujeto enfocado con el mismo tipo de detalle que la pared (rayas de otros tonos)
        subj = np.repeat(np.where(((xx + 3) // 6) % 2 == 0, 0.9, 0.35), h, 0).astype(np.float32)
        a = np.zeros((h, w), np.float32)
        a[60:260, 180:270] = 1.0
        for bg, near_expected in ((wall, True), (cv2.GaussianBlur(wall, (0, 0), 4), False)):
            g = np.where(a > 0, subj, bg)
            img = np.dstack([g] * 3).astype(np.float32)
            n = B.near_plane(img, a)
            beside = float(n[100:220, 280:300].mean())  # junto al sujeto
            if near_expected:
                self.assertGreater(beside, 0.4)
                out = B.bokeh(img, 80, subject="mask", alpha=a)
                far = B.bokeh(img, 80, subject="mask", alpha=a)[100:220, 420:440].std()
                self.assertGreater(out[100:220, 280:300].std(), far)  # la pared pegada conserva más los listones
            else:
                self.assertLess(beside, 0.15)
        self.assertEqual(float(B.near_plane(np.zeros((h, w, 3), np.float32), a).max()), 0.0)  # sin textura

    def test_disc_highlight(self):
        img = np.full((300, 300, 3), 0.05, np.float32)
        img[148:152, 148:152] = 1.0
        out = B.bokeh(img, 100, subject="mask", alpha=np.zeros((300, 300), np.float32))
        row = out[150, :, 1]
        top = row.max()
        flat = (row > top * 0.8).sum()  # disco: meseta ancha, no pico gaussiano
        self.assertGreater(flat, 6)

    def test_zero_amount(self):
        img, a = _scene()
        self.assertIs(B.bokeh(img, 0, subject="mask", alpha=a), img)

    def test_speed_direction_and_trail(self):
        img, a = _scene()
        a2 = np.zeros_like(a)
        a2[150:200, 60:220] = 1  # sujeto alargado a la izquierda → va hacia la derecha
        self.assertAlmostEqual(B.auto_angle(a2), 0.0, delta=5)
        out = B.speed(img, 60, 180, trail=80, subject="mask", alpha=a)
        self.assertLess(np.abs(out[150:230, 200:250] - img[150:230, 200:250]).max(), 0.03)
        # hacia la izquierda (180°): la estela roja queda a su derecha
        right = out[170:210, 265:300, 0].mean() - out[170:210, 265:300, 1].mean()
        left = out[170:210, 150:185, 0].mean() - out[170:210, 150:185, 1].mean()
        self.assertGreater(right, left)

    def test_fill_hole(self):
        img = np.full((64, 64, 3), 0.5, np.float32)
        hole = np.zeros((64, 64), np.float32)
        hole[20:40, 20:40] = 1
        img[20:40, 20:40] = 1.0
        f = B.fill_hole(img, hole)
        self.assertLess(np.abs(f[30, 30] - 0.5).max(), 0.02)

    def test_events_eligible(self):
        one = [["persona", 0.9, 0.3, 0.1, 0.6, 0.9]]
        crowd = [["persona", 0.9, x, 0.2, x + 0.2, 0.8] for x in (0.0, 0.2, 0.4, 0.6)]
        self.assertTrue(B.events_eligible(one, []))
        self.assertFalse(B.events_eligible(crowd, []))
        self.assertFalse(B.events_eligible([], []))


class IntegrationTest(unittest.TestCase):
    def test_events_rule(self):
        import engine as E
        ph = E.Photo(path="x.jpg", objects=[["persona", 0.9, 0.3, 0.1, 0.6, 0.9]])
        self.assertEqual(E.photo_bokeh(ph, E.Settings()), 0)  # por defecto, sin aplicar
        self.assertEqual(E.photo_bokeh(ph, E.Settings(bokeh="Medio")), B.LEVELS["Medio"])
        ph.bokeh = E.BOKEH_NONE
        self.assertEqual(E.photo_bokeh(ph, E.Settings(bokeh="Medio")), 0)
        land = E.Photo(path="y.jpg")
        self.assertEqual(E.photo_bokeh(land, E.Settings(bokeh="Fuerte")), 0)
        land.bokeh = "Suave"  # elegida a mano en la revisión: vale aunque no tenga sujeto claro
        self.assertEqual(E.photo_bokeh(land, E.Settings()), B.LEVELS["Suave"])

    def test_review_fields_and_presets(self):
        import engine as E
        import session_presets as SP
        self.assertIn("bokeh", E._REVIEW_FIELDS)
        self.assertIn("speed", E._REVIEW_FIELDS)
        self.assertIn("bokeh", SP.KEYS)

    def test_studio_mask_subject(self):
        import masks as M
        import studio_engine as SE
        img, a = _scene()
        m = M.new_mask("radial")
        m["params"].update(cx=225 / 449, cy=190 / 299, rx=0.09, ry=0.25, feather=5)
        s = SE.new_settings(masks=[m], blur_subject="mask", blur_mask=0, bokeh=80)
        out = SE.blur_stage(img, s)
        self.assertLess(out[0:80, 330:440].std(), img[0:80, 330:440].std() * 0.5)
        self.assertLess(np.abs(out[180:200, 215:235] - img[180:200, 215:235]).max(), 0.05)

    def test_edits_speed_layer(self):
        import edits_engine as EE
        lay = EE.new_layer("speed")
        self.assertTrue(EE._layer_active(lay))
        self.assertIn("speed", EE.PIXEL_ORDER)


if __name__ == "__main__":
    unittest.main()
