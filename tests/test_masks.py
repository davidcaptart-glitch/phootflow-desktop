"""Máscaras de Studio: zonas que suman, restan o intersecan, inversión y color dentro de la máscara."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import masks as M  # noqa: E402


def _img():
    rng = np.random.default_rng(1)
    return rng.random((80, 120, 3)).astype(np.float32)


class MaskCombine(unittest.TestCase):
    def setUp(self):
        self.b = _img()
        self.m = M.new_mask("radial")

    def test_sin_zonas_igual_que_antes(self):
        a = M.build(self.m, self.b)
        self.assertTrue(np.allclose(a, M.build_one(self.m, self.b)))

    def test_sumar_restar_intersecar(self):
        base = M.build(self.m, self.b)
        lin = M.build_one(M.new_part("linear"), self.b)
        self.m["parts"] = [M.new_part("linear", "add")]
        self.assertTrue(np.allclose(M.build(self.m, self.b), np.maximum(base, lin), atol=1e-6))
        self.m["parts"][0]["mode"] = "sub"
        self.assertTrue(np.allclose(M.build(self.m, self.b), base * (1 - lin), atol=1e-6))
        self.m["parts"][0]["mode"] = "inter"
        self.assertTrue(np.allclose(M.build(self.m, self.b), base * lin, atol=1e-6))

    def test_zona_desactivada_no_cuenta(self):
        base = M.build(self.m, self.b)
        self.m["parts"] = [dict(M.new_part("linear", "sub"), enabled=False)]
        self.assertTrue(np.allclose(M.build(self.m, self.b), base))

    def test_pincel_resta_varios_tipos(self):
        part = M.new_part("brush", "sub")
        part["params"]["strokes"] = [[0.5, 0.45, 0.1, 1]]
        self.m["parts"] = [part, M.new_part("linear", "add")]
        a = M.build(self.m, self.b)
        self.assertEqual(a.shape, self.b.shape[:2])

    def test_invertir_zona_y_mascara(self):
        base = M.build(self.m, self.b)
        self.m["invert_all"] = True
        self.assertTrue(np.allclose(M.build(self.m, self.b), 1 - base, atol=1e-6))
        self.m["invert_all"] = False
        self.m["invert"] = True
        self.assertTrue(np.allclose(M.build(self.m, self.b), 1 - base, atol=1e-6))

    def test_cantidad_sobre_el_total(self):
        self.m["parts"] = [M.new_part("linear", "add")]
        full = M.build(self.m, self.b)
        self.m["amount"] = 50
        self.assertTrue(np.allclose(M.build(self.m, self.b), full * 0.5, atol=1e-6))


class MaskColor(unittest.TestCase):
    def test_reposo_no_hace_nada(self):
        adj = {k: v for k, _, _, _, _, v in M.ADJ}
        adj.update(grade_blend=50, grade_sh_hue=200, hsl_red_s=0)
        self.assertFalse(M.adj_active(adj))

    def test_hsl_y_gradacion_cambian_solo_la_zona(self):
        b = _img()
        m = M.new_mask("radial")
        m["params"].update(cx=0.25, cy=0.5, rx=0.15, ry=0.2, feather=10)
        m["adj"].update(hsl_red_s=-100, hsl_orange_s=-100, grade_sh_sat=50, grade_sh_hue=220)
        self.assertTrue(M.adj_active(m["adj"]))
        y = M.apply_all(b, [m])
        self.assertGreater(float(np.abs(y[30:50, 20:40] - b[30:50, 20:40]).mean()), 0.005)
        self.assertLess(float(np.abs(y[:, 100:] - b[:, 100:]).mean()), 1e-4)  # fuera de la zona, igual


class MaskPresenceCurve(unittest.TestCase):
    def test_presencia_y_curva_cambian_la_zona(self):
        b = (0.45 + 0.4 * np.random.default_rng(2).random((80, 120, 3))).astype(np.float32)
        for k, v in (("texture", 60), ("dehaze", 70), ("dehaze", -60), ("vibrance", 50),
                     ("curve_lights", 60), ("curve_highlights", -50)):
            m = M.new_mask("radial")
            m["params"].update(cx=0.25, cy=0.5, rx=0.15, ry=0.2, feather=10)
            m["adj"][k] = v
            self.assertTrue(M.adj_active(m["adj"]), k)
            y = M.apply_all(b, [m])
            self.assertGreater(float(np.abs(y[30:50, 20:40] - b[30:50, 20:40]).mean()), 1e-3, k)
            self.assertLess(float(np.abs(y[:, 100:] - b[:, 100:]).mean()), 1e-4, k)
            self.assertTrue(0 <= y.min() and y.max() <= 1, k)


if __name__ == "__main__":
    unittest.main()
