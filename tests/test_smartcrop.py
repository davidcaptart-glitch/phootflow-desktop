"""Recorte inteligente (smartcrop + engine): con cajas e imágenes sintéticas, sin fotos reales ni modelo."""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
from PIL import Image  # noqa: E402

import engine as E  # noqa: E402
import smartcrop as SC  # noqa: E402

A = 1.5  # foto horizontal 3:2


def person(x0, y0, x1, y1, s=0.9):
    return [SC.PERSON, s, x0, y0, x1, y1]


def ball(x0, y0, x1, y1, s=0.8):
    return [SC.BALL, s, x0, y0, x1, y1]


def inside(box, crop, tol=1e-3):
    return (box[0] >= crop[0] - tol and box[1] >= crop[1] - tol and
            box[2] <= crop[2] + tol and box[3] <= crop[3] + tol)


def area(c):
    return (c[2] - c[0]) * (c[3] - c[1])


def ratio_of(c, aspect=A):
    return (c[2] - c[0]) * aspect / (c[3] - c[1])


FLAT = np.full((32, 48), 0.1, np.float32)


class Plan(unittest.TestCase):
    def test_deporte_pelota_y_jugadores_que_la_disputan(self):
        # plano abierto: seis jugadores; la pelota, entre los dos de la derecha
        objs = [person(0.05 + 0.1 * i, 0.40, 0.11 + 0.1 * i, 0.80) for i in range(4)]
        a, b = person(0.66, 0.35, 0.74, 0.80), person(0.76, 0.33, 0.84, 0.82)
        pel = ball(0.735, 0.40, 0.765, 0.445)
        objs += [a, b, pel]
        crops, scores, best, why = SC.plan(A, [], objs, FLAT, "Deporte", E.RATIOS)
        c = crops[best]
        self.assertNotEqual(best, "Original")
        self.assertIn("pelota", why)
        self.assertIn("2 jugadores", why)
        for o in (a, b, pel):
            self.assertTrue(inside(o[2:], c), (best, c, o))
        self.assertLess(area(c), 0.5)                      # se acerca a la jugada
        self.assertFalse(inside(objs[0][2:], c))           # el jugador lejano se queda fuera
        for name, (rw, rh) in E.RATIOS.items():            # cada formato con su proporción
            self.assertAlmostEqual(ratio_of(crops[name]), rw / rh, places=2)
            self.assertTrue(all(0 <= v <= 1 for v in crops[name]))

    def test_deporte_elige_la_orientacion(self):
        # un jugador de pie con la pelota en alto: vertical
        objs = [person(0.45, 0.15, 0.55, 0.95), ball(0.47, 0.05, 0.53, 0.14)]
        crops, scores, best, why = SC.plan(A, [], objs, FLAT, "Deporte", E.RATIOS)
        self.assertLess(ratio_of(crops[best]), 1.0, best)
        # dos jugadores tirados por el suelo peleando la pelota: horizontal
        objs = [person(0.20, 0.60, 0.48, 0.80), person(0.50, 0.62, 0.78, 0.82), ball(0.47, 0.62, 0.51, 0.68)]
        crops, scores, best, why = SC.plan(A, [], objs, FLAT, "Deporte", E.RATIOS)
        self.assertGreater(ratio_of(crops[best]), 1.0, best)

    def test_pelota_sin_deporte_no_cuenta(self):
        # en una fiesta, los globos que el detector toma por pelotas no mandan
        objs = [person(0.30, 0.25, 0.45, 0.95), ball(0.80, 0.10, 0.86, 0.19, s=0.9)]
        _, _, _, why = SC.plan(A, [], objs, FLAT, "Cumpleaños / fiesta", E.RATIOS)
        self.assertNotIn("pelota", why)

    def test_boda_la_pareja(self):
        faces = [(0.40, 0.30, 0.44, 0.36), (0.47, 0.31, 0.51, 0.37), (0.85, 0.40, 0.87, 0.43)]
        crops, scores, best, why = SC.plan(A, faces, [], FLAT, "Boda", E.RATIOS)
        self.assertEqual(why, "la pareja")
        c = crops[best]
        self.assertTrue(inside(faces[0], c) and inside(faces[1], c))
        self.assertLess(c[1], 0.30 - 0.02)                 # aire sobre las cabezas
        self.assertGreaterEqual(area(c), SC.RULES["Boda"]["min_area"] - 0.01)

    def test_grupo_entero_sin_partir_a_nadie(self):
        faces = [(0.10 + 0.13 * i, 0.30, 0.15 + 0.13 * i, 0.37) for i in range(6)]
        objs = [person(f[0] - 0.03, f[1] - 0.02, f[2] + 0.03, 0.98) for f in faces]
        crops, scores, best, why = SC.plan(A, faces, objs, FLAT, "Cumpleaños / fiesta", E.RATIOS)
        self.assertEqual(why, "el grupo")
        for f in faces:
            self.assertTrue(inside(f, crops[best]), best)

    def test_persona_pegada_entra_entera(self):
        # persona principal y, pegada a ella, otra con la cara pequeña (no principal): no se parte por la mitad
        faces = [(0.40, 0.25, 0.46, 0.33), (0.56, 0.40, 0.58, 0.43)]
        objs = [person(0.36, 0.22, 0.50, 0.95), person(0.48, 0.38, 0.66, 0.95)]
        crops, scores, best, _ = SC.plan(A, faces, objs, FLAT, "Cumpleaños / fiesta", E.RATIOS)
        self.assertTrue(inside(objs[1][2:], crops[best]), (best, crops[best]))

    def test_sin_personas_manda_la_saliencia(self):
        sal = np.zeros((32, 48), np.float32)
        sal[8:24, 2:14] = 1.0  # algo que destaca a la izquierda; el resto, vacío
        crops, scores, best, why = SC.plan(A, [], [], sal, "Exterior / paisaje", E.RATIOS)
        self.assertLess(crops["1:1"][0], 0.05)           # el cuadrado se va hacia lo que destaca
        self.assertEqual(crops["Original"], (0.0, 0.0, 1.0, 1.0))
        _, _, best, why = SC.plan(A, [], [], np.ones((32, 48), np.float32), "", E.RATIOS)
        self.assertEqual((best, why), ("Original", ""))  # todo interesa por igual: no se recorta

    def test_sujeto_que_llena_la_foto_no_se_recorta(self):
        objs = [person(0.02, 0.02, 0.98, 0.99)]
        _, _, best, _ = SC.plan(A, [], objs, FLAT, "Cumpleaños / fiesta", E.RATIOS)
        self.assertEqual(best, "Original")


class Composition(unittest.TestCase):
    def rules(self, faces, objs, scene, crop=None, hz=-1.0, sal=FLAT):
        crops, scores, best, why = SC.plan(A, faces, objs, sal, scene, E.RATIOS, hz)
        c = crop or crops[best]
        return best, c, dict(SC.explain(A, faces, objs, sal, scene, c, hz))

    def test_pelota_en_un_punto_fuerte(self):
        # plano abierto: la jugada es pequeña y el recorte tiene sitio para colocarla
        objs = [person(0.62, 0.55, 0.66, 0.75), person(0.70, 0.54, 0.74, 0.76), ball(0.675, 0.56, 0.695, 0.59)]
        crops, *_ = SC.plan(A, [], objs, FLAT, "Deporte", E.RATIOS)
        c = crops["4:5"]
        u = (0.685 - c[0]) / (c[2] - c[0])
        v = (0.575 - c[1]) / (c[3] - c[1])
        best_d = min(abs(u - a) + abs(v - b) for a in (1 / 3, 0.382, 0.618, 2 / 3) for b in (1 / 3, 0.382, 0.618, 2 / 3))
        self.assertLess(best_d, 0.12, (u, v))           # la pelota cae en un punto fuerte o áureo
        r = dict(SC.explain(A, [], objs, FLAT, "Deporte", c))
        self.assertIn("pelota", r["Centro de interés"])
        self.assertTrue({"Regla de los tercios", "Proporción áurea"} & set(r), r)

    def test_espacio_de_mirada(self):
        # una persona pequeña que mira a la derecha: más aire a la derecha que a la izquierda
        face = (0.45, 0.30, 0.49, 0.36, 0.6)
        objs = [person(0.42, 0.27, 0.52, 0.90)]
        crops, *_ = SC.plan(A, [face], objs, FLAT, "Cumpleaños / fiesta", E.RATIOS)
        c = crops["4:5"]
        cx = (face[0] + face[2]) / 2
        self.assertGreater(c[2] - cx, cx - c[0])
        r = dict(SC.explain(A, [face], objs, FLAT, "Cumpleaños / fiesta", c))
        self.assertIn("derecha", r.get("Espacio de mirada", ""))
        # y al revés si mira a la izquierda
        face_l = face[:4] + (-0.6,)
        c = SC.plan(A, [face_l], objs, FLAT, "Cumpleaños / fiesta", E.RATIOS)[0]["4:5"]
        self.assertGreater(cx - c[0], c[2] - cx)

    def test_retrato_de_frente_simetrico(self):
        face = (0.46, 0.25, 0.54, 0.37, 0.0)
        _, c, r = self.rules([face], [person(0.40, 0.20, 0.60, 0.99)], "Retrato / estudio")
        self.assertIn("Simetría", r)
        self.assertAlmostEqual(((c[0] + c[2]) / 2), 0.5, delta=0.03)

    def test_horizonte_en_un_tercio(self):
        sal = np.zeros((32, 48), np.float32)
        sal[10:20, 5:15] = 1.0
        _, c, r = self.rules([], [], "Exterior / paisaje", crop=(0.0, 0.2, 1.0, 0.8), hz=0.40, sal=sal)
        self.assertIn("Horizonte en un tercio", r)       # 0,40 cae en el tercio superior de 0,2..0,8
        _, c, r = self.rules([], [], "Exterior / paisaje", crop=(0.0, 0.2, 1.0, 0.8), hz=0.50, sal=sal)
        self.assertNotIn("Horizonte en un tercio", r)    # por la mitad: no

    def test_detecta_el_horizonte_y_la_mirada(self):
        g = np.zeros((300, 450), np.float32)
        g[:200] = 0.8                                     # cielo claro, suelo oscuro: horizonte a 2/3
        g += np.random.default_rng(1).normal(0, 0.01, g.shape).astype(np.float32)
        self.assertAlmostEqual(SC.horizon(g), 200 / 300, delta=0.02)
        self.assertEqual(SC.horizon(np.full((300, 450), 0.5, np.float32)), -1.0)
        row = [0, 0, 10, 10, 10, 5, 20, 5, 19, 8]         # nariz hacia la derecha de los ojos
        self.assertGreater(SC.face_yaw(row), 0.25)
        self.assertLess(SC.face_yaw([0, 0, 10, 10, 10, 5, 20, 5, 11, 8]), -0.25)
        self.assertEqual(SC.face_yaw([1, 2]), 0.0)

    def test_sin_recortar_y_resumen(self):
        _, _, r = self.rules([], [person(0.02, 0.02, 0.98, 0.99)], "Cumpleaños / fiesta")
        self.assertIn("Sin recortar", r)
        txt = SC.summary([("Regla de los tercios", "x"), ("Simplicidad", "y")])
        self.assertEqual(txt, "Regla de los tercios: x · Simplicidad: y")


class Saliency(unittest.TestCase):
    def test_lo_que_destaca(self):
        rng = np.random.default_rng(3)
        img = np.full((400, 600, 3), 0.45, np.float32) + rng.normal(0, 0.005, (400, 600, 3)).astype(np.float32)
        img[120:220, 420:540] = rng.random((100, 120, 3)).astype(np.float32)  # textura y color a la derecha
        m = SC.saliency(np.clip(img, 0, 1))
        self.assertEqual(max(m.shape), SC.SAL_LONG)
        y, x = np.unravel_index(int(np.argmax(m)), m.shape)
        self.assertGreater(x / m.shape[1], 0.6)
        self.assertTrue(0.2 < y / m.shape[0] < 0.65)
        back = SC.decode_map(SC.encode_map(m))
        self.assertEqual(back.shape, m.shape)
        self.assertLess(float(np.abs(back - m).max()), 1 / 255 + 1e-6)
        self.assertIsNone(SC.decode_map("basura"))


class FakeSession:
    """Salida en bruto de YOLOX (sin decodificar) con una persona y una pelota en posiciones conocidas."""

    def __init__(self, size):
        self.size = size

    def run(self, _out, _feed):
        d = SC.Detector.__new__(SC.Detector)
        d._grids(self.size)
        y = np.full((len(d._grid), 85), -20.0, np.float32)
        y[:, 4:] = 0.0

        def put(cx, cy, w, h, cls, conf):
            k = next(i for i, (g, s) in enumerate(zip(d._grid, d._stride[:, 0]))
                     if s == 16 and g[0] == int(cx // 16) and g[1] == int(cy // 16))
            y[k, 0], y[k, 1] = cx / 16 - d._grid[k][0], cy / 16 - d._grid[k][1]
            y[k, 2], y[k, 3] = np.log(w / 16), np.log(h / 16)
            y[k, 4], y[k, 5 + cls] = 1.0, conf
        put(200, 150, 80, 200, 0, 0.9)   # persona
        put(300, 100, 30, 30, 32, 0.7)   # pelota
        return [y[None]]


class Decode(unittest.TestCase):
    def test_cajas_normalizadas(self):
        d = SC.Detector.__new__(SC.Detector)
        d._grids(416)
        d.sess, d.name = FakeSession(416), "images"
        img = np.zeros((416, 624, 3), np.float32)  # 3:2 → escala 416/624, relleno abajo
        out = d.detect(img)
        cls = {o[0]: o for o in out}
        self.assertEqual(set(cls), {SC.PERSON, SC.BALL})
        r = 416 / 624
        p = cls[SC.PERSON]
        self.assertAlmostEqual(p[2], (200 - 40) / r / 624, places=3)
        self.assertAlmostEqual(p[5], min(1.0, (150 + 100) / r / 416), places=3)
        self.assertAlmostEqual(cls[SC.BALL][1], 0.7, places=2)


class FakeDetector:
    def __init__(self, objs):
        self.objs = objs

    def detect(self, rgb01):
        return [list(o) for o in self.objs]


class Engine(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        rng = np.random.default_rng(5)
        a = np.full((600, 900, 3), 70, np.uint8)
        cv2.circle(a, (700, 200), 30, (230, 180, 40), -1)     # «pelota»
        cv2.rectangle(a, (620, 180), (690, 560), (40, 60, 200), -1)
        cv2.rectangle(a, (720, 170), (790, 570), (200, 40, 40), -1)
        cv2.rectangle(a, (60, 250), (120, 560), (60, 160, 60), -1)
        a = np.clip(a + rng.normal(0, 3, a.shape), 0, 255).astype(np.uint8)
        Image.fromarray(a).save(os.path.join(self.d, "jugada.jpg"), quality=92)
        self.saved = dict(E.DET)
        E.DET.update(ready=True, obj=FakeDetector([
            person(620 / 900, 170 / 600, 690 / 900, 560 / 600), person(720 / 900, 170 / 600, 790 / 900, 570 / 600),
            person(60 / 900, 250 / 600, 120 / 900, 560 / 600), ball(670 / 900, 170 / 600, 730 / 900, 230 / 600)]))

    def tearDown(self):
        E.DET.clear()
        E.DET.update(self.saved)

    def test_analisis_cache_y_sesion(self):
        st = E.Settings(straighten=False)
        ps = E.list_photos(self.d)
        E.analyze_many(ps, st, 1)
        ph = ps[0]
        self.assertFalse(ph.error)
        self.assertAlmostEqual(ph.aspect, 1.5, places=2)
        self.assertEqual(len(ph.objects), 4)
        self.assertTrue(ph.sal and set(E.RATIOS) <= set(ph.crops))
        # con el tipo de la sesión: deporte → la jugada de la derecha
        self.assertEqual(E.smart_crops(ps, "Deporte"), 1)
        self.assertIn("pelota", ph.crop_why)
        c = ph.crops[ph.best]
        self.assertGreater(c[0], 0.3)
        self.assertNotEqual(ph.best, "Original")
        # la caché guarda lo necesario para rehacerlo sin abrir la foto
        wd = os.path.join(self.d, ".phootflow")
        E.save_session(ps, st, wd)
        fresh = E.list_photos(self.d)
        self.assertEqual(E.load_session(fresh, st, wd), 1)
        f = fresh[0]
        self.assertEqual((f.objects, f.sal, f.aspect), (ph.objects, ph.sal, ph.aspect))
        self.assertEqual(f.crops[f.best], c)
        # lo hecho a mano se respeta al rehacer
        f.crops[E.CUSTOM_CROP] = (0.1, 0.1, 0.6, 0.9)
        f.best = E.CUSTOM_CROP
        E.smart_crops(fresh, "Boda")
        self.assertEqual((f.best, f.crops[E.CUSTOM_CROP]), (E.CUSTOM_CROP, (0.1, 0.1, 0.6, 0.9)))
        self.assertEqual(E.suggested_crop(f), max(f.scores, key=f.scores.get))
        # un análisis antiguo (sin datos de recorte inteligente) no se toca
        old = E.Photo(path="x.jpg", crops={"Original": (0, 0, 1, 1)})
        self.assertFalse(E.replan_crop(old, "Deporte"))

    def test_exporta_el_recorte_inteligente(self):
        st = E.Settings(straighten=False, ratios=["4:5"], sizes=["max"], export_original_ratio=False,
                        archive_originals=False, camera_look=False)
        ps = E.list_photos(self.d)
        E.analyze_many(ps, st, 1)
        E.smart_crops(ps, "Deporte")
        ph = ps[0]
        out = os.path.join(self.d, "Editadas")
        E.export_many(ps, st, out)
        self.assertFalse(ph.error)
        with Image.open(E.delivery_path(out, ph.name, "max", False, st.sizes)) as im:
            x0, y0, x1, y1 = ph.crops[ph.best]
            self.assertAlmostEqual(im.width / im.height, (x1 - x0) * 1.5 / (y1 - y0), places=1)
        if ph.best != "4:5":
            with Image.open(os.path.join(out, E.FORMATS_DIR, "4x5", f"{ph.name}_4x5_max.jpg")) as im:
                self.assertAlmostEqual(im.width / im.height, 0.8, places=2)


if __name__ == "__main__":
    unittest.main()
