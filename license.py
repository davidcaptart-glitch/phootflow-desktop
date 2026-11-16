"""Licencias de PhootFlow: activación en línea, token firmado y modo sin conexión.

- Al arrancar, `ensure()` comprueba la licencia en el servidor (Edge Function de Supabase). Si no hay red, vale el
  último token guardado mientras no pase su fecha «hasta» (7 días por defecto, se decide por clave en el servidor).
- El token lo firma el servidor con Ed25519 y aquí solo está la clave pública: editar licencia.json no sirve.
- La clave queda vinculada al equipo con una huella (MachineGuid de Windows pasado por SHA-256).
- Revocada, caducada o sin comprobar demasiado tiempo: la app lo muestra y no deja trabajar. No se toca nada de
  lo ya exportado.
La verificación Ed25519 está escrita en Python puro (RFC 8032) para no depender de DLL nuevas, que Smart App
Control podría bloquear.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

import net

SERVER = "https://exlegzodfrydddgcvbrp.supabase.co/functions/v1/licencias"
PUBLIC_KEY = "25792572162f00f8dd9586b5e9c6b2cd6f85da82d12c4c04816e2438be65b097"
CONTACTO = "David"
STORE = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PhootFlow", "licencia.json")
RELOJ_TOLERANCIA = 24 * 3600  # el reloj puede ir un día por detrás sin que se considere manipulado
TIMEOUT = 8


def server_url() -> str:
    # Solo sirve para pruebas: un servidor falso no puede firmar tokens con la clave privada real.
    return os.environ.get("PHOOTFLOW_LICENCIAS_URL") or SERVER


# ------------------------------------------------------------------ Ed25519 (RFC 8032), solo enteros de Python
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    pa = (y1 - x1) * (y2 - x2) % _P
    pb = (y1 + x1) * (y2 + x2) % _P
    pc = 2 * t1 * t2 * _D % _P
    pd = 2 * z1 * z2 % _P
    e, f, g, h = pb - pa, pd - pc, pd + pc, pb + pa
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(s: int, a):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, a)
        a = _add(a, a)
        s >>= 1
    return q


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(a) -> bytes:
    x, y, z, _ = a
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(b: bytes):
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _equal(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _hint(m: bytes) -> int:
    return int.from_bytes(hashlib.sha512(m).digest(), "little") % _L


def _expand(seed: bytes):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public(seed: bytes) -> bytes:
    return _compress(_mul(_expand(seed)[0], _G))


def ed25519_sign(seed: bytes, msg: bytes) -> bytes:
    """Firma (la usa el servidor simulado de las pruebas; la app solo verifica)."""
    a, prefix = _expand(seed)
    pub = _compress(_mul(a, _G))
    r = _hint(prefix + msg)
    rs = _compress(_mul(r, _G))
    s = (r + _hint(rs + pub + msg) * a) % _L
    return rs + s.to_bytes(32, "little")


def ed25519_verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pub) != 32 or len(sig) != 64:
        return False
    a = _decompress(pub)
    r = _decompress(sig[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    h = _hint(sig[:32] + pub + msg)
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


# ------------------------------------------------------------------ token
def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_token(seed: bytes, data: dict) -> str:
    """Igual que la Edge Function (para el servidor simulado)."""
    p = _b64e(json.dumps(data, separators=(",", ":")).encode())
    return p + "." + _b64e(ed25519_sign(seed, p.encode()))


def decode_token(token: str | None) -> dict | None:
    """Contenido del token si la firma es válida; si no, None."""
    try:
        p, s = token.split(".")
        if not ed25519_verify(bytes.fromhex(PUBLIC_KEY), p.encode(), _b64d(s)):
            return None
        d = json.loads(_b64d(p))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


# ------------------------------------------------------------------ equipo y almacenamiento
def fingerprint() -> str:
    """Huella estable del equipo: no cambia al reinstalar PhootFlow ni al renombrar el PC."""
    raw = ""
    if os.name == "nt":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                               winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
            raw = str(winreg.QueryValueEx(k, "MachineGuid")[0])
        except OSError:
            raw = ""
    if not raw:
        try:
            with open("/etc/machine-id", encoding="ascii") as f:
                raw = f.read().strip()
        except OSError:
            raw = f"{uuid.getnode():x}"
    return hashlib.sha256(("phootflow|" + raw).encode()).hexdigest()[:32]


def machine_name() -> str:
    return (platform.node() or "equipo")[:80]


def _load() -> dict:
    try:
        with open(STORE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)


def mask(clave: str) -> str:
    return clave[:8] + "…" + clave[-5:] if len(clave) > 14 else clave


def normalize(clave: str) -> str:
    return "".join(clave.split()).upper()


# ------------------------------------------------------------------ estado
@dataclass
class Estado:
    ok: bool
    codigo: str  # activa | sin_licencia | revocada | caducada | vencida | otro_equipo | reloj | clave_no_valida
    mensaje: str
    titular: str = ""
    clave: str = ""
    hasta: float = 0.0
    emitido: float = 0.0
    en_linea: bool = False


class ErrorLicencia(Exception):
    pass


def evaluate(d: dict | None = None, ahora: float | None = None) -> Estado:
    """Estado sin conexión, a partir de lo guardado en este equipo."""
    d = _load() if d is None else d
    ahora = time.time() if ahora is None else ahora
    clave = d.get("clave", "")
    if d.get("codigo") == "clave_no_valida":
        return Estado(False, "clave_no_valida", "Esta clave ya no existe. Escribe otra.", clave=clave)
    t = decode_token(d.get("token"))
    if not t:
        return Estado(False, "sin_licencia", "Escribe tu clave de activación para empezar.", clave=clave)
    base = dict(titular=t.get("titular", ""), clave=t.get("clave", clave),
                hasta=float(t.get("hasta", 0)), emitido=float(t.get("emitido", 0)))
    if t.get("huella") != fingerprint():
        return Estado(False, "otro_equipo", "Esta licencia se activó en otro equipo.", **base)
    if t.get("estado") == "revocada":
        return Estado(False, "revocada", f"Esta licencia se ha desactivado. Habla con {CONTACTO}.", **base)
    if t.get("estado") == "caducada":
        return Estado(False, "caducada", f"Esta licencia ha caducado. Habla con {CONTACTO} para renovarla.",
                      **base)
    visto = float(d.get("visto", 0))
    if ahora + RELOJ_TOLERANCIA < max(base["emitido"], visto):
        return Estado(False, "reloj", "La fecha de este equipo está atrasada. Corrígela y vuelve a abrir "
                                      "PhootFlow.", **base)
    if ahora > base["hasta"]:
        return Estado(False, "vencida", "Hace demasiado que no se comprueba la licencia. Conéctate a internet y "
                                        "pulsa «Comprobar».", **base)
    dias = max(0, int((base["hasta"] - ahora + 3600) // 86400))
    return Estado(True, "activa", f"Licencia activa. Funciona sin conexión {dias} día(s) más.", **base)


def _post(payload: dict, timeout: float = TIMEOUT) -> tuple[int, dict]:
    """(código HTTP, JSON). Lanza OSError si no hay conexión con el servidor."""
    req = urllib.request.Request(server_url(), data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "PhootFlow"})
    try:
        with net.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        if e.code >= 500:
            raise OSError(f"servidor {e.code}") from e
        return e.code, body if isinstance(body, dict) else {}
    except ValueError as e:  # respuesta que no es JSON (portal cautivo, proxy…)
        raise OSError("respuesta no válida") from e


def _online(clave: str, timeout: float) -> Estado:
    """Pregunta al servidor y guarda el resultado. Lanza OSError sin conexión y ErrorLicencia si la rechaza."""
    from version import __version__
    code, body = _post({"clave": clave, "huella": fingerprint(), "nombre": machine_name(),
                        "version": __version__}, timeout)
    d = _load()
    if code == 200 and decode_token(body.get("token")):
        d.update(clave=clave, token=body["token"], comprobada=time.time(), codigo="")
        d["visto"] = max(float(d.get("visto", 0)), time.time())
        _save(d)
        st = evaluate(d)
        st.en_linea = True
        return st
    if code == 404:
        if d.get("clave") == clave:
            d.update(token=None, codigo="clave_no_valida")
            _save(d)
        raise ErrorLicencia("Esa clave no existe. Revisa que esté bien escrita.")
    if code == 409:
        raise ErrorLicencia("Esta clave ya está en uso en otro equipo. Si has cambiado de PC, pide a "
                            f"{CONTACTO} que libere el anterior.")
    if code == 200:
        raise ErrorLicencia("El servidor ha respondido con una firma que no es válida.")
    raise ErrorLicencia(f"El servidor no ha aceptado la petición ({code}).")


def activate(clave: str, timeout: float = TIMEOUT) -> Estado:
    clave = normalize(clave)
    if not clave:
        raise ErrorLicencia("Escribe la clave.")
    try:
        return _online(clave, timeout)
    except OSError as e:
        raise ErrorLicencia("No hay conexión con el servidor de licencias. Para activar hace falta internet "
                            "una vez.") from e


def refresh(timeout: float = TIMEOUT) -> Estado:
    """Comprueba en línea si se puede; si no hay red, decide con el último token guardado."""
    d = _load()
    clave = d.get("clave", "")
    if clave:
        try:
            return _online(clave, timeout)
        except ErrorLicencia as e:
            st = evaluate()
            if st.ok:  # el servidor la rechaza (p. ej. equipo liberado y ocupado por otro)
                d = _load()
                d["token"] = None
                _save(d)
                st = evaluate()
            st.mensaje = str(e)
            return st
        except OSError:
            pass
    d = _load()
    if d:
        d["visto"] = max(float(d.get("visto", 0)), time.time())
        _save(d)
    return evaluate(d)


# ------------------------------------------------------------------ interfaz
def _fmt(ts: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts)) if ts else "—"


def _dialog_classes():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                                   QPushButton, QVBoxLayout)

    import theme as T

    def btn(text, kind):
        b = QPushButton(text)
        b.setObjectName(kind)
        b.setCursor(Qt.PointingHandCursor)
        return b

    class ActivateDialog(QDialog):
        """Pide la clave. Se acepta solo cuando la licencia queda activa."""

        def __init__(self, st: Estado, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Licencia de PhootFlow")
            self.setMinimumWidth(460)
            v = QVBoxLayout(self)
            v.setContentsMargins(24, 22, 24, 20)
            v.setSpacing(12)
            title = QLabel("Activa PhootFlow")
            title.setStyleSheet(f"font-size: 15pt; font-weight: 600; color: {T.TEXT};")
            v.addWidget(title)
            self.msg = QLabel()
            self.msg.setWordWrap(True)
            v.addWidget(self.msg)
            self.key = QLineEdit(st.clave if st.codigo not in ("revocada", "caducada", "clave_no_valida") else "")
            self.key.setPlaceholderText("PF-XXXXX-XXXXX-XXXXX-XXXXX")
            self.key.returnPressed.connect(self.do_activate)
            v.addWidget(self.key)
            info = QLabel("La licencia se vincula a este equipo. Después funciona sin conexión unos días; "
                          "lo que ya hayas exportado es tuyo pase lo que pase.")
            info.setObjectName("muted")
            info.setWordWrap(True)
            v.addWidget(info)
            h = QHBoxLayout()
            h.addStretch(1)
            self.b_exit = btn("Salir", "ghost")
            self.b_exit.clicked.connect(self.reject)
            self.b_retry = btn("Comprobar", "ghost")
            self.b_retry.clicked.connect(self.do_retry)
            self.b_retry.setVisible(bool(st.clave) and st.codigo in ("vencida", "otro_equipo", "reloj"))
            self.b_ok = btn("Activar", "primary")
            self.b_ok.clicked.connect(self.do_activate)
            for b in (self.b_exit, self.b_retry, self.b_ok):
                h.addWidget(b)
            v.addLayout(h)
            self.show_state(st)

        def show_state(self, st: Estado, error: bool | None = None):
            bad = (not st.ok and st.codigo != "sin_licencia") if error is None else error
            self.msg.setStyleSheet(f"color: {T.WARN if bad else T.MUTED};")
            self.msg.setText(st.mensaje)

        def _busy(self, on: bool):
            for w in (self.b_ok, self.b_retry, self.key):
                w.setEnabled(not on)
            if on:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                QApplication.processEvents()
            else:
                QApplication.restoreOverrideCursor()

        def do_activate(self):
            self._busy(True)
            try:
                st = activate(self.key.text())
            except ErrorLicencia as e:
                self._busy(False)
                return self.show_state(Estado(False, "error", str(e)), True)
            self._busy(False)
            if st.ok:
                return self.accept()
            self.show_state(st)

        def do_retry(self):
            self._busy(True)
            st = refresh()
            self._busy(False)
            if st.ok:
                return self.accept()
            self.show_state(st)

    class StatusDialog(QDialog):
        """Estado de la licencia (opción de la barra lateral)."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Licencia")
            self.setMinimumWidth(440)
            v = QVBoxLayout(self)
            v.setContentsMargins(22, 20, 22, 18)
            v.setSpacing(12)
            self.form = QFormLayout()
            self.form.setHorizontalSpacing(18)
            self.lbl = {}
            for k, t in (("estado", "Estado"), ("titular", "Titular"), ("clave", "Clave"),
                         ("equipo", "Este equipo"), ("hasta", "Sin conexión hasta"),
                         ("comprobada", "Última comprobación")):
                lab = QLabel()
                lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
                lab.setWordWrap(True)
                self.form.addRow(QLabel(t), lab)
                self.lbl[k] = lab
            v.addLayout(self.form)
            h = QHBoxLayout()
            b_change = btn("Cambiar clave…", "ghost")
            b_change.clicked.connect(self.change)
            b_check = btn("Comprobar ahora", "ghost")
            b_check.clicked.connect(self.check)
            b_close = btn("Cerrar", "primary")
            b_close.clicked.connect(self.accept)
            h.addWidget(b_change)
            h.addStretch(1)
            h.addWidget(b_check)
            h.addWidget(b_close)
            v.addLayout(h)
            self.fill(evaluate())

        def fill(self, st: Estado):
            color = T.OK if st.ok else T.WARN
            self.lbl["estado"].setText(st.mensaje)
            self.lbl["estado"].setStyleSheet(f"color: {color};")
            self.lbl["titular"].setText(st.titular or "—")
            self.lbl["clave"].setText(mask(st.clave) if st.clave else "—")
            self.lbl["equipo"].setText(f"{machine_name()} · {fingerprint()[:8]}")
            self.lbl["hasta"].setText(_fmt(st.hasta))
            self.lbl["comprobada"].setText(_fmt(float(_load().get("comprobada", 0))))

        def check(self):
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                st = refresh()
            finally:
                QApplication.restoreOverrideCursor()
            self.fill(st)

        def change(self):
            dlg = ActivateDialog(Estado(False, "sin_licencia", "Escribe la clave nueva."), self)
            dlg.key.clear()
            dlg.exec()
            self.fill(evaluate())

    return ActivateDialog, StatusDialog


def ensure(parent=None) -> bool:
    """Comprobación de arranque. True si se puede trabajar; si no, pide la clave hasta que valga o se salga."""
    st = refresh(timeout=6)
    if st.ok:
        return True
    from PySide6.QtWidgets import QDialog
    ActivateDialog, _ = _dialog_classes()
    return ActivateDialog(st, parent).exec() == QDialog.Accepted and evaluate().ok


def show_status(parent=None) -> None:
    _, StatusDialog = _dialog_classes()
    StatusDialog(parent).exec()


_WATCH = None


def watch(window, hours: float = 6.0) -> None:
    """Revisa la licencia cada pocas horas con la app abierta. Si deja de valer, bloquea la ventana (una
    exportación en marcha termina igualmente) con el diálogo modal de la clave; si se sale, se cierra con
    normalidad."""
    global _WATCH
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QDialog

    class Watcher(QObject):
        result = Signal(object)

        def __init__(self):
            super().__init__(window)
            self.result.connect(self.handle)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(int(hours * 3600 * 1000))
            self.busy = False

        def tick(self):
            if not self.busy:
                self.busy = True
                threading.Thread(target=lambda: self.result.emit(refresh()), daemon=True).start()

        def handle(self, st: Estado):
            self.busy = False
            if st.ok:
                return
            ActivateDialog, _ = _dialog_classes()  # modal: mientras está abierto no se puede tocar la ventana
            if not (ActivateDialog(st, window).exec() == QDialog.Accepted and evaluate().ok):
                window.close()

    _WATCH = Watcher()
