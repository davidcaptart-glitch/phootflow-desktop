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
import engine as E
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
    done = Signal(str, str)  # resumen, enlace
    failed = Signal(str)

    def __init__(self, mode, photos, st, src, out, cloud=None):
        super().__init__()
        self.mode, self.photos, self.st, self.src, self.out, self.cloud = mode, photos, st, src, out, cloud or {}
        self.cancel = False

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
        E.cull(self.photos, self.st)
        keep = sum(p.keep for p in self.photos)
        warn = sum(1 for p in self.photos if p.warn)
        return detected + f"Análisis: {keep} para editar, {n - keep} descartadas" + (f", {warn} con avisos." if warn else ".")

    def export(self, prefix: str = ""):
        keep = [p for p in self.photos if p.keep]
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


def _fmt_time(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60} min {sec % 60:02d} s" if sec >= 60 else f"{sec} s"


# ------------------------------------------------------------------ piezas de interfaz

def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 12, 14, 14)
    v.setSpacing(8)
    t = QLabel(title.upper())
    t.setObjectName("cardTitle")
    v.addWidget(t)
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


def render_preview(ph: E.Photo) -> np.ndarray:
    """Vista previa con la exposición y el giro actuales, partiendo de la generada en el análisis."""
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
        p.setBrush(QColor(T.SURFACE_2))
        p.drawRoundedRect(r, 10, 10)
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
            p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), 10, 10)
        p.restore()


class PreviewLabel(QLabel):
    """Imagen grande con el recorte del formato elegido marcado (lo de fuera, oscurecido)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("previewImage")
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pm, self.crop, self.dim = None, None, False

    def set_image(self, pm, crop, dim=False):
        self.pm, self.crop, self.dim = pm, crop, dim
        self.update()

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
        area = self.rect().adjusted(10, 10, -10, -10)
        s = self.pm.size().scaled(area.size(), Qt.KeepAspectRatio)
        r = QRectF(area.x() + (area.width() - s.width()) / 2, area.y() + (area.height() - s.height()) / 2,
                   s.width(), s.height())
        p.drawPixmap(r.toRect(), self.pm)
        if self.crop and self.crop != (0.0, 0.0, 1.0, 1.0):
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
        QShortcut(QKeySequence("Ctrl+O"), self, lambda: self.pick(self.src))
        self.upd_timer = QTimer(self)
        self.upd_timer.timeout.connect(lambda: self.check_update(False))
        self.upd_timer.start(UPDATE_EVERY_MS)
        QTimer.singleShot(1500, lambda: self.check_update(False))

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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(14, 6, 14, 14)
        v.setSpacing(12)
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
        cv.addWidget(b_learn)
        v.addWidget(c)
        self.detected: tuple[str, dict] | None = None
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
        cv.addWidget(labeled("Reducción de grano", self.nr))
        cv.addWidget(labeled("Compensar exposición", self.evc))
        cv.addWidget(self.straight)
        v.addWidget(c)

        # --- descarte
        c, cv = card("Selección automática")
        self.c_focus = QCheckBox("Desenfocadas o movidas")
        self.c_exp = QCheckBox("Exposición irrecuperable")
        self.c_dup = QCheckBox("Casi duplicadas (deja la más nítida)")
        self.c_move = QCheckBox("Al exportar, mover descartes a «Descartadas»")
        for w in (self.c_focus, self.c_exp, self.c_dup, self.c_move):
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
        for w in (self.c_exif, self.c_xmp, self.c_zip):
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
        self.b_auto.clicked.connect(lambda: self.start_analyze(True))
        self.b_ca.clicked.connect(self.do_cancel)
        self.b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_dir())))
        for b in (self.b_open, self.b_ca, self.b_an, self.b_ex, self.b_auto):
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
        v.addWidget(self.preview, 1)
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
        self.style_cb.setCurrentText(g("style", S.AUTO))
        self.c_zip.setChecked(b("zip", True))
        self.c_up_orig.setChecked(b("up_orig", True))
        self.q.setValue(int(g("quality", 95)))
        self.workers.setValue(int(g("workers", min(3, max(1, (os.cpu_count() or 2) // 4)))))
        self.cloud.setCurrentIndex(int(g("cloud", 0)))
        self.cloud_folder.setText(g("cloud_folder", ""))

    def save(self):
        st = self.settings()
        for k, v in [("src", self.src.text()), ("out", self.out.text()), ("nr", st.nr), ("sens", st.sensitivity),
                     ("ratios", ",".join(st.ratios)), ("sizes", ",".join(st.sizes)),
                     ("orig", st.export_original_ratio), ("exif", st.keep_exif), ("xmp", st.write_xmp), ("bw", st.bw),
                     ("style", self.style_cb.currentText()),
                     ("zip", st.archive_originals), ("up_orig", self.c_up_orig.isChecked()),
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
        return E.Settings(style=style, style_name=style_name,
            nr=self.nr.currentText(), straighten=self.straight.isChecked(), ev_comp=self.evc.value(),
            cull_focus=self.c_focus.isChecked(), cull_exposure=self.c_exp.isChecked(),
            cull_dupes=self.c_dup.isChecked(), sensitivity=self.sens.currentText(),
            ratios=ratios,
            export_original_ratio=self.r_orig.isChecked(),
            sizes=[k for k, c in self.size_cb.items() if c.isChecked()] or ["max"],
            quality=self.q.value(), move_discards=self.c_move.isChecked(),
            archive_originals=self.c_zip.isChecked(), keep_exif=self.c_exif.isChecked(),
            write_xmp=self.c_xmp.isChecked(), workers=self.workers.value(), bw=self.c_bw.isChecked(),
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

    def on_style_changed(self, name):
        if name == S.AUTO:
            if self.detected:
                self.style_info.setText(f"Detectado: {self.detected[0]} — {S.describe(self.detected[1])}")
            else:
                self.style_info.setText("Se detecta al analizar (boda, fiesta, discoteca, deporte…). "
                                        "Si lo sabes, elígelo tú.")
        else:
            self.style_info.setText(S.describe(S.all_styles(CFG_DIR).get(name, S.BASE)))

    def on_scene(self, name, params):
        self.detected = (name, params)
        self.on_style_changed(self.style_cb.currentText())

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
        for w in (self.p_keep, self.p_ev, self.p_ang, self.p_fmt):
            w.blockSignals(True)
        self.p_keep.setChecked(ph.keep)
        self.p_ev.setValue(ph.ev)
        self.p_ang.setValue(ph.angle)
        self.p_fmt.clear()
        self.p_fmt.addItems(list(ph.crops) or ["Original"])
        self.p_fmt.setCurrentText(ph.best)
        for w in (self.p_keep, self.p_ev, self.p_ang, self.p_fmt):
            w.blockSignals(False)
        self.p_card.setEnabled(bool(ph.thumb) and not ph.error and not self.worker)
        self.show_preview()

    def show_preview(self):
        ph = self.cur
        if not ph or not (ph.preview or ph.preview_path):
            pm = self.thumb_pixmap(ph) if ph else None
            return self.preview.set_image(pm, None)
        pm = np_to_pixmap(render_preview(ph))
        self.preview.set_image(pm, ph.crops.get(ph.best), dim=not ph.keep)

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
        self.on_style_changed(self.style_cb.currentText())
        cloud = self.cloud_params() if auto else None
        if auto and cloud is None:
            return
        self.fill_grid()
        self.run(Worker("auto" if auto else "analyze", self.photos, self.settings(), src, self.out_dir(), cloud))

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

    def start_export(self):
        st = self.settings()
        cloud = self.cloud_params()
        if cloud is None:
            return
        self.save()
        self.run(Worker("export", self.photos, st, self.src.text().strip(), self.out_dir(), cloud))

    def run(self, worker):
        if worker.mode in ("analyze", "auto"):
            E.prepare_detectors(worker.st.workers)  # en el hilo principal (ver engine.prepare_detectors)
        self.worker = worker
        # se guarda hasta que el hilo termine de verdad: destruir un QThread en marcha aborta el programa
        self._threads.add(worker)
        worker.finished.connect(lambda w=worker: self._threads.discard(w))
        worker.progress.connect(self.on_progress)
        worker.photo.connect(self.on_photo)
        worker.scene.connect(self.on_scene)
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
    m = Main()
    m.show()
    code = app.exec()
    del m
    sys.exit(code)


if __name__ == "__main__":
    main()
