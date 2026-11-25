"""Perfiles DCP: lectura, búsqueda por cámara y aplicación del color (perfiles sintéticos, sin Adobe)."""
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dcp  # noqa: E402

SRGB_TO_XYZ50 = np.linalg.inv(np.array([[3.1338561, -1.6168667, -0.4906146],
                                        [-0.9787684, 1.9161415, 0.0334540],
                                        [0.0719453, -0.2289914, 1.4052427]]))


def write_dcp(path, camera, name, fm, cm, hsm=None, illum=21):
    """DCP mínimo (TIFF «IIRC»): cámara, nombre, matrices y, si se pide, HueSatMap."""
    entries, blobs = [], []

    def add(tag, typ, count, payload):
        entries.append((tag, typ, count, payload))

    srat = lambda m: b"".join(struct.pack("<ii", int(round(v * 10000)), 10000) for v in np.ravel(m))  # noqa: E731
    add(50708, 2, len(camera) + 1, camera.encode() + b"\0")
    add(50721, 10, 9, srat(cm))
    add(50778, 3, 1, struct.pack("<H", illum) + b"\0\0")
    add(50936, 2, len(name) + 1, name.encode() + b"\0")
    add(50964, 10, 9, srat(fm))
    if hsm is not None:
        dims, data = hsm
        add(50937, 4, 3, struct.pack("<3I", *dims))
        add(50938, 11, data.size, data.astype("<f4").tobytes())
    entries.sort()
    off_data = 8 + 2 + 12 * len(entries) + 4
    ifd = struct.pack("<H", len(entries))
    for tag, typ, count, payload in entries:
        if len(payload) <= 4:
            ifd += struct.pack("<HHI", tag, typ, count) + payload.ljust(4, b"\0")
        else:
            ifd += struct.pack("<HHII", tag, typ, count, off_data + sum(len(b) for b in blobs))
            blobs.append(payload)
    with open(path, "wb") as f:
        f.write(b"IIRC" + struct.pack("<I", 8) + ifd + b"\0\0\0\0" + b"".join(blobs))


class DcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["PHOOTFLOW_DCP_DIR"] = self.tmp
        dcp.reset_cache()
        self.fm = SRGB_TO_XYZ50
        self.cm = np.linalg.inv(SRGB_TO_XYZ50)
        write_dcp(os.path.join(self.tmp, "Sony ILCE-6700 Adobe Standard.dcp"), "Sony ILCE-6700", "Adobe Standard",
                  self.fm, self.cm)
        # «Camera ST»: gira todos los tonos +30° y satura ×1,2
        data = np.zeros((1, 6, 2, 3), np.float32)
        data[..., 0], data[..., 1], data[..., 2] = 30.0, 1.2, 1.0
        write_dcp(os.path.join(self.tmp, "Sony ILCE-6700 Camera ST.dcp"), "Sony ILCE-6700", "Camera ST",
                  self.fm, self.cm, hsm=((6, 2, 1), data))
        write_dcp(os.path.join(self.tmp, "Nikon Z 6_2 Adobe Standard.dcp"), "Nikon Z 6_2", "Adobe Standard",
                  self.fm, self.cm)

    def tearDown(self):
        os.environ.pop("PHOOTFLOW_DCP_DIR", None)
        dcp.reset_cache()

    def test_read_and_find(self):
        names = [p.name for p in dcp.profiles_for("SONY", "ILCE-6700")]
        self.assertEqual(names[0], "Adobe Standard")
        self.assertIn("Camera ST", names)
        self.assertEqual(dcp.find("SONY", "ILCE-6700", "Camera ST").name, "Camera ST")
        # un perfil que esa cámara no tiene -> Adobe Standard; «Sin perfil» -> None
        self.assertEqual(dcp.find("SONY", "ILCE-6700", "Camera Vivid").name, "Adobe Standard")
        self.assertIsNone(dcp.find("SONY", "ILCE-6700", dcp.NONE))
        self.assertEqual(dcp.find("NIKON CORPORATION", "NIKON Z 6_2", "Adobe Standard").camera, "Nikon Z 6_2")
        self.assertEqual(dcp.profiles_for("Acme", "Acme Fotomatic 9"), [])
        pr = dcp.find("SONY", "ILCE-6700", "Camera ST")
        self.assertEqual(pr.hsm_dims, (6, 2, 1))
        np.testing.assert_allclose(pr.fm[0], self.fm, atol=1e-4)

    def test_matrix_only_is_identity_for_srgb_camera(self):
        pr = dcp.find("SONY", "ILCE-6700", "Adobe Standard")
        rng = np.random.default_rng(1)
        x = rng.uniform(0.05, 0.9, (40, 30, 3)).astype(np.float32)
        y = dcp.apply(x, pr, np.ones(3), strip=7)
        np.testing.assert_allclose(y, x, atol=2e-3)
        # el blanco de la cámara sale blanco con cualquier neutro
        w = dcp.apply(np.full((2, 2, 3), 0.5, np.float32), pr, np.array([0.5, 1.0, 0.7]))
        self.assertLess(float(np.ptp(w[0, 0])), 2e-3)

    def test_hue_sat_map(self):
        import cv2
        pr = dcp.find("SONY", "ILCE-6700", "Camera ST")
        x = np.zeros((1, 1, 3), np.float32)
        x[0, 0] = (0.6, 0.2, 0.2)  # rojo
        y = dcp.apply(x, pr, np.ones(3))
        # el giro y la saturación se aplican en ProPhoto: en sRGB solo se comprueba la dirección
        h0 = cv2.cvtColor(x, cv2.COLOR_RGB2HSV)[0, 0]
        h1 = cv2.cvtColor(y, cv2.COLOR_RGB2HSV)[0, 0]
        self.assertGreater(h1[0], h0[0] + 10)  # hacia el amarillo
        gray = dcp.apply(np.full((1, 1, 3), 0.4, np.float32), pr, np.ones(3))
        self.assertLess(float(np.ptp(gray)), 2e-3)  # los grises no cambian

    def test_style_profile(self):
        for name in ("Camera VV2", "Camera BW", "Camera Vivid", "Camera Vivid v2"):
            write_dcp(os.path.join(self.tmp, f"Sony ILCE-6700 {name}.dcp"), "Sony ILCE-6700", name, self.fm, self.cm)
        dcp.reset_cache()
        sp = lambda s: dcp.style_profile("SONY", "ILCE-6700", s).name  # noqa: E731
        self.assertEqual(sp("Standard"), "Camera ST")
        self.assertEqual(sp("Off"), "Camera ST")       # automático inteligente
        self.assertEqual(sp("VV2"), "Camera VV2")
        self.assertEqual(sp("Vivid"), "Camera Vivid v2")  # el nombre largo y la versión más nueva
        self.assertEqual(sp("BW"), "Camera ST")        # el B/N lo hace Events: base en color
        self.assertEqual(sp(""), "Camera ST")
        # sin perfiles «Camera …»: Adobe Standard
        write_dcp(os.path.join(self.tmp, "Acme Uno Adobe Standard.dcp"), "Acme Uno", "Adobe Standard",
                  self.fm, self.cm)
        dcp.reset_cache()
        self.assertEqual(dcp.style_profile("Acme", "Uno", "VIVID").name, "Adobe Standard")
        self.assertIsNone(dcp.style_profile("Acme", "Acme Fotomatic 9", "Standard"))

    def test_label(self):
        self.assertEqual(dcp.label("Camera ST"), "Cámara · Estándar (ST)")
        self.assertEqual(dcp.label("Camera Vivid v2"), "Cámara · Vívido (Vivid)")
        self.assertEqual(dcp.label("Adobe Standard"), "Adobe Standard")


if __name__ == "__main__":
    unittest.main()
