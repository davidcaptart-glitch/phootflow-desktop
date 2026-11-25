"""«¿Cómo quieres editar esta sesión?»: lo que se aplica a la sesión entera, elegido antes de analizar.

Sin Qt. El panel (`session_setup_ui.SessionSetupDialog`) trabaja sobre los mismos valores que los «Ajustes
guardados» (`session_presets.KEYS`, `Main.snapshot`/`apply_snapshot`): aquí están los tipos de sesión con lo que
propone cada uno (`TYPES`, `propose`), el tipo que corresponde a unos valores (`type_of`), un resumen en una
línea (`summary`) y las reglas de sesión que se aplican al terminar el análisis (`apply_rules`: sin recorte
automático, la entrega va con la proporción de la cámara; «Mejorar con IA» en las que lo sugieren).
"""
from __future__ import annotations

from dataclasses import dataclass, field

AUTO = "auto"


@dataclass(frozen=True)
class SessionType:
    key: str
    label: str
    style: str          # nombre en styles (S.AUTO para «Automático»)
    icon: str           # icons.py
    text: str           # qué hace, en una frase
    values: dict = field(default_factory=dict)  # lo que propone (claves de session_presets.KEYS)


# Lo común: recorte inteligente, descartes, color de la cámara, máxima + 2048 px y ZIP de originales.
_BASE = {"crop": True, "focus": True, "exp": True, "dup": True, "eyes": False, "sens": "Normal",
         "straight": True, "uniform": True, "cam": True, "nr": "Normal", "look": "Natural",
         "ratios": ["4:5"], "orig": True, "sizes": ["max", "2048"], "bw": False, "bw_style": "Editorial",
         "portrait": False, "enhance": False, "bokeh": "", "review": True, "zip": True, "xmp": False,
         "exif": True, "move": True}

TYPES: tuple[SessionType, ...] = (
    SessionType(AUTO, "Automático", "Automático (detectar)", "auto",
                "La IA local mira las fotos y decide el tipo; el recorte y el estilo se ajustan a lo que detecte.",
                {}),
    SessionType("boda", "Boda", "Boda", "wedding",
                "Piel cálida y suave, B/N editorial, retratos retocados y la pareja en el centro del encuadre.",
                {"bw": True, "portrait": True, "bokeh": "Suave", "xmp": True, "eyes": False}),
    SessionType("comunion", "Comunión / bautizo", "Boda", "communion",
                "Luz de iglesia limpia y cálida, retratos retocados, B/N clásico y fondo algo desenfocado.",
                {"bw": True, "bw_style": "Película clásica", "portrait": True, "bokeh": "Suave", "nr": "Fuerte"}),
    SessionType("cumple", "Cumpleaños / fiesta", "Cumpleaños / fiesta", "birthday",
                "Color vivo, grano fuerte para interiores, B/N y 1080 px para compartir en redes.",
                {"bw": True, "nr": "Fuerte", "sizes": ["max", "1080"], "portrait": True}),
    SessionType("disco", "Discoteca / noche", "Discoteca / noche", "party",
                "Luces de colores con contraste, grano fuerte y exigencia alta con las movidas.",
                {"nr": "Fuerte", "look": "Vivo", "sens": "Alta", "sizes": ["max", "1080"], "ratios": ["4:5", "9:16"],
                 "enhance": True}),
    SessionType("deporte", "Deporte", "Deporte", "sport",
                "Ráfagas: fuera repetidas y movidas; recorte a la pelota y a quien la disputa, sin B/N.",
                {"look": "Vivo", "sens": "Alta", "sizes": ["max", "1080"], "zip": True}),
    SessionType("retrato", "Retrato / estudio", "Retrato / estudio", "studio_photo",
                "Piel retocada, fondo desenfocado, B/N editorial y descarte estricto de ojos cerrados.",
                {"bw": True, "portrait": True, "bokeh": "Medio", "eyes": True, "xmp": True, "sens": "Alta"}),
    SessionType("exterior", "Exterior / paisaje", "Exterior / paisaje", "sun",
                "Color y nitidez para exteriores, horizonte recto, grano suave y formatos apaisados.",
                {"nr": "Suave", "look": "Vivo", "ratios": ["3:2", "16:9"]}),
)
BY_KEY = {t.key: t for t in TYPES}


def propose(key: str, base: dict | None = None) -> dict:
    """Los valores que propone el tipo `key` sobre `base` (lo que haya; si no, lo común). No toca la nube, la
    calidad, los hilos, el perfil de color de la cámara ni el LUT (el LUT depende del tipo y se elige aparte)."""
    t = BY_KEY.get(key, BY_KEY[AUTO])
    out = dict(base or {})
    out.update(_BASE)
    out.update(t.values)
    out["style"] = t.style
    return out


def type_of(values: dict, styles: dict | None = None) -> str:
    """El tipo de sesión de unos valores (por su estilo); los estilos aprendidos van por su tipo base."""
    style = values.get("style") or ""
    base = ((styles or {}).get(style) or {}).get("_base") or style
    for t in TYPES:
        if t.style == base:
            return t.key
    return AUTO


def summary(v: dict) -> str:
    """Una línea con lo que se aplicará (para el pie del panel y la barra lateral)."""
    bits = []
    bits.append("recorte inteligente" if v.get("crop", True) else "sin recorte")
    if v.get("portrait"):
        bits.append("retratos retocados")
    if v.get("enhance"):
        bits.append("mejorar con IA")
    if v.get("bokeh"):
        bits.append(f"fondo desenfocado {v['bokeh'].lower()}")
    if v.get("lut"):
        import os
        bits.append("LUT " + os.path.splitext(os.path.basename(str(v["lut"])))[0])
    if v.get("bw"):
        bits.append("B/N " + str(v.get("bw_style", "")).lower())
    disc = [n for k, n in (("focus", "desenfocadas"), ("eyes", "ojos cerrados"), ("dup", "repetidas"))
            if v.get(k)]
    if disc:
        bits.append("fuera " + ", ".join(disc))
    sizes = v.get("sizes") or []
    if sizes:
        bits.append(" + ".join("máx." if s == "max" else f"{s} px" for s in sizes))
    for k, n in (("zip", "ZIP"), ("xmp", "XMP")):
        if v.get(k):
            bits.append(n)
    if v.get("zip") and v.get("trash_orig"):
        bits.append("originales solo en el ZIP")
    if v.get("del_disc"):
        bits.append("borrar descartadas")
    if v.get("cloud"):
        bits.append("nube")
    return " · ".join(bits)


def apply_rules(photos, smart_crop: bool = True, auto_enhance: bool = False) -> tuple[int, int]:
    """Reglas de sesión al terminar el análisis, solo en las fotos que no se han revisado a mano:
    sin recorte automático, la entrega va con la proporción de la cámara (se respeta el «Personalizado»);
    con «Mejorar con IA», las conservadas que lo sugieren (`ph.fix`) se marcan con lo sugerido.
    Devuelve (fotos sin recorte, fotos marcadas para mejorar)."""
    import engine as E
    import smartcrop as SC
    n_crop = n_enh = 0
    for p in photos:
        if p.error or p.reviewed:
            continue
        if not smart_crop and p.best not in (SC.ORIGINAL, E.CUSTOM_CROP) and SC.ORIGINAL in p.crops:
            p.best = SC.ORIGINAL
            n_crop += 1
        # «Mejorar con IA» (fx_engine.enhance): ph.fix lo deja E.suggest_fixes al final de cull
        if (auto_enhance and p.keep and getattr(p, "fix", None) and not getattr(p, "enhance", None)
                and hasattr(E, "suggested_enhance")):
            p.enhance = E.suggested_enhance(p)
            n_enh += 1
    return n_crop, n_enh
