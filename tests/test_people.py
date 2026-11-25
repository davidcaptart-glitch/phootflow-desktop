"""Personas de Events: agrupado, personas.json, personas conocidas entre sesiones y copia a «Personas/», con
vectores de cara simulados (nada de fotos reales)."""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine as E  # noqa: E402
import people as P  # noqa: E402

RNG = np.random.default_rng(7)
IDS = P._unit(RNG.normal(size=(6, 128)).astype(np.float32))  # 6 identidades casi ortogonales


def face(ident: int, noise: float = 0.55, rng=RNG) -> np.ndarray:
    """Una cara de la identidad `ident`: su vector más ruido (coseno con el centro ≈ 0,7-0,8, como SFace)."""
    v = IDS[ident] + noise * P._unit(rng.normal(size=128).astype(np.float32))
    return P._unit(v)


def photo(name: str, idents: list, px: float = 80.0, blur: float = 200.0, rng=RNG) -> E.Photo:
    ph = E.Photo(path=os.path.join("C:\\sesion", name + ".ARW"), name=name, thumb=b"x")
    if idents:
        embs = np.array([face(i, rng=rng) for i in idents])
        boxes = [[0.1 * j, 0.1, 0.1 * j + 0.08, 0.2, px, 0.95, blur] for j in range(len(idents))]
        ph.people = {"v": 1, "b": boxes, "e": P.encode(embs)}
    else:
        ph.people = {"v": 1, "b": [], "e": ""}
    return ph


def session(spec: dict, seed: int = 1) -> list:
    rng = np.random.default_rng(seed)
    return [photo(n, ids, rng=rng) for n, ids in spec.items()]


SPEC = {f"F{i:03d}": ids for i, ids in enumerate(
    [[0], [0, 1], [1], [0], [2], [2, 0], [1], [0, 1, 2], [3], [0], [1], [2]])}


def groups_by_ident(pp: P.People, photos) -> dict:
    """identidad real -> personas en que acabaron sus caras."""
    out: dict = {}
    for ph in photos:
        ids = SPEC.get(ph.name) or []
        for i, ident in enumerate(ids):
            pid = pp.assign.get(P.face_key(os.path.basename(ph.path), i))
            if pid:
                out.setdefault(ident, set()).add(pid)
    return out


class PeopleTest(unittest.TestCase):
    def test_encode_decode(self):
        a = P._unit(RNG.normal(size=(3, 128)).astype(np.float32))
        b = P.decode(P.encode(a), 3)
        self.assertEqual(b.shape, (3, 128))
        self.assertGreater(float(np.min(np.sum(a * b, axis=1))), 0.999)

    def test_cluster_one_group_per_person(self):
        photos = session(SPEC)
        pp = P.People().build(photos)
        g = groups_by_ident(pp, photos)
        for ident in (0, 1, 2):
            self.assertEqual(len(g[ident]), 1, f"identidad {ident} partida en {g[ident]}")
        self.assertEqual(len({next(iter(g[i])) for i in (0, 1, 2)}), 3)  # y sin mezclar personas
        shown = pp.groups()
        self.assertEqual(len(shown), 3)  # la 3 sale en una sola foto: no se enseña
        self.assertEqual(shown[0], next(iter(g[0])))  # la que más sale, primero
        self.assertEqual(pp.photos_of(shown[0]), {f"F{i:03d}.ARW" for i, ids in enumerate(SPEC.values())
                                                  if 0 in ids})

    def test_lookalikes_not_merged(self):
        """Dos personas parecidas (hermanos): sus centros se parecen más que sus caras; no deben unirse."""
        rng = np.random.default_rng(3)
        base = P._unit(rng.normal(size=128).astype(np.float32))
        A = P._unit(base + 1.0 * P._unit(rng.normal(size=128).astype(np.float32)))
        B = P._unit(base + 1.0 * P._unit(rng.normal(size=128).astype(np.float32)))
        photos = []
        for i in range(6):
            ph = E.Photo(path=f"C:\\s\\H{i}.ARW", name=f"H{i}", thumb=b"x")
            embs = np.array([P._unit(v + 0.65 * P._unit(rng.normal(size=128).astype(np.float32))) for v in (A, B)])
            ph.people = {"v": 1, "b": [[0, 0, .1, .1, 90, .95, 200], [.5, 0, .6, .1, 90, .95, 200]],
                         "e": P.encode(embs)}
            photos.append(ph)
        pp = P.People().build(photos)
        a = {pp.assign[f"H{i}.ARW#0"] for i in range(6)}
        b = {pp.assign[f"H{i}.ARW#1"] for i in range(6)}
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertNotEqual(a, b)

    def test_tiny_and_blurry_faces_ignored(self):
        photos = session(SPEC)
        photos.append(photo("Diminuta", [0], px=20))
        photos.append(photo("Borrosa", [0], blur=5))
        pp = P.People().build(photos)
        self.assertNotIn("Diminuta.ARW#0", pp.assign)
        self.assertNotIn("Borrosa.ARW#0", pp.assign)
        self.assertFalse(pp.faces["Diminuta.ARW#0"]["ok"])

    def test_match_filter_any_and_together(self):
        photos = session(SPEC)
        pp = P.People().build(photos)
        g = groups_by_ident(pp, photos)
        a, b = next(iter(g[0])), next(iter(g[1]))
        self.assertTrue(pp.match("F001.ARW", [a, b], together=True))
        self.assertFalse(pp.match("F000.ARW", [a, b], together=True))
        self.assertTrue(pp.match("F000.ARW", [a, b]))
        self.assertFalse(pp.match("F004.ARW", [a, b]))
        self.assertTrue(pp.match("F004.ARW", []))

    def test_manual_changes_persist(self):
        photos = session(SPEC)
        pp = P.People().build(photos)
        g = groups_by_ident(pp, photos)
        a, b, c = (next(iter(g[i])) for i in (0, 1, 2))
        pp.rename(a, "Novia")
        pp.merge(b, c)  # dos personas unidas a mano
        out = pp.members(a)[0]
        pp.remove_face(out)
        with tempfile.TemporaryDirectory() as d:
            pp.save(d)
            again = P.People().build(photos, P.load(d))
        self.assertEqual(again.names[a], "Novia")
        self.assertEqual(again.assign[out], "")  # la cara sacada no vuelve a entrar
        self.assertEqual(again.photos_of(b), pp.photos_of(b))
        self.assertNotIn(c, again.groups())
        # una foto nueva de la novia va a la persona que ya existe
        photos.append(photo("Nueva", [0], rng=np.random.default_rng(99)))
        with tempfile.TemporaryDirectory() as d:
            pp.save(d)
            more = P.People().build(photos, P.load(d))
        self.assertEqual(more.assign["Nueva.ARW#0"], a)
        # reagrupar conserva las personas con nombre
        reg = P.People().build(photos, more.to_json(), regroup=True)
        self.assertEqual(reg.names.get(a), "Novia")
        self.assertEqual(reg.photos_of(a), more.photos_of(a))

    def test_known_people_across_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "conocidas.json")
            s1 = session(SPEC, seed=1)
            k1 = P.Known(lib)
            pp = P.People().build(s1, library=k1)
            g = groups_by_ident(pp, s1)
            a, b = next(iter(g[0])), next(iter(g[1]))
            pp.rename(a, "Lucía")
            pp.rename(b, "Marcos")
            self.assertTrue(k1.sync(P.session_key("C:\\s1\\Editadas"), pp))
            k1.save()
            # otra sesión, otras fotos de las mismas personas: se reconocen solas
            spec2 = {f"G{i:02d}": ids for i, ids in enumerate([[0], [0], [1, 0], [1], [4], [4]])}
            s2 = session(spec2, seed=5)
            k2 = P.Known(lib)
            self.assertEqual(k2.names(), ["Lucía", "Marcos"])
            pp2 = P.People().build(s2, library=k2)
            names = {pp2.names.get(p) for p in pp2.groups()}
            self.assertIn("Lucía", names)
            self.assertIn("Marcos", names)
            unknown = [p for p in pp2.groups() if p not in pp2.names]
            self.assertEqual(len(unknown), 1)  # la identidad 4 no es nadie conocido
            lucia = next(p for p in pp2.groups() if pp2.names.get(p) == "Lucía")
            self.assertIn(lucia, pp2.auto)  # reconocida, sin confirmar: no se aprende todavía
            self.assertNotIn(lucia, pp2.confirmed())
            k2.sync(P.session_key("C:\\s2\\Editadas"), pp2)
            self.assertEqual(len(k2.people["Lucía"]), 1)
            # «No es Lucía»: se quita y no vuelve al reagrupar con lo guardado
            pp2.reject(lucia)
            again = P.People().build(s2, pp2.to_json(), library=k2)
            self.assertNotEqual(again.names.get(lucia), "Lucía")
            # aceptar: desde ahora también se aprende de esta sesión
            marcos = next(p for p in pp2.groups() if pp2.names.get(p) == "Marcos")
            pp2.accept(marcos)
            k2.sync(P.session_key("C:\\s2\\Editadas"), pp2)
            self.assertEqual(len(k2.people["Marcos"]), 2)
            # quitar el nombre en la sesión 1 olvida lo aprendido de ella
            pp.rename(a, "")
            k2.sync(P.session_key("C:\\s1\\Editadas"), pp)
            self.assertNotIn("Lucía", k2.people)
            k2.forget("Marcos")
            self.assertEqual(k2.names(), [])

    def test_ambiguous_known_not_named(self):
        with tempfile.TemporaryDirectory() as d:
            k = P.Known(os.path.join(d, "c.json"))
            self.assertEqual(k.match(IDS[0]), ("", 0.0))
            k.people = {"A": {"s": P.encode(IDS[[0]])}, "B": {"s": P.encode(IDS[[0]])}}
            k._index()
            self.assertEqual(k.match(IDS[0])[0], "")  # dos iguales: no se elige

    def test_folder_name(self):
        self.assertEqual(P.folder_name('Ana: "la novia"?'), "Ana_ _la novia__")
        self.assertEqual(P.folder_name("CON"), "CON_")
        self.assertEqual(P.folder_name(" ... "), "Persona")

    def test_copy_to_folders_reuses_delivery(self):
        with tempfile.TemporaryDirectory() as out:
            sizes = ["max", "1080"]
            for stem, bw in (("A", False), ("A", True), ("B", False)):
                p = E.delivery_path(out, stem, "max", bw, sizes)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(stem.encode() * 10)
            folders = {"Novia": ["A", "B"], "Novio": ["A"], "Nadie": ["C"]}
            n = P.copy_to_folders(out, folders, sizes, E.delivery_path, E.delivery_size)
            self.assertEqual(n, 5)  # A en color y B/N para cada uno, y B
            base = os.path.join(out, P.FOLDER)
            self.assertTrue(os.path.exists(os.path.join(base, "Novia", "A.jpg")))
            self.assertTrue(os.path.exists(os.path.join(base, "Novia", "Blanco y negro", "A.jpg")))
            self.assertTrue(os.path.exists(os.path.join(base, "Novio", "A.jpg")))
            self.assertFalse(os.path.exists(os.path.join(base, "Nadie")))
            self.assertEqual(P.copy_to_folders(out, folders, sizes, E.delivery_path, E.delivery_size), 0)

    def test_cache_roundtrip(self):
        """`Photo.people` viaja en analisis.json (save_session / load_session)."""
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "x.jpg")
            with open(src, "wb") as f:
                f.write(b"jpg")
            ph = E.Photo(path=src, name="x", thumb=b"t")
            ph.people = {"v": 1, "b": [[0, 0, 1, 1, 80, 0.9, 100]], "e": P.encode(IDS[[2]])}
            st = E.Settings()
            E.save_session([ph], st, d)
            back = E.Photo(path=src, name="x")
            self.assertEqual(E.load_session([back], st, d), 1)
            self.assertEqual(back.people["b"], ph.people["b"])

    def test_describe_without_model(self):
        self.assertEqual(P.describe(False, np.zeros((10, 10, 3)), [np.zeros(15)])["b"], [])
        self.assertEqual(E.face_ids(np.zeros((10, 10, 3)), []), {})


if __name__ == "__main__":
    unittest.main()
