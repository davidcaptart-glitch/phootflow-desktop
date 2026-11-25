"""Panel «¿Cómo quieres editar esta sesión?»: tipos de sesión, propuestas y reglas tras el análisis (sin Qt)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bokeh as BK  # noqa: E402
import engine as E  # noqa: E402
import session_presets as SP  # noqa: E402
import session_setup as SS  # noqa: E402
import smartcrop as SC  # noqa: E402
import styles as S  # noqa: E402


def photo(**kw):
    p = E.Photo(path=kw.pop("path", "x.jpg"))
    p.crops = {SC.ORIGINAL: (0, 0, 1, 1), "4:5": (0.1, 0, 0.9, 1)}
    p.best = "4:5"
    for k, v in kw.items():
        setattr(p, k, v)
    return p


class TestTypes(unittest.TestCase):
    def test_types_use_real_values(self):
        for t in SS.TYPES:
            v = SS.propose(t.key)
            self.assertTrue(v["style"] == S.AUTO or v["style"] in S.PRESETS, t.key)
            self.assertIn(v["look"], E.COLOR_LOOKS, t.key)
            self.assertIn(v["bw_style"], E.BW_STYLES, t.key)
            self.assertIn(v["nr"], E.NR_LEVELS, t.key)
            self.assertIn(v["sens"], E.SENS, t.key)
            self.assertTrue(v["bokeh"] == "" or v["bokeh"] in BK.LEVELS, t.key)
            self.assertTrue(all(r in E.RATIOS for r in v["ratios"]), t.key)
            self.assertTrue(all(s in E.SIZES for s in v["sizes"]), t.key)
            # todas las claves se guardan en los perfiles («portrait» llega con «Retocar retratos»)
            self.assertEqual(set(v) - set(SP.KEYS) - {"portrait"}, set(), t.key)

    def test_propose_keeps_what_it_does_not_decide(self):
        base = {"cloud": 1, "cloud_folder": "x", "quality": 88, "workers": 2, "cam_prof": "Sin perfil (LibRaw)"}
        v = SS.propose("boda", base)
        for k, val in base.items():
            self.assertEqual(v[k], val)
        self.assertTrue(v["bw"] and v["portrait"])
        self.assertEqual(SS.propose("deporte", v)["bw"], False)  # cambiar de tipo rehace la propuesta

    def test_type_of(self):
        self.assertEqual(SS.type_of({"style": "Deporte"}), "deporte")
        self.assertEqual(SS.type_of({"style": S.AUTO}), SS.AUTO)
        self.assertEqual(SS.type_of({"style": "Mi estilo: X"}, {"Mi estilo: X": {"_base": "Boda"}}), "boda")
        self.assertEqual(SS.type_of({}), SS.AUTO)

    def test_summary(self):
        s = SS.summary(SS.propose("boda"))
        self.assertIn("recorte inteligente", s)
        self.assertIn("retratos retocados", s)
        self.assertIn("B/N", s)
        self.assertIn("sin recorte", SS.summary({"crop": False}))

    def test_keys_saved_in_presets(self):
        self.assertIn("crop", SP.KEYS)
        self.assertIn("enhance", SP.KEYS)


class TestRules(unittest.TestCase):
    def test_no_smart_crop_delivers_original(self):
        a, b = photo(), photo(reviewed=True)
        c = photo()
        c.best = E.CUSTOM_CROP
        c.crops[E.CUSTOM_CROP] = (0, 0, 0.5, 0.5)
        n, _ = SS.apply_rules([a, b, c], smart_crop=False)
        self.assertEqual((n, a.best, b.best, c.best), (1, SC.ORIGINAL, "4:5", E.CUSTOM_CROP))

    def test_smart_crop_on_changes_nothing(self):
        a = photo()
        self.assertEqual(SS.apply_rules([a], smart_crop=True), (0, 0))
        self.assertEqual(a.best, "4:5")

    @unittest.skipUnless(hasattr(E, "suggested_enhance"), "sin «Mejorar con IA»")
    def test_auto_enhance_only_suggested_kept(self):
        want = photo(keep=True, fix=[E.FIX_FACES])
        nofix = photo(keep=True, fix=[])
        gone = photo(keep=False, fix=[E.FIX_DETAIL])
        seen = photo(keep=True, fix=[E.FIX_DETAIL], reviewed=True)
        _, n = SS.apply_rules([want, nofix, gone, seen], auto_enhance=True)
        self.assertEqual(n, 1)
        self.assertTrue(want.enhance and want.enhance.get("faces"))
        self.assertEqual((nofix.enhance, gone.enhance, seen.enhance), ({}, {}, {}))
        self.assertEqual(SS.apply_rules([photo(keep=True, fix=[E.FIX_LIGHT])], auto_enhance=False), (0, 0))

    def test_settings_defaults(self):
        st = E.Settings()
        self.assertTrue(st.smart_crop)
        self.assertFalse(st.auto_enhance)


if __name__ == "__main__":
    unittest.main()
