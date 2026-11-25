"""Presets de Studio: importar de Lightroom (.xmp, .lrtemplate) y de PhootFlow, renombrar, borrar, exportar."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio_engine as SE  # noqa: E402

XMP_ATTR = """<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
 <rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   crs:PresetType="Normal" crs:Exposure2012="+0.50" crs:Contrast2012="+25" crs:Highlights2012="-60"
   crs:Shadows2012="+40" crs:Vibrance="+15" crs:HueAdjustmentOrange="-8" crs:SaturationAdjustmentBlue="-30"
   crs:LuminanceAdjustmentOrange="+12" crs:ColorGradeShadowHue="210" crs:ColorGradeShadowSat="20"
   crs:SplitToningShadowHue="40" crs:PostCropVignetteAmount="-15" crs:GrainAmount="400"
   crs:Temperature="5600" crs:ConvertToGrayscale="False">
  <crs:Name><rdf:Alt><rdf:li xml:lang="x-default">Boda cálida</rdf:li></rdf:Alt></crs:Name>
 </rdf:Description></rdf:RDF></x:xmpmeta>"""

XMP_ELEM = """<rdf:Description xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/">
 <crs:Clarity2012>+20</crs:Clarity2012><crs:ConvertToGrayscale>True</crs:ConvertToGrayscale>
</rdf:Description>"""

LRTEMPLATE = """s = { id = "X", internalName = "Mate", title = "Mate antiguo", type = "Develop",
 value = { settings = { Blacks2012 = 20, Dehaze = -10, ParametricShadows = 15, }, uuid = "X", }, version = 0, }"""


class StudioPresetsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = tempfile.mkdtemp()
        self.src = tempfile.mkdtemp()

    def _file(self, name, text):
        p = os.path.join(self.src, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_lightroom_xmp(self):
        s, name, ignored = SE.lightroom_to_settings(XMP_ATTR)
        self.assertEqual(name, "Boda cálida")
        self.assertEqual(s["exposure"], 0.5)
        self.assertEqual((s["contrast"], s["highlights"], s["shadows"], s["vibrance"]), (25, -60, 40, 15))
        self.assertEqual((s["hsl_orange_h"], s["hsl_blue_s"], s["hsl_orange_l"]), (-8, -30, 12))
        self.assertEqual(s["grade_sh_hue"], 210)  # manda la gradación nueva sobre «Dividir tonos»
        self.assertEqual(s["vignette"], -15)
        self.assertEqual(s["grain"], 100)  # recortado al rango de Studio
        self.assertIs(s["bw"], False)
        self.assertNotIn("temp", s)  # la temperatura en kelvin no tiene equivalente
        self.assertGreaterEqual(ignored, 1)

    def test_xmp_elements_and_lrtemplate(self):
        s, _, _ = SE.lightroom_to_settings(XMP_ELEM)
        self.assertEqual(s, {"clarity": 20, "bw": True})
        s, name, _ = SE.lightroom_to_settings(LRTEMPLATE)
        self.assertEqual(name, "Mate antiguo")
        self.assertEqual(s, {"blacks": 20, "dehaze": -10, "curve_shadows": 15})

    def test_import_rename_delete_export(self):
        n1, _ = SE.import_preset_file(self.cfg, self._file("a.xmp", XMP_ATTR))
        n2, _ = SE.import_preset_file(self.cfg, self._file("b.xmp", XMP_ATTR))
        self.assertEqual((n1, n2), ("Boda cálida", "Boda cálida (2)"))  # no pisa el que ya hay
        ps = SE.list_presets(self.cfg)
        self.assertEqual(ps["Boda cálida"]["contrast"], 25)
        self.assertNotIn("sharpen", ps["Boda cálida"])  # solo lo que trae el preset
        new = SE.rename_preset(self.cfg, "Boda cálida (2)", "Otra")
        self.assertEqual(new, "Otra")
        dest = os.path.join(self.src, "out.json")
        SE.export_preset(self.cfg, "Otra", dest)
        n3, _ = SE.import_preset_file(self.cfg, dest)
        self.assertEqual(n3, "out")
        SE.delete_preset(self.cfg, "Otra")
        self.assertEqual(sorted(SE.list_presets(self.cfg)), ["Boda cálida", "out"])
        with self.assertRaises(ValueError):
            SE.import_preset_file(self.cfg, self._file("x.json", json.dumps({"hola": 1})))
        with self.assertRaises(ValueError):
            SE.import_preset_file(self.cfg, self._file("y.xmp", "<x/>"))


if __name__ == "__main__":
    unittest.main()
