"""Retoques casi automáticos del informe de Whitewall: ojeras, imperfecciones y sugerencias en Edits, y
«Retocar retratos» al exportar en Events. Solo imágenes y caras sintéticas (la malla facial se fabrica a mano)."""
import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import edits_engine as EE  # noqa: E402
import propagate as P  # noqa: E402


def fake_face(cx=200.0, cy=200.0, fw=160.0) -> np.ndarray:
    """Malla de 478 puntos con lo que usan `face_axes` y `undereye_alpha`: ojos, boca y ancho de la cara."""
    pts = np.tile(np.array([[cx, cy]], np.float32), (478, 1))
    ey, eo = cy - 0.12 * fw, 0.2 * fw  # altura de los ojos y separación desde el centro
    for side, lid in zip((-1, 1), EE.LOWER_LIDS):
        xs = np.linspace(cx + side * (eo + 0.12 * fw), cx + side * (eo - 0.12 * fw), len(lid))
        ys = ey + 0.03 * fw * np.sin(np.linspace(0, np.pi, len(lid)))
        pts[lid] = np.stack([xs, ys], 1)
    pts[61], pts[291] = [cx - 0.15 * fw, cy + 0.25 * fw], [cx + 0.15 * fw, cy + 0.25 * fw]
    pts[234], pts[454] = [cx - fw / 2, cy], [cx + fw / 2, cy]
    return pts


class UnderEye(unittest.TestCase):
    def test_zone_is_below_each_eye(self):
        pts = fake_face()
        a = EE.undereye_alpha((400, 400), pts)
        self.assertGreater(float(a.max()), 0.5)
        ys, xs = np.nonzero(a > 0.5)
        self.assertGreater(ys.min(), pts[EE.LOWER_LIDS[0]][:, 1].min())  # por debajo del párpado
        self.assertLess(ys.max(), pts[61][1])  # por encima de la boca
        self.assertTrue((xs < 200).any() and (xs > 200).any())  # los dos ojos
        self.assertLess(float(a[200, 200]), 0.05)  # el tabique no

    def test_lightens_dark_circles_towards_cheek(self):
        pts = fake_face()
        img = np.full((400, 400, 3), [0.75, 0.58, 0.48], np.float32)  # piel
        dark = EE.undereye_alpha((400, 400), pts)
        img = img * (1 - 0.35 * dark[..., None]) + np.array([0.35, 0.3, 0.38], np.float32) * 0.35 * dark[..., None]
        lab = cv2.cvtColor(img, cv2.COLOR_RGB2Lab)
        before = lab[..., 0].copy()
        eyes = np.zeros((400, 400), np.float32)
        EE._undereye(lab, (400, 400), [pts], 0, 0, eyes, 1.0)
        m = dark > 0.6
        cheek_L = float(before[300, 150])
        self.assertGreater(float(lab[..., 0][m].mean()), float(before[m].mean()) + 2)
        self.assertLessEqual(float(lab[..., 0][m].max()), cheek_L + 0.5)  # no pasa de la luz del pómulo
        self.assertAlmostEqual(float(lab[300, 150, 0]), cheek_L, places=3)  # fuera de la zona no cambia

    def test_old_layers_keep_their_look(self):
        self.assertIn("undereye", EE.PORTRAIT_LABELS)
        self.assertEqual(EE.new_layer("portrait")["undereye"], EE.PORTRAIT_DEFAULTS["undereye"])
        self.assertEqual(EE.PORTRAIT_OLD["undereye"], 0)  # una capa guardada sin la clave no cambia


def skin_with_blemishes():
    """Piel con poros, tres granos, una rojez, un lunar grande y un pelo; la cara de `fake_face(250, 300, 300)`."""
    rng = np.random.default_rng(5)
    h = w = 500
    img = np.full((h, w, 3), [0.78, 0.6, 0.5], np.float32)
    img += cv2.GaussianBlur(rng.normal(0, 0.02, (h, w, 1)).astype(np.float32), (0, 0), 0.8)[..., None]
    spots = [(150, 300), (330, 320), (250, 380)]
    for x, y in spots:
        cv2.circle(img, (x, y), 3, (0.55, 0.38, 0.33), -1, cv2.LINE_AA)
    cv2.circle(img, (200, 360), 3, (0.85, 0.42, 0.42), -1, cv2.LINE_AA)  # rojez
    cv2.circle(img, (380, 385), 14, (0.4, 0.28, 0.22), -1, cv2.LINE_AA)  # lunar: no se toca
    cv2.line(img, (100, 250), (160, 260), (0.45, 0.32, 0.28), 1, cv2.LINE_AA)  # pelo: tampoco
    skin = np.zeros((h, w), np.float32)
    skin[150:470, 90:420] = 1
    return np.clip(img, 0, 1), skin, spots + [(200, 360)]


class Blemishes(unittest.TestCase):
    def test_finds_spots_not_moles_or_hairs(self):
        img, skin, spots = skin_with_blemishes()
        found = EE.find_blemishes(img, [fake_face(250, 300, 300)], skin)
        got = sorted((round(x), round(y)) for x, y, _ in found)
        self.assertEqual(got, sorted(spots))

    def test_ops_heal_them(self):
        img, skin, spots = skin_with_blemishes()
        found = EE.find_blemishes(img, [fake_face(250, 300, 300)], skin)
        ops = []
        for x, y, r in found:
            op = {"tool": "heal", "r": r / 500, "pts": [[x / 499, y / 499]], "src": None, "hard": 0.35}
            op["src"] = EE.find_source(img, op)
            ops.append(op)
        out = EE.apply_retouch(img, {"ops": ops})
        for x, y in spots:
            self.assertLess(float(np.abs(out[y, x] - [0.78, 0.6, 0.5]).max()), 0.06, (x, y))
        self.assertLess(float(out[385, 380, 0]), 0.45)  # el lunar sigue

    def test_no_faces_no_ops(self):
        img, _, _ = skin_with_blemishes()
        self.assertEqual(EE.blemish_ops(img, faces=[]), [])

    def test_hints(self):
        pts = fake_face()
        img = np.full((400, 400, 3), [0.75, 0.58, 0.48], np.float32)
        h0 = EE.retouch_hints(img, faces=[pts])
        self.assertEqual(h0["faces"], 1)
        self.assertNotIn("ojeras marcadas", h0["texts"])
        dark = EE.undereye_alpha((400, 400), pts)[..., None]
        h1 = EE.retouch_hints(np.clip(img * (1 - 0.4 * dark), 0, 1).astype(np.float32), faces=[pts])
        self.assertIn("ojeras marcadas", h1["texts"])
        self.assertEqual(EE.retouch_hints(img, faces=[])["texts"], [])


def _ph(**kw):
    import engine as E
    return E.Photo(path=kw.pop("path", "a.jpg"), name="a", **kw)


class EventsRetouch(unittest.TestCase):
    def test_rule(self):
        import engine as E
        on, off = E.Settings(portrait=True), E.Settings(portrait=False)
        big = _ph(faces=1, face_boxes=[[0.4, 0.2, 0.55, 0.45]])
        small = _ph(faces=3, face_boxes=[[0.1, 0.1, 0.12, 0.13]])
        old = _ph(faces=2)  # análisis sin cajas de caras
        none = _ph(faces=0)
        self.assertTrue(E.photo_retouch(big, on))
        self.assertFalse(E.photo_retouch(big, off))
        self.assertFalse(E.photo_retouch(small, on))
        self.assertTrue(E.photo_retouch(old, on))
        self.assertFalse(E.photo_retouch(none, on))
        big.retouch = E.RETOUCH_NONE  # quitado en la revisión
        self.assertFalse(E.photo_retouch(big, on))
        small.retouch = E.RETOUCH_ON  # puesto en la revisión, aunque la sesión no lo tenga
        self.assertTrue(E.photo_retouch(small, off))
        self.assertIn("retouch", E._REVIEW_FIELDS)

    def test_portrait_fx_hooks_and_fails_soft(self):
        import engine as E
        img = np.full((64, 64, 3), 0.5, np.float32)
        ph = _ph(faces=1)
        self.assertIs(E.portrait_fx(img, ph, E.Settings(portrait=False)), img)  # sin retoque, ni se toca
        calls = []
        orig = EE.auto_portrait
        try:
            EE.auto_portrait = lambda x, key="": calls.append(key) or x * 0.5
            out = E.portrait_fx(img, ph, E.Settings(portrait=True))
            self.assertAlmostEqual(float(out.mean()), 0.25, places=5)
            EE.auto_portrait = lambda x, key="": 1 / 0
            self.assertIs(E.portrait_fx(img, ph, E.Settings(portrait=True)), img)  # error: la foto sin retoque
        finally:
            EE.auto_portrait = orig
        self.assertEqual(calls, ["a.jpg#events"])

    def test_auto_portrait_without_faces_is_gentle(self):
        rng = np.random.default_rng(2)
        img = np.clip(0.5 + rng.normal(0, 0.05, (120, 160, 3)), 0, 1).astype(np.float32)
        out = EE.auto_portrait(img)
        self.assertEqual(out.shape, img.shape)
        self.assertTrue(np.isfinite(out).all() and 0 <= out.min() and out.max() <= 1)

    def test_presets_know_the_switch(self):
        import session_presets as SP
        self.assertIn("portrait", SP.KEYS)
        self.assertFalse(SP.BUILTIN["Deporte"]["portrait"])
        self.assertTrue(SP.BUILTIN["Boda"]["portrait"])


if __name__ == "__main__":
    unittest.main()
