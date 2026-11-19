"""PhootFlow Studio: edición foto a foto tipo Lightroom (biblioteca, revelado en vivo, historial, exportar)."""
from __future__ import annotations

import copy
import math
import io
import os
import queue
import threading
import time
from collections import OrderedDict

import cv2
import numpy as np
from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QMenu,
                               QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListView, QListWidget,
                               QListWidgetItem, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QSpinBox, QStyle, QStyledItemDelegate, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

import engine as E
import luts as L
import studio_engine as SE
import compare as CMP
import zoomview as Z
import cropview as CV
import icons as I
import masks as M
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


def _ibtn(icon: str, tip: str, checkable: bool = False, text: str = "") -> QPushButton:
    """Botón de icono (con texto opcional): menos letras en las barras, la explicación en el tooltip."""
    b = _btn(("  " + text) if text else "", "iconBtn" if not text else "ghost", tip)
    b.setIcon(I.icon(icon, 17))
    b.setIconSize(QSize(17, 17))
    b.setCheckable(checkable)
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
        return QSize(126, 96)

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
        img = r.adjusted(3, 3, -3, -18)
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
        foot = r.adjusted(5, r.height() - 17, -5, -2)
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
            p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), 3, 3)
        p.restore()


class Canvas(QWidget):
    """Foto grande con antes/después y marco de recorte."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pm = self.before = None
        self.cmp = CMP.CompareState()
        self.zoom = Z.Zoom(self)
        self.crop = CV.CropTool(self)  # marco de recorte (modo Recortar)
        self.msg = "Abre una carpeta y elige una foto"
        self.img_rect = QRectF()
        self.on_mouse = None  # callback(evento, x, y) con x, y en 0..1 sobre la foto mostrada
        self.overlay = None   # overlay(painter, rect_foto): guías de la máscara (líneas, elipse, punto)
        self.on_hover = None  # on_hover(x, y) -> cursor (Qt.CursorShape) o None
        self.setMouseTracking(True)

    def _norm(self, e):
        r = self.img_rect
        if r.width() <= 0:
            return None
        p = e.position()
        return (min(1, max(0, (p.x() - r.x()) / r.width())), min(1, max(0, (p.y() - r.y()) / r.height())))

    def _near_line(self, e) -> bool:
        """¿El clic cae sobre la línea de la ventanilla? (si no, el clic es para dibujar máscaras)"""
        st, r, pt = self.cmp, self.img_rect, e.position()
        if not st.split or r.width() <= 0:
            return False
        if self.on_mouse is None or not getattr(self, "mask_drawing", lambda: False)():
            return True  # sin máscara que dibujar: cualquier clic mueve la línea
        if st.mode == "v":
            return abs(pt.x() - (r.x() + r.width() * st.pos)) < 24
        return abs(pt.y() - (r.y() + r.height() * st.pos)) < 24

    def zoom_pixmap(self):
        return self.pm

    def _area(self):
        return QRectF(self.rect()).adjusted(12, 12, -12, -12)

    def wheelEvent(self, e):
        if self.pm is not None:
            self.zoom.wheel(e.angleDelta().y(), e.position(), self._area(), self.pm.size())
            self.update()

    def mouseDoubleClickEvent(self, e):
        if self.pm is not None and not self._near_line(e):
            self.zoom.toggle(e.position(), self._area(), self.pm.size())
            self.update()

    def _wants_pan(self, e) -> bool:
        if e.button() == Qt.MiddleButton:
            return True
        drawing = getattr(self, "mask_drawing", lambda: False)()
        return self.zoom.active and e.button() == Qt.LeftButton and not drawing

    def mousePressEvent(self, e):
        if self.crop.active and e.button() == Qt.LeftButton:
            self.crop.press(e.position(), self.img_rect, e.modifiers())
            return
        if self._wants_pan(e) and not (self.cmp.split and self._near_line(e)):
            self.zoom.start_pan(e.position())
            self.setCursor(Qt.ClosedHandCursor)
            return
        if self._near_line(e):
            self.cmp.dragging = True
            CMP.drag(self.cmp, self.img_rect, e.position().x(), e.position().y())
            return self.update()
        n = self._norm(e)
        if n and self.on_mouse:
            self.on_mouse("press", *n, e.button() == Qt.RightButton)

    def mouseMoveEvent(self, e):
        if self.zoom.panning:
            self.zoom.pan(e.position(), self._area(), self.pm.size())
            return self.update()
        if self.crop.active:
            self.crop.move(e.position(), self.img_rect, e.buttons())
            return
        if self.cmp.dragging:
            CMP.drag(self.cmp, self.img_rect, e.position().x(), e.position().y())
            return self.update()
        n = self._norm(e)
        if n and self.on_mouse and e.buttons():
            self.on_mouse("move", *n, bool(e.buttons() & Qt.RightButton))
        elif n and self.on_hover and not e.buttons():
            cur = self.on_hover(*n)
            self.setCursor(cur if cur is not None else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self.zoom.panning:
            self.zoom.end_pan()
            self.setCursor(Qt.ArrowCursor)
            return
        if self.crop.active:
            self.crop.release()
            return
        if self.cmp.dragging:
            self.cmp.dragging = False
            return
        n = self._norm(e)
        if n and self.on_mouse:
            self.on_mouse("release", *n, e.button() == Qt.RightButton)

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
        pm = self.pm
        if pm is None:
            p.setPen(QColor(T.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self.msg)
            return
        area = self._area()
        r = self.zoom.rect(area, pm.size())
        self.img_rect = r
        p.setClipRect(area)
        if not (self.crop.active and self.crop.paint_image(p, r, pm)):
            CMP.paint(p, r, pm, self.before, self.cmp)
        if not self.cmp.active:
            self.zoom.draw_tile(p, r)
        self.crop.paint(p, r)
        if self.overlay and not self.crop.active and not self.cmp.active:
            p.save()
            p.setClipRect(r.intersected(area))
            self.overlay(p, r)
            p.restore()
        p.setClipping(False)
        self.zoom.draw_badge(p, area, pm.size(), busy=self.zoom.active and self.zoom.tile is None
                             and self.zoom.pct * max(1, self.zoom.full_size[0]) > pm.width() * 1.15)
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
        g.setContentsMargins(0, 1, 0, 1)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(0)
        self.lab = QLabel(label)
        self.lab.setFixedWidth(96)
        self.lab.setStyleSheet(f"color: {T.MUTED};")
        self.lab.setToolTip("Doble clic para restablecer")
        self.lab.mouseDoubleClickEvent = lambda e: self.set_value(self.neutral, emit=True)
        self.sl = QSlider(Qt.Horizontal)
        self.sl.setRange(int(lo * self.k), int(hi * self.k))
        self.sl.setPageStep(max(1, int(10 * self.k * step)))
        from PySide6.QtWidgets import QLineEdit
        self.lo, self.hi = lo, hi
        self.dec = 0 if step >= 1 else (2 if step < 0.1 else 1)
        self.val = QLineEdit()
        self.val.setFixedWidth(44)
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.val.setStyleSheet("QLineEdit { padding: 1px 3px; background: transparent; border: none; }"
                               "QLineEdit:focus { background: #121212; border: 1px solid #555; }")
        self.sl.setStyleSheet(T.slider_qss(key))
        g.addWidget(self.lab, 0, 0)
        g.addWidget(self.sl, 0, 1)
        g.addWidget(self.val, 0, 2)
        g.setColumnStretch(1, 1)
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
    """Panel plegable como los de Lightroom / Capture One: cabecera con título y triángulo."""

    def __init__(self, title, open_=True):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.title = title
        self.head = QPushButton()
        self.head.setObjectName("section")
        self.head.setCheckable(True)
        self.head.setChecked(open_)
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(10, 8, 8, 10)
        self.lay.setSpacing(5)
        self.body.setVisible(open_)
        self._label(open_)
        self.head.toggled.connect(lambda on: (self.body.setVisible(on), self._label(on)))
        v.addWidget(self.head)
        v.addWidget(self.body)

    def _label(self, on):
        self.head.setText(("▾   " if on else "▸   ") + self.title)


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
        self._before_cache: dict = {}
        threading.Thread(target=self._render_loop, daemon=True).start()
        self.mask_i: int | None = None      # máscara seleccionada
        self.comp_i = 0                     # zona elegida de esa máscara (0 = su zona principal)
        self._pending_comp = 0
        self.mask_show = False              # superponer la máscara seleccionada
        self.crop_mode = False              # marco de recorte sobre la foto entera
        self._crop_undo = None
        self._view_crop = None              # recorte de la imagen que se ve ahora (None = foto entera)
        self._drag = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_cur)
        self._full_timer = QTimer(self)
        self._full_timer.setSingleShot(True)
        self._full_timer.timeout.connect(lambda: self.request(draft=False))

        self._last_label = ""
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_left())
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        mid.addWidget(self._build_center(), 1)
        mid.addWidget(self._build_filmstrip())
        h.addLayout(mid, 1)
        h.addWidget(self._build_develop())

        self.rendered.connect(self._on_rendered)
        self.thumbs_ready.connect(lambda i: self.film.viewport().update())
        self.proxy_ready.connect(self._on_proxy)
        self.export_progress.connect(self._on_export_progress)
        for k, fn in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+C", self.copy_settings),
                      ("Ctrl+Shift+V", self.paste_settings), ("\\", self.toggle_before),
                      ("P", lambda: self.set_flag(1)), ("X", lambda: self.set_flag(-1)), ("U", lambda: self.set_flag(0)),
                      ("Ctrl+E", self.export_dialog), ("Y", self.next_split),
                      ("R", lambda: self.b_crop.toggle()),
                      ("V", lambda: self.set_bw_mode(not self.bw_cb.isChecked())),
                      ("Z", lambda: (self.canvas.zoom.toggle(None, self.canvas._area(), self.canvas.pm.size())
                                     if self.canvas.pm is not None else None))):
            QShortcut(QKeySequence(k), self, fn)
        for n in range(6):
            QShortcut(QKeySequence(str(n)), self, lambda n=n: self.set_rating(n))
        self._crop_keys = []  # solo con el marco de recorte a la vista
        for k, ok in (("Return", True), ("Enter", True), ("Escape", False)):
            sc = QShortcut(QKeySequence(k), self, lambda ok=ok: self._crop_key(ok))
            sc.setEnabled(False)
            self._crop_keys.append(sc)

    # ---------------------------------------------------------- construcción
    def _build_left(self):
        """Panel izquierdo (Lightroom): Navegador, Presets e Historial."""
        w = QFrame()
        w.setObjectName("sidebar")
        w.setFixedWidth(250)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        nav = Section("Navegador")
        self.nav = QLabel()
        self.nav.setAlignment(Qt.AlignCenter)
        self.nav.setMinimumHeight(150)
        self.nav.setStyleSheet(f"background: {T.BG};")
        self.nav_info = QLabel("")
        self.nav_info.setObjectName("meta")
        self.nav_info.setWordWrap(True)
        nav.lay.addWidget(self.nav)
        nav.lay.addWidget(self.nav_info)
        v.addWidget(nav)
        pre = Section("Presets")
        row = QHBoxLayout()
        self.preset_search = QComboBox()
        self.preset_search.addItems(["Todos", "Mis presets", "LightCraft"])
        self.preset_search.currentIndexChanged.connect(self.reload_presets)
        b_save = _btn("＋", "small", "Guardar los ajustes actuales como preset")
        b_save.setFixedWidth(30)
        b_save.clicked.connect(self.save_preset)
        row.addWidget(self.preset_search, 1)
        row.addWidget(b_save)
        pre.lay.addLayout(row)
        self.preset_list = QListWidget()
        self.preset_list.setObjectName("list")
        self.preset_list.setMouseTracking(True)
        self.preset_list.setToolTip("Pasa el ratón para ver cómo queda; clic para aplicarlo")
        self.preset_list.itemEntered.connect(self._preview_preset)
        self.preset_list.itemClicked.connect(lambda it: self.apply_preset_data(it.data(ROLE)))
        self.preset_list.leaveEvent = lambda e: self.request(draft=False)
        pre.lay.addWidget(self.preset_list)
        v.addWidget(pre, 1)
        his = Section("Historial")
        self.hist_list = QListWidget()
        self.hist_list.setObjectName("list")
        self.hist_list.setMaximumHeight(190)
        self.hist_list.itemClicked.connect(self._history_jump)
        his.lay.addWidget(self.hist_list)
        v.addWidget(his)
        self.preset_cb = QComboBox()  # compatibilidad: algunos atajos aplican por índice
        self.preset_cb.hide()
        return w

    def _build_filmstrip(self):
        """Tira de fotos inferior (Lightroom), con filtro y contador."""
        w = QFrame()
        w.setObjectName("filmstrip")
        w.setFixedHeight(142)
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 4, 10, 4)
        v.setSpacing(2)
        top = QHBoxLayout()
        self.lib_info = QLabel("Sin carpeta")
        self.lib_info.setObjectName("meta")
        self.filter = QComboBox()
        self.filter.addItems(["Todas", "Elegidas (P)", "Sin rechazadas", "Con 3★ o más", "Editadas"])
        self.filter.setFixedWidth(150)
        self.filter.currentIndexChanged.connect(self.apply_filter)
        top.addWidget(self.lib_info, 1)
        flt = QLabel("Filtro")
        flt.setObjectName("meta")
        top.addWidget(flt)
        top.addWidget(self.filter)
        v.addLayout(top)
        self.film = QListWidget()
        self.film.setObjectName("grid")
        self.film.setStyleSheet("QListWidget#grid { background: #111111; }")
        self.film.setViewMode(QListView.IconMode)
        self.film.setFlow(QListView.LeftToRight)
        self.film.setWrapping(False)
        self.film.setUniformItemSizes(True)
        self.film.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.film.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.film.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.film.setItemDelegate(FilmDelegate(self))
        self.film.currentRowChanged.connect(self.select)
        v.addWidget(self.film, 1)
        return w

    def _build_center(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bh = QHBoxLayout(self.banner)
        bh.setContentsMargins(12, 6, 6, 6)
        self.banner_txt = QLabel("")
        self.b_use = _btn("Aprobar y volver a Events", "primary")
        self.b_back = _btn("Volver sin cambios", "ghost")
        bh.addWidget(self.banner_txt, 1)
        bh.addWidget(self.b_back)
        bh.addWidget(self.b_use)
        self.banner.hide()
        self.b_use.clicked.connect(lambda: self._finish_events(True))
        self.b_back.clicked.connect(lambda: self._finish_events(False))
        self._events_cb = None
        bw = QWidget()
        bl = QVBoxLayout(bw)
        bl.setContentsMargins(10, 8, 10, 0)
        bl.addWidget(self.banner)
        v.addWidget(bw)
        self.canvas = Canvas()
        self.canvas.on_mouse = self._canvas_mouse
        self.canvas.overlay = self._paint_gizmo
        self.canvas.on_hover = self._gizmo_cursor
        self.canvas.crop.on_change = self._on_crop
        self.canvas.zoom.provider = self._tile
        self._full: tuple | None = None
        self.canvas.mask_drawing = lambda: bool(
            self.cur and self.mask_i is not None and self.mask_i < len(self._masks())
            and (self._comp() or {}).get("type") in ("radial", "linear", "brush", "point"))
        v.addWidget(self.canvas, 1)
        # barra de herramientas bajo la foto (Lightroom): vista, valoración, marcas y acciones
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(42)
        hb = QHBoxLayout(bar)
        hb.setContentsMargins(10, 4, 10, 4)
        hb.setSpacing(6)
        self.cmp_bar = CMP.CompareBar()
        self.cmp_bar.changed.connect(self.set_compare)
        hb.addWidget(self.cmp_bar)
        self.b_crop_bar = _ibtn("crop", "Recortar: mover y cambiar el tamaño del marco (R)", True)
        self.b_crop_bar.clicked.connect(lambda on: self.b_crop.setChecked(on))
        hb.addWidget(self.b_crop_bar)
        self.b_bw_bar = _ibtn("bw", "Blanco y negro (V): pasa la foto a B/N y retócala en B/N. La mezcla B/N "
                                    "(cuánto aclara u oscurece cada color) está en Color › HSL › Luminancia", True)
        self.b_bw_bar.setText(" B/N")
        self.b_bw_bar.clicked.connect(self.set_bw_mode)
        hb.addWidget(self.b_bw_bar)
        hb.addSpacing(8)
        self.stars = []
        for n in range(1, 6):
            b = QToolButton()
            b.setText("☆")
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QToolButton {{ border: none; font-size: 12pt; color: {T.MUTED}; background: transparent; }}"
                            f"QToolButton:hover {{ color: {T.TEXT_STRONG}; }}")
            b.clicked.connect(lambda _, n=n: self.set_rating(0 if self._rating() == n else n))
            self.stars.append(b)
            hb.addWidget(b)
        hb.addSpacing(8)
        self.b_pick = _btn("⚑", "flat", "Elegida (P)")
        self.b_rej = _btn("✕", "flat", "Rechazada (X)")
        self.b_pick.clicked.connect(lambda: self.set_flag(0 if self._flag() == 1 else 1))
        self.b_rej.clicked.connect(lambda: self.set_flag(0 if self._flag() == -1 else -1))
        hb.addWidget(self.b_pick)
        hb.addWidget(self.b_rej)
        self.flag_lbl = QLabel("")
        self.flag_lbl.setObjectName("meta")
        hb.addWidget(self.flag_lbl)
        self.title = QLabel("Studio")
        self.title.setObjectName("meta")
        hb.addSpacing(10)
        hb.addWidget(self.title, 1)
        self.pb = QProgressBar()
        self.pb.setTextVisible(False)
        self.pb.setFixedWidth(160)
        self.pb.hide()
        self.status = QLabel("")
        self.status.setObjectName("meta")
        hb.addWidget(self.status)
        hb.addWidget(self.pb)
        self.b_auto = _ibtn("wand", "Ajuste automático (exposición en caras, giro, recorte)")
        self.b_copy = _ibtn("dupes", "Copiar ajustes (Ctrl+Mayús+C)")
        self.b_paste = _ibtn("paste", "Pegar ajustes en las seleccionadas (Ctrl+Mayús+V)")
        self.b_sync = _ibtn("sync", "Sincronizar: aplicar los ajustes de esta foto a las seleccionadas")
        self.b_reset = _ibtn("undo", "Restablecer: volver a la foto sin ajustes")
        self.b_export = _btn("  Exportar", "primary", "Exportar (Ctrl+E)")
        self.b_export.setIcon(I.icon("export", 16, "#ffffff"))
        self.b_auto.clicked.connect(self.auto)
        self.b_reset.clicked.connect(self.reset)
        self.b_copy.clicked.connect(self.copy_settings)
        self.b_paste.clicked.connect(self.paste_settings)
        self.b_sync.clicked.connect(self.sync_selected)
        self.b_export.clicked.connect(self.export_dialog)
        for b in (self.b_auto, self.b_copy, self.b_paste, self.b_sync, self.b_reset, self.b_export):
            hb.addWidget(b)
        v.addWidget(bar)
        return w

    def _build_develop(self):
        """Panel derecho: histograma + pestañas de herramientas a la derecha (Capture One)."""
        from PySide6.QtWidgets import QButtonGroup, QStackedWidget
        outer = QFrame()
        outer.setObjectName("previewPane")
        outer.setFixedWidth(372)
        oh = QHBoxLayout(outer)
        oh.setContentsMargins(0, 0, 0, 0)
        oh.setSpacing(0)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(10, 10, 10, 6)
        tv.setSpacing(4)
        self.hist = Histogram()
        self.exif = QLabel("")
        self.exif.setObjectName("meta")
        self.exif.setAlignment(Qt.AlignCenter)
        tv.addWidget(self.hist)
        tv.addWidget(self.exif)
        col.addWidget(top)
        self.pages = QStackedWidget()
        col.addWidget(self.pages, 1)
        oh.addLayout(col, 1)
        tabs = QFrame()
        tabs.setObjectName("tooltabs")
        tabs.setFixedWidth(40)
        tl = QVBoxLayout(tabs)
        tl.setContentsMargins(0, 10, 0, 10)
        tl.setSpacing(2)
        group = QButtonGroup(self)
        self.rows: dict[str, SliderRow] = {}

        def page(title):
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget()
            bv = QVBoxLayout(body)
            bv.setContentsMargins(0, 0, 0, 0)
            bv.setSpacing(0)
            sc.setWidget(body)
            self.pages.addWidget(sc)
            return bv

        def sliders(sec, ctrls):
            for c in ctrls:
                r = SliderRow(*c)
                r.changed.connect(self._set)
                self.rows[c[0]] = r
                sec.lay.addWidget(r)

        panels = dict(SE.PANELS)
        layout = [("sun", "Luz", ["Básico", "Presencia", "Curva tonal"]),
                  ("palette", "Color", ["_color", "_hsl", "Gradación de color"]),
                  ("detail", "Detalle", ["Detalle", "Efectos"]),
                  ("mask", "Máscaras", ["_masks"]),
                  ("crop", "Recorte y geometría", ["_crop", "Geometría"])]
        self._tab_btns = []
        for i, (icon, name, secs) in enumerate(layout):
            bv = page(name)
            for t in secs:
                if t == "_masks":
                    bv.addWidget(self._build_masks())
                elif t == "_crop":
                    bv.addWidget(self._build_crop())
                elif t == "_color":
                    sec = Section("Perfil, LUT y B/N")
                    row = QHBoxLayout()
                    self.lut_cb = QComboBox()
                    self.lut_cb.setToolTip("LUT (.cube) de todos los tipos de sesión")
                    self.lut_cb.activated.connect(lambda _: self._set("lut", self.lut_cb.currentData() or "", True))
                    row.addWidget(QLabel("LUT"))
                    row.addWidget(self.lut_cb, 1)
                    sec.lay.addLayout(row)
                    lut_k = SliderRow("lut_strength", "Intensidad", 0, 100, 1, 100)
                    lut_k.changed.connect(self._set)
                    self.rows["lut_strength"] = lut_k
                    sec.lay.addWidget(lut_k)
                    row = QHBoxLayout()
                    self.bw_cb = QCheckBox("Blanco y negro")
                    self.bw_cb.toggled.connect(lambda on: (self._set("bw", on, True), self._sync_bw_ui(on)))
                    self.bw_style = QComboBox()
                    self.bw_style.addItems(list(E.BW_STYLES))
                    self.bw_style.activated.connect(lambda _: self._set("bw_style", self.bw_style.currentText(), True))
                    row.addWidget(self.bw_cb)
                    row.addWidget(self.bw_style, 1)
                    sec.lay.addLayout(row)
                    bv.addWidget(sec)
                    self._bw_sec = sec
                elif t == "_hsl":
                    hsl = Section("Mezcla de color (HSL)")
                    tb = QTabWidget()
                    for p_, nm in (("h", "Tono"), ("s", "Saturación"), ("l", "Luminancia")):
                        pg = QWidget()
                        pv = QVBoxLayout(pg)
                        pv.setContentsMargins(0, 6, 0, 0)
                        pv.setSpacing(5)
                        for b, label, _ in SE.HSL_BANDS:
                            r = SliderRow(f"hsl_{b}_{p_}", label, -100, 100, 1, 0)
                            r.changed.connect(self._set)
                            self.rows[r.key] = r
                            pv.addWidget(r)
                        tb.addTab(pg, nm)
                    hsl.lay.addWidget(tb)
                    bv.addWidget(hsl)
                    self._hsl_sec, self._hsl_tabs = hsl, tb
                else:
                    sec = Section(t)
                    sliders(sec, panels[t])
                    bv.addWidget(sec)
            bv.addStretch(1)
            b = QPushButton()
            b.setIcon(I.icon(icon, 20))
            b.setIconSize(QSize(20, 20))
            b.setObjectName("tooltab")
            b.setCheckable(True)
            b.setToolTip(name)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, i=i: self.pages.setCurrentIndex(i))
            group.addButton(b)
            tl.addWidget(b)
            self._tab_btns.append(b)
            if i == 0:
                b.setChecked(True)
        tl.addStretch(1)
        oh.addWidget(tabs)
        self.reload_presets()
        self.reload_luts()
        return outer

    # ---------------------------------------------------------- presets (vista previa al pasar el ratón), historial
    def _preview_preset(self, it):
        s = it.data(ROLE)
        if s and self.cur and self.cur.path in self._proxy:
            self._req += 1
            self._jobs.put((self._req, self.cur.path, dict(copy.deepcopy(self.cur.settings), **s), True, None))

    def apply_preset_data(self, s):
        if s and self.cur:
            self._push_history("Preset")
            self.cur.settings = dict(self.cur.settings, **copy.deepcopy(s))
            self._load_controls()
            self.request(draft=False)
            self._save_timer.start(300)

    def _refresh_history(self):
        self.hist_list.clear()
        it = self.cur
        if not it:
            return
        for label, _ in reversed(getattr(it, "timeline", [])):
            self.hist_list.addItem(label)
        self.hist_list.addItem("Importar")

    def _history_jump(self, item):
        it = self.cur
        tl = getattr(it, "timeline", []) if it else []
        row = self.hist_list.row(item)
        idx = len(tl) - 1 - row  # la lista va de lo más reciente a lo más antiguo
        target = copy.deepcopy(tl[idx][1]) if 0 <= idx < len(tl) else SE.new_settings(
            **{k: it.settings.get(k) for k in ("rating", "flag")})
        it.history.append(copy.deepcopy(it.settings))
        it.settings = target
        self._load_controls(refresh_history=False)
        self.request(draft=False)
        SE.save(self.folder, it.path, it.settings)

    # ---------------------------------------------------------- máscaras
    def _build_masks(self):
        sec = Section("Máscaras", open_=True)
        row = QHBoxLayout()
        add = _btn("  Añadir máscara", "ghost")
        add.setIcon(I.icon("plus", 16))
        add.setMenu(self._type_menu(add, self.add_mask))
        self.b_mon = _ibtn("power", "Activar o desactivar el efecto de esta máscara (H)", True)
        self.b_mon.clicked.connect(self._toggle_mask_on)
        self.b_mshow = _ibtn("eye", "Ver en magenta la zona que coge la máscara (O). Se oculta sola al mover un "
                                    "ajuste, para ver el resultado.")
        self.b_mshow.setCheckable(True)
        self.b_mshow.toggled.connect(self._toggle_mask_view)
        self.b_minv = _ibtn("invert", "Invertir la máscara entera (con lo sumado y restado)")
        self.b_minv.clicked.connect(self._invert_mask)
        self.b_mdup = _ibtn("dupes", "Duplicar la máscara (con sus zonas y ajustes)")
        self.b_mdup.clicked.connect(self._duplicate_mask)
        self.b_mdel = _ibtn("trash", "Borrar la máscara")
        self.b_mdel.clicked.connect(self._delete_mask)
        row.addWidget(add, 1)
        for b in (self.b_mon, self.b_mshow, self.b_minv, self.b_mdup, self.b_mdel):
            row.addWidget(b)
        sec.lay.addLayout(row)
        self.mask_list = QListWidget()
        self.mask_list.setFixedHeight(96)
        self.mask_list.currentRowChanged.connect(self._select_mask)
        self.mask_list.itemChanged.connect(self._mask_checked)
        self.mask_list.setToolTip("La casilla activa o desactiva cada máscara (para comparar con y sin ella)")
        sec.lay.addWidget(self.mask_list)
        # zonas de la máscara: otras de cualquier tipo que suman, restan o intersecan con la principal
        self.comp_box = QWidget()
        cb = QVBoxLayout(self.comp_box)
        cb.setContentsMargins(0, 2, 0, 0)
        cb.setSpacing(4)
        crow = QHBoxLayout()
        self.b_cadd = _btn("  Sumar", "ghost", "Añadir otra zona a esta máscara (cualquier tipo: pincel, lineal, IA…)")
        self.b_cadd.setIcon(I.icon("plus", 16))
        self.b_cadd.setMenu(self._type_menu(self.b_cadd, lambda k: self.add_part(k, "add")))
        self.b_csub = _btn("  Restar", "ghost", "Quitar una zona de esta máscara (cualquier tipo: pincel, lineal, IA…)")
        self.b_csub.setIcon(I.icon("minus", 16))
        self.b_csub.setMenu(self._type_menu(self.b_csub, lambda k: self.add_part(k, "sub")))
        crow.addWidget(self.b_cadd, 1)
        crow.addWidget(self.b_csub, 1)
        cb.addLayout(crow)
        self.comp_list = QListWidget()
        self.comp_list.setFixedHeight(78)
        self.comp_list.setToolTip("Zonas de esta máscara. Elige una para moverla o pintarla en la foto y "
                                  "cambiar sus valores. La casilla la activa o la desactiva.")
        self.comp_list.currentRowChanged.connect(self._select_comp)
        self.comp_list.itemChanged.connect(self._comp_checked)
        cb.addWidget(self.comp_list)
        prow = QHBoxLayout()
        self.c_mode = QComboBox()
        for k, label in M.COMBINE.items():
            self.c_mode.addItem(label, k)
        self.c_mode.setToolTip("Cómo se combina esta zona con las de encima: sumar, restar o solo lo común")
        self.c_mode.activated.connect(self._comp_mode)
        self.b_cinv = _ibtn("invert", "Invertir solo esta zona")
        self.b_cinv.clicked.connect(self._invert_comp)
        self.b_cdup = _ibtn("dupes", "Duplicar esta zona")
        self.b_cdup.clicked.connect(self._duplicate_comp)
        self.b_cdel = _ibtn("trash", "Quitar esta zona de la máscara")
        self.b_cdel.clicked.connect(self._delete_comp)
        prow.addWidget(self.c_mode, 1)
        for b in (self.b_cinv, self.b_cdup, self.b_cdel):
            prow.addWidget(b)
        cb.addLayout(prow)
        self.comp_box.hide()
        sec.lay.addWidget(self.comp_box)
        self.mask_hint = QLabel("")
        self.mask_hint.setObjectName("muted")
        self.mask_hint.setWordWrap(True)
        sec.lay.addWidget(self.mask_hint)
        self.mask_box = QWidget()
        mb = QVBoxLayout(self.mask_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(6)
        self.mrows: dict[str, SliderRow] = {}
        for key, label, lo, hi, st, neu in [("amount", "Cantidad", 0, 100, 1, 100), ("feather", "Difuminado", 0, 100, 1, 50),
                                            ("brush", "Tamaño del pincel", 1, 30, 1, 6),
                                            ("lo", "Luz desde", 0, 100, 1, 60), ("hi", "Luz hasta", 0, 100, 1, 100),
                                            ("hue", "Tono", 0, 360, 1, 25), ("range", "Amplitud", 5, 90, 1, 30),
                                            ("size", "Alcance", 1, 100, 1, 25),
                                            ("sim", "Parecido de color", 0, 100, 1, 50)] + \
                [(k, l, a, b, s_, n) for k, l, a, b, s_, n in M.ADJ if k not in ("clarity", "saturation")]:
            r = SliderRow("m_" + key, label, lo, hi, st, neu)
            r.changed.connect(self._mask_value)
            self.mrows[key] = r
            mb.addWidget(r)
        # presencia y curva tonal de la máscara, como los paneles generales
        for title, rows in (("Presencia", M.PRESENCE), ("Curva tonal", M.CURVE)):
            sec_ = Section(title, open_=False)
            for key, label, lo, hi, st, neu in rows:
                r = SliderRow("m_" + key, label, lo, hi, st, neu)
                r.changed.connect(self._mask_value)
                self.mrows[key] = r
                sec_.lay.addWidget(r)
            mb.addWidget(sec_)
        # color de la máscara: por colores (HSL) y por tonos (gradación), solo dentro de la zona
        hsl = Section("Color por colores (HSL)", open_=False)
        tb = QTabWidget()
        for p_, nm in (("h", "Tono"), ("s", "Saturación"), ("l", "Luminancia")):
            pg = QWidget()
            pv = QVBoxLayout(pg)
            pv.setContentsMargins(0, 6, 0, 0)
            pv.setSpacing(5)
            for b, label in M.HSL_BANDS:
                key = f"hsl_{b}_{p_}"
                r = SliderRow("m_" + key, label, -100, 100, 1, 0)
                r.changed.connect(self._mask_value)
                self.mrows[key] = r
                pv.addWidget(r)
            tb.addTab(pg, nm)
        hsl.lay.addWidget(tb)
        mb.addWidget(hsl)
        grade = Section("Color por tonos (sombras, medios, luces)", open_=False)
        for key, label, lo, hi, st, neu in M.GRADE:
            r = SliderRow("m_" + key, label, lo, hi, st, neu)
            r.changed.connect(self._mask_value)
            self.mrows[key] = r
            grade.lay.addWidget(r)
        mb.addWidget(grade)
        self.mask_box.hide()
        sec.lay.addWidget(self.mask_box)
        QShortcut(QKeySequence("O"), self, lambda: self.b_mshow.toggle())
        QShortcut(QKeySequence("H"), self, lambda: (self.b_mon.toggle(), self._toggle_mask_on()))
        return sec

    def _masks(self) -> list:
        return self.cur.settings.setdefault("masks", []) if self.cur else []

    def _type_menu(self, parent, fn) -> QMenu:
        menu = QMenu(parent)
        groups = [("Manual", M.MANUAL_TYPES), ("Personas (IA)", M.AI_TYPES),
                  ("Cara (IA)", {**{k: v[0] for k, v in M.FACE_PARTS.items()}, **M.FACE_EXTRA}), ("Otros", M.OTHER_TYPES)]
        for title, items in groups:
            menu.addSection(title)
            for k, label in items.items():
                menu.addAction(label, lambda k=k: fn(k))
        return menu

    def _mask(self) -> dict | None:
        ms = self._masks()
        return ms[self.mask_i] if self.cur and self.mask_i is not None and self.mask_i < len(ms) else None

    def _comp(self) -> dict | None:
        """La zona elegida de la máscara: la principal (la propia máscara) o una de las que suman o restan."""
        m = self._mask()
        if m is None:
            return None
        parts = m.get("parts") or []
        return parts[self.comp_i - 1] if 0 < self.comp_i <= len(parts) else m

    def _mask_changed(self, select_comp=None):
        """Tras cambiar la estructura de una máscara: refresca las listas, vuelve a revelar y guarda."""
        self._refresh_masks(select=self.mask_i, comp=self.comp_i if select_comp is None else select_comp)
        self.request(draft=False)
        self._save_timer.start(400)

    def _refresh_comps(self, select=0):
        m = self._mask()
        self.comp_list.blockSignals(True)
        self.comp_list.clear()
        if m is not None:
            sign = {"add": "+", "sub": "−", "inter": "∩"}
            self.comp_list.addItem(QListWidgetItem(f"Principal: {m['name']}"
                                                   + ("  (invertida)" if m.get("invert") else "")))
            for c in m.get("parts") or []:
                mode = c.get("mode", "add")
                it = QListWidgetItem(f"{sign.get(mode, '+')} {M.COMBINE.get(mode, 'Sumar')}: {c.get('name', c['type'])}"
                                     + ("  (invertida)" if c.get("invert") else ""))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if c.get("enabled", True) else Qt.Unchecked)
                self.comp_list.addItem(it)
        n = self.comp_list.count()
        if n:
            self.comp_list.setCurrentRow(max(0, min(select, n - 1)))
        self.comp_list.blockSignals(False)
        self._select_comp(self.comp_list.currentRow())

    def _select_comp(self, i):
        m = self._mask()
        if m is None:
            return
        self.comp_i = max(0, i)
        c = self._comp()
        main = c is m
        self.c_mode.setEnabled(not main)
        self.b_cdel.setEnabled(not main)
        if not main:
            self.c_mode.setCurrentIndex(max(0, self.c_mode.findData(c.get("mode", "add"))))
        self._show_comp_controls()

    def add_part(self, kind, mode):
        m = self._mask()
        if m is None:
            return
        self._push_history(f"{m['name']}: {M.COMBINE[mode].lower()} zona")
        m.setdefault("parts", []).append(M.new_part(kind, mode))
        if kind in M.AI_TYPES or kind in M.FACE_PARTS or kind in M.FACE_EXTRA:
            self.status.setText("Zona con IA: la primera vez se descarga el modelo (16 MB)…")
        self.b_mshow.setChecked(True)
        self._mask_changed(select_comp=len(m["parts"]))

    def _comp_mode(self, _=None):
        m, c = self._mask(), self._comp()
        if m is None or c is m:
            return
        self._push_history(f"{m['name']}: combinar")
        c["mode"] = self.c_mode.currentData()
        self._mask_changed()

    def _invert_comp(self):
        m, c = self._mask(), self._comp()
        if c is None:
            return
        self._push_history(f"{m['name']}: invertir zona")
        c["invert"] = not c.get("invert")
        self._mask_changed()

    def _duplicate_comp(self):
        m, c = self._mask(), self._comp()
        if c is None:
            return
        self._push_history(f"{m['name']}: duplicar zona")
        if c is m:  # la principal se duplica como una zona que suma
            nc = {k: copy.deepcopy(m.get(k)) for k in ("name", "type", "params", "invert")}
            nc.update(mode="add", enabled=True)
        else:
            nc = copy.deepcopy(c)
        m.setdefault("parts", []).insert(self.comp_i, nc)
        self._mask_changed(select_comp=self.comp_i + 1)

    def _delete_comp(self):
        m, c = self._mask(), self._comp()
        if c is None or c is m:
            return
        self._push_history(f"{m['name']}: quitar zona")
        del m["parts"][self.comp_i - 1]
        self._mask_changed(select_comp=self.comp_i - 1)

    def _comp_checked(self, item):
        m = self._mask()
        r = self.comp_list.row(item)
        if m is None or r < 1:
            return
        self._push_history(f"{m['name']}: activar zona")
        m["parts"][r - 1]["enabled"] = item.checkState() == Qt.Checked
        self._mask_changed(select_comp=r)

    def _duplicate_mask(self):
        m = self._mask()
        if m is None:
            return
        self._push_history("Duplicar máscara")
        nm = copy.deepcopy(m)
        nm["name"] = m["name"] + " (copia)"
        self._masks().insert(self.mask_i + 1, nm)
        self.mask_i += 1
        self._mask_changed(select_comp=0)

    def _refresh_masks(self, select=None, comp=0):
        self._pending_comp = comp
        self.mask_list.blockSignals(True)
        self.mask_list.clear()
        for m in self._masks():
            on = M.adj_active(m.get("adj", {}))
            nz = len(m.get("parts") or [])
            it = QListWidgetItem(("● " if on else "○ ") + m["name"] + (f"  (+{nz} zonas)" if nz else "")
                                 + ("  (invertida)" if m.get("invert_all") else "")
                                 + ("" if m.get("enabled", True) else "  · desactivada"))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if m.get("enabled", True) else Qt.Unchecked)
            self.mask_list.addItem(it)
        self.mask_list.blockSignals(False)
        n = len(self._masks())
        if select is not None and n:
            row = min(select, n - 1)
            if self.mask_list.currentRow() == row:
                self._select_mask(row)
            else:
                self.mask_list.setCurrentRow(row)
        elif n == 0:
            self._select_mask(-1)

    def add_mask(self, kind):
        if not self.cur:
            return
        self._push_history()
        self._masks().append(M.new_mask(kind))
        self._refresh_masks(select=len(self._masks()) - 1)
        if kind in M.AI_TYPES or kind in M.FACE_PARTS or kind in M.FACE_EXTRA:
            self.status.setText("Máscara con IA: la primera vez se descarga el modelo (16 MB)…")
        self.b_mshow.setChecked(True)
        self.request(draft=False)
        self._save_timer.start(400)

    def _select_mask(self, i):
        self.mask_i = i if i is not None and i >= 0 else None
        m = self._mask()
        self.mask_box.setVisible(m is not None)
        self.comp_box.setVisible(m is not None)
        for b in (self.b_minv, self.b_mdup, self.b_mdel):
            b.setEnabled(m is not None)
        self.b_mon.setEnabled(m is not None)
        self.b_mon.setChecked(bool(m and m.get("enabled", True)))
        comp, self._pending_comp = self._pending_comp or 0, 0
        if not m:
            self.mask_hint.setText("Añade una máscara para ajustar solo una zona (caras, fondo, cielo…).")
            return
        self._refresh_comps(comp)  # rellena los controles de la zona elegida

    def _show_comp_controls(self):
        m, c = self._mask(), self._comp()
        if m is None or c is None:
            return
        t, p = c["type"], c.setdefault("params", {})
        hints = {"radial": "Arrastra sobre la foto: el punto inicial es el centro.",
                 "linear": "Arrastra sobre la foto: de donde el efecto es completo a donde desaparece.",
                 "brush": "Pinta sobre la foto. Botón derecho: borrar.",
                 "luma": "Elige el rango de luminosidad.", "color": "Elige el tono y su amplitud.",
                 "point": "Pincha en la foto sobre lo que quieres ajustar (cielo, vestido, césped…): coge lo "
                          "que está cerca y tiene un color parecido. Alcance: hasta dónde llega; Parecido: "
                          "cuánto se tiene que parecer el color."}
        how = {"add": "se suma a", "sub": "se resta de", "inter": "se interseca con"}
        self.mask_hint.setText(hints.get(t, "Zona detectada automáticamente con IA.")
                               + ("" if c is m else f"  ·  Esta zona {how.get(c.get('mode', 'add'), 'se suma a')} "
                                                    "la máscara."))
        vis = {"feather": t in ("radial", "brush", "luma", "linear"), "brush": t == "brush", "lo": t == "luma",
               "hi": t == "luma", "hue": t == "color", "range": t == "color", "size": t == "point",
               "sim": t == "point"}
        for k, r in self.mrows.items():
            r.setVisible(vis.get(k, True))
            if k == "amount":
                r.set_value(m.get("amount", 100))
            elif k == "brush":
                r.set_value(p.get("_size", 6))
            elif k == "feather" and t == "linear":
                r.set_value(round(self._linear_width(p) * 100))
            elif k in p:
                r.set_value(p[k])
            else:
                r.set_value(m["adj"].get(k, M.NEUTRAL_ADJ.get(k, 0)))
        if self.mask_show:
            self.request(draft=False)

    def _mask_value(self, key, value, final):
        m, c = self._mask(), self._comp()
        if m is None or c is None:
            return
        p = c.setdefault("params", {})
        k = key[2:]
        if final:
            self._push_history(f"{m['name']}: {self.mrows[k].lab.text()}")
        if k == "amount":
            m["amount"] = value
        elif k == "brush":
            p["_size"] = value
            return
        elif k == "feather" and c.get("type") == "linear":
            self._set_linear_width(p, value / 100)
            self.canvas.update()
        elif k in p:
            p[k] = value
        else:
            m["adj"][k] = value
            if self.mask_show:  # al ajustar, se quita el magenta para ver el resultado (como Lightroom)
                self.b_mshow.blockSignals(True)
                self.b_mshow.setChecked(False)
                self.b_mshow.blockSignals(False)
                self.mask_show = False
        if final:
            self._refresh_masks(select=self.mask_i, comp=self.comp_i)
        self.request(draft=not final)
        self._save_timer.start(600)

    def _toggle_mask_view(self, on):
        if on and self.crop_mode:
            self.b_crop.setChecked(False)
        self.mask_show = on
        self.request(draft=False)

    def _set_mask_enabled(self, i, on):
        if not self.cur or i is None or i >= len(self._masks()):
            return
        self._push_history("Activar máscara" if on else "Desactivar máscara")
        self._masks()[i]["enabled"] = bool(on)
        self._refresh_masks(select=i)
        self.request(draft=False)
        self._save_timer.start(400)

    def _toggle_mask_on(self):
        if self.mask_i is not None:
            self._set_mask_enabled(self.mask_i, self.b_mon.isChecked())

    def _mask_checked(self, item):
        self._set_mask_enabled(self.mask_list.row(item), item.checkState() == Qt.Checked)

    # ---------------------------------------------------------- recorte
    def _build_crop(self):
        sec = Section("Recorte", open_=True)
        row = QHBoxLayout()
        self.b_crop = _ibtn("crop", "Marco sobre la foto entera (R). Intro: aplicar · Esc: cancelar", True,
                            "Recortar")
        self.b_crop.toggled.connect(self.toggle_crop)
        b_reset = _ibtn("undo", "Sin recorte: foto entera (o el mayor marco con la proporción elegida)")
        b_reset.clicked.connect(self._crop_reset)
        row.addWidget(self.b_crop, 1)
        row.addWidget(b_reset)
        sec.lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Proporción"))
        self.ratio_cb = QComboBox()
        self.ratio_cb.addItems(CV.RATIOS)
        self.ratio_cb.activated.connect(self._ratio_changed)
        self.b_flip = _ibtn("sync", "Cambiar entre vertical y horizontal", True)
        self.b_flip.clicked.connect(self._ratio_changed)
        row.addWidget(self.ratio_cb, 1)
        row.addWidget(self.b_flip)
        sec.lay.addLayout(row)
        self.crop_angle = SliderRow("crop_angle", "Enderezar", -45, 45, 0.1, 0)
        self.crop_angle.changed.connect(self._crop_angle_slider)
        sec.lay.addWidget(self.crop_angle)
        row = QHBoxLayout()
        self.b_level = _btn("  Nivel", "ghost", "Traza una línea sobre el horizonte o una vertical y la foto se pone "
                                                "recta (también con Ctrl + arrastrar sobre la foto)")
        self.b_level.setIcon(I.icon("sliders", 16))
        self.b_level.setCheckable(True)
        self.b_level.toggled.connect(self._level_mode)
        b_auto = _btn("  Auto", "ghost", "Enderezar solo: busca las verticales de la foto")
        b_auto.setIcon(I.icon("auto", 16))
        b_auto.clicked.connect(self._auto_straighten)
        b_zero = _btn("0°", "ghost", "Quitar el giro")
        b_zero.clicked.connect(lambda: self._crop_angle(0.0, "end", history=True))
        row.addWidget(self.b_level, 1)
        row.addWidget(b_auto, 1)
        row.addWidget(b_zero)
        sec.lay.addLayout(row)
        hint = QLabel("Arrastra el marco para moverlo, y las esquinas o los lados para cambiar el tamaño. "
                      "Arrastra fuera del marco para girar la foto; Ctrl + arrastrar (o «Nivel») traza el horizonte.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        sec.lay.addWidget(hint)
        return sec

    # ---------------------------------------------------------- enderezar (parte del recorte)
    def _cur_angle(self) -> float:
        return float(self.cur.settings.get("angle", 0) or 0) if self.cur else 0.0

    def _crop_angle(self, a, phase, history=False):
        """Ángulo nuevo desde el marco (girar fuera de él, nivel), el regulador, Auto o 0°."""
        if not self.cur:
            return
        if phase == "start":
            self._push_history("Enderezar")
            return
        a = round(max(-45.0, min(45.0, float(a))), 2)
        if history:
            self._push_history("Enderezar")
        if self.crop_mode:
            self.canvas.crop.target = a
            self.canvas.update()
        self.cur.settings["angle"] = a
        self._sync_angle_rows(a)
        self._refresh_history()
        self.request(draft=False)
        self._save_timer.start(400)

    def _sync_angle_rows(self, a):
        for r in (self.rows.get("angle"), getattr(self, "crop_angle", None)):
            if r is not None:
                r.blockSignals(True)
                r.set_value(a)
                r.blockSignals(False)

    def _crop_angle_slider(self, _key, value, final):
        if not self.cur:
            return
        if self.crop_mode:  # vista previa al momento mientras se arrastra
            self.canvas.crop.target = float(value)
            self.canvas.update()
        if final:
            self._crop_angle(value, "end", history=True)
        else:
            self.cur.settings["angle"] = float(value)
            r = self.rows.get("angle")
            if r is not None:
                r.blockSignals(True)
                r.set_value(value)
                r.blockSignals(False)
            if not self.crop_mode:
                self.request(draft=True)

    def _level_mode(self, on):
        if on and not self.crop_mode:
            self.b_crop.setChecked(True)
        self.canvas.crop.level_mode = bool(on) and self.crop_mode
        self.status.setText("Nivel: traza una línea sobre el horizonte o sobre una vertical" if on else
                            ("Recorte: arrastra el marco · fuera del marco gira · Intro aplica · Esc cancela"
                             if self.crop_mode else ""))
        self.canvas.update()

    def _auto_straighten(self):
        if not self.cur or self.cur.path not in self._proxy:
            return
        lin = self._proxy[self.cur.path][0]
        f = 1600 / max(lin.shape[:2])
        if f < 1:
            lin = cv2.resize(lin, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        srgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(np.maximum(lin, 0), 1 / 2.4) - 0.055)
        g8 = (np.clip(cv2.cvtColor(srgb.astype(np.float32), cv2.COLOR_RGB2GRAY), 0, 1) * 255).astype(np.uint8)
        a = E.detect_tilt(g8)
        if self.crop_mode:
            self.canvas.crop.target = a
        self._crop_angle(a, "end", history=True)
        self.status.setText(f"Enderezado automático: {-a:+.1f}°" if abs(a) > 0.05 else
                            "No hay verticales claras: la foto se queda como está.")

    def _view_size(self):
        fw, fh = E.full_size(self.cur.path) if self.cur else (3, 2)
        x0, y0, x1, y1 = E._safe_rect(fw or 3, fh or 2, float(self.cur.settings.get("angle", 0) if self.cur else 0))
        return max(1, x1 - x0), max(1, y1 - y0)

    def _ratio(self):
        size = self._view_size()
        v = CV.ratio_value(self.ratio_cb.currentText(), self.b_flip.isChecked(), size)
        return None if v is None else v * size[1] / size[0]

    def toggle_crop(self, on):
        self.b_crop_bar.setChecked(on)
        if not self.cur:
            if on:
                self.b_crop.setChecked(False)
            return
        if on == self.crop_mode:
            return
        self.crop_mode = on
        for sc in self._crop_keys:
            sc.setEnabled(on)
        if on:
            if self.canvas.cmp.mode != "off":
                self.set_compare("off")
            if self.mask_show:
                self.b_mshow.setChecked(False)
            self._crop_undo = copy.deepcopy(self.cur.settings.get("crop", [0.0, 0.0, 1.0, 1.0]))
            name, flip = CV.guess_ratio(self._crop_undo, self._view_size())
            if self._crop_undo == [0.0, 0.0, 1.0, 1.0]:
                name, flip = "Original", False
            self.ratio_cb.setCurrentText(name)
            self.b_flip.setChecked(flip)
            self.canvas.zoom.pct = None
            self.canvas.crop.start(self._crop_undo, angle=self._cur_angle())
            self.canvas.crop.rn = self._ratio()
            self.canvas.crop.on_angle = self._crop_angle
            self._sync_angle_rows(self._cur_angle())
            self.pages.setCurrentIndex(4)
            self._tab_btns[4].setChecked(True)
            self.status.setText("Recorte: arrastra el marco · fuera del marco gira · Ctrl+arrastrar: nivel · "
                                "Intro aplica · Esc cancela")
        else:
            self.b_level.setChecked(False)
            self.canvas.crop.stop()
            self.status.setText("")
        self.request(draft=False)

    def _on_crop(self, phase):
        if not self.cur or self.canvas.crop.rect is None:
            return
        if phase == "start":
            self._push_history("Recorte")
            return
        self.cur.settings["crop"] = [round(float(v), 4) for v in self.canvas.crop.rect]
        self.cur.settings["crop_ratio"] = self.ratio_cb.currentText()
        self._refresh_history()
        self._save_timer.start(400)

    def _ratio_changed(self, *_):
        if not self.cur:
            return
        if not self.crop_mode:
            self.b_crop.setChecked(True)
        self._push_history("Recorte")
        self.canvas.crop.set_ratio(self._ratio())
        self._on_crop("end")

    def _crop_reset(self):
        if not self.cur:
            return
        if not self.crop_mode:
            self.b_crop.setChecked(True)
        self._push_history("Recorte")
        self.canvas.crop.reset()
        self._on_crop("end")

    def _crop_key(self, accept: bool):
        if not self.crop_mode or not self.cur:
            return
        if not accept and self._crop_undo is not None:
            self.cur.settings["crop"] = self._crop_undo
            self._save_timer.start(300)
        self.b_crop.setChecked(False)

    def _invert_mask(self):
        if self.cur and self.mask_i is not None:
            self._push_history()
            m = self._masks()[self.mask_i]
            m["invert_all"] = not m.get("invert_all")
            self._mask_changed()

    def _delete_mask(self):
        if self.cur and self.mask_i is not None:
            self._push_history()
            del self._masks()[self.mask_i]
            self._refresh_masks(select=max(0, self.mask_i - 1))
            self.request(draft=False)
            self._save_timer.start(400)

    # ---------------------------------------------------------- guías de las máscaras sobre la foto
    def _gz_frame(self):
        """(rect de la foto en pantalla, recorte de la vista): para pasar de 0..1 de la foto entera a píxeles."""
        return self.canvas.img_rect, (self._view_crop or [0.0, 0.0, 1.0, 1.0])

    def _gz_px(self, x, y):
        r, c = self._gz_frame()
        return (r.x() + (x - c[0]) / max(c[2] - c[0], 1e-6) * r.width(),
                r.y() + (y - c[1]) / max(c[3] - c[1], 1e-6) * r.height())

    def _gz_full(self, px, py):
        r, c = self._gz_frame()
        return (c[0] + (px - r.x()) / max(r.width(), 1e-6) * (c[2] - c[0]),
                c[1] + (py - r.y()) / max(r.height(), 1e-6) * (c[3] - c[1]))

    def _gz_scale(self):
        """Píxeles de pantalla por unidad (0..1) de la foto entera, en x y en y."""
        r, c = self._gz_frame()
        return r.width() / max(c[2] - c[0], 1e-6), r.height() / max(c[3] - c[1], 1e-6)

    def _linear_width(self, p) -> float:
        """Difuminado de la lineal: distancia entre su línea de efecto pleno y la de nada, sobre el lado largo."""
        sx, sy = self._gz_scale()
        if sx <= 0 or sy <= 0:
            sx = sy = 1.0
        d = math.hypot((p["x1"] - p["x0"]) * sx, (p["y1"] - p["y0"]) * sy)
        return min(1.0, d / max(sx, sy))

    def _set_linear_width(self, p, w):
        sx, sy = self._gz_scale()
        if sx <= 0 or sy <= 0:
            sx = sy = 1.0
        mx, my = (p["x0"] + p["x1"]) / 2, (p["y0"] + p["y1"]) / 2
        dx, dy = (p["x1"] - p["x0"]) * sx, (p["y1"] - p["y0"]) * sy
        L = math.hypot(dx, dy) or 1.0
        half = max(0.005, w) * max(sx, sy) / 2
        ux, uy = dx / L * half / sx, dy / L * half / sy
        p.update(x0=round(mx - ux, 4), y0=round(my - uy, 4), x1=round(mx + ux, 4), y1=round(my + uy, 4))

    def _gz_comp(self):
        c = self._comp()
        if c is None or self.crop_mode or c.get("type") not in ("radial", "linear", "point"):
            return None
        return c

    def _gz_hit(self, px, py):
        """Qué parte de la guía hay bajo el ratón: move, p0, p1, rotate, rx, ry, edge, size; o None."""
        c = self._gz_comp()
        if c is None:
            return None
        t, p, tol = c["type"], c["params"], 9
        if t == "linear":
            ax, ay = self._gz_px(p["x0"], p["y0"])
            bx, by = self._gz_px(p["x1"], p["y1"])
            mx, my = (ax + bx) / 2, (ay + by) / 2
            if math.hypot(px - mx, py - my) <= tol + 3:
                return "move"
            dx, dy = bx - ax, by - ay
            L = math.hypot(dx, dy) or 1.0
            ux, uy = dx / L, dy / L
            for name, qx, qy in (("p0", ax, ay), ("p1", bx, by), ("rotate", mx, my)):
                if abs((px - qx) * ux + (py - qy) * uy) <= tol:
                    return name
            return None
        if t == "radial":
            cx, cy = self._gz_px(p["cx"], p["cy"])
            sx, sy = self._gz_scale()
            rx, ry = max(p["rx"] * sx, 1), max(p["ry"] * sy, 1)
            if math.hypot(px - cx, py - cy) <= tol + 3:
                return "move"
            for name, hx, hy in (("rx", cx + rx, cy), ("rx", cx - rx, cy), ("ry", cx, cy + ry), ("ry", cx, cy - ry)):
                if math.hypot(px - hx, py - hy) <= tol + 2:
                    return name
            d = math.hypot((px - cx) / rx, (py - cy) / ry)
            if abs(d - 1) * min(rx, ry) <= tol:
                return "edge"
            return "move" if d < 1 else None
        if t == "point":
            cx, cy = self._gz_px(p["cx"], p["cy"])
            sx, sy = self._gz_scale()
            rad = p.get("size", 25) / 100 * max(sx, sy)
            dist = math.hypot(px - cx, py - cy)
            if dist <= tol + 4:
                return "move"
            if abs(dist - rad) <= tol:
                return "size"
        return None

    def _gizmo_cursor(self, x, y):
        r = self.canvas.img_rect
        h = self._gz_hit(r.x() + x * r.width(), r.y() + y * r.height())
        return {"move": Qt.SizeAllCursor, "rotate": Qt.PointingHandCursor, "p0": Qt.SizeVerCursor,
                "p1": Qt.SizeVerCursor, "rx": Qt.SizeHorCursor, "ry": Qt.SizeVerCursor, "edge": Qt.SizeFDiagCursor,
                "size": Qt.SizeFDiagCursor}.get(h) if h else (Qt.CrossCursor if self._gz_comp() else None)

    def _paint_gizmo(self, p, r):
        """Las guías de la zona elegida, como en Lightroom: líneas de la lineal, elipse de la radial, punto."""
        c = self._gz_comp()
        if c is None:
            return
        t, prm = c["type"], c["params"]
        p.setRenderHint(QPainter.Antialiasing)
        white, shadow = QColor(255, 255, 255, 235), QColor(0, 0, 0, 150)

        def line(x0, y0, x1, y1, dash=False, w=1.5):
            for col, ww in ((shadow, w + 2), (white, w)):
                pen = QPen(col, ww)
                if dash:
                    pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(x0, y0), QPointF(x1, y1))

        def pin(x, y, rad=6):
            p.setPen(QPen(shadow, 3))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(x, y), rad, rad)
            p.setPen(QPen(white, 1.5))
            p.setBrush(QColor(T.ACCENT))
            p.drawEllipse(QPointF(x, y), rad, rad)
            p.setBrush(Qt.NoBrush)

        if t == "linear":
            ax, ay = self._gz_px(prm["x0"], prm["y0"])
            bx, by = self._gz_px(prm["x1"], prm["y1"])
            dx, dy = bx - ax, by - ay
            L = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / L, dx / L
            ext = math.hypot(r.width(), r.height()) * 2
            for (qx, qy), dash in (((ax, ay), False), (((ax + bx) / 2, (ay + by) / 2), True), ((bx, by), False)):
                line(qx - nx * ext, qy - ny * ext, qx + nx * ext, qy + ny * ext, dash)
            pin((ax + bx) / 2, (ay + by) / 2)
        elif t == "radial":
            cx, cy = self._gz_px(prm["cx"], prm["cy"])
            sx, sy = self._gz_scale()
            rx, ry = prm["rx"] * sx, prm["ry"] * sy
            f = max(0.02, prm.get("feather", 50) / 100)
            for (ex, ey), dash in (((rx, ry), False), ((rx * (1 - f), ry * (1 - f)), True)):
                for col, ww in ((shadow, 3.5), (white, 1.5)):
                    pen = QPen(col, ww)
                    if dash:
                        pen.setStyle(Qt.DashLine)
                    p.setPen(pen)
                    p.drawEllipse(QPointF(cx, cy), ex, ey)
            for hx, hy in ((cx + rx, cy), (cx - rx, cy), (cx, cy + ry), (cx, cy - ry)):
                p.setPen(QPen(shadow, 3))
                p.setBrush(white)
                p.drawRect(QRectF(hx - 4, hy - 4, 8, 8))
            pin(cx, cy)
        elif t == "point":
            cx, cy = self._gz_px(prm["cx"], prm["cy"])
            sx, sy = self._gz_scale()
            rad = prm.get("size", 25) / 100 * max(sx, sy)
            for col, ww in ((shadow, 3.5), (white, 1.5)):
                pen = QPen(col, ww)
                pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawEllipse(QPointF(cx, cy), rad, rad)
            pin(cx, cy, 7)

    def _gz_drag(self, mode, x, y, px, py):
        """Arrastre de una parte de la guía (x, y: 0..1 de la foto entera; px, py: píxeles de pantalla)."""
        c = self._comp()
        p, st = c["params"], self._gz_start
        if mode == "move":
            fx, fy = st["at"]
            ddx, ddy = x - fx, y - fy
            for kx, ky in (("x0", "y0"), ("x1", "y1"), ("cx", "cy")):
                if kx in st["p"]:
                    p[kx] = round(st["p"][kx] + ddx, 4)
                    p[ky] = round(st["p"][ky] + ddy, 4)
        elif mode in ("p0", "p1", "rotate"):
            q = st["p"]
            ax, ay = self._gz_px(q["x0"], q["y0"])
            bx, by = self._gz_px(q["x1"], q["y1"])
            mx, my = (ax + bx) / 2, (ay + by) / 2
            dx, dy = bx - ax, by - ay
            L = math.hypot(dx, dy) or 1.0
            ux, uy = dx / L, dy / L
            if mode == "rotate":  # la línea central pasa por el ratón: gira alrededor del centro
                vx, vy = px - mx, py - my
                if math.hypot(vx, vy) < 4:
                    return
                nux, nuy = -vy, vx
                nl = math.hypot(nux, nuy)
                nux, nuy = nux / nl, nuy / nl
                if nux * ux + nuy * uy < 0:
                    nux, nuy = -nux, -nuy
                ax, ay, bx, by = mx - nux * L / 2, my - nuy * L / 2, mx + nux * L / 2, my + nuy * L / 2
            else:  # mover una de las líneas exteriores a lo largo del eje: más o menos difuminado
                s_ = (px - mx) * ux + (py - my) * uy
                if mode == "p0":
                    s_ = min(s_, -2)
                    ax, ay = mx + ux * s_, my + uy * s_
                else:
                    s_ = max(s_, 2)
                    bx, by = mx + ux * s_, my + uy * s_
            p["x0"], p["y0"] = (round(v, 4) for v in self._gz_full(ax, ay))
            p["x1"], p["y1"] = (round(v, 4) for v in self._gz_full(bx, by))
        elif mode in ("rx", "ry", "edge"):
            cx, cy = self._gz_px(p["cx"], p["cy"])
            sx, sy = self._gz_scale()
            if mode == "rx":
                p["rx"] = round(max(0.01, abs(px - cx) / sx), 4)
            elif mode == "ry":
                p["ry"] = round(max(0.01, abs(py - cy) / sy), 4)
            else:
                q = st["p"]
                d0 = math.hypot(*(a - b for a, b in zip(self._gz_px(*st["at"]), (cx, cy)))) or 1.0
                k = max(0.05, math.hypot(px - cx, py - cy) / d0)
                p["rx"], p["ry"] = round(max(0.01, q["rx"] * k), 4), round(max(0.01, q["ry"] * k), 4)
        elif mode == "size":
            cx, cy = self._gz_px(p["cx"], p["cy"])
            sx, sy = self._gz_scale()
            p["size"] = int(min(100, max(1, round(math.hypot(px - cx, py - cy) / max(sx, sy) * 100))))
            if "size" in self.mrows:
                self.mrows["size"].set_value(p["size"])
        self.canvas.update()

    def _canvas_mouse(self, ev, x, y, right):
        """Dibujo de máscaras sobre la foto (las coordenadas son de la foto completa mientras se ve la máscara).
        Sobre las guías (líneas de la lineal, elipse de la radial, círculo del punto) se arrastran; fuera,
        se dibuja una nueva."""
        c = self._comp()
        if c is None:
            return
        t, p = c["type"], c.setdefault("params", {})
        if t not in ("radial", "linear", "brush", "point"):
            return
        r = self.canvas.img_rect
        spx, spy = r.x() + x * r.width(), r.y() + y * r.height()  # píxeles de pantalla
        vc = self._view_crop
        if vc:  # la vista está recortada: coordenadas de la foto entera
            x, y = vc[0] + x * (vc[2] - vc[0]), vc[1] + y * (vc[3] - vc[1])
        if ev == "press":
            self._push_history()
            self._drag = (x, y)
            hit = None if right else self._gz_hit(spx, spy)
            self._gz_mode = hit
            self._gz_start = {"at": (x, y), "p": copy.deepcopy(p)}
            if not self.mask_show and not hit:
                self.b_mshow.setChecked(True)
        if self._drag is None:
            return
        x0, y0 = self._drag
        if getattr(self, "_gz_mode", None):
            if ev != "press":
                self._gz_drag(self._gz_mode, x, y, spx, spy)
        elif t == "point":
            p.update(cx=round(x, 4), cy=round(y, 4))
        elif t == "radial":
            p.update(cx=x0, cy=y0, rx=max(0.02, abs(x - x0)), ry=max(0.02, abs(y - y0)))
        elif t == "linear":
            if ev == "press":
                return
            p.update(x0=x0, y0=y0, x1=x, y1=y)
        elif t == "brush":
            size = p.get("_size", 6) / 200.0
            p.setdefault("strokes", []).append([round(x, 4), round(y, 4), size, -1 if right else 1])
        if ev == "release":
            self._drag = None
            mode, self._gz_mode = getattr(self, "_gz_mode", None), None
            if t == "linear" and not mode and math.hypot(x - x0, y - y0) < 0.005:
                p.update(self._gz_start["p"] if self._gz_start else {})  # un clic suelto no deja la lineal en nada
            if t == "linear" and "feather" in self.mrows:
                self.mrows["feather"].set_value(round(self._linear_width(p) * 100))
            self.canvas.update()
            self.request(draft=False)
            self._save_timer.start(400)
        else:
            self.canvas.update()
            self.request(draft=True)

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
            li.setSizeHint(QSize(126, 96))
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
        if self.crop_mode:
            self.b_crop.setChecked(False)
        self._save_cur()
        self.cur = self.items[self.film.item(row).data(ROLE)]
        it = self.cur
        if it.settings is None:
            it.settings = self._initial_settings(it)
            it.needs_auto_ev = self.events_lookup(it.path) is None  # sin análisis de Events: exposición del proxy
        self.title.setText(it.name)
        self._load_controls()
        self._show_exif(it)
        if it.path in self._proxy:
            self.request(draft=False)
        else:
            self.canvas.set(msg="Cargando…")
            threading.Thread(target=self._load_proxy, args=(it.path,), daemon=True).start()

    def _show_exif(self, it: Item):
        ph = E.Photo(path=it.path)
        try:
            E.read_meta(ph)
        except Exception:
            pass
        m = ph.meta
        bits = []
        if ph.iso:
            bits.append(f"ISO {ph.iso}")
        if m.get("exposure"):
            bits.append(f"{str(m['exposure']).strip('[]')} s")
        if m.get("fnumber"):
            fn = str(m["fnumber"]).strip("[]")
            try:
                a, b = (fn.split("/") + ["1"])[:2]
                fn = f"{float(a) / float(b):.1f}"
            except ValueError:
                pass
            bits.append(f"f/{fn}")
        if m.get("focal"):
            fo = str(m["focal"]).strip("[]")
            try:
                a, b = (fo.split("/") + ["1"])[:2]
                fo = f"{float(a) / float(b):.0f}"
            except ValueError:
                pass
            bits.append(f"{fo} mm")
        self.exif.setText("   ".join(bits))
        cam = " ".join(str(m.get(k, "")).strip() for k in ("model",) if m.get(k))
        self.nav_info.setText(f"{it.name}  {('· ' + cam) if cam else ''}")

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
            self.canvas.before = None
            self.request(draft=False)

    # ---------------------------------------------------------- render en segundo plano
    def request(self, draft: bool):
        if not self.cur or self.cur.path not in self._proxy:
            return
        self._req += 1
        self._req_angle = (self._req, self._cur_angle())
        self._jobs.put((self._req, self.cur.path, copy.deepcopy(self.cur.settings), draft,
                        self.mask_i if self.mask_show else None, self.canvas.cmp.active, self.crop_mode))
        if draft:
            self._full_timer.start(260)

    def _render_loop(self):
        while True:
            job = self._jobs.get()
            while not self._jobs.empty():  # solo cuenta la última petición
                job = self._jobs.get()
            n, path, s, draft, show = job[:5]
            want_before = len(job) > 5 and job[5]
            cropped = show is None and not (len(job) > 6 and job[6])
            if path not in self._proxy:
                continue
            lin = self._proxy[path][0]
            if draft:
                f = DRAFT_PX / max(lin.shape[:2])
                lin = cv2.resize(lin, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else lin
                s = dict(s, nr_luma=0)  # la limpieza de grano solo en la vista definitiva
            t = time.time()
            try:
                img = SE.render(lin, s, seed=hash(path) & 0xFFFF, key=path, show_mask=show,
                                crop=cropped)
            except Exception as e:
                self.rendered.emit(n, None, f"{type(e).__name__}: {e}", draft)
                continue
            before = None
            if want_before:
                key = (path, lin.shape, tuple(s.get("crop", (0, 0, 1, 1))), s.get("angle", 0), cropped)
                before = self._before_cache.get(key)
                if before is None:
                    try:
                        neutral = SE.new_settings(crop=s.get("crop", [0.0, 0.0, 1.0, 1.0]), angle=s.get("angle", 0))
                        before = SE.render(lin, neutral, crop=cropped)
                        self._before_cache.clear()
                        self._before_cache[key] = before
                    except Exception:
                        before = None
            vc = list(s.get("crop", [0.0, 0.0, 1.0, 1.0])) if cropped else None
            self.rendered.emit(n, img, (time.time() - t, before, vc if vc != [0.0, 0.0, 1.0, 1.0] else None), draft)

    def _tile(self, box):
        """Zona visible a resolución completa con los ajustes actuales (zoom > vista previa)."""
        it = self.cur
        if not it:
            return None
        if self._full is None or self._full[0] != it.path:
            self._full = (it.path, E.load_linear(it.path, half=False, rebuild_highlights=True))
        lin = self._full[1]
        s = copy.deepcopy(it.settings)
        c = (self._view_crop or [0.0, 0.0, 1.0, 1.0])
        box_rot = (c[0] + box[0] * (c[2] - c[0]), c[1] + box[1] * (c[3] - c[1]),
                   c[0] + box[2] * (c[2] - c[0]), c[1] + box[3] * (c[3] - c[1]))
        flat = dict(s, angle=0, crop=[0.0, 0.0, 1.0, 1.0])
        return Z.region(lin, float(s.get("angle", 0)), box_rot,
                        lambda part: SE.render(part, flat, seed=hash(it.path) & 0xFFFF, key=it.path + "#zona"),
                        E._safe_rect)

    def _on_rendered(self, n, img, info, draft):
        if draft and n < self._req:  # llegó tarde: ya hay una petición más nueva en camino
            return
        if img is None:
            return self.canvas.set(msg=f"Error al revelar: {info}")
        pm = _pixmap(img)
        if isinstance(info, tuple) and len(info) > 2:
            self._view_crop = info[2]
        if not draft and self.cur:
            fw, fh = E.full_size(self.cur.path)
            c = self._view_crop or [0.0, 0.0, 1.0, 1.0]
            self.canvas.zoom.reset(key=(self.cur.path, n), full_size=(int(fw * (c[2] - c[0])) or fw, fh))
            self.canvas.zoom.schedule()
        before = info[1] if isinstance(info, tuple) else None
        if before is not None:
            self.canvas.before = _pixmap(before)
        ra = getattr(self, "_req_angle", None)
        if self.canvas.crop.active and ra and n == ra[0]:
            self.canvas.crop.angle = ra[1]  # la imagen nueva ya viene girada
            if self.canvas.crop._rot is None:
                self.canvas.crop.target = self._cur_angle()
        self.canvas.set(pm=pm, msg="" if not draft else "vista rápida")
        self.hist.set(img)
        self.nav.setPixmap(pm.scaled(self.nav.width() - 4, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ---------------------------------------------------------- blanco y negro
    def set_bw_mode(self, on: bool):
        """Botón «B/N» (V): la foto pasa a blanco y negro y se abre lo necesario para retocarla en B/N."""
        if not self.cur:
            self.b_bw_bar.setChecked(False)
            return
        self.bw_cb.setChecked(bool(on))  # guarda el ajuste (historial incluido) y sincroniza la interfaz
        if on:
            self.pages.setCurrentIndex(1)
            self._tab_btns[1].setChecked(True)
            self._bw_sec.head.setChecked(True)
            self._hsl_sec.head.setChecked(True)
            self._hsl_tabs.setCurrentIndex(2)
            self.status.setText("Blanco y negro: elige el estilo, retoca luz y contraste en Luz, y aclara u "
                                "oscurece cada color en «Mezcla B/N».")

    def _sync_bw_ui(self, on: bool):
        self.bw_style.setEnabled(on)
        self.b_bw_bar.blockSignals(True)
        self.b_bw_bar.setChecked(on)
        self.b_bw_bar.blockSignals(False)
        self._hsl_sec.title = "Mezcla B/N (luz de cada color)" if on else "Mezcla de color (HSL)"
        self._hsl_sec._label(self._hsl_sec.head.isChecked())
        self._hsl_tabs.setTabText(2, "Mezcla B/N" if on else "Luminancia")

    # ---------------------------------------------------------- ajustes
    def _load_controls(self, refresh_history: bool = True):
        s = self.cur.settings
        if refresh_history:
            self._refresh_history()
        for k, r in self.rows.items():
            r.set_value(s.get(k, SE.DEFAULTS.get(k, 0)))
        self.bw_cb.blockSignals(True)
        self.bw_cb.setChecked(bool(s.get("bw")))
        self.bw_cb.blockSignals(False)
        self.bw_style.setCurrentText(s.get("bw_style") or "Editorial")
        self._sync_bw_ui(bool(s.get("bw")))
        if hasattr(self, "crop_angle"):
            self._sync_angle_rows(float(s.get("angle", 0) or 0))
        i = self.lut_cb.findData(s.get("lut") or None)
        self.lut_cb.setCurrentIndex(max(0, i))
        self._show_rating()
        self._refresh_masks(select=0 if s.get("masks") else None)

    def _push_history(self, label: str = ""):
        self._last_label = label or self._last_label or "Ajuste"
        it = self.cur
        it.history.append(copy.deepcopy(it.settings))
        it.history = it.history[-100:]
        it.redo.clear()

    def _set(self, key, value, final=True):
        if not self.cur:
            return
        if final:
            r = self.rows.get(key)
            name = r.lab.text() if r else key
            self._push_history(f"{name} {value:+g}" if isinstance(value, (int, float)) and not isinstance(value, bool)
                               else f"{name}")
        self.cur.settings[key] = value
        if key == "angle":
            if self.crop_mode:
                self.canvas.crop.target = float(value)
                self.canvas.update()
            if hasattr(self, "crop_angle"):
                self.crop_angle.blockSignals(True)
                self.crop_angle.set_value(value)
                self.crop_angle.blockSignals(False)
        self.request(draft=not final)
        self._save_timer.start(600)
        self.film.viewport().update()

    def _save_cur(self):
        if self.cur and self.folder and self.cur.settings is not None:
            SE.save(self.folder, self.cur.path, self.cur.settings)
            tl = self.cur.__dict__.setdefault("timeline", [])
            if not tl or tl[-1][1] != self.cur.settings:
                tl.append((self._last_label or "Ajuste", copy.deepcopy(self.cur.settings)))
                del tl[:-60]
                self._refresh_history()

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

    def _flag(self):
        return int((self.cur.settings or {}).get("flag", 0)) if self.cur else 0

    def _show_rating(self):
        n = self._rating()
        for i, b in enumerate(self.stars, start=1):
            b.setText("★" if i <= n else "☆")
            b.setStyleSheet(f"QToolButton {{ border: none; font-size: 12pt; background: transparent; "
                            f"color: {T.WARN if i <= n else T.FAINT}; }}")
        fl = self._flag()
        self.b_pick.setStyleSheet(f"color: {T.TEXT_STRONG if fl == 1 else T.FAINT};")
        self.b_rej.setStyleSheet(f"color: {T.DANGER if fl == -1 else T.FAINT};")
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
        self.set_compare("off" if self.canvas.cmp.mode == "full" else "full")

    def next_split(self):
        self.canvas.cmp.next_split()
        self.set_compare(self.canvas.cmp.mode)

    def set_compare(self, mode):
        self.canvas.cmp.mode = mode
        self.cmp_bar.set_mode(mode)
        if mode != "off" and self.canvas.before is None:
            self.request(draft=False)  # hace falta el «antes» con la misma geometría
        self.canvas.update()

    def _set_before(self, on):
        self.set_compare("full" if on else "off")
        self.canvas.update()

    # ---------------------------------------------------------- presets y LUT
    def reload_presets(self, *_):
        if hasattr(self, "preset_list"):
            self.preset_list.clear()
            which = self.preset_search.currentIndex()
            groups = []
            if which in (0, 1):
                groups.append(("MIS PRESETS", SE.list_presets(self.cfg_dir)))
            if which in (0, 2):
                groups.append(("LIGHTCRAFT", SE.LIGHTCRAFT_PRESETS))
            for title, items in groups:
                head = QListWidgetItem(title)
                head.setFlags(Qt.NoItemFlags)
                head.setForeground(QColor(T.FAINT))
                self.preset_list.addItem(head)
                for name, st in items.items():
                    it = QListWidgetItem("   " + name)
                    it.setData(ROLE, st)
                    self.preset_list.addItem(it)
        if not hasattr(self, "preset_cb"):
            return
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
        size.addItems(["Original", "2048 px lado largo", "1080 px lado largo",
                       "Ampliada ×2 con IA (copias grandes; unos minutos por foto)"])
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
        long_side = {0: None, 1: 2048, 2: 1080, 3: None}[size.currentIndex()]
        upscale = 2.0 if size.currentIndex() == 3 else 1.0
        if upscale > 1:
            import fx_engine as FX
            FX.configure(os.path.join(self.cfg_dir, "modelos"))
        out = os.path.join(self.folder, "Studio")
        quality = q.value()

        def job():
            for i, it in enumerate(targets):
                self.export_progress.emit(i, len(targets), f"Exportando {it.name}…")
                try:
                    SE.export(it.path, it.settings, os.path.join(out, it.name + (".x2.jpg" if upscale > 1 else ".jpg")),
                              quality, long_side, upscale,
                              (lambda k, n, i=i, it=it: self.export_progress.emit(
                                  i, len(targets), f"Ampliando {it.name}… {k * 100 // max(n, 1)} %")) if upscale > 1
                              else None)
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
