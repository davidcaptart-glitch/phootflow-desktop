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


class PhootFlowExtras(unittest.TestCase):
    """Lo añadido en PhootFlow: salidas propias, EXIF, XMP, B/N, caché, reanudación y estilos."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.st = E.Settings(ratios=["4:5"], sizes=["max", "1080"])

    def test_own_outputs_are_ignored(self):
        for n in ("a.jpg", "a_4x5_max.jpg", "a_original_1080.jpg", "a_bn_4x5_max.jpg", "a_editada.jpg"):
            scene().save(os.path.join(self.d, n))
        self.assertEqual([p.name for p in E.list_photos(self.d)], ["a"])

    def test_bw_xmp_exif_and_resume(self):
        scene().save(os.path.join(self.d, "a.jpg"), quality=93)
        st = E.Settings(ratios=["4:5"], sizes=["max"], bw=True, cache_dir=os.path.join(self.d, "cache"))
        ps = E.list_photos(self.d)
        E.analyze_many(ps, st, 2)
        self.assertTrue(ps[0].preview_path and os.path.exists(ps[0].preview_path))
        self.assertFalse(ps[0].preview)  # en disco, no en memoria
        ps[0].meta = {"make": "SONY", "model": "ILCE-6700", "iso": 3200, "date": "2026:11:15 22:10:00"}
        out = os.path.join(self.d, "out")
        n = E.export_many(ps, st, out, "9.9")
        bw = [f for f in os.listdir(os.path.join(out, "ByN"))]
        self.assertEqual(len(bw), 1)
        a = np.asarray(Image.open(os.path.join(out, "ByN", bw[0])).convert("RGB")).astype(int)
        self.assertLess(np.abs(a[..., 0] - a[..., 2]).max(), 3)  # neutro
        ex = Image.open(os.path.join(out, "Sugeridas", os.listdir(os.path.join(out, "Sugeridas"))[0])).getexif()
        self.assertEqual(ex.get(0x0110), "ILCE-6700")
        self.assertIn("PhootFlow", ex.get(0x0131))
        self.assertEqual(E.export_many(ps, st, out, "9.9"), 0)  # reanudar: no repite lo hecho
        self.assertGreater(n, 0)
        E.save_session(ps, st, os.path.join(self.d, "w"))
        fresh = E.list_photos(self.d)
        self.assertEqual(E.load_session(fresh, st, os.path.join(self.d, "w")), 1)
        self.assertEqual(fresh[0].crops, ps[0].crops)
        # XMP: solo para RAW, nunca pisa uno existente
        raw = os.path.join(self.d, "r.ARW")
        open(raw, "wb").write(b"0")
        ph = E.Photo(path=raw, name="r", ev=1.5, angle=-1.2, crops={"Original": (0, 0, 1, 1)}, keep=False)
        x = E.xmp_sidecar(ph, st)
        self.assertIn('crs:Exposure2012="+1.50"', open(x, encoding="utf-8").read())
        self.assertIn('xmp:Rating="-1"', open(x, encoding="utf-8").read())
        self.assertEqual(E.xmp_sidecar(ph, st), "")

    def test_styles_detect_and_learn_from_finals(self):
        import styles as S
        night = E.Photo(path="x", iso=6400, ev_raw=4.0, faces=3,
                        feats={"lights": 0.06, "green": 0.0, "white": 0.0, "dark": 0.6, "sat": 0.4,
                               "exposure": 1 / 60, "focal": 24, "iso": 6400, "ev_raw": 4.0, "faces": 3})
        sport = E.Photo(path="y", feats={"lights": 0.0, "green": 0.4, "white": 0.01, "dark": 0.05, "sat": 0.3,
                                         "exposure": 1 / 2000, "focal": 200, "iso": 800, "ev_raw": 0.5,
                                         "faces": 1})
        self.assertEqual(S.detect_scene([night, night])[0], "Discoteca / noche")
        self.assertEqual(S.detect_scene([sport, sport])[0], "Deporte")
        fin = os.path.join(self.d, "finales")
        os.makedirs(fin)
        for k in range(3):  # ediciones cálidas y contrastadas en 4:5
            a = np.asarray(scene(seed=k)).astype(np.float32)
            a[..., 0] *= 1.15
            a[..., 2] *= 0.85
            Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).resize((800, 1000)).save(
                os.path.join(fin, f"f{k}.jpg"))
        cfg = os.path.join(self.d, "cfg")
        path, n = S.learn_from_finals(E, fin, cfg, "Prueba", "Boda")
        self.assertEqual(n, 3)
        st = S.all_styles(cfg)["Mi estilo: Prueba"]
        self.assertGreater(st["target_b"], 0)  # aprendió que es cálido
        self.assertEqual(st["formats"][0], "4:5")
        img = np.asarray(scene()).astype(np.float32) / 255
        out = S.apply_color(img, st)
        self.assertEqual(out.shape, img.shape)
        self.assertGreater(out[..., 0].mean() - out[..., 2].mean(), img[..., 0].mean() - img[..., 2].mean())


class Updater(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(updater.is_newer("v0.10.0", "0.9.9"))
        self.assertFalse(updater.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(updater.is_newer("0.1.0", "0.2.0"))

    def test_source_update_copies_files_and_rejects_bad_zips(self):
        d = tempfile.mkdtemp()
        target, z = os.path.join(d, "app"), os.path.join(d, "u.zip")
        os.makedirs(target)
        open(os.path.join(target, "app.py"), "w").write("viejo")
        open(os.path.join(target, "requirements.txt"), "w").write("x")
        with zipfile.ZipFile(z, "w") as zz:
            zz.writestr("PhootFlow/app.py", "nuevo")
            zz.writestr("PhootFlow/assets/a.png", "png")
            zz.writestr("PhootFlow/requirements.txt", "x")
        updater.install_source(z, target)
        self.assertEqual(open(os.path.join(target, "app.py")).read(), "nuevo")
        self.assertTrue(os.path.exists(os.path.join(target, "assets", "a.png")))
        with zipfile.ZipFile(z, "w") as zz:
            zz.writestr("../fuera.py", "x")
        with self.assertRaises(RuntimeError):
            updater.install_source(z, target)


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
