"""Actualización automática desde las Releases públicas de GitHub (repo «phootflow-releases»).

Cuatro formas de estar instalado:
- «setup»: instalada con PhootFlow-Setup.exe (lo normal para compañeros). Se descarga el instalador nuevo, se
  verifica y, al cerrar el programa, se ejecuta en silencio encima de la versión instalada y se reabre.
- «exe»: el PhootFlow.exe suelto. Se descarga el .exe nuevo, se verifica y un .bat lo sustituye.
- «source»: copia instalada con instalar.ps1 (Python + código). Se descarga PhootFlow-src.zip, se verifica,
  se copia encima, se actualizan las dependencias si cambiaron y se reinicia. Es la vía para PCs con
  Smart App Control, que bloquea ejecutables sin firmar.
- «dev»: un clon de git. Solo avisa; se actualiza con git pull.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import net
import zipfile

# El código está en un repo privado; las versiones se publican en este repo público, solo de releases.
REPO = "davidcaptart-glitch/phootflow-releases"
ASSET_EXE = "PhootFlow.exe"
ASSET_SETUP = "PhootFlow-Setup.exe"
ASSET_SRC = "PhootFlow-src.zip"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
_UA = {"User-Agent": "PhootFlow-updater", "Accept": "application/vnd.github+json"}
_KEEP_ON_UPDATE = {".git", "__pycache__", ".venv", "venv"}


def _ver(v: str) -> tuple:
    return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())


def is_newer(remote: str, local: str) -> bool:
    return _ver(remote) > _ver(local)


def mode() -> str:
    if getattr(sys, "frozen", False):
        if os.name != "nt":
            return "none"
        # el instalador deja su desinstalador junto al programa
        return "setup" if os.path.exists(os.path.join(os.path.dirname(sys.executable), "unins000.exe")) else "exe"
    if os.path.isdir(os.path.join(APP_DIR, ".git")):
        return "dev"
    return "source"


def can_self_update() -> bool:
    return mode() in ("setup", "exe", "source")


def latest() -> dict | None:
    """{'version', 'url', 'sha_url', 'notes'} de la última release para este modo, o None."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=_UA)
        with net.urlopen(req, timeout=8) as r:
            d = json.load(r)
        assets = {a["name"]: a["browser_download_url"] for a in d.get("assets", [])}
        name = {"setup": ASSET_SETUP, "exe": ASSET_EXE}.get(mode(), ASSET_SRC)
        if name not in assets:
            return None
        return {"version": d["tag_name"].lstrip("v"), "url": assets[name], "name": name,
                "sha_url": assets.get(name + ".sha256"), "notes": d.get("body") or ""}
    except Exception:
        return None


def download(info: dict, progress=None) -> str:
    """Descarga el archivo de la release y comprueba su SHA-256. Devuelve la ruta temporal."""
    if not info.get("sha_url"):
        raise RuntimeError("La versión publicada no trae suma de verificación")
    with net.urlopen(urllib.request.Request(info["sha_url"], headers=_UA), timeout=15) as r:
        expected = r.read().decode().split()[0].strip().lower()
    dest = os.path.join(tempfile.gettempdir(), "PhootFlow_nueva_" + info.get("name", ASSET_EXE))
    h = hashlib.sha256()
    with net.urlopen(urllib.request.Request(info["url"], headers=_UA), timeout=30) as r, \
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


def apply(info: dict, progress=None) -> None:
    """Descarga, verifica, instala y deja lanzado el reinicio. Después hay que cerrar la aplicación."""
    m = mode()
    if m == "setup":
        run_setup_and_restart(download(info, progress))
    elif m == "exe":
        swap_and_restart(download(info, progress))
    elif m == "source":
        install_source(download(info, progress))
        restart_source()
    else:
        raise RuntimeError("Esta copia no se puede actualizar sola (es de desarrollo): usa git pull.")


def install_source(zip_path: str, target: str = APP_DIR) -> None:
    """Copia el código nuevo encima del instalado y actualiza dependencias si cambiaron."""
    tmp = tempfile.mkdtemp(prefix="phootflow_upd_")
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():  # nada fuera de la carpeta
            if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
                raise RuntimeError(f"Paquete de actualización no válido: {n}")
        z.extractall(tmp)
    entries = os.listdir(tmp)
    src = os.path.join(tmp, entries[0]) if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])) \
        else tmp
    if not os.path.exists(os.path.join(src, "app.py")):
        raise RuntimeError("Paquete de actualización no válido: falta app.py")
    req_old = _read(os.path.join(target, "requirements.txt"))
    nodeps_old = _read(os.path.join(target, "requirements-nodeps.txt"))
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in _KEEP_ON_UPDATE]
        rel = os.path.relpath(root, src)
        os.makedirs(os.path.join(target, rel), exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(target, rel, f))
    shutil.rmtree(tmp, ignore_errors=True)
    for name, extra, old in (("requirements.txt", [], req_old), ("requirements-nodeps.txt", ["--no-deps"], nodeps_old)):
        path = os.path.join(target, name)
        if os.path.exists(path) and _read(path) != old:
            subprocess.run([_python(console=True), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                            *extra, "-r", path], check=False, creationflags=_NO_WINDOW)


def restart_source() -> None:
    subprocess.Popen([_python(console=False), os.path.join(APP_DIR, "app.py")], cwd=APP_DIR,
                     creationflags=_DETACHED, close_fds=True)


SETUP_LOG = os.path.join(tempfile.gettempdir(), "phootflow_instalacion.log")


def setup_args(setup: str, relaunch_args: str = "") -> list[str]:
    """Instalación silenciosa encima de la actual. El instalador espera a que el programa se cierre (Restart
    Manager), sustituye los archivos y lo vuelve a abrir él mismo (/RELANZAR=1, ver instalador/PhootFlow.iss)."""
    args = [setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELANZAR=1",
            f"/LOG={SETUP_LOG}"]
    if relaunch_args:
        args.append(f"/ARGS={relaunch_args}")
    return args


def run_setup_and_restart(setup: str, relaunch_args: str = "") -> None:
    """Lanza el instalador nuevo y vuelve: el programa tiene que cerrarse justo después. Sin scripts .bat
    intermedios (no siempre llegaban a ejecutarse). Lanza OSError si Windows no deja abrir el instalador."""
    args = setup_args(setup, relaunch_args)
    flags = _DETACHED
    try:  # fuera del «job» del programa, para que cerrarlo no se lleve el instalador por delante
        subprocess.Popen(args, creationflags=flags | 0x01000000, close_fds=True)
    except OSError:
        subprocess.Popen(args, creationflags=flags, close_fds=True)


def swap_and_restart(new_exe: str) -> None:
    """Lanza un script que espera a que se cierre el programa, sustituye el .exe y lo reabre."""
    current = sys.executable
    bat = os.path.join(tempfile.gettempdir(), "phootflow_actualizar.bat")
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
    subprocess.Popen(["cmd", "/c", bat], creationflags=_NO_WINDOW | 0x00000008, close_fds=True)


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_DETACHED = (0x00000008 | 0x00000200) if os.name == "nt" else 0


def _python(console: bool) -> str:
    exe = sys.executable
    d, b = os.path.split(exe)
    if os.name == "nt":
        want = "python.exe" if console else "pythonw.exe"
        alt = os.path.join(d, want)
        if os.path.exists(alt):
            return alt
    return exe


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""
