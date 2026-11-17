"""PhootFlow — edición por lotes de sesiones de fotos (interfaz de escritorio)."""
from __future__ import annotations

import ctypes
import os
import shutil
from collections import OrderedDict
import sys
import tempfile
import threading
import time
import traceback

import numpy as np
from PySide6.QtCore import QByteArray, QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QImage, QKeySequence, QPainter, QPainterPath,
                           QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QSplitter, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

import drive as gdrive
import compare as CMP
import zoomview as Z
import engine as E
import license as LIC
import luts as L
import styles as S
import theme as T
import updater
from version import __version__

APP = "PhootFlow"
OLD_APP = "EventEdits"
_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
CFG_DIR = os.path.join(_APPDATA, APP)
CLOUDS = ["No subir", "Google Drive (sube y crea enlace)", "Carpeta sincronizada (Amazon Photos u otra)"]
UPDATE_EVERY_MS = 4 * 3600 * 1000
ROLE_IDX = Qt.UserRole + 1
THUMB_CACHE = 600  # miniaturas decodificadas en memoria a la vez (con 2000 fotos no caben todas)


def keep_awake(on: bool) -> None:
    """Evita que Windows suspenda el equipo durante un trabajo largo (la pantalla sí puede apagarse)."""
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
        except Exception:
            pass


def work_dir(out_dir: str) -> str:
    return os.path.join(out_dir, ".phootflow")


def migrate_old_config() -> None:
    """Trae los ajustes y la conexión de Drive de EventEdits (el nombre anterior) la primera vez."""
    old = os.path.join(_APPDATA, OLD_APP)
    if os.path.isdir(old) and not os.path.isdir(CFG_DIR):
        shutil.copytree(old, CFG_DIR)
    new, prev = QSettings(APP, APP), QSettings(OLD_APP, OLD_APP)
    if not new.allKeys() and prev.allKeys():
        for k in prev.allKeys():
            new.setValue(k, prev.value(k))


# ------------------------------------------------------------------ trabajo en segundo plano

class Worker(QThread):
    progress = Signal(int, int, str)
    photo = Signal(int)
    scene = Signal(str, dict)  # tipo detectado, parámetros
    calibrated = Signal(dict)  # ajustes únicos de la sesión (curva, color) para el revelado
    done = Signal(str, str)  # resumen, enlace
    failed = Signal(str)

    def __init__(self, mode, photos, st, src, out, cloud=None):
        super().__init__()
        self.mode, self.photos, self.st, self.src, self.out, self.cloud = mode, photos, st, src, out, cloud or {}
        self.cancel = False
        self.overrides: dict = {}  # ruta (normcase) -> ajustes de Studio para exportar esa foto con ellos

    def run(self):
        keep_awake(True)
        try:
            if self.mode == "analyze":
                self.done.emit(self.analyze(), "")
            elif self.mode == "export":
                self.export()
            else:  # automático: analizar, seleccionar y exportar sin parar
                msg = self.analyze()
                if self.cancel:
                    self.done.emit(msg, "")
                else:
                    self.export(prefix=msg + " ")
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc(limit=3)}")
        finally:
            keep_awake(False)

    def analyze(self) -> str:
        wd = work_dir(self.out)
        reused = E.load_session(self.photos, self.st, wd)
        for i, p in enumerate(self.photos):
            if p.thumb:
                self.photo.emit(i)
        pending = {i for i, p in enumerate(self.photos) if not p.thumb}
        todo, n, t0, count = len(pending), len(self.photos), time.time(), [0]

        def on_done(i):
            if i in pending and (self.photos[i].thumb or self.photos[i].error):
                count[0] += 1
                k = count[0]
                left = (time.time() - t0) / k * (todo - k)
                self.progress.emit(k, todo, f"Analizando {k}/{todo} · quedan ~{_fmt_time(left)}")
                self.photo.emit(i)
                if k % 50 == 0:
                    E.save_session(self.photos, self.st, wd)

        extra = f" ({reused} recuperadas de la vez anterior)" if reused else ""
        self.progress.emit(0, max(todo, 1), f"Analizando {todo} fotos{extra}…")
        E.analyze_many(self.photos, self.st, self.st.workers, on_done, lambda: self.cancel)
        E.save_session(self.photos, self.st, wd)
        if self.cancel:
            return "Análisis cancelado (lo analizado se guarda para la próxima vez)."
        detected = ""
        if self.st.style_name == S.AUTO:
            name, _ = S.detect_scene(self.photos)
            self.st.style, self.st.style_name = dict(S.PRESETS[name]), name
            self.scene.emit(name, self.st.style)
            detected = f"Tipo detectado: {name}. "
        # una sola curva y un solo color para toda la sesión, y exposición igualada entre fotos
        session = E.calibrate_session(self.photos, self.st)
        self.st.style = dict(self.st.style, **session)
        self.calibrated.emit(session)
        for i, p in enumerate(self.photos):
            if p.thumb:
                self.photo.emit(i)
        E.cull(self.photos, self.st)
        keep = sum(p.keep for p in self.photos)
        warn = sum(1 for p in self.photos if p.warn)
        return detected + f"Análisis: {keep} para editar, {n - keep} descartadas" + (f", {warn} con avisos." if warn else ".")

    def export(self, prefix: str = ""):
        studio_keep = [p for p in self.photos if p.keep and os.path.normcase(p.path) in self.overrides]
        keep = [p for p in self.photos if p.keep and os.path.normcase(p.path) not in self.overrides]
        before = E.exported_names(self.out) if self.st.resume else set()
        resumed = sum(1 for p in keep if os.path.basename(p.path) in before)
        t0 = time.time()

        def on_done(k, n):
            real = max(1, k - resumed)
            left = (time.time() - t0) / real * (n - k)
            self.progress.emit(k, n, f"Editando {k}/{n} · quedan ~{_fmt_time(left)}")

        extra = f", {resumed} ya hechas antes" if resumed else ""
        self.progress.emit(0, len(keep), f"Editando {len(keep)} fotos ({self.st.workers} a la vez{extra})…")
        files = E.export_many(keep, self.st, self.out, __version__, on_done, lambda: self.cancel)
        if studio_keep and not self.cancel:
            import studio_engine as SE
            for j, p in enumerate(studio_keep):
                self.progress.emit(j, len(studio_keep), f"Exportando las retocadas en Studio ({j + 1}/{len(studio_keep)})…")
                s = self.overrides[os.path.normcase(p.path)]
                for size in self.st.sizes:
                    side = None if size == "max" else int(size)
                    try:
                        SE.export(p.path, s, os.path.join(self.out, "Sugeridas", f"{p.name}_studio_{size}.jpg"),
                                  self.st.quality, side)
                        files += 1
                    except Exception as e:
                        p.error = f"{type(e).__name__}: {e}"
            keep = keep + studio_keep
        if self.cancel:
            E.write_report(self.photos, self.out)
            return self.done.emit("Exportación cancelada (lo ya exportado se conserva).", "")
        if self.st.write_xmp:
            for p in self.photos:
                if not p.keep and not p.error:
                    E.xmp_sidecar(p, self.st)
        E.write_report(self.photos, self.out)
        zip_path = None
        if self.st.archive_originals and keep:
            self.progress.emit(0, 0, "Comprimiendo originales…")
            zip_path = E.archive_originals(
                self.photos, self.out, lambda a, b: self.progress.emit(a, b, f"Comprimiendo originales ({a}/{b})"))
        link = self.upload(zip_path)
        moved = E.move_discards(self.photos, self.src) if self.st.move_discards else 0
        errs = sum(1 for p in keep if p.error)
        msg = prefix + f"Listo: {len(keep) - errs} fotos editadas ({files} archivos JPG nuevos) en " \
                       f"{_fmt_time(time.time() - t0)}."
        if moved:
            msg += f" {moved} originales descartados movidos a «Descartadas»."
        if errs:
            msg += f" {errs} fotos fallaron (ver informe.csv)."
        self.done.emit(msg, link)

    def upload(self, zip_path) -> str:
        kind = self.cloud.get("kind", 0)
        session = os.path.basename(os.path.normpath(self.src))
        tree = [(r, f) for sub in ("Sugeridas", "Formatos", "ByN") for r, _, fs in os.walk(os.path.join(self.out, sub))
                for f in fs]
        if kind == 2:  # carpeta local que sincroniza otra aplicación
            dest = os.path.join(self.cloud["folder"], session)
            for j, (r, f) in enumerate(tree):
                d = os.path.join(dest, "Editadas", os.path.relpath(r, self.out))
                os.makedirs(d, exist_ok=True)
                shutil.copy2(os.path.join(r, f), os.path.join(d, f))
                self.progress.emit(j + 1, len(tree), "Copiando a la carpeta sincronizada…")
            if zip_path and self.cloud.get("originals"):
                os.makedirs(os.path.join(dest, "Originales"), exist_ok=True)
                shutil.copy2(zip_path, os.path.join(dest, "Originales", os.path.basename(zip_path)))
            return ""
        if kind != 1:
            return ""
        d = gdrive.Drive(CFG_DIR)
        root = d.ensure_folder(session, gdrive.folder_id_from(self.cloud["folder"]))
        edited = d.ensure_folder("Editadas", root)
        cache = {".": edited}
        for j, (r, f) in enumerate(tree):
            rel, parent = os.path.relpath(r, self.out), edited
            acc = ""
            for part in rel.split(os.sep):
                acc = os.path.join(acc, part)
                if acc not in cache:
                    cache[acc] = d.ensure_folder(part, parent)
                parent = cache[acc]
            self.progress.emit(j, len(tree), f"Subiendo a Drive {f} ({j + 1}/{len(tree)})")
            d.upload(os.path.join(r, f), parent)
        if zip_path and self.cloud.get("originals"):
            orig = d.ensure_folder("Originales", root)
            d.upload(zip_path, orig, lambda a, b: self.progress.emit(
                int(a / 2 ** 20), int(b / 2 ** 20), "Subiendo originales comprimidos a Drive (MB)…"))
        return d.share_link(edited)


class LearnWorker(QThread):
    progress = Signal(int, int)
    done = Signal(str, int)
    failed = Signal(str)

    def __init__(self, finals, originals, name, base):
        super().__init__()
        self.finals, self.originals, self.name, self.base = finals, originals, name, base

    def run(self):
        try:
            prog = lambda a, b: self.progress.emit(a, b)  # noqa: E731
            if self.originals:
                path, n = S.learn_style(E, self.originals, self.finals, CFG_DIR, self.name, self.base, prog)
            else:
                path, n = S.learn_from_finals(E, self.finals, CFG_DIR, self.name, self.base, prog)
            self.done.emit(path, n)
        except Exception as e:
            self.failed.emit(str(e))


class LearnDialog(QDialog):
    """Aprender un estilo propio a partir de sesiones ya editadas."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Aprender mi estilo")
        self.setMinimumWidth(560)
        self.worker = None
        self.result_name = ""
        v = QVBoxLayout(self)
        v.setSpacing(10)
        intro = QLabel("Elige una carpeta con <b>tus fotos ya editadas</b> de un tipo de evento. Con eso PhootFlow "
                       "aprende tu aspecto final (luces, contraste, color, B/N, formatos).<br><br>Si además "
                       "indicas la carpeta de los <b>originales</b> (mismos nombres de archivo), aprende también "
                       "cuánto transformas cada foto: el resultado es más fiel.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        v.addWidget(intro)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Nombre del estilo (p. ej. «Bodas David»)")
        self.base = QComboBox()
        self.base.addItems(list(S.PRESETS))
        self.finals, self.origs = QLineEdit(), QLineEdit()
        self.finals.setPlaceholderText("Carpeta con las fotos editadas (obligatoria)")
        self.origs.setPlaceholderText("Carpeta con los originales RAW (opcional)")
        v.addWidget(self.name)
        v.addWidget(labeled("Tipo de evento", self.base))
        for e in (self.finals, self.origs):
            row = QHBoxLayout()
            b = button("…", "small")
            b.setFixedWidth(36)
            b.clicked.connect(lambda _, ed=e: self._pick(ed))
            row.addWidget(e, 1)
            row.addWidget(b)
            v.addLayout(row)
        self.pb = QProgressBar()
        self.pb.setTextVisible(False)
        self.msg = QLabel("")
        self.msg.setObjectName("muted")
        self.msg.setWordWrap(True)
        v.addWidget(self.pb)
        v.addWidget(self.msg)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_cancel = button("Cerrar", "ghost")
        self.b_go = button("Aprender", "primary")
        self.b_cancel.clicked.connect(self.reject)
        self.b_go.clicked.connect(self.go)
        row.addWidget(self.b_cancel)
        row.addWidget(self.b_go)
        v.addLayout(row)

    def _pick(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Elegir carpeta", edit.text())
        if d:
            edit.setText(os.path.normpath(d))

    def go(self):
        name = self.name.text().strip()
        if not name or not os.path.isdir(self.finals.text().strip()):
            return QMessageBox.warning(self, APP, "Pon un nombre y elige la carpeta de fotos editadas.")
        origs = self.origs.text().strip()
        if origs and not os.path.isdir(origs):
            return QMessageBox.warning(self, APP, "La carpeta de originales no existe.")
        self.b_go.setEnabled(False)
        self.msg.setText("Aprendiendo…")
        self.worker = LearnWorker(self.finals.text().strip(), origs, name, self.base.currentText())
        self.parent()._threads.add(self.worker)
        self.worker.finished.connect(lambda w=self.worker: self.parent()._threads.discard(w))
        self.worker.progress.connect(lambda a, b: (self.pb.setMaximum(b), self.pb.setValue(a),
                                                   self.msg.setText(f"Analizando {a}/{b}…")))
        self.worker.done.connect(self.finished_ok)
        self.worker.failed.connect(lambda m: (self.msg.setText(m), self.b_go.setEnabled(True)))
        self.worker.start()

    def finished_ok(self, path, n):
        self.result_name = "Mi estilo: " + self.name.text().strip()
        self.msg.setText(f"Estilo aprendido de {n} fotos y guardado. Ya puedes elegirlo en «Tipo de sesión».")
        self.b_go.setEnabled(True)
        self.b_cancel.setText("Listo")


def ensure_extras() -> None:
    """En la instalación con Python: si faltan los paquetes que se instalan sin dependencias (MediaPipe),
    instalarlos una vez. Cubre la actualización desde versiones cuyo actualizador no los conocía."""
    import importlib.util
    import subprocess
    req = os.path.join(updater.APP_DIR, "requirements-nodeps.txt")
    if updater.mode() != "source" or not os.path.exists(req) or importlib.util.find_spec("mediapipe"):
        return
    subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-m", "pip", "install", "-q",
                    "--disable-pip-version-check", "--no-deps", "-r", req],
                   check=False, creationflags=0x08000000 if os.name == "nt" else 0)
    importlib.invalidate_caches()


def _fmt_time(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60} min {sec % 60:02d} s" if sec >= 60 else f"{sec} s"


# ------------------------------------------------------------------ piezas de interfaz

def card(title: str, open_: bool = True) -> tuple[QFrame, QVBoxLayout]:
    """Panel plegable con cabecera, como los paneles de Lightroom (Biblioteca) y Capture One."""
    f = QFrame()
    f.setObjectName("card")
    outer = QVBoxLayout(f)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    head = QPushButton()
    head.setObjectName("section")
    head.setCheckable(True)
    head.setChecked(open_)
    head.setCursor(Qt.PointingHandCursor)
    body = QWidget()
    v = QVBoxLayout(body)
    v.setContentsMargins(12, 8, 12, 12)
    v.setSpacing(7)
    body.setVisible(open_)

    def label(on):
        head.setText(("\u25be   " if on else "\u25b8   ") + title)

    label(open_)
    head.toggled.connect(lambda on: (body.setVisible(on), label(on)))
    outer.addWidget(head)
    outer.addWidget(body)
    return f, v


def button(text: str, kind: str = "", tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    return b


def labeled(label: str, w: QWidget) -> QWidget:
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 0, 0, 0)
    lab = QLabel(label)
    lab.setObjectName("muted")
    w.setMinimumWidth(120)
    h.addWidget(lab, 1)
    h.addWidget(w)
    return box


def np_to_pixmap(a: np.ndarray) -> QPixmap:
    a = np.ascontiguousarray(a)
    h, w = a.shape[:2]
    return QPixmap.fromImage(QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy())


def render_before(ph: E.Photo) -> np.ndarray:
    """La foto tal como salió de la cámara (exposición 0, sin estilo), con el mismo giro, para comparar."""
    import copy as _copy
    c = _copy.copy(ph)
    c.ev = 0.0
    return render_preview(c, None)


def render_preview(ph: E.Photo, style: dict | None = None) -> np.ndarray:
    """Vista previa con la exposición y el giro actuales, el color de la sesión y el LUT (si hay)."""
    from PIL import Image
    import io
    src = ph.preview_path if ph.preview_path else io.BytesIO(ph.preview)
    a = np.asarray(Image.open(src).convert("RGB"), dtype=np.float32) / 255.0
    d_ev, d_ang = ph.ev - ph.preview_ev, ph.angle - ph.preview_angle
    if abs(d_ev) > 0.01:  # aproximación: deshace la curva sRGB, cambia la exposición y la vuelve a aplicar
        lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
        a = E.tone(lin, d_ev)
    if abs(d_ang) > 0.01:
        a = E.rotate(a, d_ang)
    if style:
        a = S.apply_color(np.clip(a, 0, 1).astype(np.float32), style)
        if style.get("cube"):
            k = float(style.get("cube_strength", 1.0)) if ph.lut_strength is None else float(ph.lut_strength)
            try:
                a = L.apply(a, style["cube"], k)
            except Exception:
                pass
        if not style.get("_no_look"):
            a = E.apply_look(a, ph.look or style.get("look") or "Natural")
        if style.get("bw"):
            bws = ph.bw_style or style.get("bw_style") or "Editorial"
            if bws != E.BW_NONE:
                a = E.bw_editorial(a, seed=ph.dhash & 0xFFFFFFFF, style=bws)
    return (np.clip(a, 0, 1) * 255).astype(np.uint8)


class ThumbDelegate(QStyledItemDelegate):
    """Miniatura redondeada con estado: borde magenta al seleccionar, atenuada y con etiqueta si se descarta."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def sizeHint(self, option, index):
        return QSize(206, 178)

    def paint(self, p: QPainter, option, index):
        ph = self.win.photos[index.data(ROLE_IDX)]
        r = option.rect.adjusted(5, 5, -5, -5)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        sel = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#262626"))
        p.drawRoundedRect(r, 3, 3)
        img = r.adjusted(6, 6, -6, -30)
        pm = self.win.thumb_pixmap(ph)
        if pm:
            s = pm.size().scaled(img.size(), Qt.KeepAspectRatio)
            tr = QRectF(img.x() + (img.width() - s.width()) / 2, img.y() + (img.height() - s.height()) / 2,
                        s.width(), s.height())
            path = QPainterPath()
            path.addRoundedRect(tr, 6, 6)
            p.setClipPath(path)
            p.drawPixmap(tr.toRect(), pm)
            if not ph.keep:
                p.fillRect(tr, QColor(5, 7, 15, 150))
            p.setClipping(False)
        else:
            p.setPen(QColor(T.MUTED))
            p.drawText(img, Qt.AlignCenter, "Analizando…" if self.win.worker else "Sin analizar")
        # etiquetas
        f = QFont(p.font())
        f.setPointSizeF(8)
        f.setBold(True)
        p.setFont(f)
        x = img.x() + 6
        tags = []
        if ph.error:
            tags.append(("Error", T.DANGER))
        elif not ph.keep and ph.thumb:
            tags.append(("Descartada", T.DANGER))
        if ph.warn:
            tags.append(("Revisar", T.WARN))
        for text, col in tags:
            w = p.fontMetrics().horizontalAdvance(text) + 14
            box = QRectF(x, img.y() + 6, w, 18)
            p.setPen(Qt.NoPen)
            c = QColor(col)
            c.setAlpha(230)
            p.setBrush(c)
            p.drawRoundedRect(box, 9, 9)
            p.setPen(QColor("#0B0B12"))
            p.drawText(box, Qt.AlignCenter, text)
            x += w + 4
        # pie: nombre y ajustes
        f.setBold(False)
        f.setPointSizeF(8.5)
        p.setFont(f)
        foot = r.adjusted(10, r.height() - 26, -10, -6)
        p.setPen(QColor(T.TEXT if ph.keep else T.MUTED))
        p.drawText(foot, Qt.AlignLeft | Qt.AlignVCenter, ph.name)
        if ph.thumb:
            p.setPen(QColor(T.MUTED))
            p.drawText(foot, Qt.AlignRight | Qt.AlignVCenter, f"{ph.ev:+.1f} EV · {ph.best}")
        if sel or hover:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(T.ACCENT if sel else "#3A4163"), 2))
            p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), 3, 3)
        p.restore()


class PreviewLabel(QLabel):
    """Imagen grande con el recorte del formato elegido marcado (lo de fuera, oscurecido)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("previewImage")
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pm, self.crop, self.dim = None, None, False
        self.before = None
        self.cmp = CMP.CompareState()
        self.zoom = Z.Zoom(self)
        self.r = QRectF()

    def set_image(self, pm, crop, dim=False, before=None):
        self.pm, self.crop, self.dim, self.before = pm, crop, dim, before
        self.update()

    def zoom_pixmap(self):
        return self.pm

    def _area(self):
        return QRectF(self.rect()).adjusted(10, 10, -10, -10)

    def wheelEvent(self, e):
        if self.pm is not None:
            self.zoom.wheel(e.angleDelta().y(), e.position(), self._area(), self.pm.size())

    def mouseDoubleClickEvent(self, e):
        if self.pm is not None:
            self.zoom.toggle(e.position(), self._area(), self.pm.size())

    def mousePressEvent(self, e):
        if self.zoom.active and (e.button() == Qt.MiddleButton or not self.cmp.split):
            self.zoom.start_pan(e.position())
            return
        if CMP.drag(self.cmp, self.r, e.position().x(), e.position().y()):
            self.cmp.dragging = True
            self.update()

    def mouseMoveEvent(self, e):
        if self.zoom.panning:
            self.zoom.pan(e.position(), self._area(), self.pm.size())
            return self.update()
        if self.cmp.dragging and CMP.drag(self.cmp, self.r, e.position().x(), e.position().y()):
            self.update()

    def mouseReleaseEvent(self, e):
        self.zoom.end_pan()
        self.cmp.dragging = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.BG))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        if not self.pm:
            p.setPen(QColor(T.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Elige una foto de la rejilla")
            return
        area = self._area()
        r = self.zoom.rect(area, self.pm.size())
        self.r = r
        p.save()
        p.setClipRect(area)
        CMP.paint(p, r, self.pm, self.before, self.cmp)
        if not self.cmp.active:
            self.zoom.draw_tile(p, r)
        p.restore()
        self.zoom.draw_badge(p, area, self.pm.size())
        if self.crop and self.crop != (0.0, 0.0, 1.0, 1.0) and not self.cmp.active:
            x0, y0, x1, y1 = self.crop
            c = QRectF(r.x() + x0 * r.width(), r.y() + y0 * r.height(), (x1 - x0) * r.width(),
                       (y1 - y0) * r.height())
            outside = QPainterPath()
            outside.addRect(r)
            inner = QPainterPath()
            inner.addRect(c)
            p.fillPath(outside.subtracted(inner), QColor(5, 7, 15, 170))
            p.setPen(QPen(QColor(T.ACCENT), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(c)
            # tercios
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            for k in (1, 2):
                p.drawLine(int(c.x() + c.width() * k / 3), int(c.y()), int(c.x() + c.width() * k / 3), int(c.bottom()))
                p.drawLine(int(c.x()), int(c.y() + c.height() * k / 3), int(c.right()), int(c.y() + c.height() * k / 3))
        if self.dim:
            p.fillRect(r, QColor(5, 7, 15, 120))


# ------------------------------------------------------------------ ventana principal

class Main(QMainWindow):
    update_found = Signal(dict, bool)  # info, comprobación pedida a mano
    recalibrated = Signal(dict)        # ajustes de sesión recalculados (p. ej. al cambiar de LUT)
    request_studio = Signal(object)    # la revisión pide retocar esta foto en Studio
    models_status = Signal(str)        # mensajes de la descarga/carga de los modelos de IA

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon(E.asset("icon.ico")))
        self.resize(1440, 900)
        self.s = QSettings(APP, APP)
        self.photos, self.worker, self.filter = [], None, "all"
        self._threads: set = set()
        self._thumbs: OrderedDict[int, QPixmap] = OrderedDict()
        self.cur: E.Photo | None = None
        self.pending_update = None
        self.drive = gdrive.Drive(CFG_DIR)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self.build_sidebar())
        h.addWidget(self.build_content(), 1)

        self.load()
        self.cloud_ui()
        self.busy(False)
        self.update_found.connect(self.on_update_found)
        QShortcut(QKeySequence("P"), self, lambda: self.set_keep(True))
        QShortcut(QKeySequence("X"), self, lambda: self.set_keep(False))
        QShortcut(QKeySequence(Qt.Key_Space), self, lambda: self.cur and self.set_keep(not self.cur.keep))
        self.upd_timer = QTimer(self)
        self.upd_timer.timeout.connect(lambda: self.check_update(False))
        self.upd_timer.start(UPDATE_EVERY_MS)
        QTimer.singleShot(1500, lambda: self.check_update(False))
        self.recalibrated.connect(self.on_recalibrated)
        self.models_status.connect(lambda t: self.status.setText(t))
        QTimer.singleShot(800, self.load_models)

    # ---------------------------------------------------------- construcción
    def build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(360)
        outer = QVBoxLayout(side)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        brand = QWidget()
        bh = QHBoxLayout(brand)
        bh.setContentsMargins(18, 18, 18, 10)
        mark = QLabel()
        mark.setPixmap(QPixmap(E.asset("mark.png")).scaledToHeight(34, Qt.SmoothTransformation))
        word = QLabel()
        word.setPixmap(QPixmap(E.asset("wordmark.png")).scaledToHeight(20, Qt.SmoothTransformation))
        bh.addWidget(mark)
        bh.addSpacing(8)
        bh.addWidget(word)
        bh.addStretch(1)
        outer.addWidget(brand)
        self.brand = brand

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # --- sesión
        c, cv = card("Sesión")
        self.src, self.out = QLineEdit(), QLineEdit()
        self.src.setPlaceholderText("Carpeta con los RAW o JPG")
        self.out.setPlaceholderText("Salida: «Editadas» dentro de la sesión")
        for edit in (self.src, self.out):
            row = QHBoxLayout()
            row.setSpacing(6)
            b = button("…", "small", "Elegir carpeta")
            b.setFixedWidth(36)
            b.clicked.connect(lambda _, e=edit: self.pick(e))
            row.addWidget(edit, 1)
            row.addWidget(b)
            cv.addLayout(row)
        v.addWidget(c)

        # --- tipo de sesión / estilo
        c, cv = card("Tipo de sesión")
        self.style_cb = QComboBox()
        self.style_cb.currentTextChanged.connect(self.on_style_changed)
        self.style_info = QLabel("")
        self.style_info.setObjectName("muted")
        self.style_info.setWordWrap(True)
        b_learn = button("Aprender mi estilo…", "small", "Crea un preset a partir de tus sesiones ya editadas")
        b_learn.clicked.connect(self.learn_style)
        cv.addWidget(self.style_cb)
        cv.addWidget(self.style_info)
        self.lut_cb = QComboBox()
        self.lut_cb.setToolTip("LUT (.cube) asociados a este tipo de sesión, más los comunes.\n"
                               "Con un LUT elegido, el programa iguala la sesión y aplica el mismo LUT a todas.")
        self.lut_k = QSpinBox()
        self.lut_k.setRange(0, 100)
        self.lut_k.setSuffix(" %")
        self.lut_k.setValue(100)
        b_lut = button("Importar LUT…", "small", "Añade uno o varios .cube a este tipo de sesión")
        b_lut.clicked.connect(self.import_luts)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.lut_cb, 1)
        row.addWidget(self.lut_k)
        cv.addLayout(row)
        cv.addWidget(b_lut)
        cv.addWidget(b_learn)
        v.addWidget(c)
        self.detected: tuple[str, dict] | None = None
        self.session_extra: dict = {}
        self._lut_loading = False
        self.lut_cb.currentIndexChanged.connect(self.on_lut_changed)
        self.lut_k.valueChanged.connect(self.on_lut_changed)
        self.reload_styles()

        # --- edición
        c, cv = card("Revelado")
        self.nr = QComboBox()
        self.nr.addItems(list(E.NR_LEVELS))
        self.evc = QDoubleSpinBox()
        self.evc.setRange(-1.5, 1.5)
        self.evc.setSingleStep(0.1)
        self.evc.setSuffix(" EV")
        self.straight = QCheckBox("Enderezar automáticamente")
        self.straight.setChecked(True)
        self.c_uniform = QCheckBox("Edición uniforme en toda la sesión")
        self.c_uniform.setToolTip("Una sola curva y un solo color para todas las fotos, y el mismo brillo\n"
                                  "(sobre todo en las caras) entre fotos del mismo momento.")
        self.c_cam = QCheckBox("Partir del look de la cámara")
        self.c_cam.setToolTip("Toma el contraste y el color del JPEG que lleva el RAW (mediana de la sesión).\n"
                              "No se usa con los estilos aprendidos, que ya llevan tu aspecto.")
        cv.addWidget(labeled("Reducción de grano", self.nr))
        cv.addWidget(labeled("Compensar exposición", self.evc))
        self.look_cb = QComboBox()
        self.look_cb.addItems(list(E.COLOR_LOOKS))
        self.look_cb.setToolTip("Look de color de la sesión. En cada foto se puede elegir otro (panel de la foto\n"
                                "o revisión).")
        self.look_cb.currentTextChanged.connect(lambda _: self.show_preview() if hasattr(self, "preview") else None)
        cv.addWidget(labeled("Look de color", self.look_cb))
        cv.addWidget(self.straight)
        cv.addWidget(self.c_uniform)
        cv.addWidget(self.c_cam)
        v.addWidget(c)

        # --- descarte
        c, cv = card("Selección automática")
        self.c_focus = QCheckBox("Desenfocadas o movidas")
        self.c_exp = QCheckBox("Exposición irrecuperable")
        self.c_dup = QCheckBox("Casi duplicadas (deja la más nítida)")
        self.c_move = QCheckBox("Al exportar, mover descartes a «Descartadas»")
        self.c_eyes = QCheckBox("Ojos cerrados (descartar; si no, solo avisa)")
        for w in (self.c_focus, self.c_exp, self.c_dup, self.c_eyes, self.c_move):
            w.setChecked(True)
            cv.addWidget(w)
        self.sens = QComboBox()
        self.sens.addItems(list(E.SENS))
        cv.addWidget(labeled("Exigencia", self.sens))
        v.addWidget(c)

        # --- formatos
        c, cv = card("Formatos y tamaños")
        hint = QLabel("El formato sugerido se exporta siempre. Además:")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        cv.addWidget(hint)
        g = QGridLayout()
        g.setSpacing(6)
        self.r_orig = QCheckBox("Original")
        g.addWidget(self.r_orig, 0, 0)
        self.ratio_cb = {}
        for i, name in enumerate(E.RATIOS, start=1):
            cb = QCheckBox(name)
            self.ratio_cb[name] = cb
            g.addWidget(cb, i // 4, i % 4)
        cv.addLayout(g)
        self.size_cb = {}
        for k, label in [("max", "Máxima resolución"), ("2048", "2048 px lado largo"), ("1080", "1080 px de ancho")]:
            cb = QCheckBox(label)
            self.size_cb[k] = cb
            cv.addWidget(cb)
        self.c_bw = QCheckBox("Versión en blanco y negro editorial")
        self.c_bw.setToolTip("Además del color, una versión B/N de catálogo en «ByN/»: mezcla pensada para la piel,\n"
                             "contraste local, negros densos sin empastar, blancos limpios y grano fino.")
        cv.addWidget(self.c_bw)
        self.bw_style_cb = QComboBox()
        self.bw_style_cb.addItems(list(E.BW_STYLES))
        self.bw_style_cb.setToolTip("Estilo de blanco y negro de la sesión. En cada foto se puede elegir otro\n"
                                    "(panel de la foto o revisión), o quitarle la versión B/N.")
        cv.addWidget(labeled("Estilo B/N", self.bw_style_cb))
        self.q = QSpinBox()
        self.q.setRange(70, 100)
        cv.addWidget(labeled("Calidad JPG", self.q))
        v.addWidget(c)

        # --- salida
        c, cv = card("Archivo y Lightroom")
        self.c_exif = QCheckBox("Conservar EXIF (cámara, objetivo, fecha)")
        self.c_xmp = QCheckBox("Crear XMP para Lightroom junto a los RAW")
        self.c_xmp.setToolTip("Exposición, giro, recorte y reducción de ruido; los descartes como «rechazadas».\n"
                              "Nunca sobrescribe un XMP que ya exista.")
        self.c_zip = QCheckBox("Guardar los originales en un ZIP sin pérdida")
        self.c_review = QCheckBox("Revisar antes de exportar (una a una)")
        self.c_review.setToolTip("Antes de exportar se abre la revisión: aprobar/rechazar, ver B/N, probar\n"
                                 "otros recortes y retocar en Studio las que no convenzan.")
        for w in (self.c_review, self.c_exif, self.c_xmp, self.c_zip):
            cv.addWidget(w)
        self.workers = QSpinBox()
        self.workers.setRange(1, max(1, min(8, (os.cpu_count() or 2))))
        self.workers.setToolTip("Más fotos a la vez es más rápido pero usa más memoria (≈1,5 GB por foto).")
        cv.addWidget(labeled("Fotos a la vez", self.workers))
        v.addWidget(c)

        # --- nube
        c, cv = card("Nube")
        self.cloud = QComboBox()
        self.cloud.addItems(CLOUDS)
        self.cloud_folder = QLineEdit()
        self.cloud_btn = button("", "small")
        self.cloud_btn.clicked.connect(self.cloud_action)
        self.c_up_orig = QCheckBox("Subir también el ZIP de originales (privado)")
        self.cloud.currentIndexChanged.connect(self.cloud_ui)
        cv.addWidget(self.cloud)
        cv.addWidget(self.cloud_folder)
        cv.addWidget(self.cloud_btn)
        cv.addWidget(self.c_up_orig)
        v.addWidget(c)
        v.addStretch(1)

        foot = QWidget()
        fh = QHBoxLayout(foot)
        fh.setContentsMargins(18, 8, 14, 12)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("version")
        self.b_upd = button("Buscar actualizaciones", "ghost")
        self.b_upd.setObjectName("ghost")
        self.b_upd.clicked.connect(lambda: self.check_update(True))
        fh.addWidget(ver)
        fh.addStretch(1)
        b_lic = button("Licencia", "ghost", "Estado de la licencia de este equipo")
        b_lic.clicked.connect(lambda: LIC.show_status(self))
        fh.addWidget(b_lic)
        fh.addWidget(self.b_upd)
        outer.addWidget(foot)
        return side

    def build_content(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 16, 0, 0)
        v.setSpacing(12)

        # aviso de actualización
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 8, 8)
        self.banner_text = QLabel()
        self.b_apply = button("Actualizar y reiniciar", "primary")
        self.b_apply.clicked.connect(self.apply_update)
        b_later = button("Más tarde", "ghost")
        b_later.clicked.connect(self.banner.hide)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(b_later)
        bl.addWidget(self.b_apply)
        self.banner.hide()
        top_wrap = QVBoxLayout()
        top_wrap.setContentsMargins(0, 0, 20, 0)
        top_wrap.addWidget(self.banner)
        v.addLayout(top_wrap)

        # cabecera
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 20, 0)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.h1 = QLabel("Nueva sesión")
        self.h1.setObjectName("h1")
        self.sub = QLabel("Elige la carpeta de la sesión y pulsa «Analizar».")
        self.sub.setObjectName("muted")
        titles.addWidget(self.h1)
        titles.addWidget(self.sub)
        head.addLayout(titles, 1)
        self.b_open = button("Abrir salida", "ghost")
        self.b_ca = button("Cancelar", "ghost")
        self.b_an = button("Analizar", "", "Pasada rápida para revisar antes de exportar: no mueve ni borra nada")
        self.b_ex = button("Exportar", "", "Revela a resolución completa las fotos conservadas")
        self.b_auto = button("Procesar todo", "primary",
                             "Analiza, selecciona y exporta toda la sesión sin parar.\n"
                             "Si se corta, al volver a pulsarlo sigue donde se quedó.")
        self.b_an.clicked.connect(lambda: self.start_analyze(False))
        self.b_ex.clicked.connect(self.start_export)
        self.b_rev = button("Revisar", "", "Revisión una a una antes de exportar")
        self.b_rev.clicked.connect(lambda: self.open_review())
        self.b_auto.clicked.connect(lambda: self.start_analyze(True))
        self.b_ca.clicked.connect(self.do_cancel)
        self.b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_dir())))
        for b in (self.b_open, self.b_ca, self.b_an, self.b_rev, self.b_ex, self.b_auto):
            head.addWidget(b)
        v.addLayout(head)

        # filtros + contadores
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 20, 0)
        seg = QFrame()
        seg.setObjectName("segBar")
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(3, 3, 3, 3)
        sh.setSpacing(2)
        self.seg_group = QButtonGroup(self)
        self.seg_btn = {}
        for key, text in [("all", "Todas"), ("keep", "Para editar"), ("drop", "Descartadas"), ("warn", "Avisos")]:
            b = button(text, "seg")
            b.setCheckable(True)
            b.clicked.connect(lambda _, k=key: self.set_filter(k))
            self.seg_group.addButton(b)
            self.seg_btn[key] = b
            sh.addWidget(b)
        self.seg_btn["all"].setChecked(True)
        bar.addWidget(seg)
        bar.addStretch(1)
        self.chip_keep, self.chip_drop, self.chip_warn = QLabel(), QLabel(), QLabel()
        self.chip_keep.setObjectName("chipAccent")
        self.chip_drop.setObjectName("chip")
        self.chip_warn.setObjectName("chip")
        for c in (self.chip_keep, self.chip_drop, self.chip_warn):
            bar.addWidget(c)
        v.addLayout(bar)

        # rejilla + vista previa
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        self.grid = QListWidget()
        self.grid.setObjectName("grid")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(2)
        self.grid.setMouseTracking(True)
        self.grid.setItemDelegate(ThumbDelegate(self))
        self.grid.currentRowChanged.connect(self.on_select)
        self.grid.itemDoubleClicked.connect(lambda _: self.cur and self.set_keep(not self.cur.keep))
        split.addWidget(self.grid)
        split.addWidget(self.build_preview())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([700, 460])
        v.addWidget(split, 1)

        # pie
        foot = QVBoxLayout()
        foot.setContentsMargins(0, 0, 20, 12)
        foot.setSpacing(6)
        self.pb = QProgressBar()
        self.pb.setTextVisible(False)
        self.status = QLabel("Listo.")
        self.status.setObjectName("muted")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        foot.addWidget(self.pb)
        foot.addWidget(self.status)
        v.addLayout(foot)
        self.refresh_counts()
        return w

    def build_preview(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("previewPane")
        v = QVBoxLayout(pane)
        v.setContentsMargins(16, 6, 20, 12)
        v.setSpacing(10)
        self.preview = PreviewLabel()
        row = QHBoxLayout()
        lab = QLabel("Antes / después")
        lab.setObjectName("meta")
        self.cmp_bar = CMP.CompareBar()
        self.cmp_bar.changed.connect(self.set_compare)
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(self.cmp_bar)
        v.addLayout(row)
        v.addWidget(self.preview, 1)
        QShortcut(QKeySequence("Y"), self, lambda: (self.preview.cmp.next_split(),
                                                    self.set_compare(self.preview.cmp.mode)))
        QShortcut(QKeySequence("Z"), self, lambda: self.preview.zoom.toggle(
            None, self.preview._area(), self.preview.pm.size()) if self.preview.pm is not None else None)
        self.p_title = QLabel("")
        self.p_title.setObjectName("h1")
        self.p_info = QLabel("")
        self.p_info.setObjectName("muted")
        self.p_info.setWordWrap(True)
        v.addWidget(self.p_title)
        v.addWidget(self.p_info)

        c, cv = card("Ajustes de esta foto")
        self.p_keep = QCheckBox("Conservar y editar   (P / X / espacio)")
        self.p_keep.toggled.connect(lambda on: self.set_keep(on))
        self.p_ev = QDoubleSpinBox()
        self.p_ev.setRange(-3.0, E.EV_MAX + 1.0)
        self.p_ev.setSingleStep(0.1)
        self.p_ev.setDecimals(1)
        self.p_ev.setSuffix(" EV")
        self.p_ang = QDoubleSpinBox()
        self.p_ang.setRange(-10, 10)
        self.p_ang.setSingleStep(0.1)
        self.p_ang.setDecimals(1)
        self.p_ang.setSuffix(" °")
        self.p_fmt = QComboBox()
        self.p_ev.valueChanged.connect(self.on_photo_edit)
        self.p_ang.valueChanged.connect(self.on_photo_edit)
        self.p_fmt.currentTextChanged.connect(self.on_photo_edit)
        cv.addWidget(self.p_keep)
        cv.addWidget(labeled("Exposición", self.p_ev))
        cv.addWidget(labeled("Giro", self.p_ang))
        cv.addWidget(labeled("Formato sugerido", self.p_fmt))
        self.p_lut = QSpinBox()
        self.p_lut.setRange(0, 100)
        self.p_lut.setSuffix(" %")
        self.p_lut.setToolTip("Intensidad del LUT solo en esta foto (las demás no cambian).")
        self.p_lut.valueChanged.connect(self.on_photo_lut)
        cv.addWidget(labeled("LUT en esta foto", self.p_lut))
        self.p_bw = QComboBox()
        self.p_bw.addItems(["Como la sesión"] + list(E.BW_STYLES) + [E.BW_NONE])
        self.p_bw.setToolTip("Estilo de blanco y negro solo para esta foto, o sin versión B/N.")
        self.p_bw.currentTextChanged.connect(self.on_photo_bw)
        cv.addWidget(labeled("B/N en esta foto", self.p_bw))
        self.p_look = QComboBox()
        self.p_look.addItems(["Como la sesión"] + list(E.COLOR_LOOKS))
        self.p_look.setToolTip("Look de color solo para esta foto.")
        self.p_look.currentTextChanged.connect(self.on_photo_look)
        cv.addWidget(labeled("Look en esta foto", self.p_look))
        v.addWidget(c)
        self.p_card = c
        c.setEnabled(False)
        return pane

    # ---------------------------------------------------------- ajustes
    def load(self):
        g = self.s.value
        self.src.setText(g("src", ""))
        self.out.setText(g("out", ""))
        self.nr.setCurrentText(g("nr", "Normal"))
        self.sens.setCurrentText(g("sens", "Normal"))
        ratios = (g("ratios", "4:5") or "").split(",")
        for k, c in self.ratio_cb.items():
            c.setChecked(k in ratios)
        sizes = (g("sizes", "max,1080") or "").split(",")
        for k, c in self.size_cb.items():
            c.setChecked(k in sizes)
        b = lambda key, d: str(g(key, d)).lower() in ("true", "1")  # noqa: E731
        self.r_orig.setChecked(b("orig", True))
        self.c_exif.setChecked(b("exif", True))
        self.c_xmp.setChecked(b("xmp", False))
        self.c_bw.setChecked(b("bw", False))
        self.bw_style_cb.setCurrentText(g("bw_style", "Editorial"))
        self.look_cb.setCurrentText(g("look", "Natural"))
        self.c_uniform.setChecked(b("uniform", True))
        self.c_cam.setChecked(b("cam", True))
        self.c_eyes.setChecked(b("eyes", False))
        self.style_cb.setCurrentText(g("style", S.AUTO))
        self.c_zip.setChecked(b("zip", True))
        self.c_review.setChecked(b("review", True))
        self.c_up_orig.setChecked(b("up_orig", True))
        self.q.setValue(int(g("quality", 95)))
        self.workers.setValue(int(g("workers", min(3, max(1, (os.cpu_count() or 2) // 4)))))
        self.cloud.setCurrentIndex(int(g("cloud", 0)))
        self.cloud_folder.setText(g("cloud_folder", ""))

    def save(self):
        st = self.settings()
        for k, v in [("src", self.src.text()), ("out", self.out.text()), ("nr", st.nr), ("sens", st.sensitivity),
                     ("ratios", ",".join(st.ratios)), ("sizes", ",".join(st.sizes)),
                     ("orig", st.export_original_ratio), ("exif", st.keep_exif), ("xmp", st.write_xmp), ("bw", st.bw), ("bw_style", st.bw_style), ("look", st.look),
                     ("uniform", st.uniform), ("cam", st.camera_look), ("eyes", st.cull_eyes),
                     ("style", self.style_cb.currentText()),
                     ("zip", st.archive_originals), ("review", self.c_review.isChecked()), ("up_orig", self.c_up_orig.isChecked()),
                     ("quality", st.quality), ("workers", st.workers),
                     ("cloud", self.cloud.currentIndex()), ("cloud_folder", self.cloud_folder.text())]:
            self.s.setValue(k, v)

    def resolved_style(self) -> tuple[str, dict]:
        name = self.style_cb.currentText()
        if name == S.AUTO:
            return self.detected if self.detected else (S.AUTO, {})
        return name, dict(S.all_styles(CFG_DIR).get(name, S.BASE))

    def settings(self) -> E.Settings:
        style_name, style = self.resolved_style()
        ratios = [k for k, c in self.ratio_cb.items() if c.isChecked()]
        ratios += [r for r in style.get("formats", []) if r in E.RATIOS and r not in ratios]
        cube = self.lut_cb.currentData() if hasattr(self, "lut_cb") else None
        if cube:
            style = dict(style, cube=cube, cube_strength=self.lut_k.value() / 100)
        if self.session_extra and bool(self.session_extra.get("_cube")) == bool(cube):
            style = dict(style, **{k: v for k, v in self.session_extra.items() if k != "_cube"})
        return E.Settings(style=style, style_name=style_name,
            uniform=self.c_uniform.isChecked(), camera_look=self.c_cam.isChecked(),
            cull_eyes=self.c_eyes.isChecked(),
            nr=self.nr.currentText(), straighten=self.straight.isChecked(), ev_comp=self.evc.value(),
            cull_focus=self.c_focus.isChecked(), cull_exposure=self.c_exp.isChecked(),
            cull_dupes=self.c_dup.isChecked(), sensitivity=self.sens.currentText(),
            ratios=ratios,
            export_original_ratio=self.r_orig.isChecked(),
            sizes=[k for k, c in self.size_cb.items() if c.isChecked()] or ["max"],
            quality=self.q.value(), move_discards=self.c_move.isChecked(),
            archive_originals=self.c_zip.isChecked(), keep_exif=self.c_exif.isChecked(),
            write_xmp=self.c_xmp.isChecked(), workers=self.workers.value(), bw=self.c_bw.isChecked(),
            bw_style=self.bw_style_cb.currentText(), look=self.look_cb.currentText(),
            cache_dir=os.path.join(work_dir(self.out_dir()), "previas"), resume=True)

    def out_dir(self) -> str:
        return self.out.text().strip() or os.path.join(self.src.text().strip(), "Editadas")

    def pick(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Elegir carpeta", edit.text())
        if d:
            edit.setText(os.path.normpath(d))

    # ------------------------------------------------------------- estilos
    def reload_styles(self, select: str = ""):
        cur = select or self.style_cb.currentText()
        self.style_cb.blockSignals(True)
        self.style_cb.clear()
        self.style_cb.addItems([S.AUTO] + list(S.all_styles(CFG_DIR)))
        self.style_cb.setCurrentText(cur if cur else S.AUTO)
        self.style_cb.blockSignals(False)
        self.on_style_changed(self.style_cb.currentText())

    def lut_kind(self) -> str:
        name = self.style_cb.currentText()
        if name == S.AUTO:
            return self.detected[0] if self.detected else L.COMMON
        if name.startswith("Mi estilo: "):
            return S.all_styles(CFG_DIR).get(name, {}).get("_base") or L.COMMON
        return name

    def reload_luts(self):
        kind = self.lut_kind()
        self._lut_loading = True
        self.lut_cb.clear()
        self.lut_cb.addItem("Sin LUT", None)
        for label, path in L.available(CFG_DIR, kind):
            self.lut_cb.addItem(label, path)
        want = self.s.value(f"lut/{L.folder_name(kind)}", "")
        idx = self.lut_cb.findData(want) if want else 0
        self.lut_cb.setCurrentIndex(max(0, idx))
        self.lut_k.setValue(int(self.s.value(f"lutk/{L.folder_name(kind)}", 100)))
        self.lut_k.setEnabled(self.lut_cb.currentData() is not None)
        self._lut_loading = False

    def import_luts(self):
        kind = self.lut_kind()
        files, _ = QFileDialog.getOpenFileNames(self, f"LUT para «{kind}»", "", "LUT (*.cube)")
        if not files:
            return
        try:
            done = L.import_luts(CFG_DIR, kind, files)
        except Exception as e:
            return QMessageBox.warning(self, APP, f"No se pudo importar: {e}")
        self.reload_luts()
        self.lut_cb.setCurrentIndex(max(0, self.lut_cb.findData(done[-1])))
        self.status.setText(f"{len(done)} LUT añadidos a «{kind}».")

    def on_lut_changed(self, *_):
        if self._lut_loading:
            return
        kind = L.folder_name(self.lut_kind())
        self.s.setValue(f"lut/{kind}", self.lut_cb.currentData() or "")
        self.s.setValue(f"lutk/{kind}", self.lut_k.value())
        self.lut_k.setEnabled(self.lut_cb.currentData() is not None)
        self.recalibrate()

    def recalibrate(self):
        """Recalcula los ajustes únicos de la sesión (y el aviso «Revisar LUT») sin bloquear la ventana."""
        if not any(p.thumb for p in self.photos) or self.worker:
            return self.show_preview() if hasattr(self, "preview") else None
        saved, self.session_extra = self.session_extra, {}
        st = self.settings()
        self.session_extra = saved
        self.status.setText("Recalculando la sesión con el nuevo ajuste…")

        def job():
            extra = E.calibrate_session(self.photos, st)
            extra["_cube"] = st.style.get("cube") or ""
            self.recalibrated.emit(extra)

        threading.Thread(target=job, daemon=True).start()

    def on_recalibrated(self, extra):
        self.session_extra = extra
        n = sum(1 for p in self.photos if E.LUT_WARN in p.warn)
        self.status.setText("Sesión recalculada." + (f" {n} fotos a revisar con el LUT (filtro «Avisos»)." if n else ""))
        self.refresh_counts()
        self.apply_filter()
        self.grid.viewport().update()
        if self.grid.currentRow() >= 0:
            self.on_select(self.grid.currentRow())

    def load_models(self):
        """Carga (y la primera vez descarga) los modelos de IA en segundo plano: ojos cerrados (3,6 MB)
        y tipo de evento (89 MB). Si no hay red, la app usa los métodos sin IA."""
        model_dir = os.path.join(CFG_DIR, "modelos")

        def prog(a, b):
            if b:
                self.models_status.emit(f"Descargando el modelo de IA de tipo de evento ({a * 100 // b} %)…")

        def job():
            ensure_extras()
            E.setup_eyes(model_dir)
            import masks as M
            M.configure(model_dir)
            E.setup_scene_ai(model_dir, prog)
            bits = ["ojos cerrados" if E.EYES["ready"] else "", "tipo de evento" if E.AI["ready"] else ""]
            bits = [b for b in bits if b]
            self.models_status.emit("IA lista: " + " y ".join(bits) + "." if bits else
                                    "IA no disponible (sin conexión la primera vez): se usan los métodos clásicos.")

        threading.Thread(target=job, daemon=True).start()

    def on_style_changed(self, name):
        if name == S.AUTO:
            if self.detected:
                self.style_info.setText(f"Detectado: {self.detected[0]} — {S.describe(self.detected[1])}")
            else:
                self.style_info.setText("Se detecta al analizar (boda, fiesta, discoteca, deporte…). "
                                        "Si lo sabes, elígelo tú.")
        else:
            self.style_info.setText(S.describe(S.all_styles(CFG_DIR).get(name, S.BASE)))
        if hasattr(self, "lut_cb"):
            self.reload_luts()
            if not self._lut_loading:
                self.recalibrate()

    def on_scene(self, name, params):
        self.detected = (name, params)
        self.style_info.setText(f"Detectado: {name} — {S.describe(params)}")
        if self.style_cb.currentText() == S.AUTO:
            self.reload_luts()

    def learn_style(self):
        dlg = LearnDialog(self)
        dlg.exec()
        if dlg.result_name:
            self.reload_styles(dlg.result_name)

    # ------------------------------------------------------------- nube
    def cloud_ui(self):
        k = self.cloud.currentIndex()
        for w in (self.cloud_folder, self.cloud_btn, self.c_up_orig):
            w.setVisible(k > 0)
        if k == 1:
            self.cloud_folder.setPlaceholderText("Enlace de la carpeta de Google Drive")
            self.cloud_btn.setText("Drive conectado ✓ (desconectar)" if self.drive.connected()
                                   else "Conectar Google Drive…")
        elif k == 2:
            self.cloud_folder.setPlaceholderText("Carpeta que Amazon Photos (u otra app) sube sola")
            self.cloud_btn.setText("Elegir carpeta…")

    def cloud_action(self):
        if self.cloud.currentIndex() == 2:
            return self.pick(self.cloud_folder)
        try:
            if self.drive.connected():
                self.drive.disconnect()
            else:
                if not self.drive.has_client():
                    QMessageBox.information(self, APP, "La primera vez hace falta el archivo de credenciales de "
                                            "Google (client_secret.json, tipo «App de escritorio»). "
                                            "Las instrucciones están en el README del proyecto.")
                    f, _ = QFileDialog.getOpenFileName(self, "client_secret.json", "", "JSON (*.json)")
                    if not f:
                        return
                    self.drive.install_client(f)
                self.status.setText("Autoriza el acceso en el navegador que se acaba de abrir…")
                QApplication.processEvents()
                self.drive.connect()
                self.status.setText("Google Drive conectado.")
        except Exception as e:
            QMessageBox.warning(self, APP, str(e))
        self.cloud_ui()

    # ---------------------------------------------------------- rejilla y vista previa
    def thumb_pixmap(self, ph: E.Photo):
        if not ph.thumb:
            return None
        key = id(ph)
        pm = self._thumbs.get(key)
        if pm is None:
            pm = QPixmap()
            pm.loadFromData(QByteArray(ph.thumb))
            pm = pm.scaled(196, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._thumbs[key] = pm
            if len(self._thumbs) > THUMB_CACHE:
                self._thumbs.popitem(last=False)
        else:
            self._thumbs.move_to_end(key)
        return pm

    def visible(self, ph: E.Photo) -> bool:
        return {"all": True, "keep": ph.keep, "drop": not ph.keep and bool(ph.thumb or ph.error),
                "warn": bool(ph.warn)}[self.filter]

    def set_filter(self, key):
        self.filter = key
        self.apply_filter()

    def apply_filter(self):
        for row in range(self.grid.count()):
            it = self.grid.item(row)
            it.setHidden(not self.visible(self.photos[it.data(ROLE_IDX)]))

    def fill_grid(self):
        self._thumbs.clear()
        self.grid.clear()
        for i, ph in enumerate(self.photos):
            it = QListWidgetItem()
            it.setData(ROLE_IDX, i)
            it.setSizeHint(QSize(206, 178))
            it.setToolTip(os.path.basename(ph.path))
            self.grid.addItem(it)
        self.refresh_counts()

    def refresh_counts(self):
        done = [p for p in self.photos if p.thumb or p.error]
        keep = sum(p.keep for p in done)
        self.chip_keep.setText(f"{keep} para editar")
        self.chip_drop.setText(f"{len(done) - keep} descartadas")
        self.chip_warn.setText(f"{sum(1 for p in done if p.warn)} avisos")
        for c in (self.chip_keep, self.chip_drop, self.chip_warn):
            c.setVisible(bool(done))

    def on_select(self, row):
        if row < 0 or row >= self.grid.count():
            return
        self.cur = self.photos[self.grid.item(row).data(ROLE_IDX)]
        ph = self.cur
        self.p_title.setText(ph.name)
        bits = []
        if ph.iso:
            bits.append(f"ISO {ph.iso}")
        if ph.thumb:
            bits.append(f"{ph.faces} cara(s)")
            bits.append(f"nitidez {ph.sharp:.3f}")
        state = ph.error or ph.reason or ("Para editar" if ph.thumb else "Sin analizar")
        info = " · ".join(bits)
        self.p_info.setText(f"{state}{'  —  ' + info if info else ''}" + (f"\n⚠ {ph.warn}" if ph.warn else ""))
        for w in (self.p_keep, self.p_ev, self.p_ang, self.p_fmt, self.p_lut, self.p_bw, self.p_look):
            w.blockSignals(True)
        self.p_look.setCurrentText(ph.look or "Como la sesión")
        self.p_bw.setCurrentText(ph.bw_style or "Como la sesión")
        has_lut = self.lut_cb.currentData() is not None
        self.p_lut.setEnabled(has_lut)
        self.p_lut.setValue(int(round(100 * (ph.lut_strength if ph.lut_strength is not None
                                             else self.lut_k.value() / 100))))
        self.p_keep.setChecked(ph.keep)
        self.p_ev.setValue(ph.ev)
        self.p_ang.setValue(ph.angle)
        self.p_fmt.clear()
        self.p_fmt.addItems(list(ph.crops) or ["Original"])
        self.p_fmt.setCurrentText(ph.best)
        for w in (self.p_keep, self.p_ev, self.p_ang, self.p_fmt, self.p_lut, self.p_bw, self.p_look):
            w.blockSignals(False)
        self.p_card.setEnabled(bool(ph.thumb) and not ph.error and not self.worker)
        self.show_preview()

    def show_preview(self):
        ph = self.cur
        if not ph or not (ph.preview or ph.preview_path):
            pm = self.thumb_pixmap(ph) if ph else None
            return self.preview.set_image(pm, None)
        pm = np_to_pixmap(render_preview(ph, self.settings().style))
        before = np_to_pixmap(render_before(ph)) if self.preview.cmp.active else None
        self.preview.set_image(pm, ph.crops.get(ph.best), dim=not ph.keep, before=before)
        style = self.settings().style
        self.preview.zoom.reset(key=(ph.path, round(ph.ev, 2), round(ph.angle, 2), ph.look, ph.bw_style,
                                     str(sorted((k, str(v)) for k, v in style.items() if k != "lut"))[:400]),
                                full_size=E.full_size(ph.path))
        self.preview.zoom.provider = lambda box, ph=ph: self.zone_tile(ph, box, False)
        self.preview.zoom.schedule()

    def zone_tile(self, ph, box_rot, bw_view: bool):
        """Zona visible a resolución completa con el revelado real de Events (para el zoom > vista previa)."""
        if getattr(self, "_full", None) is None or self._full[0] != ph.path:
            self._full = (ph.path, E.load_full(ph))
        st = self.settings()
        stats = None
        if (st.style or {}).get("target_q") is not None:  # estilo aprendido: medidas de la foto entera
            stats = {}
            E.develop_lin(self._full[1][::6, ::6], ph, st, stats)

        def dev(part):
            out = E.develop_lin(part, ph, st, stats)
            if bw_view and not st.style.get("bw"):
                bws = E.photo_bw_style(ph, st)
                if bws != E.BW_NONE:
                    out = E.bw_editorial(out, seed=ph.dhash & 0xFFFFFFFF, style=bws)
            return out

        return Z.region(self._full[1], ph.angle, box_rot, dev, E._safe_rect)

    def set_compare(self, mode):
        self.preview.cmp.mode = mode
        self.cmp_bar.set_mode(mode)
        self.show_preview()

    def on_photo_look(self, text):
        if self.cur:
            self.cur.look = "" if text == "Como la sesión" else text
            self.show_preview()
            self.grid.viewport().update()

    def on_photo_bw(self, text):
        if self.cur:
            self.cur.bw_style = "" if text == "Como la sesión" else text

    def on_photo_lut(self, v):
        ph = self.cur
        if not ph or not ph.thumb:
            return
        ph.lut_strength = None if v == self.lut_k.value() else v / 100
        ph.warn = " · ".join(w for w in ph.warn.split(" · ") if w and not w.startswith(E.LUT_WARN))
        self.show_preview()
        self.grid.viewport().update()

    def on_photo_edit(self, *_):
        ph = self.cur
        if not ph or not ph.thumb:
            return
        ph.ev = round(self.p_ev.value(), 2)
        ph.angle = round(self.p_ang.value(), 2)
        if self.p_fmt.currentText():
            ph.best = self.p_fmt.currentText()
        self.show_preview()
        self.grid.viewport().update()

    def set_keep(self, on: bool):
        ph = self.cur
        if not ph or ph.error or not ph.thumb or self.worker:
            return
        ph.keep = on
        ph.reason = "" if on else (ph.reason or "Descartada a mano")
        self.p_keep.blockSignals(True)
        self.p_keep.setChecked(on)
        self.p_keep.blockSignals(False)
        self.refresh_counts()
        self.on_select(self.grid.currentRow())
        self.grid.viewport().update()
        self.b_ex.setEnabled(any(p.keep for p in self.photos))

    # ---------------------------------------------------------- acciones
    def busy(self, on: bool):
        self.b_an.setEnabled(not on)
        self.b_auto.setEnabled(not on)
        self.b_rev.setEnabled(not on and any(p.thumb for p in self.photos))
        self.b_ex.setEnabled(not on and any(p.keep and p.thumb for p in self.photos))
        self.b_ca.setVisible(on)
        self.b_open.setVisible(not on and bool(self.src.text().strip()))
        self.p_card.setEnabled(not on and bool(self.cur and self.cur.thumb))

    def start_analyze(self, auto: bool = False):
        src = self.src.text().strip()
        if not os.path.isdir(src):
            return QMessageBox.warning(self, APP, "Elige primero la carpeta de la sesión.")
        self.photos = E.list_photos(src)
        if not self.photos:
            return QMessageBox.information(self, APP, "No hay fotos RAW ni JPG en esa carpeta.")
        self.save()
        self.h1.setText(os.path.basename(os.path.normpath(src)))
        self.sub.setText(f"{len(self.photos)} fotos · {src}")
        self.cur = None
        self.detected = None
        self.session_extra = {}
        self.on_style_changed(self.style_cb.currentText())
        cloud = self.cloud_params() if auto else None
        if auto and cloud is None:
            return
        self.fill_grid()
        self._review_after = bool(auto and self.c_review.isChecked())
        mode = "analyze" if (not auto or self._review_after) else "auto"
        self.run(Worker(mode, self.photos, self.settings(), src, self.out_dir(), cloud))

    def cloud_params(self):
        cloud = {"kind": self.cloud.currentIndex(), "folder": self.cloud_folder.text().strip(),
                 "originals": self.c_up_orig.isChecked()}
        try:
            if cloud["kind"] == 1:
                if not self.drive.connected():
                    raise gdrive.DriveError("Conecta Google Drive antes de exportar, o elige «No subir».")
                self.drive.check_folder(gdrive.folder_id_from(cloud["folder"]))
            if cloud["kind"] == 2 and not os.path.isdir(cloud["folder"]):
                raise gdrive.DriveError("Elige la carpeta sincronizada, o cambia el destino a «No subir».")
        except Exception as e:
            QMessageBox.warning(self, APP, str(e))
            return None
        return cloud

    def start_export(self, reviewed: bool = False):
        if self.c_review.isChecked() and not reviewed:
            return self.open_review()
        st = self.settings()
        cloud = self.cloud_params()
        if cloud is None:
            return
        self.save()
        import review
        w = Worker("export", self.photos, st, self.src.text().strip(), self.out_dir(), cloud)
        w.overrides = review.pending_studio_paths(self.src.text().strip())
        self.run(w)

    # ----------------------------------------------------- revisión
    def open_review(self, start: int = 0):
        import review
        import studio_engine as SE
        if not any(p.thumb for p in self.photos):
            return QMessageBox.information(self, APP, "Analiza primero la sesión.")
        style = self.settings().style
        overrides = review.pending_studio_paths(self.src.text().strip())

        def render_fn(ph, bw):
            return render_preview(ph, dict(style, bw=False, _no_look=True))

        def studio_fn(ph, bw):
            s = overrides[os.path.normcase(ph.path)]
            lin = E.load_linear(ph.path, half=True)
            f = 1600 / max(lin.shape[:2])
            if f < 1:
                import cv2
                lin = cv2.resize(lin, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            return SE.render(lin, dict(s, bw=False))

        dlg = review.ReviewDialog(self, render_fn, studio_fn,
                                  lambda ph: os.path.normcase(ph.path) in overrides, start,
                                  bw_style=self.bw_style_cb.currentText(), bw_on=self.c_bw.isChecked(),
                                  look=self.look_cb.currentText(), before_fn=render_before,
                                  tile_fn=self.zone_tile)
        dlg.exec()
        self.refresh_counts()
        self.apply_filter()
        self.grid.viewport().update()
        if self.grid.currentRow() >= 0:
            self.on_select(self.grid.currentRow())
        if dlg.action == "export":
            self.start_export(reviewed=True)
        elif dlg.action == "studio":
            self.request_studio.emit(dlg.studio_photo)

    def run(self, worker):
        st_cube = worker.st.style.get("cube") or ""
        if worker.mode in ("analyze", "auto"):
            E.prepare_detectors(worker.st.workers)  # en el hilo principal (ver engine.prepare_detectors)
        self.worker = worker
        # se guarda hasta que el hilo termine de verdad: destruir un QThread en marcha aborta el programa
        self._threads.add(worker)
        worker.finished.connect(lambda w=worker: self._threads.discard(w))
        worker.progress.connect(self.on_progress)
        worker.photo.connect(self.on_photo)
        worker.scene.connect(self.on_scene)
        worker.calibrated.connect(lambda d: setattr(self, "session_extra", dict(d, _cube=st_cube)))
        worker.done.connect(self.on_done)
        worker.failed.connect(self.on_failed)
        self.busy(True)
        worker.start()

    def do_cancel(self):
        if self.worker:
            self.worker.cancel = True
            self.status.setText("Cancelando al terminar las fotos en curso…")

    def on_progress(self, i, n, text):
        self.pb.setMaximum(n)
        self.pb.setValue(i)
        self.status.setText(text)

    def on_photo(self, i):
        self._thumbs.pop(id(self.photos[i]), None)
        self.grid.viewport().update()
        if self.grid.currentRow() < 0 and self.photos[i].thumb:
            self.grid.setCurrentRow(i)
        self.refresh_counts()

    def on_failed(self, msg):
        self.worker = None
        self.busy(False)
        QMessageBox.critical(self, APP, msg)

    def on_done(self, msg, link):
        self.worker = None
        if getattr(self, "_review_after", False):
            self._review_after = False
            self.busy(False)
            self.status.setText(msg + " Revisa y aprueba antes de exportar.")
            self.refresh_counts()
            self.grid.viewport().update()
            return self.open_review()
        self.pb.setMaximum(1)
        self.pb.setValue(1)
        self.status.setText(msg + (f"  Enlace para compartir: {link}" if link else ""))
        self.busy(False)
        self.refresh_counts()
        self.apply_filter()
        self.grid.viewport().update()
        if self.grid.currentRow() >= 0:
            self.on_select(self.grid.currentRow())
        if link:
            QApplication.clipboard().setText(link)
            QMessageBox.information(self, APP, f"{msg}\n\nEnlace para compartir (ya copiado al portapapeles):\n{link}")
        if self.pending_update and not self.worker:
            self.on_update_found(self.pending_update, False)

    # ----------------------------------------------------- actualización
    def check_update(self, manual: bool):
        if manual:
            self.b_upd.setText("Comprobando…")

        def job():
            info = updater.latest()
            if info and updater.is_newer(info["version"], __version__):
                self.update_found.emit(info, manual)
            elif manual:
                self.update_found.emit({}, True)

        threading.Thread(target=job, daemon=True).start()

    def on_update_found(self, info: dict, manual: bool):
        self.b_upd.setText("Buscar actualizaciones")
        if not info:
            return self.status.setText(f"Tienes la última versión ({__version__}).")
        self.pending_update = info
        mode = updater.mode()
        if mode == "dev":
            self.banner_text.setText(f"Hay una versión nueva ({info['version']}). Esta copia es de desarrollo: "
                                     "actualízala con «git pull».")
            self.b_apply.hide()
        else:
            self.banner_text.setText(f"<b>PhootFlow {info['version']}</b> está disponible (tienes la {__version__}). "
                                     "Se descarga, se comprueba y el programa se reinicia solo.")
            self.b_apply.show()
            self.b_apply.setEnabled(not self.worker)
        self.banner.show()

    def apply_update(self):
        info = self.pending_update
        if not info or self.worker:
            return
        try:
            self.b_apply.setEnabled(False)
            self.status.setText("Descargando la actualización…")

            def prog(a, b):
                self.on_progress(a, b, f"Descargando la actualización ({a * 100 // max(b, 1)} %)…")
                QApplication.processEvents()

            updater.apply(info, prog)
            self.save()
            QApplication.quit()
        except Exception as e:
            self.b_apply.setEnabled(True)
            QMessageBox.warning(self, APP, f"No se pudo actualizar: {e}")

    def closeEvent(self, e):
        if self.worker and QMessageBox.question(
                self, APP, "Hay un trabajo en curso. ¿Salir igualmente? Lo ya exportado se conserva.") != \
                QMessageBox.Yes:
            return e.ignore()
        if self.worker:
            self.worker.cancel = True
            self.worker.wait(60000)
        self.save()
        e.accept()


# ------------------------------------------------------------------ entornos: Events y Studio

ENV_EVENTS, ENV_STUDIO = "events", "studio"


class ModeDialog(QDialog):
    """Al abrir una carpeta: ¿en qué entorno la usamos?"""

    def __init__(self, parent, folder: str):
        super().__init__(parent)
        self.setWindowTitle("Abrir carpeta")
        self.choice = ""
        v = QVBoxLayout(self)
        v.setSpacing(12)
        t = QLabel(f"<b>{os.path.basename(os.path.normpath(folder))}</b>")
        t.setObjectName("h1")
        sub = QLabel("¿Cómo quieres trabajar con esta carpeta? Puedes cambiar de entorno cuando quieras.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(sub)
        row = QHBoxLayout()
        row.setSpacing(12)
        for key, title, text in (
                (ENV_EVENTS, "Events", "Edición automática de la sesión completa: selección, revelado uniforme, "
                                       "formatos y exportación de cientos de fotos de una vez."),
                (ENV_STUDIO, "Studio", "Edición foto a foto, como Lightroom: sliders de revelado, curva, color, "
                                       "antes/después, presets y LUT.")):
            b = QPushButton(f"{title}\n\n{text}")
            b.setObjectName("primary" if key == ENV_EVENTS else "")
            b.setMinimumSize(250, 140)
            b.setStyleSheet("QPushButton { text-align: left; padding: 14px; font-weight: 500; }")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, k=key: (setattr(self, "choice", k), self.accept()))
            row.addWidget(b)
        v.addLayout(row)


class Shell(QMainWindow):
    """Ventana principal: cabecera con la marca, el interruptor de entorno y «Abrir carpeta»."""

    def __init__(self):
        super().__init__()
        import studio
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon(E.asset("icon.ico")))
        self.resize(1500, 920)
        self.s = QSettings(APP, APP)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # cabecera como la placa de identidad + selector de módulos de Lightroom
        head = QFrame()
        head.setObjectName("topbar")
        head.setFixedHeight(48)
        hh = QHBoxLayout(head)
        hh.setContentsMargins(16, 6, 18, 6)
        mark = QLabel()
        mark.setPixmap(QPixmap(E.asset("mark.png")).scaledToHeight(24, Qt.SmoothTransformation))
        word = QLabel()
        word.setPixmap(QPixmap(E.asset("wordmark.png")).scaledToHeight(15, Qt.SmoothTransformation))
        hh.addWidget(mark)
        hh.addSpacing(8)
        hh.addWidget(word)
        hh.addSpacing(22)
        self.folder_lbl = QLabel("")
        self.folder_lbl.setObjectName("meta")
        hh.addWidget(self.folder_lbl, 1)
        b_open = button("Abrir carpeta…", "ghost", "Elige la sesión y en qué entorno trabajarla (Ctrl+O)")
        b_open.clicked.connect(self.open_folder)
        hh.addWidget(b_open)
        hh.addSpacing(18)
        self.env_btn = {}
        group = QButtonGroup(self)
        for i, (key, text, tip) in enumerate(((ENV_EVENTS, "Events", "Edición automática de sesiones completas (Ctrl+Tab)"),
                                              (ENV_STUDIO, "Studio", "Edición foto a foto, como Lightroom (Ctrl+Tab)"))):
            if i:
                sep = QLabel("|")
                sep.setObjectName("moduleSep")
                hh.addWidget(sep)
            b = button(text, "module", tip)
            b.setCheckable(True)
            b.clicked.connect(lambda _, k=key: self.switch(k))
            group.addButton(b)
            hh.addWidget(b)
            self.env_btn[key] = b
        v.addWidget(head)

        from PySide6.QtWidgets import QStackedWidget
        self.stack = QStackedWidget()
        self.events = Main()
        self.events.brand.hide()  # la marca ya está en la cabecera
        self.studio = studio.StudioView(CFG_DIR, self.events_photo)
        self.stack.addWidget(self.events)
        self.stack.addWidget(self.studio)
        self.events.request_studio.connect(self.edit_in_studio)
        v.addWidget(self.stack, 1)
        QShortcut(QKeySequence("Ctrl+O"), self, self.open_folder)
        QShortcut(QKeySequence("Ctrl+Tab"), self, lambda: self.switch(
            ENV_STUDIO if self.env == ENV_EVENTS else ENV_EVENTS))
        self.folder = self.events.src.text().strip()
        self.env = ""
        self.switch(self.s.value("env", ENV_EVENTS))

    def events_photo(self, path: str):
        for p in self.events.photos:
            if os.path.normcase(p.path) == os.path.normcase(path):
                return p
        return None

    def open_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Elegir carpeta de la sesión", self.folder)
        if not d:
            return
        d = os.path.normpath(d)
        dlg = ModeDialog(self, d)
        if dlg.exec() != QDialog.Accepted or not dlg.choice:
            return
        self.folder = d
        self.events.src.setText(d)
        self.events.save()
        if dlg.choice == ENV_STUDIO:
            self.studio.open_folder(d)
        self.switch(dlg.choice)

    def switch(self, env: str):
        """Cambia de entorno conservando la carpeta: lo de un lado se ve en el otro."""
        env = env if env in (ENV_EVENTS, ENV_STUDIO) else ENV_EVENTS
        folder = self.events.src.text().strip() or self.folder
        if env == ENV_STUDIO and folder and os.path.isdir(folder):
            self.studio.open_folder(folder)  # no hace nada si ya está abierta
        if env == ENV_EVENTS and self.studio.folder and not self.events.src.text().strip():
            self.events.src.setText(self.studio.folder)
        self.env = env
        self.stack.setCurrentWidget(self.events if env == ENV_EVENTS else self.studio)
        self.env_btn[env].setChecked(True)
        self.folder = folder
        self.folder_lbl.setText(os.path.basename(folder) if folder else "")
        self.s.setValue("env", env)

    def edit_in_studio(self, ph):
        """Desde la revisión: retocar esta foto en Studio partiendo de la edición de Events."""
        import studio_engine as SE
        style = self.events.settings().style
        init = SE.from_style(SE.auto_settings(ph), style)
        if style.get("cube"):
            init.update(lut=style["cube"], lut_strength=int(100 * float(style.get("cube_strength", 1.0))))
        self.switch(ENV_STUDIO)
        self.studio.edit_for_events(ph.path, init, lambda: self.back_to_review(ph, True),
                                    lambda: self.back_to_review(ph, False))

    def back_to_review(self, ph, use: bool):
        if use:
            ph.keep, ph.reviewed, ph.reason = True, True, ""
        self.switch(ENV_EVENTS)
        photos = [p for p in self.events.photos if p.thumb and not p.error]
        i = photos.index(ph) if ph in photos else 0
        # si se aprobó, se sigue con la siguiente; si se volvió sin cambios, se queda en esa
        self.events.open_review(min(i + 1, len(photos) - 1) if use else i)

    def closeEvent(self, e):
        self.events.closeEvent(e)
        if e.isAccepted():
            self.studio.shutdown()


# ------------------------------------------------------------------ arranque

def make_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setOrganizationName(APP)
    app.setWindowIcon(QIcon(E.asset("icon.ico")))
    app.setStyle("Fusion")
    check = os.path.join(tempfile.gettempdir(), "phootflow_check.svg")
    try:
        with open(check, "wb") as f:
            f.write(T.CHECK_SVG)
    except OSError:
        pass
    app.setStyleSheet(T.QSS.replace(":/check.svg", check.replace("\\", "/")))
    return app


_SELFTEST_WIN = None
_SELFTEST_SHELL = None


def selftest() -> int:
    """Comprobación que ejecuta el compilado en GitHub antes de publicar una versión."""
    log = os.path.join(tempfile.gettempdir(), "phootflow_selftest.log")
    try:
        import cv2
        import rawpy
        from PIL import Image
        d = tempfile.mkdtemp(prefix="phootflow_")
        rng = np.random.default_rng(1)
        for k in range(2):
            a = np.full((900, 600, 3), 18, np.uint8)
            cv2.rectangle(a, (150, 250 + 40 * k), (450, 700), (200, 190, 170), -1)
            for x in range(40, 600, 70):
                cv2.line(a, (x, 0), (x + 16, 899), (90, 90, 90), 3)
            a = np.clip(a + rng.normal(0, 4, a.shape), 0, 255).astype(np.uint8)
            Image.fromarray(a).save(os.path.join(d, f"t{k}.jpg"), quality=92)
        st = E.Settings(ratios=["4:5", "16:9"], sizes=["max", "1080"], bw=True)
        ps = E.list_photos(d)
        E.analyze_many(ps, st, 2)
        assert len(ps) == 2 and not any(p.error for p in ps), [p.error for p in ps]
        assert E._detector("yunet") is not False, "falta el modelo YuNet"
        assert not E._detector("eye").empty()
        # la IA tiene que viajar dentro del .exe (los modelos se descargan al usarla; aquí solo los paquetes)
        import eyes
        import onnxruntime  # noqa: F401
        import scene_ai  # noqa: F401
        eyes._mediapipe()
        assert os.path.exists(E.asset("text_embeddings.npy"))
        import luts as LL
        cube = os.path.join(d, "t.cube")
        LL.write_cube(cube, 9, lambda x: x)
        assert abs(float(LL.apply(np.full((4, 4, 3), 0.5, np.float32), cube).mean()) - 0.5) < 0.01
        E.cull(ps, st)
        for p in ps:
            p.keep = True
        out = os.path.join(d, "Editadas")
        n = E.export_many(ps, st, out, __version__)
        assert n >= 10, n  # color (4 formatos x 2 tamaños… según caso) + B/N
        assert os.path.getsize(E.archive_originals(ps, out)) > 0
        E.write_report(ps, out)
        global _SELFTEST_WIN
        make_app()
        m = _SELFTEST_WIN = Main()
        m.photos = ps
        m.fill_grid()
        m.grid.setCurrentRow(0)
        assert m.preview.pm is not None
        # los dos entornos y el interruptor
        global _SELFTEST_SHELL
        sh = _SELFTEST_SHELL = Shell()
        sh.events.src.setText(d)
        sh.switch(ENV_STUDIO)
        assert sh.studio.items and sh.stack.currentWidget() is sh.studio
        import studio_engine as SE
        lin = E.load_linear(sh.studio.items[0].path, half=True)
        assert SE.render(lin, SE.new_settings(exposure=0.5, clarity=20, hsl_orange_s=10)).shape[2] == 3
        sh.switch(ENV_EVENTS)
        assert sh.stack.currentWidget() is sh.events
        # licencias: sin red ni licencia (el build no tiene clave); solo que el módulo y sus diálogos cargan
        assert LIC.decode_token("x.y") is None and len(LIC.fingerprint()) == 32
        LIC._dialog_classes()[0](LIC.Estado(False, "sin_licencia", "prueba")).close()
        with open(log, "w", encoding="utf-8") as f:
            f.write(f"OK {__version__} libraw {rawpy.libraw_version} archivos {n}\n")
        return 0
    except Exception:
        with open(log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        return 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    migrate_old_config()
    app = make_app()
    if not LIC.ensure():
        sys.exit(0)
    m = Shell()
    m.show()
    LIC.watch(m)
    code = app.exec()
    del m
    sys.exit(code)


if __name__ == "__main__":
    main()
