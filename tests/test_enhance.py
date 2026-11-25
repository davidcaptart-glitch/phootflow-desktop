"""«Mejorar con IA»: aclarar, enfocar y limpiar (Real-ESRGAN), caras (GFPGAN) y sugerencias de Events.
Sin modelos ni fotos reales: sesiones ONNX falsas e imágenes sintéticas."""
import os
import sys
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine as E  # noqa: E402
import fx_engine as FX  # noqa: E402


def _img(h=160, w=240, seed=0, level=0.5):
    rng = np.random.default_rng(seed)
    a = np.zeros((h, w, 3), np.float32) + level
    a[h // 4:h // 2, w // 4:w // 2] = [0.8, 0.2, 0.2]
    return np.clip(a + rng.normal(0, 0.03, a.shape).astype(np.float32), 0, 1)


class FakeSR:
    """Amplía ×4 con vecino (como si la IA no cambiara nada)."""
    def get_inputs(self):
        return [mock.Mock(name="image")]

    def run(self, _o, feed):
        t = feed["image"][0].transpose(1, 2, 0)
        return [cv2.resize(t, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST).transpose(2, 0, 1)[None]]


class FakeFace:
    """Devuelve una cara gris uniforme (-1..1): se nota dónde se pega."""
    def get_inputs(self):
        m = mock.Mock()
        m.name = "input"
        return [m]

    def run(self, _o, feed):
        return [np.zeros_like(feed["input"])]


class Enhance(unittest.TestCase):
    def setUp(self):
        FX.configure(tempfile.mkdtemp())  # carpeta de modelos vacía

    def tearDown(self):
        FX._UP["session"] = None
        FX._FACE["session"] = None

    def test_without_models_changes_nothing(self):
        img = _img()
        self.assertFalse(FX.upscaler_ready() or FX.face_ready())
        out = FX.enhance(img, {"detail": True, "faces": True, "light": False}, faces_fn=lambda a: [])
        self.assertTrue(np.allclose(out, img))
        rgba = np.dstack([img, np.ones(img.shape[:2], np.float32)])
        self.assertEqual(FX.enhance(rgba, {}).shape, rgba.shape)

    def test_opts_and_seconds(self):
        o = FX.enhance_opts({"amount": 30, "otra": 1})
        self.assertEqual(o["amount"], 30)
        self.assertNotIn("otra", o)
        self.assertGreater(FX.enhance_seconds(4128, 6192, 2), FX.enhance_seconds(4128, 6192, 0))
        self.assertEqual(FX.enhance_seconds(4128, 6192, 3, {"detail": False, "faces": False}), 0)

    def test_brighten_dark_only(self):
        dark = _img(level=0.12)
        out = FX.brighten(dark)
        self.assertGreater(float(np.median(out)), float(np.median(dark)) + 0.15)
        self.assertLessEqual(float(out.max()), 1.0)
        bright = _img(level=0.6)
        self.assertTrue(np.array_equal(FX.brighten(bright), bright))

    def test_detail_blend(self):
        FX._UP["session"] = FakeSR()
        img = _img(130, 210)
        self.assertTrue(np.allclose(FX.enhance_detail(img, 0.0), img))
        out = FX.enhance_detail(img, 1.0)
        self.assertEqual(out.shape, img.shape)
        # con una IA «neutra», reducir ×0,5 → ×4 → ×1 es un suavizado: no inventa nada
        self.assertLess(float(np.abs(out - img).mean()), 0.05)
        with mock.patch.object(FX, "upscaler_ready", return_value=True):
            seen = []
            FX.enhance(img, {"detail": True, "faces": False}, progress=lambda k, n: seen.append(k))
            self.assertEqual(seen[-1], 100)

    def test_restore_faces_only_touches_the_face(self):
        FX._FACE["session"] = FakeFace()
        img = _img(400, 600, level=0.9)
        # cara de ~140 px centrada en (300, 200): puntos con la geometría de la plantilla FFHQ
        lm = (FX.FFHQ_5 - 256) * 0.3 + np.array([300, 200], np.float32)
        out = FX.restore_faces(img, [lm], amount=1.0)
        self.assertEqual(out.shape, img.shape)
        self.assertLess(float(out[200, 300].mean()), 0.75)            # el centro de la cara, sustituido
        self.assertTrue(np.allclose(out[:40, :80], img[:40, :80]))   # lejos de la cara, intacto
        self.assertTrue(np.allclose(FX.restore_faces(img, [lm], amount=0.0), img))
        tiny = (FX.FFHQ_5 - 256) * 0.02 + np.array([300, 200], np.float32)  # ojos a 2,5 px: no se toca
        self.assertTrue(np.allclose(FX.restore_faces(img, [tiny], amount=1.0), img))
        with mock.patch.object(FX, "face_ready", return_value=True):
            out2 = FX.enhance(img, {"detail": False, "faces": True, "amount": 100}, faces_fn=lambda a: [lm])
            self.assertLess(float(out2[200, 300].mean()), 0.75)


class Suggestions(unittest.TestCase):
    def _photos(self):
        ps = []
        for i in range(8):
            p = E.Photo(path=f"f{i}.jpg", name=f"f{i}")
            p.sharp, p.iso, p.ev, p.lum_med = 0.05, 400, 0.0, 0.18
            ps.append(p)
        ps[1].sharp = 0.01                     # blanda
        ps[2].iso = 12800                      # ruido
        ps[3].lum_med, ps[3].ev = 0.01, 1.0    # oscura aun después de la exposición
        for p in ps[4:]:
            p.face_sharp = 0.05
        ps[4].face_sharp = 0.01                # cara blanda
        ps[5].error = "no se pudo leer"
        return ps

    def test_suggest_fixes(self):
        ps = self._photos()
        n = E.suggest_fixes(ps)
        self.assertEqual(ps[0].fix, [])
        self.assertEqual(ps[1].fix, [E.FIX_DETAIL])
        self.assertEqual(ps[2].fix, [E.FIX_DETAIL])
        self.assertEqual(ps[3].fix, [E.FIX_LIGHT])
        self.assertIn(E.FIX_FACES, ps[4].fix)
        self.assertEqual(ps[5].fix, [])
        self.assertEqual(n, 4)
        self.assertIn("cara blanda", E.fix_text(ps[4]))
        o = E.suggested_enhance(ps[4])
        self.assertTrue(o["faces"] and not o["light"])
        self.assertTrue(E.suggested_enhance(ps[3])["light"])

    def test_review_keeps_choice(self):
        ps = self._photos()[:2]
        for p in ps:
            p.thumb = b"x"
        ps[0].enhance = {"detail": True, "faces": False, "light": True, "amount": 40}
        d = tempfile.mkdtemp()
        E.save_review(ps, d)
        fresh = [E.Photo(path=p.path, name=p.name) for p in ps]
        E.load_review(fresh, d)
        self.assertEqual(fresh[0].enhance, ps[0].enhance)
        self.assertEqual(fresh[1].enhance, {})

    def test_face_points_without_faces(self):
        E.prepare_detectors(1)
        self.assertEqual(E.face_points(_img()), [])


class StudioAndEdits(unittest.TestCase):
    """Studio (ajuste `ai`, solo en la vista definitiva y al exportar) y Edits (capa «enhance»)."""

    def test_studio_render_only_when_asked(self):
        import studio_engine as SE
        lin = (_img(90, 120) ** 2.2).astype(np.float32)
        calls = []

        def fake(img, opts):
            calls.append(opts)
            return np.full_like(img, 0.25)
        with mock.patch.object(E, "enhance_photo", side_effect=fake):
            SE.render(lin, SE.new_settings(ai=True), ai=False)  # vista rápida: sin IA
            self.assertEqual(calls, [])
            SE.render(lin, SE.new_settings(ai=False), ai=True)  # no activado en la foto
            self.assertEqual(calls, [])
            out = SE.render(lin, SE.new_settings(ai=True, ai_faces=False, ai_amount=30, sharpen=0), ai=True)
            self.assertEqual(calls, [{"detail": True, "faces": False, "light": False, "amount": 30}])
            self.assertLess(abs(float(out.mean()) - 0.25), 0.05)
            base = SE.new_settings(ai=True, ai_faces=False, ai_amount=30, sharpen=0)
            SE.render(lin, dict(base, vignette=-40, bw=True), ai=True)  # después de la IA: no se repite
            self.assertEqual(len(calls), 1)
            SE.render(lin, dict(base, exposure=0.5), ai=True)            # antes de la IA: se repite
            self.assertEqual(len(calls), 2)
        self.assertTrue(set(SE.AI_KEYS) <= SE.PRESET_KEYS_EXCLUDED)  # es de cada foto, no de los presets

    def test_edits_layer(self):
        import edits_engine as EE
        lay = EE.new_layer("enhance")
        self.assertTrue(EE._layer_active(lay))
        self.assertFalse(EE._layer_active(dict(lay, detail=False, faces=False, light=False)))
        img = _img()
        with mock.patch.object(E, "enhance_photo", side_effect=lambda a, o: np.full_like(a, 0.25)) as m:
            out, alpha = EE.apply_pixel_layer(img, dict(lay, opacity=50))
            self.assertIsNone(alpha)
            self.assertEqual(m.call_args[0][1]["amount"], 60.0)
        self.assertTrue(np.allclose(out, img * 0.5 + 0.125, atol=1e-5))


if __name__ == "__main__":
    unittest.main()
