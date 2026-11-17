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
                               QFrame, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QMenu,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

import compare as CMP
import zoomview as Z
import edits_engine as EE
import engine as E
import studio_engine as SE
import theme as T
from studio import ROLE, FilmDelegate, Item, Section, SliderRow, _btn, _pixmap, quick_thumb

PROXY_PX = 2048
# clave, icono, consejo, atajo, tipo de capa sobre la que trabaja
TOOLS = [("move", "✥", "Mover y mano: arrastra el texto o el logo seleccionado; si no, desplaza la vista", "V", None),
         ("heal", "✚", "Pincel corrector: pinta encima de granos, brillos o manchas (busca solo el parche)", "J",
          "retouch"),
         ("clone", "◎", "Tampón de clonar: Alt+clic para elegir el origen y pinta donde quieras copiarlo", "S",
          "retouch"),
         ("fill", "▨", "Relleno según contenido: pinta lo que quieras borrar (cables, carteles, gente)", "G",
          "retouch"),
         ("skin", "◐", "Pincel de máscara: añade zonas a la capa Piel o a la persona de la capa Fondo (clic "
                       "derecho o Alt: quita)", "B", "skin"),
         ("light", "☀", "Ojos y luz: aclarar ojeras, blanquear dientes y ojos, oscurecer", "O", "light"),
         ("liquify", "〰", "Licuar: empujar, inflar o encoger", "W", "liquify")]
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
    from PySide6.QtGui import QIcon, QPainterPath, QPixmap

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
        self.handler = None           # callback(tipo, x, y, evento) -> ¿consumido?
        self.space = False
        self.zoom_changed = None

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
        if self.box:
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


# ------------------------------------------------------------------ vista Edits

class EditsView(QWidget):
    rendered = Signal(int, object, object, object)  # nº de petición, imagen, fuentes resueltas, cajas
    loaded = Signal(str, object, object)             # ruta, base (sin recortar), ajustes de revelado
    thumbs_ready = Signal(int)
    export_progress = Signal(int, int, str)

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

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_tools())
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        mid.addWidget(self._build_options())
        mid.addWidget(self._build_banner())
        self.canvas = EditCanvas()
        self.canvas.handler = self._mouse
        self.canvas.zoom_changed = lambda: self.zoom_lbl.setText(self.canvas.zoom_label())
        mid.addWidget(self.canvas, 1)
        mid.addWidget(self._build_filmstrip())
        h.addLayout(mid, 1)
        h.addWidget(self._build_panels())

        self.rendered.connect(self._on_rendered)
        self.loaded.connect(self._on_loaded)
        self.thumbs_ready.connect(lambda i: self.film.viewport().update())
        self.export_progress.connect(self._on_export_progress)
        for k, fn in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo),
                      ("\\", self.toggle_before), ("Y", self.next_split), ("Ctrl+E", self.export_current),
                      ("[", lambda: self._nudge_size(1 / 1.2)), ("]", lambda: self._nudge_size(1.2)),
                      ("Ctrl+0", lambda: self.canvas.set_zoom(None)), ("Ctrl+1", lambda: self.canvas.set_zoom(1.0)),
                      ("Z", lambda: self.canvas.toggle_zoom(
                          self.canvas.cursor if self.canvas.cursor is not None
                          and self.canvas.img_rect.contains(self.canvas.cursor) else None)),
                      ("T", lambda: self.add_layer("text")), (Qt.Key_Delete, self.delete_layer)):
            QShortcut(QKeySequence(k), self, fn)
        for key, _, _, sc, _ in TOOLS:
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
        lay = EE.add_layer(self.doc, "image", **kw)
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
        w.setFixedWidth(46)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 8)
        v.setSpacing(2)
        self.tool_btn = {}
        g = QButtonGroup(self)
        for key, icon, tip, sc, _ in TOOLS:
            b = QPushButton()
            b.setIcon(tool_icon(key))
            b.setIconSize(QSize(22, 22))
            b.setObjectName("tooltab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"{tip} ({sc})")
            b.setStyleSheet("QPushButton#tooltab { padding: 9px 0; }")
            b.clicked.connect(lambda _, k=key: self.set_tool(k))
            g.addButton(b)
            v.addWidget(b)
            self.tool_btn[key] = b
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
        for x in (self.opt_size, self.opt_hard, self.opt_str, self.opt_mode):
            hb.addWidget(x)
        hb.addWidget(self.opt_hint, 1)
        self.zoom_lbl = QLabel("Encajar")
        self.zoom_lbl.setObjectName("meta")
        self.zoom_lbl.setToolTip("Rueda del ratón: zoom hacia el cursor (25 · 50 · 100 · 200 · 400 %) · Z: 100 % / "
                                 "encajar · Ctrl+0 encajar · botón central o barra espaciadora: mano")
        hb.addWidget(self.zoom_lbl)
        self.cmp_bar = CMP.CompareBar()
        self.cmp_bar.changed.connect(self.set_compare)
        hb.addWidget(self.cmp_bar)
        return bar

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
        w.setFixedHeight(142)
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 4, 10, 4)
        v.setSpacing(2)
        top = QHBoxLayout()
        self.lib_info = QLabel("Sin carpeta")
        self.lib_info.setObjectName("meta")
        self.status = QLabel("")
        self.status.setObjectName("meta")
        self.pb = QProgressBar()
        self.pb.setTextVisible(False)
        self.pb.setFixedWidth(160)
        self.pb.hide()
        top.addWidget(self.lib_info, 1)
        top.addWidget(self.status)
        top.addWidget(self.pb)
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

    def _build_panels(self):
        outer = QFrame()
        outer.setObjectName("previewPane")
        outer.setFixedWidth(320)
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        sc.setWidget(inner)
        ov.addWidget(sc)

        # Capas
        lay = Section("Capas")
        self.layer_list = QListWidget()
        self.layer_list.setObjectName("list")
        self.layer_list.setFixedHeight(170)
        self.layer_list.currentRowChanged.connect(self._layer_selected)
        self.layer_list.itemChanged.connect(self._layer_checked)
        lay.lay.addWidget(self.layer_list)
        row = QHBoxLayout()
        row.setSpacing(4)
        b_add = _btn("＋ Capa", "small", "Añadir una capa")
        menu = QMenu(b_add)
        for kind in ("retouch", "skin", "light", "liquify", "background", "text", "logo"):
            menu.addAction(EE.KINDS[kind], lambda k=kind: self.add_layer(k))
        menu.addAction("Imagen… (o arrástrala desde el Explorador)", self._pick_image)
        menu.addSeparator()
        menu.addAction("Quitar fondo (recortar a PNG)", self.remove_background)
        b_add.setMenu(menu)
        b_up = _btn("▲", "small", "Subir la capa")
        b_dn = _btn("▼", "small", "Bajar la capa")
        b_del = _btn("Eliminar", "small", "Eliminar la capa (Supr)")
        b_up.clicked.connect(lambda: self.move_layer(1))
        b_dn.clicked.connect(lambda: self.move_layer(-1))
        b_del.clicked.connect(self.delete_layer)
        for b in (b_add, b_up, b_dn, b_del):
            row.addWidget(b)
        lay.lay.addLayout(row)
        self.opacity = SliderRow("opacity", "Opacidad", 0, 100, 1, 100)
        self.opacity.changed.connect(lambda k, val, final: self._set_prop("opacity", int(val), final))
        lay.lay.addWidget(self.opacity)
        v.addWidget(lay)

        # Propiedades de la capa elegida
        self.props = Section("Propiedades")
        # páginas de propiedades: solo se ve (y solo ocupa sitio) la de la capa elegida
        self.prop_box = QWidget()
        self.prop_lay = QVBoxLayout(self.prop_box)
        self.prop_lay.setContentsMargins(0, 0, 0, 0)
        self.props.lay.addWidget(self.prop_box)
        self._pages = {}
        self._build_prop_pages()
        v.addWidget(self.props)

        his = Section("Historial", open_=False)
        self.hist_list = QListWidget()
        self.hist_list.setObjectName("list")
        self.hist_list.setFixedHeight(150)
        self.hist_list.itemClicked.connect(lambda it: self._history_jump(self.hist_list.row(it)))
        his.lay.addWidget(self.hist_list)
        v.addWidget(his)

        exp = Section("Exportar")
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
            exp.lay.addWidget(x)
        exp.lay.addLayout(qrow)
        exp.lay.addWidget(self.b_export)
        exp.lay.addWidget(self.b_export_sel)
        v.addWidget(exp)
        v.addStretch(1)
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
                                  "que quieras borrar."))
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
        for x in (self.img_name, b, self.img_w, self.img_angle, self.img_flip, self._label("Quitar el fondo"),
                  self.img_cut, self.img_tol, self.img_pos):
            lay.addWidget(x)
        lay.addWidget(self._label("Con Mover (V) la arrastras; Ctrl+rueda cambia el tamaño. El tamaño es % del "
                                  "ancho de la foto; la opacidad, en Capas."))

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
        self.opt_size.setVisible(brush)
        if brush:
            self.opt_size.set_value(self.radius[key])
        self.opt_hard.setVisible(key in self.hardness)
        if key in self.hardness:
            self.opt_hard.set_value(self.hardness[key])
        self.opt_str.setVisible(key in self.strength)
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
                               "move": "Rueda: zoom · arrastrar: mano"}.get(key, "[ ] tamaño"))
        self.canvas.brush_px = self.radius.get(key, 0) if brush else 0
        self.canvas.marker = None
        self.canvas.setCursor(Qt.OpenHandCursor if key == "move" else Qt.CrossCursor)
        # al elegir la herramienta se selecciona su capa (si existe)
        kind = TOOL_LAYER.get(key)
        if key == "skin" and self._layer() and self._layer()["kind"] == "background":
            kind = None  # el pincel de máscara sigue en la capa Fondo
        if kind and self.doc:
            lay = self._layer() if self._layer() and self._layer()["kind"] == kind else \
                next((l for l in reversed(self.doc["layers"]) if l["kind"] == kind), None)
            if lay:
                self._select_layer(lay["id"])
        self.canvas.update()

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
            if not lay or lay["kind"] not in EE.OVERLAY:
                return False
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
            return t != "move"
        if t == "move":
            return self._move_overlay(kind, x, y)
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
        lay = sel if t == "skin" and sel and sel["kind"] == "background" else self._tool_layer(TOOL_LAYER[t])
        if t in ("heal", "clone", "fill"):
            op = {"id": uuid.uuid4().hex[:6], "tool": t, "r": round(r, 5), "pts": pts,
                  "src": [round(self.clone_off[0], 5), round(self.clone_off[1], 5)] if t == "clone" else None}
            if t == "clone":
                op["hard"] = self.hardness["clone"] / 100.0
            lay["ops"].append(op)
            label = {"heal": "Corrector", "clone": "Tampón", "fill": "Relleno"}[t]
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

    def _move_overlay(self, kind, x, y) -> bool:
        lay = self._layer()
        if not lay or lay["kind"] not in EE.OVERLAY:
            return False
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

    def _refresh_layers(self):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        layers = list(reversed(self.doc["layers"])) if self.doc else []
        for l in layers:
            n = {"retouch": len(l.get("ops", [])), "light": len(l.get("strokes", [])),
                 "liquify": len(l.get("strokes", []))}.get(l["kind"])
            extra = "  ·  " + _count(n, "trazo") if n else ""
            if l.get("opacity", 100) < 100:
                extra += f"  ·  {l['opacity']} %"
            it = QListWidgetItem(l["name"] + extra)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if l.get("visible", True) else Qt.Unchecked)
            it.setData(ROLE, l["id"])
            it.setToolTip("Casilla: mostrar u ocultar la capa")
            self.layer_list.addItem(it)
        row = next((i for i, l in enumerate(layers) if l["id"] == self.layer_id), -1)
        self.layer_list.setCurrentRow(row)
        self.layer_list.blockSignals(False)
        self._show_props()

    def _select_layer(self, lid):
        self.layer_id = lid
        self._refresh_layers()

    def _layer_selected(self, row):
        it = self.layer_list.item(row)
        self.layer_id = it.data(ROLE) if it else None
        self._show_props()
        lay = self._layer()
        if lay:  # al elegir una capa de herramienta se activa su herramienta
            tool = {"retouch": None, "skin": "skin", "light": "light", "liquify": "liquify",
                    "background": "skin"}.get(lay["kind"])
            if lay["kind"] in EE.OVERLAY:
                tool = "move"
            if tool and tool != self.tool:
                self.set_tool(tool)

    def _layer_checked(self, it):
        lay = next((l for l in self.doc["layers"] if l["id"] == it.data(ROLE)), None) if self.doc else None
        if lay is not None:
            lay["visible"] = it.checkState() == Qt.Checked
            self._commit(("Mostrar " if lay["visible"] else "Ocultar ") + lay["name"].lower())

    def _show_props(self):
        lay = self._layer()
        kind = lay["kind"] if lay else "none"
        self.props.head.setText(("▾   " if self.props.head.isChecked() else "▸   ") +
                                ("Propiedades: " + lay["name"] if lay else "Propiedades"))
        self.props.title = "Propiedades: " + lay["name"] if lay else "Propiedades"
        for name, w in self._pages.items():
            w.setVisible(name == kind)
        self.canvas.box = self.boxes.get(self.layer_id) if lay and kind in EE.OVERLAY else None
        self.canvas.update()
        self.opacity.setEnabled(lay is not None)
        if not lay:
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

    def _start_eyedrop(self):
        self._eyedrop = True
        self.canvas.setCursor(Qt.PointingHandCursor)
        self.status.setText("Cuentagotas: haz clic en el fondo que quieres quitar.")

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
        self.items = [Item(p.path) for p in E.list_photos(self.folder)]
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
                resolved = self._resolve_sources(comp, base, doc, key)
                boxes: dict = {}
                img, alpha = comp.pixel_stage(base, doc["layers"], key)
                img, alpha = EE.finish(img, alpha, doc, box)
                for lay in doc["layers"]:
                    if lay["kind"] in EE.OVERLAY and EE._layer_active(lay):
                        r = EE.overlay_rgba(img.shape, lay)
                        if r:
                            h, w = img.shape[:2]
                            x0, y0, x1, y1 = r[1]
                            boxes[lay["id"]] = (x0 / w, y0 / h, x1 / w, y1 / h)
                if alpha is not None:
                    img = _checker(img, alpha)
                self.rendered.emit(n, img, resolved, boxes)
            except Exception as e:
                self.rendered.emit(n, None, f"{type(e).__name__}: {e}", {})

    @staticmethod
    def _resolve_sources(comp, base, doc, key) -> list:
        """Busca el origen de los trazos del corrector y del relleno que aún no lo tienen."""
        out = []
        for lay in doc["layers"]:
            if lay["kind"] != "retouch" or not any(o.get("src") is None and o["tool"] in ("heal", "fill")
                                                   for o in lay["ops"]):
                continue
            img = EE.layer_input(comp, base, doc, lay, key)
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
        self.canvas.box = boxes.get(self.layer_id) if lay and lay["kind"] in EE.OVERLAY else None
        self.canvas.set(_pixmap(img))
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
    b = _btn("Editar en Edits", "ghost", "Retocar esta foto en Edits: corrector, tampón, piel, licuar, texto…")
    bar = studio_view.b_export.parentWidget().layout()
    bar.insertWidget(bar.indexOf(studio_view.b_export), b)
    b.clicked.connect(lambda: on_click(studio_view.cur.path) if studio_view.cur else None)
    return b
