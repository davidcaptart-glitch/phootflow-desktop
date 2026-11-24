"""Ajustes de sesión guardados («perfiles»): LUT, revelado, B/N, formatos, cómo exportar, nube y calidad.

Sin Qt. Un perfil es un dict con las mismas claves que guarda la ventana de Events (`Main.snapshot`); las
claves que falten no se tocan al aplicarlo (los predefinidos, por ejemplo, no cambian la carpeta de la nube ni
los hilos). Los tuyos van en `%APPDATA%\\PhootFlow\\ajustes_sesion\\<nombre>.json`; uno tuyo con el nombre de
un predefinido lo sustituye, y al borrarlo vuelve el predefinido.

Cada sesión recuerda además los ajustes con los que se trabajó en `<salida>/.phootflow/ajustes.json`
(`save_for_session` / `load_for_session`), para que al volver a abrirla se exporte igual.
"""
from __future__ import annotations

import json
import os
import re

SUBDIR = "ajustes_sesion"
SESSION_FILE = "ajustes.json"
KEEP = "Los últimos usados"        # opción de «Abrir carpeta»: no cambiar nada
OWN = "Los de esta sesión"         # opción de «Abrir carpeta»: los que guardó esta sesión

# claves que entiende la ventana (las demás se ignoran al aplicar)
KEYS = ("style", "look", "nr", "evc", "straight", "uniform", "cam", "focus", "exp", "dup", "eyes", "sens",
        "ratios", "sizes", "orig", "bw", "bw_style", "quality", "workers", "review", "move", "exif", "xmp",
        "zip", "trash_orig", "del_disc", "cloud", "cloud_folder", "up_orig", "lut", "lutk")

BUILTIN: dict[str, dict] = {
    "Boda": {
        "_desc": "Boda completa: color + B/N editorial, 4:5 para redes, máxima y 2048 px, ZIP de originales y XMP.",
        "style": "Boda", "look": "Natural", "nr": "Normal", "straight": True, "uniform": True, "cam": True,
        "focus": True, "exp": True, "dup": True, "eyes": False, "sens": "Normal",
        "ratios": ["4:5"], "sizes": ["max", "2048"], "orig": True, "bw": True, "bw_style": "Editorial",
        "quality": 95, "review": True, "move": True, "exif": True, "xmp": True, "zip": True,
        "trash_orig": False, "del_disc": False},
    "Cumpleaños / fiesta": {
        "_desc": "Fiesta o cumpleaños: color vivo, B/N, grano fuerte para interiores, 4:5 y 1080 px para redes.",
        "style": "Cumpleaños / fiesta", "look": "Natural", "nr": "Fuerte", "straight": True, "uniform": True,
        "cam": True, "focus": True, "exp": True, "dup": True, "eyes": False, "sens": "Normal",
        "ratios": ["4:5"], "sizes": ["max", "1080"], "orig": True, "bw": True, "bw_style": "Editorial",
        "quality": 90, "review": True, "move": True, "exif": True, "xmp": False, "zip": True,
        "trash_orig": False, "del_disc": False},
    "Deporte": {
        "_desc": "Deporte: muchas ráfagas (repetidas y desenfocadas fuera, exigencia alta), sin B/N, entrega rápida.",
        "style": "Deporte", "look": "Vivo", "nr": "Normal", "straight": True, "uniform": True, "cam": True,
        "focus": True, "exp": True, "dup": True, "eyes": False, "sens": "Alta",
        "ratios": ["4:5"], "sizes": ["max", "1080"], "orig": True, "bw": False, "quality": 90,
        "review": False, "move": True, "exif": True, "xmp": False, "zip": True, "trash_orig": False,
        "del_disc": False},
    "Entrega rápida (redes)": {
        "_desc": "Solo lo justo para publicar: 1080 px en 4:5 y 9:16, calidad 85, sin revisión, ZIP ni XMP.",
        "look": "Vivo", "nr": "Normal", "straight": True, "uniform": True, "focus": True, "exp": True,
        "dup": True, "sens": "Normal", "ratios": ["4:5", "9:16"], "sizes": ["1080"], "orig": False,
        "bw": False, "quality": 85, "review": False, "move": True, "exif": False, "xmp": False, "zip": False,
        "trash_orig": False, "del_disc": False},
    "Archivo completo (Lightroom)": {
        "_desc": "Para seguir en Lightroom: XMP, EXIF, máxima calidad (100), todos los tamaños y ZIP de originales.",
        "nr": "Suave", "straight": True, "uniform": True, "focus": True, "exp": True, "dup": True,
        "sens": "Baja", "ratios": [], "sizes": ["max", "2048", "1080"], "orig": True, "bw": True,
        "bw_style": "Editorial", "quality": 100, "review": True, "move": True, "exif": True, "xmp": True,
        "zip": True, "trash_orig": False, "del_disc": False},
}


def folder(cfg_dir: str) -> str:
    return os.path.join(cfg_dir, SUBDIR)


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "ajustes"


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def user_presets(cfg_dir: str) -> dict[str, dict]:
    out = {}
    d = folder(cfg_dir)
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d), key=str.lower):
            if fn.lower().endswith(".json"):
                p = _read(os.path.join(d, fn))
                if p is not None:
                    out[p.get("_name") or fn[:-5]] = p
    return out


def all_presets(cfg_dir: str) -> dict[str, dict]:
    """Predefinidos primero (o tu versión si guardaste uno con su nombre) y luego los tuyos."""
    mine = user_presets(cfg_dir)
    out = {n: mine.get(n, p) for n, p in BUILTIN.items()}
    out.update({n: p for n, p in mine.items() if n not in BUILTIN})
    return out


def is_user(cfg_dir: str, name: str) -> bool:
    return name in user_presets(cfg_dir)


def describe(p: dict) -> str:
    if p.get("_desc"):
        return p["_desc"]
    bits = []
    if p.get("style"):
        bits.append(p["style"].replace("Mi estilo: ", ""))
    if p.get("lut"):
        bits.append("LUT " + os.path.splitext(os.path.basename(p["lut"]))[0])
    if p.get("bw"):
        bits.append("B/N " + p.get("bw_style", ""))
    if p.get("sizes"):
        bits.append(", ".join("máx." if s == "max" else f"{s} px" for s in p["sizes"]))
    if "quality" in p:
        bits.append(f"JPG {p['quality']}")
    if p.get("xmp"):
        bits.append("XMP")
    if p.get("zip"):
        bits.append("ZIP")
    if p.get("cloud"):
        bits.append("nube")
    return " · ".join(b.strip() for b in bits if b.strip())


def clean(values: dict) -> dict:
    return {k: values[k] for k in KEYS if k in values}


def save_preset(cfg_dir: str, name: str, values: dict, desc: str = "") -> str:
    name = name.strip()
    if not name:
        raise ValueError("El nombre está vacío.")
    os.makedirs(folder(cfg_dir), exist_ok=True)
    data = dict(clean(values), _name=name)
    if desc:
        data["_desc"] = desc
    path = os.path.join(folder(cfg_dir), _safe(name) + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def delete_preset(cfg_dir: str, name: str) -> bool:
    """Borra un perfil tuyo (si tenía el nombre de un predefinido, vuelve el predefinido)."""
    d = folder(cfg_dir)
    if not os.path.isdir(d):
        return False
    for fn in os.listdir(d):
        p = os.path.join(d, fn)
        if fn.lower().endswith(".json") and ((_read(p) or {}).get("_name") or fn[:-5]) == name:
            os.remove(p)
            return True
    return False


def save_for_session(work_dir: str, values: dict, preset: str = "") -> None:
    """Guarda en la sesión los ajustes con los que se trabaja (y el nombre del perfil, si vino de uno)."""
    os.makedirs(work_dir, exist_ok=True)
    data = dict(clean(values), _preset=preset)
    path = os.path.join(work_dir, SESSION_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_for_session(work_dir: str) -> dict | None:
    return _read(os.path.join(work_dir, SESSION_FILE))
