"""Subida a Google Drive y enlace para compartir (API REST de Drive v3, sin dependencias)."""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import mimetypes
import os
import re
import secrets
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request

import net
import webbrowser

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive"
CHUNK = 16 * 1024 * 1024
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(RuntimeError):
    pass


def folder_id_from(text: str) -> str:
    """Acepta el enlace de una carpeta de Drive o directamente su ID."""
    text = text.strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", text) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", text):
        return text
    raise DriveError("No reconozco esa carpeta de Drive: pega el enlace de la carpeta")


class Drive:
    def __init__(self, cfg_dir: str):
        self.cfg = cfg_dir
        os.makedirs(cfg_dir, exist_ok=True)
        self.client_path = os.path.join(cfg_dir, "client_secret.json")
        self.token_path = os.path.join(cfg_dir, "drive_token.json")
        self._tok = None

    # ---- credenciales
    def has_client(self) -> bool:
        return os.path.exists(self.client_path)

    def install_client(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if "installed" not in d:
            raise DriveError('Ese archivo no es una credencial de tipo "App de escritorio"')
        shutil.copyfile(path, self.client_path)

    @staticmethod
    def find_downloaded_client(folder: str = "") -> str:
        """El client_secret*.json de tipo «App de escritorio» más reciente de Descargas ('' si no hay)."""
        folder = folder or os.path.join(os.path.expanduser("~"), "Downloads")
        try:
            names = [n for n in os.listdir(folder) if n.lower().startswith("client_secret") and
                     n.lower().endswith(".json")]
        except OSError:
            return ""
        for n in sorted(names, key=lambda n: os.path.getmtime(os.path.join(folder, n)), reverse=True):
            p = os.path.join(folder, n)
            try:
                with open(p, encoding="utf-8") as f:
                    if "installed" in json.load(f):
                        return p
            except (OSError, ValueError):
                pass
        return ""

    def _client(self) -> dict:
        with open(self.client_path, encoding="utf-8") as f:
            return json.load(f)["installed"]

    def connected(self) -> bool:
        return self.has_client() and os.path.exists(self.token_path)

    def disconnect(self) -> None:
        self._tok = None
        if os.path.exists(self.token_path):
            os.remove(self.token_path)

    def connect(self, timeout: int = 240) -> None:
        """Abre el navegador para autorizar y guarda el permiso (flujo OAuth de escritorio con PKCE)."""
        c = self._client()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        got = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get("state", [""])[0] == state:
                    got.update({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<h2>PhootFlow: ya puedes cerrar esta pestaña.</h2>".encode())

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        redirect = f"http://127.0.0.1:{srv.server_port}"
        url = c["auth_uri"] + "?" + urllib.parse.urlencode({
            "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
            "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        webbrowser.open(url)
        srv.timeout = 1
        end = time.time() + timeout
        while not got and time.time() < end:
            srv.handle_request()
        srv.server_close()
        if "code" not in got:
            raise DriveError("No se completó la autorización de Google" + (f": {got['error']}" if "error" in got else ""))
        tok = self._post_form(c["token_uri"], {
            "client_id": c["client_id"], "client_secret": c.get("client_secret", ""), "code": got["code"],
            "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect})
        if "refresh_token" not in tok:
            raise DriveError("Google no devolvió permiso permanente; vuelve a conectar")
        self._store(tok)

    def _post_form(self, url: str, data: dict) -> dict:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
        try:
            with net.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise DriveError(f"Google rechazó la autorización ({e.code}): {e.read().decode(errors='replace')[:300]}")

    def _store(self, tok: dict) -> None:
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        self._tok = tok
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(tok, f)

    def _access(self) -> str:
        if self._tok is None:
            if not os.path.exists(self.token_path):
                raise DriveError("Google Drive no está conectado")
            with open(self.token_path, encoding="utf-8") as f:
                self._tok = json.load(f)
        if time.time() >= self._tok.get("expires_at", 0):
            c = self._client()
            new = self._post_form(c["token_uri"], {
                "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                "refresh_token": self._tok["refresh_token"], "grant_type": "refresh_token"})
            new.setdefault("refresh_token", self._tok["refresh_token"])
            self._store(new)
        return self._tok["access_token"]

    # ---- llamadas
    def _call(self, method: str, url: str, body=None, headers=None, raw=False):
        h = {"Authorization": "Bearer " + self._access()}
        h.update(headers or {})
        data = body
        if isinstance(body, dict):
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json; charset=UTF-8"
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with net.urlopen(req, timeout=120) as r:
                return r if raw else (json.loads(r.read() or b"{}"), r.headers)
        except urllib.error.HTTPError as e:
            if e.code == 308:
                return {}, e.headers
            raise DriveError(f"Drive respondió {e.code}: {e.read().decode(errors='replace')[:300]}")

    def check_folder(self, folder_id: str) -> str:
        d, _ = self._call("GET", f"{API}/files/{folder_id}?fields=id,name,mimeType&supportsAllDrives=true")
        if d.get("mimeType") != FOLDER_MIME:
            raise DriveError("Ese enlace no es una carpeta de Drive")
        return d["name"]

    def ensure_folder(self, name: str, parent: str) -> str:
        q = f"name = '{name.replace(chr(39), chr(92) + chr(39))}' and '{parent}' in parents and " \
            f"mimeType = '{FOLDER_MIME}' and trashed = false"
        d, _ = self._call("GET", f"{API}/files?" + urllib.parse.urlencode(
            {"q": q, "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}))
        if d.get("files"):
            return d["files"][0]["id"]
        d, _ = self._call("POST", f"{API}/files?supportsAllDrives=true&fields=id",
                          {"name": name, "mimeType": FOLDER_MIME, "parents": [parent]})
        return d["id"]

    def upload(self, path: str, parent: str, progress=None) -> str:
        """Subida reanudable por trozos (sirve para ZIP de varios GB)."""
        size = os.path.getsize(path)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        _, hd = self._call("POST", f"{UPLOAD}/files?uploadType=resumable&supportsAllDrives=true&fields=id",
                           {"name": os.path.basename(path), "parents": [parent]},
                           {"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)})
        session = hd["Location"]
        sent = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                rng = f"bytes {sent}-{sent + len(chunk) - 1}/{size}" if chunk else f"bytes */{size}"
                d, _ = self._call("PUT", session, chunk, {"Content-Range": rng, "Content-Type": mime})
                sent += len(chunk)
                if progress:
                    progress(sent, size)
                if d.get("id"):
                    return d["id"]
                if not chunk:
                    raise DriveError("La subida terminó sin confirmación de Drive")

    def share_link(self, file_id: str) -> str:
        """Enlace 'cualquiera con el enlace puede ver'."""
        self._call("POST", f"{API}/files/{file_id}/permissions?supportsAllDrives=true",
                   {"role": "reader", "type": "anyone"})
        d, _ = self._call("GET", f"{API}/files/{file_id}?fields=webViewLink&supportsAllDrives=true")
        return d["webViewLink"]

    def link(self, file_id: str) -> str:
        d, _ = self._call("GET", f"{API}/files/{file_id}?fields=webViewLink&supportsAllDrives=true")
        return d["webViewLink"]
