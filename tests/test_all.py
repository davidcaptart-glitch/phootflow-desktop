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


class Uniformity(unittest.TestCase):
    """La sesión se edita de forma uniforme: un único ajuste para todas y brillos igualados."""

    def _ph(self, name, sec, ev, face, **kw):
        import datetime as dtm
        p = E.Photo(path=name, name=name, when=dtm.datetime(2026, 1, 1, 22, 0, 0) + dtm.timedelta(seconds=sec),
                    ev=ev, ev_auto=ev, face_lin=face, lum_med=0.01, thumb=b"x",
                    tone_q=list(np.linspace(0, 100, 64)), chroma=10.0, ab=[1.0, 2.0], **kw)
        return p

    def test_exposure_is_equalized_and_style_is_one_for_all(self):
        ps = [self._ph(f"p{i}", i * 10, 3.0, 0.02) for i in range(6)]
        ps[2].face_lin = 0.005  # cara 2 EV más oscura que las demás
        st = E.Settings(style={"target_q": list(np.linspace(5, 95, 256)), "target_chroma": 15.0, "strength": 0.6})
        sess = E.calibrate_session(ps, st)
        self.assertGreater(ps[2].ev, 3.5)  # sube para parecerse a sus vecinas
        self.assertAlmostEqual(ps[0].ev, 3.0, delta=0.05)
        self.assertEqual(len(sess["lut"]), 256)  # una sola curva para todas
        self.assertIsNone(sess["target_q"])  # ya no se ajusta foto a foto
        self.assertGreater(sess["chroma"], 1.0)

    def test_burst_keeps_open_eyes_and_sharp_face(self):
        ps = [self._ph(f"b{i}", 0.3 * i, 3.0, 0.02, sharp=0.05, face_sharp=0.05, dhash=0, eyes_open=1.0)
              for i in range(4)]
        ps[1].face_sharp = 0.09  # la más nítida… pero con los ojos cerrados
        ps[1].eyes_open = 0.0
        ps[3].face_sharp = 0.08
        E.cull(ps, E.Settings(cull_focus=False, cull_exposure=False))
        self.assertEqual([p.name for p in ps if p.keep], ["b3"])
        self.assertIn("Ráfaga", ps[0].reason)

    def test_noise_measure_tracks_real_noise(self):
        rng = np.random.default_rng(3)
        flat = np.full((800, 800), 0.4, np.float32)
        lo = E.noise_sigma(flat + rng.normal(0, 0.01, flat.shape).astype(np.float32))
        hi = E.noise_sigma(flat + rng.normal(0, 0.04, flat.shape).astype(np.float32))
        self.assertAlmostEqual(hi / lo, 4.0, delta=0.6)


class Luts(unittest.TestCase):
    def setUp(self):
        import luts as L
        self.L = L
        self.d = tempfile.mkdtemp()

    def test_identity_warm_and_1d(self):
        idp, warm = os.path.join(self.d, "id.cube"), os.path.join(self.d, "warm.cube")
        self.L.write_cube(idp, 17, lambda x: x)
        self.L.write_cube(warm, 17, lambda x: x * np.array([1.1, 1.0, 0.85], np.float32))
        img = np.random.default_rng(1).random((50, 70, 3)).astype(np.float32)
        self.assertLess(np.abs(self.L.apply(img, idp) - img).max(), 1e-4)
        w = self.L.apply(img, warm)
        self.assertGreater(w[..., 0].mean(), img[..., 0].mean())
        half = self.L.apply(img, warm, 0.5)
        self.assertAlmostEqual(float(half[..., 2].mean()), float((img[..., 2].mean() + w[..., 2].mean()) / 2),
                               delta=1e-3)
        one = os.path.join(self.d, "inv.cube")
        with open(one, "w") as f:
            f.write("LUT_1D_SIZE 2\n1 1 1\n0 0 0\n")
        self.assertLess(np.abs(self.L.apply(img, one) - (1 - img)).max(), 1e-4)
        with open(os.path.join(self.d, "malo.cube"), "w") as f:
            f.write("hola\n")
        with self.assertRaises(ValueError):
            self.L.import_luts(self.d, "Boda", [os.path.join(self.d, "malo.cube")])
        self.L.import_luts(self.d, "Boda", [warm])
        self.assertEqual([n for n, _ in self.L.available(self.d, "Boda")], ["warm"])

    def test_lut_review_flags_only_the_photos_it_ruins(self):
        import io as _io
        burn = os.path.join(self.d, "burn.cube")
        self.L.write_cube(burn, 17, lambda x: np.clip(x * 1.25, 0, 1))
        ps = []
        for i, level in enumerate([0.3, 0.32, 0.35, 0.3, 0.85]):  # la última ya es muy clara
            b = _io.BytesIO()
            Image.fromarray(np.full((40, 60, 3), int(level * 255), np.uint8)).save(b, "JPEG")
            ps.append(E.Photo(path=f"p{i}", name=f"p{i}", thumb=b.getvalue()))
        n = E.lut_review(ps, burn, 1.0)
        self.assertEqual(n, 1)
        self.assertIn(E.LUT_WARN, ps[-1].warn)
        ps[-1].lut_strength = 0.0  # el usuario quita el LUT en esa foto
        self.assertEqual(E.lut_review(ps, burn, 1.0), 0)

    def test_scene_detection_uses_ai_votes(self):
        import styles as S
        ps = [E.Photo(path=str(i), scene_ai={"Deporte": 0.7, "Boda": 0.3}) for i in range(3)]
        ps += [E.Photo(path="x", scene_ai={"Deporte": 0.1, "Boda": 0.9})]
        self.assertEqual(S.detect_scene(ps)[0], "Deporte")


class Studio(unittest.TestCase):
    """Entorno Studio: revelado tipo Lightroom no destructivo."""

    def setUp(self):
        import studio_engine as SE
        self.SE = SE
        rng = np.random.default_rng(2)
        g = np.linspace(0, 1, 256, dtype=np.float32)
        base = np.tile(g, (160, 1))
        self.lin = np.stack([base * 0.5, base * 0.4, base * 0.3], -1) ** 2.2 + rng.random((160, 256, 3)).astype(
            np.float32) * 0.002
        self.lin = self.lin.astype(np.float32)

    def L(self, img):
        return float((0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]).mean())

    def test_each_slider_moves_in_the_right_direction(self):
        SE = self.SE
        n = SE.render(self.lin, SE.new_settings(sharpen=0, nr_color=0))
        for key, up in (("exposure", 1), ("shadows", 60), ("whites", 60), ("curve_lights", 60)):
            self.assertGreater(self.L(SE.render(self.lin, SE.new_settings(**{key: up}))), self.L(n), key)
        self.assertLess(self.L(SE.render(self.lin, SE.new_settings(blacks=-60))), self.L(n))
        warm = SE.render(self.lin, SE.new_settings(temp=60))
        self.assertGreater(warm[..., 0].mean() - warm[..., 2].mean(), n[..., 0].mean() - n[..., 2].mean())
        sat = SE.render(self.lin, SE.new_settings(saturation=80))
        self.assertGreater(np.std(sat, -1).mean(), np.std(n, -1).mean())
        bw = SE.render(self.lin, SE.new_settings(bw=True))
        self.assertLess(np.abs(bw[..., 0] - bw[..., 2]).max(), 0.02)
        cut = SE.render(self.lin, SE.new_settings(crop=[0.25, 0.0, 0.75, 1.0]))
        self.assertAlmostEqual(cut.shape[1], n.shape[1] / 2, delta=2)

    def test_settings_presets_and_export_are_non_destructive(self):
        SE = self.SE
        d = tempfile.mkdtemp()
        src = os.path.join(d, "a.jpg")
        scene().save(src, quality=93)
        before = open(src, "rb").read()
        s = SE.new_settings(exposure=0.5, contrast=20, rating=4)
        SE.save(d, src, s)
        self.assertEqual(SE.load(d, src)["contrast"], 20)
        p = SE.save_preset(d, "Mi look", s)
        self.assertNotIn("rating", json.load(open(p, encoding="utf-8")))  # un preset no copia la valoración
        self.assertIn("Mi look", SE.list_presets(d))
        out = SE.export(src, s, os.path.join(d, "Studio", "a.jpg"), 90, 400)
        self.assertEqual(max(Image.open(out).size), 400)
        self.assertEqual(open(src, "rb").read(), before)  # el original no se toca


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
