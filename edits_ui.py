"""Piezas de interfaz de Edits al estilo de PhotoCraft (ArtCraft) / Photoshop: panel de Capas (ojo, miniatura,
nombre y tipo), editores de Curvas y Niveles sobre el histograma, paneles de ajuste, muestras de color
frontal/fondo, pestañas de panel y contorno de selección («hormigas»)."""
from __future__ import annotations

import os

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout,
                               QWidget)

import edits_adjust as A
import theme as T

ROLE_LAYER = Qt.UserRole + 21
EYE_W, THUMB = 30, 32


# ------------------------------------------------------------------ pestañas de panel (Propiedades | Ajustes…)

class PanelTabs(QWidget):
    """Cabecera de pestañas fina como la de los paneles de PhotoCraft, con su contenido debajo."""
    changed = Signal(int)

    def __init__(self, titles):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QFrame()
        head.setObjectName("panelHead")
        head.setStyleSheet(f"QFrame#panelHead {{ background: {T.SURFACE}; border-bottom: 1px solid {T.LINE}; }}"
                           f"QPushButton {{ background: transparent; border: none; border-bottom: 2px solid "
                           f"transparent; border-radius: 0; padding: 7px 10px; color: {T.MUTED}; }}"
                           f"QPushButton:checked {{ color: {T.TEXT_STRONG}; border-bottom: 2px solid {T.ACCENT}; }}"
                           f"QPushButton:hover {{ color: {T.TEXT_STRONG}; }}")
        h = QHBoxLayout(head)
        h.setContentsMargins(6, 0, 6, 0)
        h.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons = []
        for i, t in enumerate(titles):
            b = QPushButton(t)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, i=i: self.set_current(i))
            self.group.addButton(b)
            h.addWidget(b)
            self.buttons.append(b)
        h.addStretch(1)
        self.menu_btn = QToolButton()
        self.menu_btn.setText("≡")
        self.menu_btn.setStyleSheet(f"QToolButton {{ border: none; color: {T.MUTED}; font-size: 12pt; }}")
        h.addWidget(self.menu_btn)
        v.addWidget(head)
        self.pages = []
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.body, 1)
        self.cur = -1

    def add_page(self, w: QWidget):
        self.pages.append(w)
        w.hide()
        self.body.addWidget(w, 1)
        if self.cur < 0:
            self.set_current(0)

    def set_current(self, i):
        self.cur = i
        for k, (b, w) in enumerate(zip(self.buttons, self.pages)):
            b.setChecked(k == i)
            w.setVisible(k == i)
        self.changed.emit(i)


# ------------------------------------------------------------------ miniaturas e iconos de capa

def _glyph_icon(kind: str, size=THUMB) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(QColor("#2a2a2a"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#d8d8d8"), 1.6))
    p.setBrush(Qt.NoBrush)
    c = QPointF(size / 2, size / 2)
    if kind == "adjust":  # círculo mitad lleno (como el icono de capa de ajuste)
        p.drawEllipse(c, 9, 9)
        path = QPainterPath()
        path.moveTo(c.x(), c.y() - 9)
        path.arcTo(QRectF(c.x() - 9, c.y() - 9, 18, 18), 90, 180)
        path.closeSubpath()
        p.setBrush(QColor("#d8d8d8"))
        p.drawPath(path)
    elif kind == "text":
        f = QFont("Georgia")
        f.setPixelSize(int(size * 0.62))
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "T")
    else:
        glyph = {"retouch": "✚", "skin": "◐", "light": "◉", "liquify": "≈", "background": "◫",
                 "logo": "◆", "image": "▣"}.get(kind, "■")
        f = QFont(p.font())
        f.setPixelSize(int(size * 0.5))
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, glyph)
    p.end()
    return pm


def _mask_icon(size=THUMB) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(QColor("#000000"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#ffffff"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(size / 2, size / 2), size * 0.3, size * 0.36)
    p.end()
    return pm


def image_thumb(path: str, size=THUMB) -> QPixmap | None:
    pm = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
    if pm.isNull():
        return None
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def np_thumb(rgb01: np.ndarray, size=THUMB) -> QPixmap:
    h, w = rgb01.shape[:2]
    f = size / max(h, w)
    small = cv2.resize(rgb01, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
    a = np.ascontiguousarray((np.clip(small, 0, 1) * 255).astype(np.uint8))
    return QPixmap.fromImage(QImage(a.data, a.shape[1], a.shape[0], 3 * a.shape[1], QImage.Format_RGB888).copy())


class LayersDelegate(QStyledItemDelegate):
    """Fila de capa: ojo · miniatura (· máscara) · nombre y tipo (como PhotoCraft). El ojo es clicable."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view   # EditsView: info_for(row_data) -> dict

    def sizeHint(self, *_):
        return QSize(200, 40)

    def paint(self, p: QPainter, opt, idx):
        d = idx.data(ROLE_LAYER) or {}
        r = opt.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        sel = bool(opt.state & QStyle.State_Selected)
        p.fillRect(r, QColor("#3a3a3a") if sel else QColor(T.SURFACE))
        p.setPen(QColor(T.LINE))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        # ojo
        eye = QRectF(r.x() + 6, r.y() + (r.height() - 16) / 2, 18, 16)
        if d.get("visible", True):
            p.setPen(QPen(QColor("#cfcfcf"), 1.3))
            path = QPainterPath()
            path.moveTo(eye.left(), eye.center().y())
            path.quadTo(eye.center().x(), eye.top() - 2, eye.right(), eye.center().y())
            path.quadTo(eye.center().x(), eye.bottom() + 2, eye.left(), eye.center().y())
            p.drawPath(path)
            p.setBrush(QColor("#cfcfcf"))
            p.drawEllipse(eye.center(), 2.6, 2.6)
            p.setBrush(Qt.NoBrush)
        # miniatura (y máscara)
        x = r.x() + EYE_W
        if d.get("clip"):  # recortada a la imagen de debajo: sangría y flecha, como en Photoshop
            p.setPen(QPen(QColor("#bdbdbd"), 1.4))
            cy = r.y() + r.height() / 2
            p.drawLine(QPointF(x + 3, cy - 8), QPointF(x + 3, cy + 3))
            p.drawLine(QPointF(x + 3, cy + 3), QPointF(x + 11, cy + 3))
            p.drawLine(QPointF(x + 8, cy), QPointF(x + 11, cy + 3))
            p.drawLine(QPointF(x + 8, cy + 6), QPointF(x + 11, cy + 3))
            x += 16
        tr = QRect(int(x), r.y() + (r.height() - THUMB) // 2, THUMB, THUMB)
        thumb = d.get("thumb")
        if isinstance(thumb, QPixmap):
            p.fillRect(tr, QColor("#202020"))
            t = thumb.scaled(THUMB, THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(tr.x() + (THUMB - t.width()) // 2, tr.y() + (THUMB - t.height()) // 2, t)
        p.setPen(QPen(QColor("#777777") if sel else QColor("#444444"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(tr)
        x += THUMB + 6
        if d.get("mask"):
            mr = QRect(int(x), tr.y(), THUMB, THUMB)
            p.drawPixmap(mr, _mask_icon())
            p.setPen(QPen(QColor("#777777"), 1))
            p.drawRect(mr)
            x += THUMB + 6
        # nombre y tipo
        f = QFont(p.font())
        f.setPointSizeF(9)
        f.setItalic(bool(d.get("background_row")))
        p.setFont(f)
        p.setPen(QColor(T.TEXT_STRONG if sel else T.TEXT))
        right = r.right() - (24 if d.get("locked") else 8)
        p.drawText(QRectF(x, r.y() + 3, right - x, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(d.get("name", ""), Qt.ElideRight, int(right - x)))
        if d.get("subtitle"):
            f2 = QFont(p.font())
            f2.setPointSizeF(7.5)
            f2.setItalic(False)
            p.setFont(f2)
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(x, r.y() + 20, right - x, 16), Qt.AlignLeft | Qt.AlignVCenter, d["subtitle"])
        if d.get("locked"):
            p.setPen(QPen(QColor(T.MUTED), 1.2))
            lk = QRectF(r.right() - 18, r.y() + r.height() / 2 - 2, 9, 7)
            p.drawRect(lk)
            p.drawArc(QRectF(lk.x() + 1.5, lk.y() - 5, 6, 8), 0, 180 * 16)
        p.restore()

    def editorEvent(self, event, model, opt, idx):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.MouseButtonRelease and event.position().x() - opt.rect.x() < EYE_W:
            d = idx.data(ROLE_LAYER) or {}
            if d.get("id") and not d.get("background_row"):
                self.view.toggle_visible(d["id"])
                return True
        return super().editorEvent(event, model, opt, idx)


# ------------------------------------------------------------------ histograma

def histogram(rgb01: np.ndarray, channel: str = "rgb", bins: int = 128) -> np.ndarray:
    small = rgb01[::4, ::4]
    if channel == "rgb":
        x = 0.2126 * small[..., 0] + 0.7152 * small[..., 1] + 0.0722 * small[..., 2]
    else:
        x = small[..., "rgb".index(channel)]
    h = np.histogram(x, bins, (0, 1))[0].astype(np.float32)
    m = max(float(np.percentile(h, 99)), 1.0)
    return np.clip(h / m, 0, 1)


_CH_COLOR = {"rgb": "#d8d8d8", "r": "#ff5a5a", "g": "#5adc78", "b": "#6aa0ff"}


class CurveEditor(QWidget):
    """Editor de Curvas sobre el histograma: clic añade un punto, arrastrar lo mueve, sacarlo fuera lo quita."""
    changed = Signal(object, bool)  # puntos, ¿definitivo?

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.points = [[0.0, 0.0], [1.0, 1.0]]
        self.hist = None
        self.channel = "rgb"
        self._drag = None
        self.setMouseTracking(True)

    def set(self, points, hist, channel):
        self.points = [list(p) for p in points]
        self.hist, self.channel = hist, channel
        self.update()

    def _r(self):
        return QRectF(self.rect()).adjusted(8, 6, -8, -14)

    def _to_px(self, x, y):
        r = self._r()
        return QPointF(r.x() + x * r.width(), r.bottom() - y * r.height())

    def _from_px(self, pt):
        r = self._r()
        return (min(1, max(0, (pt.x() - r.x()) / r.width())), min(1, max(0, (r.bottom() - pt.y()) / r.height())))

    def mousePressEvent(self, e):
        x, y = self._from_px(e.position())
        near = min(range(len(self.points)), key=lambda i: abs(self.points[i][0] - x) + abs(self.points[i][1] - y))
        if abs(self.points[near][0] - x) < 0.04 and abs(self.points[near][1] - y) < 0.06:
            self._drag = near
        else:
            self.points.append([x, float(np.interp(x, *zip(*sorted(self.points))))])
            self.points.sort()
            self._drag = next(i for i, p in enumerate(self.points) if p[0] == x)
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        x, y = self._from_px(e.position())
        i = self._drag
        lo = self.points[i - 1][0] + 0.01 if i > 0 else 0.0
        hi = self.points[i + 1][0] - 0.01 if i < len(self.points) - 1 else 1.0
        out = e.position().y() < -20 or e.position().y() > self.height() + 20
        if out and 0 < i < len(self.points) - 1:
            self.points.pop(i)
            self._drag = None
        else:
            self.points[i] = [round(min(hi, max(lo, x)), 4), round(y, 4)]
        self.update()
        self.changed.emit([list(p) for p in self.points], False)

    def mouseReleaseEvent(self, e):
        if self._drag is not None or True:
            self.changed.emit([list(p) for p in self.points], True)
        self._drag = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._r()
        p.fillRect(self.rect(), QColor(T.SURFACE))
        p.fillRect(r, QColor("#161616"))
        if self.hist is not None:
            path = QPainterPath()
            path.moveTo(r.left(), r.bottom())
            n = len(self.hist)
            for i, v in enumerate(self.hist):
                path.lineTo(r.left() + r.width() * i / (n - 1), r.bottom() - r.height() * float(v) * 0.95)
            path.lineTo(r.right(), r.bottom())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(110, 110, 110, 120))
            p.drawPath(path)
        p.setPen(QPen(QColor("#2f2f2f"), 1))
        for k in range(1, 4):
            p.drawLine(QPointF(r.left() + r.width() * k / 4, r.top()), QPointF(r.left() + r.width() * k / 4, r.bottom()))
            p.drawLine(QPointF(r.left(), r.top() + r.height() * k / 4), QPointF(r.right(), r.top() + r.height() * k / 4))
        p.setPen(QPen(QColor("#444444"), 1, Qt.DashLine))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.top()))
        lut = A.curve_lut(self.points)[::32]
        poly = QPolygonF([self._to_px(i / (len(lut) - 1), float(v)) for i, v in enumerate(lut)])
        p.setPen(QPen(QColor(_CH_COLOR.get(self.channel, "#ddd")), 1.8))
        p.drawPolyline(poly)
        p.setBrush(QColor("#161616"))
        for x, y in self.points:
            p.drawRect(QRectF(self._to_px(x, y) - QPointF(4, 4), QSize(8, 8)))
        # degradado de entrada
        g = QRectF(r.left(), r.bottom() + 5, r.width(), 6)
        from PySide6.QtGui import QLinearGradient
        lg = QLinearGradient(g.topLeft(), g.topRight())
        lg.setColorAt(0, QColor("black"))
        lg.setColorAt(1, QColor(_CH_COLOR.get(self.channel, "white")) if self.channel != "rgb" else QColor("white"))
        p.fillRect(g, QBrush(lg))


class HistView(QWidget):
    """Histograma pequeño (para Niveles) con los marcadores de entrada negro / gamma / blanco."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(110)
        self.hist = None
        self.marks = (0.0, 1.0, 1.0)
        self.channel = "rgb"

    def set(self, hist, marks, channel):
        self.hist, self.marks, self.channel = hist, marks, channel
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 4, -6, -16)
        p.fillRect(self.rect(), QColor(T.SURFACE))
        p.fillRect(r, QColor("#161616"))
        if self.hist is not None:
            n = len(self.hist)
            w = r.width() / n
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(_CH_COLOR.get(self.channel, "#bbb")))
            for i, v in enumerate(self.hist):
                p.drawRect(QRectF(r.left() + i * w, r.bottom() - r.height() * float(v), max(1.0, w), r.height() * float(v)))
        b, g, wt = self.marks
        mid = b + (wt - b) * 0.5 ** (1.0 / max(g, 0.01))  # dónde cae el gris medio con esa gamma
        for x, col in ((b, "#000000"), (mid, "#888888"), (wt, "#ffffff")):
            px = r.left() + x * r.width()
            tri = QPolygonF([QPointF(px, r.bottom() + 2), QPointF(px - 5, r.bottom() + 12), QPointF(px + 5, r.bottom() + 12)])
            p.setPen(QPen(QColor("#999999"), 1))
            p.setBrush(QColor(col))
            p.drawPolygon(tri)


# ------------------------------------------------------------------ muestras de color frontal / fondo

class Swatches(QWidget):
    """Color frontal y de fondo (como en la barra de herramientas de Photoshop). Clic: elegir; X: intercambiar."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(40, 40)
        self.fg, self.bg = QColor("#000000"), QColor("#ffffff")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Color frontal y de fondo · clic para elegir · X para intercambiar · D: blanco y negro")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QPen(QColor("#888888"), 1))
        p.setBrush(self.bg)
        p.drawRect(15, 15, 20, 20)
        p.setBrush(self.fg)
        p.drawRect(4, 4, 20, 20)

    def mousePressEvent(self, e):
        from PySide6.QtWidgets import QColorDialog
        front = e.position().x() < 24 and e.position().y() < 24
        c = QColorDialog.getColor(self.fg if front else self.bg, self, "Color frontal" if front else "Color de fondo")
        if c.isValid():
            if front:
                self.fg = c
            else:
                self.bg = c
            self.update()
            self.changed.emit()

    def swap(self):
        self.fg, self.bg = self.bg, self.fg
        self.update()
        self.changed.emit()

    def reset(self):
        self.fg, self.bg = QColor("#000000"), QColor("#ffffff")
        self.update()
        self.changed.emit()


# ------------------------------------------------------------------ contorno de la selección

def selection_outline(alpha: np.ndarray, box, max_px: int = 900) -> list:
    """Contornos (listas de puntos 0..1 sobre la foto MOSTRADA, ya recortada) del borde de la selección."""
    h, w = alpha.shape
    x0, y0, x1, y1 = box
    a = alpha[int(y0 * h):max(int(y0 * h) + 1, int(y1 * h)), int(x0 * w):max(int(x0 * w) + 1, int(x1 * w))]
    ch, cw = a.shape
    f = min(1.0, max_px / max(ch, cw))
    if f < 1:
        a = cv2.resize(a, (max(1, int(cw * f)), max(1, int(ch * f))), interpolation=cv2.INTER_AREA)
    m = (a > 0.5).astype(np.uint8)
    cs, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    hh, ww = m.shape
    return [[(float(px) / max(1, ww - 1), float(py) / max(1, hh - 1)) for px, py in c.reshape(-1, 2)]
            for c in cs if len(c) >= 3]


# ------------------------------------------------------------------ panel de ajustes (botones) y páginas

ADJ_ICONS = {"brightness": "☀", "levels": "▥", "curves": "∿", "exposure": "±", "vibrance": "▽",
             "hue_sat": "◑", "color_balance": "⚖", "black_white": "◧", "photo_filter": "◎", "invert": "◩",
             "posterize": "▦", "threshold": "▬"}


def adjustments_grid(on_pick) -> QWidget:
    """Pestaña «Ajustes»: una cuadrícula de botones que crean capas de ajuste (como en Photoshop)."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(10, 10, 10, 10)
    t = QLabel("Añadir un ajuste")
    t.setObjectName("meta")
    v.addWidget(t)
    g = QGridLayout()
    g.setSpacing(4)
    for i, k in enumerate(A.ORDER):
        b = QToolButton()
        b.setText(ADJ_ICONS.get(k, "●"))
        b.setToolTip(A.ADJUSTMENTS[k][0] + " (con la selección como máscara, si hay)")
        b.setFixedSize(42, 36)
        b.setStyleSheet(f"QToolButton {{ font-size: 14pt; background: {T.SURFACE_2}; border: 1px solid {T.LINE};"
                        f" border-radius: 4px; color: {T.TEXT}; }} QToolButton:hover {{ border-color: {T.ACCENT}; }}")
        b.clicked.connect(lambda _, k=k: on_pick(k))
        g.addWidget(b, i // 6, i % 6)
    v.addLayout(g)
    names = QLabel(" · ".join(A.ADJUSTMENTS[k][0] for k in A.ORDER))
    names.setObjectName("meta")
    names.setWordWrap(True)
    v.addWidget(names)
    v.addStretch(1)
    return w


class AdjustPanel(QWidget):
    """Propiedades de una capa de ajuste (sliders y editores según el tipo), como el panel de PhotoCraft."""
    changed = Signal(object, bool)  # parámetros nuevos, ¿definitivo?
    reset = Signal()

    def __init__(self, slider_cls):
        super().__init__()
        self.SliderRow = slider_cls
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(6)
        self.title = QLabel("")
        self.title.setStyleSheet(f"color: {T.TEXT_STRONG}; font-weight: 600;")
        self.v.addWidget(self.title)
        self.body = QWidget()
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(0, 0, 0, 0)
        self.bl.setSpacing(5)
        self.v.addWidget(self.body)
        self.b_reset = QPushButton("Restablecer valores")
        self.b_reset.setObjectName("ghost")
        self.b_reset.clicked.connect(self.reset.emit)
        self.v.addWidget(self.b_reset)
        self.kind = None
        self.p = {}
        self.img = None

    def _clear(self):
        while self.bl.count():
            it = self.bl.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()  # fuera de la vista ya: deleteLater tarda un ciclo y se vería encima del nuevo panel
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    ww = lay.takeAt(0).widget()
                    if ww is not None:
                        ww.hide()
                        ww.setParent(None)
                        ww.deleteLater()

    def _emit(self, final=True):
        self.changed.emit(dict(self.p), final)

    def _slider(self, label, lo, hi, step, value, setter, neutral=0, key="x"):
        s = self.SliderRow(key, label, lo, hi, step, neutral)
        s.set_value(value)
        s.changed.connect(lambda k, val, final: (setter(val), self._emit(final)))
        self.bl.addWidget(s)
        return s

    def load(self, kind: str, p: dict, img: np.ndarray | None):
        """Reconstruye los controles para el ajuste `kind` (solo si cambia de tipo o de capa)."""
        import copy
        self.kind, self.p, self.img = kind, copy.deepcopy(p), img
        self.title.setText(A.ADJUSTMENTS[kind][0])
        self._clear()
        getattr(self, "_ui_" + kind, self._ui_none)()

    def _ui_none(self):
        self.bl.addWidget(QLabel("Este ajuste no tiene opciones."))

    def _ui_brightness(self):
        self._slider("Brillo", -150, 150, 1, self.p["brightness"], lambda v: self.p.update(brightness=v))
        self._slider("Contraste", -50, 100, 1, self.p["contrast"], lambda v: self.p.update(contrast=v))

    def _channel_combo(self, on_change):
        cb = QComboBox()
        for k, label in (("rgb", "RGB"), ("r", "Rojo"), ("g", "Verde"), ("b", "Azul")):
            cb.addItem(label, k)
        cb.setCurrentIndex(["rgb", "r", "g", "b"].index(self.p.get("channel", "rgb")))
        cb.currentIndexChanged.connect(lambda i: on_change(cb.itemData(i)))
        row = QHBoxLayout()
        lab = QLabel("Canal:")
        lab.setObjectName("meta")
        row.addWidget(lab)
        row.addWidget(cb, 1)
        self.bl.addLayout(row)
        return cb

    def _ui_curves(self):
        ed = CurveEditor()

        def show():
            ch = self.p.get("channel", "rgb")
            ed.set(self.p["curves"][ch], histogram(self.img, ch) if self.img is not None else None, ch)

        def ch_changed(ch):
            self.p["channel"] = ch
            show()

        def pts_changed(pts, final):
            self.p["curves"][self.p.get("channel", "rgb")] = pts
            self._emit(final)

        self._channel_combo(ch_changed)
        ed.changed.connect(pts_changed)
        self.bl.addWidget(ed)
        hint = QLabel("Clic para añadir un punto · arrástralo · sácalo fuera para quitarlo")
        hint.setObjectName("meta")
        hint.setWordWrap(True)
        self.bl.addWidget(hint)
        show()

    def _ui_levels(self):
        hv = HistView()
        rows = {}

        def cur():
            return self.p["levels"][self.p.get("channel", "rgb")]

        def show():
            ch = self.p.get("channel", "rgb")
            c = cur()
            hv.set(histogram(self.img, ch) if self.img is not None else None, (c[0], c[1], c[2]), ch)
            for i, s in rows.items():
                s.set_value(c[i] * (255 if i != 1 else 1))

        def setv(i, v):
            c = cur()
            c[i] = float(v) / (255.0 if i != 1 else 1.0)
            ch = self.p.get("channel", "rgb")
            hv.set(hv.hist, (c[0], c[1], c[2]), ch)

        def ch_changed(ch):
            self.p["channel"] = ch
            show()

        self._channel_combo(ch_changed)
        self.bl.addWidget(hv)
        for i, (label, lo, hi, step, neutral) in enumerate((("Entrada: negro", 0, 253, 1, 0), ("Gamma", 0.1, 9.99, 0.01, 1),
                                                              ("Entrada: blanco", 2, 255, 1, 255),
                                                              ("Salida: negro", 0, 255, 1, 0),
                                                              ("Salida: blanco", 0, 255, 1, 255))):
            rows[i] = self._slider(label, lo, hi, step, cur()[i] * (255 if i != 1 else 1),
                                   lambda v, i=i: setv(i, v), neutral)
        auto = QPushButton("Automático")
        auto.setObjectName("ghost")

        def do_auto():
            if self.img is None:
                return
            y = 0.2126 * self.img[..., 0] + 0.7152 * self.img[..., 1] + 0.0722 * self.img[..., 2]
            lo, hi = np.percentile(y[::4, ::4], (0.1, 99.9))
            self.p["levels"]["rgb"] = [float(lo), 1.0, float(max(hi, lo + 0.02)), 0.0, 1.0]
            self.p["channel"] = "rgb"
            show()
            self._emit(True)

        auto.clicked.connect(do_auto)
        self.bl.addWidget(auto)
        show()

    def _ui_exposure(self):
        self._slider("Exposición", -5, 5, 0.01, self.p["exposure"], lambda v: self.p.update(exposure=v))
        self._slider("Desplazamiento", -0.5, 0.5, 0.001, self.p["offset"], lambda v: self.p.update(offset=v))
        self._slider("Corrección gamma", 0.01, 9.99, 0.01, self.p["gamma"], lambda v: self.p.update(gamma=v), 1)

    def _ui_vibrance(self):
        self._slider("Intensidad", -100, 100, 1, self.p["vibrance"], lambda v: self.p.update(vibrance=v), key="vibrance")
        self._slider("Saturación", -100, 100, 1, self.p["saturation"], lambda v: self.p.update(saturation=v),
                     key="saturation")

    def _ui_hue_sat(self):
        self._slider("Tono", -180, 180, 1, self.p["hue"], lambda v: self.p.update(hue=v), key="hue")
        self._slider("Saturación", -100, 100, 1, self.p["saturation"], lambda v: self.p.update(saturation=v),
                     key="saturation")
        self._slider("Luminosidad", -100, 100, 1, self.p["lightness"], lambda v: self.p.update(lightness=v),
                     key="exposure")
        cb = QCheckBox("Colorear")
        cb.setChecked(bool(self.p.get("colorize")))

        def col(on):
            self.p["colorize"] = bool(on)
            if on and self.p["saturation"] == 0:
                self.p["saturation"] = 25.0
            self._emit(True)

        cb.toggled.connect(col)
        self.bl.addWidget(cb)

    def _ui_color_balance(self):
        tone = QComboBox()
        for k, label in (("shadows", "Sombras"), ("mid", "Medios tonos"), ("highlights", "Iluminaciones")):
            tone.addItem(label, k)
        tone.setCurrentIndex(["shadows", "mid", "highlights"].index(self.p.get("tone", "mid")))
        sliders = []

        def show():
            vals = self.p[self.p.get("tone", "mid")]
            for s, v in zip(sliders, vals):
                s.set_value(v)

        def setv(i, v):
            self.p[self.p.get("tone", "mid")][i] = float(v)

        tone.currentIndexChanged.connect(lambda i: (self.p.update(tone=tone.itemData(i)), show()))
        row = QHBoxLayout()
        lab = QLabel("Tono:")
        lab.setObjectName("meta")
        row.addWidget(lab)
        row.addWidget(tone, 1)
        self.bl.addLayout(row)
        for i, (label, key) in enumerate((("Cian · Rojo", "tint"), ("Magenta · Verde", "tint"), ("Amarillo · Azul", "temp"))):
            sliders.append(self._slider(label, -100, 100, 1, self.p[self.p.get("tone", "mid")][i],
                                        lambda v, i=i: setv(i, v)))
        cb = QCheckBox("Conservar luminosidad")
        cb.setChecked(bool(self.p.get("preserve", True)))
        cb.toggled.connect(lambda on: (self.p.update(preserve=bool(on)), self._emit(True)))
        self.bl.addWidget(cb)

    def _ui_black_white(self):
        for i, label in enumerate(("Rojos", "Amarillos", "Verdes", "Cianes", "Azules", "Magentas")):
            self._slider(label, -200, 300, 1, self.p["weights"][i],
                         lambda v, i=i: self.p["weights"].__setitem__(i, float(v)))
        cb = QCheckBox("Matiz")
        cb.setChecked(bool(self.p.get("tint")))
        cb.toggled.connect(lambda on: (self.p.update(tint=bool(on)), self._emit(True)))
        self.bl.addWidget(cb)
        self.bl.addWidget(self._color_button("tint_color", "Color del matiz…"))

    def _color_button(self, key, label):
        from PySide6.QtWidgets import QColorDialog
        b = QPushButton(label)
        b.setObjectName("ghost")
        b.setStyleSheet(f"QPushButton {{ border-left: 14px solid {self.p.get(key, '#ffffff')}; }}")

        def pick():
            c = QColorDialog.getColor(QColor(self.p.get(key, "#ffffff")), self, label)
            if c.isValid():
                self.p[key] = c.name()
                b.setStyleSheet(f"QPushButton {{ border-left: 14px solid {c.name()}; }}")
                self._emit(True)

        b.clicked.connect(pick)
        return b

    def _ui_photo_filter(self):
        self.bl.addWidget(self._color_button("color", "Color del filtro…"))
        self._slider("Densidad", 0, 100, 1, self.p["density"], lambda v: self.p.update(density=v))
        cb = QCheckBox("Conservar luminosidad")
        cb.setChecked(bool(self.p.get("preserve", True)))
        cb.toggled.connect(lambda on: (self.p.update(preserve=bool(on)), self._emit(True)))
        self.bl.addWidget(cb)

    def _ui_invert(self):
        lab = QLabel("Invierte los colores. Con una máscara, solo dentro de ella.")
        lab.setObjectName("meta")
        lab.setWordWrap(True)
        self.bl.addWidget(lab)

    def _ui_posterize(self):
        self._slider("Niveles", 2, 255, 1, self.p["levels"], lambda v: self.p.update(levels=int(v)), 4)

    def _ui_threshold(self):
        self._slider("Umbral", 1, 255, 1, self.p["level"], lambda v: self.p.update(level=int(v)), 128)
