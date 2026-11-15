"""Actualización automática desde las Releases públicas de GitHub."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

REPO = "davidcaptart-glitch/eventedits"
ASSET = "EventEdits.exe"
_UA = {"User-Agent": "EventEdits-updater", "Accept": "application/vnd.github+json"}


def _ver(v: str) -> tuple:
    return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())


def is_newer(remote: str, local: str) -> bool:
    return _ver(remote) > _ver(local)


def latest() -> dict | None:
    """{'version', 'url', 'sha_url', 'notes'} de la última release, o None si no hay o no hay red."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=_UA)
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.load(r)
        assets = {a["name"]: a["browser_download_url"] for a in d.get("assets", [])}
        if ASSET not in assets:
            return None
        return {"version": d["tag_name"].lstrip("v"), "url": assets[ASSET],
                "sha_url": assets.get(ASSET + ".sha256"), "notes": d.get("body") or ""}
    except Exception:
        return None


def can_self_update() -> bool:
    return bool(getattr(sys, "frozen", False)) and os.name == "nt"


def download(info: dict, progress=None) -> str:
    """Descarga el .exe nuevo y comprueba su SHA-256. Devuelve la ruta temporal."""
    if not info.get("sha_url"):
        raise RuntimeError("La versión publicada no trae suma de verificación")
    with urllib.request.urlopen(urllib.request.Request(info["sha_url"], headers=_UA), timeout=15) as r:
        expected = r.read().decode().split()[0].strip().lower()
    dest = os.path.join(tempfile.gettempdir(), "EventEdits_nueva.exe")
    h = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(info["url"], headers=_UA), timeout=30) as r, \
            open(dest, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(done, total)
    if h.hexdigest() != expected:
        os.remove(dest)
        raise RuntimeError("La descarga no coincide con la suma de verificación")
    return dest


def swap_and_restart(new_exe: str) -> None:
    """Lanza un script que espera a que se cierre el programa, sustituye el .exe y lo reabre."""
    current = sys.executable
    bat = os.path.join(tempfile.gettempdir(), "eventedits_actualizar.bat")
    pid = os.getpid()
    with open(bat, "w", encoding="mbcs") as f:
        f.write(f"""@echo off
:espera
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto espera)
copy /y "{current}" "{current}.anterior" >nul
move /y "{new_exe}" "{current}" >nul || (move /y "{current}.anterior" "{current}" >nul & exit /b 1)
del "{current}.anterior" >nul 2>&1
start "" "{current}"
del "%~f0"
""")
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000 | 0x00000008, close_fds=True)
