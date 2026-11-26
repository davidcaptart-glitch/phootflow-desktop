"""Ventana para gestionar las licencias de PhootFlow (solo para David; no va en el paquete de la app).

Hace lo mismo que admin_licencias.py, pero con botones: crear claves (sin fin, por meses o hasta una
fecha), copiar el mensaje para enviarlas, revocar, reactivar, cambiar la fecha de fin y liberar equipos.
Usa la clave secreta de Supabase de %APPDATA%\\PhootFlow\\admin.env (ver LICENCIAS.md).

    pythonw admin_licencias_gui.py
"""
from __future__ import annotations

import calendar
import datetime as dt
import os
import sys
import tempfile

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDateEdit, QDialog,
                               QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import admin_licencias as AL
import theme as T

DESCARGA = "https://phootflow.com/"

# (texto, meses) — None: sin fin; -1: hasta una fecha concreta; 0: mantener la actual (solo al editar)
DURACIONES = [("Sin fin (la corto yo cuando quiera)", None), ("1 mes", 1), ("3 meses", 3),
              ("6 meses", 6), ("1 año", 12), ("Hasta una fecha…", -1)]


# ------------------------------------------------------------------ utilidades (probadas en tests)
def add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def fin_iso(d: dt.date | None) -> str | None:
    """Fecha de fin → ISO al final de ese día (como `--caduca` en la consola)."""
    return None if d is None else AL._caduca(d.isoformat())


def parse_fecha(s) -> dt.datetime | None:
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None


def estado(lic: dict, ahora: dt.datetime | None = None) -> str:
    """Estado tal y como lo verá la app: activa, terminada (revocada) o caducada."""
    if lic.get("estado") == "revocada":
        return "terminada"
    fin = parse_fecha(lic.get("caduca"))
    if fin and fin < (ahora or dt.datetime.now(dt.timezone.utc)):
        return "caducada"
    return "activa"


# Dirección de las apps móviles (PWA). Pendiente de publicarlas: cámbiala cuando tengan su dominio.
MOVIL = {"lite": "https://lite.phootflow.com", "pro": "https://pro.phootflow.com"}
PRODUCTO_TXT = {"escritorio": "PC", "lite": "Móvil Lite", "pro": "Móvil Pro"}


def mensaje(lic: dict) -> str:
    """Texto listo para mandar por WhatsApp o email."""
    fin = parse_fecha(lic.get("caduca"))
    dura = f"válida hasta el {fin.astimezone():%d/%m/%Y}" if fin else "sin fecha de fin"
    prod = lic.get("producto") or "escritorio"
    if prod != "escritorio":
        app = "PhootFlow Pro" if prod == "pro" else "PhootFlow Lite"
        return (f"¡Hola{(' ' + lic['titular'].split()[0]) if lic.get('titular') else ''}! Aquí tienes tu acceso a {app}.\n\n"
                f"Clave de activación: {lic['clave']}\n"
                f"({dura}, para un móvil)\n\n"
                f"1. Abre {MOVIL[prod]} en el navegador del móvil (Safari en iPhone, Chrome en Android).\n"
                "2. Añádela a la pantalla de inicio: Compartir › «Añadir a pantalla de inicio» (iPhone) o "
                "menú ⋮ › «Instalar app» (Android).\n"
                "3. Ábrela y pega la clave cuando te la pida.\n\n"
                + ("Con Pro también puedes usar PhootFlow Lite con la misma clave.\n" if prod == "pro" else "")
                + "Queda vinculada a tu móvil y funciona hasta 7 días sin conexión. No la compartas, por favor.")
    return (f"¡Hola{(' ' + lic['titular'].split()[0]) if lic.get('titular') else ''}! Aquí tienes tu acceso a PhootFlow.\n\n"
            f"Clave de activación: {lic['clave']}\n"
            f"({dura}, para un PC)\n\n"
            f"1. Descarga PhootFlow: {DESCARGA}\n"
            "   (instalar.ps1 si Windows bloquea el .exe; si no, PhootFlow.exe)\n"
            "2. Ábrelo y pega la clave cuando te la pida.\n\n"
            "Queda vinculada a tu PC y funciona hasta 7 días sin conexión. No la compartas, por favor.")


def _dia(s) -> str:
    d = parse_fecha(s)
    return d.astimezone().strftime("%d/%m/%Y") if d else "—"


# ------------------------------------------------------------------ diálogos
class LicenciaDialog(QDialog):
    """Crear una licencia nueva o editar una existente."""

    def __init__(self, parent=None, lic: dict | None = None):
        super().__init__(parent)
        self.lic = lic
        self.setWindowTitle("Editar licencia" if lic else "Nueva licencia")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        title = QLabel(self.windowTitle())
        title.setObjectName("h1")
        lay.addWidget(title)
        form = QFormLayout()
        form.setSpacing(9)
        self.titular = QLineEdit(lic.get("titular", "") if lic else "")
        self.titular.setPlaceholderText("Nombre y apellido")
        self.email = QLineEdit(lic.get("email", "") if lic else "")
        self.email.setPlaceholderText("opcional")
        self.notas = QLineEdit(lic.get("notas", "") if lic else "")
        self.notas.setPlaceholderText("opcional: de qué lo conoces, para qué la usa…")
        self.dura = QComboBox()
        if lic:
            actual = parse_fecha(lic.get("caduca"))
            self.dura.addItem(f"Mantener ({'hasta el ' + format(actual.astimezone(), '%d/%m/%Y') if actual else 'sin fin'})", 0)
        for txt, m in DURACIONES:
            self.dura.addItem(("Desde hoy: " + txt) if lic and m and m > 0 else txt, m)
        self.fecha = QDateEdit(QDate.currentDate().addMonths(3))
        self.fecha.setCalendarPopup(True)
        self.fecha.setDisplayFormat("dd/MM/yyyy")
        self.fecha.setMinimumDate(QDate.currentDate())
        self.fecha.setVisible(False)
        self.dura.currentIndexChanged.connect(lambda: self.fecha.setVisible(self.dura.currentData() == -1))
        self.producto = QComboBox()
        for k, txt in (("escritorio", "PhootFlow para PC"), ("lite", "App móvil Lite"),
                       ("pro", "App móvil Pro (incluye Lite)")):
            self.producto.addItem(txt, k)
        if lic:
            self.producto.setCurrentIndex(max(0, self.producto.findData(lic.get("producto") or "escritorio")))
            self.producto.setEnabled(False)  # una clave no cambia de producto: se crea otra
        form.addRow("Producto", self.producto)
        form.addRow("Titular", self.titular)
        form.addRow("Email", self.email)
        form.addRow("Notas", self.notas)
        form.addRow("Duración", self.dura)
        form.addRow("", self.fecha)
        lay.addLayout(form)
        hint = QLabel("La clave se genera sola y sirve para un solo equipo (un PC o un móvil, según el producto). "
                      "Puedes terminarla cuando quieras.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox()
        ok = bb.addButton("Guardar cambios" if lic else "Crear clave", QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        bb.addButton("Cancelar", QDialogButtonBox.RejectRole).setObjectName("ghost")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self):
        if not self.titular.text().strip():
            self.titular.setFocus()
            self.titular.setPlaceholderText("Pon al menos un nombre")
            return
        self.accept()

    def fin(self):
        """None: sin fin; 'mantener': no tocarla; o la fecha ISO de fin."""
        m = self.dura.currentData()
        if m == 0:
            return "mantener"
        if m is None:
            return None
        if m == -1:
            return fin_iso(self.fecha.date().toPython())
        return fin_iso(add_months(dt.date.today(), m))

    def datos(self) -> dict:
        d = {"titular": self.titular.text().strip(), "email": self.email.text().strip(),
             "notas": self.notas.text().strip()}
        if not self.lic:
            d["producto"] = self.producto.currentData()
        f = self.fin()
        if f != "mantener":
            d["caduca"] = f
        return d


class ClaveDialog(QDialog):
    """Muestra la clave recién creada con el mensaje listo para enviar."""

    def __init__(self, parent, lic: dict):
        super().__init__(parent)
        self.setWindowTitle("Clave creada")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        t = QLabel(f"Clave para {lic.get('titular') or '—'}")
        t.setObjectName("h1")
        lay.addWidget(t)
        k = QLabel(lic["clave"])
        k.setTextInteractionFlags(Qt.TextSelectableByMouse)
        k.setStyleSheet(f"font: 600 17pt 'Consolas'; color: {T.ACCENT_HOVER}; padding: 8px 0;")
        lay.addWidget(k)
        lab = QLabel("Mensaje para enviarle (puedes retocarlo):")
        lab.setObjectName("muted")
        lay.addWidget(lab)
        self.txt = QPlainTextEdit(mensaje(lic))
        self.txt.setMinimumHeight(190)
        lay.addWidget(self.txt)
        row = QHBoxLayout()
        b1 = QPushButton("Copiar mensaje")
        b1.setObjectName("primary")
        b1.clicked.connect(lambda: self._copy(self.txt.toPlainText(), b1))
        b2 = QPushButton("Copiar solo la clave")
        b2.setObjectName("ghost")
        b2.clicked.connect(lambda: self._copy(lic["clave"], b2))
        b3 = QPushButton("Cerrar")
        b3.setObjectName("ghost")
        b3.clicked.connect(self.accept)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        row.addWidget(b3)
        lay.addLayout(row)

    @staticmethod
    def _copy(text: str, btn: QPushButton):
        QGuiApplication.clipboard().setText(text)
        btn.setText("Copiado ✓")


# ------------------------------------------------------------------ ventana
COLS = ["Titular", "Clave", "Producto", "Estado", "Hasta", "Equipo", "Último uso", "Notas"]
COLOR = {"activa": T.OK, "terminada": T.DANGER, "caducada": T.WARN}


class AdminWindow(QWidget):
    def __init__(self, admin: AL.Admin | None = None):
        super().__init__()
        self.adm = admin
        self.rows: list[dict] = []
        self.setObjectName("root")
        self.setWindowTitle("PhootFlow · Licencias")
        self.resize(1080, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        h = QLabel("Licencias")
        h.setObjectName("h1")
        top.addWidget(h)
        self.resumen = QLabel("")
        self.resumen.setObjectName("muted")
        top.addWidget(self.resumen)
        top.addStretch(1)
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText("Buscar nombre, clave o nota…")
        self.buscar.setFixedWidth(260)
        self.buscar.textChanged.connect(self._fill)
        top.addWidget(self.buscar)
        b = QPushButton("↻")
        b.setObjectName("ghost")
        b.setToolTip("Volver a cargar")
        b.clicked.connect(self.reload)
        top.addWidget(b)
        nueva = QPushButton("＋  Nueva licencia")
        nueva.setObjectName("primary")
        nueva.clicked.connect(self.nueva)
        top.addWidget(nueva)
        lay.addLayout(top)

        self.tabla = QTableWidget(0, len(COLS))
        self.tabla.setHorizontalHeaderLabels(COLS)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.setShowGrid(False)
        self.tabla.setAlternatingRowColors(False)
        hh = self.tabla.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(len(COLS) - 1, QHeaderView.Stretch)
        self.tabla.itemSelectionChanged.connect(self._sel)
        self.tabla.doubleClicked.connect(lambda *_: self.editar())
        self.tabla.setStyleSheet(
            f"QTableWidget {{ background: {T.SURFACE}; border: 1px solid {T.LINE}; border-radius: 6px; }}"
            f"QTableWidget::item {{ padding: 6px 10px; border-bottom: 1px solid {T.LINE}; }}"
            f"QTableWidget::item:selected {{ background: {T.SURFACE_3}; color: {T.TEXT_STRONG}; }}"
            f"QHeaderView::section {{ background: {T.CHROME}; color: {T.MUTED}; border: none;"
            f" border-bottom: 1px solid {T.LINE}; padding: 6px 10px; font-weight: 600; }}")
        lay.addWidget(self.tabla, 1)

        self.detalle = QLabel("Selecciona una licencia.")
        self.detalle.setObjectName("muted")
        self.detalle.setWordWrap(True)
        lay.addWidget(self.detalle)

        acts = QHBoxLayout()
        self.b_msg = self._btn("Copiar mensaje", self.copiar_mensaje, acts)
        self.b_clave = self._btn("Copiar clave", self.copiar_clave, acts)
        self.b_edit = self._btn("Editar / cambiar duración…", self.editar, acts)
        self.b_lib = self._btn("Cambiar de PC", self.liberar, acts)
        acts.addStretch(1)
        self.b_rev = self._btn("Terminar licencia", self.revocar, acts)
        lay.addLayout(acts)
        self._sel()

    def _btn(self, text, fn, lay):
        b = QPushButton(text)
        b.setObjectName("ghost")
        b.clicked.connect(fn)
        lay.addWidget(b)
        return b

    # --- datos
    def _call(self, fn, *a, **k):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self.adm is None:
                self.adm = AL.Admin(os.environ.get("PHOOTFLOW_SUPABASE_URL") or AL.SUPABASE_URL, AL.service_key())
            return fn(*a, **k) if callable(fn) else getattr(self.adm, fn)(*a, **k)
        except SystemExit as e:  # admin_licencias informa de los errores así
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Licencias", str(e))
            return None
        except OSError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Licencias", f"No se pudo conectar con Supabase:\n{e}")
            return None
        finally:
            if QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()

    def reload(self):
        rows = self._call("all")
        if rows is not None:
            self.rows = rows
            self._fill()

    def _fill(self):
        q = self.buscar.text().strip().lower()
        cur = self.actual()
        cur_id = cur["id"] if cur else None
        vis = [r for r in self.rows
               if not q or q in " ".join(str(r.get(k) or "") for k in ("titular", "clave", "notas", "email")).lower()]
        self.tabla.setRowCount(len(vis))
        for i, r in enumerate(vis):
            eqs = r.get("equipos") or []
            ult = max((e.get("ultima") or "" for e in eqs), default="")
            est = estado(r)
            vals = [r.get("titular") or "—", r["clave"], PRODUCTO_TXT.get(r.get("producto") or "escritorio", "?"), est, _dia(r.get("caduca")) if r.get("caduca") else "sin fin",
                    (eqs[0].get("nombre") or "?") if eqs else "—", _dia(ult) if ult else "nunca", r.get("notas") or ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, r["id"])
                if c == 1:
                    it.setFont(QGuiApplication.font())
                    it.setForeground(QColor(T.TEXT))
                if c == 3:
                    it.setForeground(QColor(COLOR[est]))
                self.tabla.setItem(i, c, it)
            if r["id"] == cur_id:
                self.tabla.selectRow(i)
        n = {s: sum(estado(r) == s for r in self.rows) for s in COLOR}
        self.resumen.setText(f"   {n['activa']} activas · {n['terminada']} terminadas · {n['caducada']} caducadas")
        self._sel()

    def actual(self) -> dict | None:
        it = self.tabla.item(self.tabla.currentRow(), 0) if self.tabla.selectedItems() else None
        if not it:
            return None
        rid = it.data(Qt.UserRole)
        return next((r for r in self.rows if r["id"] == rid), None)

    def _sel(self):
        lic = self.actual()
        for b in (self.b_msg, self.b_clave, self.b_edit, self.b_lib, self.b_rev):
            b.setEnabled(lic is not None)
        if not lic:
            self.detalle.setText("Selecciona una licencia." if self.rows else "Aún no hay licencias.")
            self.b_rev.setText("Terminar licencia")
            return
        self.b_rev.setText("Reactivar" if lic["estado"] == "revocada" else "Terminar licencia")
        eqs = lic.get("equipos") or []
        pcs = ", ".join(f"{e.get('nombre') or '?'} (v{e.get('version') or '?'}, último uso {_dia(e.get('ultima'))})"
                        for e in eqs) or "ninguno todavía (aún no la ha activado)"
        self.detalle.setText(f"<b>{lic.get('titular') or '—'}</b> · {lic.get('email') or 'sin email'} · "
                             f"creada el {_dia(lic.get('creada'))}"
                             f"<br>PC: {pcs}")
        self.b_lib.setEnabled(bool(eqs))

    def _replace(self, lic: dict | None):
        if not lic:
            return
        for i, r in enumerate(self.rows):
            if r["id"] == lic["id"]:
                self.rows[i] = {**r, **lic, "equipos": lic.get("equipos", r.get("equipos", []))}
                break
        else:
            self.rows.insert(0, {**lic, "equipos": lic.get("equipos", [])})
        self._fill()

    # --- acciones
    def nueva(self):
        d = LicenciaDialog(self)
        if d.exec() != QDialog.Accepted:
            return
        x = d.datos()
        lic = self._call("create", x["titular"], x["email"], x["notas"], 1, 7, x.get("caduca"),
                         x.get("producto", "escritorio"))
        if lic:
            self._replace(lic)
            self._select(lic["id"])
            ClaveDialog(self, lic).exec()

    def _select(self, rid):
        for i in range(self.tabla.rowCount()):
            if self.tabla.item(i, 0).data(Qt.UserRole) == rid:
                self.tabla.selectRow(i)

    def editar(self):
        lic = self.actual()
        if not lic:
            return
        d = LicenciaDialog(self, lic)
        if d.exec() == QDialog.Accepted:
            self._replace(self._call("update", lic, **d.datos()))

    def revocar(self):
        lic = self.actual()
        if not lic:
            return
        if lic["estado"] == "revocada":
            self._replace(self._call("update", lic, estado="activa"))
            return
        r = QMessageBox.question(
            self, "Terminar licencia", f"¿Terminar la licencia de {lic.get('titular') or lic['clave']}?\n\n"
            "Lo notará al abrir PhootFlow o en menos de 6 h. Sin conexión, en "
            f"{lic.get('dias_offline', 7)} días como mucho. Podrás reactivarla cuando quieras.")
        if r == QMessageBox.Yes:
            self._replace(self._call("update", lic, estado="revocada"))

    def liberar(self):
        lic = self.actual()
        if not lic:
            return
        r = QMessageBox.question(self, "Cambiar de PC",
                                 f"¿Desvincular el PC de {lic.get('titular') or lic['clave']}?\n\n"
                                 "Así podrá activar la misma clave en su PC nuevo.")
        if r == QMessageBox.Yes and self._call("release", lic) is not None:
            self._replace({**lic, "equipos": []})

    def copiar_clave(self):
        lic = self.actual()
        if lic:
            QGuiApplication.clipboard().setText(lic["clave"])
            self.b_clave.setText("Copiada ✓")

    def copiar_mensaje(self):
        lic = self.actual()
        if lic:
            ClaveDialog(self, lic).exec()


def make_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico")
    app.setWindowIcon(QIcon(ico))
    check = os.path.join(tempfile.gettempdir(), "phootflow_check.svg")
    try:
        with open(check, "wb") as f:
            f.write(T.CHECK_SVG)
    except OSError:
        pass
    extra = (f"QDateEdit, QPlainTextEdit {{ background: {T.SURFACE_2}; color: {T.TEXT}; border: 1px solid {T.LINE};"
             f" border-radius: 4px; padding: 5px 7px; }}"
             f"QDateEdit::drop-down {{ border: none; width: 18px; }}"
             f"QCalendarWidget QWidget {{ background: {T.SURFACE_2}; color: {T.TEXT}; }}")
    app.setStyleSheet(T.QSS.replace(":/check.svg", check.replace("\\", "/")) + extra)
    return app


def main():
    app = make_app()
    w = AdminWindow()
    w.show()
    w.reload()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
