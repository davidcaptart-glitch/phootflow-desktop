"""Formatos de entrada (RAW de varias marcas, JPEG, HEIF, WebP, 16 bits…) y color selectivo de Studio.
Imágenes sintéticas, sin fotos reales."""
import os
import sys
import tempfile
import unittest

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine as E  # noqa: E402
import studio_engine as SE  # noqa: E402


def card():
    a = np.full((400, 600, 3), 128, np.uint8)
    a[80:320, 60:280] = (215, 25, 30)   # «jersey» rojo
    a[80:320, 330:550] = (30, 160, 60)  # verde
    return a


def sat(x):
    return float(x.max(-1).mean() - x.min(-1).mean())


class Formats(unittest.TestCase):
    def test_brands_and_formats_are_listed(self):
        for e in (".arw", ".cr2", ".cr3", ".nef", ".nrw", ".raf", ".orf", ".rw2", ".pef", ".dng", ".srw", ".3fr",
                  ".iiq"):
            self.assertIn(e, E.RAW_EXT)
        for e in (".jpg", ".jpeg", ".png", ".tif", ".webp", ".bmp", ".avif"):
            self.assertIn(e, E.IMG_EXT)
        self.assertIn("*.cr3", E.file_filter())
        self.assertNotIn("*.nef", E.file_filter(raw=False))

    def test_every_image_format_is_read(self):
        d = tempfile.mkdtemp()
        im = Image.fromarray(card())
        im.save(os.path.join(d, "a.JPG"), quality=95)
        im.save(os.path.join(d, "b.webp"), quality=95)
        im.save(os.path.join(d, "c.bmp"))
        im.convert("RGBA").save(os.path.join(d, "d.png"))
        cv2.imwrite(os.path.join(d, "e.tif"), card()[..., ::-1].astype(np.uint16) * 257)  # 16 bits
        if E.HEIF:
            ex = Image.Exif()
            ex[0x0110] = "ILCE-6700"
            ex.get_ifd(0x8769)[0x9003] = "2026:11:20 18:00:00"
            im.save(os.path.join(d, "f.HIF"), quality=95, exif=ex.tobytes())
        ps = E.list_photos(d)
        self.assertEqual(len(ps), 6 if E.HEIF else 5)
        ref = E.load_linear(os.path.join(d, "c.bmp"), half=False)
        for ph in ps:
            x = E.load_linear(ph.path, half=False)
            self.assertEqual(x.shape, (400, 600, 3), ph.path)
            self.assertLess(float(np.abs(x - ref).mean()), 0.01, ph.path)
        if E.HEIF:
            ph = next(p for p in ps if p.path.endswith(".HIF"))
            E.read_meta(ph)
            self.assertEqual(ph.meta.get("model"), "ILCE-6700")
            self.assertEqual(ph.when.year, 2026)

    def test_16bit_tiff_keeps_precision(self):
        d = tempfile.mkdtemp()
        ramp = np.tile(np.linspace(0, 65535, 1000, dtype=np.float64), (40, 1))
        a = np.stack([ramp] * 3, -1).astype(np.uint16)
        p = os.path.join(d, "r.tif")
        cv2.imwrite(p, a)
        x = E.load_linear(p, half=False)
        self.assertGreater(len(np.unique(x[0, :, 0])), 600)  # con 8 bits no pasaría de 256

    def test_unreadable_raw_gives_a_clear_error(self):
        if E.rawpy is None:
            self.skipTest("sin rawpy")
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.NEF")
        with open(p, "wb") as f:
            f.write(b"no es un raw")
        ph = E.analyze(E.list_photos(d)[0], E.Settings())
        self.assertIn("no se puede leer", ph.error)


class HighEfficiencyNef(unittest.TestCase):
    """RAW que LibRaw no revela (NEF «High Efficiency»): se convierte a DNG con el Adobe DNG Converter (simulado)."""

    def setUp(self):
        self.saved = (E.dng_converter, E._run_converter, E._develop_raw)
        self.calls = []
        real = E._develop_raw

        def develop(path, half, rebuild):
            if path.lower().endswith(".nef"):
                raise RuntimeError("LibRawFileUnsupportedError")  # como con TicoRAW
            return np.full((30, 40, 3), 0.2, np.float32) if path.endswith(".dng") else real(path, half, rebuild)

        def convert(exe, src, out_dir, name):
            self.calls.append(src)
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(b"DNG")

        E.dng_converter, E._run_converter, E._develop_raw = (lambda: "conversor.exe"), convert, develop

    def tearDown(self):
        E.dng_converter, E._run_converter, E._develop_raw = self.saved
        E._DNG_OF.clear()

    def test_he_nef_is_converted_once_and_developed(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "DSC_0001.NEF")
        with open(p, "wb") as f:
            f.write(b"nef he")
        x = E.load_linear(p, half=True)
        self.assertEqual(x.shape, (30, 40, 3))
        E.load_linear(p, half=False)
        self.assertEqual(self.calls, [p])  # una sola conversión
        self.assertNotIn(p, E.PREVIEW_ONLY)
        dng = E._DNG_OF[p]
        self.assertTrue(dng.startswith(os.path.join(d, ".phootflow", "dng")))
        self.assertEqual(open(p, "rb").read(), b"nef he")  # el original, intacto
        E._DNG_OF.clear()  # otra sesión: reutiliza el DNG de la caché
        E.load_linear(p, half=True)
        self.assertEqual(len(self.calls), 1)


class Splash(unittest.TestCase):
    def setUp(self):
        self.lin = E._to_linear(card().astype(np.float32) / 255)
        self.red = SE.hue_of(np.array([215, 25, 30]) / 255)[0]

    def test_picked_color_stays_rest_goes_bw(self):
        out = SE.render(self.lin, SE.new_settings(splash=True, splash_hues=[self.red]), crop=False)
        self.assertGreater(sat(out[120:280, 100:240]), 0.5)
        self.assertLess(sat(out[120:280, 370:510]), 0.02)
        self.assertLess(sat(out[:40, :40]), 0.02)

    def test_band_buttons_and_mask_mode(self):
        out = SE.render(self.lin, SE.new_settings(splash=True, splash_hues=[SE.band_hue(120)]), crop=False)
        self.assertGreater(sat(out[120:280, 370:510]), 0.3)  # botón verde
        self.assertLess(sat(out[120:280, 100:240]), 0.02)
        import masks as M
        m = M.new_mask("radial")
        m["params"].update(cx=0.73, cy=0.5, rx=0.2, ry=0.35, feather=5)
        s = SE.new_settings(splash=True, splash_mode="mask", masks=[m])
        out = SE.render(self.lin, s, crop=False)
        self.assertGreater(sat(out[150:250, 400:480]), 0.3)  # dentro de la máscara, cualquier color
        self.assertLess(sat(out[120:280, 100:240]), 0.02)
        out = SE.render(self.lin, dict(s, splash_mode="both", splash_hues=[self.red]), crop=False)
        self.assertLess(sat(out[150:250, 400:480]), 0.02)  # verde dentro de la máscara, pero no elegido

    def test_skin_is_protected_from_red(self):
        y = np.zeros((200, 400, 3), np.float32)
        y[:, :200] = E._to_linear(np.array([0.85, 0.1, 0.12], np.float32))
        y[:, 200:] = E._to_linear(np.array([0.95, 0.75, 0.6], np.float32))  # piel
        s = SE.new_settings(splash=True, splash_hues=[self.red])
        out = SE.render(y, s, crop=False)
        self.assertGreater(sat(out[:, :180]), 0.5)
        self.assertLess(sat(out[:, 220:]), 0.03)
        self.assertGreater(sat(SE.render(y, dict(s, splash_skin=False, splash_range=45), crop=False)[:, 220:]), 0.03)

    def test_view_and_bw_interplay(self):
        s = SE.new_settings(splash=True, splash_hues=[self.red], bw=True)
        out = SE.render(self.lin, s, crop=False)
        self.assertGreater(sat(out[120:280, 100:240]), 0.5)  # el color selectivo manda sobre el B/N
        v = SE.render(self.lin, dict(s, _splash_view=True), crop=False)
        self.assertGreater(v[20, 20, 0] - v[20, 20, 1], 0.2)  # lo que pasa a B/N, en magenta


if __name__ == "__main__":
    unittest.main()
