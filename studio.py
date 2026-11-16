"""PhootFlow Studio: edición foto a foto tipo Lightroom (biblioteca, revelado en vivo, historial, exportar)."""
from __future__ import annotations

import copy
import io
import os
import queue
import threading
import time
from collections import OrderedDict

import cv2
import numpy as np
from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListView, QListWidget,
                               QListWidgetItem, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QSpinBox, QStyle, QStyledItemDelegate, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

import engine as E
import luts as L
import studio_engine as SE
import theme as T

DRAFT_PX, FULL_PX = 860, 1600
ROLE = Qt.UserRole + 7


def _pixmap(rgb01: np.ndarray) -> QPixmap:
    a = np.ascontiguousarray((np.clip(rgb01, 0, 1) * 255 + 0.5).astype(np.uint8))
    h, w = a.shape[:2]
    return QPixmap.fromImage(QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy())


def _btn(text, kind="", tip=""):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    return b


def quick_thumb(path: str, size: int = 320) -> bytes:
    """Miniatura rápida: el JPEG que lleva el RAW (o el propio JPG), reducida."""
    from PIL import Image, ImageOps
    try:
        if E.is_raw(path) and E.rawpy is not None:
            with E.rawpy.imread(path) as r:
                th = r.extract_thumb()
            im = Image.open(io.BytesIO(th.data)) if th.format == E.rawpy.ThumbFormat.JPEG else \
                Image.fromarray(th.data)
        else:
            im = Image.open(path)
        im.draft("RGB", (size * 2, size * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=82)
        return b.getvalue()
    except Exception:
        return b""


class Item:
    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.thumb = b""
        self.settings: dict | None = None
        self.history: list[dict] = []
        self.redo: list[dict] = []


# ------------------------------------------------------------------ piezas visuales

class FilmDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self, *_):
        return QSize(210, 150)

    def paint(self, p: QPainter, opt, idx):
        it: Item = self.view.items[idx.data(ROLE)]
        r = opt.rect.adjusted(4, 4, -4, -4)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        sel = bool(opt.state & QStyle.State_Selected)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.SURFACE_2))
        p.drawRoundedRect(r, 8, 8)
        pm = self.view.thumb_pm(it)
        img = r.adjusted(5, 5, -5, -24)
        if pm:
            s = pm.size().scaled(img.size(), Qt.KeepAspectRatio)
            tr = QRectF(img.x() + (img.width() - s.width()) / 2, img.y() + (img.height() - s.height()) / 2,
                        s.width(), s.height())
            p.drawPixmap(tr.toRect(), pm)
            st = it.settings or {}
            if st.get("flag") == -1:
                p.fillRect(tr, QColor(5, 7, 15, 150))
        st = it.settings or {}
        f = QFont(p.font())
        f.setPointSizeF(8)
        p.setFont(f)
        foot = r.adjusted(8, r.height() - 22, -8, -4)
        p.setPen(QColor(T.MUTED))
        p.drawText(foot, Qt.AlignLeft | Qt.AlignVCenter, it.name)
        stars = "★" * int(st.get("rating", 0))
        if st.get("flag") == 1:
            stars = "⚑ " + stars
        elif st.get("flag") == -1:
            stars = "✕ " + stars
        p.setPen(QColor(T.WARN if st.get("flag", 0) >= 0 else T.DANGER))
        p.drawText(foot, Qt.AlignRight | Qt.AlignVCenter, stars)
        if it.settings and it.settings != SE.DEFAULTS:
            p.setBrush(QColor(T.ACCENT))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(img.right() - 10, img.y() + 4, 7, 7))  # editada
        if sel:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(T.ACCENT), 2))
            p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), 8, 8)
        p.restore()


class Canvas(QWidget):
    """Foto grande con antes/después y marco de recorte."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pm = self.before = None
        self.show_before = False
        self.msg = "Abre una carpeta y elige una foto"

    def set(self, pm=None, before=None, msg=""):
        if pm is not None:
            self.pm = pm
        if before is not None:
            self.before = before
        self.msg = msg
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(T.BG))
        pm = self.before if (self.show_before and self.before is not None) else self.pm
        if pm is None:
            p.setPen(QColor(T.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self.msg)
            return
        area = self.rect().adjusted(16, 16, -16, -16)
        s = pm.size().scaled(area.size(), Qt.KeepAspectRatio)
        r = QRectF(area.x() + (area.width() - s.width()) / 2, area.y() + (area.height() - s.height()) / 2,
                   s.width(), s.height())
        p.drawPixmap(r.toRect(), pm)
        if self.show_before:
            p.setPen(QColor(T.TEXT))
            p.fillRect(QRectF(r.x() + 8, r.y() + 8, 70, 22), QColor(5, 7, 15, 180))
            p.drawText(QRectF(r.x() + 8, r.y() + 8, 70, 22), Qt.AlignCenter, "Antes")
        if self.msg:
            p.setPen(QColor(T.MUTED))
            p.drawText(self.rect().adjusted(0, 0, -20, -6), Qt.AlignRight | Qt.AlignBottom, self.msg)


class Histogram(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(82)
        self.h = None

    def set(self, rgb01):
        small = rgb01[::4, ::4]
        self.h = [np.histogram(small[..., c], 64, (0, 1))[0].astype(np.float32) for c in range(3)]
        m = max(float(np.percentile(np.concatenate(self.h), 98)), 1.0)
        self.h = [np.clip(x / m, 0, 1) for x in self.h]
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.BG))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        if not self.h:
            return
        w, h = self.width() - 12, self.height() - 10
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        for c, col in enumerate((QColor(255, 60, 90, 150), QColor(60, 220, 120, 150), QColor(70, 120, 255, 150))):
            path = QPainterPath()
            path.moveTo(6, 5 + h)
            for i, v in enumerate(self.h[c]):
                path.lineTo(6 + w * i / 63, 5 + h - h * float(v))
            path.lineTo(6 + w, 5 + h)
            p.setBrush(col)
            p.drawPath(path)


class SliderRow(QWidget):
    """Etiqueta + slider + valor. Doble clic en la etiqueta: vuelve al neutro."""
    changed = Signal(str, float, bool)  # clave, valor, ¿definitivo? (False mientras se arrastra)

    def __init__(self, key, label, lo, hi, step, neutral):
        super().__init__()
        self.key, self.neutral, self.step = key, neutral, step
        self.k = int(round(1 / step)) if step < 1 else 1
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(0)
        self.lab = QLabel(label)
        self.lab.setObjectName("muted")
        self.lab.setToolTip("Doble clic para restablecer")
        self.lab.mouseDoubleClickEvent = lambda e: self.set_value(self.neutral, emit=True)
        self.sl = QSlider(Qt.Horizontal)
        self.sl.setRange(int(lo * self.k), int(hi * self.k))
        self.sl.setPageStep(max(1, int(10 * self.k * step)))
        from PySide6.QtWidgets import QLineEdit
        self.lo, self.hi = lo, hi
        self.dec = 0 if step >= 1 else (2 if step < 0.1 else 1)
        self.val = QLineEdit()
        self.val.setFixedWidth(58)
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.val.setStyleSheet("QLineEdit { padding: 2px 6px; }")
        self.sl.setStyleSheet("QSlider::sub-page:horizontal { background: " + T.SURFACE_3 + "; }")
        g.addWidget(self.lab, 0, 0)
        g.addWidget(self.val, 0, 1, Qt.AlignRight)
        g.addWidget(self.sl, 1, 0, 1, 2)
        self.sl.valueChanged.connect(self._from_slider)
        self.sl.sliderReleased.connect(lambda: self.changed.emit(self.key, self.value(), True))
        self.val.editingFinished.connect(self._from_text)
        self._mute = False
        self._show(neutral)

    def _show(self, v):
        self.val.setText(f"{float(v):+.{self.dec}f}" if self.lo < 0 and float(v) else f"{float(v):.{self.dec}f}")

    def _from_text(self):
        try:
            v = float(self.val.text().replace(",", ".").replace("+", ""))
        except ValueError:
            return self._show(self.value())
        self.set_value(min(self.hi, max(self.lo, v)), emit=True)

    def value(self) -> float:
        return self.sl.value() / self.k

    def _from_slider(self, v):
        if self._mute:
            return
        self._show(v / self.k)
        self.changed.emit(self.key, v / self.k, not self.sl.isSliderDown())

    def set_value(self, v, emit=False):
        self._mute = True
        self.sl.setValue(int(round(float(v) * self.k)))
        self._show(v)
        self._mute = False
        if emit:
            self.changed.emit(self.key, float(v), True)


class Section(QWidget):
    """Panel plegable como los de Lightroom."""

    def __init__(self, title, open_=True):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.head = QPushButton()
        self.head.setText(("▾  " if open_ else "▸  ") + title.upper())
        self.head.setCheckable(True)
        self.head.setChecked(open_)
        self.head.setStyleSheet(f"QPushButton {{ border: none; color: {T.MUTED}; font-weight: 600; "
                                f"font-size: 8.5pt; text-align: left; padding: 6px 0; background: transparent; }}")
        self.head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 0, 6)
        self.lay.setSpacing(8)
        self.body.setVisible(open_)
        self.head.toggled.connect(lambda on: (self.body.setVisible(on),
                                              self.head.setText(("▾  " if on else "▸  ") + title.upper())))
        v.addWidget(self.head)
        v.addWidget(self.body)


# ------------------------------------------------------------------ vista Studio

class StudioView(QWidget):
    rendered = Signal(int, object, object, bool)  # nº de petición, imagen, «antes», ¿borrador?
    thumbs_ready = Signal(int)
    proxy_ready = Signal(str)
    export_progress = Signal(int, int, str)

    def __init__(self, cfg_dir: str, events_lookup=None):
        super().__init__()
        self.cfg_dir = cfg_dir
        self.events_lookup = events_lookup or (lambda path: None)  # devuelve el Photo de Events si existe
        self.folder = ""
        self.items: list[Item] = []
        self.cur: Item | None = None
        self.clip: dict | None = None
        self._pm_cache: OrderedDict = OrderedDict()
        self._proxy: OrderedDict = OrderedDict()  # ruta -> (proxy lineal, antes sRGB)
        self._req = 0
        self._jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._render_loop, daemon=True).start()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_cur)
        self._full_timer = QTimer(self)
        self._full_timer.setSingleShot(True)
        self._full_timer.timeout.connect(lambda: self.request(draft=False))

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_library())
        h.addWidget(self._build_center(), 1)
        h.addWidget(self._build_develop())

        self.rendered.connect(self._on_rendered)
        self.thumbs_ready.connect(lambda i: self.film.viewport().update())
        self.proxy_ready.connect(self._on_proxy)
        self.export_progress.connect(self._on_export_progress)
        for k, fn in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+C", self.copy_settings),
                      ("Ctrl+Shift+V", self.paste_settings), ("\\", self.toggle_before),
                      ("P", lambda: self.set_flag(1)), ("X", lambda: self.set_flag(-1)), ("U", lambda: self.set_flag(0)),
                      ("Ctrl+E", self.export_dialog)):
            QShortcut(QKeySequence(k), self, fn)
        for n in range(6):
            QShortcut(QKeySequence(str(n)), self, lambda n=n: self.set_rating(n))

    # ---------------------------------------------------------- construcción
    def _build_library(self):
        w = QFrame()
        w.setObjectName("sidebar")
        w.setFixedWidth(240)
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        t = QLabel("BIBLIOTECA")
        t.setObjectName("cardTitle")
        self.lib_info = QLabel("Sin carpeta")
        self.lib_info.setObjectName("muted")
        self.lib_info.setWordWrap(True)
        self.filter = QComboBox()
        self.filter.addItems(["Todas", "Elegidas (P)", "Sin rechazadas", "Con 3★ o más", "Editadas"])
        self.filter.currentIndexChanged.connect(self.apply_filter)
        self.film = QListWidget()
        self.film.setObjectName("grid")
        self.film.setViewMode(QListView.IconMode)
        self.film.setFlow(QListView.LeftToRight)
        self.film.setWrapping(True)
        self.film.setResizeMode(QListView.Adjust)
        self.film.setUniformItemSizes(True)
        self.film.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.film.setItemDelegate(FilmDelegate(self))
        self.film.currentRowChanged.connect(self.select)
        v.addWidget(t)
        v.addWidget(self.lib_info)
        v.addWidget(self.filter)
        v.addWidget(self.film, 1)
        return w

    def _build_center(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)
        bar = QHBoxLayout()
        self.title = QLabel("Studio")
        self.title.setObjectName("h1")
        bar.addWidget(self.title, 1)
        self.b_before = _btn("Antes / después", "ghost", "Atajo: \\")
        self.b_before.setCheckable(True)
        self.b_before.toggled.connect(self._set_before)
        self.b_auto = _btn("Auto", "ghost", "Ajuste automático (exposición en caras, giro, recorte)")
        self.b_reset = _btn("Restablecer", "ghost")
        self.b_copy = _btn("Copiar", "ghost", "Copiar ajustes (Ctrl+Mayús+C)")
        self.b_paste = _btn("Pegar", "ghost", "Pegar ajustes en las seleccionadas (Ctrl+Mayús+V)")
        self.b_sync = _btn("Sincronizar", "ghost", "Aplicar los ajustes de esta foto a todas las seleccionadas")
        self.b_export = _btn("Exportar…", "primary", "Ctrl+E")
        self.b_auto.clicked.connect(self.auto)
        self.b_reset.clicked.connect(self.reset)
        self.b_copy.clicked.connect(self.copy_settings)
        self.b_paste.clicked.connect(self.paste_settings)
        self.b_sync.clicked.connect(self.sync_selected)
        self.b_export.clicked.connect(self.export_dialog)
        for b in (self.b_before, self.b_auto, self.b_reset, self.b_copy, self.b_paste, self.b_sync, self.b_export):
            bar.addWidget(b)
        v.addLayout(bar)
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bh = QHBoxLayout(self.banner)
        bh.setContentsMargins(12, 6, 6, 6)
        self.banner_txt = QLabel("")
        self.b_use = _btn("Usar en Events y volver", "primary")
        self.b_back = _btn("Volver sin cambios", "ghost")
        bh.addWidget(self.banner_txt, 1)
        bh.addWidget(self.b_back)
        bh.addWidget(self.b_use)
        self.banner.hide()
        self.b_use.clicked.connect(lambda: self._finish_events(True))
        self.b_back.clicked.connect(lambda: self._finish_events(False))
        self._events_cb = None
        v.addWidget(self.banner)
        self.canvas = Canvas()
        v.addWidget(self.canvas, 1)
        foot = QHBoxLayout()
        self.stars = []
        for n in range(1, 6):
            b = QToolButton()
            b.setText("☆")
            b.setStyleSheet(f"QToolButton {{ border: none; font-size: 15pt; color: {T.WARN}; background: transparent; }}")
            b.clicked.connect(lambda _, n=n: self.set_rating(0 if self._rating() == n else n))
            self.stars.append(b)
            foot.addWidget(b)
        self.flag_lbl = QLabel("")
        self.flag_lbl.setObjectName("muted")
        foot.addSpacing(12)
        foot.addWidget(self.flag_lbl)
        foot.addStretch(1)
        self.pb = QProgressBar()
        self.pb.setTextVisible(False)
        self.pb.setFixedWidth(220)
        self.pb.hide()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        foot.addWidget(self.status)
        foot.addWidget(self.pb)
        v.addLayout(foot)
        return w

    def _build_develop(self):
        outer = QFrame()
        outer.setObjectName("previewPane")
        outer.setFixedWidth(340)
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(12, 12, 6, 12)
        ov.setSpacing(8)
        self.hist = Histogram()
        ov.addWidget(self.hist)
        row = QHBoxLayout()
        self.preset_cb = QComboBox()
        self.preset_cb.setToolTip("Presets de Studio guardados por ti")
        self.preset_cb.activated.connect(self.apply_preset)
        b_save = _btn("Guardar", "small", "Guardar los ajustes actuales como preset")
        b_save.clicked.connect(self.save_preset)
        row.addWidget(self.preset_cb, 1)
        row.addWidget(b_save)
        ov.addLayout(row)
        row = QHBoxLayout()
        self.lut_cb = QComboBox()
        self.lut_cb.setToolTip("LUT (.cube) de todos los tipos de sesión")
        self.lut_cb.activated.connect(lambda _: self._set("lut", self.lut_cb.currentData() or "", True))
        self.bw_cb = QCheckBox("B/N")
        self.bw_cb.toggled.connect(lambda on: self._set("bw", on, True))
        row.addWidget(self.lut_cb, 1)
        row.addWidget(self.bw_cb)
        ov.addLayout(row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 8, 0)
        bv.setSpacing(4)
        self.rows: dict[str, SliderRow] = {}
        for i, (title, ctrls) in enumerate(SE.PANELS):
            sec = Section(title, open_=i < 2)
            for c in ctrls:
                r = SliderRow(*c)
                r.changed.connect(self._set)
                self.rows[c[0]] = r
                sec.lay.addWidget(r)
            if title == "Detalle":
                lut_k = SliderRow("lut_strength", "Intensidad del LUT", 0, 100, 1, 100)
                lut_k.changed.connect(self._set)
                self.rows["lut_strength"] = lut_k
                sec.lay.addWidget(lut_k)
            bv.addWidget(sec)
            if title == "Curva tonal":
                hsl = Section("Mezcla de color (HSL)", open_=False)
                tabs = QTabWidget()
                for p, name in (("h", "Tono"), ("s", "Saturación"), ("l", "Luminancia")):
                    page = QWidget()
                    pv = QVBoxLayout(page)
                    pv.setSpacing(6)
                    for b, label, _ in SE.HSL_BANDS:
                        r = SliderRow(f"hsl_{b}_{p}", label, -100, 100, 1, 0)
                        r.changed.connect(self._set)
                        self.rows[r.key] = r
                        pv.addWidget(r)
                    tabs.addTab(page, name)
                hsl.lay.addWidget(tabs)
                bv.addWidget(hsl)
        bv.addStretch(1)
        scroll.setWidget(body)
        ov.addWidget(scroll, 1)
        self.reload_presets()
        self.reload_luts()
        return outer

    # ---------------------------------------------------------- carpeta y miniaturas
    def open_folder(self, folder: str):
        if not os.path.isdir(folder) or os.path.normpath(folder) == os.path.normpath(self.folder or "~"):
            return
        self._save_cur()
        self.folder = folder
        files = [p.path for p in E.list_photos(folder)]
        self.items = [Item(p) for p in files]
        for it in self.items:
            it.settings = SE.load(folder, it.path)
        self._pm_cache.clear()
        self.film.clear()
        for i, it in enumerate(self.items):
            li = QListWidgetItem()
            li.setData(ROLE, i)
            li.setSizeHint(QSize(210, 150))
            self.film.addItem(li)
        self.lib_info.setText(f"{os.path.basename(folder)} · {len(self.items)} fotos")
        self.title.setText(os.path.basename(folder))
        self.cur = None
        self.canvas.set(msg="Elige una foto")
        threading.Thread(target=self._thumb_loop, args=(list(self.items),), daemon=True).start()
        if self.items:
            self.film.setCurrentRow(0)

    def _thumb_loop(self, items):
        for i, it in enumerate(items):
            if it not in self.items:
                return
            it.thumb = quick_thumb(it.path)
            if i % 8 == 0 or i == len(items) - 1:
                self.thumbs_ready.emit(i)

    def thumb_pm(self, it: Item):
        if not it.thumb:
            return None
        pm = self._pm_cache.get(it.path)
        if pm is None:
            pm = QPixmap()
            pm.loadFromData(QByteArray(it.thumb))
            pm = pm.scaled(200, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._pm_cache[it.path] = pm
            if len(self._pm_cache) > 800:
                self._pm_cache.popitem(last=False)
        return pm

    def apply_filter(self):
        f = self.filter.currentIndex()
        for row in range(self.film.count()):
            it = self.items[self.film.item(row).data(ROLE)]
            s = it.settings or SE.DEFAULTS
            show = [True, s.get("flag") == 1, s.get("flag") != -1, s.get("rating", 0) >= 3,
                    bool(it.settings) and it.settings != SE.DEFAULTS][f]
            self.film.item(row).setHidden(not show)

    # ---------------------------------------------------------- selección y proxy
    def select(self, row):
        if row < 0 or row >= self.film.count():
            return
        self._save_cur()
        self.cur = self.items[self.film.item(row).data(ROLE)]
        it = self.cur
        if it.settings is None:
            it.settings = self._initial_settings(it)
            it.needs_auto_ev = self.events_lookup(it.path) is None  # sin análisis de Events: exposición del proxy
        self.title.setText(it.name)
        self._load_controls()
        if it.path in self._proxy:
            self.request(draft=False)
        else:
            self.canvas.set(msg="Cargando…")
            threading.Thread(target=self._load_proxy, args=(it.path,), daemon=True).start()

    def _initial_settings(self, it: Item) -> dict:
        ph = self.events_lookup(it.path)
        return SE.auto_settings(ph) if ph is not None and ph.thumb else SE.new_settings()

    def _load_proxy(self, path):
        try:
            lin = E.load_linear(path, half=True)
            f = FULL_PX / max(lin.shape[:2])
            if f < 1:
                lin = cv2.resize(lin, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            before = SE.render(lin, SE.new_settings(), crop=False)
            self._proxy[path] = (lin.astype(np.float32), before)
            while len(self._proxy) > 6:
                self._proxy.popitem(last=False)
            self.proxy_ready.emit(path)
        except Exception as e:
            self.proxy_ready.emit("ERROR:" + f"{type(e).__name__}: {e}")

    def _on_proxy(self, path):
        if path.startswith("ERROR:"):
            return self.canvas.set(msg="No se pudo abrir: " + path[6:])
        if self.cur and self.cur.path == path:
            it = self.cur
            if getattr(it, "needs_auto_ev", False) and it.settings.get("exposure", 0) == 0:
                # primera vez que se abre: exposición automática (un RAW de discoteca sale 3–5 pasos oscuro)
                ev, _, _ = E.estimate_ev(self._proxy[path][0])
                it.settings["exposure"] = round(float(ev), 2)
                self.rows["exposure"].set_value(it.settings["exposure"])
            it.needs_auto_ev = False
            self.canvas.set(before=_pixmap(self._proxy[path][1]))
            self.request(draft=False)

    # ---------------------------------------------------------- render en segundo plano
    def request(self, draft: bool):
        if not self.cur or self.cur.path not in self._proxy:
            return
        self._req += 1
        self._jobs.put((self._req, self.cur.path, copy.deepcopy(self.cur.settings), draft))
        if draft:
            self._full_timer.start(260)

    def _render_loop(self):
        while True:
            job = self._jobs.get()
            while not self._jobs.empty():  # solo cuenta la última petición
                job = self._jobs.get()
            n, path, s, draft = job
            if path not in self._proxy:
                continue
            lin = self._proxy[path][0]
            if draft:
                f = DRAFT_PX / max(lin.shape[:2])
                lin = cv2.resize(lin, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else lin
                s = dict(s, nr_luma=0)  # la limpieza de grano solo en la vista definitiva
            t = time.time()
            try:
                img = SE.render(lin, s, seed=hash(path) & 0xFFFF)
            except Exception as e:
                self.rendered.emit(n, None, f"{type(e).__name__}: {e}", draft)
                continue
            self.rendered.emit(n, img, time.time() - t, draft)

    def _on_rendered(self, n, img, info, draft):
        if draft and n < self._req:  # llegó tarde: ya hay una petición más nueva en camino
            return
        if img is None:
            return self.canvas.set(msg=f"Error al revelar: {info}")
        self.canvas.set(pm=_pixmap(img), msg="" if not draft else "vista rápida")
        self.hist.set(img)

    # ---------------------------------------------------------- ajustes
    def _load_controls(self):
        s = self.cur.settings
        for k, r in self.rows.items():
            r.set_value(s.get(k, SE.DEFAULTS.get(k, 0)))
        self.bw_cb.blockSignals(True)
        self.bw_cb.setChecked(bool(s.get("bw")))
        self.bw_cb.blockSignals(False)
        i = self.lut_cb.findData(s.get("lut") or None)
        self.lut_cb.setCurrentIndex(max(0, i))
        self._show_rating()

    def _push_history(self):
        it = self.cur
        it.history.append(copy.deepcopy(it.settings))
        it.history = it.history[-100:]
        it.redo.clear()

    def _set(self, key, value, final=True):
        if not self.cur:
            return
        if final:
            self._push_history()
        self.cur.settings[key] = value
        self.request(draft=not final)
        self._save_timer.start(600)
        self.film.viewport().update()

    def _save_cur(self):
        if self.cur and self.folder and self.cur.settings is not None:
            SE.save(self.folder, self.cur.path, self.cur.settings)

    def undo(self):
        it = self.cur
        if it and it.history:
            it.redo.append(copy.deepcopy(it.settings))
            it.settings = it.history.pop()
            self._load_controls()
            self.request(draft=False)
            self._save_timer.start(300)

    def redo(self):
        it = self.cur
        if it and it.redo:
            it.history.append(copy.deepcopy(it.settings))
            it.settings = it.redo.pop()
            self._load_controls()
            self.request(draft=False)
            self._save_timer.start(300)

    def reset(self):
        if self.cur:
            self._push_history()
            keep = {k: self.cur.settings.get(k) for k in ("rating", "flag")}
            self.cur.settings = SE.new_settings(**keep)
            self._load_controls()
            self.request(draft=False)
            self._save_timer.start(300)

    def auto(self):
        if not self.cur:
            return
        ph = self.events_lookup(self.cur.path)
        if ph is None or not ph.thumb:
            self.status.setText("Analizando la foto…")
            ph = E.Photo(path=self.cur.path, name=self.cur.name)
            E.analyze(ph, E.Settings())
        self._push_history()
        keep = {k: self.cur.settings.get(k) for k in ("rating", "flag", "lut", "lut_strength", "bw")}
        self.cur.settings = dict(SE.auto_settings(ph), **keep)
        self._load_controls()
        self.request(draft=False)
        self._save_timer.start(300)
        self.status.setText("Ajuste automático aplicado.")

    def copy_settings(self):
        if self.cur:
            self.clip = {k: v for k, v in self.cur.settings.items() if k not in ("rating", "flag", "crop", "crop_ratio")}
            self.status.setText("Ajustes copiados.")

    def _targets(self):
        rows = [i.row() for i in self.film.selectedIndexes()]
        return [self.items[self.film.item(r).data(ROLE)] for r in rows] or ([self.cur] if self.cur else [])

    def paste_settings(self, src=None):
        src = src or self.clip
        if not src:
            return
        n = 0
        for it in self._targets():
            base = it.settings or SE.new_settings()
            it.history.append(copy.deepcopy(base))
            it.settings = dict(base, **copy.deepcopy(src))
            SE.save(self.folder, it.path, it.settings)
            n += 1
        if self.cur:
            self._load_controls()
            self.request(draft=False)
        self.film.viewport().update()
        self.status.setText(f"Ajustes aplicados a {n} foto(s).")

    def sync_selected(self):
        if self.cur:
            self.copy_settings()
            self.paste_settings()

    # ---------------------------------------------------------- valoración
    def _rating(self):
        return int((self.cur.settings or {}).get("rating", 0)) if self.cur else 0

    def _show_rating(self):
        n = self._rating()
        for i, b in enumerate(self.stars, start=1):
            b.setText("★" if i <= n else "☆")
        f = (self.cur.settings or {}).get("flag", 0) if self.cur else 0
        self.flag_lbl.setText({1: "⚑ Elegida", -1: "✕ Rechazada"}.get(f, ""))

    def set_rating(self, n):
        for it in self._targets():
            it.settings = it.settings or SE.new_settings()
            it.settings["rating"] = n
            SE.save(self.folder, it.path, it.settings)
        self._show_rating()
        self.film.viewport().update()

    def set_flag(self, f):
        for it in self._targets():
            it.settings = it.settings or SE.new_settings()
            it.settings["flag"] = f
            SE.save(self.folder, it.path, it.settings)
        self._show_rating()
        self.film.viewport().update()

    def toggle_before(self):
        self.b_before.toggle()

    def _set_before(self, on):
        self.canvas.show_before = on
        self.canvas.update()

    # ---------------------------------------------------------- presets y LUT
    def reload_presets(self):
        self.preset_cb.clear()
        self.preset_cb.addItem("Presets…", None)
        for name, s in SE.list_presets(self.cfg_dir).items():
            self.preset_cb.addItem(name, s)
        for name, s in SE.LIGHTCRAFT_PRESETS.items():  # integrados (LightCraft, Apache-2.0)
            self.preset_cb.addItem("LightCraft · " + name, s)

    def apply_preset(self, i):
        s = self.preset_cb.itemData(i)
        if s and self.cur:
            self._push_history()
            self.cur.settings = dict(self.cur.settings, **s)
            self._load_controls()
            self.request(draft=False)
            self._save_timer.start(300)
        self.preset_cb.setCurrentIndex(0)

    def save_preset(self):
        if not self.cur:
            return
        name, ok = QInputDialog.getText(self, "Guardar preset", "Nombre del preset:")
        if ok and name.strip():
            SE.save_preset(self.cfg_dir, name.strip(), self.cur.settings)
            self.reload_presets()
            self.status.setText(f"Preset «{name.strip()}» guardado.")

    def reload_luts(self):
        self.lut_cb.clear()
        self.lut_cb.addItem("Sin LUT", None)
        root = os.path.join(self.cfg_dir, "luts")
        if os.path.isdir(root):
            for kind in sorted(os.listdir(root)):
                for label, path in L.available(self.cfg_dir, kind) if kind != L.COMMON else []:
                    if not label.startswith("Común: "):
                        self.lut_cb.addItem(f"{kind}: {label}", path)
            for label, path in L.available(self.cfg_dir, L.COMMON):
                self.lut_cb.addItem(label, path)

    # ---------------------------------------------------------- exportar
    def export_dialog(self):
        targets = [it for it in self._targets() if it.settings]
        if not targets:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Exportar")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"Exportar {len(targets)} foto(s) seleccionada(s)"))
        size = QComboBox()
        size.addItems(["Original", "2048 px lado largo", "1080 px lado largo"])
        q = QSpinBox()
        q.setRange(70, 100)
        q.setValue(95)
        v.addWidget(size)
        row = QHBoxLayout()
        row.addWidget(QLabel("Calidad JPG"))
        row.addWidget(q)
        v.addLayout(row)
        go = _btn("Exportar", "primary")
        go.clicked.connect(dlg.accept)
        v.addWidget(go)
        if dlg.exec() != QDialog.Accepted:
            return
        self._save_cur()
        long_side = {0: None, 1: 2048, 2: 1080}[size.currentIndex()]
        out = os.path.join(self.folder, "Studio")
        quality = q.value()

        def job():
            for i, it in enumerate(targets):
                self.export_progress.emit(i, len(targets), f"Exportando {it.name}…")
                try:
                    SE.export(it.path, it.settings, os.path.join(out, it.name + ".jpg"), quality, long_side)
                except Exception as e:
                    self.export_progress.emit(i, len(targets), f"Error en {it.name}: {e}")
            self.export_progress.emit(len(targets), len(targets), f"Exportadas {len(targets)} en «Studio».")

        self.pb.show()
        threading.Thread(target=job, daemon=True).start()

    def _on_export_progress(self, i, n, text):
        self.pb.setMaximum(max(n, 1))
        self.pb.setValue(i)
        self.status.setText(text)
        if i >= n:
            QTimer.singleShot(2500, self.pb.hide)

    # ---------------------------------------------------------- ida y vuelta con Events
    def edit_for_events(self, path, initial: dict, on_use, on_back):
        """Abre `path` para retocarla y usarla en la exportación de Events."""
        idx = next((i for i, it in enumerate(self.items) if os.path.normcase(it.path) == os.path.normcase(path)), -1)
        if idx < 0:
            return
        it = self.items[idx]
        if not it.settings or not it.settings.get("_events_override"):
            it.settings = dict(initial)
            it.needs_auto_ev = False
        self._events_cb = (on_use, on_back)
        self.banner_txt.setText(f"<b>Editando {it.name} para Events.</b> Ajusta lo que quieras; al volver, se "
                                "exportará con esta edición.")
        self.banner.show()
        for row in range(self.film.count()):
            if self.film.item(row).data(ROLE) == idx:
                self.film.setCurrentRow(row)
                break
        self.select(self.film.currentRow())

    def _finish_events(self, use: bool):
        if not self._events_cb or not self.cur:
            return
        if use:
            self.cur.settings["_events_override"] = True
        self._save_cur()
        self.banner.hide()
        cb, self._events_cb = self._events_cb[0 if use else 1], None
        cb()

    def shutdown(self):
        self._save_cur()
