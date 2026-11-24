"""Ajustes de sesión guardados: perfiles predefinidos y tuyos, y los que recuerda cada sesión."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session_presets as SP  # noqa: E402


class Presets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_builtin_only_known_keys(self):
        for name, p in SP.BUILTIN.items():
            extra = set(p) - set(SP.KEYS) - {"_desc"}
            self.assertFalse(extra, f"{name}: {extra}")
            self.assertTrue(SP.describe(p))

    def test_save_override_and_delete(self):
        SP.save_preset(self.cfg, "Bodas: LUT cálido", {"quality": 92, "lut": "x.cube", "basura": 1})
        allp = SP.all_presets(self.cfg)
        self.assertEqual(list(allp)[:len(SP.BUILTIN)], list(SP.BUILTIN))
        self.assertEqual(allp["Bodas: LUT cálido"]["quality"], 92)
        self.assertNotIn("basura", allp["Bodas: LUT cálido"])
        # tu versión de un predefinido lo sustituye; al borrarla vuelve el original
        SP.save_preset(self.cfg, "Deporte", {"quality": 70})
        self.assertEqual(SP.all_presets(self.cfg)["Deporte"]["quality"], 70)
        self.assertTrue(SP.delete_preset(self.cfg, "Deporte"))
        self.assertEqual(SP.all_presets(self.cfg)["Deporte"], SP.BUILTIN["Deporte"])
        self.assertTrue(SP.delete_preset(self.cfg, "Bodas: LUT cálido"))
        self.assertNotIn("Bodas: LUT cálido", SP.all_presets(self.cfg))
        with self.assertRaises(ValueError):
            SP.save_preset(self.cfg, "  ", {})

    def test_session_file(self):
        wd = os.path.join(self.cfg, "Editadas", ".phootflow")
        self.assertIsNone(SP.load_for_session(wd))
        SP.save_for_session(wd, {"quality": 88, "bw": True}, "Boda")
        d = SP.load_for_session(wd)
        self.assertEqual((d["quality"], d["bw"], d["_preset"]), (88, True, "Boda"))


class WindowPresets(unittest.TestCase):
    """La ventana de Events aplica y recuerda los ajustes (sin tocar el registro ni %APPDATA%)."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import app
        cls.app = app
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ini = os.path.join(cls.tmp.name, "prefs.ini")
        cls._old = (app.QSettings, app.CFG_DIR)
        app.QSettings = lambda *_: QSettings(cls.ini, QSettings.IniFormat)
        app.CFG_DIR = os.path.join(cls.tmp.name, "cfg")
        cls.win = app.Main()
        cls.win.upd_timer.stop()

    @classmethod
    def tearDownClass(cls):
        cls.win.deleteLater()
        cls.app.QSettings, cls.app.CFG_DIR = cls._old
        cls.tmp.cleanup()

    def test_apply_save_and_reopen(self):
        w = self.win
        self.assertTrue(w.apply_preset("Entrega rápida (redes)"))
        snap = w.snapshot()
        self.assertEqual(snap["sizes"], ["1080"])
        self.assertEqual(sorted(snap["ratios"]), ["4:5", "9:16"])
        self.assertEqual((snap["quality"], snap["zip"], snap["review"]), (85, False, False))
        self.assertEqual(w.settings().quality, 85)

        # una sesión recuerda sus ajustes y los recupera al abrirla
        sess = os.path.join(self.tmp.name, "Sesion A")
        os.makedirs(sess)
        w.open_session(sess, preset="Boda")
        self.assertEqual(w.q.value(), 95)
        self.assertTrue(w.c_bw.isChecked())
        w.q.setValue(91)
        w.save_session_settings()
        other = os.path.join(self.tmp.name, "Sesion B")
        os.makedirs(other)
        w.open_session(other, preset="Deporte")
        self.assertFalse(w.c_bw.isChecked())
        w.open_session(sess)  # sin elegir: los de la sesión
        self.assertEqual((w.q.value(), w.c_bw.isChecked(), w.preset_name), (91, True, "Boda"))
        w.apply_preset("Deporte")
        w.open_session(sess, preset=SP.KEEP)  # «los últimos usados»: no cambia nada
        self.assertEqual(w.q.value(), 90)

        # guardar los actuales como un perfil propio
        SP.save_preset(self.app.CFG_DIR, "Mío", w.snapshot())
        w.reload_presets("Mío")
        self.assertEqual(w.preset_cb.currentText(), "Mío")
        self.assertTrue(w.b_preset_del.isEnabled())


if __name__ == "__main__":
    unittest.main()
