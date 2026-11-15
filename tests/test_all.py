"""Pruebas sin fotos reales: motor sobre imágenes sintéticas, actualizador y Drive contra un servidor simulado."""
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import drive  # noqa: E402
import engine as E  # noqa: E402
import updater  # noqa: E402


def scene(tilt=0.0, blur=0.0, gain=1.0, seed=1):
    rng = np.random.default_rng(seed)
    a = np.full((1200, 800, 3), 14, np.float32)
    cv2.rectangle(a, (250, 420), (550, 950), (210, 200, 185), -1)
    cv2.circle(a, (400, 330), 80, (190, 150, 130), -1)
    for x in range(30, 800, 55):
        cv2.line(a, (x, 0), (x, 1199), (95, 95, 95), 3)
    for y in range(500, 950, 30):
        cv2.line(a, (250, y), (550, y), (120, 110, 100), 1)
    if tilt:
        m = cv2.getRotationMatrix2D((400, 600), tilt, 1.0)
        a = cv2.warpAffine(a, m, (800, 1200), borderMode=cv2.BORDER_REFLECT)
    if blur:
        a = cv2.GaussianBlur(a, (0, 0), blur)
    a = a * gain + rng.normal(0, 2, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


class Engine(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.st = E.Settings(ratios=["4:5", "16:9"], sizes=["max", "1080"])

    def photos(self, **imgs):
        for n, im in imgs.items():
            im.save(os.path.join(self.d, n + ".jpg"), quality=93)
        ps = [E.analyze(p, self.st) for p in E.list_photos(self.d)]
        self.assertFalse([p.error for p in ps if p.error])
        return ps

    def test_tilt_is_detected_and_sign_is_right(self):
        p = self.photos(a=scene(tilt=3.0))[0]
        self.assertAlmostEqual(p.angle, -3.0, delta=0.5)  # se corrige girando al revés
        self.assertEqual(self.photos(b=scene())[1].angle, 0.0)

    def test_cull_blur_dark_and_duplicates(self):
        ps = self.photos(a=scene(), b=scene(seed=2), c=scene(seed=3), d=scene(blur=6), e=scene(gain=0.002),
                         f=scene(tilt=4), g=scene(tilt=-4), h=scene(tilt=2))
        for p, t in zip(ps, range(len(ps))):
            p.when = None
        E.cull(ps, self.st)
        r = {p.name: (p.keep, p.reason) for p in ps}
        self.assertEqual(sum(r[n][0] for n in "abc"), 1, r)  # tres iguales: queda una
        self.assertIn("duplicada", [r[n][1] for n in "abc" if not r[n][0]][0])
        self.assertIn("Desenfocada", r["d"][1])
        self.assertIn("Subexpuesta", r["e"][1])

    def test_export_ratios_sizes_and_archive(self):
        ps = self.photos(a=scene())
        out = os.path.join(self.d, "out")
        files = E.export(ps[0], self.st, out)
        self.assertTrue(files)
        for f in files:
            w, h = Image.open(f).size
            base = os.path.basename(f)
            for tag, r in (("4x5", 4 / 5), ("16x9", 16 / 9)):
                if f"_{tag}_" in base:
                    self.assertAlmostEqual(w / h, r, delta=0.01, msg=base)
            if base.endswith("_1080.jpg"):
                self.assertEqual(w, 1080)
            self.assertTrue(Image.open(f).info.get("icc_profile"))
        z = E.archive_originals(ps, out)
        with zipfile.ZipFile(z) as zz:
            self.assertEqual(zz.namelist(), ["a.jpg"])
            self.assertEqual(zz.read("a.jpg"), open(ps[0].path, "rb").read())  # recuperable bit a bit
        self.assertTrue(os.path.exists(ps[0].path))  # el original no se toca

    def test_discards_are_moved_never_deleted_and_raw_pairs_stay_together(self):
        for n in ("x.ARW", "x.JPG", "y.jpg"):
            open(os.path.join(self.d, n), "wb").write(b"0")
        ps = E.list_photos(self.d)
        self.assertEqual(len(ps), 2)
        ps[0].keep = False
        E.move_discards(ps, self.d)
        self.assertEqual(sorted(os.listdir(os.path.join(self.d, "Descartadas"))), ["x.ARW", "x.JPG"])
        self.assertTrue(os.path.exists(os.path.join(self.d, "y.jpg")))


class Updater(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(updater.is_newer("v0.10.0", "0.9.9"))
        self.assertFalse(updater.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(updater.is_newer("0.1.0", "0.2.0"))


class FakeGoogle(http.server.BaseHTTPRequestHandler):
    store = {"chunks": [], "calls": [], "files": {}}

    def _json(self, code, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        s = self.store
        s["calls"].append(("GET", self.path))
        if self.headers.get("Authorization") != "Bearer fresh":
            return self._json(401, {"error": "bad token"})
        if self.path.startswith("/drive/files?"):
            return self._json(200, {"files": []})
        self._json(200, {"id": "x", "name": "Clientes", "mimeType": drive.FOLDER_MIME,
                         "webViewLink": "https://drive.google.com/drive/folders/EDIT"})

    def do_POST(self):
        s = self.store
        body = self._body()
        s["calls"].append(("POST", self.path, body))
        if self.path == "/token":
            return self._json(200, {"access_token": "fresh", "expires_in": 3600})
        if self.path.startswith("/upload/"):
            host = self.headers["Host"]
            return self._json(200, {}, {"Location": f"http://{host}/session/1"})
        self._json(200, {"id": "NEW"})

    def do_PUT(self):
        s = self.store
        body = self._body()
        rng = self.headers["Content-Range"]
        s["chunks"].append((rng, body))
        total = int(rng.split("/")[1])
        got = sum(len(b) for _, b in s["chunks"])
        if got < total:
            self.send_response(308)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._json(200, {"id": "FILE"})

    def log_message(self, *a):
        pass


class Drive(unittest.TestCase):
    def test_refresh_folder_chunked_upload_and_link(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGoogle)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        drive.API, drive.UPLOAD, drive.CHUNK = base + "/drive", base + "/upload/drive", 1000
        cfg = tempfile.mkdtemp()
        d = drive.Drive(cfg)
        json.dump({"installed": {"client_id": "c", "client_secret": "s", "auth_uri": base + "/auth",
                                 "token_uri": base + "/token"}}, open(d.client_path, "w"))
        json.dump({"access_token": "old", "refresh_token": "r", "expires_at": time.time() - 5},
                  open(d.token_path, "w"))
        self.assertEqual(d.check_folder("ROOTFOLDER1"), "Clientes")
        self.assertEqual(d.ensure_folder("Boda", "ROOTFOLDER1"), "NEW")
        data = os.urandom(2500)
        p = os.path.join(cfg, "foto.jpg")
        open(p, "wb").write(data)
        self.assertEqual(d.upload(p, "NEW"), "FILE")
        ch = FakeGoogle.store["chunks"]
        self.assertEqual([c[0] for c in ch], ["bytes 0-999/2500", "bytes 1000-1999/2500", "bytes 2000-2499/2500"])
        self.assertEqual(b"".join(c[1] for c in ch), data)
        self.assertEqual(d.share_link("NEW"), "https://drive.google.com/drive/folders/EDIT")
        perm = [c for c in FakeGoogle.store["calls"] if c[0] == "POST" and "/permissions" in c[1]][0]
        self.assertEqual(json.loads(perm[2]), {"role": "reader", "type": "anyone"})
        self.assertEqual(json.load(open(d.token_path))["refresh_token"], "r")
        srv.shutdown()

    def test_folder_id_parsing(self):
        fid = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
        self.assertEqual(drive.folder_id_from(f"https://drive.google.com/drive/folders/{fid}?usp=sharing"), fid)
        self.assertEqual(drive.folder_id_from(f"https://drive.google.com/drive/u/0/folders/{fid}"), fid)
        self.assertEqual(drive.folder_id_from(fid), fid)
        with self.assertRaises(drive.DriveError):
            drive.folder_id_from("mi carpeta")


if __name__ == "__main__":
    unittest.main()
