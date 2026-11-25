"""Biblioteca de LUT incluida: se lista en Studio/Events, se carga por su ruta simbólica y se aplica."""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import luts as L  # noqa: E402


class BuiltinLuts(unittest.TestCase):
    def test_library_listed(self):
        b = L.builtin()
        self.assertGreaterEqual(len(b), 70)
        self.assertEqual(len({p for _, p in b}), len(b))
        self.assertTrue(all(L.is_builtin(p) for _, p in b))
        with tempfile.TemporaryDirectory() as cfg:
            mine = os.path.join(L.luts_dir(cfg, L.COMMON), "mio.cube")
            os.makedirs(os.path.dirname(mine))
            L.write_cube(mine, 5, lambda x: x)
            common = L.available(cfg, L.COMMON)
            self.assertEqual(common[0], ("mio", mine))  # los del usuario, antes
            self.assertEqual(common[1:], b)
            # en un tipo de sesión van como comunes (Studio los filtra por el prefijo para no repetirlos)
            bodas = L.available(cfg, "Bodas")
            self.assertTrue(all(label.startswith("Común: ") for label, _ in bodas))
            self.assertEqual(len(bodas), len(b) + 1)

    def test_load_and_apply(self):
        path = dict((p.rsplit("/", 1)[-1], p) for _, p in L.builtin())["Kodak Portra 400"]
        lut = L.load(path)
        self.assertEqual(lut["table"].shape, (33, 33, 33, 3))
        img = np.random.default_rng(0).random((32, 48, 3)).astype(np.float32)
        out = L.apply(img, path)
        self.assertEqual(out.shape, img.shape)
        self.assertTrue(0 < float(np.abs(out - img).mean()) < 0.3)  # cambia el color, sin destrozarlo
        bw = L.apply(img, L.BUILTIN + "street-monochrome/Standard")
        self.assertLess(float(np.abs(bw[..., 0] - bw[..., 2]).max()), 0.02)  # B/N: canales iguales

    def test_pack_roundtrip(self):
        g = np.linspace(0, 1, 9, dtype=np.float32)
        b, gg, r = np.meshgrid(g, g, g, indexing="ij")
        tab = np.stack([r ** 0.8, gg, b * 0.5], -1)
        self.assertLess(float(np.abs(L.unpack_table(L.pack_table(tab)) - tab).max()), 1 / 4095 + 1e-6)

    def test_unknown_builtin(self):
        with self.assertRaises(ValueError):
            L.load(L.BUILTIN + "no/existe")


if __name__ == "__main__":
    unittest.main()
