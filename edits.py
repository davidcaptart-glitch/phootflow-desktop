"""PhootFlow Edits: retoque foto a foto tipo Photoshop.

Barra de herramientas a la izquierda (mover, corrector, tampón, relleno, piel, ojos y luz, licuar), lienzo con
zoom y antes/después, y a la derecha los paneles de Capas, Propiedades, Historial y Exportar. Todo es no
destructivo: el trabajo se guarda en `<carpeta>/.phootflow/edits/<foto>.json` (ver `edits_engine`) y se
exporta a «<carpeta>/Edits» en JPG, TIFF de 16 bits o PNG.
"""
from __future__ import annotations

import copy
import json
import os
import queue
import threading
import uuid

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListView, QListWidget,
                               QListWidgetItem, QMenu, QMenuBar, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QSplitter, QToolButton, QVBoxLayout, QWidget)

import compare as CMP
import zoomview as Z
import edits_adjust as A
import edits_engine as EE
import edits_ui as UI
import engine as E
import studio_engine as SE
import theme as T
from studio import ROLE, FilmDelegate, Item, SliderRow, _btn, _pixmap, quick_thumb

PROXY_PX = 2048
# clave, icono, consejo, atajo, tipo de capa sobre la que trabaja
TOOLS = [("move", "✥", "Mover: arrastra el texto, el logo o la imagen elegida; si no, desplaza la vista", "V", None),
         ("marquee", "▭", "Marco rectangular o elíptico: arrastra para seleccionar (Mayús añade, Alt resta)", "M",
          None),
         ("lasso", "ʘ", "Lazo: rodea a mano alzada la zona a seleccionar", "L", None),
         ("subject", "✦", "Selección de sujeto: clic para seleccionar a las personas (o el objeto principal)", "W",
          None),
         ("eyedrop", "⌖", "Cuentagotas: toma el color frontal de la foto", "I", None),
         ("heal", "✚", "Pincel corrector puntual: pinta encima de granos, brillos o manchas (busca solo el parche)",
          "J", "retouch"),
         ("skin", "◐", "Pincel de máscara: pinta la máscara de la capa elegida, la Piel o la persona del Fondo (clic "
                       "derecho o Alt: quita)", "B", "skin"),
         ("clone", "◎", "Tampón de clonar: Alt+clic para elegir el origen y pinta donde quieras copiarlo", "S",
          "retouch"),
         ("fill", "▨", "Relleno según contenido con IA: pinta lo que quieras borrar (personas, objetos, cables, "
                       "carteles)", "G", "retouch"),
         ("light", "☀", "Sobreexponer / subexponer: aclarar ojeras, blanquear dientes y ojos, oscurecer", "O", "light"),
         ("liquify", "〰", "Licuar: empujar, inflar o encoger", "Shift+Ctrl+X", "liquify"),
         ("type", "T", "Texto horizontal: clic en la foto para escribir", "T", None),
         ("hand", "✋", "Mano: arrastra para desplazar la vista ampliada", "H", None),
         ("zoom", "⌕", "Zoom: clic acerca, Alt+clic aleja", "", None)]
# disposición de la barra en dos columnas (como PhotoCraft / Photoshop)
TOOL_GRID = [("move", None), ("marquee", "lasso"), ("subject", "eyedrop"), ("heal", "skin"), ("clone", "fill"),
             ("light", "liquify"), ("type", None), ("hand", "zoom")]
SELECT_TOOLS = ("marquee", "lasso", "subject")
MOVABLE = ("text", "logo", "image")  # capas que se arrastran con Mover y tienen caja en la vista
HANDLE_PX = 9  # radio de agarre de las esquinas de la transformación libre (píxeles de pantalla)
TOOL_LAYER = {k: lay for k, _, _, _, lay in TOOLS}
LIGHT_MODES = [("dodge", "Aclarar (ojeras)"), ("whiten", "Blanquear (dientes, ojos)"), ("burn", "Oscurecer")]
LIQ_MODES = [("push", "Empujar"), ("bloat", "Inflar"), ("pucker", "Encoger")]
BG_MODES = [("blur", "Desenfocar"), ("bw", "Blanco y negro"), ("color", "Color liso"),
            ("transparent", "Transparente (PNG)")]
POSITIONS = [("rb", "Abajo derecha", 0.97, 0.965), ("mb", "Abajo centro", 0.5, 0.965),
             ("lb", "Abajo izquierda", 0.03, 0.965), ("rt", "Arriba derecha", 0.97, 0.035),
             ("lt", "Arriba izquierda", 0.03, 0.035), ("mm", "Centro", 0.5, 0.5)]


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def tool_icon(key: str):
    """Iconos de línea de la barra de herramientas (gris normal, blanco la activa)."""
    from PySide6.QtGui import QFont, QIcon, QPainterPath, QPixmap

    def draw(color):
        pm = QPixmap(44, 44)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(44 / 24, 44 / 24)
        pen = QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if key == "move":
            p.drawLine(QPointF(12, 3), QPointF(12, 21))
            p.drawLine(QPointF(3, 12), QPointF(21, 12))
            for a, b, c in (((9, 6), (12, 3), (15, 6)), ((9, 18), (12, 21), (15, 18)),
                            ((6, 9), (3, 12), (6, 15)), ((18, 9), (21, 12), (18, 15))):
                p.drawPolyline([QPointF(*a), QPointF(*b), QPointF(*c)])
        elif key == "heal":  # tirita
            p.translate(12, 12)
            p.rotate(-45)
            p.drawRoundedRect(QRectF(-10, -4, 20, 8), 4, 4)
            p.drawRect(QRectF(-3.5, -4, 7, 8))
            p.setBrush(QColor(color))
            for dx, dy in ((-1.5, -1.5), (1.5, -1.5), (-1.5, 1.5), (1.5, 1.5)):
                p.drawEllipse(QPointF(dx, dy), 0.5, 0.5)
        elif key == "clone":  # tampón
            p.drawEllipse(QPointF(12, 6), 3.2, 3.2)
            p.drawLine(QPointF(12, 9.2), QPointF(12, 13))
            path = QPainterPath()
            path.moveTo(6, 17)
            path.lineTo(7.5, 13)
            path.lineTo(16.5, 13)
            path.lineTo(18, 17)
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(QPointF(5, 20.5), QPointF(19, 20.5))
        elif key == "fill":  # selección + destello (relleno según contenido)
            pen.setDashPattern([2, 2])
            p.setPen(pen)
            p.drawRoundedRect(QRectF(3, 5, 15, 13), 2, 2)
            p.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap))
            for a, b in (((18, 2), (18, 8)), ((15, 5), (21, 5)), ((16, 3), (20, 7)), ((20, 3), (16, 7))):
                p.drawLine(QPointF(*a), QPointF(*b))
        elif key == "skin":  # pincel de máscara
            p.drawLine(QPointF(20, 4), QPointF(11, 13))
            blob = QPainterPath()
            blob.moveTo(11.5, 12.5)
            blob.cubicTo(8, 11, 5, 14, 5.5, 17)
            blob.cubicTo(5.8, 19, 4.5, 20, 3.5, 20.5)
            blob.cubicTo(8, 21.5, 13, 19, 11.5, 12.5)
            p.setBrush(QColor(color))
            p.drawPath(blob)
        elif key == "light":  # ojo con brillo
            eye = QPainterPath()
            eye.moveTo(2.5, 12)
            eye.quadTo(12, 3, 21.5, 12)
            eye.quadTo(12, 21, 2.5, 12)
            p.drawPath(eye)
            p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(13.2, 10.8), 0.9, 0.9)
        elif key == "marquee":  # rectángulo discontinuo
            pen.setDashPattern([2, 1.5])
            p.setPen(pen)
            p.drawRect(QRectF(4, 6, 16, 12))
        elif key == "lasso":
            path = QPainterPath()
            path.moveTo(7, 17)
            path.cubicTo(1, 12, 6, 4, 13, 5)
            path.cubicTo(21, 6, 22, 14, 14, 16)
            path.cubicTo(10, 17, 8, 15, 9, 13)
            p.drawPath(path)
            p.drawLine(QPointF(7, 17), QPointF(6, 21))
        elif key == "subject":  # silueta con destello
            p.drawEllipse(QPointF(10, 8), 3, 3)
            path = QPainterPath()
            path.moveTo(4, 21)
            path.cubicTo(4, 14, 16, 14, 16, 21)
            p.drawPath(path)
            for a, b in (((19, 3), (19, 9)), ((16, 6), (22, 6))):
                p.drawLine(QPointF(*a), QPointF(*b))
        elif key == "eyedrop":
            p.drawLine(QPointF(5, 19), QPointF(14, 10))
            p.drawLine(QPointF(4, 20), QPointF(5, 19))
            p.drawRoundedRect(QRectF(13, 4, 6, 9), 2, 2)
        elif key == "type":
            f = QFont("Georgia")
            f.setPixelSize(17)
            p.setFont(f)
            p.drawText(QRectF(0, 0, 24, 24), Qt.AlignCenter, "T")
        elif key == "hand":
            path = QPainterPath()
            path.moveTo(7, 21)
            path.lineTo(5, 13)
            path.lineTo(7, 12)
            path.lineTo(9, 15)
            path.lineTo(9, 5)
            path.lineTo(11, 5)
            path.lineTo(11, 12)
            path.lineTo(11, 4)
            path.lineTo(13, 4)
            path.lineTo(13, 12)
            path.lineTo(13, 5)
            path.lineTo(15, 5)
            path.lineTo(15, 13)
            path.lineTo(15, 7)
            path.lineTo(17, 7)
            path.lineTo(17, 16)
            path.lineTo(15, 21)
            path.closeSubpath()
            p.drawPath(path)
        elif key == "zoom":
            p.drawEllipse(QPointF(10, 10), 6, 6)
            p.drawLine(QPointF(14.5, 14.5), QPointF(20, 20))
        elif key == "liquify":  # ondas
            for y in (8, 12.5, 17):
                path = QPainterPath()
                path.moveTo(3, y)
                path.cubicTo(7, y - 4, 9, y + 4, 12, y)
                path.cubicTo(15, y - 4, 17, y + 4, 21, y)
                p.drawPath(path)
        p.end()
        return pm

    ic = QIcon()
    ic.addPixmap(draw(T.MUTED), QIcon.Normal, QIcon.Off)
    ic.addPixmap(draw(T.TEXT_STRONG), QIcon.Normal, QIcon.On)
    ic.addPixmap(draw(T.TEXT_STRONG), QIcon.Active, QIcon.Off)
    return ic


def _checker(img: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Fondo de cuadros para ver la transparencia (como Photoshop)."""
    h, w = alpha.shape
    yy, xx = np.mgrid[0:h, 0:w]
    s = max(8, max(h, w) // 96)
    chk = np.where(((yy // s) + (xx // s)) % 2 == 0, 0.42, 0.30).astype(np.float32)[..., None]
    a = alpha[..., None]
    return img * a + chk * (1 - a)


# ------------------------------------------------------------------ lienzo con zoom

class EditCanvas(QWidget):
    """Foto con zoom (rueda), mano (botón central, barra espaciadora o herramienta Mover), cursor de pincel,
    trazo en curso y antes/después."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.pm = self.before = None
        self.cmp = CMP.CompareState()
        self.msg = "Abre una carpeta y elige una foto"
        self.zoom = Z.Zoom(self)      # zoom común a todos los visores (zoomview)
        self.img_rect = QRectF()
        self.brush_px = 0.0           # radio del pincel en píxeles de la vista (0 = sin pincel)
        self.cursor = None            # QPointF del ratón
        self.stroke: list = []        # trazo en curso, en 0..1 de la foto mostrada
        self.marker = None            # origen del tampón (0..1)
        self.box = None               # caja de la capa de texto/logo seleccionada (0..1)
        self.quad = None              # transformación libre: 4 esquinas (0..1) con asas
        self.on_enter = None          # Intro sobre el lienzo (terminar la transformación)
        self.handler = None           # callback(tipo, x, y, evento) -> ¿consumido?
        self.space = False
        self.zoom_changed = None
        self.sel_paths: list = []     # contornos de la selección (0..1 de la foto mostrada)
        self.shape = None             # forma en curso: ("rect"|"ellipse", x0, y0, x1, y1) o ("lasso", [puntos])
        self._ants = 0
        self._ant_timer = QTimer(self)
        self._ant_timer.timeout.connect(self._march)
        self._ant_timer.start(140)

    def _march(self):
        if self.sel_paths and self.isVisible():
            self._ants = (self._ants + 1) % 8
            self.update()

    # ---- geometría (zoomview: 100 % = un píxel de la vista de trabajo de 2048 px)
    def zoom_pixmap(self):
        return self.pm

    def _area(self) -> QRectF:
        return QRectF(self.rect()).adjusted(12, 12, -12, -12)

    def _rect(self) -> QRectF:
        return self.zoom.rect(self._area(), self.pm.size()) if self.pm is not None else QRectF()

    def norm(self, pt) -> tuple[float, float]:
        r = self.img_rect
        if r.width() <= 0:
            return 0.0, 0.0
        return (pt.x() - r.x()) / r.width(), (pt.y() - r.y()) / r.height()

    def _zoomed(self):
        self.img_rect = self._rect()
        if self.zoom_changed:
            self.zoom_changed()
        self.update()

    def set_zoom(self, pct, anchor=None):
        """None = encajar; 1.0 = 100 %."""
        if self.pm is not None:
            self.zoom._set_level(pct, self._area(), self.pm.size(), anchor)
            self._zoomed()

    def toggle_zoom(self, anchor=None):
        if self.pm is not None:
            self.zoom.toggle(anchor, self._area(), self.pm.size())
            self._zoomed()

    def zoom_label(self) -> str:
        return self.zoom.label(self._area(), self.pm.size()) if self.pm is not None else "Encajar"

    # ---- ratón y teclado
    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        if steps and e.modifiers() & Qt.ControlModifier and self.handler and self.handler("wheel", steps, 0, e):
            return
        if steps and self.pm is not None:
            self.zoom.wheel(e.angleDelta().y(), e.position(), self._area(), self.pm.size())
            self._zoomed()

    def mouseDoubleClickEvent(self, e):
        if self.handler and self.handler("dbl", *self.norm(e.position()), e):
            return
        self.toggle_zoom(e.position())

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.on_enter:
            self.on_enter()
            return
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self.space = True
            self.setCursor(Qt.OpenHandCursor)
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self.space = False
            self.unsetCursor()
            return
        super().keyReleaseEvent(e)

    def _near_line(self, pt) -> bool:
        st, r = self.cmp, self.img_rect
        if not st.split or r.width() <= 0:
            return False
        if st.mode == "v":
            return abs(pt.x() - (r.x() + r.width() * st.pos)) < 14
        return abs(pt.y() - (r.y() + r.height() * st.pos)) < 14

    def _start_pan(self, pt):
        self.zoom.start_pan(pt)
        self.setCursor(Qt.ClosedHandCursor)

    def mousePressEvent(self, e):
        self.setFocus()
        pt = e.position()
        if e.button() == Qt.MiddleButton or (self.space and e.button() == Qt.LeftButton):
            return self._start_pan(pt)
        if e.button() == Qt.LeftButton and self._near_line(pt):
            self.cmp.dragging = True
            CMP.drag(self.cmp, self.img_rect, pt.x(), pt.y())
            return self.update()
        if self.handler and self.handler("press", *self.norm(pt), e):
            return
        if e.button() == Qt.LeftButton and self.zoom.active:
            self._start_pan(pt)  # herramienta Mover sin capa que mover: mano

    def mouseMoveEvent(self, e):
        pt = e.position()
        self.cursor = pt
        if self.zoom.panning and self.pm is not None:
            self.zoom.pan(pt, self._area(), self.pm.size())
            self.img_rect = self._rect()
            return self.update()
        if self.cmp.dragging:
            CMP.drag(self.cmp, self.img_rect, pt.x(), pt.y())
            return self.update()
        if self.handler and e.buttons():
            self.handler("move", *self.norm(pt), e)
        elif self.handler:
            self.handler("hover", *self.norm(pt), e)
        self.update()

    def mouseReleaseEvent(self, e):
        if self.zoom.panning:
            self.zoom.end_pan()
            self.setCursor(Qt.OpenHandCursor if self.space else Qt.ArrowCursor)
            if not self.space and self.handler:
                self.handler("cursor", 0, 0, e)  # que la vista vuelva a poner el cursor de su herramienta
            return
        if self.cmp.dragging:
            self.cmp.dragging = False
            return
        if self.handler:
            self.handler("release", *self.norm(e.position()), e)

    def leaveEvent(self, e):
        self.cursor = None
        self.update()

    def resizeEvent(self, e):
        self.img_rect = self._rect()
        super().resizeEvent(e)

    # ---- dibujo
    def set(self, pm=None, before=None, msg=""):
        new_size = pm is not None and (self.pm is None or pm.size() != self.pm.size())
        if pm is not None:
            self.pm = pm
        if before is not None:
            self.before = before
        if new_size:  # otra foto (u otro recorte): vuelve a encajar
            self.zoom.reset(key=(pm.width(), pm.height()), full_size=(pm.width(), pm.height()))
            self.zoom.pct, self.zoom.cx, self.zoom.cy = None, 0.5, 0.5
            if self.zoom_changed:
                self.zoom_changed()
        self.msg = msg
        self.img_rect = self._rect()
        self.update()

    def clear(self, msg=""):
        self.pm = self.before = None
        self.msg = msg
        self.update()

    def to_view(self, x, y) -> QPointF:
        r = self.img_rect
        return QPointF(r.x() + x * r.width(), r.y() + y * r.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BG))
        if self.pm is None:
            p.setPen(QColor(T.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self.msg)
            return
        area = self._area()
        self.img_rect = r = self._rect()
        p.setClipRect(area)
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.zoom.pct is None or self.zoom.pct < 1.5)
        CMP.paint(p, r, self.pm, self.before, self.cmp)
        p.setRenderHint(QPainter.Antialiasing)
        sc = r.width() / max(1, self.pm.width())
        if self.stroke:
            col = QColor(T.ACCENT)
            col.setAlpha(80)
            pen = QPen(col, max(2.0, 2 * self.brush_px * sc), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            pts = [self.to_view(x, y) for x, y in self.stroke]
            if len(pts) == 1:
                p.drawPoint(pts[0])
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
        if self.sel_paths or self.shape:
            from PySide6.QtGui import QPainterPath
            path = QPainterPath()
            for poly in self.sel_paths:
                path.moveTo(self.to_view(*poly[0]))
                for q in poly[1:]:
                    path.lineTo(self.to_view(*q))
                path.closeSubpath()
            if self.shape:
                if self.shape[0] == "lasso":
                    pts = self.shape[1]
                    if pts:
                        path.moveTo(self.to_view(*pts[0]))
                        for q in pts[1:]:
                            path.lineTo(self.to_view(*q))
                else:
                    rr = QRectF(self.to_view(*self.shape[1:3]), self.to_view(*self.shape[3:5])).normalized()
                    if self.shape[0] == "rect":
                        path.addRect(rr)
                    else:
                        path.addEllipse(rr)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255), 1))
            p.drawPath(path)
            ants = QPen(QColor(0, 0, 0), 1, Qt.CustomDashLine)
            ants.setDashPattern([4, 4])
            ants.setDashOffset(self._ants)
            p.setPen(ants)
            p.drawPath(path)
        if self.quad:
            from PySide6.QtGui import QPolygonF
            pts = [self.to_view(*q) for q in self.quad]
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 150), 3))
            p.drawPolygon(QPolygonF(pts))
            p.setPen(QPen(QColor(T.ACCENT), 1.4))
            p.drawPolygon(QPolygonF(pts))
            for q in pts:
                p.setPen(QPen(QColor(0, 0, 0, 200), 1))
                p.setBrush(QColor(255, 255, 255))
                p.drawRect(QRectF(q.x() - 5, q.y() - 5, 10, 10))
        elif self.box:
            x0, y0, x1, y1 = self.box
            p.setPen(QPen(QColor(T.ACCENT), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(self.to_view(x0, y0), self.to_view(x1, y1)))
        if self.marker:
            c = self.to_view(*self.marker)
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
            p.drawEllipse(c, 7, 7)
            p.drawLine(QPointF(c.x() - 11, c.y()), QPointF(c.x() + 11, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - 11), QPointF(c.x(), c.y() + 11))
        if self.cursor is not None and self.brush_px > 0 and r.contains(self.cursor):
            rad = self.brush_px * sc
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 160), 3))
            p.drawEllipse(self.cursor, rad, rad)
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.2))
            p.drawEllipse(self.cursor, rad, rad)
        p.setClipping(False)
        self.zoom.draw_badge(p, area, self.pm.size())
        if self.msg:
            p.setPen(QColor(T.MUTED))
            p.drawText(self.rect().adjusted(0, 0, -16, -8), Qt.AlignRight | Qt.AlignBottom, self.msg)


class _Spin(QSpinBox):
    """QSpinBox con set_value sin emitir (misma interfaz que SliderRow para la opacidad)."""

    def set_value(self, v, emit=False):
        self.blockSignals(not emit)
        self.setValue(int(round(float(v))))
        self.blockSignals(False)


# ------------------------------------------------------------------ vista Edits

class EditsView(QWidget):
    rendered = Signal(int, object, object, object)  # nº de petición, imagen, fuentes resueltas, cajas
    loaded = Signal(str, object, object)             # ruta, base (sin recortar), ajustes de revelado
    thumbs_ready = Signal(int)
    export_progress = Signal(int, int, str)
    lama_done = Signal(str)                          # "" si el modelo de borrado ya está; si no, el error

    def __init__(self, cfg_dir: str, events_lookup=None):
        super().__init__()
        self.cfg_dir = cfg_dir
        self.events_lookup = events_lookup or (lambda path: None)
        self.folder = ""
        self.items: list[Item] = []
        self.cur: Item | None = None
        self.doc: dict | None = None
        self.base = None            # revelado de partida sin recortar (proxy)
        self.base_s: dict = {}
        self.box = [0.0, 0.0, 1.0, 1.0]
        self.layer_id = None
        self.tool = "heal"
        self.radius = {"heal": 14, "clone": 22, "fill": 30, "skin": 40, "light": 16, "liquify": 90}
        self.hardness = {"clone": 50}
        self.strength = {"light": 45, "liquify": 50}
        self.mode = {"light": "dodge", "liquify": "push"}
        self.clone_off = None       # desplazamiento del tampón (fracción ancho/alto) o None
        self.clone_pick = None      # origen elegido con Alt+clic (0..1 sin recortar)
        self.stroke_u: list = []    # trazo en curso en 0..1 SIN recortar
        self._drag = None
        self.boxes: dict = {}
        self.hist: list = []
        self.hpos = -1
        self._initial: dict = {}    # ruta (normcase) -> ajustes de partida que manda Events
        self._events_cb = None
        self._pm_cache: dict = {}
        self._req = 0
        self._shown = 0
        self._jobs: queue.Queue = queue.Queue()
        self._busy_load = ""
        self.prefs = self._load_prefs()
        threading.Thread(target=self._render_loop, daemon=True).start()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_doc)
        self._prop_timer = QTimer(self)
        self._prop_timer.setSingleShot(True)
        self._prop_timer.timeout.connect(lambda: self._commit("Propiedades"))

        self.sel = None              # selección activa (especificación de edits_engine.selection_mask) o None
        self.sel_mode = "new"        # nueva · añadir · restar · intersecar
        self.sel_shape = "rect"      # forma del marco
        self.sel_feather = 0         # calado en px de la vista
        self._sel_alpha = None
        self._last_img = None        # última composición mostrada (para el cuentagotas y los histogramas)
        self._thumbs: dict = {}
        self._adj_key = None
        self._adj_force = False
        self.swatches = UI.Swatches()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        menubar = self._build_menubar()
        root.addWidget(menubar)
        self.canvas = EditCanvas()
        self.canvas.handler = self._mouse
        self.canvas.on_enter = self.end_transform
        self.canvas.zoom_changed = self._update_chrome
        opts = self._build_options()
        root.addWidget(opts)
        import ui as PUI
        PUI.collapse_menubar(menubar, self, opts.layout())  # los menús, tras ☰: una fila menos
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_tools())
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        mid.addWidget(self._build_doc_tab())
        mid.addWidget(self._build_banner())
        mid.addWidget(self.canvas, 1)
        h.addLayout(mid, 1)
        h.addWidget(self._build_dock_strip())
        h.addWidget(self._build_panels())
        root.addLayout(h, 1)
        root.addWidget(self._build_filmstrip())
        root.addWidget(self._build_statusbar())

        self.rendered.connect(self._on_rendered)
        self.loaded.connect(self._on_loaded)
        self.thumbs_ready.connect(lambda i: self.film.viewport().update())
        self.export_progress.connect(self._on_export_progress)
        self.lama_done.connect(self._on_lama_done)
        self._lama_dl = False
        for k, fn in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo),
                      ("\\", self.toggle_before), ("Y", self.next_split), ("Ctrl+E", self.export_current),
                      ("[", lambda: self._nudge_size(1 / 1.2)), ("]", lambda: self._nudge_size(1.2)),
                      ("Ctrl+0", lambda: self.canvas.set_zoom(None)), ("Ctrl+1", lambda: self.canvas.set_zoom(1.0)),
                      ("Ctrl++", lambda: self._zoom_step(1)), ("Ctrl+=", lambda: self._zoom_step(1)),
                      ("Ctrl+-", lambda: self._zoom_step(-1)),
                      ("Z", lambda: self.canvas.toggle_zoom(
                          self.canvas.cursor if self.canvas.cursor is not None
                          and self.canvas.img_rect.contains(self.canvas.cursor) else None)),
                      ("Ctrl+A", self.select_all), ("Ctrl+D", self.deselect), ("Ctrl+Shift+I", self.invert_selection),
                      ("Shift+F6", self.feather_selection), ("Ctrl+J", self.duplicate_layer),
                      ("Shift+Del", self.fill_selection), ("Shift+Backspace", self.fill_selection),
                      (Qt.Key_Delete, self._delete_key), ("X", self.swatches.swap), ("D", self.swatches.reset),
                      ("Ctrl+]", lambda: self.move_layer(1)), ("Ctrl+[", lambda: self.move_layer(-1)),
                      ("Ctrl+T", self.free_transform), ("Ctrl+Alt+G", self.toggle_clip),
                      ("Ctrl+N", self.new_canvas),
                      ("Escape", self.deselect)):
            QShortcut(QKeySequence(k), self, fn)
        for key, _, _, sc, _ in TOOLS:
            if sc:
                QShortcut(QKeySequence(sc), self, lambda k=key: self.set_tool(k))
        self.set_tool("heal")
        self._refresh_layers()
        self.setAcceptDrops(True)

    # ---------------------------------------------------------- arrastrar imágenes desde el Explorador
    @staticmethod
    def _dropped_images(e) -> list:
        if not e.mimeData().hasUrls():
            return []
        return [u.toLocalFile() for u in e.mimeData().urls()
                if u.isLocalFile() and u.toLocalFile().lower().endswith(EE.IMAGE_EXT) and os.path.isfile(u.toLocalFile())]

    def dragEnterEvent(self, e):
        if self._dropped_images(e):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if self._dropped_images(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = self._dropped_images(e)
        if not files:
            return
        e.acceptProposedAction()
        if not self.doc or self.base is None:
            self.status.setText("Abre primero una foto: la imagen se añade como capa encima.")
            return
        pt = self.canvas.mapFrom(self, e.position().toPoint())
        x, y = self.canvas.norm(pt)
        at = (x, y) if 0 <= x <= 1 and 0 <= y <= 1 else None
        for i, f in enumerate(files):
            self.add_image(f, (at[0] + 0.03 * i, at[1] + 0.03 * i) if at else None)

    def add_image(self, src: str, at=None):
        """Nueva capa con la imagen `src` (copiada junto a la sesión), centrada en `at` (0..1 de la foto)."""
        if not self.doc or not self.folder:
            return None
        try:
            path = EE.import_image(self.folder, src)
        except OSError as e:
            self.status.setText(f"No se pudo copiar la imagen: {e}")
            return None
        kw = {"path": path}
        if at:
            kw.update(x=round(min(1.0, max(0.0, at[0])), 4), y=round(min(1.0, max(0.0, at[1])), 4), anchor="mm")
        sel = self._layer()
        lay = EE.add_layer(self.doc, "image", above=sel["id"] if sel and sel["kind"] not in EE.OVERLAY else None,
                           **kw)
        lay["name"] = "Imagen: " + os.path.splitext(os.path.basename(src))[0]
        self.layer_id = lay["id"]
        self.set_tool("move")
        self._commit("Añadir imagen")
        self.status.setText("Imagen añadida: arrástrala con Mover, Ctrl+rueda para el tamaño; en Propiedades, "
                            "giro y quitar fondo.")
        return lay

    def _pick_image(self):
        p, _ = QFileDialog.getOpenFileName(self, "Añadir imagen como capa", self.prefs.get("img_dir", ""),
                                           "Imágenes (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp)")
        if p:
            self.prefs["img_dir"] = os.path.dirname(p)
            self._save_prefs()
            self.add_image(p)

    # ---------------------------------------------------------- preferencias (último texto, fuente y logo)
    def _prefs_path(self):
        return os.path.join(self.cfg_dir, "edits_prefs.json")

    def _load_prefs(self) -> dict:
        try:
            with open(self._prefs_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_prefs(self):
        try:
            os.makedirs(self.cfg_dir, exist_ok=True)
            with open(self._prefs_path(), "w", encoding="utf-8") as f:
                json.dump(self.prefs, f, ensure_ascii=False)
        except OSError:
            pass

    # ---------------------------------------------------------- construcción
    def _build_tools(self):
        w = QFrame()
        w.setObjectName("tooltabs")
        w.setFixedWidth(74)
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 8, 4, 8)
        v.setSpacing(2)
        self.tool_btn = {}
        g = QButtonGroup(self)
        tips = {k: (tip, sc) for k, _, tip, sc, _ in TOOLS}
        grid = QGridLayout()
        grid.setSpacing(2)
        for row, pair in enumerate(TOOL_GRID):
            for col, key in enumerate(pair):
                if key is None:
                    continue
                b = QPushButton()
                b.setIcon(tool_icon(key))
                b.setIconSize(QSize(20, 20))
                b.setObjectName("tool")
                b.setCheckable(True)
                b.setFixedSize(32, 32)
                b.setCursor(Qt.PointingHandCursor)
                tip, sc = tips[key]
                b.setToolTip(f"{tip}" + (f" ({sc})" if sc else ""))
                b.setStyleSheet(f"QPushButton#tool {{ background: transparent; border: none; border-radius: 4px; }}"
                                f"QPushButton#tool:hover {{ background: {T.SURFACE_3}; }}"
                                f"QPushButton#tool:checked {{ background: #3b3b3b; border: 1px solid #5a5a5a; }}")
                b.clicked.connect(lambda _, k=key: self.set_tool(k))
                g.addButton(b)
                grid.addWidget(b, row + (row >= 1) + (row >= 5) + (row >= 7), col)
                self.tool_btn[key] = b
        for r in (1, 6, 9):  # separadores entre grupos (como en PhotoCraft)
            sep = QFrame()
            sep.setFixedHeight(9)
            sep.setStyleSheet(f"border-bottom: 1px solid {T.LINE}; margin: 0 6px 4px 6px;")
            grid.addWidget(sep, r, 0, 1, 2)
        v.addLayout(grid)
        v.addSpacing(10)
        sw = QHBoxLayout()
        sw.addStretch(1)
        sw.addWidget(self.swatches)
        sw.addStretch(1)
        v.addLayout(sw)
        v.addStretch(1)
        return w

    def _build_options(self):
        """Barra de opciones de la herramienta (como la de Photoshop) + zoom y antes/después."""
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setStyleSheet(f"QFrame#toolbar {{ border-top: none; border-bottom: 1px solid {T.BORDER}; }}")
        bar.setFixedHeight(42)
        hb = QHBoxLayout(bar)
        hb.setContentsMargins(12, 4, 10, 4)
        hb.setSpacing(10)
        self.tool_lbl = QLabel("")
        self.tool_lbl.setStyleSheet(f"color: {T.TEXT_STRONG}; font-weight: 600;")
        self.tool_lbl.setMinimumWidth(130)
        hb.addWidget(self.tool_lbl)
        self.opt_size = SliderRow("size", "Tamaño", 2, 400, 1, 20)
        self.opt_size.lab.setFixedWidth(52)
        self.opt_size.setFixedWidth(230)
        self.opt_size.changed.connect(lambda k, v, f: self._set_opt("size", v))
        self.opt_hard = SliderRow("hard", "Dureza", 0, 100, 1, 50)
        self.opt_hard.lab.setFixedWidth(52)
        self.opt_hard.setFixedWidth(210)
        self.opt_hard.changed.connect(lambda k, v, f: self._set_opt("hard", v))
        self.opt_str = SliderRow("str", "Fuerza", 5, 100, 1, 50)
        self.opt_str.lab.setFixedWidth(52)
        self.opt_str.setFixedWidth(210)
        self.opt_str.changed.connect(lambda k, v, f: self._set_opt("str", v))
        self.opt_mode = QComboBox()
        self.opt_mode.setFixedWidth(180)
        self.opt_mode.currentIndexChanged.connect(lambda i: self._set_opt("mode", self.opt_mode.itemData(i)))
        self.opt_hint = QLabel("")
        self.opt_hint.setObjectName("meta")
        # opciones de las herramientas de selección: nueva · añadir · restar · intersecar, forma y calado
        self.opt_sel = QWidget()
        sl = QHBoxLayout(self.opt_sel)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(2)
        self.sel_btns = {}
        gsel = QButtonGroup(self)
        for k, txt, tip in (("new", "▢", "Nueva selección"), ("add", "＋", "Añadir a la selección (Mayús)"),
                            ("sub", "−", "Restar de la selección (Alt)"), ("inter", "⧉", "Intersecar")):
            b = QPushButton(txt)
            b.setObjectName("flat")
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFixedWidth(28)
            b.clicked.connect(lambda _, k=k: setattr(self, "sel_mode", k))
            gsel.addButton(b)
            sl.addWidget(b)
            self.sel_btns[k] = b
        self.sel_btns["new"].setChecked(True)
        self.opt_shape = QComboBox()
        self.opt_shape.addItem("Rectángulo", "rect")
        self.opt_shape.addItem("Elipse", "ellipse")
        self.opt_shape.currentIndexChanged.connect(lambda i: setattr(self, "sel_shape", self.opt_shape.itemData(i)))
        sl.addSpacing(8)
        sl.addWidget(self.opt_shape)
        sl.addSpacing(8)
        fl = QLabel("Calar:")
        fl.setObjectName("meta")
        self.opt_feather = QSpinBox()
        self.opt_feather.setRange(0, 250)
        self.opt_feather.setSuffix(" px")
        self.opt_feather.valueChanged.connect(lambda v: setattr(self, "sel_feather", int(v)))
        sl.addWidget(fl)
        sl.addWidget(self.opt_feather)
        sl.addSpacing(8)
        b_subj = _btn("Seleccionar sujeto", "ghost", "Selecciona a las personas de la foto con IA")
        b_subj.clicked.connect(self.select_subject)
        b_fill = _btn("Rellenar según contenido", "ghost", "Borra lo seleccionado con IA (Mayús+Supr)")
        b_fill.clicked.connect(self.fill_selection)
        b_mask = _btn("Máscara", "ghost", "Añade la selección como máscara de la capa elegida")
        b_mask.clicked.connect(self.add_mask_from_selection)
        for b in (b_subj, b_fill, b_mask):
            sl.addWidget(b)
        for x in (self.opt_size, self.opt_hard, self.opt_str, self.opt_mode, self.opt_sel):
            hb.addWidget(x)
        hb.addWidget(self.opt_hint, 1)
        self.cmp_bar = CMP.CompareBar()
        self.cmp_bar.changed.connect(self.set_compare)
        hb.addWidget(self.cmp_bar)
        return bar

    def _act(self, menu, text, fn, sc=""):
        """Entrada de menú; el atajo se muestra (lo atienden los QShortcut de la vista)."""
        a = menu.addAction(text + (f"\t{sc}" if sc else ""))
        a.triggered.connect(lambda *_: fn())
        return a

    def _build_menubar(self):
        mb = QMenuBar()
        mb.setNativeMenuBar(False)
        mb.setStyleSheet(f"QMenuBar {{ background: #1b1b1b; border-bottom: 1px solid {T.BORDER}; padding: 1px 6px; }}"
                         f"QMenuBar::item {{ padding: 4px 9px; background: transparent; color: {T.TEXT}; }}"
                         f"QMenuBar::item:selected {{ background: {T.SURFACE_3}; border-radius: 3px; }}")
        f = mb.addMenu("Archivo")
        self._act(f, "Nuevo lienzo en blanco…", self.new_canvas, "Ctrl+N")
        self._act(f, "Colocar imagen…", self._pick_image)
        self._act(f, "Exportar esta foto", self.export_current, "Ctrl+E")
        self._act(f, "Exportar las seleccionadas", self.export_selected)
        f.addSeparator()
        self._act(f, "Descartar el retoque de esta foto…", self.reset_photo)
        e = mb.addMenu("Edición")
        self._act(e, "Deshacer", self.undo, "Ctrl+Z")
        self._act(e, "Rehacer", self.redo, "Mayús+Ctrl+Z")
        e.addSeparator()
        self._act(e, "Transformación libre", self.free_transform, "Ctrl+T")
        self._act(e, "Quitar la transformación", self.reset_transform)
        e.addSeparator()
        self._act(e, "Rellenar según contenido", self.fill_selection, "Mayús+Supr")
        self._act(e, "Quitar fondo (recortar a PNG)", self.remove_background)
        i = mb.addMenu("Imagen")
        sub = i.addMenu("Ajustes")
        for k in A.ORDER:
            self._act(sub, A.ADJUSTMENTS[k][0] + "…", lambda k=k: self.add_adjust(k))
        i.addSeparator()
        self._act(i, "Quitar fondo (recortar a PNG)", self.remove_background)
        c = mb.addMenu("Capa")
        new = c.addMenu("Nueva")
        for kind in ("retouch", "skin", "light", "liquify", "background", "text", "logo"):
            self._act(new, EE.KINDS[kind], lambda k=kind: self.add_layer(k))
        self._act(new, "Imagen…", self._pick_image)
        adj = c.addMenu("Nueva capa de ajuste")
        for k in A.ORDER:
            self._act(adj, A.ADJUSTMENTS[k][0] + "…", lambda k=k: self.add_adjust(k))
        c.addSeparator()
        self._act(c, "Duplicar capa", self.duplicate_layer, "Ctrl+J")
        self._act(c, "Eliminar capa", self.delete_layer)
        c.addSeparator()
        self._act(c, "Máscara de capa: de la selección", self.add_mask_from_selection)
        self._act(c, "Máscara de capa: invertir", self.invert_mask)
        self._act(c, "Máscara de capa: eliminar", self.delete_mask)
        self._act(c, "Crear / soltar máscara de recorte", self.toggle_clip, "Alt+Ctrl+G")
        c.addSeparator()
        self._act(c, "Traer adelante", lambda: self.move_layer(1), "Ctrl+]")
        self._act(c, "Enviar atrás", lambda: self.move_layer(-1), "Ctrl+[")
        t = mb.addMenu("Texto")
        self._act(t, "Nueva capa de texto", lambda: self.add_layer("text"))
        self._act(t, "Herramienta Texto", lambda: self.set_tool("type"), "T")
        se = mb.addMenu("Selección")
        self._act(se, "Todo", self.select_all, "Ctrl+A")
        self._act(se, "Deseleccionar", self.deselect, "Ctrl+D")
        self._act(se, "Invertir", self.invert_selection, "Mayús+Ctrl+I")
        se.addSeparator()
        self._act(se, "Sujeto", self.select_subject)
        self._act(se, "Calar…", self.feather_selection, "Mayús+F6")
        self._act(se, "Selección a máscara de capa", self.add_mask_from_selection)
        fi = mb.addMenu("Filtro")
        self._act(fi, "Licuar", lambda: self.set_tool("liquify"), "Mayús+Ctrl+X")
        self._act(fi, "Suavizar piel", lambda: self.add_layer("skin"))
        self._act(fi, "Desenfocar fondo", lambda: self.add_layer("background"))
        vi = mb.addMenu("Vista")
        self._act(vi, "Acercar", lambda: self._zoom_step(1), "Ctrl++")
        self._act(vi, "Alejar", lambda: self._zoom_step(-1), "Ctrl+-")
        self._act(vi, "Encajar en pantalla", lambda: self.canvas.set_zoom(None), "Ctrl+0")
        self._act(vi, "100 %", lambda: self.canvas.set_zoom(1.0), "Ctrl+1")
        vi.addSeparator()
        self._act(vi, "Ver el antes", self.toggle_before, "\\")
        self._act(vi, "Antes / después", self.next_split, "Y")
        ve = mb.addMenu("Ventana")
        self._act(ve, "Propiedades", lambda: self._show_tab(self.top_tabs, 0))
        self._act(ve, "Ajustes", lambda: self._show_tab(self.top_tabs, 1))
        self._act(ve, "Capas", lambda: self._show_tab(self.bottom_tabs, 0))
        self._act(ve, "Historial", lambda: self._show_tab(self.bottom_tabs, 1))
        self._act(ve, "Exportar", lambda: self._show_tab(self.bottom_tabs, 2))
        self._act(ve, "Tira de fotos", lambda: self.film_box.setVisible(not self.film_box.isVisible()))
        hp = mb.addMenu("Ayuda")
        self._act(hp, "Atajos de teclado", self._show_shortcuts)
        return mb

    def _show_tab(self, tabs, i):
        self.panels.show()
        tabs.set_current(i)

    def _show_shortcuts(self):
        from PySide6.QtWidgets import QMessageBox
        rows = [f"{sc or '—'}  ·  {tip.split(':')[0]}" for _, _, tip, sc, _ in TOOLS]
        rows += ["Ctrl+A / Ctrl+D / Mayús+Ctrl+I  ·  todo / deseleccionar / invertir",
                 "Mayús+Supr  ·  rellenar la selección según contenido", "Ctrl+J  ·  duplicar capa",
                 "Ctrl+Z / Mayús+Ctrl+Z  ·  deshacer / rehacer", "[ ]  ·  tamaño del pincel",
                 "Rueda · Z · Ctrl+0 · Ctrl+1  ·  zoom", "Espacio + arrastrar  ·  mano",
                 "X / D  ·  intercambiar / restablecer colores", "\\ · Y  ·  antes / después"]
        QMessageBox.information(self, "Atajos de Edits", "\n".join(rows))

    def _build_doc_tab(self):
        bar = QFrame()
        bar.setStyleSheet(f"QFrame {{ background: #1a1a1a; border-bottom: 1px solid {T.BORDER}; }}")
        bar.setFixedHeight(30)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 0, 8, 0)
        self.doc_tab = QLabel("")
        self.doc_tab.setStyleSheet(f"QLabel {{ background: {T.SURFACE}; color: {T.TEXT}; padding: 5px 12px; "
                                   f"border: 1px solid {T.LINE}; border-bottom: none; border-top-left-radius: 4px;"
                                   f" border-top-right-radius: 4px; }}")
        h.addWidget(self.doc_tab)
        h.addStretch(1)
        return bar

    def _build_statusbar(self):
        bar = QFrame()
        bar.setStyleSheet(f"QFrame {{ background: #1b1b1b; border-top: 1px solid {T.BORDER}; }}"
                          f"QLabel {{ color: {T.MUTED}; padding: 0 10px; border-right: 1px solid {T.LINE}; }}")
        bar.setFixedHeight(24)
        h = QHBoxLayout(bar)
        h.setContentsMargins(4, 0, 8, 0)
        h.setSpacing(0)
        self.st_zoom = QLabel("—")
        self.st_mode = QLabel("Color RGB · 8 bits")
        self.st_size = QLabel("")
        self.st_layers = QLabel("")
        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {T.MUTED}; border: none;")
        self.pb = QProgressBar()
        self.pb.setTextVisible(False)
        self.pb.setFixedWidth(160)
        self.pb.setFixedHeight(10)
        self.pb.hide()
        for x in (self.st_zoom, self.st_mode, self.st_size, self.st_layers):
            h.addWidget(x)
        h.addWidget(self.status, 1)
        h.addWidget(self.pb)
        return bar

    def _update_chrome(self):
        """Pestaña del documento y barra de estado (zoom, tamaño, capas), como PhotoCraft."""
        z = self.canvas.zoom
        if self.canvas.pm is not None:
            pct = (z.pct if z.pct is not None else z.fit_pct(self.canvas._area(), self.canvas.pm.size())) * 100
        else:
            pct = 0
        name = self.cur.name + os.path.splitext(self.cur.path)[1] if self.cur else "Sin foto"
        lay = self._layer()
        self.doc_tab.setText(f"×   {name} @ {pct:.1f} % ({lay['name'] if lay else 'Fondo'}, RGB/8)"
                             + ("*" if self.doc and len(self.hist) > 1 else ""))
        self.st_zoom.setText(f"{pct:.1f} %" if pct else "—")
        if self.cur:
            try:
                fw, fh = E.full_size(self.cur.path)
            except Exception:
                fw = fh = 0
            self.st_size.setText(f"{fw} × {fh} px" if fw else "")
        n = len(self.doc["layers"]) + 1 if self.doc else 0
        self.st_layers.setText(f"{n} capa{'s' if n != 1 else ''}" if n else "")

    def _zoom_step(self, d):
        c = self.canvas
        if c.pm is not None:
            c.zoom.wheel(120 * d, c._area().center(), c._area(), c.pm.size())
            c._zoomed()

    def _build_banner(self):
        bw = QWidget()
        bl = QVBoxLayout(bw)
        bl.setContentsMargins(10, 6, 10, 0)
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
        self.b_use.clicked.connect(lambda: self._finish_events(True))
        self.b_back.clicked.connect(lambda: self._finish_events(False))
        bl.addWidget(self.banner)
        bw.hide()
        self.banner_wrap = bw
        return bw

    def _build_filmstrip(self):
        w = QFrame()
        w.setObjectName("filmstrip")
        w.setFixedHeight(122)
        self.film_box = w
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 4, 10, 4)
        v.setSpacing(2)
        top = QHBoxLayout()
        self.lib_info = QLabel("Sin carpeta")
        self.lib_info.setObjectName("meta")
        top.addWidget(self.lib_info, 1)
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

    def _build_dock_strip(self):
        """Columna fina de iconos a la izquierda de los paneles (como PhotoCraft)."""
        w = QFrame()
        w.setFixedWidth(34)
        w.setStyleSheet(f"QFrame {{ background: #1a1a1a; border-left: 1px solid {T.BORDER}; }}"
                        f"QToolButton {{ border: none; color: {T.MUTED}; font-size: 13pt; padding: 6px 0; }}"
                        f"QToolButton:hover {{ color: {T.TEXT_STRONG}; }}")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 8)
        v.setSpacing(4)
        for icon, tip, fn in (("☰", "Propiedades", lambda: self._show_tab(self.top_tabs, 0)),
                              ("◑", "Ajustes", lambda: self._show_tab(self.top_tabs, 1)),
                              ("❏", "Capas", lambda: self._show_tab(self.bottom_tabs, 0)),
                              ("⟲", "Historial", lambda: self._show_tab(self.bottom_tabs, 1)),
                              ("⇩", "Exportar", lambda: self._show_tab(self.bottom_tabs, 2)),
                              ("⇥", "Mostrar u ocultar los paneles", lambda: self.panels.setVisible(
                                  not self.panels.isVisible()))):
            b = QToolButton()
            b.setText(icon)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            v.addWidget(b)
        v.addStretch(1)
        return w

    def _build_panels(self):
        outer = QFrame()
        outer.setObjectName("previewPane")
        outer.setFixedWidth(330)
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(0)
        self.panels = outer
        split = QSplitter(Qt.Vertical)
        split.setHandleWidth(3)
        ov.addWidget(split)

        # arriba: Propiedades | Ajustes
        self.top_tabs = UI.PanelTabs(["Propiedades", "Ajustes"])
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        pv = QVBoxLayout(inner)
        pv.setContentsMargins(12, 10, 12, 10)
        pv.setSpacing(8)
        self.props_title = QLabel("Propiedades")
        self.props_title.setStyleSheet(f"color: {T.TEXT_STRONG}; font-weight: 600;")
        pv.addWidget(self.props_title)
        self.prop_box = QWidget()
        self.prop_lay = QVBoxLayout(self.prop_box)
        self.prop_lay.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(self.prop_box)
        self._pages = {}
        self._build_prop_pages()
        self.adj_panel = UI.AdjustPanel(SliderRow)
        self.adj_panel.changed.connect(self._adj_changed)
        self.adj_panel.reset.connect(self._adj_reset)
        self.adj_panel.hide()
        self.prop_lay.addWidget(self.adj_panel)
        self._pages["adjust"] = self.adj_panel
        self.mask_info = QWidget()
        mi = QHBoxLayout(self.mask_info)
        mi.setContentsMargins(0, 4, 0, 0)
        self.mask_lbl = QLabel("Máscara de capa")
        self.mask_lbl.setObjectName("meta")
        b_inv = _btn("Invertir", "small", "Invertir la máscara de la capa")
        b_inv.clicked.connect(self.invert_mask)
        b_del = _btn("Quitar", "small", "Eliminar la máscara de la capa")
        b_del.clicked.connect(self.delete_mask)
        mi.addWidget(self.mask_lbl, 1)
        mi.addWidget(b_inv)
        mi.addWidget(b_del)
        pv.addWidget(self.mask_info)
        pv.addStretch(1)
        sc.setWidget(inner)
        self.top_tabs.add_page(sc)
        self.top_tabs.add_page(UI.adjustments_grid(self.add_adjust))
        split.addWidget(self.top_tabs)

        # abajo: Capas | Historial | Exportar
        self.bottom_tabs = UI.PanelTabs(["Capas", "Historial", "Exportar"])
        lw = QWidget()
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(8, 8, 8, 6)
        lv.setSpacing(6)
        row = QHBoxLayout()
        self.blend_cb = QComboBox()
        for k, label in A.BLEND_MODES.items():
            self.blend_cb.addItem(label, k)
        self.blend_cb.setToolTip("Modo de fusión de la capa")
        self.blend_cb.currentIndexChanged.connect(
            lambda i: self._set_prop("blend", self.blend_cb.itemData(i), True))
        ol = QLabel("Opacidad:")
        ol.setObjectName("meta")
        self.opacity = _Spin()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix(" %")
        self.opacity.valueChanged.connect(lambda v: self._set_prop("opacity", int(v), True, delay=True))
        row.addWidget(self.blend_cb, 1)
        row.addWidget(ol)
        row.addWidget(self.opacity)
        lv.addLayout(row)
        self.clip_chk = QCheckBox("Recortar a la imagen de debajo")
        self.clip_chk.setToolTip("Máscara de recorte (Alt+Ctrl+G): esta capa solo afecta a la imagen de debajo, "
                                 "o solo se ve dentro de ella")
        self.clip_chk.toggled.connect(lambda on: self.toggle_clip(bool(on)))
        lv.addWidget(self.clip_chk)
        self.layer_list = QListWidget()
        self.layer_list.setObjectName("list")
        self.layer_list.setItemDelegate(UI.LayersDelegate(self))
        self.layer_list.setStyleSheet(f"QListWidget#list {{ background: {T.SURFACE}; border: 1px solid {T.LINE}; }}")
        self.layer_list.currentRowChanged.connect(self._layer_selected)
        lv.addWidget(self.layer_list, 1)
        icons = QHBoxLayout()
        icons.setSpacing(2)
        icons.addStretch(1)
        b_mask = QToolButton()
        b_mask.setText("◙")
        b_mask.setToolTip("Añadir máscara de capa (de la selección, si hay)")
        b_mask.clicked.connect(self.add_mask_from_selection)
        b_adj = QToolButton()
        b_adj.setText("◑")
        b_adj.setToolTip("Nueva capa de ajuste")
        madj = QMenu(b_adj)
        for k in A.ORDER:
            madj.addAction(A.ADJUSTMENTS[k][0], lambda k=k: self.add_adjust(k))
        b_adj.setMenu(madj)
        b_adj.setPopupMode(QToolButton.InstantPopup)
        b_new = QToolButton()
        b_new.setText("＋")
        b_new.setToolTip("Nueva capa")
        mnew = QMenu(b_new)
        for kind in ("retouch", "skin", "light", "liquify", "background", "text", "logo"):
            mnew.addAction(EE.KINDS[kind], lambda k=kind: self.add_layer(k))
        mnew.addAction("Imagen… (o arrástrala desde el Explorador)", self._pick_image)
        mnew.addSeparator()
        mnew.addAction("Nuevo lienzo en blanco…", self.new_canvas)
        mnew.addAction("Quitar fondo (recortar a PNG)", self.remove_background)
        b_new.setMenu(mnew)
        b_new.setPopupMode(QToolButton.InstantPopup)
        b_up = QToolButton()
        b_up.setText("▲")
        b_up.setToolTip("Subir la capa (Ctrl+])")
        b_up.clicked.connect(lambda: self.move_layer(1))
        b_dn = QToolButton()
        b_dn.setText("▼")
        b_dn.setToolTip("Bajar la capa (Ctrl+[)")
        b_dn.clicked.connect(lambda: self.move_layer(-1))
        b_dup = QToolButton()
        b_dup.setText("⧉")
        b_dup.setToolTip("Duplicar la capa (Ctrl+J)")
        b_dup.clicked.connect(self.duplicate_layer)
        b_del = QToolButton()
        b_del.setText("🗑")
        b_del.setToolTip("Eliminar la capa")
        b_del.clicked.connect(self.delete_layer)
        for b in (b_mask, b_adj, b_new, b_up, b_dn, b_dup, b_del):
            b.setStyleSheet(f"QToolButton {{ border: none; color: {T.MUTED}; font-size: 12pt; padding: 2px 5px; }}"
                            f"QToolButton:hover {{ color: {T.TEXT_STRONG}; }}"
                            f"QToolButton::menu-indicator {{ image: none; }}")
            icons.addWidget(b)
        lv.addLayout(icons)
        self.bottom_tabs.add_page(lw)

        hw = QWidget()
        hv = QVBoxLayout(hw)
        hv.setContentsMargins(8, 8, 8, 8)
        self.hist_list = QListWidget()
        self.hist_list.setObjectName("list")
        self.hist_list.itemClicked.connect(lambda it: self._history_jump(self.hist_list.row(it)))
        hv.addWidget(self.hist_list)
        self.bottom_tabs.add_page(hw)

        ew = QWidget()
        ev = QVBoxLayout(ew)
        ev.setContentsMargins(10, 10, 10, 10)
        ev.setSpacing(6)
        self.fmt = QComboBox()
        for k, label in EE.FORMATS.items():
            self.fmt.addItem(label, k)
        self.size = QComboBox()
        self.size.addItems(["Tamaño original", "2048 px lado largo", "1080 px lado largo"])
        qrow = QHBoxLayout()
        ql = QLabel("Calidad JPG")
        ql.setObjectName("meta")
        self.quality = QSpinBox()
        self.quality.setRange(70, 100)
        self.quality.setValue(95)
        qrow.addWidget(ql)
        qrow.addWidget(self.quality)
        self.b_export = _btn("Exportar esta foto", "primary", "Ctrl+E · se guarda en «Edits» dentro de la carpeta")
        self.b_export.clicked.connect(self.export_current)
        self.b_export_sel = _btn("Exportar las seleccionadas", "ghost",
                                 "Exporta las fotos seleccionadas en la tira que tengan retoque")
        self.b_export_sel.clicked.connect(self.export_selected)
        for x in (self.fmt, self.size):
            ev.addWidget(x)
        ev.addLayout(qrow)
        ev.addWidget(self.b_export)
        ev.addWidget(self.b_export_sel)
        ev.addStretch(1)
        self.bottom_tabs.add_page(ew)
        split.addWidget(self.bottom_tabs)
        split.setSizes([520, 360])
        return outer

    def _label(self, text):
        l = QLabel(text)
        l.setObjectName("meta")
        l.setWordWrap(True)
        return l

    def _build_prop_pages(self):
        def page(name):
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            w.hide()
            self.prop_lay.addWidget(w)
            self._pages[name] = w
            return lay

        lay = page("none")
        lay.addWidget(self._label("Elige una herramienta a la izquierda y pinta sobre la foto, o añade una capa "
                                  "con «＋ Capa». Cada herramienta crea su capa la primera vez."))

        lay = page("retouch")
        lay.addWidget(self._label("Corrector (J): pinta sobre granos, brillos o manchas; busca solo el mejor "
                                  "parche. Tampón (S): Alt+clic para elegir el origen. Relleno (G): pinta lo "
                                  "que quieras borrar (una persona, un objeto); lo rellena la IA LaMa en tu PC "
                                  "(la primera vez se descargan 208 MB)."))
        self.retouch_info = self._label("")
        lay.addWidget(self.retouch_info)
        b = _btn("Quitar el último trazo", "ghost")
        b.clicked.connect(lambda: self._pop_last("ops", "Quitar trazo"))
        lay.addWidget(b)

        lay = page("skin")
        self.skin_amount = SliderRow("amount", "Suavizado", 0, 100, 1, 55)
        self.skin_texture = SliderRow("texture", "Textura", 0, 100, 1, 45)
        self.skin_amount.changed.connect(lambda k, val, f: self._set_prop("amount", int(val), f))
        self.skin_texture.changed.connect(lambda k, val, f: self._set_prop("texture", int(val), f))
        self.skin_auto = QCheckBox("Encontrar la piel con IA (caras y cuerpo)")
        self.skin_auto.toggled.connect(lambda on: self._set_prop("auto", bool(on), True))
        lay.addWidget(self.skin_amount)
        lay.addWidget(self.skin_texture)
        lay.addWidget(self.skin_auto)
        lay.addWidget(self._label("Con el pincel de Piel (B) añades zonas; con clic derecho o Alt las quitas. "
                                  "Textura alta = se conservan los poros finos."))
        b = _btn("Borrar las zonas pintadas", "ghost")
        b.clicked.connect(lambda: self._clear_list("strokes", "Borrar zonas de piel"))
        lay.addWidget(b)

        lay = page("light")
        lay.addWidget(self._label("Ojos y luz (O): elige el modo arriba. Aclarar para ojeras, Blanquear para "
                                  "dientes y blanco de los ojos, Oscurecer para brillos."))
        self.light_info = self._label("")
        lay.addWidget(self.light_info)
        b = _btn("Quitar el último trazo", "ghost")
        b.clicked.connect(lambda: self._pop_last("strokes", "Quitar trazo"))
        lay.addWidget(b)

        lay = page("liquify")
        lay.addWidget(self._label("Licuar (W): Empujar arrastra la zona en la dirección del trazo; Inflar y "
                                  "Encoger actúan donde haces clic o pasas. Úsalo con poca fuerza."))
        self.liq_info = self._label("")
        lay.addWidget(self.liq_info)
        b = _btn("Quitar el último trazo", "ghost")
        b.clicked.connect(lambda: self._pop_last("strokes", "Quitar trazo"))
        lay.addWidget(b)

        lay = page("background")
        self.bg_detect = QComboBox()
        for k, label in EE.DETECTS.items():
            self.bg_detect.addItem(label, k)
        self.bg_detect.setToolTip("Qué es el sujeto: personas (IA), un objeto o producto, o un logo / fondo liso")
        self.bg_detect.currentIndexChanged.connect(
            lambda i: self._set_prop("detect", self.bg_detect.itemData(i), True))
        lay.addWidget(self._label("Sujeto"))
        lay.addWidget(self.bg_detect)
        self.bg_tol = SliderRow("tol", "Tolerancia", 1, 80, 1, 12)
        self.bg_tol.changed.connect(lambda k, val, f: self._set_prop("tol", int(val), f))
        self.bg_soft = SliderRow("soft", "Suavidad", 1, 40, 1, 8)
        self.bg_soft.changed.connect(lambda k, val, f: self._set_prop("soft", int(val), f))
        crow2 = QHBoxLayout()
        self.bg_pick = _btn("Cuentagotas", "ghost", "Haz clic en el fondo de la foto para elegir su color")
        self.bg_pick.clicked.connect(self._start_eyedrop)
        self.bg_auto = _btn("Automático", "ghost", "Color del fondo tomado del borde de la imagen")
        self.bg_auto.clicked.connect(lambda: self._set_prop("key_color", None, True))
        crow2.addWidget(self.bg_pick)
        crow2.addWidget(self.bg_auto)
        self.bg_contig = QCheckBox("Solo el fondo que toca el borde")
        self.bg_contig.setToolTip("Desmarcado (logos): también se quita el hueco de letras como la O. "
                                  "Marcado: el mismo color dentro del sujeto se conserva")
        self.bg_contig.toggled.connect(lambda on: self._set_prop("contiguous", bool(on), True))
        self.bg_color_box = QWidget()
        cb_l = QVBoxLayout(self.bg_color_box)
        cb_l.setContentsMargins(0, 0, 0, 0)
        cb_l.setSpacing(5)
        for x in (self.bg_tol, self.bg_soft):
            cb_l.addWidget(x)
        cb_l.addLayout(crow2)
        cb_l.addWidget(self.bg_contig)
        lay.addWidget(self.bg_color_box)
        lay.addWidget(self._label("Qué hacer con el fondo"))
        self.bg_mode = QComboBox()
        for k, label in BG_MODES:
            self.bg_mode.addItem(label, k)
        self.bg_mode.currentIndexChanged.connect(lambda i: self._set_prop("mode", self.bg_mode.itemData(i), True))
        self.bg_blur = SliderRow("blur", "Desenfoque", 1, 100, 1, 40)
        self.bg_blur.changed.connect(lambda k, val, f: self._set_prop("blur", int(val), f))
        self.bg_color = _btn("Color del fondo…", "ghost")
        self.bg_color.clicked.connect(lambda: self._pick_color("color"))
        lay.addWidget(self.bg_mode)
        lay.addWidget(self.bg_blur)
        lay.addWidget(self.bg_color)
        lay.addWidget(self._label("Si el recorte se equivoca (un cartel, un ramo, una letra), pinta con el pincel "
                                  "de máscara (B) lo que es sujeto; con clic derecho, lo que es fondo. "
                                  "«Transparente» se exporta en PNG o TIFF con transparencia."))
        b = _btn("Borrar las zonas pintadas", "ghost")
        b.clicked.connect(lambda: self._clear_list("strokes", "Borrar zonas del fondo"))
        lay.addWidget(b)

        lay = page("text")
        self.txt = QPlainTextEdit()
        self.txt.setFixedHeight(58)
        self.txt.textChanged.connect(lambda: self._set_prop("text", self.txt.toPlainText(), False, delay=True))
        self.txt_font = QComboBox()
        self.txt_font.setEditable(True)
        self.txt_font.setInsertPolicy(QComboBox.NoInsert)
        self.txt_font.currentTextChanged.connect(lambda t: self._set_prop("font", t, True) if t in EE.list_fonts()
                                                 else None)
        self.txt_size = SliderRow("size", "Tamaño", 0.5, 20, 0.1, 3)
        self.txt_size.changed.connect(lambda k, val, f: self._set_prop("size", float(val), f))
        crow = QHBoxLayout()
        self.txt_color = _btn("Color…", "ghost")
        self.txt_color.clicked.connect(lambda: self._pick_color("color"))
        self.txt_shadow = QCheckBox("Sombra")
        self.txt_shadow.toggled.connect(lambda on: self._set_prop("shadow", bool(on), True))
        crow.addWidget(self.txt_color)
        crow.addWidget(self.txt_shadow)
        self.txt_pos = self._pos_combo()
        for x in (self.txt, self.txt_font, self.txt_size):
            lay.addWidget(x)
        lay.addLayout(crow)
        lay.addWidget(self.txt_pos)
        lay.addWidget(self._label("Con Mover (V) arrastras el texto. El tamaño es % del alto de la foto."))

        lay = page("logo")
        self.logo_btn = _btn("Elegir logo (PNG)…", "ghost")
        self.logo_btn.clicked.connect(self._pick_logo)
        self.logo_name = self._label("")
        self.logo_w = SliderRow("width", "Ancho", 2, 60, 0.5, 16)
        self.logo_w.changed.connect(lambda k, val, f: self._set_prop("width", float(val), f))
        self.logo_pos = self._pos_combo()
        self.logo_ko = QCheckBox("Quitar el fondo del logo (JPG o con recuadro)")
        self.logo_ko.toggled.connect(lambda on: self._set_prop("knockout", bool(on), True))
        self.logo_tol = SliderRow("tol", "Tolerancia", 1, 80, 1, 12)
        self.logo_tol.changed.connect(lambda k, val, f: self._set_prop("tol", int(val), f))
        for x in (self.logo_btn, self.logo_name, self.logo_w, self.logo_ko, self.logo_tol, self.logo_pos):
            lay.addWidget(x)
        lay.addWidget(self._label("Con Mover (V) arrastras el logo. El ancho es % del ancho de la foto; la "
                                  "opacidad, en Capas."))

        self._build_image_page(page("image"))

    def _build_image_page(self, lay):
        self.img_name = self._label("")
        b = _btn("Cambiar imagen…", "ghost")
        b.clicked.connect(self._replace_image)
        self.img_w = SliderRow("width", "Tamaño", 1, 150, 0.5, 40)
        self.img_w.changed.connect(lambda k, val, f: self._set_prop("width", float(val), f))
        self.img_angle = SliderRow("angle", "Giro", -180, 180, 1, 0)
        self.img_angle.changed.connect(lambda k, val, f: self._set_prop("angle", float(val), f))
        self.img_flip = QCheckBox("Voltear horizontal")
        self.img_flip.toggled.connect(lambda on: self._set_prop("flip", bool(on), True))
        self.img_cut = QComboBox()
        for k, label in EE.CUTS.items():
            self.img_cut.addItem(label, k)
        self.img_cut.currentIndexChanged.connect(lambda i: self._set_prop("cut", self.img_cut.itemData(i), True))
        self.img_tol = SliderRow("tol", "Tolerancia", 1, 80, 1, 12)
        self.img_tol.changed.connect(lambda k, val, f: self._set_prop("tol", int(val), f))
        self.img_pos = self._pos_combo()
        self.img_tf = _btn("Transformación libre (Ctrl+T)", "ghost",
                           "Asas en las 4 esquinas: arrastra una para deformar o dar perspectiva; Mayús la escala "
                           "desde el centro; dentro, mueve. Intro termina.")
        self.img_tf.clicked.connect(self.free_transform)
        self.img_tf_reset = _btn("Quitar la transformación", "ghost", "Vuelve al tamaño, giro y posición de antes")
        self.img_tf_reset.clicked.connect(self.reset_transform)
        self.img_tf_info = self._label("Transformada a mano: el tamaño y el giro de arriba no se usan.")
        for x in (self.img_name, b, self.img_w, self.img_angle, self.img_flip, self.img_tf, self.img_tf_reset,
                  self.img_tf_info, self._label("Quitar el fondo"), self.img_cut, self.img_tol, self.img_pos):
            lay.addWidget(x)
        lay.addWidget(self._label("Con Mover (V) la arrastras; Ctrl+rueda cambia el tamaño. El tamaño es % del "
                                  "ancho de la foto; la opacidad, en Capas. Las capas de ajuste que pongas encima "
                                  "la afectan; con «Recortar a la imagen de debajo», solo a ella."))

    def _replace_image(self):
        lay = self._layer()
        if not lay or lay["kind"] != "image":
            return
        p, _ = QFileDialog.getOpenFileName(self, "Cambiar imagen", self.prefs.get("img_dir", ""),
                                           "Imágenes (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp)")
        if p:
            lay["path"] = EE.import_image(self.folder, p)
            lay["name"] = "Imagen: " + os.path.splitext(os.path.basename(p))[0]
            self._commit("Cambiar imagen")

    def _pos_combo(self):
        cb = QComboBox()
        cb.addItem("Posición…", None)
        for k, label, x, y in POSITIONS:
            cb.addItem(label, (k, x, y))
        cb.activated.connect(lambda i, c=cb: self._set_position(c.itemData(i)))
        return cb

    # ---------------------------------------------------------- herramientas
    def set_tool(self, key):
        self.tool = key
        self.tool_btn[key].setChecked(True)
        name = {k: tip.split(":")[0] for k, _, tip, _, _ in TOOLS}[key]
        self.tool_lbl.setText(name)
        brush = key in self.radius
        self.opt_sel.setVisible(key in SELECT_TOOLS)
        self.opt_shape.setVisible(key == "marquee")
        self.opt_size.setVisible(brush)
        if brush:
            self.opt_size.set_value(self.radius[key])
        self.opt_hard.setVisible(key in self.hardness)
        if key in self.hardness:
            self.opt_hard.set_value(self.hardness[key])
        self.opt_str.setVisible(key in self.strength)
        if key != "move":
            self._tf_on = False
        self._sync_quad()
        if key in self.strength:
            self.opt_str.set_value(self.strength[key])
        modes = {"light": LIGHT_MODES, "liquify": LIQ_MODES}.get(key)
        self.opt_mode.setVisible(bool(modes))
        if modes:
            self.opt_mode.blockSignals(True)
            self.opt_mode.clear()
            for k, label in modes:
                self.opt_mode.addItem(label, k)
            self.opt_mode.setCurrentIndex([k for k, _ in modes].index(self.mode[key]))
            self.opt_mode.blockSignals(False)
        self.opt_hint.setText({"clone": "Alt+clic: origen", "skin": "Clic derecho o Alt: quitar",
                               "move": "Rueda: zoom · arrastrar: mano", "eyedrop": "Clic: color frontal",
                               "type": "Clic en la foto para escribir", "hand": "Arrastra para desplazar",
                               "zoom": "Clic acerca · Alt+clic aleja", "marquee": "", "lasso": "",
                               "subject": "Clic en la foto"}.get(key, "[ ] tamaño"))
        self.canvas.brush_px = self.radius.get(key, 0) if brush else 0
        if key == "fill":
            self._preload_lama()
        self.canvas.marker = None
        self.canvas.setCursor(Qt.OpenHandCursor if key in ("move", "hand") else
                              (Qt.PointingHandCursor if key in ("subject", "eyedrop") else
                               (Qt.IBeamCursor if key == "type" else Qt.CrossCursor)))
        # al elegir la herramienta se selecciona su capa (si existe)
        kind = TOOL_LAYER.get(key)
        if key == "skin" and self._layer() and (self._layer()["kind"] == "background" or self._layer().get("mask")
                                                 or self._layer()["kind"] == "adjust"):
            kind = None  # el pincel de máscara sigue en la capa elegida (su máscara)
        if kind and self.doc:
            lay = self._layer() if self._layer() and self._layer()["kind"] == kind else \
                next((l for l in reversed(self.doc["layers"]) if l["kind"] == kind), None)
            if lay:
                self._select_layer(lay["id"])
        self._update_chrome()
        self.canvas.update()

    def _preload_lama(self):
        """Carga el modelo de borrado en segundo plano al elegir Relleno (la primera carga tarda ~15 s)."""
        if EE.lama_ready() and EE._LAMA["session"] is None and not getattr(self, "_lama_loading", False):
            self._lama_loading = True
            threading.Thread(target=lambda: (EE._lama_session(), setattr(self, "_lama_loading", False)),
                             daemon=True).start()

    def _set_opt(self, what, v):
        t = self.tool
        if what == "size" and t in self.radius:
            self.radius[t] = int(v)
            self.canvas.brush_px = int(v)
        elif what == "hard" and t in self.hardness:
            self.hardness[t] = int(v)
        elif what == "str" and t in self.strength:
            self.strength[t] = int(v)
        elif what == "mode" and t in self.mode and v:
            self.mode[t] = v
        self.canvas.update()

    def _nudge_size(self, f):
        if self.tool in self.radius:
            self.radius[self.tool] = int(max(2, min(400, round(self.radius[self.tool] * f))))
            self.opt_size.set_value(self.radius[self.tool])
            self.canvas.brush_px = self.radius[self.tool]
            self.canvas.update()

    # ---------------------------------------------------------- coordenadas
    def _to_u(self, x, y):
        """De 0..1 de la foto mostrada (recortada) a 0..1 de la foto sin recortar."""
        c = self.box
        return c[0] + x * (c[2] - c[0]), c[1] + y * (c[3] - c[1])

    def _from_u(self, xu, yu):
        c = self.box
        return (xu - c[0]) / max(1e-6, c[2] - c[0]), (yu - c[1]) / max(1e-6, c[3] - c[1])

    def _r_norm(self) -> float:
        return self.radius[self.tool] / float(max(self.base.shape[:2]))

    def _decimate(self, pts_u, step_px):
        if self.base is None or not pts_u:
            return pts_u
        h, w = self.base.shape[:2]
        out = [pts_u[0]]
        for p in pts_u[1:]:
            if np.hypot((p[0] - out[-1][0]) * w, (p[1] - out[-1][1]) * h) >= step_px:
                out.append(p)
        if len(pts_u) > 1 and out[-1] is not pts_u[-1]:
            out.append(pts_u[-1])
        return [[round(float(x), 5), round(float(y), 5)] for x, y in out]

    # ---------------------------------------------------------- ratón sobre el lienzo
    def _mouse(self, kind, x, y, e) -> bool:
        if kind == "cursor":
            self.canvas.setCursor(Qt.OpenHandCursor if self.tool == "move" else Qt.CrossCursor)
            return True
        if self.base is None or self.doc is None:
            return False
        if kind == "wheel":
            lay = self._layer()
            if not lay or lay["kind"] not in MOVABLE:
                return False
            if lay.get("quad"):  # transformada: la rueda la escala desde su centro
                self._scale_quad(lay, 1.04 ** x)
                self.request()
                self._prop_timer.start(500)
                return True
            k, lo, hi = ("size", 0.5, 20.0) if lay["kind"] == "text" else ("width", 1.0, 150.0)
            lay[k] = round(min(hi, max(lo, float(lay.get(k, 10)) * (1.08 ** x))), 2)
            self.request()
            self._prop_timer.start(500)
            self._show_props()
            return True
        alt = bool(e.modifiers() & Qt.AltModifier)
        right = (e.buttons() & Qt.RightButton) if kind == "move" else (e.button() == Qt.RightButton)
        xu, yu = self._to_u(x, y)
        t = self.tool
        if kind == "press" and getattr(self, "_eyedrop", False):
            self._eyedrop = False
            self.canvas.setCursor(Qt.OpenHandCursor if t == "move" else Qt.CrossCursor)
            h, w = self.base.shape[:2]
            px, py = int(min(max(xu, 0), 1) * (w - 1)), int(min(max(yu, 0), 1) * (h - 1))
            c = self.base[max(0, py - 2):py + 3, max(0, px - 2):px + 3].reshape(-1, 3).mean(0)
            self.status.setText("")
            self._set_prop("key_color", "#" + "".join(f"{int(v * 255 + 0.5):02x}" for v in c), True)
            self._set_prop("detect", "color", True)
            self._show_props()
            return True
        if kind == "hover":
            if t == "clone":
                self.canvas.marker = self._from_u(xu + self.clone_off[0], yu + self.clone_off[1]) \
                    if self.clone_off else (self._from_u(*self.clone_pick) if self.clone_pick else None)
            return False
        if kind == "dbl":
            return t not in ("move", "hand")
        if t == "move":
            return self._move_overlay(kind, x, y, e)
        if t == "hand":
            return False  # el lienzo desplaza la vista
        if t == "zoom":
            if kind == "press":
                c = self.canvas
                c.zoom.wheel(-120 if alt else 120, e.position(), c._area(), c.pm.size())
                c._zoomed()
            return True
        if t == "eyedrop":
            if kind in ("press", "move") and self._last_img is not None:
                h, w = self._last_img.shape[:2]
                px, py = int(min(max(x, 0), 1) * (w - 1)), int(min(max(y, 0), 1) * (h - 1))
                c = self._last_img[max(0, py - 1):py + 2, max(0, px - 1):px + 2].reshape(-1, 3).mean(0)
                self.swatches.fg = QColor(*[int(v * 255 + 0.5) for v in c])
                self.swatches.update()
            return True
        if t == "type":
            if kind == "press":
                self._new_text_at(x, y)
            return True
        if t in SELECT_TOOLS:
            return self._select_mouse(kind, x, y, xu, yu, e)
        if kind == "press":
            if t == "clone" and alt:
                self.clone_pick, self.clone_off = (xu, yu), None
                self.canvas.marker = (x, y)
                self.status.setText("Origen del tampón elegido: ahora pinta donde quieras copiarlo.")
                self.canvas.update()
                return True
            if t == "clone" and not self.clone_pick and not self.clone_off:
                self.status.setText("Tampón: primero Alt+clic sobre la zona que quieres copiar.")
                return True
            self._erase = bool(right or (alt and t == "skin"))
            if t == "clone" and self.clone_pick:
                # como Photoshop con «Alineado»: el origen sigue al pincel con el mismo desplazamiento
                self.clone_off = [self.clone_pick[0] - xu, self.clone_pick[1] - yu]
                self.clone_pick = None
            self.stroke_u = [(xu, yu)]
            self.canvas.stroke = [(x, y)]
            self.canvas.update()
            return True
        if kind == "move" and self.stroke_u:
            self.stroke_u.append((xu, yu))
            self.canvas.stroke.append((x, y))
            if t == "clone" and self.clone_off:
                self.canvas.marker = self._from_u(xu + self.clone_off[0], yu + self.clone_off[1])
            return True
        if kind == "release" and self.stroke_u:
            self._commit_stroke()
            return True
        return False

    def _commit_stroke(self):
        t = self.tool
        r = self._r_norm()
        rp = self.radius[t]
        pts = self._decimate(self.stroke_u, max(1.0, rp / (4 if t != "liquify" else 3)))
        self.stroke_u = []
        self.canvas.stroke = []
        self.status.setText("")
        sel = self._layer()
        if t == "skin" and sel and (sel.get("mask") or sel["kind"] in ("adjust", "image")):
            # máscara de capa: blanco = se ve el efecto; clic derecho / Alt pinta negro (lo oculta)
            sel.setdefault("mask", {"shapes": [{"type": "all", "op": "add"}]})
            sign = -1 if getattr(self, "_erase", False) else 1
            sel["mask"].setdefault("strokes", [])
            sel["mask"]["strokes"] += [[p[0], p[1], round(r, 5), sign] for p in self._decimate(pts, max(1.0, rp / 2))]
            return self._commit(f"{sel['name']}: máscara")
        lay = sel if t == "skin" and sel and sel["kind"] == "background" else self._tool_layer(TOOL_LAYER[t])
        if t in ("heal", "clone", "fill"):
            op = {"id": uuid.uuid4().hex[:6], "tool": t, "r": round(r, 5), "pts": pts,
                  "src": [round(self.clone_off[0], 5), round(self.clone_off[1], 5)] if t == "clone" else None}
            if t == "clone":
                op["hard"] = self.hardness["clone"] / 100.0
            lay["ops"].append(op)
            label = {"heal": "Corrector", "clone": "Tampón", "fill": "Relleno"}[t]
            if t == "fill":
                op["ai"] = True  # con el modelo LaMa si está (si no, relleno clásico)
                self._ensure_lama()
        elif t == "skin":
            sign = -1 if getattr(self, "_erase", False) else 1
            lay.setdefault("strokes", [])
            lay["strokes"] += [[p[0], p[1], round(r, 5), sign] for p in self._decimate(pts, max(1.0, rp / 2))]
            label = f"{lay['name']}: {'quitar' if sign < 0 else 'añadir'} zona"
        elif t == "light":
            lay["strokes"].append({"mode": self.mode["light"], "r": round(r, 5),
                                   "s": self.strength["light"] / 100.0, "pts": pts})
            label = dict(LIGHT_MODES)[self.mode["light"]]
        else:
            lay["strokes"].append({"mode": self.mode["liquify"], "r": round(r, 5),
                                   "s": self.strength["liquify"] / 100.0, "pts": pts})
            label = "Licuar: " + dict(LIQ_MODES)[self.mode["liquify"]].lower()
        self._commit(label)

    def _move_overlay(self, kind, x, y, e=None) -> bool:
        lay = self._layer()
        if not lay or lay["kind"] not in MOVABLE:
            return False
        if lay.get("quad"):
            return self._quad_mouse(lay, kind, x, y, e)
        if kind == "press":
            self._drag = (x, y, lay.get("x", 0.5), lay.get("y", 0.5))
            return True
        if kind == "move" and self._drag:
            x0, y0, lx, ly = self._drag
            lay["x"] = round(min(1.0, max(0.0, lx + x - x0)), 4)
            lay["y"] = round(min(1.0, max(0.0, ly + y - y0)), 4)
            self.request()
            return True
        if kind == "release" and self._drag:
            self._drag = None
            self._commit("Mover " + lay["name"].lower())
            return True
        return False

    # ---------------------------------------------------------- transformación libre (4 esquinas)
    def _frame_shape(self):
        fx, fy, fw, fh = EE._frame(self.base.shape, self.box)
        return fh, fw

    def free_transform(self):
        """Ctrl+T: asas en las esquinas de la imagen elegida (deformar, perspectiva, escalar y mover)."""
        lay = self._layer()
        if not lay or lay["kind"] != "image" or self.base is None:
            self.status.setText("Transformación libre: elige antes una capa de imagen en Capas.")
            return
        if not lay.get("quad"):
            lay["quad"] = EE.image_quad(self._frame_shape(), lay)
            self._commit("Transformación libre")
        self._tf_on = True
        self.set_tool("move")
        self._sync_quad()
        self._show_props()
        self.status.setText("Arrastra una esquina para deformar o dar perspectiva (Mayús: escalar desde el "
                            "centro); dentro del marco, mover; Ctrl+rueda, tamaño. Intro termina.")

    def end_transform(self):
        if getattr(self, "_tf_on", False):
            self._tf_on = False
            self._sync_quad()
            self.status.setText("")

    def reset_transform(self):
        lay = self._layer()
        if lay and lay.get("quad"):
            lay.pop("quad")
            self._tf_on = False
            self._commit("Quitar la transformación")

    def _sync_quad(self):
        lay = self._layer()
        on = bool(lay and lay.get("quad") and getattr(self, "_tf_on", False) and self.tool == "move")
        self.canvas.quad = [tuple(q) for q in lay["quad"]] if on else None
        self.canvas.update()

    def _scale_quad(self, lay, f, ref=None):
        q = np.float64(ref if ref is not None else lay["quad"])
        c = q.mean(0)
        lay["quad"] = np.round(c + (q - c) * f, 5).tolist()

    def _quad_mouse(self, lay, kind, x, y, e) -> bool:
        if kind == "press":
            self._tf_on = True
            r = self.canvas.img_rect
            pts = [(qx * r.width(), qy * r.height()) for qx, qy in lay["quad"]]
            d = [np.hypot(px - x * r.width(), py - y * r.height()) for px, py in pts]
            k = int(np.argmin(d))
            corner = k if d[k] <= HANDLE_PX + 3 else None
            shift = bool(e is not None and e.modifiers() & Qt.ShiftModifier)
            self._drag = ("quad", corner, shift, x, y, copy.deepcopy(lay["quad"]))
            self._sync_quad()
            return True
        if kind == "move" and self._drag and self._drag[0] == "quad":
            _, corner, shift, x0, y0, q0 = self._drag
            if corner is None:  # mover todo
                lay["quad"] = [[round(qx + x - x0, 5), round(qy + y - y0, 5)] for qx, qy in q0]
            elif shift:  # escalar proporcional desde el centro
                q = np.float64(q0)
                c = q.mean(0)
                d0 = np.linalg.norm(q[corner] - c)
                d1 = np.linalg.norm(np.float64([x, y]) - c)
                self._scale_quad(lay, max(0.02, d1 / max(1e-6, d0)), q0)
            else:  # mover una esquina: deformar / perspectiva
                lay["quad"] = copy.deepcopy(q0)
                lay["quad"][corner] = [round(x, 5), round(y, 5)]
            self._sync_quad()
            self.request()
            return True
        if kind == "release" and self._drag and self._drag[0] == "quad":
            corner = self._drag[1]
            self._drag = None
            self._commit("Transformar " + lay["name"].lower() if corner is not None else "Mover " + lay["name"].lower())
            return True
        return False

    # ---------------------------------------------------------- máscara de recorte
    def _clip_base(self, lay):
        """La capa de imagen a la que se recortaría `lay` (la primera de debajo que no esté recortada)."""
        pix = [l for l in self.doc["layers"] if l["kind"] not in EE.OVERLAY] if self.doc else []
        if lay not in pix:
            return None
        for l in reversed(pix[:pix.index(lay)]):
            if l["kind"] == "image" and not l.get("clip"):
                return l
            if not (l.get("clip") and l["kind"] in EE.CLIPPABLE):
                return None
        return None

    def toggle_clip(self, on=None):
        lay = self._layer()
        if not lay or lay["kind"] not in EE.CLIPPABLE:
            self.status.setText("Máscara de recorte: elige una capa de ajuste o de imagen que esté encima de "
                                "una imagen.")
            return self._show_props()
        on = (not lay.get("clip")) if on is None else on
        base = self._clip_base(lay)
        if on and base is None:
            self.status.setText("Para recortar, la capa de justo debajo tiene que ser una imagen (o estar ya "
                                "recortada a una).")
            return self._show_props()
        if bool(lay.get("clip")) == on:
            return
        if on:
            lay["clip"] = True
        else:
            lay.pop("clip", None)
        self._commit(("Recortar a " + base["name"]) if on else "Soltar máscara de recorte")

    # ---------------------------------------------------------- lienzo en blanco
    def new_canvas(self):
        """Archivo › Nuevo lienzo: un PNG de un color, del tamaño elegido, para montar encima imágenes."""
        if not self.folder:
            self.status.setText("Abre primero una carpeta: el lienzo se guarda con sus ediciones.")
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit
        dlg = QDialog(self)
        dlg.setWindowTitle("Nuevo lienzo")
        form = QFormLayout(dlg)
        name = QLineEdit(self.prefs.get("canvas_name", "Montaje"))
        preset = QComboBox()
        for label, w, h in EE.CANVAS_PRESETS:
            preset.addItem(label, (w, h))
        preset.addItem("Personalizado", None)
        wsp, hsp = QSpinBox(), QSpinBox()
        for sp in (wsp, hsp):
            sp.setRange(16, 20000)
            sp.setSuffix(" px")
        color = {"v": self.prefs.get("canvas_color", "#ffffff")}
        bcol = _btn("", "ghost", "Color de fondo del lienzo")

        def paint_btn():
            bcol.setText(color["v"])
            bcol.setStyleSheet(f"QPushButton {{ border-left: 18px solid {color['v']}; }}")

        def pick():
            c = QColorDialog.getColor(QColor(color["v"]), dlg, "Color del lienzo")
            if c.isValid():
                color["v"] = c.name()
                paint_btn()

        def on_preset(i):
            v = preset.itemData(i)
            if v:
                wsp.setValue(v[0])
                hsp.setValue(v[1])

        bcol.clicked.connect(pick)
        preset.currentIndexChanged.connect(on_preset)
        last = self.prefs.get("canvas_size")
        idx = next((i for i, (_, w, h) in enumerate(EE.CANVAS_PRESETS) if last and [w, h] == list(last)), None)
        preset.setCurrentIndex(idx if idx is not None else (len(EE.CANVAS_PRESETS) if last else 0))
        on_preset(preset.currentIndex())
        if last and idx is None:
            wsp.setValue(int(last[0]))
            hsp.setValue(int(last[1]))
        wsp.valueChanged.connect(lambda _: preset.setCurrentIndex(preset.count() - 1)
                                 if preset.itemData(preset.currentIndex()) and
                                 preset.itemData(preset.currentIndex())[0] != wsp.value() else None)
        hsp.valueChanged.connect(lambda _: preset.setCurrentIndex(preset.count() - 1)
                                 if preset.itemData(preset.currentIndex()) and
                                 preset.itemData(preset.currentIndex())[1] != hsp.value() else None)
        paint_btn()
        form.addRow("Nombre", name)
        form.addRow("Tamaño", preset)
        form.addRow("Ancho", wsp)
        form.addRow("Alto", hsp)
        form.addRow("Fondo", bcol)
        form.addRow(self._label("Se guarda con las ediciones de esta carpeta (no aparece en Events). Arrastra "
                                "imágenes desde el Explorador para montar encima."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Crear")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        self.prefs.update(canvas_name=name.text().strip() or "Montaje", canvas_color=color["v"],
                          canvas_size=[wsp.value(), hsp.value()])
        self._save_prefs()
        try:
            path = EE.new_canvas(self.folder, wsp.value(), hsp.value(), color["v"], name.text())
        except OSError as e:
            self.status.setText(f"No se pudo crear el lienzo: {e}")
            return
        self._add_item(path)
        self.open_photo(path)
        self.status.setText("Lienzo creado: arrastra imágenes desde el Explorador para montar encima.")

    def _add_item(self, path):
        it = Item(path)
        self.items.append(it)
        li = QListWidgetItem()
        li.setData(ROLE, len(self.items) - 1)
        li.setSizeHint(QSize(126, 96))
        self.film.addItem(li)
        it.thumb = quick_thumb(path)
        self.lib_info.setText(f"{os.path.basename(self.folder)} · {len(self.items)} fotos")
        self.film.viewport().update()

    # ---------------------------------------------------------- capas
    def _layer(self):
        if not self.doc or not self.layer_id:
            return None
        return next((l for l in self.doc["layers"] if l["id"] == self.layer_id), None)

    def _tool_layer(self, kind):
        lay = self._layer()
        if lay and lay["kind"] == kind:
            return lay
        lay = next((l for l in reversed(self.doc["layers"]) if l["kind"] == kind), None)
        if lay is None:
            lay = EE.add_layer(self.doc, kind)
        self.layer_id = lay["id"]
        return lay

    def add_layer(self, kind):
        if not self.doc:
            return
        kw = {}
        if kind == "text":
            kw = {k: self.prefs[k] for k in ("text", "font", "color") if self.prefs.get(k)}
        elif kind == "logo" and self.prefs.get("logo") and os.path.isfile(self.prefs["logo"]):
            kw = {"path": self.prefs["logo"]}
        lay = EE.add_layer(self.doc, kind, **kw)
        self.layer_id = lay["id"]
        self._commit("Nueva capa: " + lay["name"].lower())
        if kind == "logo" and not lay.get("path"):
            self._pick_logo()
        if kind == "text":
            self.txt.setFocus()
            self.txt.selectAll()

    def delete_layer(self):
        lay = self._layer()
        if not lay:
            return
        self.doc["layers"] = [l for l in self.doc["layers"] if l["id"] != lay["id"]]
        self.layer_id = self.doc["layers"][-1]["id"] if self.doc["layers"] else None
        self._commit("Eliminar capa: " + lay["name"].lower())

    def move_layer(self, d):
        """d = +1 sube (hacia el final de la lista del documento), -1 baja. Texto y logo siempre encima."""
        lay = self._layer()
        if not lay:
            return
        L = self.doc["layers"]
        i = L.index(lay)
        j = i + d
        if j < 0 or j >= len(L) or (L[j]["kind"] in EE.OVERLAY) != (lay["kind"] in EE.OVERLAY):
            return
        L[i], L[j] = L[j], L[i]
        self._commit("Ordenar capas")

    def _layer_info(self, l) -> dict:
        n = {"retouch": len(l.get("ops", [])), "light": len(l.get("strokes", [])),
             "liquify": len(l.get("strokes", []))}.get(l["kind"])
        if l["kind"] == "adjust":
            sub = A.ADJUSTMENTS[l.get("adj", "curves")][0]
        else:
            sub = {"retouch": "Retoque", "skin": "Piel", "light": "Ojos y luz", "liquify": "Licuar",
                   "background": "Fondo (IA)", "text": "Texto", "logo": "Logo", "image": "Imagen"}.get(l["kind"], "")
        if n:
            sub += " · " + _count(n, "trazo")
        if l.get("blend", "normal") != "normal":
            sub += " · " + A.BLEND_MODES.get(l["blend"], "")
        if l.get("opacity", 100) < 100:
            sub += f" · {l['opacity']} %"
        thumb = None
        if l["kind"] in ("image", "logo"):
            key = ("img", l.get("path"))
            thumb = self._thumbs.get(key)
            if thumb is None:
                thumb = self._thumbs[key] = UI.image_thumb(l.get("path", ""))
        if thumb is None:
            thumb = self._thumbs.get(("glyph", l["kind"]))
            if thumb is None:
                thumb = self._thumbs[("glyph", l["kind"])] = UI._glyph_icon(l["kind"])
        if l.get("quad"):
            sub += " · transformada"
        return {"id": l["id"], "name": l["name"], "subtitle": sub, "visible": l.get("visible", True),
                "thumb": thumb, "mask": bool(l.get("mask")), "clip": bool(l.get("clip"))}

    def _refresh_layers(self):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        layers = list(reversed(self.doc["layers"])) if self.doc else []
        for l in layers:
            it = QListWidgetItem()
            it.setData(UI.ROLE_LAYER, self._layer_info(l))
            it.setData(ROLE, l["id"])
            it.setSizeHint(QSize(200, 40))
            self.layer_list.addItem(it)
        if self.doc is not None:  # «Fondo»: la foto revelada, bloqueada (como en PhotoCraft)
            bthumb = self._thumbs.get(("base", id(self.base)))
            if bthumb is None and self.base is not None:
                bthumb = self._thumbs[("base", id(self.base))] = UI.np_thumb(EE.apply_crop(self.base, self.box))
            it = QListWidgetItem()
            it.setData(UI.ROLE_LAYER, {"id": None, "name": "Fondo", "subtitle": "Foto revelada", "visible": True,
                                       "thumb": bthumb, "background_row": True, "locked": True})
            it.setData(ROLE, None)
            it.setSizeHint(QSize(200, 40))
            self.layer_list.addItem(it)
        row = next((i for i, l in enumerate(layers) if l["id"] == self.layer_id), len(layers) if self.doc else -1)
        self.layer_list.setCurrentRow(row)
        self.layer_list.blockSignals(False)
        self._show_props()
        self._update_chrome()

    def toggle_visible(self, lid):
        lay = next((l for l in self.doc["layers"] if l["id"] == lid), None) if self.doc else None
        if lay is not None:
            lay["visible"] = not lay.get("visible", True)
            self._commit(("Mostrar " if lay["visible"] else "Ocultar ") + lay["name"].lower())

    def _select_layer(self, lid):
        self.layer_id = lid
        self._refresh_layers()

    def _layer_selected(self, row):
        self._tf_on = False
        it = self.layer_list.item(row)
        self.layer_id = it.data(ROLE) if it else None
        self._show_props()
        lay = self._layer()
        if lay:  # al elegir una capa de herramienta se activa su herramienta
            tool = {"retouch": None, "skin": "skin", "light": "light", "liquify": "liquify",
                    "background": "skin"}.get(lay["kind"])
            if lay["kind"] == "adjust" and self.tool not in ("skin",) + SELECT_TOOLS:
                tool = None
            if lay["kind"] in MOVABLE and self.tool != "skin":
                tool = "move"
            if tool and tool != self.tool:
                self.set_tool(tool)

    def _show_props(self):
        lay = self._layer()
        kind = lay["kind"] if lay else "none"
        self.props_title.setText(lay["name"] if lay else "Fondo")
        for name, w in self._pages.items():
            w.setVisible(name == kind)
        self.mask_info.setVisible(bool(lay and lay.get("mask")))
        self.blend_cb.setEnabled(lay is not None)
        self.clip_chk.blockSignals(True)
        self.clip_chk.setChecked(bool(lay and lay.get("clip")))
        self.clip_chk.setEnabled(bool(lay and lay["kind"] in EE.CLIPPABLE and self._clip_base(lay) is not None))
        self.clip_chk.blockSignals(False)
        if lay:
            self.blend_cb.blockSignals(True)
            self.blend_cb.setCurrentIndex(max(0, list(A.BLEND_MODES).index(lay.get("blend", "normal"))))
            self.blend_cb.blockSignals(False)
        if kind == "adjust":
            key = (lay["id"], self.hpos if self._adj_key is None else self._adj_key[1])
            if self._adj_key is None or self._adj_key[0] != lay["id"] or self._adj_force:
                img = self._last_img if self._last_img is not None else self.base
                self.adj_panel.load(lay.get("adj", "curves"), lay.get("p") or {}, img)
                self._adj_key = key
                self._adj_force = False
        self.canvas.box = self.boxes.get(self.layer_id) if lay and kind in MOVABLE else None
        self._sync_quad()
        self.canvas.update()
        self.opacity.setEnabled(lay is not None)
        if not lay:
            self.opacity.set_value(100)
            return
        self.opacity.set_value(lay.get("opacity", 100))
        if kind == "retouch":
            self.retouch_info.setText(_count(len(lay["ops"]), "retoque") + " en esta capa.")
        elif kind == "light":
            self.light_info.setText(_count(len(lay["strokes"]), "trazo") + " en esta capa.")
        elif kind == "liquify":
            self.liq_info.setText(_count(len(lay["strokes"]), "trazo") + " en esta capa.")
        elif kind == "skin":
            self.skin_amount.set_value(lay["amount"])
            self.skin_texture.set_value(lay["texture"])
            self.skin_auto.blockSignals(True)
            self.skin_auto.setChecked(lay.get("auto", True))
            self.skin_auto.blockSignals(False)
        elif kind == "background":
            det = lay.get("detect", "person")
            self.bg_detect.blockSignals(True)
            self.bg_detect.setCurrentIndex(list(EE.DETECTS).index(det) if det in EE.DETECTS else 0)
            self.bg_detect.blockSignals(False)
            self.bg_color_box.setVisible(det == "color")
            self.bg_tol.set_value(lay.get("tol", 12))
            self.bg_soft.set_value(lay.get("soft", 8))
            self.bg_contig.blockSignals(True)
            self.bg_contig.setChecked(bool(lay.get("contiguous", False)))
            self.bg_contig.blockSignals(False)
            kc = lay.get("key_color")
            self.bg_pick.setStyleSheet(f"QPushButton {{ border-left: 14px solid {kc}; }}" if kc else "")
            self.bg_mode.blockSignals(True)
            self.bg_mode.setCurrentIndex(max(0, [k for k, _ in BG_MODES].index(lay.get("mode", "blur"))))
            self.bg_mode.blockSignals(False)
            self.bg_blur.set_value(lay.get("blur", 40))
            self.bg_blur.setVisible(lay.get("mode") == "blur")
            self.bg_color.setVisible(lay.get("mode") == "color")
        elif kind == "text":
            if self.txt.toPlainText() != lay.get("text", ""):
                self.txt.blockSignals(True)
                self.txt.setPlainText(lay.get("text", ""))
                self.txt.blockSignals(False)
            if self.txt_font.count() == 0:
                self.txt_font.blockSignals(True)
                self.txt_font.addItems(list(EE.list_fonts()))
                self.txt_font.blockSignals(False)
            self.txt_font.blockSignals(True)
            self.txt_font.setCurrentText(lay.get("font", "Arial"))
            self.txt_font.blockSignals(False)
            self.txt_size.set_value(lay.get("size", 3))
            self.txt_shadow.blockSignals(True)
            self.txt_shadow.setChecked(lay.get("shadow", True))
            self.txt_shadow.blockSignals(False)
            self.txt_color.setStyleSheet(f"QPushButton {{ border-left: 14px solid {lay.get('color', '#ffffff')}; }}")
        elif kind == "image":
            self.img_name.setText(os.path.basename(lay.get("path", "")))
            self.img_w.set_value(lay.get("width", 40))
            self.img_angle.set_value(lay.get("angle", 0))
            self.img_flip.blockSignals(True)
            self.img_flip.setChecked(bool(lay.get("flip")))
            self.img_flip.blockSignals(False)
            self.img_cut.blockSignals(True)
            self.img_cut.setCurrentIndex(max(0, list(EE.CUTS).index(lay.get("cut", "none"))))
            self.img_cut.blockSignals(False)
            self.img_tol.set_value(lay.get("tol", 12))
            self.img_tol.setVisible(lay.get("cut") == "color")
            tf = bool(lay.get("quad"))
            for w in (self.img_w, self.img_angle, self.img_pos):
                w.setEnabled(not tf)
            self.img_tf_reset.setVisible(tf)
            self.img_tf_info.setVisible(tf)
        elif kind == "logo":
            self.logo_name.setText(os.path.basename(lay.get("path", "")) or "Sin logo")
            self.logo_w.set_value(lay.get("width", 16))
            self.logo_ko.blockSignals(True)
            self.logo_ko.setChecked(bool(lay.get("knockout")))
            self.logo_ko.blockSignals(False)
            self.logo_tol.set_value(lay.get("tol", 12))
            self.logo_tol.setVisible(bool(lay.get("knockout")))

    def _set_prop(self, key, value, final=True, delay=False):
        lay = self._layer()
        if not lay or lay.get(key) == value:
            return
        lay[key] = value
        if lay["kind"] == "image" and key == "cut":
            self.img_tol.setVisible(value == "color")
        if lay["kind"] == "logo" and key == "knockout":
            self.logo_tol.setVisible(bool(value))
        if lay["kind"] == "text" and key in ("text", "font", "color"):
            self.prefs[key] = value
            self._save_prefs()
        if lay["kind"] == "background":
            self.bg_blur.setVisible(lay.get("mode") == "blur")
            self.bg_color.setVisible(lay.get("mode") == "color")
            self.bg_color_box.setVisible(lay.get("detect") == "color")
        self.request()
        if final:
            self._prop_timer.start(450 if delay else 0)
        elif delay:
            self._prop_timer.start(700)

    def _set_position(self, data):
        lay = self._layer()
        if not lay or not data:
            return
        k, x, y = data
        lay.update(anchor=k, x=x, y=y)
        self._commit("Posición")

    def _pick_color(self, key):
        lay = self._layer()
        if not lay:
            return
        c = QColorDialog.getColor(QColor(lay.get(key, "#ffffff")), self, "Color")
        if c.isValid():
            self._set_prop(key, c.name(), True)
            self._show_props()

    def _pick_logo(self):
        lay = self._layer()
        if not lay or lay["kind"] != "logo":
            return
        p, _ = QFileDialog.getOpenFileName(self, "Elegir logo", os.path.dirname(self.prefs.get("logo", "")),
                                           "Imágenes (*.png *.jpg *.jpeg *.webp)")
        if p:
            self.prefs["logo"] = p
            self._save_prefs()
            self._set_prop("path", p, True)
            self._show_props()

    def remove_background(self):
        """Recortar el sujeto a transparente. Si es un JPG/PNG (un logo, un producto), por color o como objeto."""
        if not self.doc:
            return
        sel = self._layer()
        if sel and sel["kind"] == "image":  # la capa de imagen elegida: persona (IA) u objeto
            cut = "object"
            im = EE._cutout(sel.get("path") or "", "none", 12)
            if im is not None:
                small = im.convert("RGB")
                small.thumbnail((512, 512))
                pr = EE.person_probs(np.asarray(small, np.float32) / 255.0, "quitar:" + sel["path"])
                if pr is not None and float((1 - pr[..., 0]).mean()) > 0.05:
                    cut = "person"
            sel["cut"] = cut
            self._commit("Quitar fondo: " + sel["name"].lower())
            self.status.setText("Fondo quitado (" + EE.CUTS[cut].lower() + "). Si no acierta, cambia el modo en "
                                "Propiedades o pinta su máscara con el pincel de máscara (B).")
            return
        lay = next((l for l in self.doc["layers"] if l["kind"] == "background"), None)
        if lay is None:
            lay = EE.add_layer(self.doc, "background")
            if self.cur and not E.is_raw(self.cur.path) and self.base is not None:
                # fondo casi liso en el borde (logo, fondo de estudio): por color; si no, como objeto
                edge = np.concatenate([self.base[:4].reshape(-1, 3), self.base[-4:].reshape(-1, 3),
                                       self.base[:, :4].reshape(-1, 3), self.base[:, -4:].reshape(-1, 3)])
                lay["detect"] = "color" if float(edge.std(0).max()) < 0.06 else "object"
        lay["mode"] = "transparent"
        self.layer_id = lay["id"]
        self.fmt.setCurrentIndex(list(EE.FORMATS).index("png"))
        self._commit("Quitar fondo")

    # ---------------------------------------------------------- selecciones (como PhotoCraft / Photoshop)
    def _select_mouse(self, kind, x, y, xu, yu, e) -> bool:
        mods = e.modifiers() if e is not None else Qt.NoModifier
        op = "add" if mods & Qt.ShiftModifier else ("sub" if mods & Qt.AltModifier else self.sel_mode)
        t = self.tool
        if t == "subject":
            if kind == "press":
                self.select_subject(op)
            return True
        if kind == "press":
            self._sel_op = op
            self._sel_start = (x, y, xu, yu)
            self.canvas.shape = ("lasso", [(x, y)]) if t == "lasso" else (self.sel_shape, x, y, x, y)
            self._lasso_u = [(xu, yu)]
            self.canvas.update()
            return True
        if kind == "move" and self.canvas.shape:
            if t == "lasso":
                self.canvas.shape[1].append((x, y))
                self._lasso_u.append((xu, yu))
            else:
                x0, y0 = self._sel_start[:2]
                if mods & Qt.ShiftModifier and not getattr(self, "_sel_op", "") == "add":
                    d = max(abs(x - x0), abs(y - y0))
                    x, y = x0 + d * np.sign(x - x0), y0 + d * np.sign(y - y0)
                self.canvas.shape = (self.sel_shape, x0, y0, x, y)
            self.canvas.update()
            return True
        if kind == "release" and self.canvas.shape:
            shape = self.canvas.shape
            self.canvas.shape = None
            if t == "lasso":
                pts = self._lasso_u
                if len(pts) >= 3:
                    self._apply_selection({"type": "lasso", "pts": [[round(a, 5), round(b, 5)] for a, b in pts]},
                                          self._sel_op)
            else:
                _, x0, y0, x1, y1 = shape
                if abs(x1 - x0) < 0.003 or abs(y1 - y0) < 0.003:
                    if self._sel_op == "new":
                        self.deselect()  # clic sin arrastrar: deselecciona, como Photoshop
                    return True
                u0, u1 = self._to_u(x0, y0), self._to_u(x1, y1)
                self._apply_selection({"type": self.sel_shape, "box": [round(u0[0], 5), round(u0[1], 5),
                                                                        round(u1[0], 5), round(u1[1], 5)]},
                                      self._sel_op)
            return True
        return False

    def _apply_selection(self, shape: dict, op: str):
        shape = dict(shape, op="add" if op == "new" else op)
        if op == "new" or not self.sel:
            if op == "sub":
                return
            spec = {"shapes": [shape], "feather": 0.0}
        else:
            spec = copy.deepcopy(self.sel)
            spec["shapes"].append(shape)
        if self.sel_feather and op == "new":
            spec["feather"] = round(self.sel_feather / max(self.base.shape[:2]), 5)
        self.set_selection(spec)

    def set_selection(self, spec):
        self.sel = spec
        self._sel_alpha = None
        if spec and self.base is not None:
            self._sel_alpha = EE.selection_mask(self.base.shape, spec, self.base, (self.cur.path + "#edits")
                                                if self.cur else "")
            self.canvas.sel_paths = UI.selection_outline(self._sel_alpha, self.box)
            if not self.canvas.sel_paths:
                self.sel = None
        else:
            self.canvas.sel_paths = []
        self.canvas.update()
        self.status.setText("Selección activa: Mayús+Supr la rellena según contenido; un ajuste la usa como "
                            "máscara." if self.sel else "")

    def select_all(self):
        if self.base is not None:
            self.set_selection({"shapes": [{"type": "all", "op": "add"}], "feather": 0.0})

    def deselect(self):
        if self.sel:
            self.set_selection(None)
        self.canvas.shape = None

    def invert_selection(self):
        if self.sel:
            spec = copy.deepcopy(self.sel)
            spec["invert"] = not spec.get("invert", False)
            self.set_selection(spec)

    def select_subject(self, op="new"):
        if self.base is None:
            return
        self.status.setText("Buscando el sujeto con IA…")
        self.status.repaint()
        self._apply_selection({"type": "subject"}, op if isinstance(op, str) else "new")

    def feather_selection(self):
        if not self.sel:
            return
        cur = int(round(float(self.sel.get("feather", 0)) * max(self.base.shape[:2])))
        v, ok = QInputDialog.getInt(self, "Calar selección", "Radio de calado (px):", cur, 0, 250)
        if ok:
            spec = copy.deepcopy(self.sel)
            spec["feather"] = round(v / max(self.base.shape[:2]), 5)
            self.set_selection(spec)

    def fill_selection(self):
        """Edición › Rellenar según contenido: borra lo seleccionado con IA (LaMa) o con el relleno clásico."""
        if not self.sel or not self.doc:
            self.status.setText("Primero selecciona lo que quieres borrar (marco, lazo o sujeto).")
            return
        lay = self._tool_layer("retouch")
        a = self._sel_alpha if self._sel_alpha is not None else EE.selection_mask(self.base.shape, self.sel, self.base)
        r = float(np.sqrt(max((a > 0.5).sum(), 1)) / max(a.shape) * 0.15)
        lay["ops"].append({"id": uuid.uuid4().hex[:6], "tool": "fill", "r": round(max(r, 0.004), 5), "pts": [],
                           "sel": copy.deepcopy(self.sel), "src": None, "ai": True})
        self._ensure_lama()
        self.deselect()
        self._commit("Rellenar según contenido")

    def _delete_key(self):
        if self.sel:
            self.fill_selection()
        else:
            self.delete_layer()

    # ---------------------------------------------------------- capas de ajuste y máscaras
    def add_adjust(self, kind):
        if not self.doc:
            return
        n = 1 + sum(1 for l in self.doc["layers"] if l["kind"] == "adjust" and l.get("adj") == kind)
        sel = self._layer()
        lay = EE.add_layer(self.doc, "adjust", above=sel["id"] if sel and sel["kind"] not in EE.OVERLAY else None,
                           adj=kind)
        lay["name"] = f"{A.ADJUSTMENTS[kind][0]} {n}"
        if self.sel:  # la selección se convierte en la máscara del ajuste (y se deselecciona)
            lay["mask"] = copy.deepcopy(self.sel)
            self.deselect()
        self.layer_id = lay["id"]
        self._adj_key = None
        self._commit("Nueva capa de ajuste: " + lay["name"])
        self.top_tabs.set_current(0)

    def _adj_changed(self, p, final):
        lay = self._layer()
        if not lay or lay["kind"] != "adjust":
            return
        lay["p"] = p
        self.request()
        if final:
            self._prop_timer.start(250)

    def _adj_reset(self):
        lay = self._layer()
        if lay and lay["kind"] == "adjust":
            lay["p"] = A.defaults(lay["adj"])
            self._adj_force = True
            self._commit("Restablecer " + lay["name"])

    def add_mask_from_selection(self):
        lay = self._layer()
        if not lay:
            self.status.setText("Elige primero una capa en el panel Capas.")
            return
        if lay["kind"] in EE.OVERLAY:
            self.status.setText("Las capas de texto y logo no llevan máscara.")
            return
        lay["mask"] = copy.deepcopy(self.sel) if self.sel else {"shapes": [{"type": "all", "op": "add"}]}
        self.deselect()
        self._commit("Añadir máscara de capa")

    def invert_mask(self):
        lay = self._layer()
        if lay and lay.get("mask"):
            lay["mask"]["invert"] = not lay["mask"].get("invert", False)
            self._commit("Invertir máscara")

    def delete_mask(self):
        lay = self._layer()
        if lay and lay.get("mask"):
            lay.pop("mask")
            self._commit("Eliminar máscara")

    def duplicate_layer(self):
        lay = self._layer()
        if not lay:
            return
        dup = copy.deepcopy(lay)
        dup["id"] = uuid.uuid4().hex[:8]
        dup["name"] = lay["name"] + " copia"
        L = self.doc["layers"]
        L.insert(L.index(lay) + 1, dup)
        self.layer_id = dup["id"]
        self._adj_key = None
        self._commit("Duplicar capa")

    def reset_photo(self):
        from PySide6.QtWidgets import QMessageBox
        if not self.doc or not self.doc["layers"]:
            return
        if QMessageBox.question(self, "Edits", "¿Quitar todas las capas de esta foto? (Se puede deshacer)") \
                == QMessageBox.Yes:
            self.doc["layers"] = []
            self.layer_id = None
            self._commit("Descartar retoque")

    def _new_text_at(self, x, y):
        if not self.doc:
            return
        kw = {k: self.prefs[k] for k in ("font",) if self.prefs.get(k)}
        lay = EE.add_layer(self.doc, "text", text="Texto", color=self.swatches.fg.name(), x=round(x, 4),
                           y=round(y, 4), anchor="lm", shadow=False, **kw)
        self.layer_id = lay["id"]
        self._commit("Nueva capa de texto")
        self.top_tabs.set_current(0)
        self.txt.setFocus()
        self.txt.selectAll()

    def _start_eyedrop(self):
        self._eyedrop = True
        self.canvas.setCursor(Qt.PointingHandCursor)
        self.status.setText("Cuentagotas: haz clic en el fondo que quieres quitar.")

    def _ensure_lama(self):
        """La primera vez que se usa el relleno se descarga el modelo de borrado con IA (en segundo plano)."""
        if self._lama_dl or EE.lama_ready():
            return
        self._lama_dl = True

        def job():
            try:
                EE.ensure_lama(lambda d, t: self.export_progress.emit(
                    d // 1_000_000, max(1, t // 1_000_000),
                    f"Descargando el modelo de borrado con IA ({d // 1_000_000} de {t // 1_000_000} MB, solo "
                    "esta vez)…"))
                self.lama_done.emit("")
            except Exception as e:
                self.lama_done.emit(f"{type(e).__name__}: {e}")

        self.pb.show()
        threading.Thread(target=job, daemon=True).start()

    def _on_lama_done(self, err):
        self._lama_dl = False
        self.pb.hide()
        if err:
            self.status.setText("No se pudo descargar el modelo de borrado (se usa el relleno clásico): " + err)
        else:
            self.status.setText("Borrado con IA listo.")
            self.request()

    def _pop_last(self, field, label):
        lay = self._layer()
        if lay and lay.get(field):
            lay[field].pop()
            self._commit(label)

    def _clear_list(self, field, label):
        lay = self._layer()
        if lay and lay.get(field):
            lay[field] = []
            self._commit(label)

    # ---------------------------------------------------------- historial (deshacer / rehacer)
    def _commit(self, label):
        if not self.doc:
            return
        self.hist = self.hist[:self.hpos + 1] + [(label, EE.snapshot(self.doc))]
        if len(self.hist) > 80:
            self.hist = [self.hist[0]] + self.hist[-79:]
        self.hpos = len(self.hist) - 1
        self._after_change()

    def _after_change(self):
        self._refresh_layers()
        self._refresh_history()
        self._save_timer.start(500)
        self.request()

    def _refresh_history(self):
        self.hist_list.clear()
        for i, (label, _) in enumerate(self.hist):
            it = QListWidgetItem(label)
            if i > self.hpos:
                it.setForeground(QColor(T.FAINT))
            self.hist_list.addItem(it)
        self.hist_list.setCurrentRow(self.hpos)

    def _history_jump(self, i):
        if 0 <= i < len(self.hist):
            self._adj_force = True
            self.hpos = i
            self.doc = EE.snapshot(self.hist[i][1])
            if not self._layer():
                self.layer_id = self.doc["layers"][-1]["id"] if self.doc["layers"] else None
            self._after_change()

    def undo(self):
        if self.hpos > 0:
            self._history_jump(self.hpos - 1)

    def redo(self):
        if self.hpos < len(self.hist) - 1:
            self._history_jump(self.hpos + 1)

    # ---------------------------------------------------------- carpeta y miniaturas
    def open_folder(self, folder: str):
        if not folder or not os.path.isdir(folder) or os.path.normcase(os.path.normpath(folder)) == \
                os.path.normcase(os.path.normpath(self.folder or "~")):
            return
        self._save_doc()
        self.folder = os.path.normpath(folder)
        import masks as M
        if not M._CFG.get("model_dir"):
            M.configure(os.path.join(self.cfg_dir, "modelos"))
        self.items = [Item(p.path) for p in E.list_photos(self.folder)] + \
            [Item(p) for p in EE.list_canvases(self.folder)]
        for it in self.items:
            it.settings = {"_edits": 1} if EE.has_edits(EE.load(self.folder, it.path)) else None
        self._pm_cache.clear()
        self.film.blockSignals(True)
        self.film.clear()
        for i, it in enumerate(self.items):
            li = QListWidgetItem()
            li.setData(ROLE, i)
            li.setSizeHint(QSize(126, 96))
            self.film.addItem(li)
        self.film.blockSignals(False)
        self.lib_info.setText(f"{os.path.basename(self.folder)} · {len(self.items)} fotos")
        self.cur = self.doc = self.base = None
        self.canvas.clear("Elige una foto")
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
        from PySide6.QtCore import QByteArray
        from PySide6.QtGui import QPixmap
        if not it.thumb:
            return None
        pm = self._pm_cache.get(it.path)
        if pm is None:
            pm = QPixmap()
            pm.loadFromData(QByteArray(it.thumb))
            pm = pm.scaled(200, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._pm_cache[it.path] = pm
        return pm

    def open_photo(self, path: str, initial: dict | None = None):
        """Abre `path` (de la carpeta abierta). `initial`: revelado de partida que manda Events."""
        if initial is not None:
            self._initial[os.path.normcase(path)] = dict(initial)
        idx = next((i for i, it in enumerate(self.items) if os.path.normcase(it.path) == os.path.normcase(path)), -1)
        if idx < 0:
            return False
        if self.cur and os.path.normcase(self.cur.path) == os.path.normcase(path):
            self.refresh()
            return True
        for row in range(self.film.count()):
            if self.film.item(row).data(ROLE) == idx:
                self.film.setCurrentRow(row)
                break
        return True

    def refresh(self):
        """Al volver a Edits: si el revelado de Studio de la foto ha cambiado, se rehace la base."""
        if self.cur and self.base is not None:
            s = EE.base_settings(self.folder, self.cur.path, self.doc)
            if s != self.base_s:
                self.select(self.film.currentRow(), force=True)

    def select(self, row, force=False):
        if row < 0 or row >= self.film.count():
            return
        it = self.items[self.film.item(row).data(ROLE)]
        if it is self.cur and not force:
            return
        self._save_doc()
        self.cur = it
        self.doc = EE.load(self.folder, it.path) or EE.new_doc()
        self.layer_id = self.doc["layers"][-1]["id"] if self.doc["layers"] else None
        self.hist, self.hpos = [("Abrir", EE.snapshot(self.doc))], 0
        self.clone_off = self.clone_pick = None
        self.sel, self._sel_alpha = None, None
        self.canvas.sel_paths = []
        self._adj_key = None
        self.boxes = {}
        self.base = None
        self._refresh_layers()
        self._refresh_history()
        self.canvas.clear("Revelando…")
        # revelado de partida: Studio > el guardado en el documento > el que manda Events > Events > automático
        s = EE.base_settings(self.folder, it.path, self.doc) if (SE.load(self.folder, it.path) is not None
                                                                 or self.doc.get("base")) else None
        if s is None:
            init = self._initial.get(os.path.normcase(it.path))
            ph = self.events_lookup(it.path)
            if init:
                s = dict(SE.DEFAULTS, **init)
            elif ph is not None and getattr(ph, "thumb", None):
                s = SE.auto_settings(ph)
        self._busy_load = it.path
        threading.Thread(target=self._load, args=(it.path, s), daemon=True).start()

    def _load(self, path, s):
        try:
            if s is None and not E.is_raw(path):
                s = dict(EE.ORIGINAL)  # un JPG, PNG o TIFF (también un logo) se retoca tal cual
            if s is None:
                lin = E.load_linear(path, half=True)
                f = PROXY_PX / max(lin.shape[:2])
                if f < 1:
                    lin = cv2.resize(lin, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
                s = EE.auto_base(lin)
            base = EE.render_base(path, s, full=False, max_px=PROXY_PX, key=path + "#edits")
            self.loaded.emit(path, base, s)
        except Exception as e:
            self.loaded.emit("ERROR:" + path, None, f"{type(e).__name__}: {e}")

    def _on_loaded(self, path, base, s):
        if path.startswith("ERROR:"):
            if self.cur and self.cur.path == path[6:]:
                self.canvas.clear("No se pudo abrir: " + str(s))
            return
        if not self.cur or self.cur.path != path:
            return
        self.base, self.base_s = base, dict(s)
        self.box = EE.crop_box(s)
        if SE.load(self.folder, path) is None:
            self.doc["base"] = {k: v for k, v in s.items() if k != "masks" or v}
            self.hist[0] = ("Abrir", EE.snapshot(self.doc))
        self.canvas.before = _pixmap(EE.apply_crop(base, self.box))
        self.canvas.pm = None
        self._refresh_layers()  # miniatura de la capa Fondo
        self.request()

    # ---------------------------------------------------------- render en segundo plano
    def request(self):
        if self.base is None or not self.doc or not self.cur:
            return
        self._req += 1
        self._jobs.put((self._req, self.cur.path, copy.deepcopy(self.doc), self.base, list(self.box)))

    def _render_loop(self):
        comp = EE.Compositor()
        while True:
            job = self._jobs.get()
            try:
                while True:
                    job = self._jobs.get_nowait()  # solo interesa la última petición
            except queue.Empty:
                pass
            if job is None:
                return
            n, path, doc, base, box = job
            key = path + "#edits"
            try:
                resolved = self._resolve_sources(comp, base, doc, key, box)
                boxes: dict = {}
                img, alpha = comp.pixel_stage(base, doc["layers"], key, box=box)
                img, alpha = EE.finish(img, alpha, doc, box)
                boxes = EE.layer_boxes(doc, base.shape, box)
                if alpha is not None:
                    img = _checker(img, alpha)
                self.rendered.emit(n, img, resolved, boxes)
            except Exception as e:
                self.rendered.emit(n, None, f"{type(e).__name__}: {e}", {})

    @staticmethod
    def _resolve_sources(comp, base, doc, key, box=None) -> list:
        """Busca el origen de los trazos del corrector y del relleno que aún no lo tienen."""
        out = []
        for lay in doc["layers"]:
            if lay["kind"] != "retouch" or not any(o.get("src") is None and o["tool"] in ("heal", "fill")
                                                   for o in lay["ops"]):
                continue
            img = EE.layer_input(comp, base, doc, lay, key, box)
            for j, op in enumerate(lay["ops"]):
                if op.get("src") is None and op["tool"] in ("heal", "fill"):
                    img_j = EE.apply_retouch(img, {"ops": lay["ops"][:j]})
                    op["src"] = EE.find_source(img_j, op) or [0.0, 0.0]
                    out.append((op.get("id"), op["src"]))
        return out

    def _on_rendered(self, n, img, resolved, boxes):
        if img is None:
            self.status.setText("Error al componer: " + str(resolved))
            return
        if resolved and self.doc:
            ids = dict(resolved)
            for lay in self.doc["layers"]:
                for op in lay.get("ops", []):
                    if op.get("src") is None and op.get("id") in ids:
                        op["src"] = ids[op["id"]]
            if self.hist:  # que el historial no vuelva a buscar el mismo origen
                for label, snap in self.hist:
                    for lay in snap["layers"]:
                        for op in lay.get("ops", []):
                            if op.get("src") is None and op.get("id") in ids:
                                op["src"] = ids[op["id"]]
            self._save_timer.start(300)
        if n < self._shown:
            return
        self._shown = n
        self.boxes = boxes
        lay = self._layer()
        self.canvas.box = boxes.get(self.layer_id) if lay and lay["kind"] in MOVABLE else None
        self._sync_quad()
        self._last_img = img
        self.canvas.set(_pixmap(img))
        self._update_chrome()
        if self.cur:
            self.cur.settings = {"_edits": 1} if EE.has_edits(self.doc) else None
            self.film.viewport().update()

    # ---------------------------------------------------------- guardar y comparar
    def _save_doc(self):
        if self.cur and self.doc is not None and self.folder:
            if EE.has_edits(self.doc) or os.path.exists(EE.doc_path(self.folder, self.cur.path)):
                try:
                    EE.save(self.folder, self.cur.path, self.doc)
                except OSError as e:
                    self.status.setText(f"No se pudo guardar: {e}")

    def toggle_before(self):
        self.set_compare("off" if self.canvas.cmp.mode == "full" else "full")

    def next_split(self):
        self.canvas.cmp.next_split()
        self.set_compare(self.canvas.cmp.mode)

    def set_compare(self, mode):
        self.canvas.cmp.mode = mode
        self.cmp_bar.set_mode(mode)
        self.canvas.update()

    # ---------------------------------------------------------- exportar
    def _out_path(self, it: Item, fmt: str) -> str:
        return os.path.join(self.folder, "Edits", f"{it.name}.{fmt}")

    def _export(self, targets):
        if not targets:
            return
        self._save_doc()
        fmt = self.fmt.currentData()
        side = {0: None, 1: 2048, 2: 1080}[self.size.currentIndex()]
        quality = self.quality.value()
        jobs = [(it, EE.load(self.folder, it.path) or EE.new_doc()) for it in targets]
        if self.cur and self.doc:
            jobs = [(it, self.doc if it is self.cur else d) for it, d in jobs]
        jobs = [(it, copy.deepcopy(d)) for it, d in jobs]
        folder = self.folder

        def job():
            for i, (it, doc) in enumerate(jobs):
                self.export_progress.emit(i, len(jobs), f"Exportando {it.name}…")
                try:
                    out = EE.export(folder, it.path, doc, self._out_path(it, fmt), fmt, quality, side)
                    if not out.endswith("." + fmt):
                        self.export_progress.emit(i, len(jobs), f"{it.name}: con transparencia, se guarda en "
                                                                f"{os.path.splitext(out)[1][1:].upper()}")
                except Exception as e:
                    self.export_progress.emit(i, len(jobs), f"Error en {it.name}: {e}")
                    continue
            self.export_progress.emit(len(jobs), len(jobs), f"Exportadas {len(jobs)} en «Edits».")

        self.pb.show()
        threading.Thread(target=job, daemon=True).start()

    def export_current(self):
        if self.cur:
            self._export([self.cur])

    def export_selected(self):
        rows = sorted({i.row() for i in self.film.selectedIndexes()})
        targets = [self.items[self.film.item(r).data(ROLE)] for r in rows]
        targets = [it for it in targets if it is self.cur or EE.has_edits(EE.load(self.folder, it.path))]
        self._export(targets)

    def _on_export_progress(self, i, n, text):
        self.pb.setMaximum(max(n, 1))
        self.pb.setValue(i)
        self.status.setText(text)
        if i >= n:
            QTimer.singleShot(2500, self.pb.hide)

    # ---------------------------------------------------------- ida y vuelta con Events
    def edit_for_events(self, path, initial: dict, on_use, on_back) -> bool:
        if not self.open_photo(path, initial):
            return False
        self._events_cb = (on_use, on_back)
        self.banner_txt.setText(f"<b>Retocando {os.path.basename(path)} para Events.</b> Al aprobar, la exportación "
                                "de Events añade esta foto retocada en «Sugeridas».")
        self.banner_wrap.show()
        return True

    def _finish_events(self, use: bool):
        if not self._events_cb:
            return
        if use and self.doc is not None:
            self.doc["_events_override"] = True
            if self.cur:
                EE.save(self.folder, self.cur.path, self.doc)
        self._save_doc()
        self.banner_wrap.hide()
        cb, self._events_cb = self._events_cb[0 if use else 1], None
        cb()

    def shutdown(self):
        self._save_doc()
        self._jobs.put(None)


def attach_studio_button(studio_view, on_click) -> QPushButton:
    """Añade «Editar en Edits» a la barra de Studio sin tocar studio.py (junto a «Exportar…»)."""
    import icons as I
    from PySide6.QtCore import QSize
    b = _btn("", "iconBtn", "Retocar esta foto en Edits: corrector, tampón, piel, licuar, texto…")
    b.setIcon(I.icon("edits", 17))
    b.setIconSize(QSize(17, 17))
    bar = studio_view.b_export.parentWidget().layout()
    bar.insertWidget(bar.indexOf(studio_view.b_export), b)
    b.clicked.connect(lambda: on_click(studio_view.cur.path) if studio_view.cur else None)
    return b
