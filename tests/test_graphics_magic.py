"""Graphics: estilos y diseños con las fotos de la sesión, páginas seguidas y texto enriquecido (sin fotos reales)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import graphics_engine as G  # noqa: E402
import graphics_magic as M  # noqa: E402
import graphics_pages as GP  # noqa: E402


def _session(n=8):
    """Carpeta de sesión sintética con su análisis de Events en Editadas/.phootflow."""
    root = tempfile.mkdtemp()
    wd = os.path.join(root, "Editadas", ".phootflow")
    os.makedirs(wd)
    rows = {}
    for i in range(n):
        w, h = (300, 200) if i % 2 else (200, 300)
        name = f"IMG_{i:03d}.jpg"
        Image.fromarray(np.full((h, w, 3), 40 + i * 20, np.uint8)).save(os.path.join(root, name))
        rows[name] = {"sig": "x", "data": {"keep": i != 3, "sharp": float(i), "faces": 1, "eyes_open": 1.0,
                                           "dhash": (i + 1) * 0x0F0F0F0F, "crops": {}, "best": "Original"}}
    with open(os.path.join(wd, "analisis.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f)
    return root


class StylesTest(unittest.TestCase):
    def test_restyle_keeps_roles(self):
        d = G.design_from_preset("Instagram vertical 4:5")
        G.apply_template(d, "Post de evento")
        st = next(s for s in M.STYLES if s[0] == "Azul marino")
        M.restyle(d, st)
        self.assertEqual(d["style"], "Azul marino")
        L = lambda c: M._lum(M._rgb(c)[0])  # noqa: E731
        self.assertLess(L(d["bg"]), 0.2)   # el fondo oscuro sigue oscuro
        texts = [e for e in d["elements"] if e["type"] == "text"]
        self.assertTrue(all(L(e["color"]) > 0.7 for e in texts))   # y el texto claro, claro
        self.assertIn(st[5], {e["font"] for e in texts})

    def test_mirror_and_variants(self):
        d = G.design_from_preset("Instagram vertical 4:5")
        G.apply_template(d, "Post de evento")
        x0 = [(e["x"], e["w"]) for e in d["elements"]]
        M.mirror(d)
        for (x, w), e in zip(x0, d["elements"]):
            self.assertAlmostEqual(e["x"], d["w"] - x - w, 3)
        vs = M.variants(d, 6, seed=3)
        self.assertEqual(len(vs), 6)
        self.assertEqual(len({n for n, _ in vs}), 6)


class SessionTest(unittest.TestCase):
    def test_ranking_and_discards_last(self):
        ph = M.session_photos(_session())
        self.assertEqual(len(ph), 8)
        self.assertEqual(ph[-1]["name"], "IMG_003.jpg")   # descartada: la última
        self.assertEqual(ph[0]["name"], "IMG_007.jpg")    # la más nítida, la primera

    def test_without_analysis(self):
        root = tempfile.mkdtemp()
        Image.fromarray(np.zeros((20, 30, 3), np.uint8)).save(os.path.join(root, "a.jpg"))
        self.assertEqual([p["name"] for p in M.session_photos(root)], ["a.jpg"])

    def test_fill_matches_orientation(self):
        ph = M.session_photos(_session())
        d = G.new_design(1000, 1000)
        d["elements"] = [G.image_el(d, "", x=0, y=0, w=600, h=300), G.image_el(d, "", x=0, y=400, w=300, h=600)]
        G.sync_page(d)
        self.assertEqual(M.fill_placeholders(d, ph), 2)
        wide, tall = d["elements"]
        self.assertGreater(M.photo_aspect(wide["path"]), 1)
        self.assertLess(M.photo_aspect(tall["path"]), 1)

    def test_album_uses_every_photo(self):
        ph = M.session_photos(_session(9))
        a = M.album(ph, "Carrusel de Instagram", "Boda", "2026")
        used = [e["path"] for pg in a["pages"] for e in pg["elements"] if e["type"] == "image"]
        self.assertEqual(sorted(used), sorted(p["path"] for p in ph))
        self.assertTrue(any(e["type"] == "text" and e["text"] == "Boda" for e in a["pages"][0]["elements"]))

    def test_focus_on_crop(self):
        fx, fy = M.focus(1.5, 1.0, [0.6, 0.0, 1.0, 1.0])
        self.assertGreater(fx, 0.7)
        self.assertEqual(fy, 0.5)


class PagesTest(unittest.TestCase):
    def test_reorder_keeps_active_page(self):
        d = G.new_design(100, 100)
        for i in range(3):
            G.add_page(d)
            d["elements"].append(G.text_el(d, f"p{i + 1}"))
            G.sync_page(d)
        G.set_page(d, 1)
        GP.reorder(d, [3, 2, 1, 0])
        self.assertEqual(d["page"], 2)
        self.assertEqual(d["elements"][0]["text"], "p1")


class RichTextTest(unittest.TestCase):
    def _el(self):
        d = G.new_design(1000, 1000)
        return G.text_el(d, "x", "body", align="left", w=800)

    def test_runs_invariant(self):
        el = self._el()
        G.set_runs(el, [{"t": "Hola "}, {"t": "mundo", "b": True, "u": True}])
        self.assertEqual(el["text"], "Hola mundo")
        self.assertEqual(el["runs"][1], {"b": True, "u": True, "t": "mundo"})
        G.set_runs(el, [{"t": "sin "}, {"t": "formato"}])
        self.assertNotIn("runs", el)
        el["runs"] = [{"t": "otra", "b": True}]   # no coincide con el texto: se ignora
        self.assertEqual(G.text_runs(el), [{"t": el["text"]}])

    def test_layout_formats_and_list(self):
        el = self._el()
        h0 = G.text_height(el)
        G.set_runs(el, [{"t": "uno "}, {"t": "dos", "k": 2.0, "color": "#FF0000"}])
        self.assertGreater(G.text_height(el), h0 * 1.4)   # el tramo grande hace la línea más alta
        lays, _ = G._text_layout(el)
        self.assertEqual(len(lays[0].formats()), 1)
        el2 = self._el()
        el2.update(text="a\nb", list="number")
        lays, _ = G._text_layout(el2)
        self.assertTrue(lays[1].text().startswith("2."))

    def test_outline_keeps_colors_and_underline(self):
        el = self._el()
        el["color"] = "#000000"
        G.set_runs(el, [{"t": "Hola "}, {"t": "mundo", "color": "#F50D8A", "u": True}])
        v = G.outline_text_el(el)
        self.assertEqual([p["fill"] for p in v["paths"]], ["#000000", "#F50D8A"])
        self.assertEqual(len(G._decorations(el)), 1)

    def test_restyle_maps_run_colors(self):
        d = G.new_design(1000, 1000)
        el = G.text_el(d, "x", "title", color="#FFFFFF")
        G.set_runs(el, [{"t": "a"}, {"t": "b", "color": "#F50D8A"}])
        d["elements"].append(el)
        G.sync_page(d)
        M.restyle(d, "Bosque")
        self.assertEqual(el["runs"][1]["color"], "#4CC38A")


if __name__ == "__main__":
    unittest.main()
