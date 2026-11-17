"""Pruebas sin fotos reales: motor sobre imágenes sintéticas, actualizador, y Drive y licencias contra servidores
simulados."""
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
import admin_licencias  # noqa: E402
import drive  # noqa: E402
import engine as E  # noqa: E402
import license as LIC  # noqa: E402
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


class StylesPerPhoto(unittest.TestCase):
    def test_bw_styles_are_distinct_and_per_photo_wins(self):
        img = np.asarray(scene()).astype(np.float32) / 255
        outs = {n: E.bw_editorial(img, 1, n) for n in E.BW_STYLES}
        means = [round(float(o.mean()), 3) for o in outs.values()]
        self.assertGreater(len(set(means)), 7)  # cada estilo da un resultado distinto
        self.assertLess(np.abs(outs["Editorial"][..., 0] - outs["Editorial"][..., 2]).max(), 1e-6)  # neutro
        sep = outs["Sepia"]
        self.assertGreater(sep[..., 0].mean(), sep[..., 2].mean())  # sepia = cálido
        st = E.Settings(bw_style="Noir", look="Vivo")
        ph = E.Photo(path="x")
        self.assertEqual(E.photo_bw_style(ph, st), "Noir")
        self.assertEqual(E.photo_look(ph, st), "Vivo")
        ph.bw_style, ph.look = "Clave alta", "Frío"
        self.assertEqual((E.photo_bw_style(ph, st), E.photo_look(ph, st)), ("Clave alta", "Frío"))

    def test_color_looks_and_no_bw_export(self):
        img = np.asarray(scene()).astype(np.float32) / 255
        self.assertIs(E.apply_look(img, "Natural"), img)
        warm, cold = E.apply_look(img, "Cálido"), E.apply_look(img, "Frío")
        self.assertGreater(warm[..., 0].mean() - warm[..., 2].mean(), cold[..., 0].mean() - cold[..., 2].mean())
        vivid = E.apply_look(img, "Vivo")
        self.assertGreater(np.std(vivid, -1).mean(), np.std(img, -1).mean())
        d = tempfile.mkdtemp()
        scene().save(os.path.join(d, "a.jpg"), quality=93)
        st = E.Settings(ratios=[], sizes=["max"], bw=True)
        ps = E.list_photos(d)
        E.analyze(ps[0], st)
        ps[0].bw_style = E.BW_NONE  # a esta foto no le va el B/N
        E.export(ps[0], st, os.path.join(d, "out"))
        self.assertFalse(os.path.isdir(os.path.join(d, "out", "ByN")))
        ps[0].bw_style = "Selenio"
        E.export(ps[0], st, os.path.join(d, "out"))
        self.assertTrue(os.listdir(os.path.join(d, "out", "ByN")))


class Masks(unittest.TestCase):
    """Máscaras de Studio sin IA (las de IA necesitan los modelos descargados)."""

    def test_geometric_and_range_masks_and_local_adjust(self):
        import masks as M
        import studio_engine as SE
        img = np.asarray(scene()).astype(np.float32) / 255
        rad = M.build(dict(M.new_mask("radial"), params=dict(cx=0.5, cy=0.5, rx=0.2, ry=0.2, feather=30)), img)
        self.assertGreater(rad[img.shape[0] // 2, img.shape[1] // 2], 0.9)
        self.assertLess(rad[5, 5], 0.01)
        lin = M.build(dict(M.new_mask("linear"), params=dict(x0=0.5, y0=0.0, x1=0.5, y1=0.5)), img)
        self.assertGreater(lin[0, 10], 0.9)
        self.assertLess(lin[-1, 10], 0.01)
        inv = M.build(dict(M.new_mask("linear"), params=dict(x0=0.5, y0=0.0, x1=0.5, y1=0.5), invert=True), img)
        self.assertAlmostEqual(float(inv[0, 10] + lin[0, 10]), 1.0, delta=1e-4)
        br = dict(M.new_mask("brush"), params=dict(strokes=[[0.5, 0.5, 0.05, 1], [0.5, 0.5, 0.02, -1]], feather=0))
        b = M.build(br, img)
        self.assertLess(b[img.shape[0] // 2, img.shape[1] // 2], 0.1)  # borrado en el centro
        self.assertGreater(b.max(), 0.9)
        luma = M.build(dict(M.new_mask("luma"), params=dict(lo=60, hi=100, feather=5)), img)
        self.assertGreater(float(luma[img.mean(-1) > 0.75].mean()), float(luma[img.mean(-1) < 0.2].mean()))
        m = dict(M.new_mask("radial"), params=dict(cx=0.5, cy=0.5, rx=0.2, ry=0.2, feather=30))
        m["adj"]["exposure"] = 1.0
        out = M.apply_all(img, [m])
        c = (img.shape[0] // 2, img.shape[1] // 2)
        self.assertGreater(out[c].mean(), img[c].mean())  # dentro se aclara
        self.assertAlmostEqual(float(out[5, 5].mean()), float(img[5, 5].mean()), delta=1e-3)  # fuera no cambia
        lin_img = (img ** 2.2).astype(np.float32)
        r = SE.render(lin_img, SE.new_settings(masks=[m]), key="t")
        self.assertEqual(r.shape, img.shape)


class ReviewFlow(unittest.TestCase):
    def test_studio_overrides_are_found_for_export(self):
        import review
        import studio_engine as SE
        d = tempfile.mkdtemp()
        for n in ("a.jpg", "b.jpg"):
            scene().save(os.path.join(d, n))
        SE.save(d, os.path.join(d, "a.jpg"), SE.new_settings(exposure=0.4, _events_override=True))
        SE.save(d, os.path.join(d, "b.jpg"), SE.new_settings(exposure=0.2))  # editada en Studio, no para Events
        found = review.pending_studio_paths(d)
        self.assertEqual(list(found), [os.path.normcase(os.path.join(d, "a.jpg"))])
        self.assertEqual(found[os.path.normcase(os.path.join(d, "a.jpg"))]["exposure"], 0.4)


class Zoom(unittest.TestCase):
    def test_region_matches_full_rotate(self):
        import engine as E
        import zoomview as Z
        yy, xx = np.mgrid[0:600, 0:900].astype(np.float32)
        lin = np.dstack([xx / 900, yy / 600, (xx + yy) / 1500]).astype(np.float32)
        for ang in (0.0, 2.5):
            full = E.rotate(lin, ang)
            box = (0.3, 0.4, 0.6, 0.7)
            h, w = full.shape[:2]
            ref = full[int(round(box[1] * h)):, int(round(box[0] * w)):]
            part = Z.region(lin, ang, box, lambda a: a, E._safe_rect)
            ref = ref[:part.shape[0], :part.shape[1]]
            self.assertLess(float(np.abs(part[2:-2, 2:-2] - ref[2:-2, 2:-2]).mean()), 0.01)

    def test_zoom_geometry(self):
        from PySide6.QtCore import QPointF, QRectF, QSize
        from PySide6.QtWidgets import QApplication, QWidget
        import zoomview as Z
        QApplication.instance() or QApplication([])
        w = QWidget()
        z = Z.Zoom(w)
        z.reset("a", (6000, 4000))
        area, pm = QRectF(0, 0, 800, 600), QSize(1500, 1000)
        self.assertFalse(z.active)
        z.toggle(QPointF(400, 300), area, pm)
        self.assertEqual(z.pct, 1.0)
        r = z.rect(area, pm)
        self.assertAlmostEqual(r.width(), 6000, places=3)
        b = z.visible_box(area, pm)
        self.assertAlmostEqual(b[2] - b[0], 800 / 6000, places=3)
        z.wheel(-120, QPointF(400, 300), area, pm)
        self.assertEqual(z.pct, 0.5)
        z.toggle(None, area, pm)
        self.assertFalse(z.active)
class Edits(unittest.TestCase):
    """Entorno Edits: retoque tipo Photoshop por capas, no destructivo."""

    def setUp(self):
        import edits_engine as EE
        self.EE = EE
        rng = np.random.default_rng(5)
        img = np.clip(0.5 + 0.05 * rng.standard_normal((600, 900, 3)), 0, 1).astype(np.float32)
        self.clean = cv2.GaussianBlur(img, (0, 0), 1.0)
        self.img = self.clean.copy()
        cv2.circle(self.img, (450, 300), 10, (0.9, 0.1, 0.1), -1)  # un «grano» rojo

    def spot(self, a):
        return a[288:313, 438:463]

    def test_heal_clone_and_fill_remove_a_spot_keeping_the_texture(self):
        EE = self.EE
        texture = float(self.clean[100:125, 100:125].std())
        for tool in ("heal", "fill", "clone"):
            op = {"tool": tool, "r": 13 / 900, "pts": [[450 / 899, 300 / 599]]}
            op["src"] = EE.find_source(self.img, op)
            self.assertIsNotNone(op["src"], tool)
            out = EE.apply_retouch(self.img, {"ops": [op]})
            reg = self.spot(out)
            self.assertLess(float(np.abs(reg[..., 0] - reg[..., 1]).mean()), 0.06, tool)  # sin rojo
            self.assertLess(abs(float(reg.std()) - texture), 0.01, tool)  # misma textura que alrededor
            self.assertLess(float(np.abs(out[:200] - self.img[:200]).max()), 1e-6, tool)  # nada más cambia

    def test_layers_order_visibility_opacity_crop_and_text(self):
        EE = self.EE
        doc = EE.new_doc()
        EE.add_layer(doc, "text", text="© Prueba", size=8, x=0.5, y=0.5, anchor="mm")
        r = EE.add_layer(doc, "retouch")
        EE.add_layer(doc, "liquify")
        self.assertEqual([l["kind"] for l in doc["layers"]], ["liquify", "retouch", "text"])
        op = {"tool": "heal", "r": 13 / 900, "pts": [[450 / 899, 300 / 599]]}
        op["src"] = EE.find_source(self.img, op)
        r["ops"].append(op)
        box = [0.1, 0.1, 0.9, 0.9]
        out, alpha = EE.compose(self.img, doc, box)
        self.assertIsNone(alpha)
        self.assertEqual(out.shape[:2], EE.apply_crop(self.img, box).shape[:2])  # el texto va sobre la recortada
        txt = EE.text_rgba(out.shape, doc["layers"][-1])
        self.assertIsNotNone(txt)
        x0, y0, x1, y1 = txt[1]
        self.assertLess(abs((x0 + x1) / 2 - out.shape[1] / 2), 3)  # anclado al centro
        r["visible"] = False
        doc["layers"][-1]["opacity"] = 0
        plain, _ = EE.compose(self.img, doc, [0.0, 0.0, 1.0, 1.0])
        self.assertTrue(np.allclose(plain, self.img, atol=1e-6))  # ocultas u opacidad 0: no hacen nada
        r["visible"], r["opacity"] = True, 50
        half, _ = EE.compose(self.img, doc, [0.0, 0.0, 1.0, 1.0])
        self.assertAlmostEqual(float(self.spot(half)[..., 0].mean()),
                               (float(self.spot(self.img)[..., 0].mean()) + float(self.spot(EE.apply_retouch(
                                   self.img, r))[..., 0].mean())) / 2, delta=0.01)
        c = EE.Compositor()  # la caché por prefijo da lo mismo que componer desde cero
        self.assertTrue(np.allclose(c.render(self.img, doc, box)[0], EE.compose(self.img, doc, box)[0]))
        self.assertTrue(np.allclose(c.render(self.img, doc, box)[0], EE.compose(self.img, doc, box)[0]))

    def test_liquify_skin_and_light(self):
        EE = self.EE
        a = np.full((300, 300, 3), 0.2, np.float32)
        a[:, 140:160] = 0.9  # barra vertical
        lay = EE.new_layer("liquify", strokes=[{"mode": "push", "r": 0.15, "s": 0.8,
                                                 "pts": [[0.5, 0.5], [0.62, 0.5]]}])
        out = EE.apply_liquify(a, lay)
        cols = np.nonzero(out[150, :, 0] > 0.55)[0]
        self.assertGreater(cols.mean(), 155)  # la barra se ha desplazado hacia la derecha
        self.assertTrue(np.allclose(out[:20], a[:20], atol=1e-4))  # lejos del pincel no se mueve
        dot = np.full((300, 300, 3), 0.2, np.float32)
        cv2.circle(dot, (150, 150), 20, (0.9, 0.9, 0.9), -1)
        big = EE.apply_liquify(dot, EE.new_layer("liquify", strokes=[{"mode": "bloat", "r": 0.2, "s": 1.0,
                                                                        "pts": [[0.5, 0.5]] * 4}]))
        self.assertGreater((big[..., 0] > 0.55).sum(), (dot[..., 0] > 0.55).sum())  # inflar agranda
        # piel: alisa dentro de la zona pintada y no fuera (sin IA: solo los trazos)
        skin = EE.new_layer("skin", auto=False, amount=100, texture=0, strokes=[[0.25, 0.5, 0.12, 1]])
        sm = EE.apply_skin(self.clean, skin)
        self.assertLess(float(sm[260:340, 180:260].std()), 0.6 * float(self.clean[260:340, 180:260].std()))
        self.assertTrue(np.allclose(sm[:, 600:], self.clean[:, 600:], atol=1e-4))
        # blanquear: los dientes amarillos pierden amarillo
        teeth = np.full((100, 100, 3), (0.85, 0.75, 0.45), np.float32)
        w = EE.apply_light(teeth, {"strokes": [{"mode": "whiten", "r": 0.3, "s": 1.0, "pts": [[0.5, 0.5]]}]})
        self.assertGreater(float(w[50, 50, 2]) - float(w[50, 50, 0]), float(teeth[50, 50, 2] - teeth[50, 50, 0]))
        dodge = EE.apply_light(teeth * 0.4, {"strokes": [{"mode": "dodge", "r": 0.3, "s": 1.0, "pts": [[0.5, 0.5]]}]})
        self.assertGreater(float(dodge[50, 50].mean()), float((teeth * 0.4)[50, 50].mean()))

    def test_remove_background_of_logos_and_objects(self):
        EE = self.EE
        u = np.full((300, 500, 3), 255, np.uint8)
        cv2.putText(u, "PO", (40, 230), cv2.FONT_HERSHEY_SIMPLEX, 7, (245, 13, 138), 25, cv2.LINE_AA)
        logo = u.astype(np.float32) / 255
        lay = EE.new_layer("background", mode="transparent", detect="color")
        out, a = EE.apply_background(logo, lay)
        self.assertEqual(a[5, 5], 0.0)  # fondo fuera
        self.assertEqual(a[150, 100], 1.0)  # letra dentro
        self.assertEqual(a[150, 270], 0.0)  # el hueco de la O también se quita…
        lay["contiguous"] = True
        self.assertEqual(EE.apply_background(logo, lay)[1][150, 270], 1.0)  # …salvo con «solo el borde»
        edge = (a > 0.05) & (a < 0.95)
        self.assertGreater(edge.sum(), 100)  # borde suave (antialias), no dentado
        self.assertLess(float(out[edge][:, 1].mean()), 0.15)  # y sin halo blanco: magenta puro
        # objeto sobre fondo con textura: GrabCut
        rng = np.random.default_rng(0)
        img = np.clip(np.full((400, 600, 3), (0.3, 0.5, 0.8), np.float32) + rng.normal(0, 0.02, (400, 600, 3)),
                      0, 1).astype(np.float32)
        ref = np.zeros((400, 600), np.uint8)
        cv2.ellipse(img, (300, 200), (140, 110), 0, 0, 360, (0.9, 0.6, 0.2), -1)
        cv2.ellipse(ref, (300, 200), (140, 110), 0, 0, 360, 1, -1)
        a = EE.apply_background(img, EE.new_layer("background", mode="transparent", detect="object"))[1]
        iou = ((a > 0.5) & (ref > 0)).sum() / ((a > 0.5) | (ref > 0)).sum()
        self.assertGreater(iou, 0.95)
        # el logo de la capa Logo también puede perder su fondo
        d = tempfile.mkdtemp()
        p = os.path.join(d, "logo.jpg")
        Image.fromarray(u).save(p, quality=95)
        rgba, _ = EE.logo_rgba((600, 1000, 3), EE.new_layer("logo", path=p, knockout=True, width=50, x=0.5, y=0.5,
                                                            anchor="mm"))
        self.assertLess(float(rgba[300, 270, 3]), 0.1)  # el blanco del JPG es transparente

    def test_original_files_are_used_as_is_and_transparency_is_exported(self):
        EE = self.EE
        d = tempfile.mkdtemp()
        u = np.full((200, 300, 3), 255, np.uint8)
        cv2.circle(u, (150, 100), 60, (20, 40, 200), -1)
        src = os.path.join(d, "logo.png")
        Image.fromarray(u).save(src)
        base = EE.render_base(src, dict(EE.ORIGINAL), full=True)
        self.assertLess(float(np.abs(base * 255 - u).max()), 1.0)  # sin revelar: idéntico al archivo
        doc = EE.new_doc(dict(EE.ORIGINAL))
        EE.add_layer(doc, "background", mode="transparent", detect="color")
        out = EE.export(d, src, doc, os.path.join(d, "Edits", "logo.jpg"), "jpg")
        self.assertTrue(out.endswith(".png"))  # un JPG no guarda transparencia: sale en PNG
        im = cv2.imread(out, cv2.IMREAD_UNCHANGED)
        self.assertEqual(im.shape[2], 4)
        self.assertEqual(im[5, 5, 3], 0)
        self.assertEqual(im[100, 150, 3], 255)

    def test_dropped_image_becomes_an_editable_layer(self):
        EE = self.EE
        d = tempfile.mkdtemp()
        u = np.full((100, 200, 3), 255, np.uint8)
        cv2.rectangle(u, (40, 20), (160, 80), (20, 40, 200), -1)
        src = os.path.join(d, "fuera", "sello.jpg")
        os.makedirs(os.path.dirname(src))
        Image.fromarray(u).save(src, quality=95)
        dst = EE.import_image(d, src)
        self.assertTrue(dst.startswith(os.path.join(d, ".phootflow", "edits", "capas")))
        self.assertEqual(EE.import_image(d, src), dst)  # la misma imagen no se copia dos veces
        doc = EE.new_doc()
        lay = EE.add_layer(doc, "image", path=dst, width=50, x=0.5, y=0.5, anchor="mm")
        self.assertEqual(doc["layers"][-1]["kind"], "image")
        photo = np.zeros((400, 600, 3), np.float32)
        out, _ = EE.compose(photo, doc, [0.0, 0.0, 1.0, 1.0])
        self.assertGreater(float(out[200, 300].mean()), 0.2)  # la imagen está en el centro
        self.assertGreater(float(out[200, 160].mean()), 0.9)  # con su fondo blanco
        lay["cut"] = "color"  # quitar su fondo blanco: deja ver la foto
        out, _ = EE.compose(photo, doc, [0.0, 0.0, 1.0, 1.0])
        self.assertLess(float(out[200, 160].mean()), 0.05)
        self.assertGreater(float(out[200, 300, 2]), 0.6)
        x0, y0, x1, y1 = EE.image_rgba(photo.shape, lay)[1]
        self.assertEqual(x1 - x0, 300)  # 50 % del ancho
        lay["angle"] = 90
        x0, y0, x1, y1 = EE.image_rgba(photo.shape, lay)[1]
        self.assertGreater(y1 - y0, x1 - x0)  # girada en vertical
        lay["path"] = os.path.join(d, "no_existe.png")
        self.assertTrue(np.allclose(EE.compose(photo, doc, [0.0, 0.0, 1.0, 1.0])[0], photo))

    def test_ai_fill_uses_lama_restores_grain_and_falls_back(self):
        EE = self.EE
        rng = np.random.default_rng(3)
        img = np.clip(0.4 + 0.04 * rng.standard_normal((1600, 1600, 3)), 0, 1).astype(np.float32)
        cv2.rectangle(img, (700, 500), (900, 1100), (0.9, 0.2, 0.2), -1)  # «una persona» que borrar
        op = {"tool": "fill", "r": 0.08, "pts": [[0.5, 0.35], [0.5, 0.65]], "ai": True, "src": None}
        calls = []

        def fake(crop, hole):  # LaMa simulado: devuelve el tono medio del entorno, liso
            calls.append(crop.shape)
            return np.broadcast_to(crop[hole < 0.5].mean(0), crop.shape).astype(np.float32).copy()

        ready, inpaint = EE.lama_ready, EE.lama_inpaint
        try:
            EE.lama_ready = lambda: True
            EE.lama_inpaint = fake
            out = EE.apply_retouch(img, {"ops": [op]})
            self.assertTrue(calls)  # con el modelo, el relleno lo hace la IA
            reg = out[600:1000, 760:840]
            self.assertLess(abs(float(reg[..., 0].mean()) - 0.4), 0.03)  # el rojo ha desaparecido
            ref = float((img - cv2.GaussianBlur(img, (0, 0), 1.2))[100:400, 100:400].std())
            got = float((out - cv2.GaussianBlur(out, (0, 0), 1.2))[600:1000, 760:840].std())
            self.assertGreater(got, 0.6 * ref)  # y conserva el grano de la foto, no queda liso
            EE.lama_inpaint = lambda *a: (_ for _ in ()).throw(RuntimeError("sin memoria"))
            out2 = EE.apply_retouch(img, {"ops": [op]})  # si la IA falla, relleno clásico
            self.assertLess(float(out2[600:1000, 760:840, 0].mean()), 0.75)
        finally:
            EE.lama_ready, EE.lama_inpaint = ready, inpaint
        self.assertTrue(EE.LAMA_URL.startswith("https://huggingface.co/") and len(EE.LAMA_SHA256) == 64)

    def test_adjustment_layers_selections_masks_and_blend_modes(self):
        EE = self.EE
        import edits_adjust as A
        rng = np.random.default_rng(7)
        img = np.clip(rng.random((300, 400, 3)), 0, 1).astype(np.float32)
        for k in A.ORDER:  # cada ajuste con sus valores por defecto: no rompe la imagen
            out = A.apply(img, k, A.defaults(k))
            self.assertEqual(out.shape, img.shape, k)
            self.assertTrue(np.isfinite(out).all(), k)
        for k in ("brightness", "levels", "curves", "exposure", "vibrance", "hue_sat", "color_balance"):
            self.assertLess(float(np.abs(A.apply(img, k, A.defaults(k)) - img).max()), 1e-3, k)  # neutros
        # Curvas: spline natural por los puntos (y pasa por ellos)
        lut = A.curve_lut([[0, 0], [0.5, 0.7], [1, 1]])
        self.assertAlmostEqual(float(lut[len(lut) // 2]), 0.7, delta=0.01)
        # Niveles: el punto blanco de entrada a 200 lleva 200/255 a blanco
        p = A.defaults("levels")
        p["levels"]["rgb"] = [0.0, 1.0, 200 / 255, 0.0, 1.0]
        self.assertGreater(float(A.apply(np.full((2, 2, 3), 200 / 255, np.float32), "levels", p).min()), 0.99)
        # Tono/Saturación -100: gris; Blanco y negro: gris
        p = A.defaults("hue_sat")
        p["saturation"] = -100
        self.assertLess(float(np.ptp(A.apply(img, "hue_sat", p), axis=-1).max()), 1e-4)
        self.assertLess(float(np.ptp(A.apply(img, "black_white", A.defaults("black_white")), axis=-1).max()), 1e-6)
        # modos de fusión (W3C): multiplicar con blanco no cambia; trama con negro no cambia
        self.assertTrue(np.allclose(A.blend(img, np.ones_like(img), "multiply"), img))
        self.assertTrue(np.allclose(A.blend(img, np.zeros_like(img), "screen"), img))
        # selecciones: rectángulo, elipse, lazo, restar, invertir y calar
        sel = {"shapes": [{"type": "rect", "box": [0.25, 0.25, 0.75, 0.75], "op": "add"},
                          {"type": "ellipse", "box": [0.4, 0.4, 0.6, 0.6], "op": "sub"}]}
        a = EE.selection_mask((300, 400), sel)
        self.assertEqual(a[100, 120], 1.0)
        self.assertEqual(a[150, 200], 0.0)  # el agujero restado
        self.assertEqual(a[10, 10], 0.0)
        self.assertEqual(EE.selection_mask((300, 400), dict(sel, invert=True))[10, 10], 1.0)
        lasso = EE.selection_mask((300, 400), {"shapes": [{"type": "lasso", "op": "add",
                                                           "pts": [[0.1, 0.1], [0.5, 0.1], [0.3, 0.6]]}]})
        self.assertGreater(lasso[60, 120], 0.9)
        soft = EE.selection_mask((300, 400), dict(sel, feather=0.03))
        self.assertTrue(0.05 < soft[75, 200] < 0.95)  # borde calado
        # capa de ajuste con la selección como máscara: solo cambia dentro
        doc = EE.new_doc()
        lay = EE.add_layer(doc, "adjust", adj="invert")
        self.assertEqual(lay["name"], "Invertir")
        lay["mask"] = {"shapes": [{"type": "rect", "box": [0.0, 0.0, 0.5, 1.0], "op": "add"}]}
        out, _ = EE.compose(img, doc, [0.0, 0.0, 1.0, 1.0])
        self.assertTrue(np.allclose(out[:, 210:], img[:, 210:], atol=1e-5))
        self.assertTrue(np.allclose(out[:, :190], 1 - img[:, :190], atol=1e-5))
        lay.pop("mask")
        lay["blend"] = "luminosity"  # el modo de fusión se aplica a la capa
        out2, _ = EE.compose(img, doc, [0.0, 0.0, 1.0, 1.0])
        self.assertFalse(np.allclose(out2, 1 - img, atol=1e-3))
        # «Rellenar según contenido» de una selección (sin IA aquí: relleno clásico)
        flat = np.full((300, 400, 3), 0.5, np.float32) + rng.normal(0, 0.01, (300, 400, 3)).astype(np.float32)
        cv2.rectangle(flat, (180, 120), (220, 180), (0.9, 0.1, 0.1), -1)
        op = {"tool": "fill", "r": 0.02, "pts": [], "ai": False,
              "sel": {"shapes": [{"type": "rect", "box": [0.43, 0.38, 0.57, 0.62], "op": "add"}]}}
        op["src"] = EE.find_source(flat, op)
        filled = EE.apply_retouch(flat, {"ops": [op]})
        self.assertLess(float(np.abs(filled[130:170, 190:210, 0] - 0.5).mean()), 0.05)

    def test_documents_base_events_and_export_are_non_destructive(self):
        EE = self.EE
        import studio_engine as SE
        d = tempfile.mkdtemp()
        src = os.path.join(d, "a.jpg")
        scene().save(src, quality=93)
        before = open(src, "rb").read()
        doc = EE.new_doc(SE.new_settings(exposure=0.3))
        self.assertFalse(EE.has_edits(doc))
        EE.add_layer(doc, "text", text="Boda")
        lay = EE.add_layer(doc, "retouch")
        lay["ops"].append({"tool": "clone", "r": 0.02, "pts": [[0.5, 0.5]], "src": [0.1, 0.0]})
        self.assertTrue(EE.has_edits(doc))
        EE.save(d, src, doc)
        self.assertTrue(os.path.exists(os.path.join(d, ".phootflow", "edits", "a.jpg.json")))
        self.assertEqual(EE.load(d, src)["layers"][1]["text"], "Boda")
        self.assertEqual(EE.base_settings(d, src, doc)["exposure"], 0.3)
        SE.save(d, src, SE.new_settings(exposure=0.7))  # con revelado de Studio, se parte de él
        self.assertEqual(EE.base_settings(d, src, doc)["exposure"], 0.7)
        for fmt in ("jpg", "tif", "png"):
            out = EE.export(d, src, doc, os.path.join(d, "Edits", "a." + fmt), fmt, 90, 300)
            im = cv2.imread(out, cv2.IMREAD_UNCHANGED)
            self.assertEqual(max(im.shape[:2]), 300, fmt)
            if fmt == "tif":
                self.assertEqual(im.dtype, np.uint16)
        self.assertEqual(open(src, "rb").read(), before)  # el original no se toca
        self.assertEqual(EE.pending_events(d), {})
        doc["_events_override"] = True
        EE.save(d, src, doc)
        self.assertEqual(list(EE.pending_events(d)), [os.path.normcase(src)])
        ph = E.Photo(path=src, name="a")
        ph.keep = True
        out_dir = os.path.join(d, "Editadas")
        self.assertEqual(EE.export_for_events([ph], d, out_dir, ["1080"], 90), 1)
        self.assertTrue(os.path.exists(os.path.join(out_dir, "Sugeridas", "a_edits_1080.jpg")))


class Graphics(unittest.TestCase):
    """Diseño tipo Canva/Illustrator sobre imágenes sintéticas."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])

    def logo(self):
        a = np.full((300, 400, 3), 255, np.uint8)
        cv2.circle(a, (130, 150), 90, (245, 13, 138), -1)
        cv2.rectangle(a, (240, 60), (370, 240), (5, 7, 15), -1)
        cv2.circle(a, (305, 150), 30, (255, 255, 255), -1)  # agujero
        return a

    def test_vectorize_color_bw_and_svg(self):
        import xml.etree.ElementTree as ET
        import graphics_engine as G
        vec = G.vectorize(self.logo(), "color", 3, 70)
        fills = [p["fill"] for p in vec["paths"]]
        self.assertEqual(len(fills), 3)
        self.assertEqual(fills[0], "#FFFFFF")  # el fondo va debajo y lo cubre todo
        bw = G.vectorize(self.logo(), "bw", drop_bg=True)
        self.assertEqual([p["fill"] for p in bw["paths"]], ["#000000"])
        # el trazado rellena lo que era tinta y respeta el agujero (par-impar)
        path = G.parse_path(bw["paths"][0]["d"])
        from PySide6.QtCore import QPointF
        self.assertTrue(path.contains(QPointF(305, 90)))
        self.assertFalse(path.contains(QPointF(305, 150)))
        self.assertFalse(path.contains(QPointF(20, 20)))
        root = ET.fromstring(G.vector_svg(vec).split("?>", 1)[1])
        self.assertEqual(len(root), 3)

    def test_remove_background_keeps_subject(self):
        import graphics_engine as G
        out, used = G.remove_background(self.logo())
        self.assertEqual(used, "color")
        self.assertEqual(out.shape[2], 4)
        self.assertLess(out[10, 10, 3], 10)
        self.assertGreater(out[150, 130, 3], 245)
        self.assertGreater(out[150, 300, 3], 245)
        obj = np.full((240, 320, 3), 0, np.uint8)
        obj[:] = np.random.default_rng(3).integers(0, 90, obj.shape, dtype=np.uint8)
        cv2.circle(obj, (160, 120), 60, (240, 220, 40), -1)
        out, _ = G.remove_background(obj, "object")
        self.assertGreater(out[120, 160, 3], 200)
        self.assertLess(out[10, 10, 3], 60)

    def test_nodes_pathfinder_and_outlines(self):
        import graphics_engine as G
        d = G.design_from_preset("Instagram cuadrado")
        subs = [{"nodes": [{"p": [10, 10], "i": [10, 10], "o": [60, 0]},
                           {"p": [200, 100], "i": [150, 0], "o": [200, 100]},
                           {"p": [50, 200], "i": [50, 200], "o": [50, 200]}], "closed": True}]
        el = G.path_el(d, subs)
        dd = el["paths"][0]["d"]
        self.assertEqual(G.nodes_to_d(G.d_to_nodes(dd)), dd)
        a = G.shape_el(d, "rect", x=0, y=0, w=100, h=100)
        b = G.shape_el(d, "rect", x=50, y=0, w=100, h=100)
        self.assertEqual((G.pathfinder([a, b], "union")["w"], G.pathfinder([a, b], "intersect")["w"]), (150, 50))
        self.assertAlmostEqual(G.pathfinder([a, b], "subtract")["w"], 50, places=3)
        t = G.text_el(d, "Hola", "title")
        o = G.outline_text_el(t)
        self.assertEqual(o["type"], "vector")
        self.assertGreater(len(o["paths"][0]["d"]), 100)

    def test_qr_is_readable_and_text_effects_survive_outlines(self):
        import graphics_engine as G
        from PySide6.QtGui import QImage, QPainter
        d = G.design_from_preset("Instagram cuadrado")
        q = G.qr_el(d, "https://ejemplo.com/galeria")
        d["elements"] = [q]
        with tempfile.TemporaryDirectory() as tmp:
            png = G.export_raster(d, os.path.join(tmp, "qr.png"), G.ImageCache(), 0.5)
            img = cv2.imread(png)
        text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        self.assertEqual(text, "https://ejemplo.com/galeria")
        t = G.text_el(d, "Fiesta", "title", outline="#F50D8A", outline_w=6.0,
                      shadow={"color": "#00000080", "dx": 8, "dy": 8})
        v = G.outline_text_el(t)
        self.assertEqual([p["fill"] for p in v["paths"]], ["#00000080", "#00000000", t["color"]])
        im = QImage(400, 200, QImage.Format_ARGB32)
        im.fill(0)
        pa = QPainter(im)
        G.paint_element(pa, t, G.ImageCache())  # pinta contorno y sombra sin errores
        pa.end()

    def test_pages_curved_text_and_brand(self):
        import re
        import graphics_engine as G
        d = G.design_from_preset("Tarjeta de visita")
        G.apply_template(d, "Tarjeta de visita")
        self.assertEqual(len(d["pages"]), 2)  # anverso y reverso
        front = len(d["elements"])
        G.add_page(d, duplicate_current=True)
        self.assertEqual((len(d["pages"]), d["page"], len(d["elements"])), (3, 1, front))
        self.assertNotEqual(d["elements"][0]["id"], d["pages"][0]["elements"][0]["id"])
        d["elements"] = []  # el código de la interfaz reasigna la lista: sync_page la guarda en su página
        d["bg"] = "#123456"
        G.move_page(d, 1)
        self.assertEqual((d["page"], d["pages"][2]["bg"], d["pages"][2]["elements"]), (2, "#123456", []))
        G.delete_page(d)
        self.assertEqual(len(d["pages"]), 2)
        t = G.text_el(d, "Fotografía de eventos", "subtitle")
        flat = t["h"]
        t["curve"] = 140.0
        self.assertGreater(G.text_height(t), flat * 1.3)  # el arco ocupa más alto que la línea recta
        self.assertEqual(G.outline_text_el(t)["type"], "vector")
        d["elements"].append(t)
        n = G.apply_brand(d, {"title_font": "Georgia", "body_font": "Arial"})
        self.assertGreater(n, 3)
        self.assertEqual(t["font"], "Arial" if t["size"] < 0.06 * min(d["w"], d["h"]) else "Georgia")
        with tempfile.TemporaryDirectory() as tmp:
            back = G.load(G.save(d, tmp))
            self.assertEqual([len(pg["elements"]) for pg in back["pages"]],
                             [len(pg["elements"]) for pg in d["pages"]])
            self.assertIs(back["elements"], back["pages"][back["page"]]["elements"])
            pdf = G.export_pdf(d, os.path.join(tmp, "t.pdf"), G.ImageCache())
            with open(pdf, "rb") as f:
                self.assertEqual(len(re.findall(rb"/Type\s*/Page[^s]", f.read())), 2)

    def test_snap_align_and_save(self):
        import graphics_engine as G
        d = G.design_from_preset("Instagram cuadrado")
        e = G.shape_el(d, "rect", x=3, y=500, w=100, h=100)
        dx, dy, gx, gy = G.snap(G.bbox(e), *G.snap_lines(d, []), 6)
        self.assertEqual((dx, gx), (-3, [0.0]))
        G.align(d, [e], "hcenter")
        self.assertAlmostEqual(e["x"] + 50, 540)
        d["elements"].append(e)
        with tempfile.TemporaryDirectory() as tmp:
            G.save(d, tmp)
            (path, back), = G.list_designs(tmp)
            self.assertEqual(back["elements"][0]["x"], e["x"])

    def test_print_pdf_bleed_marks_cmyk_and_raster(self):
        import re
        import graphics_engine as G
        with tempfile.TemporaryDirectory() as tmp:
            scene().save(os.path.join(tmp, "f.jpg"))
            d = G.design_from_preset("A5 (flyer)")
            for name in G.TEMPLATES:
                G.apply_template(d, name)  # todas las plantillas se montan sin errores
            G.apply_template(d, "Flyer de servicios")
            ph = next(e for e in d["elements"] if e["type"] == "image")
            ph["path"] = os.path.join(tmp, "f.jpg")
            cache = G.ImageCache()
            pdf = os.path.join(tmp, "a.pdf")
            G.export_pdf(d, pdf, cache, 3.0, True, True)
            with open(pdf, "rb") as f:
                data = f.read()
            self.assertIn(b"DeviceCMYK", data)
            box = [float(x) for x in re.search(rb"/MediaBox \[([\d. ]+)\]", data).group(1).split()]
            # A5 + 2 × (3 mm de sangrado + 8 mm para las marcas), en puntos
            self.assertAlmostEqual(box[2], (148 + 22) / 25.4 * 72, delta=1.0)
            self.assertAlmostEqual(box[3], (210 + 22) / 25.4 * 72, delta=1.0)
            # la foto toca el borde: se alarga hasta el sangrado
            ext = next(e for e in G.bleed_elements(d, 30) if e["type"] == "image")
            self.assertEqual((ext["x"], ext["y"]), (-30, -30))
            png = G.export_raster(d, os.path.join(tmp, "a.png"), cache, 0.25)
            with Image.open(png) as im:
                self.assertEqual(im.size, (round(d["w"] * 0.25), round(d["h"] * 0.25)))
            self.assertGreater(os.path.getsize(G.export_svg(d, os.path.join(tmp, "a.svg"), cache)), 1000)
            # 800 px de ancho para ~148 mm: se avisa de que se imprimiría a menos de 200 ppp
            warn = G.image_warnings(d, cache)
            self.assertEqual(len(warn), 1)
            self.assertTrue(warn[0].startswith("f.jpg: "))


class Updater(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(updater.is_newer("v0.10.0", "0.9.9"))
        self.assertFalse(updater.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(updater.is_newer("0.1.0", "0.2.0"))

    def test_setup_mode(self):
        from unittest import mock
        d = tempfile.mkdtemp()
        exe = os.path.join(d, "PhootFlow.exe")
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", exe), \
                mock.patch.object(os, "name", "nt"):
            self.assertEqual(updater.mode(), "exe")
            open(os.path.join(d, "unins000.exe"), "wb").close()
            self.assertEqual(updater.mode(), "setup")
            self.assertTrue(updater.can_self_update())

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


TEST_SEED = bytes(range(32))  # clave de firma solo para las pruebas


class FakeLicencias(http.server.BaseHTTPRequestHandler):
    """Imita la Edge Function «licencias» (servidor_licencias/functions/licencias/index.ts)."""
    db = {}       # clave -> {estado, titular, max_equipos, dias_offline, caduca, equipos: {huella: nombre}}
    seed = TEST_SEED
    down = False

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if FakeLicencias.down:
            return self._json(503, {"error": "caido"})
        clave, huella = body.get("clave", "").upper(), body.get("huella", "")
        lic = self.db.get(clave)
        if not lic:
            return self._json(404, {"error": "clave_no_valida"})
        now = int(time.time())
        estado = ("revocada" if lic["estado"] == "revocada"
                  else "caducada" if lic.get("caduca") and lic["caduca"] <= now else "activa")
        eqs = lic.setdefault("equipos", {})
        if huella not in eqs and estado == "activa":
            if len(eqs) >= lic.get("max_equipos", 1):
                return self._json(409, {"error": "limite_equipos", "equipos": list(eqs.values())})
            eqs[huella] = body.get("nombre", "")
        hasta = now + lic.get("dias_offline", 7) * 86400
        if lic.get("caduca"):
            hasta = min(hasta, lic["caduca"])
        tok = LIC.make_token(self.seed, {"v": 1, "clave": clave, "huella": huella, "estado": estado,
                                         "titular": lic.get("titular", ""), "emitido": now, "hasta": hasta,
                                         "caduca": lic.get("caduca")})
        self._json(200, {"token": tok, "estado": estado})

    def log_message(self, *a):
        pass


class Licencias(unittest.TestCase):
    KEY = "PF-AAAAA-BBBBB-CCCCC-DDDDD"

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeLicencias)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.saved = (LIC.PUBLIC_KEY, LIC.STORE, os.environ.get("PHOOTFLOW_LICENCIAS_URL"))
        LIC.PUBLIC_KEY = LIC.ed25519_public(TEST_SEED).hex()
        os.environ["PHOOTFLOW_LICENCIAS_URL"] = f"http://127.0.0.1:{cls.srv.server_port}/"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        LIC.PUBLIC_KEY, LIC.STORE, url = cls.saved
        if url is None:
            os.environ.pop("PHOOTFLOW_LICENCIAS_URL", None)
        else:
            os.environ["PHOOTFLOW_LICENCIAS_URL"] = url

    def setUp(self):
        LIC.STORE = os.path.join(tempfile.mkdtemp(), "licencia.json")
        FakeLicencias.db = {self.KEY: {"estado": "activa", "titular": "Ana"}}
        FakeLicencias.seed, FakeLicencias.down = TEST_SEED, False

    def test_ed25519_rfc8032(self):
        sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        pk = LIC.ed25519_public(sk)
        self.assertEqual(pk.hex(), "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = LIC.ed25519_sign(sk, b"")
        self.assertEqual(sig.hex()[:32], "e5564300c360ac729086e2cc806e828a")
        self.assertTrue(LIC.ed25519_verify(pk, b"", sig))
        self.assertFalse(LIC.ed25519_verify(pk, b"x", sig))

    def test_activate_revoke_reactivate_and_offline(self):
        lic = FakeLicencias.db[self.KEY]
        self.assertEqual(LIC.refresh().codigo, "sin_licencia")
        with self.assertRaises(LIC.ErrorLicencia):
            LIC.activate("PF-NO-EXISTE")
        st = LIC.activate(" pf-aaaaa-bbbbb-ccccc-ddddd ")
        self.assertTrue(st.ok and st.en_linea)
        self.assertEqual(st.titular, "Ana")
        self.assertIn(LIC.fingerprint(), lic["equipos"])
        # sin conexión vale el token hasta «hasta», ni un segundo más; y un reloj atrasado no cuela
        FakeLicencias.down = True
        st = LIC.refresh()
        self.assertTrue(st.ok)
        self.assertFalse(st.en_linea)
        self.assertEqual(LIC.evaluate(ahora=time.time() + 8 * 86400).codigo, "vencida")
        self.assertEqual(LIC.evaluate(ahora=time.time() - 3 * 86400).codigo, "reloj")
        FakeLicencias.down = False
        # revocar: deja de valer, también sin conexión después
        lic["estado"] = "revocada"
        self.assertEqual(LIC.refresh().codigo, "revocada")
        FakeLicencias.down = True
        self.assertEqual(LIC.refresh().codigo, "revocada")
        FakeLicencias.down = False
        lic["estado"] = "activa"
        self.assertTrue(LIC.refresh().ok)
        # caducada
        lic["caduca"] = int(time.time()) - 10
        self.assertEqual(LIC.refresh().codigo, "caducada")
        # clave borrada en el servidor
        del FakeLicencias.db[self.KEY]
        self.assertEqual(LIC.refresh().codigo, "clave_no_valida")
        self.assertEqual(LIC.evaluate().codigo, "clave_no_valida")

    def test_one_machine_tampering_and_foreign_signatures(self):
        lic = FakeLicencias.db[self.KEY]
        lic["equipos"] = {"f" * 32: "Otro PC"}
        with self.assertRaisesRegex(LIC.ErrorLicencia, "otro equipo"):
            LIC.activate(self.KEY)
        lic["equipos"] = {}
        self.assertTrue(LIC.activate(self.KEY).ok)
        # editar el token a mano rompe la firma
        d = LIC._load()
        p, s = d["token"].split(".")
        data = json.loads(LIC._b64d(p))
        data["hasta"] += 365 * 86400
        d["token"] = LIC._b64e(json.dumps(data).encode()) + "." + s
        self.assertEqual(LIC.evaluate(d).codigo, "sin_licencia")
        # copiar licencia.json a otro equipo no sirve
        d = LIC._load()
        real = LIC.fingerprint
        LIC.fingerprint = lambda: "0" * 32
        try:
            self.assertEqual(LIC.evaluate(d).codigo, "otro_equipo")
        finally:
            LIC.fingerprint = real
        # un servidor que firma con otra clave no puede dar licencias
        FakeLicencias.seed = bytes(32)
        with self.assertRaises(LIC.ErrorLicencia):
            LIC.activate(self.KEY)

    def test_admin_tool(self):
        import contextlib
        import io
        calls = []

        class Resp:
            def __init__(self, obj):
                self.raw = json.dumps(obj).encode()

            def read(self):
                return self.raw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        row = {"id": "u1", "clave": "PF-X", "titular": "Ana", "estado": "activa", "max_equipos": 1,
               "dias_offline": 7, "caduca": None, "email": "", "notas": "", "creada": "2026-11-16T10:00:00+00:00",
               "equipos": [{"id": "e1", "huella": "abcdef0123", "nombre": "PORTATIL", "version": "0.6.0",
                            "primera": None, "ultima": None}]}

        def opener(req, timeout=0):
            body = json.loads(req.data) if req.data else None
            calls.append((req.get_method(), req.full_url, body, dict(req.header_items())))
            if req.get_method() in ("POST", "PATCH"):
                return Resp([{**row, **body}])
            return Resp([] if req.get_method() == "DELETE" else [row])

        adm = admin_licencias.Admin("https://x.supabase.co", "sb_secret_prueba", opener)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            admin_licencias.main(["crear", "Luis", "--email", "l@x.es"], adm)
            admin_licencias.main(["revocar", "pf-x"], adm)
            admin_licencias.main(["liberar", "PF-X", "portatil"], adm)
            admin_licencias.main(["listar"], adm)
        m, url, body, hdr = calls[0]
        self.assertEqual((m, url), ("POST", "https://x.supabase.co/rest/v1/licencias"))
        self.assertRegex(body["clave"], r"^PF(-[0-9A-HJKMNP-TV-Z]{5}){4}$")
        self.assertEqual((body["titular"], body["max_equipos"], body["dias_offline"]), ("Luis", 1, 7))
        self.assertEqual(hdr.get("Apikey"), "sb_secret_prueba")
        self.assertNotIn("Authorization", hdr)
        patch = [c for c in calls if c[0] == "PATCH"][0]
        self.assertTrue(patch[1].endswith("licencias?id=eq.u1"))
        self.assertEqual(patch[2]["estado"], "revocada")
        self.assertTrue([c for c in calls if c[0] == "DELETE"][0][1].endswith("equipos?id=eq.e1"))
        self.assertIn(body["clave"], out.getvalue())
        self.assertIn("PORTATIL", out.getvalue())


if __name__ == "__main__":
    unittest.main()
