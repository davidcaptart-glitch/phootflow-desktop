"""Archivo de sesión «<sesión>.phootflow»: se deja en la carpeta de la sesión y, con doble clic, abre PhootFlow
con esa sesión (análisis, revisión y retoques guardados; si los RAW se quitaron, se ofrecen sacar del ZIP).

Las rutas van relativas al propio archivo, así la carpeta se puede mover o copiar a otro disco.
La asociación de la extensión se registra por usuario (HKCU), sin permisos de administrador.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

EXT = ".phootflow"
PROGID = "PhootFlow.Sesion"
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def path_for(src_dir: str) -> str:
    name = os.path.basename(os.path.normpath(src_dir)) or "Sesión"
    return os.path.join(src_dir, name + EXT)


def find(src_dir: str) -> str:
    """El archivo de sesión que ya haya en la carpeta (aunque la carpeta se haya renombrado)."""
    try:
        for fn in sorted(os.listdir(src_dir)):
            p = os.path.join(src_dir, fn)
            if fn.lower().endswith(EXT) and os.path.isfile(p):
                return p
    except OSError:
        pass
    return ""


def write(src_dir: str, out_dir: str, version: str = "") -> str:
    """Crea o actualiza el archivo de sesión y oculta las carpetas internas `.phootflow`."""
    path = find(src_dir) or path_for(src_dir)
    try:
        rel = os.path.relpath(out_dir, src_dir)
        out = out_dir if rel.startswith("..") else rel
    except ValueError:  # otra unidad
        out = out_dir
    data = {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        pass
    data.update({"app": "PhootFlow", "formato": 1, "salida": out, "version": version,
                 "actualizada": dt.datetime.now().isoformat(timespec="seconds")})
    data.setdefault("creada", data["actualizada"])
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    for d in (os.path.join(src_dir, ".phootflow"), os.path.join(out_dir, ".phootflow")):
        hide(d)
    return path


def read(path: str) -> tuple[str, str]:
    """(carpeta de la sesión, carpeta de salida) a partir del archivo de sesión."""
    src = os.path.dirname(os.path.abspath(path))
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = data.get("salida") or "Editadas"
    return src, os.path.normpath(out if os.path.isabs(out) else os.path.join(src, out))


def session_arg(argv: list[str]) -> str:
    """Archivo de sesión (o carpeta) pasado al programa, p. ej. por el doble clic del Explorador."""
    for a in argv[1:]:
        if not a.startswith("--") and (a.lower().endswith(EXT) and os.path.isfile(a) or os.path.isdir(a)):
            return os.path.abspath(a)
    return ""


def hide(path: str) -> None:
    if os.name == "nt" and os.path.isdir(path):
        try:
            import ctypes
            FILE_ATTRIBUTE_HIDDEN = 0x2
            attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
            if attrs != -1 and not attrs & FILE_ATTRIBUTE_HIDDEN:
                ctypes.windll.kernel32.SetFileAttributesW(path, attrs | FILE_ATTRIBUTE_HIDDEN)
        except Exception:
            pass


def open_command() -> str:
    """Orden que abre un archivo de sesión con este PhootFlow (el .exe, o pythonw + app.py si va como código)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return f'"{w if os.path.exists(w) else exe}" "{os.path.join(APP_DIR, "app.py")}" "%1"'


def register() -> bool:
    """Asocia «.phootflow» a PhootFlow para el usuario actual. True si ha cambiado algo."""
    if os.name != "nt":
        return False
    import winreg
    icon = f'"{sys.executable}",0' if getattr(sys, "frozen", False) else \
        f'"{os.path.join(APP_DIR, "assets", "icon.ico")}",0'
    want = {
        rf"Software\Classes\{EXT}": {"": PROGID},
        rf"Software\Classes\{PROGID}": {"": "Sesión de PhootFlow"},
        rf"Software\Classes\{PROGID}\DefaultIcon": {"": icon},
        rf"Software\Classes\{PROGID}\shell\open\command": {"": open_command()},
    }
    changed = False
    for key, vals in want.items():
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            for name, val in vals.items():
                try:
                    cur = winreg.QueryValueEx(k, name)[0]
                except OSError:
                    cur = None
                if cur != val:
                    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
                    changed = True
    if changed:
        try:
            import ctypes
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
        except Exception:
            pass
    return changed
