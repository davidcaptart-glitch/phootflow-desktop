"""Graphics: pantalla de inicio y diálogo «Crear un diseño».

- `HomePage`: cabecera con el degradado de PhootFlow, buscador, accesos rápidos por tipo de diseño, «Seguir
  diseñando» (los diseños guardados, con su miniatura y cuándo se tocaron) y «Plantillas para ti». La barra de la
  izquierda cambia entre Inicio, Proyectos (todos los diseños) y Plantillas (todas, por grupos).
- `CreateDialog`: categorías a la izquierda (Para ti, Redes sociales, Impresión…, Tamaño personalizado, Subir
  imagen) y tarjetas de tamaño con una miniatura dibujada a su proporción.

Diseño propio con los colores de PhootFlow. No hace nada por sí misma: llama a métodos del `GraphicsView` dueño
(`open_path`, `new_from_preset`, `new_from_template`, `new_custom`, `upload_new`, `open_editor`…).
"""
from __future__ import annotations

import math
import os
import unicodedata

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDoubleSpinBox, QFrame,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QScrollArea, QSizePolicy, QStyle, QStyledItemDelegate,
                               QToolButton, QVBoxLayout, QWidget)

import graphics_engine as G
import icons as I
import theme as T

TILE = "#161a25"        # fondo de las tarjetas
TILE_HOVER = "#1d2230"
# color de cada grupo (accesos rápidos y miniaturas de tamaño): degradados propios sobre el magenta y el violeta
GROUP_COLORS = {
    "Redes sociales": ("#F50D8A", "#FF6FB5"), "Impresión": ("#7B2FF7", "#A57BFF"),
    "Copias de foto": ("#E8B04A", "#F5D38A"), "Presentaciones": ("#FF7A30", "#FFB07A"),
    "Documentos": ("#2E86AB", "#6CC3E6"), "Marketing": ("#4CC38A", "#8FE3B8"),
    "Personalizado": ("#8A90A2", "#C3C7D2"),
}
HOME_QSS = f"""
QWidget#gHome, QWidget#gHomeBody {{ background: {T.CHROME}; }}
QFrame#gRail {{ background: {T.SURFACE}; border-right: 1px solid {T.LINE}; }}
QToolButton#gRailBtn {{ background: transparent; border: none; border-radius: 8px; color: {T.MUTED};
    padding: 6px 2px; font-size: 8pt; }}
QToolButton#gRailBtn:hover {{ background: {TILE_HOVER}; color: {T.TEXT}; }}
QToolButton#gRailBtn:checked {{ background: #24102b; color: {T.TEXT_STRONG}; }}
QLabel#gH2 {{ font-size: 15pt; font-weight: 600; color: {T.TEXT_STRONG}; padding-top: 6px; }}
QLabel#gEmpty {{ color: {T.MUTED}; padding: 18px 2px; }}
QPushButton#gLink {{ background: transparent; border: none; color: {T.TEXT}; font-weight: 600; padding: 4px 6px; }}
QPushButton#gLink:hover {{ color: {T.ACCENT_HOVER}; }}
QPushButton#gMore {{ background: transparent; border: 1px solid {T.LINE}; border-radius: 8px; padding: 8px 22px;
    color: {T.TEXT}; font-weight: 600; }}
QPushButton#gMore:hover {{ background: {TILE_HOVER}; }}
QPushButton#gSeg {{ background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.25);
    border-radius: 13px; padding: 4px 16px; min-height: 18px; color: white; }}
QPushButton#gSeg:checked {{ background: white; color: {T.CHROME}; font-weight: 600; }}
QLineEdit#gSearch {{ background: {T.SURFACE}; border: 1px solid {T.LINE}; border-radius: 12px; padding: 12px 16px;
    font-size: 11pt; color: {T.TEXT_STRONG}; }}
QLineEdit#gSearch:focus {{ border-color: {T.ACCENT}; }}
QToolButton#gQuick {{ background: transparent; border: none; color: {T.TEXT}; font-size: 8.5pt; padding: 2px; }}
QToolButton#gQuick:hover {{ color: white; }}
QPushButton#gChip {{ background: {TILE}; border: 1px solid {T.LINE}; border-radius: 12px; padding: 4px 14px;
    color: {T.TEXT}; }}
QPushButton#gChip:checked {{ background: #3a1030; border-color: {T.ACCENT}; color: white; }}
QListWidget#gCards {{ background: transparent; border: none; outline: none; }}
QDialog#gCreate {{ background: {T.CHROME}; }}
QPushButton#gCat {{ background: transparent; border: none; border-radius: 8px; text-align: left; padding: 9px 12px;
    color: {T.TEXT}; font-size: 10pt; }}
QPushButton#gCat:hover {{ background: {TILE_HOVER}; }}
QPushButton#gCat:checked {{ background: #2a1233; color: white; font-weight: 600; }}
QLabel#gH1 {{ font-size: 21pt; font-weight: 600; color: {T.TEXT_STRONG}; }}
"""


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------------ dibujos propios

def _grad(r: QRectF, a: str, b: str) -> QLinearGradient:
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor(a))
    g.setColorAt(1, QColor(b))
    return g


def size_mockup(w: float, h: float, group: str, side: int = 150) -> QPixmap:
    """Miniatura de un tamaño: una hoja a su proporción, con sombra y un contenido abstracto (bloque de foto,
    título y líneas) en el color del grupo. Las hojas de impresión y documentos llevan la esquina doblada."""
    dpr = 2.0
    pm = QPixmap(int(side * dpr), int(side * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    box = side * 0.74
    k = box / max(w, h)
    pw, ph = max(w * k, side * 0.16), max(h * k, side * 0.16)
    r = QRectF((side - pw) / 2, (side - ph) / 2, pw, ph)
    a, b = GROUP_COLORS.get(group, GROUP_COLORS["Personalizado"])
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawRoundedRect(r.translated(4, 5), 3, 3)
    fold_ = group in ("Impresión", "Documentos")
    page = QPainterPath()
    c = min(pw, ph) * 0.16 if fold_ else 0
    page.moveTo(r.left(), r.top())
    page.lineTo(r.right() - c, r.top())
    page.lineTo(r.right(), r.top() + c)
    page.lineTo(r.right(), r.bottom())
    page.lineTo(r.left(), r.bottom())
    page.closeSubpath()
    p.setBrush(QColor("#F4F5F8"))
    p.drawPath(page)
    if c:
        p.setBrush(QColor("#C9CCD6"))
        tri = QPainterPath()
        tri.moveTo(r.right() - c, r.top())
        tri.lineTo(r.right() - c, r.top() + c)
        tri.lineTo(r.right(), r.top() + c)
        tri.closeSubpath()
        p.drawPath(tri)
    # contenido: foto (degradado del grupo), título y dos líneas
    m = min(pw, ph) * 0.1
    wide = pw > ph * 1.25
    if wide:
        img = QRectF(r.left() + pw * 0.52, r.top(), pw * 0.48, ph)
        txt = QRectF(r.left() + m, r.top() + m, pw * 0.42, ph - 2 * m)
    else:
        img = QRectF(r.left() + m, r.top() + m + c * 0.6, pw - 2 * m, ph * 0.52)
        txt = QRectF(r.left() + m, img.bottom() + m * 0.8, pw - 2 * m, r.bottom() - img.bottom() - 2 * m)
    p.setBrush(_grad(img, a, b))
    p.drawRoundedRect(img, 2, 2)
    # «flor» de cinco pétalos blanca en la foto (adorno propio)
    cx, cy, rr = img.center().x(), img.center().y(), min(img.width(), img.height()) * 0.22
    p.setBrush(QColor(255, 255, 255, 215))
    for i in range(5):
        ang = i * 2 * math.pi / 5 - math.pi / 2
        p.drawEllipse(QRectF(cx + math.cos(ang) * rr * 0.55 - rr * 0.38, cy + math.sin(ang) * rr * 0.55 - rr * 0.38,
                             rr * 0.76, rr * 0.76))
    p.setBrush(QColor(a))
    p.drawEllipse(QRectF(cx - rr * 0.28, cy - rr * 0.28, rr * 0.56, rr * 0.56))
    lh = max(2.0, min(txt.height() * 0.2, ph * 0.07))
    y = txt.top() + (txt.height() * 0.25 if wide else 0)
    for frac, col in ((0.8, a), (0.62, "#B8BCC8"), (0.45, "#D3D6DE")):
        if y + lh > txt.bottom() + 0.5:
            break
        p.setBrush(QColor(col))
        p.drawRoundedRect(QRectF(txt.left(), y, txt.width() * frac, lh), lh / 2, lh / 2)
        y += lh * 1.9
    p.end()
    return pm


def quick_icon(name: str, group: str, side: int = 46) -> QIcon:
    """Círculo de color del grupo con un icono blanco (accesos rápidos del inicio)."""
    dpr = 2.0
    pm = QPixmap(int(side * dpr), int(side * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    a, b = GROUP_COLORS.get(group, GROUP_COLORS["Personalizado"])
    r = QRectF(1, 1, side - 2, side - 2)
    p.setPen(Qt.NoPen)
    p.setBrush(_grad(r, a, b))
    p.drawEllipse(r)
    s = int(side * 0.5)
    p.drawPixmap(int((side - s) / 2), int((side - s) / 2), I.pixmap(name, s, "#FFFFFF"))
    p.end()
    return QIcon(pm)


PLACEHOLDER_GRADS = [("#3b1f5c", "#c0517e"), ("#1f3a5c", "#5b8fc9"), ("#5c3a1f", "#d9a35b"),
                     ("#1f5c4a", "#6cc3a0"), ("#4a1f5c", "#9a6cf0")]


def _placeholder(p, el, seed: int):
    """Hueco de foto de una plantilla, para las miniaturas: degradado con un sol y dos montañas (propio)."""
    w, h = el["w"], el["h"]
    r = QRectF(0, 0, w, h)
    path = QPainterPath()
    if el.get("clip") == "ellipse":
        path.addEllipse(r)
    else:
        rad = min(float(el.get("radius", 0.0)), w / 2, h / 2)
        path.addRoundedRect(r, rad, rad)
    a, b = PLACEHOLDER_GRADS[seed % len(PLACEHOLDER_GRADS)]
    p.save()
    p.setClipPath(path)
    p.fillPath(path, _grad(r, a, b))
    p.setPen(Qt.NoPen)
    m = min(w, h)
    p.setBrush(QColor(255, 255, 255, 70))
    p.drawEllipse(QRectF(w * 0.62, h * 0.18, m * 0.22, m * 0.22))
    p.setBrush(QColor(255, 255, 255, 45))
    for x0, x1, top in ((-0.1, 0.7, 0.42), (0.35, 1.15, 0.55)):
        tri = QPainterPath()
        tri.moveTo(w * x0, h)
        tri.lineTo(w * (x0 + x1) / 2, h * top)
        tri.lineTo(w * x1, h)
        tri.closeSubpath()
        p.drawPath(tri)
    p.restore()


_CACHE = None


def design_thumb(d: dict, side: int = 190, cache=None, seed: int = 0, page: int | None = None) -> QImage:
    """Miniatura de un diseño (una página) con los huecos de foto vacíos dibujados con un degradado en vez del
    aspa: plantillas, estilos y propuestas."""
    global _CACHE
    if cache is None:
        if _CACHE is None:
            _CACHE = G.ImageCache()
        cache = _CACHE
    v = G.page_views(d)[d.get("page", 0) if page is None else page] if d.get("pages") else d
    s = side / max(v["w"], v["h"])
    img = QImage(max(1, round(v["w"] * s)), max(1, round(v["h"] * s)), QImage.Format_RGB32)
    img.fill(Qt.white)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    p.scale(s, s)
    p.fillRect(QRectF(0, 0, v["w"], v["h"]), G.qcolor(v.get("bg", "#FFFFFF")))
    for i, el in enumerate(v["elements"]):
        if el.get("hidden"):
            continue
        p.save()
        p.setTransform(G.element_transform(el), True)
        if el["type"] == "image" and not el.get("path"):
            _placeholder(p, el, seed + i)
        else:
            G.paint_element(p, el, cache, editing=False, img_px=s, max_img=512)
        p.restore()
    p.end()
    return img


def template_thumb(name: str, side: int = 190) -> QImage:
    """Miniatura de una plantilla para el inicio: los huecos de foto salen con un degradado en vez del aspa."""
    d = G.design_from_preset(G.TEMPLATE_SIZE[name])
    G.apply_template(d, name)
    return design_thumb(d, side, seed=sum(map(ord, name)), page=0)


class Hero(QFrame):
    """Cabecera con el degradado magenta → violeta de PhootFlow sobre el negro azulado."""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, QColor("#3a0a2c"))
        g.setColorAt(0.55, QColor("#2a0f55"))
        g.setColorAt(1, QColor("#101a3a"))
        p.fillRect(r, g)
        fade = QLinearGradient(r.topLeft(), r.bottomLeft())
        fade.setColorAt(0, QColor(0, 0, 0, 0))
        fade.setColorAt(1, QColor(T.CHROME))
        p.fillRect(r, fade)
        p.end()


# ------------------------------------------------------------------ rejilla de tarjetas

class CardDelegate(QStyledItemDelegate):
    """Tarjeta: miniatura centrada en un recuadro redondeado y, debajo, el nombre y una línea de detalle."""

    def __init__(self, parent, thumb_h: int):
        super().__init__(parent)
        self.thumb_h = thumb_h

    def paint(self, p, opt, idx):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(opt.rect).adjusted(6, 4, -6, -4)
        tile = QRectF(r.left(), r.top(), r.width(), self.thumb_h)
        hover = bool(opt.state & QStyle.State_MouseOver)
        sel = bool(opt.state & QStyle.State_Selected)
        p.setPen(QPen(QColor(T.ACCENT), 2) if sel else Qt.NoPen)
        p.setBrush(QColor(TILE_HOVER if hover else TILE))
        p.drawRoundedRect(tile, 10, 10)
        icon = idx.data(Qt.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            pm = icon.pixmap(QSize(int(tile.width() - 24), int(tile.height() - 20)))
            sz = pm.deviceIndependentSize()
            k = min((tile.width() - 24) / max(1, sz.width()), (tile.height() - 20) / max(1, sz.height()), 1.0)
            w, h = sz.width() * k, sz.height() * k
            tr = QRectF(tile.center().x() - w / 2, tile.center().y() - h / 2, w, h)
            if idx.data(Qt.UserRole + 2):  # miniatura de diseño: sombra suave
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 80))
                p.drawRoundedRect(tr.translated(2, 3), 2, 2)
            p.drawPixmap(tr.toRect(), pm)
        title = idx.data(Qt.DisplayRole) or ""
        meta = idx.data(Qt.UserRole + 1) or ""
        f = QFont(opt.font)
        f.setPointSizeF(9.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(T.TEXT_STRONG))
        fm = p.fontMetrics()
        tr = QRectF(r.left() + 2, tile.bottom() + 6, r.width() - 4, fm.height())
        p.drawText(tr, Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(title, Qt.ElideRight, int(tr.width())))
        if meta:
            f.setBold(False)
            f.setPointSizeF(8.5)
            p.setFont(f)
            p.setPen(QColor(T.MUTED))
            fm = p.fontMetrics()
            mr = QRectF(tr.left(), tr.bottom() + 2, tr.width(), fm.height())
            p.drawText(mr, Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(meta, Qt.ElideRight, int(mr.width())))
        p.restore()

    def sizeHint(self, opt, idx):
        return self.parent().gridSize()


class CardGrid(QListWidget):
    """Rejilla de tarjetas sin barra propia: crece en alto para mostrar todas (o `max_rows` filas) dentro de la
    página con scroll."""
    activated_payload = Signal(object)

    def __init__(self, tile_w: int = 200, thumb_h: int = 150, max_rows: int = 0):
        super().__init__()
        self.setObjectName("gCards")
        self.setViewMode(QListWidget.IconMode)
        self.setMovement(QListWidget.Static)
        self.setResizeMode(QListWidget.Adjust)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setGridSize(QSize(tile_w, thumb_h + 52))
        self.setIconSize(QSize(tile_w - 36, thumb_h - 20))
        self.setItemDelegate(CardDelegate(self, thumb_h))
        self.max_rows = max_rows
        self.more_btn = None
        self.itemClicked.connect(lambda it: self.activated_payload.emit(it.data(Qt.UserRole)))

    def add(self, title: str, meta: str, payload, icon: QIcon | None = None, design: bool = False):
        it = QListWidgetItem(icon or QIcon(), title)
        it.setData(Qt.UserRole, payload)
        it.setData(Qt.UserRole + 1, meta)
        it.setData(Qt.UserRole + 2, design)
        it.setToolTip(f"{title}\n{meta}" if meta else title)
        self.addItem(it)
        self._fit()
        return it

    def cols(self):
        return max(1, (self.viewport().width() or self.width()) // self.gridSize().width())

    def visible_count(self):
        return self.count() if not self.max_rows else min(self.count(), self.max_rows * self.cols())

    def _fit(self):
        n = self.count()
        cols = self.cols()
        rows = math.ceil(n / cols) if n else 0
        if self.max_rows:
            rows = min(rows, self.max_rows)
            for i in range(n):
                self.item(i).setHidden(i >= rows * cols)
        if self.more_btn is not None:
            self.more_btn.setVisible(self.visible_count() < n)
        self.setFixedHeight(rows * self.gridSize().height() + 6)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not hasattr(self, "_fit_timer"):  # temporizador hijo: muere con la rejilla (no llega tarde)
            self._fit_timer = QTimer(self)
            self._fit_timer.setSingleShot(True)
            self._fit_timer.timeout.connect(self._fit)
        self._fit_timer.start(0)


def _section(title: str, link: str = "", cb=None):
    row = QHBoxLayout()
    row.setContentsMargins(0, 14, 0, 4)
    t = QLabel(title)
    t.setObjectName("gH2")
    row.addWidget(t)
    row.addStretch(1)
    if link:
        b = QPushButton(link)
        b.setObjectName("gLink")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(cb)
        row.addWidget(b)
    return row


# ------------------------------------------------------------------ inicio

QUICK = [  # (texto, icono, grupo de color, acción)
    ("Con tus fotos", "studio_photo", "Redes sociales", ("session", None)),
    ("Plantillas", "lut", "Impresión", ("mode", "templates")),
    ("Redes", "star", "Redes sociales", ("group", "Redes sociales")),
    ("Presentación", "review", "Presentaciones", ("preset", "Presentación 16:9")),
    ("Impresión", "archive", "Impresión", ("group", "Impresión")),
    ("Copias de foto", "studio_photo", "Copias de foto", ("group", "Copias de foto")),
    ("Documento", "paste", "Documentos", ("group", "Documentos")),
    ("Logo", "graphics", "Marketing", ("preset", "Logo")),
    ("Marketing", "zap", "Marketing", ("group", "Marketing")),
    ("Personalizar", "crop", "Personalizado", ("custom", None)),
    ("Subir", "cloud", "Personalizado", ("upload", None)),
    ("Más", "menu", "Personalizado", ("create", None)),
]


class HomePage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.mode = "home"
        self.tgroup = ""
        self._tpl_icons: dict[str, QIcon] = {}
        self._tpl_queue: list[str] = []
        self._tpl_timer = QTimer(self)
        self._tpl_timer.setInterval(0)
        self._tpl_timer.timeout.connect(self._render_some_templates)
        self.setObjectName("gHome")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(HOME_QSS)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_rail())
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("gHomeBody")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(0, 0, 0, 24)
        self.v.setSpacing(0)
        self.v.addWidget(self._build_hero())
        self.content = QWidget()
        self.cv = QVBoxLayout(self.content)
        self.cv.setContentsMargins(40, 0, 40, 0)
        self.cv.setSpacing(4)
        self.v.addWidget(self.content)
        self.v.addStretch(1)
        self.scroll.setWidget(body)
        h.addWidget(self.scroll, 1)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(220)
        self.search_timer.timeout.connect(self.refresh)

    # ---------------------------------------------------------- piezas fijas
    def _build_rail(self):
        w = QFrame()
        w.setObjectName("gRail")
        w.setFixedWidth(78)
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 14, 6, 10)
        v.setSpacing(6)
        create = QToolButton()
        create.setObjectName("gRailBtn")
        create.setIcon(quick_icon("plus", "Redes sociales", 40))
        create.setIconSize(QSize(40, 40))
        create.setText("Crear")
        create.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        create.setCursor(Qt.PointingHandCursor)
        create.setToolTip("Crear un diseño nuevo")
        create.clicked.connect(lambda: self.owner.open_create_dialog())
        create.setFixedWidth(66)
        v.addWidget(create, 0, Qt.AlignHCenter)
        v.addSpacing(8)
        self.rail_btns = {}
        grp = QButtonGroup(self)
        for key, text, ic in (("home", "Inicio", "events"), ("projects", "Proyectos", "folder"),
                              ("templates", "Plantillas", "lut"), ("brand", "Marca", "palette")):
            b = QToolButton()
            b.setObjectName("gRailBtn")
            b.setIcon(I.icon(ic, 20))
            b.setIconSize(QSize(20, 20))
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setCheckable(key != "brand")
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedWidth(66)
            if key == "brand":
                b.setToolTip("Kit de marca: colores, fuentes y logos (se abre en el editor)")
                b.clicked.connect(lambda: self.owner.open_editor("brand"))
            else:
                grp.addButton(b)
                b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            v.addWidget(b, 0, Qt.AlignHCenter)
            self.rail_btns[key] = b
        self.rail_btns["home"].setChecked(True)
        v.addStretch(1)
        back = QToolButton()
        back.setObjectName("gRailBtn")
        back.setIcon(I.icon("edits", 20))
        back.setIconSize(QSize(20, 20))
        back.setText("Editor")
        back.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        back.setToolTip("Volver al diseño abierto")
        back.setCursor(Qt.PointingHandCursor)
        back.setFixedWidth(66)
        back.clicked.connect(lambda: self.owner.open_editor())
        v.addWidget(back, 0, Qt.AlignHCenter)
        return w

    def _build_hero(self):
        hero = Hero()
        hero.setMinimumHeight(330)
        v = QVBoxLayout(hero)
        v.setContentsMargins(40, 34, 40, 10)
        v.setSpacing(14)
        self.title = QLabel("¿Qué diseñamos hoy?")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 27pt; font-weight: 700; color: white; background: transparent;")
        v.addWidget(self.title)
        seg = QHBoxLayout()
        seg.addStretch(1)
        self.seg_btns = {}
        grp = QButtonGroup(self)
        for key, text in (("home", "Inicio"), ("templates", "Plantillas"), ("projects", "Proyectos")):
            b = QPushButton(text)
            b.setObjectName("gSeg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            grp.addButton(b)
            seg.addWidget(b)
            self.seg_btns[key] = b
        self.seg_btns["home"].setChecked(True)
        seg.addStretch(1)
        v.addLayout(seg)
        row = QHBoxLayout()
        row.addStretch(1)
        self.search = QLineEdit()
        self.search.setObjectName("gSearch")
        self.search.setPlaceholderText("Busca diseños, plantillas o tamaños (boda, story, A4, logo…)")
        self.search.addAction(I.icon("search", 18), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(760)
        self.search.setMinimumWidth(320)
        self.search.textChanged.connect(lambda _: self.search_timer.start())
        row.addWidget(self.search, 4)
        row.addStretch(1)
        v.addLayout(row)
        v.addSpacing(6)
        self.quick = QWidget()
        q = QHBoxLayout(self.quick)
        q.setContentsMargins(0, 0, 0, 0)
        q.setSpacing(4)
        q.addStretch(1)
        for text, ic, group, act in QUICK:
            b = QToolButton()
            b.setObjectName("gQuick")
            b.setIcon(quick_icon(ic, group))
            b.setIconSize(QSize(46, 46))
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setFixedSize(84, 82)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, a=act: self._quick(a))
            q.addWidget(b)
        q.addStretch(1)
        self.quick.setFixedHeight(92)
        v.addWidget(self.quick)
        return hero

    def _quick(self, act):
        kind, arg = act
        if kind == "mode":
            self.set_mode(arg)
        elif kind == "group":
            self.owner.open_create_dialog(arg)
        elif kind == "preset":
            self.owner.new_from_preset(arg)
        elif kind == "custom":
            self.owner.open_create_dialog("custom")
        elif kind == "upload":
            self.owner.upload_new()
        elif kind == "session":
            self.owner.open_session_designer()
        else:
            self.owner.open_create_dialog()

    # ---------------------------------------------------------- contenido
    def set_mode(self, mode: str):
        self.mode = mode
        for k, b in self.rail_btns.items():
            if b.isCheckable():
                b.setChecked(k == mode)
        for k, b in self.seg_btns.items():
            b.setChecked(k == mode)
        self.title.setText({"home": "¿Qué diseñamos hoy?", "templates": "Plantillas",
                            "projects": "Tus diseños"}[mode])
        self.quick.setVisible(mode == "home")
        self.refresh()
        self.scroll.verticalScrollBar().setValue(0)

    def _clear(self):
        while self.cv.count():
            it = self.cv.takeAt(0)
            if it.widget():
                _drop_widget(it.widget())
            elif it.layout():
                _drop_layout(it.layout())

    def refresh(self):
        self._clear()
        q = fold(self.search.text().strip())
        designs = G.list_designs(self.owner.ddir)
        if q:
            self._search_results(q, designs)
        elif self.mode == "projects":
            self.cv.addLayout(_section(f"Todos tus diseños ({len(designs)})"))
            self._designs_grid(designs, 0)
        elif self.mode == "templates":
            self._templates_page()
        else:
            self.cv.addLayout(_section("Seguir diseñando", "Ver todo" if len(designs) > 0 else "",
                                       lambda: self.set_mode("projects")))
            grid = self._designs_grid(designs, 2)
            if grid is not None:
                more = QPushButton("Mostrar más")
                more.setObjectName("gMore")
                more.setCursor(Qt.PointingHandCursor)

                def show_more(_=False, g=grid, b=more):
                    g.max_rows += 2
                    g._fit()
                    if g.visible_count() >= g.count():
                        b.hide()
                more.clicked.connect(show_more)
                grid.more_btn = more
                more.hide()
                self.cv.addWidget(more, 0, Qt.AlignHCenter)
                grid._fit()
            self.cv.addLayout(_section("Plantillas para ti", "Ver todo", lambda: self.set_mode("templates")))
            self._templates_grid(self._suggested_templates(designs), 2)

    def _designs_grid(self, designs, max_rows):
        if not designs:
            e = QLabel("Aún no hay diseños. Pulsa «Crear» o elige una plantilla para empezar; se guardan solos.")
            e.setObjectName("gEmpty")
            self.cv.addWidget(e)
            return None
        grid = CardGrid(214, 156, max_rows)
        grid.setContextMenuPolicy(Qt.CustomContextMenu)
        grid.customContextMenuRequested.connect(lambda pos, g=grid: self._design_menu(g, pos))
        for path, d in designs:
            png = os.path.splitext(path)[0] + ".png"
            icon = QIcon(png) if os.path.exists(png) else QIcon()
            grid.add(d.get("name") or "Diseño sin título",
                     f"{G.size_label(d)} · {G.edited_ago(d.get('modified', 0))}", path, icon, design=True)
        grid.activated_payload.connect(self.owner.open_path_editor)
        self.cv.addWidget(grid)
        return grid

    def _design_menu(self, grid, pos):
        it = grid.itemAt(pos)
        if not it:
            return
        path = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("Abrir").triggered.connect(lambda: self.owner.open_path_editor(path))
        m.addAction("Duplicar").triggered.connect(lambda: (self.owner.duplicate_path(path), self.refresh()))
        m.addAction("Cambiar nombre…").triggered.connect(lambda: self._rename(path, it.text()))
        m.addSeparator()
        m.addAction("Eliminar…").triggered.connect(lambda: self.owner.delete_path(path, it.text()) and
                                                   self.refresh())
        m.exec(grid.viewport().mapToGlobal(pos))

    def _rename(self, path, old):
        name, ok = QInputDialog.getText(self, "Cambiar nombre", "Nombre del diseño:", text=old)
        if ok and name.strip():
            self.owner.rename_path(path, name.strip())
            self.refresh()

    def _suggested_templates(self, designs):
        """Plantillas para ti: primero las del tamaño de tus últimos diseños, luego el resto."""
        sizes = []
        for _, d in designs[:12]:
            for g, n, w, h, u in G.PRESETS:
                ww, hh = (G.mm_to_px(w), G.mm_to_px(h)) if u == "mm" else (w, h)
                if abs(ww - d["w"]) < 2 and abs(hh - d["h"]) < 2 and n not in sizes:
                    sizes.append(n)
        names = list(G.TEMPLATES)
        return sorted(names, key=lambda n: (G.TEMPLATE_SIZE.get(n) not in sizes, 0))

    def _templates_page(self):
        groups = []
        for n in G.TEMPLATES:
            g = G.TEMPLATE_GROUP.get(n, "Otras")
            if g not in groups:
                groups.append(g)
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 8, 0, 0)
        chips.setSpacing(6)
        grp = QButtonGroup(self)
        for g in [""] + groups:
            b = QPushButton(g or "Todas")
            b.setObjectName("gChip")
            b.setCheckable(True)
            b.setChecked(g == self.tgroup)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, g=g: (setattr(self, "tgroup", g), self.refresh()))
            grp.addButton(b)
            chips.addWidget(b)
        chips.addStretch(1)
        self.cv.addLayout(chips)
        for g in groups:
            if self.tgroup and g != self.tgroup:
                continue
            names = [n for n in G.TEMPLATES if G.TEMPLATE_GROUP.get(n, "Otras") == g]
            self.cv.addLayout(_section(f"{g} ({len(names)})"))
            self._templates_grid(names, 0)

    def _templates_grid(self, names, max_rows):
        grid = CardGrid(200, 176, max_rows)
        for n in names:
            size = G.TEMPLATE_SIZE.get(n, "")
            it = grid.add(n, size, n, self._tpl_icons.get(n))
            if n not in self._tpl_icons and n not in self._tpl_queue:
                self._tpl_queue.append(n)
            it.setData(Qt.UserRole + 3, n)
        grid.activated_payload.connect(self.owner.new_from_template)
        self.cv.addWidget(grid)
        if self._tpl_queue:
            self._tpl_timer.start()
        return grid

    def _render_some_templates(self):
        """Las miniaturas de plantilla se dibujan de pocas en pocas para no congelar la ventana."""
        done = []
        for _ in range(3):
            if not self._tpl_queue:
                break
            n = self._tpl_queue.pop(0)
            try:
                self._tpl_icons[n] = QIcon(QPixmap.fromImage(template_thumb(n, 190)))
            except Exception:
                self._tpl_icons[n] = QIcon()
            done.append(n)
        for grid in self.content.findChildren(CardGrid):
            for i in range(grid.count()):
                it = grid.item(i)
                n = it.data(Qt.UserRole + 3)
                if n in done:
                    it.setIcon(self._tpl_icons[n])
        if not self._tpl_queue:
            self._tpl_timer.stop()

    def _search_results(self, q, designs):
        found = False
        sizes = [p for p in G.PRESETS if q in fold(p[1]) or q in fold(p[0])]
        if sizes:
            found = True
            self.cv.addLayout(_section("Tamaños"))
            grid = CardGrid(176, 130)
            for g, n, w, h, u in sizes:
                grid.add(n, f"{w:g} × {h:g} {u}", n, QIcon(size_mockup(w, h, g, 130)))
            grid.activated_payload.connect(self.owner.new_from_preset)
            self.cv.addWidget(grid)
        mine = [(p, d) for p, d in designs if q in fold(d.get("name", ""))]
        if mine:
            found = True
            self.cv.addLayout(_section("Tus diseños"))
            self._designs_grid(mine, 0)
        tpls = [n for n in G.TEMPLATES if q in fold(n) or q in fold(G.TEMPLATE_GROUP.get(n, ""))
                or q in fold(G.TEMPLATE_SIZE.get(n, ""))]
        if tpls:
            found = True
            self.cv.addLayout(_section("Plantillas"))
            self._templates_grid(tpls, 0)
        if not found:
            e = QLabel(f"Nada con «{self.search.text().strip()}». Prueba con «boda», «story», «A4» o «logo».")
            e.setObjectName("gEmpty")
            self.cv.addWidget(e)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self.refresh)


def _drop_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget():
            _drop_widget(it.widget())
        elif it.layout():
            _drop_layout(it.layout())


def _drop_widget(w):
    """Fuera ya de la página (no esperar al borrado diferido: se verían y contarían junto a los nuevos)."""
    w.hide()
    w.setParent(None)
    w.deleteLater()


# ------------------------------------------------------------------ crear un diseño

class CreateDialog(QDialog):
    """«Crear un diseño»: categorías y tarjetas de tamaño. `choice` queda en ("preset", nombre),
    ("custom", ancho, alto, unidad) o ("upload",)."""

    def __init__(self, parent, used: list[str] | None = None, start: str = ""):
        super().__init__(parent)
        self.setObjectName("gCreate")
        self.setWindowTitle("Crear un diseño")
        self.setStyleSheet(HOME_QSS)
        self.resize(1120, 760)
        self.choice = None
        self.used = [u for u in (used or []) if G.preset_info(u)]
        h = QHBoxLayout(self)
        h.setContentsMargins(26, 24, 18, 18)
        h.setSpacing(22)
        left = QVBoxLayout()
        left.setSpacing(3)
        t = QLabel("Crea un diseño")
        t.setObjectName("gH1")
        left.addWidget(t)
        left.addSpacing(12)
        self.cats = {}
        grp = QButtonGroup(self)
        cats = [("foryou", "Para ti", "auto", "Redes sociales")] + \
               [(g, g, {"Redes sociales": "star", "Impresión": "archive", "Copias de foto": "studio_photo",
                        "Presentaciones": "review", "Documentos": "paste", "Marketing": "zap"}.get(g, "lut"), g)
                for g in G.PRESET_GROUPS] + \
               [("custom", "Tamaño personalizado", "crop", "Personalizado"),
                ("upload", "Subir una imagen", "cloud", "Personalizado")]
        for key, text, ic, group in cats:
            b = QPushButton(text)
            b.setObjectName("gCat")
            b.setIcon(quick_icon(ic, group, 24))
            b.setIconSize(QSize(24, 24))
            b.setCheckable(key != "upload")
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(240)
            if key == "upload":
                b.clicked.connect(self._upload)
            else:
                grp.addButton(b)
                b.clicked.connect(lambda _=False, k=key: self.show_cat(k))
            left.addWidget(b)
            self.cats[key] = b
        left.addStretch(1)
        h.addLayout(left)
        right = QVBoxLayout()
        right.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("gSearch")
        self.search.setPlaceholderText("¿Qué quieres diseñar?")
        self.search.addAction(I.icon("search", 18), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.show_cat(self.cat))
        right.addWidget(self.search)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right.addWidget(self.scroll, 1)
        h.addLayout(right, 1)
        self.cat = ""
        self.show_cat(start if start in self.cats else "foryou")
        self.search.setFocus()

    def _cards(self, v, title, names):
        names = [n for n in names if G.preset_info(n)]
        if not names:
            return
        v.addLayout(_section(title))
        grid = CardGrid(186, 146)
        for n in names:
            g, _, w, hh, u = G.preset_info(n)
            grid.add(n, f"{w:g} × {hh:g} {u}", n, QIcon(size_mockup(w, hh, g, 150)))
        grid.activated_payload.connect(lambda n: self._pick(("preset", n)))
        v.addWidget(grid)

    def show_cat(self, key):
        self.cat = key
        if key in self.cats and self.cats[key].isCheckable():
            self.cats[key].setChecked(True)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(4)
        q = fold(self.search.text().strip())
        if q:
            names = [p[1] for p in G.PRESETS if q in fold(p[1]) or q in fold(p[0])]
            self._cards(v, f"Resultados para «{self.search.text().strip()}»", names)
            if not names:
                e = QLabel("Ningún tamaño con ese nombre. Usa «Tamaño personalizado» para poner las medidas.")
                e.setObjectName("gEmpty")
                v.addWidget(e)
        elif key == "foryou":
            if self.used:
                self._cards(v, "Más usados", self.used[:4])
            self._cards(v, "Populares", [n for n in G.POPULAR if n not in self.used[:4]])
            new = [p[1] for p in G.PRESETS if p[0] in ("Presentaciones", "Documentos", "Marketing")
                   and p[1] not in self.used]
            self._cards(v, "Prueba algo nuevo", new[:8])
        elif key == "custom":
            v.addWidget(self._custom_form())
        else:
            self._cards(v, key, [p[1] for p in G.PRESETS if p[0] == key])
        v.addStretch(1)
        self.scroll.setWidget(body)

    def _custom_form(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(_section("Tamaño personalizado"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.cw, self.ch = QDoubleSpinBox(), QDoubleSpinBox()
        for sb, lab in ((self.cw, "Ancho"), (self.ch, "Alto")):
            sb.setRange(10, 20000)
            sb.setDecimals(0)
            sb.setValue(1080)
            sb.setMinimumWidth(110)
            row.addWidget(QLabel(lab))
            row.addWidget(sb)
        self.cu = QComboBox()
        self.cu.addItems(["px", "mm"])
        self.cu.currentTextChanged.connect(self._unit_changed)
        row.addWidget(self.cu)
        row.addStretch(1)
        v.addLayout(row)
        self.cprev = QLabel()
        self.cprev.setFixedSize(200, 200)
        v.addWidget(self.cprev)
        for sb in (self.cw, self.ch):
            sb.valueChanged.connect(self._custom_preview)
        self._custom_preview()
        b = QPushButton("Crear diseño nuevo")
        b.setObjectName("primary")
        b.setStyleSheet("QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #F50D8A, "
                        "stop:1 #7B2FF7); border: none; border-radius: 8px; color: white; font-weight: 600; "
                        "padding: 10px 22px; }")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: self._pick(("custom", self.cw.value(), self.ch.value(), self.cu.currentText())))
        v.addWidget(b, 0, Qt.AlignLeft)
        hint = QLabel("En mm se diseña a 300 ppp para imprenta (con sangrado al exportar el PDF).")
        hint.setObjectName("gEmpty")
        v.addWidget(hint)
        return w

    def _unit_changed(self, u):
        for sb in (self.cw, self.ch):
            sb.setValue(sb.value() / 25.4 * 300 if u == "px" else sb.value() * 25.4 / 300)

    def _custom_preview(self, *_):
        self.cprev.setPixmap(size_mockup(self.cw.value(), self.ch.value(), "Personalizado", 200))

    def _upload(self):
        self._pick(("upload",))

    def _pick(self, choice):
        self.choice = choice
        self.accept()
