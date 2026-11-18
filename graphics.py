"""Entorno Graphics: diseño tipo Canva con herramientas de Illustrator.

- Canva: tamaños para redes e imprenta, plantillas, fotos de la carpeta abierta (arrastrar y soltar o rellenar
  huecos), texto, formas, marcos, capas, alinear con imán, deshacer/rehacer, autoguardado en «Mis diseños».
- Illustrator (referencia: VectorCraft, Apache-2.0/MIT): pluma Bézier, selección directa de nodos, Buscatrazos
  (unir, restar, intersecar, excluir), degradados, texto a contornos, vectorizar imagen.
- Quitar fondo de fotos y logos (personas con IA local, fondo liso, objeto con GrabCut): se guarda un PNG aparte,
  el original no se toca.
- Exportar: PDF de imprenta (sangrado, marcas de corte, 300 ppp, RGB o CMYK), SVG, PNG y JPG.
"""
from __future__ import annotations

import copy
import json
import math
import os
import threading
import time

import numpy as np
from PySide6.QtCore import QMimeData, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen,
                           QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout, QFrame,
                               QGraphicsItem, QGraphicsScene, QGraphicsView, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMenuBar, QMessageBox,
                               QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

import engine as E
import graphics_engine as G
import theme as T

# Distribución de VectorCraft (github.com/storytold/vectorcraft, MIT o Apache-2.0) con los colores de PhootFlow:
# negro azulado de la interfaz, herramienta activa en magenta, selección azul y guías inteligentes en magenta.
VC = dict(app=T.CHROME, panel=T.SURFACE, panel2=T.SURFACE_2, field=T.SURFACE_2, canvas="#14161c", border=T.LINE,
          text=T.TEXT, muted=T.MUTED, faint=T.FAINT, tool="#4a1036", tool_line=T.ACCENT,
          accent=T.ACCENT, accent_hi=T.ACCENT_HOVER, select="#4C82F7", guide="#E8489A")
ACCENT = QColor(VC["select"])
GUIDE = QColor(VC["guide"])

VC_QSS = f"""
QWidget {{ color: {VC['text']}; font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 9pt; }}
QWidget#vcRoot, QDialog {{ background: {VC['app']}; }}
QFrame#vcPanel, QWidget#vcPanel {{ background: {VC['panel']}; }}
QFrame#vcTools {{ background: {VC['panel']}; border-right: 1px solid {VC['border']}; }}
QFrame#vcRight {{ background: {VC['panel']}; border-left: 1px solid {VC['border']}; }}
QFrame#vcDocTab {{ background: {VC['app']}; border-bottom: 1px solid {VC['border']}; }}
QFrame#vcHint, QFrame#vcStatus {{ background: {VC['app']}; border-top: 1px solid {VC['border']}; }}
QLabel {{ background: transparent; }}
QLabel#vcDocName {{ font-weight: 600; color: #F2F3F6; }}
QLabel#vcSection {{ font-size: 10pt; color: #EEF0F4; padding-top: 8px; }}
QLabel#vcKind {{ color: {VC['muted']}; }}
QLabel#muted, QLabel#meta {{ color: {VC['muted']}; }}
QLabel#faint {{ color: {VC['faint']}; }}
QLabel#cardTitle {{ color: {VC['muted']}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }}
QMenuBar {{ background: {VC['app']}; color: {VC['text']}; padding: 2px 4px; font-size: 9.5pt; }}
QMenuBar::item {{ padding: 5px 9px; background: transparent; border-radius: 3px; }}
QMenuBar::item:selected {{ background: {VC['panel2']}; }}
QMenu {{ background: {VC['panel']}; border: 1px solid {VC['border']}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 14px; border-radius: 3px; }}
QMenu::item:selected {{ background: {VC['panel2']}; color: white; }}
QMenu::item:disabled {{ color: {VC['faint']}; }}
QMenu::separator {{ height: 1px; background: {VC['border']}; margin: 4px 6px; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QFontComboBox {{
    background: {VC['field']}; border: 1px solid {VC['border']}; border-radius: 4px; padding: 4px 6px;
    selection-background-color: {VC['select']}; color: {VC['text']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {VC['select']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {VC['panel']}; border: 1px solid {VC['border']};
    selection-background-color: {VC['panel2']}; }}
QPushButton {{ background: {VC['panel2']}; border: 1px solid {VC['border']}; border-radius: 4px; padding: 5px 10px;
    color: {VC['text']}; }}
QPushButton:hover {{ background: #343945; }}
QPushButton:checked {{ background: {VC['tool']}; border-color: {VC['tool_line']}; color: white; }}
QPushButton:disabled {{ color: {VC['faint']}; }}
QPushButton#primary {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F50D8A, stop:1 #7B2FF7);
                       border: none; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF3DA3, stop:1 #9150ff); }}
QPushButton#ghost, QPushButton#flat {{ background: transparent; }}
QPushButton#ghost:hover, QPushButton#flat:hover {{ background: {VC['panel2']}; }}
QPushButton#vcTool {{ background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 0; }}
QPushButton#vcTool:hover {{ background: {VC['panel2']}; }}
QPushButton#vcTool:checked {{ background: {VC['tool']}; border-color: {VC['tool_line']}; }}
QPushButton#vcIcon {{ background: transparent; border: none; border-radius: 4px; padding: 3px; }}
QPushButton#vcIcon:hover {{ background: {VC['panel2']}; }}
QPushButton#vcIcon:checked {{ background: {VC['tool']}; }}
QPushButton#vcQuick {{ background: {VC['panel2']}; border: none; border-radius: 4px; padding: 7px 6px; }}
QPushButton#vcQuick:hover {{ background: #383D49; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 4px; padding: 4px 8px; }}
QPushButton#seg:checked {{ background: {VC['tool']}; }}
QPushButton#small {{ padding: 3px 8px; }}
QFrame#vcTask {{ background: {VC['panel']}; border: 1px solid {VC['border']}; border-radius: 7px; }}
QFrame#vcTask QPushButton {{ background: transparent; border: 1px solid {VC['border']}; border-radius: 5px;
    padding: 5px 10px; }}
QFrame#vcTask QPushButton:hover {{ background: {VC['panel2']}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {VC['muted']}; padding: 6px 12px; border-radius: 5px;
    margin: 4px 2px; }}
QTabBar::tab:selected {{ background: {VC['panel2']}; color: #F2F3F6; font-weight: 600; }}
QTabBar::tab:hover {{ color: {VC['text']}; }}
QTabWidget#libTabs QTabBar::tab {{ padding: 4px 7px; margin: 2px 1px; font-weight: 400; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ color: {VC['text']}; padding: 2px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {VC['panel2']}; color: white; }}
QListWidget::item:hover {{ background: #2A2E37; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 9px; }}
QScrollBar::handle:vertical {{ background: #3A3F4B; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; }}
QScrollBar::handle:horizontal {{ background: #3A3F4B; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSlider::groove:horizontal {{ height: 3px; background: #3A3F4B; border-radius: 1px; }}
QSlider::handle:horizontal {{ background: #E3E6EC; width: 11px; height: 11px; margin: -4px 0; border-radius: 5px; }}
QCheckBox {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border-radius: 3px; border: 1px solid #4A505D;
    background: {VC['field']}; }}
QCheckBox::indicator:checked {{ background: {VC['select']}; border-color: {VC['select']}; }}
QToolTip {{ background: {VC['panel2']}; color: white; border: 1px solid {VC['border']}; padding: 4px 6px; }}
"""


def vc_icon(name: str, size: int = 22, color: str = "#D9DCE3") -> QIcon:
    """Iconos de línea dibujados (estilo VectorCraft), sin archivos ni fuentes de iconos."""
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size * 2 / 24, size * 2 / 24)
    col = QColor(color)
    pen = QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    if name in ("select", "direct"):
        path.moveTo(6, 3)
        path.lineTo(6, 19)
        path.lineTo(10, 15)
        path.lineTo(13, 21)
        path.lineTo(15.5, 20)
        path.lineTo(12.5, 14)
        path.lineTo(18, 14)
        path.closeSubpath()
        if name == "select":
            p.setBrush(col)
        p.drawPath(path)
    elif name == "pen":
        path.moveTo(12, 3)
        path.lineTo(18, 13)
        path.lineTo(14, 20)
        path.lineTo(10, 20)
        path.lineTo(6, 13)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12, 3), QPointF(12, 12))
        p.drawEllipse(QPointF(12, 13.5), 1.4, 1.4)
    elif name == "text":
        p.drawLine(QPointF(5, 5), QPointF(19, 5))
        p.drawLine(QPointF(12, 5), QPointF(12, 20))
        p.drawLine(QPointF(9, 20), QPointF(15, 20))
    elif name == "rect":
        p.drawRoundedRect(QRectF(4, 5, 16, 14), 1.5, 1.5)
    elif name == "ellipse":
        p.drawEllipse(QRectF(4, 5, 16, 14))
    elif name == "hand":
        path.moveTo(8, 20)
        path.cubicTo(5, 16, 4, 13, 6, 12)
        path.lineTo(8, 14)
        path.lineTo(8, 5)
        path.cubicTo(8, 3.5, 10.5, 3.5, 10.5, 5)
        path.lineTo(10.5, 11)
        path.lineTo(10.5, 4)
        path.cubicTo(10.5, 2.5, 13, 2.5, 13, 4)
        path.lineTo(13, 11)
        path.lineTo(13, 5)
        path.cubicTo(13, 3.5, 15.5, 3.5, 15.5, 5)
        path.lineTo(15.5, 11.5)
        path.lineTo(15.5, 7.5)
        path.cubicTo(15.5, 6, 18, 6, 18, 7.5)
        path.lineTo(18, 15)
        path.cubicTo(18, 18, 16, 20, 14, 20)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "zoom":
        p.drawEllipse(QPointF(10, 10), 6, 6)
        p.drawLine(QPointF(14.5, 14.5), QPointF(20, 20))
    elif name.startswith("al_"):
        k = name[3:]
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        if k in ("left", "hcenter", "right"):
            x = {"left": 5.0, "hcenter": 12.0, "right": 19.0}[k]
            for y, w in ((7, 10), (14, 6)):
                x0 = x if k == "left" else (x - w if k == "right" else x - w / 2)
                p.drawRoundedRect(QRectF(x0, y - 2, w, 4), 1, 1)
            p.setPen(pen)
            p.drawLine(QPointF(x, 3), QPointF(x, 21))
        else:
            y = {"top": 5.0, "vcenter": 12.0, "bottom": 19.0}[k]
            for x, h in ((7, 10), (14, 6)):
                y0 = y if k == "top" else (y - h if k == "bottom" else y - h / 2)
                p.drawRoundedRect(QRectF(x - 2, y0, 4, h), 1, 1)
            p.setPen(pen)
            p.drawLine(QPointF(3, y), QPointF(21, y))
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)


TOOL_HINTS = {
    "select": "<b>Clic</b> en un objeto para seleccionarlo  |  <b>Mayús+clic</b> para añadir a la selección  |  "
              "<b>Arrastrar</b> para mover · asas para escalar y girar",
    "direct": "<b>Clic</b> en un nodo o manecilla para moverlo  |  <b>Doble clic</b> en un nodo: esquina ↔ curva  |  "
              "<b>Alt</b> al mover una manecilla: romperla",
    "pen": "<b>Clic</b> para una esquina  |  <b>Arrastrar</b> para una curva  |  <b>Clic en el primer punto</b> cierra  |"
           "  <b>Intro</b> o <b>Esc</b> termina",
    "text": "<b>Clic</b> en el lienzo para escribir un texto nuevo",
    "rect": "<b>Arrastrar</b> para dibujar un rectángulo  |  <b>Mayús</b>: cuadrado",
    "ellipse": "<b>Arrastrar</b> para dibujar una elipse  |  <b>Mayús</b>: círculo",
    "hand": "<b>Arrastrar</b> para desplazar la vista  |  también <b>espacio + arrastrar</b> o botón central",
    "zoom": "<b>Clic</b> para acercar  |  <b>Alt+clic</b> para alejar  |  <b>Rueda</b> en cualquier herramienta",
}
TOOL_NAMES = {"select": "Selección", "direct": "Selección directa", "pen": "Pluma", "text": "Texto",
              "rect": "Rectángulo", "ellipse": "Elipse", "hand": "Mano", "zoom": "Zoom"}
TOOLS = [("select", "➤", "Selección (V): mover, escalar y girar"),
         ("direct", "◇", "Selección directa (A): editar nodos y manecillas de un trazado. Doble clic en un nodo: "
                         "esquina ↔ curva"),
         ("pen", "✒", "Pluma (P): clic = esquina, arrastrar = curva; clic en el primer punto cierra; "
                      "Intro o Esc termina"),
         ("text", "T", "Texto (T): clic en el lienzo"),
         ("rect", "▭", "Rectángulo (M): arrastrar"),
         ("ellipse", "◯", "Elipse (L): arrastrar"),
         ("hand", "✋", "Mano (H o espacio): desplazar"),
         ("zoom", "⌕", "Zoom (Z): clic acerca, Alt+clic aleja")]


def _btn(text, kind="", tip="", cb=None):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    if cb:
        b.clicked.connect(cb)
    return b


def _swatch_icon(col: str, size=16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    if len(col) == 9 and col.endswith("00"):
        p.setPen(QPen(QColor("#888"), 1))
        p.drawRect(0, 0, size - 1, size - 1)
        p.setPen(QPen(QColor(T.DANGER), 2))
        p.drawLine(1, size - 2, size - 2, 1)
    else:
        p.fillRect(0, 0, size, size, G.qcolor(col))
        p.setPen(QPen(QColor("#555"), 1))
        p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    return QIcon(pm)


def _qimage(a: np.ndarray) -> QImage:
    a = np.ascontiguousarray(a)
    h, w = a.shape[:2]
    ch = a.shape[2] if a.ndim == 3 else 1
    fmt = QImage.Format_RGBA8888 if ch == 4 else QImage.Format_RGB888
    return QImage(a.data, w, h, ch * w, fmt).copy()


def _checker(w, h, s=10) -> QPixmap:
    pm = QPixmap(max(1, w), max(1, h))
    pm.fill(QColor("#cfcfcf"))
    p = QPainter(pm)
    for y in range(0, h, s):
        for x in range((y // s % 2) * s, w, 2 * s):
            p.fillRect(x, y, s, s, QColor("#f2f2f2"))
    p.end()
    return pm


class ColorButton(QPushButton):
    """Botón de color con la paleta de la marca, los colores del diseño y «Personalizado…»."""
    changed = Signal(str)

    def __init__(self, value="#000000", allow_none=False, extra=None):
        super().__init__()
        self.value = value
        self.allow_none = allow_none
        self.extra = extra or (lambda: [])
        self.setFixedHeight(26)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._menu)
        self.set(value)

    def set(self, v):
        self.value = v or "#00000000"
        self.setIcon(_swatch_icon(self.value, 14))
        self.setText(" Sin color" if self.value.endswith("00") and len(self.value) == 9 else " " + self.value[:7])

    def _menu(self):
        m = QMenu(self)
        cols = list(dict.fromkeys(list(self.extra()) + G.PALETTE))[:24]
        for c in cols:
            a = m.addAction(_swatch_icon(c), c[:7])
            a.triggered.connect(lambda _=False, c=c: self._pick(c))
        m.addSeparator()
        if self.allow_none:
            m.addAction(_swatch_icon("#00000000"), "Sin color").triggered.connect(lambda: self._pick("#00000000"))
        m.addAction("Personalizado…").triggered.connect(self._custom)
        m.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def _custom(self):
        c = QColorDialog.getColor(G.qcolor(self.value), self, "Color", QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self._pick(c.name(QColor.HexRgb).upper() + ("" if c.alpha() == 255 else f"{c.alpha():02X}"))

    def _pick(self, c):
        self.set(c)
        self.changed.emit(c)


# ------------------------------------------------------------------ elemento en el lienzo

MAX_NODES = 600
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


class ElementItem(QGraphicsItem):
    H = 8  # tamaño de las asas en píxeles de pantalla

    def __init__(self, canvas: "Canvas", el: dict):
        super().__init__()
        self.cv = canvas
        self.el = el
        self.mode = None
        self.handle = None
        self.node = None
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.sync()

    # ---------------------------------------------------------- geometría
    def sync(self):
        self.prepareGeometryChange()
        e = self.el
        self.setTransformOriginPoint(e["w"] / 2, e["h"] / 2)
        self.setRotation(e.get("rot", 0.0))
        self.setPos(e["x"], e["y"])
        self.setFlag(QGraphicsItem.ItemIsMovable, not e.get("locked"))
        self.setVisible(not e.get("hidden"))
        self.update()

    def _s(self):
        return max(1e-3, self.cv.transform().m11())

    def boundingRect(self):
        m = (self.H * 2 + 34) / self._s()
        return QRectF(-m, -m, self.el["w"] + 2 * m, self.el["h"] + 2 * m)

    def shape(self):
        p = QPainterPath()
        t = 5 / self._s()
        p.addRect(QRectF(-t, -t, self.el["w"] + 2 * t, self.el["h"] + 2 * t))
        if self.isSelected():
            r = self.H / self._s()
            for pt in self.handles().values():
                p.addEllipse(pt, r, r)
        if self.cv.tool == "direct" and self.el["type"] == "vector" and self.isSelected():
            p.addRect(self.boundingRect())
        return p

    def handles(self) -> dict:
        w, h = self.el["w"], self.el["h"]
        pts = {"nw": (0, 0), "n": (w / 2, 0), "ne": (w, 0), "e": (w, h / 2), "se": (w, h), "s": (w / 2, h),
               "sw": (0, h), "w": (0, h / 2)}
        if self.el["type"] == "text":
            pts = {k: v for k, v in pts.items() if k not in ("n", "s")}
        if self.el.get("shape") == "line":
            pts = {k: v for k, v in pts.items() if k in ("e", "w")}
        out = {k: QPointF(*v) for k, v in pts.items()}
        out["rot"] = QPointF(w / 2, -28 / self._s())
        return out

    def handle_at(self, pos: QPointF):
        if not self.isSelected() or len(self.cv.scene().selectedItems()) != 1 or self.el.get("locked"):
            return None
        r = (self.H + 3) / self._s()
        for k, pt in self.handles().items():
            if (pt - pos).manhattanLength() <= r * 1.5:
                return k
        return None

    # ---------------------------------------------------------- nodos (selección directa)
    def _vb(self):
        vw, vh = self.el.get("viewbox", [1, 1])
        return self.el["w"] / max(vw, 1e-6), self.el["h"] / max(vh, 1e-6)

    def node_list(self):
        out = []
        if sum(sp["d"].count(" C ") + sp["d"].count(" L ") for sp in self.el.get("paths", [])) > MAX_NODES:
            return out  # vectorizados con miles de nodos: se recolorean y escalan, no se editan nodo a nodo
        for pi, sp in enumerate(self.el.get("paths", [])):
            if len(sp["d"]) > 60000:
                continue  # vectorizados enormes: no se editan nodo a nodo
            for si, sub in enumerate(G.d_to_nodes(sp["d"])):
                for ni, nd in enumerate(sub["nodes"]):
                    out.append((pi, si, ni, nd))
        return out

    def node_at(self, pos: QPointF):
        sx, sy = self._vb()
        r = (self.H + 2) / self._s()
        best = None
        for pi, si, ni, nd in self.node_list():
            for k in ("i", "o", "p"):
                if k != "p" and nd[k] == nd["p"]:
                    continue
                q = QPointF(nd[k][0] * sx, nd[k][1] * sy)
                dd = (q - pos).manhattanLength()
                if dd <= r and (best is None or dd < best[0] or k == "p"):
                    best = (dd, (pi, si, ni, k))
        return best[1] if best else None

    # ---------------------------------------------------------- pintar
    def paint(self, p: QPainter, opt, widget=None):
        s = self._s()
        G.paint_element(p, self.el, self.cv.cache, editing=True, img_px=s * 1.5, max_img=2000)
        sel = self.isSelected()
        if self.cv.hover is self and not sel:
            p.setPen(QPen(ACCENT, 1.5 / s))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(0, 0, self.el["w"], self.el["h"]))
        if not sel:
            return
        pen = QPen(ACCENT, 1.5 / s)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0, 0, self.el["w"], self.el["h"]))
        if self.cv.tool == "direct" and self.el["type"] == "vector":
            self._paint_nodes(p, s)
            return
        if len(self.cv.scene().selectedItems()) != 1 or self.el.get("locked"):
            return
        r = self.H / 2 / s
        hs = self.handles()
        p.drawLine(hs["n"] if "n" in hs else QPointF(self.el["w"] / 2, 0), hs["rot"])
        p.setPen(QPen(ACCENT, 1.2 / s))
        p.setBrush(QColor("white"))
        for k, pt in hs.items():
            if k == "rot":
                p.drawEllipse(pt, r * 1.2, r * 1.2)
            else:
                p.drawRect(QRectF(pt.x() - r, pt.y() - r, 2 * r, 2 * r))

    def _paint_nodes(self, p, s):
        sx, sy = self._vb()
        r = 4 / s
        for pi, si, ni, nd in self.node_list():
            P = QPointF(nd["p"][0] * sx, nd["p"][1] * sy)
            for k in ("i", "o"):
                if nd[k] != nd["p"]:
                    Q = QPointF(nd[k][0] * sx, nd[k][1] * sy)
                    p.setPen(QPen(ACCENT, 1 / s))
                    p.drawLine(P, Q)
                    p.setBrush(ACCENT)
                    p.drawEllipse(Q, r * 0.8, r * 0.8)
            sel = self.node and self.node[:3] == (pi, si, ni)
            p.setBrush(ACCENT if sel else QColor("white"))
            p.setPen(QPen(ACCENT, 1.2 / s))
            p.drawRect(QRectF(P.x() - r, P.y() - r, 2 * r, 2 * r))

    # ---------------------------------------------------------- ratón
    def hoverEnterEvent(self, e):
        self.cv.hover = self
        self.update()

    def hoverLeaveEvent(self, e):
        if self.cv.hover is self:
            self.cv.hover = None
        self.update()

    def mousePressEvent(self, e):
        if self.cv.tool in ("pen", "text", "rect", "ellipse", "hand"):
            e.ignore()
            return
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        if self.cv.tool == "direct" and self.el["type"] == "vector" and not self.el.get("locked"):
            if not self.isSelected():
                self.scene().clearSelection()
                self.setSelected(True)
            n = self.node_at(e.pos())
            if n:
                self.mode, self.node = "node", n
                self.start_el = copy.deepcopy(self.el)
                self.start_pos = e.pos()
                self.update()
                return
        h = self.handle_at(e.pos())
        if h:
            self.mode = "rotate" if h == "rot" else "resize"
            self.handle = h
            self.start_tf = self.sceneTransform()
            self.start_el = copy.deepcopy(self.el)
            return
        self.cv.begin_move(self)
        if self.cv.owner.right_tabs.currentIndex() != 0:  # como VectorCraft: al pinchar un objeto, sus propiedades
            self.cv.owner.right_tabs.setCurrentIndex(0)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.mode == "resize":
            self._resize(e.scenePos(), bool(e.modifiers() & Qt.ShiftModifier))
        elif self.mode == "rotate":
            c = self.start_tf.map(QPointF(self.start_el["w"] / 2, self.start_el["h"] / 2))
            q = e.scenePos()
            ang = math.degrees(math.atan2(q.y() - c.y(), q.x() - c.x())) + 90
            if e.modifiers() & Qt.ShiftModifier:
                ang = round(ang / 15) * 15
            elif abs(ang - round(ang / 45) * 45) < 3:
                ang = round(ang / 45) * 45
            self.el["rot"] = ((ang + 180) % 360) - 180
            self.sync()
            self.cv.owner.live_geometry()
        elif self.mode == "node":
            self._move_node(e.pos(), bool(e.modifiers() & Qt.AltModifier))
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        was = self.mode
        self.mode = None
        if was == "node":
            self.normalize()
        super().mouseReleaseEvent(e)
        self.cv.guides = ([], [])
        self.cv.viewport().update()
        self.cv.owner.after_interaction()

    def mouseDoubleClickEvent(self, e):
        if self.cv.tool == "direct" and self.el["type"] == "vector":
            n = self.node_at(e.pos())
            if n:
                self._toggle_node(n)
                return
        if self.el["type"] == "text":
            self.cv.owner.focus_text()
            return
        if self.el["type"] == "vector" and self.cv.tool == "select":
            self.cv.owner.set_tool("direct")
            return
        super().mouseDoubleClickEvent(e)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.mode is None and self.cv.snap_on \
                and self.cv.moving is self and len(self.scene().selectedItems()) == 1:
            e = dict(self.el, x=value.x(), y=value.y())
            tol = 6 / self._s()
            dx, dy, gx, gy = G.snap(G.bbox(e), *self.cv.snap_cache, tol)
            self.cv.guides = (gx, gy)
            self.cv.viewport().update()
            return QPointF(value.x() + dx, value.y() + dy)
        return super().itemChange(change, value)

    # ---------------------------------------------------------- escalar
    def _resize(self, sp: QPointF, shift: bool):
        e0, h = self.start_el, self.handle
        inv, _ = self.start_tf.inverted()
        q = inv.map(sp)
        W0, H0 = e0["w"], e0["h"]
        x0, y0, x1, y1 = 0.0, 0.0, W0, H0
        if "w" in h:
            x0 = min(q.x(), W0 - 4)
        if "e" in h:
            x1 = max(q.x(), 4)
        if "n" in h:
            y0 = min(q.y(), H0 - 4)
        if "s" in h:
            y1 = max(q.y(), 4)
        corner = len(h) == 2
        keep = corner and ((e0["type"] in ("image", "vector", "text")) != shift)
        nw, nh = x1 - x0, y1 - y0
        if keep:
            k = max(nw / W0, nh / H0)
            nw, nh = W0 * k, H0 * k
            if "w" in h:
                x0 = W0 - nw
            else:
                x1 = nw
            if "n" in h:
                y0 = H0 - nh
            else:
                y1 = nh
        el = self.el
        if e0["type"] == "text":
            if corner:
                k = nw / W0
                el["size"] = round(e0["size"] * k, 2)
                if e0.get("fit_h"):  # escalar también el hueco del autoajuste
                    el["fit_h"] = e0["fit_h"] * k
                    el["size_max"] = e0.get("size_max", e0["size"]) * k
            el["w"] = nw
            nh = G.fit_text(el)["h"]
            if "n" in h:
                y0 = y1 - nh
            else:
                y1 = y0 + nh
        if e0["type"] == "shape" and e0.get("shape") == "line":
            nh = H0
            y0, y1 = 0.0, H0
        c = self.start_tf.map(QPointF((x0 + x1) / 2, (y0 + y1) / 2))
        el["w"], el["h"] = nw, nh
        el["x"], el["y"] = c.x() - nw / 2, c.y() - nh / 2
        self.sync()
        self.cv.owner.live_geometry()

    # ---------------------------------------------------------- nodos
    def _move_node(self, pos: QPointF, alt: bool):
        pi, si, ni, k = self.node
        sx, sy = self._vb()
        subs = G.d_to_nodes(self.start_el["paths"][pi]["d"])
        nd = subs[si]["nodes"][ni]
        dx, dy = (pos.x() - self.start_pos.x()) / sx, (pos.y() - self.start_pos.y()) / sy
        if k == "p":
            for kk in "pio":
                nd[kk] = [nd[kk][0] + dx, nd[kk][1] + dy]
        else:
            other = "o" if k == "i" else "i"
            px, py = nd["p"]
            sym = nd[other] != nd["p"] and abs((nd[k][0] - px) + (nd[other][0] - px)) < 0.5 and \
                abs((nd[k][1] - py) + (nd[other][1] - py)) < 0.5
            nd[k] = [nd[k][0] + dx, nd[k][1] + dy]
            if sym and not alt:
                nd[other] = [2 * px - nd[k][0], 2 * py - nd[k][1]]
        self.prepareGeometryChange()
        self.el["paths"][pi]["d"] = G.nodes_to_d(subs)
        self.update()

    def _toggle_node(self, n):
        pi, si, ni, _ = n
        subs = G.d_to_nodes(self.el["paths"][pi]["d"])
        ns = subs[si]["nodes"]
        nd = ns[ni]
        if nd["i"] != nd["p"] or nd["o"] != nd["p"]:
            nd["i"], nd["o"] = list(nd["p"]), list(nd["p"])
        else:
            prev = ns[ni - 1]["p"] if (ni > 0 or subs[si]["closed"]) else nd["p"]
            nxt = ns[(ni + 1) % len(ns)]["p"] if (ni < len(ns) - 1 or subs[si]["closed"]) else nd["p"]
            tx, ty = (nxt[0] - prev[0]) / 6, (nxt[1] - prev[1]) / 6
            nd["i"] = [nd["p"][0] - tx, nd["p"][1] - ty]
            nd["o"] = [nd["p"][0] + tx, nd["p"][1] + ty]
        self.prepareGeometryChange()
        self.el["paths"][pi]["d"] = G.nodes_to_d(subs)
        self.normalize()
        self.cv.owner.after_interaction()

    def normalize(self):
        """Tras mover nodos: la caja del elemento vuelve a ajustarse al dibujo."""
        el = self.el
        if abs(el.get("rot", 0.0)) > 0.01:
            return
        vw, vh = el["viewbox"]
        r = QRectF()
        for sp in el["paths"]:
            r = r.united(G.parse_path(sp["d"]).controlPointRect())
        if r.isEmpty() or (abs(r.x()) < 0.01 and abs(r.y()) < 0.01 and abs(r.width() - vw) < 0.01
                           and abs(r.height() - vh) < 0.01):
            return
        sx, sy = self._vb()
        for sp in el["paths"]:
            subs = G.d_to_nodes(sp["d"])
            for s in subs:
                for nd in s["nodes"]:
                    for kk in "pio":
                        nd[kk] = [nd[kk][0] - r.x(), nd[kk][1] - r.y()]
            sp["d"] = G.nodes_to_d(subs)
        el["x"] += r.x() * sx
        el["y"] += r.y() * sy
        el["w"], el["h"] = max(1.0, r.width() * sx), max(1.0, r.height() * sy)
        el["viewbox"] = [max(1e-3, r.width()), max(1e-3, r.height())]
        self.sync()


# ------------------------------------------------------------------ lienzo

class Canvas(QGraphicsView):
    def __init__(self, owner: "GraphicsView"):
        super().__init__()
        self.owner = owner
        self.cache = G.ImageCache()
        self.sc = QGraphicsScene(self)
        self.setScene(self.sc)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.tool = "select"
        self.hover = None
        self.moving = None
        self.snap_on = True
        self.snap_cache = ([], [])
        self.guides = ([], [])
        self.pen_nodes: list[dict] = []
        self.pen_drag = False
        self.pen_mouse = None
        self.draw = None  # (elemento, punto inicial) mientras se dibuja un rectángulo o elipse
        self._pan = None
        self._space = False

    @property
    def d(self):
        return self.owner.d

    def page(self) -> QRectF:
        return QRectF(0, 0, self.d["w"], self.d["h"])

    def set_page(self):
        m = max(self.d["w"], self.d["h"]) * 2
        self.sc.setSceneRect(QRectF(-m, -m, self.d["w"] + 2 * m, self.d["h"] + 2 * m))

    def fit(self):
        r = self.page()
        m = max(r.width(), r.height()) * 0.06
        self.fitInView(r.adjusted(-m, -m, m, m), Qt.KeepAspectRatio)
        self.owner.zoom_changed()

    def set_zoom(self, z):
        self.resetTransform()
        self.scale(z, z)
        self.centerOn(self.page().center())
        self.owner.zoom_changed()

    # ---------------------------------------------------------- dibujo de fondo y guías
    def drawBackground(self, p, rect):
        p.fillRect(rect, QColor(VC["canvas"]))
        pg = self.page()
        s = self.transform().m11()
        p.fillRect(pg.translated(4 / s, 5 / s), QColor(0, 0, 0, 110))
        p.fillRect(pg, G.qcolor(self.d.get("bg", "#FFFFFF")))

    def drawForeground(self, p, rect):
        pg = self.page()
        s = max(1e-3, self.transform().m11())
        outside = QPainterPath()
        outside.addRect(rect.united(pg.adjusted(-10, -10, 10, 10)))
        inner = QPainterPath()
        inner.addRect(pg)
        bgc = QColor(VC["canvas"])
        bgc.setAlpha(215)
        p.fillPath(outside.subtracted(inner), bgc)
        # nombre de la mesa de trabajo encima, como en VectorCraft («01 - Página»)
        p.save()
        p.resetTransform()
        tl = self.mapFromScene(pg.topLeft())
        p.setPen(QColor(VC["muted"]))
        f = p.font()
        f.setPixelSize(12)
        p.setFont(f)
        n = len(self.d.get("pages", [])) or 1
        p.drawText(QPointF(tl.x(), tl.y() - 8),
                   f"{self.d.get('page', 0) + 1:02d} - {self.d.get('name', '')}" + (f"  ({n} páginas)" if n > 1 else ""))
        p.restore()
        if self.d.get("unit") == "mm":
            b = G.mm_to_px(3.0, self.d.get("dpi", G.PRINT_DPI))
            pen = QPen(QColor(229, 72, 77, 170), 1 / s, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(pg.adjusted(-b, -b, b, b))
            p.setPen(QPen(QColor(46, 134, 171, 140), 1 / s, Qt.DotLine))
            p.drawRect(pg.adjusted(b, b, -b, -b))  # margen de seguridad
        gx, gy = self.guides
        p.setPen(QPen(GUIDE, 1 / s))
        for x in gx:
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for y in gy:
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        if self.tool == "pen" and self.pen_nodes:
            self._paint_pen(p, s)

    def _paint_pen(self, p, s):
        subs = [{"nodes": self.pen_nodes, "closed": False}]
        path = G.parse_path(G.nodes_to_d(subs))
        if self.pen_mouse is not None and not self.pen_drag:
            last = self.pen_nodes[-1]
            path.cubicTo(QPointF(*last["o"]), self.pen_mouse, self.pen_mouse)
        p.setPen(QPen(ACCENT, 1.5 / s))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        r = 4 / s
        for i, nd in enumerate(self.pen_nodes):
            P = QPointF(*nd["p"])
            for k in ("i", "o"):
                if nd[k] != nd["p"]:
                    p.drawLine(P, QPointF(*nd[k]))
                    p.drawEllipse(QPointF(*nd[k]), r * 0.7, r * 0.7)
            p.setBrush(ACCENT if i == 0 else QColor("white"))
            p.drawRect(QRectF(P.x() - r, P.y() - r, 2 * r, 2 * r))
            p.setBrush(Qt.NoBrush)

    # ---------------------------------------------------------- mover con imán
    def begin_move(self, item):
        self.moving = item
        sel = {i.el["id"] for i in self.sc.selectedItems()} | {item.el["id"]}
        self.snap_cache = G.snap_lines(self.d, [e for e in self.d["elements"] if e["id"] not in sel])

    # ---------------------------------------------------------- ratón
    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.owner.place_taskbar()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.owner.place_taskbar()

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        z = self.transform().m11() * f
        if 0.02 < z < 40:
            self.scale(f, f)
            self.owner.zoom_changed()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.viewport().setCursor(Qt.OpenHandCursor)
            return
        if self.tool == "pen" and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
            self.finish_pen(False)
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.owner.apply_cursor()
            return
        super().keyReleaseEvent(e)

    def mousePressEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and (self._space or self.tool == "hand")):
            self._pan = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        self.owner.taskbar.hide()
        if e.button() == Qt.LeftButton and self.tool == "pen":
            if len(self.pen_nodes) > 2 and (QPointF(*self.pen_nodes[0]["p"]) - sp).manhattanLength() < \
                    10 / self.transform().m11():
                self.finish_pen(True)
                return
            pt = [sp.x(), sp.y()]
            self.pen_nodes.append({"p": pt, "i": list(pt), "o": list(pt)})
            self.pen_drag = True
            self.viewport().update()
            return
        if e.button() == Qt.LeftButton and self.tool == "zoom":
            f = 0.8 if e.modifiers() & Qt.AltModifier else 1.25
            self.scale(f, f)
            self.centerOn(sp)
            self.owner.zoom_changed()
            return
        if e.button() == Qt.LeftButton and self.tool == "text":
            self.owner.add_text("body", at=sp)
            self.owner.set_tool("select")
            return
        if e.button() == Qt.LeftButton and self.tool in ("rect", "ellipse"):
            el = G.shape_el(self.d, self.tool, x=sp.x(), y=sp.y(), w=1, h=1)
            self.draw = (el, sp)
            self.d["elements"].append(el)
            self.owner.rebuild(select=[el["id"]])
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        if self._pan is not None:
            dlt = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(dlt.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(dlt.y()))
            return
        if self.tool == "pen":
            self.pen_mouse = sp
            if self.pen_drag and self.pen_nodes:
                nd = self.pen_nodes[-1]
                nd["o"] = [sp.x(), sp.y()]
                nd["i"] = [2 * nd["p"][0] - sp.x(), 2 * nd["p"][1] - sp.y()]
            self.viewport().update()
            return
        if self.draw:
            el, p0 = self.draw
            x0, y0, x1, y1 = p0.x(), p0.y(), sp.x(), sp.y()
            if e.modifiers() & Qt.ShiftModifier:
                m = max(abs(x1 - x0), abs(y1 - y0))
                x1 = x0 + math.copysign(m, x1 - x0)
                y1 = y0 + math.copysign(m, y1 - y0)
            el.update(x=min(x0, x1), y=min(y0, y1), w=max(1.0, abs(x1 - x0)), h=max(1.0, abs(y1 - y0)))
            it = self.owner.items.get(el["id"])
            if it:
                it.sync()
            self.owner.live_geometry()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.owner.apply_cursor()
            return
        if self.tool == "pen":
            self.pen_drag = False
            return
        if self.draw:
            el, _ = self.draw
            self.draw = None
            if el["w"] < 3 and el["h"] < 3:  # un clic sin arrastrar: tamaño por defecto
                dflt = G.shape_el(self.d, el["shape"])
                el.update(w=dflt["w"], h=dflt["h"], x=el["x"] - dflt["w"] / 2, y=el["y"] - dflt["h"] / 2)
            self.owner.set_tool("select")
            self.owner.rebuild(select=[el["id"]])
            self.owner.commit()
            return
        super().mouseReleaseEvent(e)
        self.moving = None
        self.guides = ([], [])
        self.owner.after_interaction()

    def mouseDoubleClickEvent(self, e):
        if self.tool == "pen":
            self.finish_pen(False)
            return
        super().mouseDoubleClickEvent(e)

    def finish_pen(self, closed: bool):
        nodes = self.pen_nodes
        self.pen_nodes, self.pen_mouse, self.pen_drag = [], None, False
        self.viewport().update()
        if len(nodes) < 2:
            return
        el = G.path_el(self.d, [{"nodes": nodes, "closed": closed}], fill=self.owner.pen_fill,
                       stroke=self.owner.pen_stroke, stroke_w=0.0 if closed else 0.0)
        if el:
            self.d["elements"].append(el)
            self.owner.rebuild(select=[el["id"]])
            self.owner.commit()

    # ---------------------------------------------------------- soltar fotos
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasFormat(GE_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasFormat(GE_MIME):
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.mimeData().hasFormat(GE_MIME):
            import json as _json
            data = _json.loads(bytes(e.mimeData().data(GE_MIME)).decode("utf-8"))
            self.owner.elements.drop(data, self.mapToScene(e.position().toPoint()))
            e.acceptProposedAction()
            return
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        sp = self.mapToScene(e.position().toPoint())
        for i, p in enumerate(paths):
            self.owner.place_photo(p, QPointF(sp.x() + i * 30, sp.y() + i * 30))
        e.acceptProposedAction()


GE_MIME = "application/x-phootflow-online"


class TaskBar(QFrame):
    """Barra contextual flotante bajo la selección (como la «contextual task bar» de VectorCraft)."""

    def __init__(self, owner: "GraphicsView"):
        super().__init__(owner.canvas.viewport())
        self.owner = owner
        self.setObjectName("vcTask")
        self.h = QHBoxLayout(self)
        self.h.setContentsMargins(8, 6, 8, 6)
        self.h.setSpacing(6)
        self.hide()

    def update_for(self, sel):
        while self.h.count():
            it = self.h.takeAt(0)
            w_ = it.widget()
            if w_:  # fuera ya (no esperar al borrado diferido: se verían encima de los nuevos)
                w_.hide()
                w_.setParent(None)
                w_.deleteLater()
        if not sel:
            self.hide()
            return
        o = self.owner
        acts = []
        if len(sel) > 1:
            acts += [("Unir", lambda: o.pathfinder("union")), ("Restar", lambda: o.pathfinder("subtract")),
                     ("Agrupar", o.group)]
        else:
            t = sel[0]["type"]
            if t == "text":
                acts += [("T  Crear contornos", o.outline_text)]
            elif t == "image" and sel[0].get("path"):
                acts += [("Quitar fondo", o.remove_bg), ("Vectorizar", o.vectorize)]
            elif t == "vector":
                acts += [("Editar nodos", lambda: o.set_tool("direct"))]
        acts += [("Duplicar", o.duplicate)]
        for txt, fn in acts:
            b = QPushButton(txt)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            self.h.addWidget(b)
        lock = QPushButton("🔒" if not all(e.get("locked") for e in sel) else "🔓")
        lock.setToolTip("Bloquear / desbloquear")
        lock.clicked.connect(o.toggle_lock)
        self.h.addWidget(lock)
        more = QToolButton()
        more.setText("···")
        more.setPopupMode(QToolButton.InstantPopup)
        more.setStyleSheet("QToolButton { background: transparent; border: none; padding: 4px 8px; } "
                           "QToolButton::menu-indicator { image: none; }")
        m = QMenu(more)
        for t2, f2 in (("Traer al frente", lambda: o.order("front")), ("Enviar al fondo", lambda: o.order("back")),
                       ("Agrupar", o.group), ("Desagrupar", o.ungroup), ("Eliminar", o.delete)):
            m.addAction(t2).triggered.connect(f2)
        more.setMenu(m)
        self.h.addWidget(more)
        self.adjustSize()
        self.place()

    def place(self):
        sel = self.owner.canvas.sc.selectedItems() if self.owner.canvas.sc else []
        if not sel or self.owner.canvas.tool not in ("select", "direct") or not self.h.count():
            self.hide()
            return
        r = QRectF()
        for it in sel:
            r = r.united(it.mapRectToScene(QRectF(0, 0, it.el["w"], it.el["h"])))
        cv = self.owner.canvas
        bottom = cv.mapFromScene(QPointF(r.center().x(), r.bottom()))
        top = cv.mapFromScene(QPointF(r.center().x(), r.top()))
        vp = cv.viewport().rect()
        self.adjustSize()
        x = int(bottom.x() - self.width() / 2)
        y = int(bottom.y() + 22)
        if y + self.height() > vp.bottom() - 6:  # sin sitio debajo: encima
            y = int(top.y() - self.height() - 40)
        x = max(6, min(x, vp.right() - self.width() - 6))
        y = max(6, min(y, vp.bottom() - self.height() - 6))
        self.move(x, y)
        self.show()
        self.raise_()


class PhotoList(QListWidget):
    """Fotos de la carpeta: se arrastran al lienzo (o a un hueco de plantilla)."""

    def mimeData(self, items):
        m = QMimeData()
        m.setUrls([QUrl.fromLocalFile(i.data(Qt.UserRole)) for i in items])
        return m


# ------------------------------------------------------------------ diálogos

class VectorizeDialog(QDialog):
    def __init__(self, parent, rgb: np.ndarray):
        super().__init__(parent)
        self.setWindowTitle("Vectorizar")
        self.rgb = rgb
        self.vec = None
        self.choice = ""
        self.resize(980, 620)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.a = QLabel()
        self.b = QLabel()
        for lab, t in ((self.a, "Original"), (self.b, "Vector")):
            box = QVBoxLayout()
            tt = QLabel(t)
            tt.setObjectName("cardTitle")
            box.addWidget(tt)
            lab.setMinimumSize(440, 440)
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet("background: #ffffff; border: 1px solid #333;")
            box.addWidget(lab, 1)
            row.addLayout(box, 1)
        v.addLayout(row, 1)
        f = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["Color", "Blanco y negro"])
        self.colors = QSpinBox()
        self.colors.setRange(2, 24)
        self.colors.setValue(6)
        self.detail = QSlider(Qt.Horizontal)
        self.detail.setRange(0, 100)
        self.detail.setValue(60)
        self.detail.setFixedWidth(160)
        self.drop = QCheckBox("Quitar el fondo")
        self.invert = QCheckBox("Invertir")
        self.invert.setToolTip("B/N: usar lo claro como tinta (neones, logos claros sobre fondo oscuro)")
        self.curves = QCheckBox("Curvas suaves")
        self.curves.setChecked(True)
        for w_ in (QLabel("Modo"), self.mode, QLabel("Colores"), self.colors, QLabel("Detalle"), self.detail,
                   self.invert, self.drop, self.curves):
            f.addWidget(w_)
        f.addStretch(1)
        v.addLayout(f)
        self.info = QLabel("")
        self.info.setObjectName("meta")
        v.addWidget(self.info)
        bb = QHBoxLayout()
        bb.addStretch(1)
        bb.addWidget(_btn("Guardar SVG…", "ghost", "Guarda solo el dibujo vectorial (vinilo, plotter, imprenta)",
                          self.save_svg))
        bb.addWidget(_btn("Cancelar", "", "", self.reject))
        bb.addWidget(_btn("Añadir encima", "", "Deja la foto y pone el vector encima", lambda: self._done("add")))
        bb.addWidget(_btn("Sustituir la foto", "primary", "", lambda: self._done("replace")))
        v.addLayout(bb)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.preview)
        for s in (self.mode.currentIndexChanged, self.colors.valueChanged, self.detail.valueChanged,
                  self.drop.toggled, self.curves.toggled, self.invert.toggled):
            s.connect(lambda *_: self.timer.start(300))
        self.mode.currentIndexChanged.connect(lambda i: (self.colors.setEnabled(i == 0),
                                                         self.invert.setEnabled(i == 1)))
        self.invert.setEnabled(False)
        self.a.setPixmap(self._fit(QPixmap.fromImage(_qimage(rgb))))
        QTimer.singleShot(0, self.preview)

    def _fit(self, pm):
        return pm.scaled(QSize(440, 440), Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def params(self):
        return dict(mode="bw" if self.mode.currentIndex() == 1 else "color", colors=self.colors.value(),
                    detail=self.detail.value(), drop_bg=self.drop.isChecked(), curves=self.curves.isChecked(),
                    invert=self.invert.isChecked())

    def preview(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            t = time.time()
            self.vec = G.vectorize(self.rgb, max_side=700, **self.params())
            vw, vh = self.vec["viewbox"]
            s = 440 / max(vw, vh)
            pm = _checker(round(vw * s), round(vh * s)) if self.drop.isChecked() else \
                QPixmap(round(vw * s), round(vh * s))
            if not self.drop.isChecked():
                pm.fill(Qt.white)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            el = G.vector_el(self.vec, 0, 0, vw * s, vh * s)
            G.paint_element(p, el, G.ImageCache())
            p.end()
            self.b.setPixmap(pm)
            nodes = sum(sp["d"].count("C") + sp["d"].count("L") for sp in self.vec["paths"])
            nc = len(self.vec["paths"])
            self.info.setText(f"{nc} {'color' if nc == 1 else 'colores'} · {nodes} segmentos · "
                              f"{time.time() - t:.1f} s")
        finally:
            QApplication.restoreOverrideCursor()

    def final(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return G.vectorize(self.rgb, max_side=1400, **self.params())
        finally:
            QApplication.restoreOverrideCursor()

    def save_svg(self):
        path, _ = QFileDialog.getSaveFileName(self, "Guardar SVG", "vector.svg", "SVG (*.svg)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(G.vector_svg(self.final()))

    def _done(self, c):
        self.vec = self.final()
        self.choice = c
        self.accept()


class BackgroundDialog(QDialog):
    def __init__(self, parent, rgb: np.ndarray):
        super().__init__(parent)
        self.setWindowTitle("Quitar fondo")
        self.rgb = rgb
        self.out = None
        self.method = "auto"
        self.resize(900, 600)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.a, self.b = QLabel(), QLabel()
        for lab in (self.a, self.b):
            lab.setMinimumSize(420, 420)
            lab.setAlignment(Qt.AlignCenter)
            row.addWidget(lab, 1)
        v.addLayout(row, 1)
        f = QHBoxLayout()
        self.combo = QComboBox()
        for k, t in G.BG_METHODS.items():
            self.combo.addItem(t, k)
        self.tol = QSlider(Qt.Horizontal)
        self.tol.setRange(4, 60)
        self.tol.setValue(18)
        self.tol.setFixedWidth(150)
        self.tol.setToolTip("Tolerancia del color de fondo (solo «Fondo liso / logo»)")
        self.feather = QSlider(Qt.Horizontal)
        self.feather.setRange(0, 6)
        self.feather.setValue(1)
        self.feather.setFixedWidth(100)
        for w_ in (QLabel("Método"), self.combo, QLabel("Tolerancia"), self.tol, QLabel("Suavizar borde"),
                   self.feather):
            f.addWidget(w_)
        f.addStretch(1)
        v.addLayout(f)
        self.info = QLabel("")
        self.info.setObjectName("meta")
        v.addWidget(self.info)
        bb = QHBoxLayout()
        bb.addStretch(1)
        bb.addWidget(_btn("Cancelar", "", "", self.reject))
        self.ok = _btn("Aplicar", "primary", "", self.accept)
        bb.addWidget(self.ok)
        v.addLayout(bb)
        self.a.setPixmap(QPixmap.fromImage(_qimage(rgb)).scaled(QSize(420, 420), Qt.KeepAspectRatio,
                                                                Qt.SmoothTransformation))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.preview)
        for s in (self.combo.currentIndexChanged, self.tol.valueChanged, self.feather.valueChanged):
            s.connect(lambda *_: self.timer.start(350))
        QTimer.singleShot(0, self.preview)

    def run(self, rgb):
        return G.remove_background(rgb, self.combo.currentData(), float(self.tol.value()),
                                   float(self.feather.value()))

    def preview(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            h, w = self.rgb.shape[:2]
            s = min(1.0, 800 / max(h, w))
            import cv2
            small = cv2.resize(self.rgb, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
            out, used = self.run(small)
            self.method = used
            img = _qimage(out)
            pm = _checker(img.width(), img.height())
            p = QPainter(pm)
            p.drawImage(0, 0, img)
            p.end()
            self.b.setPixmap(pm.scaled(QSize(420, 420), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.info.setText(f"Método: {G.BG_METHODS.get(used, used)}. El original no se modifica: se guarda un "
                              f"PNG recortado aparte.")
        except Exception as ex:
            self.info.setText(f"No se pudo: {ex}")
        finally:
            QApplication.restoreOverrideCursor()

    def final(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            out, used = self.run(self.rgb)
            return out, used
        finally:
            QApplication.restoreOverrideCursor()


class ExportDialog(QDialog):
    def __init__(self, parent, d: dict, cache=None):
        super().__init__(parent)
        self.setWindowTitle("Exportar diseño")
        self.d = d
        self.cache = cache or G.ImageCache()
        outer = QHBoxLayout(self)
        self.proof = QLabel()
        self.proof.setFixedSize(300, 380)
        self.proof.setAlignment(Qt.AlignCenter)
        self.proof.setStyleSheet("background: #2a2a2a;")
        self.proof.setToolTip("Prueba de la hoja del PDF: sangrado (lo que se corta) y marcas de corte")
        outer.addWidget(self.proof)
        f = QFormLayout()
        outer.addLayout(f)
        self.fmt = QComboBox()
        self.fmt.addItems(["PDF para imprenta", "PNG", "JPG", "SVG (vectorial)"])
        f.addRow("Formato", self.fmt)
        self.bleed = QDoubleSpinBox()
        self.bleed.setRange(0, 10)
        self.bleed.setValue(3.0 if d.get("unit") == "mm" else 0.0)
        self.bleed.setSuffix(" mm")
        self.marks = QCheckBox("Marcas de corte")
        self.marks.setChecked(d.get("unit") == "mm")
        self.cmyk = QCheckBox("CMYK (conversión simple; para color exacto, la imprenta usa su perfil)")
        self.outline = QCheckBox("Convertir textos a contornos (la imprenta no necesita las fuentes)")
        self.outline.setChecked(True)
        self.scale = QComboBox()
        self.scale.addItems(["1×", "2×", "3×"])
        self.quality = QSpinBox()
        self.quality.setRange(50, 100)
        self.quality.setValue(92)
        f.addRow("Sangrado", self.bleed)
        f.addRow("", self.marks)
        f.addRow("", self.cmyk)
        f.addRow("", self.outline)
        f.addRow("Escala", self.scale)
        f.addRow("Calidad JPG", self.quality)
        info = QLabel(f"Tamaño: {G.size_label(d)}" + (" · fotos a 300 ppp" if d.get("unit") == "mm" else ""))
        info.setObjectName("meta")
        f.addRow(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Exportar…")
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.fmt.currentIndexChanged.connect(self._upd)
        self.bleed.valueChanged.connect(self._proof)
        self.marks.toggled.connect(self._proof)
        self._upd()

    def _upd(self):
        i = self.fmt.currentIndex()
        for w_ in (self.bleed, self.marks, self.cmyk, self.outline):
            w_.setEnabled(i == 0)
        self.scale.setEnabled(i in (1, 2))
        self.quality.setEnabled(i == 2)
        self._proof()

    def _proof(self, *_):
        pdf = self.fmt.currentIndex() == 0
        img = G.print_proof(self.d, self.cache, self.bleed.value() if pdf else 0, self.marks.isChecked() and pdf,
                            res=60) if pdf else G.thumbnail(self.d, self.cache, 380)
        self.proof.setPixmap(QPixmap.fromImage(img).scaled(QSize(290, 370), Qt.KeepAspectRatio,
                                                           Qt.SmoothTransformation))


# ------------------------------------------------------------------ entorno

class GraphicsView(QWidget):
    thumbs_ready = Signal(int, bytes)

    def __init__(self, cfg_dir: str):
        super().__init__()
        self.cfg_dir = cfg_dir
        self.ddir = G.designs_dir(cfg_dir)
        self.folder = ""
        self.items: dict[str, ElementItem] = {}
        self.undo_st: list[str] = []
        self.redo_st: list[str] = []
        self._last = ""
        self._co = (None, 0.0)
        self.clip: list[dict] = []
        self.pen_fill = "#F50D8A"
        self.pen_stroke = "#05070F"
        self._building = False
        self._photo_paths: list[str] = []
        self._closing = False
        self.d = G.design_from_preset("Instagram vertical 4:5")
        self.canvas = Canvas(self)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_now)

        # entorno VectorCraft: menús arriba, herramientas a la izquierda, Propiedades | Capas | Bibliotecas a la
        # derecha, línea de ayuda y barra de estado abajo
        self.setObjectName("vcRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(VC_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_tools())
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        doctab = self._build_doctab()
        mid.addWidget(doctab)
        mid.addWidget(self.canvas, 1)
        h.addLayout(mid, 1)
        h.addWidget(self._build_right())
        menubar = self._build_menubar()
        outer.addWidget(menubar)
        outer.addLayout(h, 1)
        import ui as PUI
        PUI.collapse_menubar(menubar, self, doctab.layout())  # los menús, tras ☰: una fila menos
        outer.addWidget(self._build_hint())
        outer.addWidget(self._build_status())
        self.taskbar = TaskBar(self)
        self.canvas.sc.selectionChanged.connect(self.on_selection)
        self.thumbs_ready.connect(self._on_thumb)
        self._shortcuts()
        self.pages_timer = QTimer(self)
        self.pages_timer.setSingleShot(True)
        self.pages_timer.timeout.connect(self.refresh_pages)
        self.load_design(self._initial_design())

    # ---------------------------------------------------------- construcción
    def _build_libraries(self):
        """Bibliotecas (como en VectorCraft): lo de Canva — tamaños y plantillas, fotos, elementos, diseños y marca."""
        tabs = self.tabs = QTabWidget()
        tabs.setObjectName("libTabs")
        tabs.tabBar().setExpanding(False)

        # Diseño: tamaños y plantillas
        pg = QWidget()
        pv = QVBoxLayout(pg)
        pv.setContentsMargins(4, 8, 4, 4)
        t = QLabel("NUEVO DISEÑO")
        t.setObjectName("cardTitle")
        pv.addWidget(t)
        self.presets = QListWidget()
        self.presets.setObjectName("list")
        last = ""
        for g, n, ww, hh, u in G.PRESETS:
            if g != last:
                it = QListWidgetItem(g.upper())
                it.setFlags(Qt.NoItemFlags)
                it.setForeground(QColor(T.FAINT))
                self.presets.addItem(it)
                last = g
            it = QListWidgetItem(f"{n}   ·   {ww} × {hh} {u}")
            it.setData(Qt.UserRole, n)
            self.presets.addItem(it)
        self.presets.itemDoubleClicked.connect(lambda it: it.data(Qt.UserRole) and self.new_design(it.data(Qt.UserRole)))
        pv.addWidget(self.presets, 2)
        row = QHBoxLayout()
        self.cw, self.ch = QDoubleSpinBox(), QDoubleSpinBox()
        for sb in (self.cw, self.ch):
            sb.setRange(10, 20000)
            sb.setDecimals(0)
        self.cw.setValue(1080)
        self.ch.setValue(1080)
        self.cu = QComboBox()
        self.cu.addItems(["px", "mm"])
        row.addWidget(self.cw)
        row.addWidget(QLabel("×"))
        row.addWidget(self.ch)
        row.addWidget(self.cu)
        pv.addLayout(row)
        pv.addWidget(_btn("Crear con medidas propias", "ghost", "", lambda: self.load_design(
            G.new_design(self.cw.value(), self.ch.value(), self.cu.currentText()))))
        hint = QLabel("Doble clic en un tamaño para empezar.")
        hint.setObjectName("faint")
        pv.addWidget(hint)
        trow = QHBoxLayout()
        t = QLabel(f"PLANTILLAS ({len(G.TEMPLATES)})")
        t.setObjectName("cardTitle")
        trow.addWidget(t)
        trow.addStretch(1)
        self.tpl_group = QComboBox()
        import graphics_templates as GT
        self.tpl_group.addItems(["Todas"] + GT.GROUPS)
        self.tpl_group.currentTextChanged.connect(self._filter_templates)
        trow.addWidget(self.tpl_group)
        pv.addLayout(trow)
        self.templates = QListWidget()
        self.templates.setViewMode(QListWidget.IconMode)
        self.templates.setIconSize(QSize(110, 110))
        self.templates.setGridSize(QSize(120, 138))
        self.templates.setResizeMode(QListWidget.Adjust)
        self.templates.setMovement(QListWidget.Static)
        self.templates.setWordWrap(True)
        import graphics_templates as GT
        order = {g: i for i, g in enumerate(GT.GROUPS)}
        blank = QPixmap(110, 110)
        blank.fill(QColor(255, 255, 255, 14))
        self._tpl_todo = []
        for name in sorted(G.TEMPLATES, key=lambda n: order.get(G.TEMPLATE_GROUP.get(n, ""), 99)):
            it = QListWidgetItem(QIcon(blank), name)
            it.setData(Qt.UserRole, name)
            it.setToolTip(f"{G.TEMPLATE_GROUP.get(name, '')} · {G.TEMPLATE_SIZE[name]}")
            self.templates.addItem(it)
            self._tpl_todo.append(it)
        QTimer.singleShot(200, self._next_template_icon)  # miniaturas poco a poco: no frenan el arranque
        self.templates.itemDoubleClicked.connect(lambda it: self.use_template(it.data(Qt.UserRole)))
        self.templates.setToolTip("Doble clic: aplicar al diseño actual (o crear uno nuevo con su tamaño si está "
                                  "vacío)")
        pv.addWidget(self.templates, 3)
        tabs.addTab(pg, "Diseño")

        # Elementos de internet (Iconify, Openverse/Pexels, Google Fonts)
        import graphics_elements as GEL
        self.elements = GEL.ElementsPanel(self)
        GEL.register_fonts(self.elements.store)
        tabs.addTab(self.elements, "Elementos")

        # Fotos
        ph = QWidget()
        phv = QVBoxLayout(ph)
        phv.setContentsMargins(4, 8, 4, 4)
        self.photo_info = QLabel("Abre una carpeta para ver sus fotos.")
        self.photo_info.setObjectName("meta")
        self.photo_info.setWordWrap(True)
        phv.addWidget(self.photo_info)
        self.photos = PhotoList()
        self.photos.setViewMode(QListWidget.IconMode)
        self.photos.setIconSize(QSize(112, 112))
        self.photos.setGridSize(QSize(120, 124))
        self.photos.setResizeMode(QListWidget.Adjust)
        self.photos.setMovement(QListWidget.Static)
        self.photos.setDragEnabled(True)
        self.photos.setSelectionMode(QListWidget.ExtendedSelection)
        self.photos.itemDoubleClicked.connect(lambda it: self.place_photo(it.data(Qt.UserRole)))
        self.photos.setToolTip("Arrastra al lienzo o a un hueco de plantilla · doble clic: añadir")
        phv.addWidget(self.photos, 1)
        phv.addWidget(_btn("Añadir imagen o logo de otro sitio…", "ghost", "", self.add_file))
        tabs.addTab(ph, "Fotos")

        # Elementos
        el = QWidget()
        ev = QGridLayout(el)
        ev.setContentsMargins(4, 8, 4, 4)
        r = 0
        for title, items in (("TEXTO", [("Añadir título", lambda: self.add_text("title")),
                                        ("Añadir subtítulo", lambda: self.add_text("subtitle")),
                                        ("Añadir texto", lambda: self.add_text("body"))]),
                             ("FORMAS", [("▭ Rectángulo", lambda: self.add_shape("rect")),
                                         ("◯ Círculo", lambda: self.add_shape("ellipse")),
                                         ("△ Triángulo", lambda: self.add_shape("triangle")),
                                         ("☆ Estrella", lambda: self.add_shape("star")),
                                         ("— Línea", lambda: self.add_shape("line"))]),
                             ("CÓDIGO QR", [("▦ Código QR…", self.add_qr)]),
                             ("MARCOS DE FOTO", [("▭ Rectangular", lambda: self.add_frame("rect")),
                                                 ("◯ Circular", lambda: self.add_frame("ellipse")),
                                                 ("☆ Estrella", lambda: self.add_frame("star")),
                                                 ("△ Triangular", lambda: self.add_frame("triangle"))])):
            t = QLabel(title)
            t.setObjectName("cardTitle")
            ev.addWidget(t, r, 0, 1, 2)
            r += 1
            for i, (txt, cb) in enumerate(items):
                ev.addWidget(_btn(txt, "", "", cb), r + i // 2, i % 2)
            r += (len(items) + 1) // 2
        ev.setRowStretch(r, 1)
        tabs.addTab(el, "Añadir")

        # Mis diseños
        md = QWidget()
        mv = QVBoxLayout(md)
        mv.setContentsMargins(4, 8, 4, 4)
        self.mine = QListWidget()
        self.mine.setViewMode(QListWidget.IconMode)
        self.mine.setIconSize(QSize(110, 110))
        self.mine.setGridSize(QSize(122, 146))
        self.mine.setResizeMode(QListWidget.Adjust)
        self.mine.setMovement(QListWidget.Static)
        self.mine.setWordWrap(True)
        self.mine.itemDoubleClicked.connect(lambda it: self.open_path(it.data(Qt.UserRole)))
        mv.addWidget(self.mine, 1)
        row = QHBoxLayout()
        row.addWidget(_btn("Abrir", "", "", lambda: self.mine.currentItem() and
                           self.open_path(self.mine.currentItem().data(Qt.UserRole))))
        row.addWidget(_btn("Duplicar", "", "", self.duplicate_design))
        row.addWidget(_btn("Eliminar", "", "", self.delete_design))
        mv.addLayout(row)
        tabs.addTab(md, "Míos")
        tabs.addTab(self._build_brand(), "Marca")
        tabs.currentChanged.connect(lambda i: tabs.widget(i) is md and self.refresh_mine())
        return tabs

    def _next_template_icon(self):
        if self._closing or not self._tpl_todo:
            return
        it = self._tpl_todo.pop(0)
        try:
            it.setIcon(self._template_icon(it.data(Qt.UserRole)))
        except RuntimeError:  # la lista ya no existe
            return
        QTimer.singleShot(0, self._next_template_icon)

    def _filter_templates(self, group):
        for i in range(self.templates.count()):
            it = self.templates.item(i)
            it.setHidden(group != "Todas" and G.TEMPLATE_GROUP.get(it.data(Qt.UserRole)) != group)

    def _template_icon(self, name):
        d = G.design_from_preset(G.TEMPLATE_SIZE[name])
        G.apply_template(d, name)
        img = G.thumbnail(d, G.ImageCache(), 110)
        p = QPainter(img)
        for el in d["elements"]:
            if el["type"] == "image" and not el["path"]:
                s = 110 / max(d["w"], d["h"])
                p.save()
                p.scale(s, s)
                p.setTransform(G.element_transform(el), True)
                G.paint_element(p, el, G.ImageCache(), editing=True)
                p.restore()
        p.end()
        return QIcon(QPixmap.fromImage(img))

    def _build_tools(self):
        w = QFrame()
        w.setObjectName("vcTools")
        w.setFixedWidth(52)
        v = QVBoxLayout(w)
        v.setContentsMargins(5, 10, 5, 10)
        v.setSpacing(4)
        self.tool_btn = {}
        grp = QButtonGroup(self)
        for k, _, tip in TOOLS:
            b = QPushButton()
            b.setObjectName("vcTool")
            b.setIcon(vc_icon(k))
            b.setIconSize(QSize(22, 22))
            b.setFixedSize(40, 38)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, k=k: self.set_tool(k))
            grp.addButton(b)
            v.addWidget(b, 0, Qt.AlignHCenter)
            self.tool_btn[k] = b
        self.tool_btn["select"].setChecked(True)
        v.addStretch(1)
        # relleno y contorno para lo que se dibuje (pluma, formas), como los cuadros de Illustrator/VectorCraft
        self.fill_btn = ColorButton(self.pen_fill, True, self.design_colors)
        self.stroke_btn = ColorButton(self.pen_stroke, False, self.design_colors)
        for b, tip in ((self.fill_btn, "Relleno de lo que dibujes (pluma, formas)"),
                       (self.stroke_btn, "Contorno de lo que dibujes con la pluma")):
            b.setText("")
            b.setFixedSize(30, 30)
            b.setIconSize(QSize(22, 22))
            b.setToolTip(tip)
            v.addWidget(b, 0, Qt.AlignHCenter)
        self.fill_btn.changed.connect(lambda c: (setattr(self, "pen_fill", c), self.fill_btn.setText("")))
        self.stroke_btn.changed.connect(lambda c: (setattr(self, "pen_stroke", c), self.stroke_btn.setText("")))
        return w

    def _build_doctab(self):
        """Pestaña del documento: «nombre @ zoom (RGB/Vista previa)», con el nombre editable."""
        w = QFrame()
        w.setObjectName("vcDocTab")
        w.setFixedHeight(38)
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 4, 12, 4)
        h.setSpacing(4)
        self.name = QLineEdit()
        self.name.setObjectName("vcDocName")
        self.name.setFixedWidth(220)
        self.name.setToolTip("Nombre del diseño (clic para cambiarlo)")
        self.name.setStyleSheet("QLineEdit { background: transparent; border: 1px solid transparent; "
                                "font-weight: 600; color: #F2F3F6; } QLineEdit:focus { border-color: #4C82F7; "
                                f"background: {VC['field']}; }}")
        self.name.editingFinished.connect(self._rename)
        h.addWidget(self.name)
        self.zoom_lbl = QLabel("")
        self.zoom_lbl.setObjectName("vcDocName")
        h.addWidget(self.zoom_lbl)
        h.addStretch(1)
        self.saved_lbl = QLabel("")
        self.saved_lbl.setObjectName("faint")
        h.addWidget(self.saved_lbl)
        h.addSpacing(10)
        h.addWidget(_btn("Exportar", "primary", "PDF de imprenta, PNG, JPG o SVG (Ctrl+E)", self.export))
        return w

    def _build_menubar(self):
        mb = QMenuBar()
        mb.setNativeMenuBar(False)

        def menu(title, entries):
            m = mb.addMenu(title)
            out = {}
            for e_ in entries:
                if e_ is None:
                    m.addSeparator()
                    continue
                if isinstance(e_[1], list):
                    sub = m.addMenu(e_[0])
                    for t2, f2 in e_[1]:
                        sub.addAction(t2).triggered.connect(f2)
                    continue
                txt, fn = e_[0], e_[1]
                a = m.addAction(txt)
                if len(e_) > 2 and e_[2]:
                    a.setShortcut(QKeySequence(e_[2]))
                    a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                    self.addAction(a)
                a.triggered.connect(fn)
                out[txt] = a
            return out

        menu("Archivo", [
            ("Nuevo diseño…", lambda: self.show_library(0)),
            ("Abrir de «Míos»…", lambda: self.show_library(4)),
            ("Duplicar diseño", self.duplicate_design), None,
            ("Añadir imagen o logo…", self.add_file), None,
            ("Guardar", self.save_now, "Ctrl+S"),
            ("Exportar…", self.export, "Ctrl+E")])
        ed = menu("Edición", [
            ("Deshacer", self.undo, "Ctrl+Z"), ("Rehacer", self.redo, "Ctrl+Y"), None,
            ("Copiar", self.copy), ("Pegar", self.paste), ("Duplicar", self.duplicate),
            ("Eliminar", self.delete), None, ("Seleccionar todo", self.select_all)])
        self.b_undo, self.b_redo = ed["Deshacer"], ed["Rehacer"]
        ob = menu("Objeto", [
            ("Organizar", [("Traer al frente", lambda: self.order("front")),
                           ("Traer adelante", lambda: self.order("up")),
                           ("Enviar atrás", lambda: self.order("down")),
                           ("Enviar al fondo", lambda: self.order("back"))]),
            ("Agrupar", self.group), ("Desagrupar", self.ungroup), ("Bloquear / desbloquear", self.toggle_lock),
            None,
            ("Buscatrazos", [(t, lambda _=False, k=k: self.pathfinder(k)) for k, t in G.PATHFINDER.items()]),
            ("Alinear", [("Izquierda", lambda: self.align("left")), ("Centro horizontal", lambda: self.align("hcenter")),
                         ("Derecha", lambda: self.align("right")), ("Arriba", lambda: self.align("top")),
                         ("Centro vertical", lambda: self.align("vcenter")), ("Abajo", lambda: self.align("bottom")),
                         ("Repartir en horizontal", lambda: self.distribute("h")),
                         ("Repartir en vertical", lambda: self.distribute("v"))]),
            None,
            ("Quitar fondo…", self.remove_bg), ("Vectorizar imagen…", self.vectorize),
            ("Código QR…", self.add_qr)])
        self.b_bg, self.b_vec = ob["Quitar fondo…"], ob["Vectorizar imagen…"]
        menu("Texto", [("Añadir título", lambda: self.add_text("title")),
                       ("Añadir subtítulo", lambda: self.add_text("subtitle")),
                       ("Añadir texto", lambda: self.add_text("body")), None,
                       ("Crear contornos", self.outline_text, "Ctrl+Shift+O")])
        menu("Seleccionar", [("Todo", self.select_all), ("Nada", lambda: self.canvas.sc.clearSelection())])
        vw = menu("Ver", [("Encajar la página", self.canvas.fit, "Ctrl+0"),
                          ("Tamaño real (100 %)", lambda: self.canvas.set_zoom(1.0), "Ctrl+1"), None,
                          ("Imán (guías inteligentes)", lambda: None)])
        self.snap_cb = vw["Imán (guías inteligentes)"]
        self.snap_cb.setCheckable(True)
        self.snap_cb.setChecked(True)
        self.snap_cb.toggled.connect(lambda on: setattr(self.canvas, "snap_on", on))
        menu("Ventana", [("Propiedades", lambda: self.right_tabs.setCurrentIndex(0)),
                         ("Capas", lambda: self.right_tabs.setCurrentIndex(1)), None,
                         ("Bibliotecas: diseño y plantillas", lambda: self.show_library(0)),
                         ("Bibliotecas: elementos de internet", lambda: self.show_library(1)),
                         ("Bibliotecas: fotos", lambda: self.show_library(2)),
                         ("Bibliotecas: añadir", lambda: self.show_library(3)),
                         ("Bibliotecas: mis diseños", lambda: self.show_library(4)),
                         ("Bibliotecas: marca", lambda: self.show_library(5))])
        menu("Ayuda", [("Atajos de teclado", self.show_shortcuts)])
        return mb

    def show_library(self, i):
        self.right_tabs.setCurrentIndex(2)
        self.tabs.setCurrentIndex(i)

    def show_shortcuts(self):
        QMessageBox.information(self, "Atajos", "V selección · A selección directa · P pluma · T texto · "
                                "M rectángulo · L elipse · H mano · Z zoom\n"
                                "Ctrl+Z / Ctrl+Y deshacer y rehacer · Ctrl+D duplicar · Ctrl+G agrupar · "
                                "Supr eliminar\nCtrl+] / Ctrl+[ adelante y atrás · flechas: mover (Mayús ×10)\n"
                                "Ctrl+Mayús+O crear contornos · Ctrl+E exportar · Ctrl+0 encajar · Ctrl+1 100 %\n"
                                "Rueda: zoom · espacio + arrastrar: desplazar")

    def _build_hint(self):
        w = QFrame()
        w.setObjectName("vcHint")
        w.setFixedHeight(28)
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 2, 12, 2)
        q = QLabel("?")
        q.setFixedSize(18, 18)
        q.setAlignment(Qt.AlignCenter)
        q.setStyleSheet(f"border: 1px solid {VC['muted']}; border-radius: 9px; color: {VC['muted']};")
        h.addWidget(q)
        self.hint_lbl = QLabel(TOOL_HINTS["select"])
        h.addWidget(self.hint_lbl, 1)
        return w

    def _build_status(self):
        w = QFrame()
        w.setObjectName("vcStatus")
        w.setFixedHeight(32)
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 2, 12, 2)
        h.setSpacing(6)
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.addItems(["Encajar", "25 %", "50 %", "100 %", "200 %", "400 %"])
        self.zoom_box.setFixedWidth(96)
        self.zoom_box.activated.connect(self._zoom_from_box)
        h.addWidget(self.zoom_box)
        h.addSpacing(10)
        for txt, tip, fn in (("«", "Primera página", lambda: self.goto_page(0)),
                             ("‹", "Página anterior", lambda: self.goto_page(self.d.get("page", 0) - 1))):
            h.addWidget(_btn(txt, "flat", tip, fn))
        self.pages = QComboBox()
        self.pages.setFixedWidth(190)
        self.pages.setIconSize(QSize(28, 28))
        self.pages.setToolTip("Mesas de trabajo (páginas) del diseño")
        self.pages.currentIndexChanged.connect(self.goto_page)
        h.addWidget(self.pages)
        for txt, tip, fn in (("›", "Página siguiente", lambda: self.goto_page(self.d.get("page", 0) + 1)),
                             ("»", "Última página", lambda: self.goto_page(len(self.d["pages"]) - 1))):
            h.addWidget(_btn(txt, "flat", tip, fn))
        pg = QToolButton()
        pg.setText("+ Página ▾")
        pg.setPopupMode(QToolButton.InstantPopup)
        pg.setStyleSheet("QToolButton { background: transparent; border: none; padding: 3px 8px; } "
                         "QToolButton::menu-indicator { image: none; }")
        m = QMenu(pg)
        for t, op in (("Página nueva", "add"), ("Duplicar esta página", "dup"), ("Mover antes", "left"),
                      ("Mover después", "right"), ("Eliminar esta página", "del")):
            m.addAction(t).triggered.connect(lambda _=False, op=op: self.page_op(op))
        pg.setMenu(m)
        h.addWidget(pg)
        h.addSpacing(14)
        self.tool_lbl = QLabel("Selección")
        self.tool_lbl.setObjectName("muted")
        h.addWidget(self.tool_lbl)
        h.addStretch(1)
        self.size_lbl = QLabel("")
        self.size_lbl.setObjectName("faint")
        h.addWidget(self.size_lbl)
        return w

    def _zoom_from_box(self, *_):
        t = self.zoom_box.currentText().replace("%", "").strip().replace(",", ".")
        if t.lower().startswith("encaj"):
            self.canvas.fit()
            return
        try:
            self.canvas.set_zoom(max(0.02, min(40.0, float(t) / 100)))
        except ValueError:
            pass

    def _build_right(self):
        w = QFrame()
        w.setObjectName("vcRight")
        w.setFixedWidth(340)
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(0)
        self.right_tabs = QTabWidget()
        self.right_tabs.tabBar().setExpanding(False)
        # Propiedades
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.props_area = sa
        self.props_host = QWidget()
        self.props = QVBoxLayout(self.props_host)
        sa.setWidget(self.props_host)
        self.right_tabs.addTab(sa, "Propiedades")
        # Capas
        self.layers = QListWidget()
        self.layers.setSelectionMode(QListWidget.ExtendedSelection)
        self.layers.setDragDropMode(QListWidget.InternalMove)
        self.layers.itemSelectionChanged.connect(self._layers_to_scene)
        self.layers.itemChanged.connect(self._layer_checked)
        self.layers.model().rowsMoved.connect(self._layers_moved)
        self.layers.setToolTip("Casilla: visible · arrastra para cambiar el orden")
        self.right_tabs.addTab(self.layers, "Capas")
        # Bibliotecas (Canva)
        self.right_tabs.addTab(self._build_libraries(), "Bibliotecas")
        v.addWidget(self.right_tabs)
        return w

    # ---------------------------------------------------------- barra contextual
    def place_taskbar(self):
        if not hasattr(self, "taskbar"):
            return
        self.taskbar.place()

    def _shortcuts(self):
        ctx = Qt.WidgetWithChildrenShortcut
        for k, fn in (("Ctrl+Shift+Z", self.redo),):
            s = QShortcut(QKeySequence(k), self, fn)
            s.setContext(ctx)
        for k, fn in (("Delete", self.delete), ("Backspace", self.delete), ("Ctrl+D", self.duplicate),
                      ("Ctrl+C", self.copy), ("Ctrl+V", self.paste), ("Ctrl+A", self.select_all),
                      ("Ctrl+G", self.group), ("Ctrl+Shift+G", self.ungroup),
                      ("Ctrl+]", lambda: self.order("up")), ("Ctrl+[", lambda: self.order("down")),
                      ("Ctrl+Shift+]", lambda: self.order("front")), ("Ctrl+Shift+[", lambda: self.order("back")),
                      ("V", lambda: self.set_tool("select")), ("A", lambda: self.set_tool("direct")),
                      ("P", lambda: self.set_tool("pen")), ("T", lambda: self.set_tool("text")),
                      ("M", lambda: self.set_tool("rect")), ("L", lambda: self.set_tool("ellipse")),
                      ("H", lambda: self.set_tool("hand")), ("Z", lambda: self.set_tool("zoom")),
                      ("Escape", self.escape),
                      ("Left", lambda: self.nudge(-1, 0)), ("Right", lambda: self.nudge(1, 0)),
                      ("Up", lambda: self.nudge(0, -1)), ("Down", lambda: self.nudge(0, 1)),
                      ("Shift+Left", lambda: self.nudge(-10, 0)), ("Shift+Right", lambda: self.nudge(10, 0)),
                      ("Shift+Up", lambda: self.nudge(0, -10)), ("Shift+Down", lambda: self.nudge(0, 10))):
            s = QShortcut(QKeySequence(k), self.canvas, fn)
            s.setContext(Qt.WidgetShortcut)

    # ---------------------------------------------------------- diseño
    def _initial_design(self):
        ds = G.list_designs(self.ddir)
        if ds:
            return ds[0][1]
        d = G.design_from_preset("Instagram vertical 4:5")
        G.apply_template(d, "Post de evento")
        return d

    def load_design(self, d: dict):
        if self._last:
            self.save_now()
        self.d = G.ensure_pages(d)
        self.undo_st, self.redo_st = [], []
        self._last = json.dumps(d, sort_keys=True)
        self.name.setText(d.get("name", ""))
        self.size_lbl.setText(G.size_label(d))
        self.canvas.set_page()
        self.rebuild()
        QTimer.singleShot(0, self.canvas.fit)
        self._update_undo()
        self.refresh_pages()

    def new_design(self, preset: str):
        self.load_design(G.design_from_preset(preset))

    def use_template(self, name: str):
        if not self.d["elements"]:
            d = G.design_from_preset(G.TEMPLATE_SIZE[name])
            d["name"] = name
            G.apply_template(d, name)
            self.load_design(d)
            return
        if QMessageBox.question(self, "Plantilla", f"¿Aplicar «{name}» a este diseño? Sustituye sus elementos "
                                                   "(se puede deshacer). Para empezar uno nuevo con el tamaño de "
                                                   "la plantilla elige «No» y luego un diseño vacío.") \
                != QMessageBox.Yes:
            return
        G.apply_template(self.d, name)
        self.rebuild()
        self.commit()
        self.refresh_pages()

    def open_path(self, path):
        try:
            self.load_design(G.load(path))
        except Exception as ex:
            QMessageBox.warning(self, "Abrir diseño", f"No se pudo abrir: {ex}")

    def duplicate_design(self):
        d = copy.deepcopy(self.d)
        d["id"] = G.new_id()
        d["name"] = d.get("name", "") + " (copia)"
        self.load_design(d)
        self.save_now()
        self.refresh_mine()

    def delete_design(self):
        it = self.mine.currentItem()
        if not it:
            return
        path = it.data(Qt.UserRole)
        if QMessageBox.question(self, "Eliminar diseño", f"¿Eliminar «{it.text()}»? Las fotos que usa no se "
                                                         "tocan.") != QMessageBox.Yes:
            return
        for p in (path, os.path.splitext(path)[0] + ".png"):
            try:
                os.remove(p)
            except OSError:
                pass
        if os.path.normcase(path) == os.path.normcase(os.path.join(self.ddir, self.d["id"] + ".json")):
            self._last = ""
            self.load_design(G.design_from_preset("Instagram cuadrado"))
        self.refresh_mine()

    def refresh_mine(self):
        self.mine.clear()
        for path, d in G.list_designs(self.ddir):
            png = os.path.splitext(path)[0] + ".png"
            icon = QIcon(png) if os.path.exists(png) else QIcon()
            it = QListWidgetItem(icon, f"{d.get('name', '')}\n{G.size_label(d)}")
            it.setData(Qt.UserRole, path)
            self.mine.addItem(it)

    def _rename(self):
        n = self.name.text().strip() or "Diseño sin título"
        if n != self.d.get("name"):
            self.d["name"] = n
            self.commit()

    def save_now(self):
        if not self.d.get("elements") and not self.undo_st:
            return  # no llenar «Mis diseños» de diseños vacíos
        try:
            path = G.save(self.d, self.ddir)
            G.thumbnail(G.page_views(self.d)[0], self.canvas.cache, 220).save(os.path.splitext(path)[0] + ".png")
            self.saved_lbl.setText("Guardado")
        except OSError as ex:
            self.saved_lbl.setText(f"No se pudo guardar: {ex}")

    def set_folder(self, folder: str):
        if not folder or folder == self.folder or not os.path.isdir(folder):
            return
        self.folder = folder
        paths = []
        for sub in ("", "Editadas"):
            dd = os.path.join(folder, sub)
            if os.path.isdir(dd):
                for n in sorted(os.listdir(dd)):
                    ext = os.path.splitext(n)[1].lower()
                    if ext in E.IMG_EXT or ext in E.RAW_EXT:
                        paths.append(os.path.join(dd, n))
        self._photo_paths = paths
        self.photos.clear()
        ph = QPixmap(112, 112)
        ph.fill(QColor("#222"))
        for p in paths:
            it = QListWidgetItem(QIcon(ph), "")
            it.setData(Qt.UserRole, p)
            it.setToolTip(os.path.relpath(p, folder))
            self.photos.addItem(it)
        self.photo_info.setText(f"{len(paths)} fotos de «{os.path.basename(folder)}» (y su carpeta Editadas). "
                                "Arrástralas al lienzo.")
        threading.Thread(target=self._thumb_loop, args=(list(paths),), daemon=True).start()

    def _thumb_loop(self, paths):
        from studio import quick_thumb
        for i, p in enumerate(paths):
            if self._closing or (paths is not self._photo_paths and paths != self._photo_paths):
                return
            data = quick_thumb(p, 224)
            if self._closing:
                return
            try:
                self.thumbs_ready.emit(i, data)
            except RuntimeError:  # la vista ya se destruyó (cierre de la app)
                return

    def _on_thumb(self, i, data):
        it = self.photos.item(i)
        if it and data:
            pm = QPixmap()
            pm.loadFromData(data)
            it.setIcon(QIcon(pm))

    # ---------------------------------------------------------- escena
    def rebuild(self, select=None):
        """Recrea los elementos del lienzo desde el diseño (tras deshacer, plantillas, orden…)."""
        keep = select if select is not None else [i.el["id"] for i in self.canvas.sc.selectedItems()]
        self._building = True
        self.canvas.hover = None
        self.canvas.sc.clear()
        self.items = {}
        for z, el in enumerate(self.d["elements"]):
            it = ElementItem(self.canvas, el)
            it.setZValue(z)
            self.canvas.sc.addItem(it)
            self.items[el["id"]] = it
            if el["id"] in keep:
                it.setSelected(True)
        self._building = False
        self.canvas.viewport().update()
        self.refresh_layers()
        self.on_selection()

    def selected(self) -> list[dict]:
        return [i.el for i in sorted(self.canvas.sc.selectedItems(), key=lambda i: i.zValue())]

    def on_selection(self):
        if self._building:
            return
        try:
            sel_items = self.canvas.sc.selectedItems()
        except RuntimeError:  # la escena ya se destruyó (cierre de la ventana)
            return
        groups = {i.el.get("group") for i in sel_items if i.el.get("group")}
        if groups and self.canvas.tool != "direct":  # un grupo se selecciona y se mueve entero
            self._building = True
            for it in self.items.values():
                if it.el.get("group") in groups and not it.isSelected():
                    it.setSelected(True)
            self._building = False
        self._sync_layer_sel()
        self.build_props()
        sel = self.selected()
        img = len(sel) == 1 and sel[0]["type"] == "image" and bool(sel[0].get("path"))
        self.b_bg.setEnabled(img)
        self.b_vec.setEnabled(img)
        self.taskbar.update_for(sel)

    def set_tool(self, k):
        if self.canvas.tool == "pen" and k != "pen":
            self.canvas.finish_pen(False)
        self.canvas.tool = k
        self.tool_btn[k].setChecked(True)
        self.hint_lbl.setText(TOOL_HINTS.get(k, ""))
        self.tool_lbl.setText(TOOL_NAMES.get(k, ""))
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag if k in ("select", "direct") else
                                QGraphicsView.NoDrag)
        self.apply_cursor()
        for it in self.items.values():
            it.update()
        self.canvas.setFocus()

    def apply_cursor(self):
        cur = {"pen": Qt.CrossCursor, "rect": Qt.CrossCursor, "ellipse": Qt.CrossCursor, "text": Qt.IBeamCursor,
               "hand": Qt.OpenHandCursor, "zoom": Qt.PointingHandCursor}.get(self.canvas.tool, Qt.ArrowCursor)
        self.canvas.viewport().setCursor(cur)

    def escape(self):
        if self.canvas.tool == "pen" and self.canvas.pen_nodes:
            self.canvas.finish_pen(False)
        elif self.canvas.tool != "select":
            self.set_tool("select")
        else:
            self.canvas.sc.clearSelection()

    def zoom_changed(self):
        z = self.canvas.transform().m11() * 100
        mode = "CMYK/Imprenta" if self._mm() else "RGB"
        self.zoom_lbl.setText(f"@ {z:.0f} % ({mode}/Vista previa)")
        self.zoom_box.setEditText(f"{z:.0f} %")
        self.place_taskbar()

    # ---------------------------------------------------------- historial
    def commit(self, coalesce: str | None = None):
        G.sync_page(self.d)
        js = json.dumps(self.d, sort_keys=True)
        if js == self._last:
            return
        now = time.time()
        if not (coalesce and self._co[0] == coalesce and now - self._co[1] < 1.2):
            self.undo_st.append(self._last)
            del self.undo_st[:-200]
        self._co = (coalesce, now)
        self._last = js
        self.redo_st.clear()
        self.saved_lbl.setText("Sin guardar…")
        self.save_timer.start(1500)
        self.refresh_layers()
        self._update_undo()
        self.pages_timer.start(600)

    def _restore(self, js):
        sel = [i.el["id"] for i in self.canvas.sc.selectedItems()]
        self.d = G.ensure_pages(json.loads(js))
        self._last = js
        self.name.setText(self.d.get("name", ""))
        self.size_lbl.setText(G.size_label(self.d))
        self.canvas.set_page()
        self.rebuild(select=sel)
        self.save_timer.start(1500)
        self._update_undo()
        self.refresh_pages()

    def undo(self):
        if self.undo_st:
            self.redo_st.append(self._last)
            self._restore(self.undo_st.pop())

    def redo(self):
        if self.redo_st:
            self.undo_st.append(self._last)
            self._restore(self.redo_st.pop())

    def _update_undo(self):
        self.b_undo.setEnabled(bool(self.undo_st))
        self.b_redo.setEnabled(bool(self.redo_st))

    def after_interaction(self):
        """Fin de un arrastre: pasar las posiciones del lienzo al diseño y apuntarlo en el historial."""
        for it in self.items.values():
            if abs(it.pos().x() - it.el["x"]) > 1e-6 or abs(it.pos().y() - it.el["y"]) > 1e-6:
                it.el["x"], it.el["y"] = it.pos().x(), it.pos().y()
        self.commit()
        self.live_geometry()
        self.place_taskbar()

    def live_geometry(self):
        sel = self.selected()
        if len(sel) == 1 and getattr(self, "_geo", None):
            el = sel[0]
            for k, sb in self._geo.items():
                sb.blockSignals(True)
                sb.setValue(self._to_unit(el[k]) if k != "rot" else el.get("rot", 0.0))
                sb.blockSignals(False)
            if el["type"] == "text" and getattr(self, "_size_sb", None):
                self._size_sb.blockSignals(True)
                self._size_sb.setValue(self._font_pt(el["size"]))
                self._size_sb.blockSignals(False)

    # ---------------------------------------------------------- unidades
    def _mm(self):
        return self.d.get("unit") == "mm"

    def _to_unit(self, px):
        return G.px_to_mm(px, self.d["dpi"]) if self._mm() else px

    def _from_unit(self, v):
        return G.mm_to_px(v, self.d["dpi"]) if self._mm() else v

    def _font_pt(self, px):
        """Tamaño de letra mostrado: puntos tipográficos en impresión, píxeles en redes."""
        return px / self.d["dpi"] * 72 if self._mm() else px

    def _font_px(self, v):
        return v * self.d["dpi"] / 72 if self._mm() else v

    def design_colors(self):
        cols = list(self.brand.get("colors", [])) + [self.d.get("bg", "#FFFFFF")]
        for e in self.d["elements"]:
            for k in ("fill", "stroke", "color"):
                if e.get(k):
                    cols.append(e[k])
            for sp in e.get("paths", [])[:6]:
                cols.append(sp["fill"])
        return [c for c in dict.fromkeys(cols) if not (len(c) == 9 and c.endswith("00"))]

    # ---------------------------------------------------------- propiedades
    def _clear_props(self):
        """Panel nuevo cada vez (QScrollArea borra el anterior): nada de restos del panel previo."""
        self.props_host = QWidget()
        self.props = QVBoxLayout(self.props_host)
        self.props.setContentsMargins(10, 6, 10, 10)
        self.props.setSpacing(6)
        self.props_area.setWidget(self.props_host)

    def _title(self, t):
        """Sección del panel Propiedades (Transformar, Carácter, Apariencia, Alinear…), con una línea encima."""
        if self.props.count():
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background: {VC['border']};")
            self.props.addWidget(line)
        lab = QLabel(t)
        lab.setObjectName("vcSection")
        self.props.addWidget(lab)

    def _row(self, label, w_):
        w_.setMinimumWidth(0)
        w_.setSizePolicy(QSizePolicy.Ignored, w_.sizePolicy().verticalPolicy())
        r = QHBoxLayout()
        lab = QLabel(label)
        lab.setFixedWidth(84)
        r.addWidget(lab)
        r.addWidget(w_, 1)
        self.props.addLayout(r)

    def _spin(self, val, lo, hi, dec=1, suffix="", step=1.0):
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(dec)
        sb.setSingleStep(step)
        sb.setValue(val)
        if suffix:
            sb.setSuffix(suffix)
        sb.setKeyboardTracking(False)
        sb.setMinimumWidth(40)
        sb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        return sb

    def set_prop(self, el, key, val, refit_text=False):
        el[key] = val
        if el["type"] == "text" and key == "size" and el.get("fit_h"):
            el["size_max"] = val  # el tamaño que elige el usuario es el nuevo máximo del autoajuste
        if el["type"] == "text" and (refit_text or key in ("text", "font", "size", "bold", "italic", "line",
                                                           "spacing", "upper", "w", "curve")):
            G.fit_text(el)
            if key != "size" and getattr(self, "_size_sb", None):
                self._size_sb.blockSignals(True)
                self._size_sb.setValue(self._font_pt(el["size"]))
                self._size_sb.blockSignals(False)
        it = self.items.get(el["id"])
        if it:
            it.sync()
        self.canvas.viewport().update()
        self.commit(coalesce=f"{el['id']}:{key}")

    def build_props(self):
        self._clear_props()
        self._geo = {}
        self._size_sb = None
        self.text_edit = None
        sel = self.selected()
        if not sel:
            return self._page_props()
        if len(sel) > 1:
            kind = QLabel(f"{len(sel)} objetos")
            kind.setObjectName("vcKind")
            self.props.addWidget(kind)
            self._title("Alinear")
            g = QGridLayout()
            for i, (t, k) in enumerate((("⇤", "left"), ("⇹", "hcenter"), ("⇥", "right"), ("⤒", "top"),
                                        ("⇕", "vcenter"), ("⤓", "bottom"))):
                g.addWidget(_btn(t, "", f"Alinear: {k}", lambda _=False, k=k: self.align(k)), i // 3, i % 3)
            self.props.addLayout(g)
            self._title("Buscatrazos")
            g2 = QGridLayout()
            for i, (k, t) in enumerate(G.PATHFINDER.items()):
                g2.addWidget(_btn(t, "", "", lambda _=False, k=k: self.pathfinder(k)), i // 2, i % 2)
            self.props.addLayout(g2)
            self.props.addStretch(1)
            return
        el = sel[0]
        t = el["type"]
        kind = QLabel({"text": "Texto", "image": "Imagen", "vector": "Trazado", "shape": "Forma"}[t])
        kind.setObjectName("vcKind")
        self.props.addWidget(kind)
        self._transform_section(el)
        self._title({"text": "Carácter", "image": "Imagen", "vector": "Colores", "shape": "Forma"}[t])
        if t == "text":
            self.text_edit = QPlainTextEdit(el.get("text", ""))
            self.text_edit.setStyleSheet(f"QPlainTextEdit {{ background: #121212; border: 1px solid {T.LINE}; "
                                         f"border-radius: 3px; color: {T.TEXT_STRONG}; }}")
            self.text_edit.setFixedHeight(80)
            self.text_edit.textChanged.connect(lambda: self.set_prop(el, "text", self.text_edit.toPlainText()))
            self.props.addWidget(self.text_edit)
            fc = QFontComboBox()
            fc.setCurrentFont(G.text_font(el))
            fc.currentFontChanged.connect(lambda f: self.set_prop(el, "font", f.family()))
            self._row("Fuente", fc)
            sz = self._size_sb = self._spin(self._font_pt(el["size"]), 1, 2000, 1, " pt" if self._mm() else " px")
            sz.valueChanged.connect(lambda v: self.set_prop(el, "size", self._font_px(v)))
            self._row("Tamaño", sz)
            r = QHBoxLayout()
            for key, txt, tip in (("bold", "N", "Negrita"), ("italic", "K", "Cursiva"), ("upper", "AA", "Mayúsculas")):
                b = _btn(txt, "seg", tip)
                b.setCheckable(True)
                b.setChecked(bool(el.get(key)))
                b.toggled.connect(lambda on, key=key: self.set_prop(el, key, on))
                r.addWidget(b)
            r.addSpacing(8)
            grp = QButtonGroup(self.props_host)
            for a, txt in (("left", "⯇"), ("center", "≡"), ("right", "⯈"), ("justify", "☰")):
                b = _btn(txt, "seg", {"left": "Izquierda", "center": "Centro", "right": "Derecha",
                                      "justify": "Justificado"}[a])
                b.setCheckable(True)
                b.setChecked(el.get("align") == a)
                grp.addButton(b)
                b.clicked.connect(lambda _=False, a=a: self.set_prop(el, "align", a))
                r.addWidget(b)
            self.props.addLayout(r)
            cb = ColorButton(el.get("color", "#000000"), False, self.design_colors)
            cb.changed.connect(lambda c: self.set_prop(el, "color", c))
            self._row("Color", cb)
            ln = self._spin(el.get("line", 1.15), 0.6, 3, 2, "", 0.05)
            ln.valueChanged.connect(lambda v: self.set_prop(el, "line", v))
            self._row("Interlineado", ln)
            sp = self._spin(el.get("spacing", 0.0), -5, 40, 1, "", 0.5)
            sp.valueChanged.connect(lambda v: self.set_prop(el, "spacing", v))
            self._row("Espaciado", sp)
            cv = QSlider(Qt.Horizontal)
            cv.setRange(-180, 180)
            cv.setValue(int(el.get("curve", 0)))
            cv.setToolTip("Texto en arco: a la derecha curva hacia arriba, a la izquierda hacia abajo (0 = recto)")
            cv.valueChanged.connect(lambda v: self.set_prop(el, "curve", float(v) if abs(v) >= 3 else 0.0))
            self._row("Curva", cv)
            self._title("Efectos de texto")
            oc = ColorButton(el.get("outline", "#000000"), False, self.design_colors)
            oc.changed.connect(lambda c: self.set_prop(el, "outline", c))
            ow = self._spin(self._to_unit(el.get("outline_w", 0.0)), 0, 500, 2 if self._mm() else 1,
                            " mm" if self._mm() else " px", 0.5)
            ow.valueChanged.connect(lambda v: self.set_prop(el, "outline_w", self._from_unit(v)))
            r = QHBoxLayout()
            r.addWidget(oc)
            r.addWidget(ow)
            lab = QLabel("Contorno")
            lab.setObjectName("muted")
            lab.setFixedWidth(84)
            r.insertWidget(0, lab)
            self.props.addLayout(r)
            sh = el.get("shadow") or {}
            shon = QCheckBox("Sombra")
            shon.setChecked(bool(sh))
            shc = ColorButton(sh.get("color", "#00000080"), False, self.design_colors)
            sho = self._spin(self._to_unit(sh.get("dx", el["size"] * 0.06)), 0, 500, 1,
                             " mm" if self._mm() else " px", 0.5)

            def upd_sh(*_):
                o = self._from_unit(sho.value())
                self.set_prop(el, "shadow", {"color": shc.value, "dx": o, "dy": o} if shon.isChecked() else None)
            shon.toggled.connect(upd_sh)
            shc.changed.connect(upd_sh)
            sho.valueChanged.connect(upd_sh)
            r = QHBoxLayout()
            r.addWidget(shon)
            r.addWidget(shc)
            r.addWidget(sho)
            self.props.addLayout(r)
        elif t == "shape":
            if el.get("shape") != "line":
                fb = ColorButton(el.get("fill", "#000000"), True, self.design_colors)
                fb.changed.connect(lambda c: (el.pop("grad", None), self.set_prop(el, "fill", c)))
                self._row("Relleno", fb)
                self._gradient_row(el)
            sb_ = ColorButton(el.get("stroke", "#000000"), False, self.design_colors)
            sb_.changed.connect(lambda c: self.set_prop(el, "stroke", c))
            self._row("Contorno", sb_)
            sw = self._spin(self._to_unit(el.get("stroke_w", 0.0)), 0, 2000, 2 if self._mm() else 1,
                            " mm" if self._mm() else " px", 0.5)
            sw.valueChanged.connect(lambda v: self.set_prop(el, "stroke_w", self._from_unit(v)))
            self._row("Grosor", sw)
            if el.get("shape") == "rect":
                rd = self._spin(self._to_unit(el.get("radius", 0.0)), 0, 5000, 1, " mm" if self._mm() else " px")
                rd.valueChanged.connect(lambda v: self.set_prop(el, "radius", self._from_unit(v)))
                self._row("Esquinas", rd)
        elif t == "image":
            info = QLabel(os.path.basename(el.get("path", "")) or "Hueco vacío: arrastra una foto encima")
            info.setObjectName("meta")
            info.setWordWrap(True)
            self.props.addWidget(info)
            z = QSlider(Qt.Horizontal)
            z.setRange(100, 400)
            z.setValue(int(el.get("zoom", 1.0) * 100))
            z.valueChanged.connect(lambda v: self.set_prop(el, "zoom", v / 100))
            self._row("Zoom", z)
            for key, lab in (("fx", "Encuadre ↔"), ("fy", "Encuadre ↕")):
                s = QSlider(Qt.Horizontal)
                s.setRange(0, 100)
                s.setValue(int(el.get(key, 0.5) * 100))
                s.valueChanged.connect(lambda v, key=key: self.set_prop(el, key, v / 100))
                self._row(lab, s)
            cl = QComboBox()
            for k, txt in (("rect", "Rectángulo"), ("ellipse", "Círculo"), ("star", "Estrella"),
                           ("triangle", "Triángulo")):
                cl.addItem(txt, k)
            cl.setCurrentIndex(max(0, cl.findData(el.get("clip", "rect"))))
            cl.currentIndexChanged.connect(lambda i: self.set_prop(el, "clip", cl.itemData(i)))
            self._row("Forma", cl)
            rd = self._spin(self._to_unit(el.get("radius", 0.0)), 0, 5000, 1, " mm" if self._mm() else " px")
            rd.valueChanged.connect(lambda v: self.set_prop(el, "radius", self._from_unit(v)))
            self._row("Esquinas", rd)
            lk = QComboBox()
            lk.addItems(list(E.COLOR_LOOKS))
            lk.setCurrentText(el.get("look") or "Natural")
            lk.currentTextChanged.connect(lambda t: self.set_prop(el, "look", "" if t == "Natural" else t))
            self._row("Filtro", lk)
            bw = QCheckBox("Blanco y negro")
            bw.setChecked(bool(el.get("bw")))
            bw.toggled.connect(lambda on: self.set_prop(el, "bw", on))
            self.props.addWidget(bw)
        elif t == "vector":
            paths = el.get("paths", [])
            if len(paths) == 1:
                self._gradient_row(el)
            for i, sp in enumerate(paths[:16]):
                cb = ColorButton(sp["fill"], True, self.design_colors)
                cb.changed.connect(lambda c, i=i: self._set_path(el, i, "fill", c))
                self._row("Relleno" if len(paths) == 1 else f"Color {i + 1}", cb)
            if len(paths) > 16:
                note = QLabel(f"… y {len(paths) - 16} colores más")
                note.setObjectName("faint")
                self.props.addWidget(note)
            if paths:
                sc = ColorButton(paths[0].get("stroke", "#000000"), False, self.design_colors)
                sc.changed.connect(lambda c: self._set_path(el, None, "stroke", c))
                self._row("Contorno", sc)
                vw = el["viewbox"][0]
                k = el["w"] / max(vw, 1e-6)
                sw = self._spin(self._to_unit(paths[0].get("stroke_w", 0.0) * k), 0, 2000, 2 if self._mm() else 1,
                                " mm" if self._mm() else " px", 0.5)
                sw.valueChanged.connect(lambda v: self._set_path(el, None, "stroke_w", self._from_unit(v) / k))
                self._row("Grosor", sw)
            big = not self.items[el["id"]].node_list() and paths
            hint = QLabel("Vector con demasiados nodos para editarlos uno a uno: recolorea, escala o combínalo con "
                          "el Buscatrazos." if big else
                          "Doble clic o herramienta ◇ (A): editar nodos. Doble clic en un nodo: esquina ↔ curva.")
            hint.setObjectName("faint")
            hint.setWordWrap(True)
            self.props.addWidget(hint)
        if el.get("credit"):
            cr = QLabel("Origen: " + el["credit"])
            cr.setObjectName("faint")
            cr.setWordWrap(True)
            cr.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.props.addWidget(cr)
        self._title("Apariencia")
        op = QSlider(Qt.Horizontal)
        op.setRange(0, 100)
        op.setValue(int(el.get("opacity", 1.0) * 100))
        op.valueChanged.connect(lambda v: self.set_prop(el, "opacity", v / 100))
        self._row("Opacidad", op)
        self._align_section()
        self._quick_section(el)
        self.props.addStretch(1)

    def _transform_section(self, el):
        self._title("Transformar")
        unit = " mm" if self._mm() else " px"
        g = QGridLayout()
        g.setHorizontalSpacing(6)
        for i, (k, lab) in enumerate((("x", "X:"), ("w", "An:"), ("y", "Y:"), ("h", "Al:"))):
            sb = self._spin(self._to_unit(el[k]), -100000, 100000, 2 if self._mm() else 1, unit)
            sb.valueChanged.connect(lambda v, k=k: self._set_geo(el, k, self._from_unit(v)))
            ll = QLabel(lab)
            ll.setObjectName("muted")
            g.addWidget(ll, i // 2, (i % 2) * 2)
            g.addWidget(sb, i // 2, (i % 2) * 2 + 1)
            self._geo[k] = sb
        rot = self._spin(el.get("rot", 0.0), -180, 180, 1, "°")
        rot.valueChanged.connect(lambda v: self._set_geo(el, "rot", v))
        self._geo["rot"] = rot
        rl = QLabel("⟲")
        rl.setObjectName("muted")
        g.addWidget(rl, 2, 0)
        g.addWidget(rot, 2, 1)
        self.props.addLayout(g)

    def _align_section(self):
        self._title("Alinear")
        r = QHBoxLayout()
        r.setSpacing(2)
        for k, tip in (("left", "Izquierda"), ("hcenter", "Centro horizontal"), ("right", "Derecha"),
                       ("top", "Arriba"), ("vcenter", "Centro vertical"), ("bottom", "Abajo")):
            b = QPushButton()
            b.setObjectName("vcIcon")
            b.setIcon(vc_icon("al_" + k, 20))
            b.setIconSize(QSize(20, 20))
            b.setToolTip(f"Alinear: {tip} (a la página con un elemento; entre sí con varios)")
            b.clicked.connect(lambda _=False, k=k: self.align(k))
            r.addWidget(b)
        more = QToolButton()
        more.setText("···")
        more.setPopupMode(QToolButton.InstantPopup)
        more.setStyleSheet("QToolButton { background: transparent; border: none; } "
                           "QToolButton::menu-indicator { image: none; }")
        m = QMenu(more)
        m.addAction("Repartir en horizontal").triggered.connect(lambda: self.distribute("h"))
        m.addAction("Repartir en vertical").triggered.connect(lambda: self.distribute("v"))
        more.setMenu(m)
        r.addWidget(more)
        r.addStretch(1)
        self.props.addLayout(r)

    def _quick_section(self, el):
        """Acciones rápidas según lo seleccionado, como en VectorCraft."""
        t = el["type"]
        acts = []
        if t == "text":
            acts += [("Crear contornos", self.outline_text)]
        if t == "image" and el.get("path"):
            acts += [("Quitar fondo", self.remove_bg), ("Vectorizar", self.vectorize)]
            if el.get("orig"):
                acts += [("Restaurar el fondo", lambda: self.restore_bg(el))]
        if t == "image":
            acts += [("Cambiar foto", lambda: self.change_photo(el))]
        acts += [("Duplicar", self.duplicate), ("Organizar: al frente", lambda: self.order("front")),
                 ("Desbloquear" if el.get("locked") else "Bloquear", self.toggle_lock)]
        self._title("Acciones rápidas")
        g = QGridLayout()
        g.setSpacing(6)
        for i, (txt, fn) in enumerate(acts):
            b = QPushButton(txt)
            b.setObjectName("vcQuick")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            g.addWidget(b, i // 2, i % 2)
        self.props.addLayout(g)

    def _gradient_row(self, el):
        g = el.get("grad") or {}
        r = QHBoxLayout()
        on = QCheckBox("Degradado")
        on.setChecked(bool(g))
        c1 = ColorButton(g.get("c1", "#F50D8A"), False, self.design_colors)
        c2 = ColorButton(g.get("c2", "#05070F"), False, self.design_colors)
        ang = QSpinBox()
        ang.setRange(0, 359)
        ang.setValue(int(g.get("angle", 90)))
        ang.setSuffix("°")

        def upd(*_):
            self.set_prop(el, "grad", {"c1": c1.value, "c2": c2.value, "angle": ang.value()}
                          if on.isChecked() else None)
        on.toggled.connect(upd)
        c1.changed.connect(upd)
        c2.changed.connect(upd)
        ang.valueChanged.connect(upd)
        r.addWidget(on)
        r.addWidget(ang)
        self.props.addLayout(r)
        r2 = QHBoxLayout()
        r2.addWidget(c1)
        r2.addWidget(c2)
        self.props.addLayout(r2)

    def _set_path(self, el, i, key, val):
        for j, sp in enumerate(el["paths"]):
            if i is None or j == i:
                sp[key] = val
        if key == "fill" and i is not None:
            el.pop("grad", None)
        self.items[el["id"]].update()
        self.commit(coalesce=f"{el['id']}:path{key}")

    def _set_geo(self, el, k, v):
        if k in ("w", "h") and el["type"] in ("image", "vector") and QApplication.keyboardModifiers() & \
                Qt.ShiftModifier:
            r = el["h"] / el["w"]
            if k == "w":
                el["h"] = v * r
            else:
                el["w"] = v / r
        el[k] = v
        if el["type"] == "text" and k == "w":
            G.fit_text(el)
        self.items[el["id"]].sync()
        self.commit(coalesce=f"{el['id']}:geo")

    def _page_props(self):
        kind = QLabel("Documento")
        kind.setObjectName("vcKind")
        self.props.addWidget(kind)
        self._title("Mesa de trabajo")
        info = QLabel(f"{self.d.get('name', '')}\n{G.size_label(self.d)}"
                      + ("  ·  300 ppp  ·  sangrado 3 mm (línea roja)\nlínea azul: margen de seguridad"
                         if self._mm() else ""))
        info.setObjectName("meta")
        info.setWordWrap(True)
        self.props.addWidget(info)
        cb = ColorButton(self.d.get("bg", "#FFFFFF"), False, self.design_colors)

        def setbg(c):
            self.d["bg"] = c
            self.canvas.resetCachedContent()
            self.canvas.viewport().update()
            self.commit(coalesce="bg")
        cb.changed.connect(setbg)
        self._row("Fondo", cb)
        rs = QComboBox()
        rs.addItem("Cambiar tamaño…", "")
        for g, n, ww, hh, u in G.PRESETS:
            rs.addItem(f"{n} ({ww}×{hh} {u})", n)
        rs.currentIndexChanged.connect(lambda i: rs.itemData(i) and self.resize_design(rs.itemData(i)))
        self.props.addWidget(rs)
        self._title("Consejos")
        tips = QLabel("• Plantillas, fotos, formas y tu marca: pestaña Bibliotecas.\n• Arrastra fotos desde Bibliotecas › Fotos.\n• Rueda: zoom · espacio + arrastrar: "
                      "desplazar.\n• Mayús al escalar: libera o fija la proporción.\n• Alt al mover una manecilla: "
                      "esquina.")
        tips.setObjectName("faint")
        tips.setWordWrap(True)
        self.props.addWidget(tips)
        self.props.addStretch(1)

    def resize_design(self, preset):
        """Cambia el tamaño escalando el contenido para que quepa (como «Cambiar tamaño» de Canva)."""
        nd = G.design_from_preset(preset)
        s = min(nd["w"] / self.d["w"], nd["h"] / self.d["h"])
        ox, oy = (nd["w"] - self.d["w"] * s) / 2, (nd["h"] - self.d["h"] * s) / 2
        G.sync_page(self.d)
        for e in [e for pg in self.d["pages"] for e in pg["elements"]]:
            e["x"], e["y"] = e["x"] * s + ox, e["y"] * s + oy
            e["w"], e["h"] = e["w"] * s, e["h"] * s
            for k in ("size", "stroke_w", "radius"):
                if k in e:
                    e[k] *= s
            if e["type"] == "text":
                e["h"] = G.text_height(e)
        self.d.update(w=nd["w"], h=nd["h"], unit=nd["unit"], dpi=nd["dpi"])
        self.size_lbl.setText(G.size_label(self.d))
        self.canvas.set_page()
        self.rebuild()
        self.commit()
        QTimer.singleShot(0, self.canvas.fit)

    def focus_text(self):
        if self.text_edit:
            self.text_edit.setFocus()
            self.text_edit.selectAll()

    # ---------------------------------------------------------- capas
    def refresh_layers(self):
        self._building = True
        self.layers.blockSignals(True)
        self.layers.clear()
        for el in reversed(self.d["elements"]):
            it = QListWidgetItem(("🔒 " if el.get("locked") else "") + ("▣ " if el.get("group") else "") +
                                 G.layer_name(el))
            it.setData(Qt.UserRole, el["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setCheckState(Qt.Unchecked if el.get("hidden") else Qt.Checked)
            it.setToolTip("Casilla: visible · arrastra para cambiar el orden")
            self.layers.addItem(it)
        self.layers.blockSignals(False)
        self._building = False
        self._sync_layer_sel()

    def _sync_layer_sel(self):
        ids = {i.el["id"] for i in self.canvas.sc.selectedItems()}
        self.layers.blockSignals(True)
        for r in range(self.layers.count()):
            it = self.layers.item(r)
            it.setSelected(it.data(Qt.UserRole) in ids)
        self.layers.blockSignals(False)

    def _layers_to_scene(self):
        ids = {it.data(Qt.UserRole) for it in self.layers.selectedItems()}
        self._building = True
        for k, it in self.items.items():
            it.setSelected(k in ids)
        self._building = False
        self.on_selection()

    def _layer_checked(self, it):
        el = G.find(self.d, it.data(Qt.UserRole))
        if el is not None:
            el["hidden"] = it.checkState() != Qt.Checked
            self.items[el["id"]].sync()
            self.commit()

    def _layers_moved(self, *_):
        ids = [self.layers.item(r).data(Qt.UserRole) for r in range(self.layers.count())]
        byid = {e["id"]: e for e in self.d["elements"]}
        self.d["elements"] = [byid[i] for i in reversed(ids) if i in byid]
        QTimer.singleShot(0, lambda: (self.rebuild(), self.commit()))

    # ---------------------------------------------------------- añadir
    def _add(self, el, select=True):
        self.d["elements"].append(el)
        self.rebuild(select=[el["id"]] if select else None)
        self.commit()
        self.canvas.setFocus()
        return el

    def add_text(self, role, at: QPointF | None = None):
        txt = {"title": "Añade un título", "subtitle": "Añade un subtítulo", "body": "Añade un poco de texto"}[role]
        el = G.text_el(self.d, txt, role, color="#FFFFFF" if self._dark_bg() else "#05070F")
        if at is not None:
            el["x"], el["y"] = at.x() - el["w"] / 2, at.y() - el["h"] / 2
        self._add(el)
        self.focus_text()

    def _dark_bg(self):
        c = G.qcolor(self.d.get("bg", "#FFFFFF"))
        return c.lightness() < 110

    def add_shape(self, kind):
        self._add(G.shape_el(self.d, kind, fill=self.pen_fill))

    def add_frame(self, clip):
        m = min(self.d["w"], self.d["h"]) * 0.45
        self._add(G.image_el(self.d, "", aspect=1.0, w=m, h=m, x=(self.d["w"] - m) / 2, y=(self.d["h"] - m) / 2,
                             clip=clip))

    def _aspect(self, path):
        w, h = self.canvas.cache.full_size(path)
        if w and h:
            return w / h
        return 1.5

    def place_photo(self, path, at: QPointF | None = None):
        if not path or not os.path.exists(path):
            return
        # soltar encima de un hueco o de otra foto: la sustituye (como Canva)
        target = None
        if at is not None:
            for it in self.canvas.sc.items(at):
                if isinstance(it, ElementItem) and it.el["type"] == "image" and not it.el.get("locked"):
                    target = it.el
                    break
        else:
            sel = self.selected()
            if len(sel) == 1 and sel[0]["type"] == "image" and not sel[0].get("path"):
                target = sel[0]
        if target is not None:
            target.update(path=path, zoom=1.0, fx=0.5, fy=0.5)
            target.pop("orig", None)
            self.items[target["id"]].update()
            self.canvas.sc.clearSelection()
            self.items[target["id"]].setSelected(True)
            self.commit()
            return target
        el = G.image_el(self.d, path, self._aspect(path))
        if at is not None:
            el["x"], el["y"] = at.x() - el["w"] / 2, at.y() - el["h"] / 2
        return self._add(el)

    def add_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Añadir imagen", self.folder,
                                              "Imágenes (*.jpg *.jpeg *.png *.tif *.tiff *.arw *.nef *.cr2 *.cr3 "
                                              "*.dng *.raf);;Diseño SVG (*.svg)")
        if path:
            self.place_photo(path)

    def change_photo(self, el):
        path, _ = QFileDialog.getOpenFileName(self, "Cambiar foto", self.folder,
                                              "Imágenes (*.jpg *.jpeg *.png *.tif *.tiff *.arw *.nef *.cr2 *.cr3 "
                                              "*.dng *.raf)")
        if path:
            el.update(path=path, zoom=1.0, fx=0.5, fy=0.5)
            el.pop("orig", None)
            self.items[el["id"]].update()
            self.commit()
            self.build_props()

    # ---------------------------------------------------------- edición
    def align(self, how):
        sel = [e for e in self.selected() if not e.get("locked")]
        G.align(self.d, sel, how)
        self._resync(sel)

    def distribute(self, axis):
        sel = [e for e in self.selected() if not e.get("locked")]
        G.distribute(sel, axis)
        self._resync(sel)

    def _resync(self, els):
        for e in els:
            self.items[e["id"]].sync()
        self.commit()
        self.live_geometry()

    def nudge(self, dx, dy):
        sel = [e for e in self.selected() if not e.get("locked")]
        if not sel:
            return
        k = 1.0 if not self._mm() else G.mm_to_px(0.5, self.d["dpi"])
        for e in sel:
            e["x"] += dx * k
            e["y"] += dy * k
        for e in sel:
            self.items[e["id"]].sync()
        self.commit(coalesce="nudge")
        self.live_geometry()

    def order(self, how):
        sel = self.selected()
        if not sel:
            return
        ids = {e["id"] for e in sel}
        els = self.d["elements"]
        if how == "front":
            els[:] = [e for e in els if e["id"] not in ids] + [e for e in els if e["id"] in ids]
        elif how == "back":
            els[:] = [e for e in els if e["id"] in ids] + [e for e in els if e["id"] not in ids]
        elif how == "up":
            for i in range(len(els) - 2, -1, -1):
                if els[i]["id"] in ids and els[i + 1]["id"] not in ids:
                    els[i], els[i + 1] = els[i + 1], els[i]
        else:
            for i in range(1, len(els)):
                if els[i]["id"] in ids and els[i - 1]["id"] not in ids:
                    els[i], els[i - 1] = els[i - 1], els[i]
        self.rebuild()
        self.commit()

    def duplicate(self):
        sel = self.selected()
        new = self._regroup([G.duplicate(self.d, e) for e in sel])
        self.d["elements"] += new
        self.rebuild(select=[e["id"] for e in new])
        self.commit()

    def delete(self):
        ids = {e["id"] for e in self.selected() if not e.get("locked")}
        if not ids:
            return
        self.d["elements"] = [e for e in self.d["elements"] if e["id"] not in ids]
        self.rebuild(select=[])
        self.commit()

    def copy(self):
        self.clip = copy.deepcopy(self.selected())

    def paste(self):
        if not self.clip:
            return
        new = self._regroup([G.duplicate(self.d, e) for e in self.clip])
        self.clip = copy.deepcopy(new)  # pegar otra vez: un poco más desplazado
        self.d["elements"] += new
        self.rebuild(select=[e["id"] for e in new])
        self.commit()

    def select_all(self):
        for it in self.items.values():
            if not it.el.get("hidden"):
                it.setSelected(True)

    @staticmethod
    def _regroup(els):
        """Las copias de un grupo forman su propio grupo."""
        ids = {}
        for e in els:
            if e.get("group"):
                e["group"] = ids.setdefault(e["group"], G.new_id())
        return els

    def group(self):
        sel = self.selected()
        if len(sel) < 2:
            return
        gid = G.new_id()
        for e in sel:
            e["group"] = gid
        self.commit()

    def ungroup(self):
        for e in self.selected():
            e.pop("group", None)
        self.commit()

    def add_qr(self):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Código QR", "Enlace o texto (por ejemplo, la galería del evento):",
                                        text="https://")
        if ok and text.strip():
            try:
                self._add(G.qr_el(self.d, text.strip()))
            except Exception as ex:
                QMessageBox.warning(self, "Código QR", str(ex))

    def toggle_lock(self):
        sel = self.selected()
        lock = not all(e.get("locked") for e in sel)
        for e in sel:
            e["locked"] = lock
            self.items[e["id"]].sync()
        self.commit()
        self.build_props()

    def pathfinder(self, op):
        sel = [e for e in self.selected() if e["type"] in ("shape", "vector", "text")]
        if len(sel) < 2:
            QMessageBox.information(self, "Buscatrazos", "Selecciona dos o más formas, vectores o textos.")
            return
        res = G.pathfinder(sel, op)
        if res is None:
            QMessageBox.information(self, "Buscatrazos", "El resultado está vacío (las formas no se tocan).")
            return
        ids = {e["id"] for e in sel}
        first = min(i for i, e in enumerate(self.d["elements"]) if e["id"] in ids)
        rest = [e for e in self.d["elements"] if e["id"] not in ids]
        rest.insert(min(first, len(rest)), res)
        self.d["elements"] = rest
        self.rebuild(select=[res["id"]])
        self.commit()

    def outline_text(self):
        sel = [e for e in self.selected() if e["type"] == "text"]
        if not sel:
            return
        out = []
        for e in sel:
            v = G.outline_text_el(e)
            i = self.d["elements"].index(e)
            self.d["elements"][i] = v
            out.append(v["id"])
        self.rebuild(select=out)
        self.commit()

    # ---------------------------------------------------------- fotos: fondo y vectorizado
    def _sel_image(self):
        sel = self.selected()
        if len(sel) == 1 and sel[0]["type"] == "image" and sel[0].get("path"):
            return sel[0]
        return None

    def remove_bg(self):
        el = self._sel_image()
        if el is None:
            return
        import masks as M
        M.configure(os.path.join(self.cfg_dir, "modelos"))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        rgb = G.load_rgb(el["path"], 2400)
        QApplication.restoreOverrideCursor()
        if rgb is None:
            QMessageBox.warning(self, "Quitar fondo", "No se pudo abrir la foto.")
            return
        dlg = BackgroundDialog(self, rgb)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            out, used = dlg.final()
            src = el.get("orig") or el["path"]
            path = G.save_cutout(out, G.cutout_path(self.cfg_dir, src, used))
        except Exception as ex:
            QMessageBox.warning(self, "Quitar fondo", f"No se pudo: {ex}")
            return
        el["orig"] = src
        el["path"] = path
        self.items[el["id"]].update()
        self.commit()
        self.build_props()

    def restore_bg(self, el):
        if el.get("orig"):
            el["path"] = el.pop("orig")
            self.items[el["id"]].update()
            self.commit()
            self.build_props()

    def vectorize(self):
        el = self._sel_image()
        if el is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        rgb = G.load_rgb(el["path"], 2000)
        QApplication.restoreOverrideCursor()
        if rgb is None:
            QMessageBox.warning(self, "Vectorizar", "No se pudo abrir la foto.")
            return
        rgb = G.crop_for(el, rgb)
        dlg = VectorizeDialog(self, rgb)
        if dlg.exec() != QDialog.Accepted or not dlg.vec:
            return
        v = G.vector_el(dlg.vec, el["x"], el["y"], el["w"], el["h"], rot=el.get("rot", 0.0),
                        opacity=el.get("opacity", 1.0))
        i = self.d["elements"].index(el)
        if dlg.choice == "replace":
            self.d["elements"][i] = v
        else:
            self.d["elements"].insert(i + 1, v)
        self.rebuild(select=[v["id"]])
        self.commit()

    # ---------------------------------------------------------- exportar
    def export(self):
        dlg = ExportDialog(self, self.d, self.canvas.cache)
        if dlg.exec() != QDialog.Accepted:
            return
        i = dlg.fmt.currentIndex()
        ext = [".pdf", ".png", ".jpg", ".svg"][i]
        base = os.path.join(self.folder, "Diseños") if self.folder else os.path.expanduser("~\\Documents")
        os.makedirs(base, exist_ok=True)
        safe = "".join(c for c in self.d.get("name", "diseño") if c not in '\\/:*?"<>|').strip() or "diseño"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar", os.path.join(base, safe + ext),
                                              f"{ext[1:].upper()} (*{ext})")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            views = G.page_views(self.d)
            warn = [w_ for v in views for w_ in G.image_warnings(v, self.canvas.cache)] if self._mm() else []
            files = [path]
            if i == 0:
                d = G.ensure_pages(copy.deepcopy(self.d))
                if dlg.outline.isChecked():
                    for pg in d["pages"]:
                        pg["elements"] = [G.outline_text_el(e) if e["type"] == "text" else e
                                          for e in pg["elements"]]
                    G.ensure_pages(d)
                G.export_pdf(d, path, self.canvas.cache, dlg.bleed.value(), dlg.marks.isChecked(),
                             dlg.cmyk.isChecked())
            else:
                # varias páginas: un archivo por página (nombre-1, nombre-2…)
                root, ext_ = os.path.splitext(path)
                files = [path] if len(views) == 1 else [f"{root}-{k + 1}{ext_}" for k in range(len(views))]
                for v, f in zip(views, files):
                    if i in (1, 2):
                        G.export_raster(v, f, self.canvas.cache, float(dlg.scale.currentIndex() + 1),
                                        dlg.quality.value())
                    else:
                        G.export_svg(v, f, self.canvas.cache)
        except Exception as ex:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Exportar", f"No se pudo exportar: {ex}")
            return
        QApplication.restoreOverrideCursor()
        msg = "Exportado:\n" + "\n".join(files)
        if warn:
            msg += "\n\nFotos con poca resolución para imprimir a este tamaño (menos de 200 ppp):\n• " + \
                   "\n• ".join(warn)
        QMessageBox.information(self, "Exportar", msg)

    # ---------------------------------------------------------- páginas
    def refresh_pages(self):
        if not hasattr(self, "pages"):
            return
        self.pages.blockSignals(True)
        views = G.page_views(self.d)
        self.pages.clear()
        for k, v in enumerate(views):
            self.pages.addItem(QIcon(QPixmap.fromImage(G.thumbnail(v, self.canvas.cache, 56))),
                               f"{k + 1}   Página {k + 1}")
        self.pages.setCurrentIndex(self.d.get("page", 0))
        self.pages.blockSignals(False)

    def goto_page(self, i):
        if i < 0 or i == self.d.get("page", 0) or i >= len(self.d["pages"]):
            return
        G.set_page(self.d, i)
        self.rebuild(select=[])
        self.canvas.resetCachedContent()
        self.canvas.viewport().update()
        self.pages.blockSignals(True)
        self.pages.setCurrentIndex(i)
        self.pages.blockSignals(False)

    def page_op(self, op):
        if op == "add":
            G.add_page(self.d)
        elif op == "dup":
            G.add_page(self.d, duplicate_current=True)
        elif op == "left":
            G.move_page(self.d, -1)
        elif op == "right":
            G.move_page(self.d, 1)
        elif op == "del":
            if len(self.d["pages"]) < 2 or QMessageBox.question(
                    self, "Eliminar página", f"¿Eliminar la página {self.d['page'] + 1}? Se puede deshacer.") \
                    != QMessageBox.Yes:
                return
            G.delete_page(self.d)
        self.rebuild(select=[])
        self.canvas.viewport().update()
        self.commit()
        self.refresh_pages()

    # ---------------------------------------------------------- kit de marca
    def _build_brand(self):
        self.brand = G.load_brand(self.cfg_dir)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 4)
        t = QLabel("COLORES")
        t.setObjectName("cardTitle")
        v.addWidget(t)
        self.brand_cols = QListWidget()
        self.brand_cols.setViewMode(QListWidget.IconMode)
        self.brand_cols.setIconSize(QSize(30, 30))
        self.brand_cols.setGridSize(QSize(38, 38))
        self.brand_cols.setMovement(QListWidget.Static)
        self.brand_cols.setFixedHeight(90)
        self.brand_cols.setToolTip("Aparecen los primeros en todos los selectores de color · doble clic: aplicar al "
                                   "elemento seleccionado")
        self.brand_cols.itemDoubleClicked.connect(lambda it: self.apply_color(it.data(Qt.UserRole)))
        v.addWidget(self.brand_cols)
        r = QHBoxLayout()
        r.addWidget(_btn("Añadir color…", "small", "", self._brand_add_color))
        r.addWidget(_btn("Quitar", "small", "", self._brand_del_color))
        v.addLayout(r)
        t = QLabel("FUENTES")
        t.setObjectName("cardTitle")
        v.addWidget(t)
        self.brand_title = QFontComboBox()
        self.brand_body = QFontComboBox()
        for cb, key, lab in ((self.brand_title, "title_font", "Títulos"), (self.brand_body, "body_font", "Texto")):
            if self.brand.get(key):
                cb.setCurrentText(self.brand[key])
            cb.currentFontChanged.connect(lambda f, key=key: self._brand_set(key, f.family()))
            r = QHBoxLayout()
            lb = QLabel(lab)
            lb.setFixedWidth(50)
            lb.setObjectName("muted")
            r.addWidget(lb)
            r.addWidget(cb, 1)
            v.addLayout(r)
        v.addWidget(_btn("Aplicar las fuentes a este diseño", "", "Títulos (textos grandes) y texto (el resto), en "
                                                                  "todas las páginas", self.apply_brand))
        t = QLabel("LOGOS")
        t.setObjectName("cardTitle")
        v.addWidget(t)
        self.brand_logos = PhotoList()
        self.brand_logos.setViewMode(QListWidget.IconMode)
        self.brand_logos.setIconSize(QSize(100, 70))
        self.brand_logos.setGridSize(QSize(112, 84))
        self.brand_logos.setResizeMode(QListWidget.Adjust)
        self.brand_logos.setMovement(QListWidget.Static)
        self.brand_logos.setDragEnabled(True)
        self.brand_logos.setToolTip("Arrastra al lienzo o doble clic para añadir")
        self.brand_logos.itemDoubleClicked.connect(lambda it: self.place_photo(it.data(Qt.UserRole)))
        v.addWidget(self.brand_logos, 1)
        r = QHBoxLayout()
        r.addWidget(_btn("Añadir logo…", "small", "PNG con transparencia, JPG o el recorte de «Quitar fondo»",
                         self._brand_add_logo))
        r.addWidget(_btn("Quitar", "small", "", self._brand_del_logo))
        v.addLayout(r)
        self._brand_refresh()
        return w

    def _brand_refresh(self):
        self.brand_cols.clear()
        for c in self.brand["colors"]:
            it = QListWidgetItem(_swatch_icon(c, 30), "")
            it.setData(Qt.UserRole, c)
            it.setToolTip(c)
            self.brand_cols.addItem(it)
        self.brand_logos.clear()
        for path in self.brand["logos"]:
            it = QListWidgetItem(QIcon(path), "")
            it.setData(Qt.UserRole, path)
            it.setToolTip(os.path.basename(path))
            self.brand_logos.addItem(it)

    def _brand_save(self):
        try:
            G.save_brand(self.cfg_dir, self.brand)
        except OSError as ex:
            QMessageBox.warning(self, "Marca", f"No se pudo guardar: {ex}")
        self._brand_refresh()

    def _brand_set(self, key, val):
        self.brand[key] = val
        self._brand_save()

    def _brand_add_color(self):
        c = QColorDialog.getColor(QColor(T.ACCENT), self, "Color de la marca")
        if c.isValid():
            col = c.name(QColor.HexRgb).upper()
            if col not in self.brand["colors"]:
                self.brand["colors"].append(col)
                self._brand_save()

    def _brand_del_color(self):
        it = self.brand_cols.currentItem()
        if it:
            self.brand["colors"].remove(it.data(Qt.UserRole))
            self._brand_save()

    def _brand_add_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Logo", self.folder, "Imágenes (*.png *.jpg *.jpeg *.svg *.tif)")
        if path and path not in self.brand["logos"]:
            self.brand["logos"].append(path)
            self._brand_save()

    def _brand_del_logo(self):
        it = self.brand_logos.currentItem()
        if it:
            self.brand["logos"].remove(it.data(Qt.UserRole))
            self._brand_save()

    def apply_brand(self):
        if not (self.brand.get("title_font") or self.brand.get("body_font")):
            QMessageBox.information(self, "Marca", "Elige primero las fuentes de la marca.")
            return
        n = G.apply_brand(self.d, self.brand)
        G.ensure_pages(self.d)
        self.rebuild()
        self.commit()
        self.saved_lbl.setText(f"Fuentes de marca en {n} textos")

    def apply_color(self, col):
        """Doble clic en un color de la marca: al elemento seleccionado (texto, forma o vector) o al fondo."""
        sel = self.selected()
        if not sel:
            self.d["bg"] = col
            self.canvas.resetCachedContent()
            self.canvas.viewport().update()
            self.commit()
            return
        for e in sel:
            if e["type"] == "text":
                e["color"] = col
            elif e["type"] == "shape":
                e.pop("grad", None)
                e["fill" if e.get("shape") != "line" else "stroke"] = col
            elif e["type"] == "vector":
                for sp in e["paths"][-1:]:
                    sp["fill"] = col
            self.items[e["id"]].update()
        self.commit()
        self.build_props()

    def shutdown(self):
        if hasattr(self, "elements"):
            self.elements.shutdown()
        self._closing = True  # para el hilo de miniaturas antes de que se destruya la vista
        self.save_timer.stop()
        self.save_now()
