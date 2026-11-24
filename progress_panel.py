"""Panel de progreso de la exportación de Events: lista de pasos, barra general con el tiempo que queda,
las fotos que se están editando ahora y las que ya están hechas, para ver el trabajo mientras avanza.

El Worker de app.py emite `plan` (pasos), `step` (estado de cada paso), `item` (cada foto empieza o termina)
y `progress` (texto y barra del paso). El panel calcula el tiempo restante con lo que tarda cada foto.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

import theme as T

MARK = {"pending": ("○", T.FAINT), "active": ("◐", T.ACCENT), "done": ("✓", T.OK), "skip": ("–", T.FAINT),
        "error": ("!", T.WARN)}


def fmt_time(sec: float) -> str:
    sec = int(max(0, sec))
    if sec >= 3600:
        return f"{sec // 3600} h {sec % 3600 // 60:02d} min"
    return f"{sec // 60} min {sec % 60:02d} s" if sec >= 60 else f"{sec} s"


class StepRow(QWidget):
    def __init__(self, label: str):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(8)
        self.mark = QLabel()
        self.mark.setFixedWidth(16)
        self.mark.setAlignment(Qt.AlignCenter)
        self.label = QLabel(label)
        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        h.addWidget(self.mark)
        h.addWidget(self.label, 1)
        h.addWidget(self.detail)
        self.state = ""
        self.set("pending")

    def set(self, state: str, detail: str | None = None):
        self.state = state
        ch, col = MARK.get(state, MARK["pending"])
        self.mark.setText(ch)
        self.mark.setStyleSheet(f"color: {col}; font-weight: 700; font-size: 11pt;")
        strong = state == "active"
        dim = state in ("skip", "pending")
        self.label.setStyleSheet(f"color: {T.TEXT_STRONG if strong else (T.FAINT if dim else T.TEXT)};"
                                 + (" font-weight: 600;" if strong else ""))
        if detail is not None:
            self.detail.setText(detail)
        elif state == "skip":
            self.detail.setText("no hace falta")


class ExportProgress(QDialog):
    """Ventana no modal: se puede ocultar y el trabajo sigue (la barra de abajo de Events sigue avanzando)."""

    def __init__(self, main, title: str = "Exportando la sesión"):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(title)
        self.setModal(False)
        self.resize(760, 620)
        self.rows: dict[str, StepRow] = {}
        self.counts: dict[str, int | None] = {}
        self.cur_step = ""
        self.running: dict[str, QListWidgetItem] = {}
        self.done_units = 0
        self.t0 = time.time()
        self.t_first = None  # cuándo empezó la primera foto (el arranque no cuenta para el ritmo)
        self.finished = False

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        self.head = QLabel(title)
        self.head.setStyleSheet(f"font-size: 14pt; font-weight: 600; color: {T.TEXT_STRONG};")
        v.addWidget(self.head)
        self.total = QProgressBar()
        self.total.setTextVisible(False)
        self.total.setFixedHeight(10)
        v.addWidget(self.total)
        row = QHBoxLayout()
        self.count_lbl = QLabel("Preparando…")
        self.count_lbl.setStyleSheet(f"color: {T.TEXT};")
        self.eta_lbl = QLabel("")
        self.eta_lbl.setStyleSheet(f"color: {T.TEXT_STRONG}; font-weight: 600;")
        row.addWidget(self.count_lbl, 1)
        row.addWidget(self.eta_lbl)
        v.addLayout(row)

        steps_box = QFrame()
        steps_box.setObjectName("card")
        self.steps_lay = QVBoxLayout(steps_box)
        self.steps_lay.setContentsMargins(12, 10, 12, 10)
        self.steps_lay.setSpacing(2)
        v.addWidget(steps_box)
        self.step_bar = QProgressBar()
        self.step_bar.setTextVisible(False)
        self.step_bar.setFixedHeight(4)
        self.step_text = QLabel("")
        self.step_text.setObjectName("muted")
        v.addWidget(self.step_bar)
        v.addWidget(self.step_text)

        now_t = QLabel("AHORA")
        now_t.setObjectName("cardTitle")
        v.addWidget(now_t)
        self.now = self._strip(150, 106)
        self.now.setFixedHeight(140)
        v.addWidget(self.now)
        done_t = QLabel("TERMINADAS")
        done_t.setObjectName("cardTitle")
        v.addWidget(done_t)
        self.done = self._strip(96, 68)
        v.addWidget(self.done, 1)

        brow = QHBoxLayout()
        self.elapsed_lbl = QLabel("")
        self.elapsed_lbl.setObjectName("muted")
        brow.addWidget(self.elapsed_lbl, 1)
        self.b_open = QPushButton("Abrir la carpeta")
        self.b_open.setObjectName("ghost")
        self.b_open.clicked.connect(self._open_folder)
        self.b_cancel = QPushButton("Cancelar")
        self.b_cancel.setObjectName("ghost")
        self.b_cancel.setToolTip("Para al terminar las fotos en curso; lo exportado se conserva")
        self.b_cancel.clicked.connect(self._cancel)
        self.b_hide = QPushButton("Seguir en segundo plano")
        self.b_hide.setObjectName("primary")
        self.b_hide.setToolTip("Oculta esta ventana; el trabajo sigue y puedes volver a abrirla desde la barra")
        self.b_hide.clicked.connect(self.hide)
        for b in (self.b_open, self.b_cancel, self.b_hide):
            brow.addWidget(b)
        v.addLayout(brow)

        self.tick = QTimer(self)
        self.tick.timeout.connect(self._update_times)
        self.tick.start(1000)

    def _strip(self, w, h) -> QListWidget:
        lw = QListWidget()
        lw.setViewMode(QListView.IconMode)
        lw.setFlow(QListView.LeftToRight)
        lw.setWrapping(True)
        lw.setResizeMode(QListView.Adjust)
        lw.setMovement(QListView.Static)
        lw.setIconSize(QSize(w, h))
        lw.setGridSize(QSize(w + 12, h + 30))
        lw.setSelectionMode(QListWidget.NoSelection)
        return lw

    # ---------------------------------------------------------- señales del Worker
    def set_plan(self, plan):
        """Pasos (clave, etiqueta, nº de fotos). Los pasos que ya estaban conservan su estado."""
        old = {k: (r.state, r.detail.text()) for k, r in self.rows.items()}
        while self.steps_lay.count():
            w = self.steps_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.rows = {}
        for key, label, n in plan:
            if n is not None:
                self.counts[key] = n
                label += f" ({n})" if n else ""
            r = StepRow(label)
            if key in old:
                r.set(*old[key])
            self.rows[key] = r
            self.steps_lay.addWidget(r)
        self._update_total()

    def set_step(self, key, state, detail=""):
        r = self.rows.get(key)
        if r is None:
            return
        r.set(state, detail or None)
        if state == "active":
            self.cur_step = key
            self.step_bar.setMaximum(0)  # en marcha hasta que llegue el primer avance
            self.step_text.setText("")
        self._update_total()

    def set_progress(self, i, n, text):
        self.step_bar.setMaximum(max(0, n))
        self.step_bar.setValue(i)
        self.step_text.setText(text.split(" · quedan")[0])

    def on_item(self, step, phase, path):
        ph = self._photo(path)
        name = ph.name if ph is not None else os.path.splitext(os.path.basename(path))[0]
        key = step + "|" + path
        if phase == "start":
            if self.t_first is None:
                self.t_first = time.time()
            it = QListWidgetItem(self._icon(ph, 150, 106), name)
            it.setData(Qt.UserRole, time.time())
            it.setToolTip({"edit": "Editando", "studio": "Exportando su edición de Studio",
                           "edits": "Exportando su retoque de Edits"}.get(step, "") + f": {name}")
            self.now.addItem(it)
            self.running[key] = it
            return
        it = self.running.pop(key, None)
        if it is not None:
            self.now.takeItem(self.now.row(it))
        self.done_units += 1
        if phase == "skip":
            return self._update_total()
        badge = "!" if phase == "error" else "✓"
        di = QListWidgetItem(self._icon(ph, 96, 68, badge), name)
        if phase == "error":
            di.setToolTip(f"{name}: {getattr(ph, 'error', '') or 'error'}")
        self.done.insertItem(0, di)  # la última hecha, la primera
        while self.done.count() > 120:
            self.done.takeItem(self.done.count() - 1)
        self._update_total()

    def finish(self, msg: str, ok: bool = True):
        self.finished = True
        self.tick.stop()
        for r in self.rows.values():
            if r.state == "pending":
                r.set("skip", "cancelado" if not ok else None)
            elif r.state == "active":
                r.set("done" if ok else "error")
        self.now.clear()
        self.head.setText("Exportación terminada" if ok else "Exportación detenida")
        self.total.setMaximum(1)
        self.total.setValue(1)
        self.eta_lbl.setText("")
        self.step_bar.setMaximum(1)
        self.step_bar.setValue(1)
        self.step_text.setText(msg)
        self.step_text.setWordWrap(True)
        self.elapsed_lbl.setText(f"Tiempo total: {fmt_time(time.time() - self.t0)}")
        self.b_cancel.hide()
        self.b_hide.setText("Cerrar")
        self.b_open.setObjectName("primary")
        self.b_open.style().unpolish(self.b_open)
        self.b_open.style().polish(self.b_open)

    # ---------------------------------------------------------- cálculos
    def _units(self) -> int:
        return sum(n for n in self.counts.values() if n)

    def _update_total(self):
        total = self._units()
        self.total.setMaximum(max(1, total))
        self.total.setValue(min(self.done_units, total))
        if total:
            self.count_lbl.setText(f"{self.done_units} de {total} fotos")
        self._update_times()

    def eta(self) -> float | None:
        """Segundos que quedan: lo que tarda de media cada foto por las que faltan (las que están en curso
        cuentan por la mitad). None hasta que haya alguna hecha."""
        total, k = self._units(), self.done_units
        if not k or self.t_first is None or k >= total:
            return None
        rate = (time.time() - self.t_first) / k
        left = total - k - 0.5 * len(self.running)
        return max(0.0, rate * left)

    def _update_times(self):
        if self.finished:
            return
        self.elapsed_lbl.setText(f"Llevas {fmt_time(time.time() - self.t0)}")
        e = self.eta()
        if e is not None:
            self.eta_lbl.setText(f"Quedan ~{fmt_time(e)}")
        elif self.done_units and self.done_units >= self._units():
            self.eta_lbl.setText("Terminando…")
        else:
            self.eta_lbl.setText("Calculando el tiempo…" if self._units() else "")

    # ---------------------------------------------------------- utilidades
    def _photo(self, path):
        for p in getattr(self.main, "photos", []):
            if p.path == path:
                return p
        return None

    def _icon(self, ph, w, h, badge="") -> QIcon:
        pm = None
        try:
            pm = self.main.thumb_pixmap(ph) if ph is not None else None
        except Exception:
            pm = None
        canvas = QPixmap(w, h)
        canvas.fill(QColor(T.SURFACE))
        p = QPainter(canvas)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if pm is not None and not pm.isNull():
            s = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap((w - s.width()) // 2, (h - s.height()) // 2, s)
        if badge:
            col = QColor(T.OK if badge == "✓" else T.WARN)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(w - 22, 4, 18, 18)
            p.setPen(QColor("#05070F"))
            p.drawText(w - 22, 4, 18, 18, Qt.AlignCenter, badge)
        p.end()
        return QIcon(canvas)

    def _open_folder(self):
        out = getattr(self.main, "out_dir", lambda: "")()
        if out and os.path.isdir(out):
            os.startfile(out)  # noqa: S606 (Windows)

    def _cancel(self):
        if hasattr(self.main, "do_cancel"):
            self.main.do_cancel()
        self.b_cancel.setEnabled(False)
        self.b_cancel.setText("Cancelando…")
        self.head.setText("Cancelando al terminar las fotos en curso…")

    def closeEvent(self, e):  # cerrar con la X mientras trabaja = ocultar, no cancelar
        e.accept()
