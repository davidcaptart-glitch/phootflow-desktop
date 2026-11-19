"""Marco de recorte con enderezado: nivel, giro arrastrando fuera del marco y vista previa."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402

import cropview as CV  # noqa: E402


class Level(unittest.TestCase):
    def test_horizonte_y_verticales(self):
        self.assertAlmostEqual(CV.level_angle(0, 0, 100, 0), 0.0)
        # bajando hacia la derecha (horario en pantalla): se corrige en antihorario (positivo en OpenCV)
        self.assertGreater(CV.level_angle(0, 0, 100, 10), 0)
        self.assertLess(CV.level_angle(0, 0, 100, -10), 0)
        # una casi vertical se pone vertical, no horizontal
        self.assertAlmostEqual(abs(CV.level_angle(0, 0, 10, 100)), abs(CV.level_angle(0, 0, 100, 10)), places=6)
        self.assertLessEqual(abs(CV.level_angle(0, 0, 3, -100)), 45)

    def test_cubrir_al_girar(self):
        self.assertAlmostEqual(CV.cover_scale(300, 200, 0), 1.0)
        self.assertGreater(CV.cover_scale(300, 200, 5), 1.0)
        self.assertGreater(CV.cover_scale(300, 200, 10), CV.cover_scale(300, 200, 5))


class RotateInFrame(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication, QWidget
        QApplication.instance() or QApplication([])  # la misma que el resto de pruebas (con fuentes reales)
        self.w = QWidget()
        self.got = []
        self.c = CV.CropTool(self.w)
        self.c.on_angle = lambda a, ph: self.got.append((ph, a))
        self.c.start([0.2, 0.2, 0.8, 0.8], angle=1.0)
        self.r = QRectF(0, 0, 600, 400)

    def test_fuera_del_marco_gira(self):
        self.assertEqual(self.c.hit(QPointF(590, 200), self.r), "rotate")
        self.assertEqual(self.c.hit(QPointF(300, 200), self.r), "move")
        self.c.press(QPointF(590, 200), self.r)
        self.c.move(QPointF(590, 260), self.r, Qt.LeftButton)  # horario
        self.assertLess(self.c.target, 1.0)
        self.assertLess(self.c.preview, 0)
        self.c.release()
        self.assertEqual(self.got[0][0], "start")
        self.assertEqual(self.got[-1][0], "end")
        self.c.rendered(self.got[-1][1])
        self.assertAlmostEqual(self.c.preview, 0.0, places=6)

    def test_nivel_con_ctrl(self):
        self.c.press(QPointF(150, 150), self.r, Qt.ControlModifier)
        self.c.move(QPointF(450, 180), self.r, Qt.LeftButton)
        self.c.release()
        ph, a = self.got[-1]
        self.assertEqual(ph, "end")
        self.assertAlmostEqual(a, round(1.0 + CV.level_angle(150, 150, 450, 180), 2), places=2)

    def test_clic_suelto_no_endereza(self):
        self.c.level_mode = True
        self.c.press(QPointF(150, 150), self.r)
        self.c.release()
        self.assertEqual(self.got, [])
        self.assertFalse(self.c.level_mode)

    def test_sin_anfitrion_no_gira(self):
        c = CV.CropTool(self.w)
        c.start([0.2, 0.2, 0.8, 0.8])
        self.assertEqual(c.hit(QPointF(590, 200), self.r), "")


if __name__ == "__main__":
    unittest.main()
