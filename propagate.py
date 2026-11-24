"""Propagar la edición de una foto (Studio y/o Edits) a otras fotos de la sesión de Events.

Se copia «qué» se quiere (grupos de ajustes de Studio y capas de Edits) y se aplica foto a foto o a varias.
Lo que depende de la posición en la foto (retoques puntuales, licuar) no se copia: no tendría sentido en otra.
"""
from __future__ import annotations

import copy
import os
import uuid

import studio_engine as SE


def _panel_keys(*names) -> list[str]:
    return [c[0] for t, ctrls in SE.PANELS if t in names for c in ctrls]


_LIGHT = [k for k in _panel_keys("Básico", "Presencia", "Curva tonal")
          if k not in ("exposure", "temp", "tint", "vibrance", "saturation")]
STUDIO_GROUPS: list[tuple[str, str, list[str], bool]] = [
    ("light", "Luz: contraste, luces, sombras, presencia y curva", _LIGHT, True),
    ("exposure", "Exposición", ["exposure"], False),
    ("color", "Color: temperatura, matiz, intensidad, HSL y gradación",
     ["temp", "tint", "vibrance", "saturation"] + [k for k in SE.DEFAULTS if k.startswith("hsl_")]
     + _panel_keys("Gradación de color"), True),
    ("detail", "Detalle: nitidez y ruido", _panel_keys("Detalle"), True),
    ("effects", "Efectos: viñeta y grano", _panel_keys("Efectos"), True),
    ("profile", "Perfil, LUT y blanco y negro", ["profile", "lut", "lut_strength", "bw", "bw_style"], True),
    ("splash", "Color selectivo (un color se queda, el resto en B/N)",
     [k for k in SE.DEFAULTS if k.startswith("splash")], False),
    ("masks", "Máscaras (las de IA se recalculan en cada foto)", ["masks"], False),
    ("geometry", "Recorte y enderezado", ["crop", "crop_ratio", "angle"], False),
]
# capas de Edits que valen para otra foto (las de IA se recalculan; texto y logo se colocan igual)
EDITS_COPYABLE = ("adjust", "skin", "light", "portrait", "background", "text", "logo", "image")


def studio_patch(settings: dict, groups) -> dict:
    keys = [k for gid, _, ks, _ in STUDIO_GROUPS if gid in groups for k in ks]
    return {k: copy.deepcopy(settings[k]) for k in keys if k in settings}


class Propagator:
    """`studio_init(ph)` = ajustes de Studio de partida de una foto (los de su edición de Events).
    `overrides` = {ruta normcase: ajustes} de las fotos con edición de Studio para Events (se actualiza aquí)."""

    def __init__(self, folder: str, studio_init, overrides: dict):
        self.folder, self.studio_init, self.overrides = folder, studio_init, overrides

    def studio_of(self, ph) -> dict | None:
        return self.overrides.get(os.path.normcase(ph.path))

    def edits_of(self, ph) -> list:
        import edits_engine as EE
        doc = EE.load(self.folder, ph.path)
        if not doc or not doc.get("_events_override"):
            return []
        return [l for l in doc.get("layers", []) if l.get("kind") in EDITS_COPYABLE and EE._layer_active(l)]

    def has_edit(self, ph) -> bool:
        return self.studio_of(ph) is not None or bool(self.edits_of(ph))

    def make_clip(self, ph, groups, layer_ids) -> dict | None:
        s = self.studio_of(ph)
        patch = studio_patch(s, groups) if s else {}
        layers = [copy.deepcopy(l) for l in self.edits_of(ph) if l["id"] in set(layer_ids)]
        if not patch and not layers:
            return None
        parts = []
        if patch:
            parts.append(", ".join(lbl.split(":")[0] for gid, lbl, _, _ in STUDIO_GROUPS if gid in groups))
        if layers:
            parts.append("capas de Edits: " + ", ".join(l.get("name", l["kind"]) for l in layers))
        return {"id": uuid.uuid4().hex[:8], "src": os.path.basename(ph.path), "studio": patch, "layers": layers,
                "desc": " · ".join(parts)}

    def apply(self, clip: dict, photos) -> int:
        """Aplica la edición copiada a `photos` (no cambia si están aprobadas o no). Devuelve cuántas."""
        import edits_engine as EE
        n = 0
        for ph in photos:
            if os.path.basename(ph.path) == clip["src"]:
                continue
            changed = False
            if clip["studio"]:
                base = self.studio_of(ph) or self.studio_init(ph)
                s = dict(copy.deepcopy(base), **copy.deepcopy(clip["studio"]))
                s["_events_override"] = True
                SE.save(self.folder, ph.path, s)
                self.overrides[os.path.normcase(ph.path)] = s
                changed = True
            if clip["layers"]:
                doc = EE.load(self.folder, ph.path) or EE.new_doc(base=self.studio_init(ph))
                doc["layers"] = [l for l in doc.get("layers", []) if l.get("_from") != clip["id"]]
                for l in clip["layers"]:
                    nl = copy.deepcopy(l)
                    nl["id"], nl["_from"] = uuid.uuid4().hex[:8], clip["id"]
                    doc["layers"].append(nl)
                doc["_events_override"] = True
                EE.save(self.folder, ph.path, doc)
                changed = True
            n += changed
        return n
