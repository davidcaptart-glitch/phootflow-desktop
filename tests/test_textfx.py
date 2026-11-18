"""Efectos de texto de Graphics y fuentes propias (sin red)."""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import graphics_engine as G  # noqa: E402
import graphics_online as O  # noqa: E402
import graphics_textfx as TFX  # noqa: E402


def _text(**kw):
    d = G.new_design(800, 600)
    el = G.text_el(d, "Hola", "title")
    el.update(kw)
    return el


class TextFxTest(unittest.TestCase):
    def test_all_effects_paint(self):
        from PySide6.QtGui import QImage, QPainter
        for k in TFX.FX:
            el = _text(tfx={"type": k}, color="#7C3AED")
            img = QImage(900, 700, QImage.Format_ARGB32_Premultiplied)
            img.fill(0)
            p = QPainter(img)
            p.setTransform(G.element_transform(el))
            G.paint_element(p, el, G.ImageCache())
            p.end()
            self.assertGreater(sum(1 for x in range(0, 900, 7) for y in range(0, 700, 7)
                                   if img.pixelColor(x, y).alpha()), 10, k)

    def test_layers_and_vectors(self):
        el = _text(tfx={"type": "neon"})
        self.assertTrue(TFX.has_blur(el))
        v = G.outline_text_el(el)
        self.assertEqual(v["type"], "vector")
        self.assertEqual(len(v["paths"]), 1)   # el desenfoque no pasa a vector
        el = _text(tfx={"type": "echo"})
        self.assertFalse(TFX.has_blur(el))
        self.assertEqual(len(G.outline_text_el(el)["paths"]), 3)

    def test_margin(self):
        self.assertEqual(TFX.margin(_text()), 0.0)
        self.assertGreater(TFX.margin(_text(tfx={"type": "shadow", "offset": 100, "blur": 60})), 10)

    def test_style(self):
        el = _text(bold=True)
        name, st = TFX.STYLES[0]
        TFX.apply_style(el, {k: v for k, v in st.items() if k != "font"})
        self.assertEqual(el["tfx"]["type"], st["tfx"]["type"])
        self.assertNotIn("bold", el)
        el["tfx"]["amount"] = 1      # copia, no la misma lista del estilo
        self.assertNotEqual(st["tfx"].get("amount"), 1)

    def test_import_fonts_zip(self):
        src = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")
        if not os.path.exists(src):
            self.skipTest("sin arial.ttf")
        with tempfile.TemporaryDirectory() as td:
            store = O.Store(td)
            z = os.path.join(td, "pack.zip")
            with zipfile.ZipFile(z, "w") as f:
                f.write(src, "carpeta/Mi Letra.ttf")
                f.writestr("__MACOSX/._x.ttf", b"x")
                f.writestr("leeme.txt", "hola")
            out = O.import_fonts(store, [z])
            self.assertEqual([os.path.basename(x) for x in out], ["Mi Letra.ttf"])
            self.assertEqual(O.user_font_files(store), out)
            self.assertIn(out[0], O.installed_font_files(store))
            O.remove_user_font(store, out[0])
            self.assertEqual(O.user_font_files(store), [])

    def test_detect_license(self):
        self.assertEqual(O.detect_license(["This font is FREE FOR PERSONAL USE only."]), "personal")
        self.assertEqual(O.detect_license(["100% Free. Use it as you like."]), "libre")
        self.assertEqual(O.detect_license(["Free for personal and commercial use."]), "libre")
        self.assertEqual(O.detect_license([], ["Lovely Demo.otf"]), "personal")
        self.assertEqual(O.detect_license(["Thanks for downloading!"]), "?")

    def test_zip_license_and_downloads(self):
        src = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")
        if not os.path.exists(src):
            self.skipTest("sin arial.ttf")
        import time
        with tempfile.TemporaryDirectory() as td:
            store = O.Store(td)
            dl = os.path.join(td, "Descargas")
            os.makedirs(dl)
            z = os.path.join(dl, "bonita.zip")
            with zipfile.ZipFile(z, "w") as f:
                f.write(src, "Bonita.ttf")
                f.writestr("readme.txt", "Free for personal use. Commercial license: contact me.")
            with zipfile.ZipFile(os.path.join(dl, "fotos.zip"), "w") as f:
                f.writestr("a.jpg", b"x")
            orig = O.downloads_dir
            O.downloads_dir = lambda: dl
            try:
                found = O.new_font_downloads(store, time.time() - 60)
                self.assertEqual(found, [z])            # el zip sin fuentes no cuenta
                O.mark_imported(store, z)
                self.assertEqual(O.new_font_downloads(store, time.time() - 60), [])
            finally:
                O.downloads_dir = orig
            out = O.import_fonts(store, [z], "Descargas")
            self.assertEqual(O.font_meta(store)[os.path.basename(out[0])]["lic"], "personal")
            self.assertTrue(os.path.exists(z))          # el original se queda en Descargas


if __name__ == "__main__":
    unittest.main()
