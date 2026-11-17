"""Comparación antes/después (como «Antes/Después» de Lightroom y Capture One).

Modos:
- "off":  solo el resultado.
- "full": solo el antes (atajo «\\»).
- "v":    ventanilla vertical: a la izquierda de la línea el antes, a la derecha el después.
- "h":    ventanilla horizontal: arriba el antes, abajo el después.
La línea se arrastra con el ratón. El «antes» tiene que llegar con la misma geometría (recorte y giro) que el
«después»; si no coincide en proporción, se escala al mismo rectángulo.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

MODES = ("off", "full", "v", "h")


class CompareState:
    def __init__(self):
        self.mode = "off"
        self.pos = 0.5          # posición de la línea (0..1)
        self.dragging = False

    @property
    def active(self) -> bool:
        return self.mode != "off"

    @property
    def split(self) -> bool:
        return self.mode in ("v", "h")

    def next_split(self):
        """Atajo Y: ventanilla vertical → horizontal → sin comparar."""
        self.mode = {"v": "h", "h": "off"}.get(self.mode, "v")


def _tag(p: QPainter, x: float, y: float, text: str):
    f = QFont(p.font())
    f.setPointSizeF(8)
    f.setBold(True)
    p.setFont(f)
    w = p.fontMetrics().horizontalAdvance(text) + 14
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 165))
    p.drawRoundedRect(QRectF(x, y, w, 20), 3, 3)
    p.setPen(QColor(235, 235, 235))
    p.drawText(QRectF(x, y, w, 20), Qt.AlignCenter, text)
    return w


def paint(p: QPainter, r: QRectF, after, before, st: CompareState):
    """Dibuja el después y, según el modo, el antes en `r` (pixmaps ya escalables al mismo rectángulo)."""
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    if st.mode == "full" and before is not None:
        p.drawPixmap(r.toRect(), before)
        _tag(p, r.x() + 8, r.y() + 8, "ANTES")
        return
    p.drawPixmap(r.toRect(), after)
    if not st.split or before is None:
        return
    p.save()
    if st.mode == "v":
        cut = r.x() + r.width() * st.pos
        clip = QRectF(r.x(), r.y(), cut - r.x(), r.height())
    else:
        cut = r.y() + r.height() * st.pos
        clip = QRectF(r.x(), r.y(), r.width(), cut - r.y())
    path = QPainterPath()
    path.addRect(clip)
    p.setClipPath(path)
    p.drawPixmap(r.toRect(), before)
    p.restore()
    # línea y tirador
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(255, 255, 255, 230), 2))
    if st.mode == "v":
        p.drawLine(QPointF(cut, r.y()), QPointF(cut, r.bottom()))
        c = QPointF(cut, r.y() + r.height() / 2)
    else:
        p.drawLine(QPointF(r.x(), cut), QPointF(r.right(), cut))
        c = QPointF(r.x() + r.width() / 2, cut)
    p.setBrush(QColor(20, 20, 20, 220))
    p.drawEllipse(c, 13, 13)
    p.setPen(QColor(255, 255, 255))
    f = QFont(p.font())
    f.setPointSizeF(9)
    p.setFont(f)
    p.drawText(QRectF(c.x() - 13, c.y() - 13, 26, 26), Qt.AlignCenter, "⇔" if st.mode == "v" else "⇕")
    if st.mode == "v":
        _tag(p, r.x() + 8, r.y() + 8, "ANTES")
        p.save()
        f2 = QFont(p.font())
        f2.setPointSizeF(8)
        f2.setBold(True)
        p.setFont(f2)
        w = p.fontMetrics().horizontalAdvance("DESPUÉS") + 14
        p.restore()
        _tag(p, r.right() - w - 8, r.y() + 8, "DESPUÉS")
    else:
        _tag(p, r.x() + 8, r.y() + 8, "ANTES")
        _tag(p, r.x() + 8, r.bottom() - 28, "DESPUÉS")


def drag(st: CompareState, r: QRectF, x: float, y: float) -> bool:
    """Mueve la línea al punto (x, y) del widget. Devuelve True si se ha movido."""
    if not st.split or r.width() <= 0:
        return False
    v = (x - r.x()) / r.width() if st.mode == "v" else (y - r.y()) / r.height()
    st.pos = min(0.98, max(0.02, v))
    return True


class CompareBar(QWidget):
    """Botones de modo: sin comparar · antes · ventanilla vertical · ventanilla horizontal."""
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for mode, text, tip in (("off", "▣", "Solo el resultado"),
                                ("full", "Y|Y", "Ver el antes (\\)"),
                                ("v", "◧", "Antes / después con ventanilla vertical: arrastra la línea (Y)"),
                                ("h", "⬒", "Antes / después con ventanilla horizontal: arrastra la línea (Y)")):
            b = QPushButton(text)
            b.setObjectName("flat")
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedWidth(34 if mode != "full" else 40)
            b.setStyleSheet("font-size: 12pt;" if mode != "full" else "font-size: 9pt; font-weight: 600;")
            b.clicked.connect(lambda _, m=mode: self.changed.emit(m))
            self.group.addButton(b)
            self.buttons[mode] = b
            h.addWidget(b)
        self.buttons["off"].setChecked(True)

    def set_mode(self, mode: str):
        if mode in self.buttons:
            self.buttons[mode].setChecked(True)
