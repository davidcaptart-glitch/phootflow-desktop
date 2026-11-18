"""Marco de recorte interactivo común (Studio y revisión de Events), como en Lightroom.

Sobre la foto entera se dibuja el marco: se arrastra por dentro para moverlo y por las esquinas o los lados
para cambiar el tamaño, con proporción fija o libre. Las coordenadas van de 0 a 1 sobre la foto mostrada.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

RATIOS = ["Original", "Libre", "1:1", "4:5", "3:2", "4:3", "16:9", "5:7"]
MIN = 0.03


def ratio_value(name: str, flip: bool, size: tuple[int, int]) -> float | None:
    """Proporción ancho/alto en píxeles (None = libre). `size` = (ancho, alto) de la foto entera."""
    if name == "Libre":
        return None
    if name == "Original" or ":" not in name:
        v = size[0] / max(1, size[1])
    else:
        a, b = name.split(":")
        v = float(a) / float(b)
    return 1 / v if flip else v


def guess_ratio(rect, size) -> tuple[str, bool]:
    """Nombre de la proporción que más se parece a un recorte (para poner el desplegable al abrirlo)."""
    w = (rect[2] - rect[0]) * size[0]
    h = (rect[3] - rect[1]) * size[1]
    if h <= 0:
        return "Libre", False
    v = w / h
    for name in RATIOS[2:] + ["Original"]:
        for flip in (False, True):
            r = ratio_value(name, flip, size)
            if r and abs(r - v) / r < 0.01:
                return name, flip
    return "Libre", False


def _shift_inside(x0, y0, w, h):
    x0 = min(max(0.0, x0), 1.0 - w)
    y0 = min(max(0.0, y0), 1.0 - h)
    return [x0, y0, x0 + w, y0 + h]


def fit_aspect(rect, rn: float, kind: str = "br"):
    """Ajusta `rect` a la proporción `rn` (ancho/alto en unidades 0..1) dejando fijo el lado o la esquina
    opuestos a lo que se arrastra (`kind`: l, r, t, b o dos letras para una esquina)."""
    x0, y0, x1, y1 = rect
    w, h = max(MIN, x1 - x0), max(MIN, y1 - y0)
    if kind in ("l", "r"):
        h = w / rn
    elif kind in ("t", "b"):
        w = h * rn
    elif w / h > rn:
        h = w / rn
    else:
        w = h * rn
    s = min(1.0, 1.0 / w, 1.0 / h)
    w, h = w * s, h * s
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    nx0 = x1 - w if "l" in kind else (x0 if "r" in kind else cx - w / 2)
    ny0 = y1 - h if "t" in kind else (y0 if "b" in kind else cy - h / 2)
    return _shift_inside(nx0, ny0, w, h)


def refit(rect, rn: float | None):
    """Mismo centro y una superficie parecida, con la proporción nueva (al cambiar el desplegable)."""
    if rn is None:
        return list(rect)
    x0, y0, x1, y1 = rect
    area = max(MIN * MIN, (x1 - x0) * (y1 - y0))
    w = (area * rn) ** 0.5
    h = w / rn
    s = min(1.0, 1.0 / w, 1.0 / h)
    w, h = w * s, h * s
    return _shift_inside((x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - h / 2, w, h)


class CropTool:
    """Estado e interacción del marco. El widget llama a press/move/release/paint con su rectángulo de foto.
    `on_change(fase)`: "start" al empezar a arrastrar, "end" al soltar."""

    def __init__(self, widget, on_change=None):
        self.w = widget
        self.rect: list | None = None
        self.rn: float | None = None      # proporción en unidades 0..1 (ancho/alto); None = libre
        self.on_change = on_change
        self._drag = None

    @property
    def active(self) -> bool:
        return self.rect is not None

    def start(self, rect, rn=None):
        self.rect, self.rn = list(rect or [0.0, 0.0, 1.0, 1.0]), rn
        if rn:
            self.rect = refit(self.rect, rn)
        self.w.update()

    def stop(self):
        self.rect, self._drag = None, None
        self.w.unsetCursor()
        self.w.update()

    def set_ratio(self, rn):
        self.rn = rn
        if self.rect is not None and rn:
            self.rect = refit(self.rect, rn)
        self.w.update()

    def reset(self):
        """Sin recorte (o el mayor posible con la proporción elegida)."""
        self.rect = refit([0.0, 0.0, 1.0, 1.0], self.rn) if self.rn else [0.0, 0.0, 1.0, 1.0]
        if self.rn:
            w, h = self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]
            s = min(1.0 / w, 1.0 / h)
            self.rect = _shift_inside(0.5 - w * s / 2, 0.5 - h * s / 2, w * s, h * s)
        self.w.update()

    # ---------------------------------------------------------- ratón
    def _screen(self, r: QRectF) -> QRectF:
        c = self.rect
        return QRectF(r.x() + c[0] * r.width(), r.y() + c[1] * r.height(),
                      (c[2] - c[0]) * r.width(), (c[3] - c[1]) * r.height())

    def hit(self, pos: QPointF, r: QRectF) -> str:
        if self.rect is None or r.width() <= 0:
            return ""
        cr, tol, k = self._screen(r), 10, ""
        if cr.top() - tol <= pos.y() <= cr.bottom() + tol:
            if abs(pos.x() - cr.left()) <= tol:
                k += "l"
            elif abs(pos.x() - cr.right()) <= tol:
                k += "r"
        if cr.left() - tol <= pos.x() <= cr.right() + tol:
            if abs(pos.y() - cr.top()) <= tol:
                k += "t"
            elif abs(pos.y() - cr.bottom()) <= tol:
                k += "b"
        if k:
            return k
        return "move" if cr.contains(pos) else ""

    def press(self, pos: QPointF, r: QRectF) -> bool:
        k = self.hit(pos, r)
        if not k:
            return self.rect is not None  # en modo recorte, un clic fuera no hace nada más
        self._drag = (k, QPointF(pos), list(self.rect))
        if self.on_change:
            self.on_change("start")
        return True

    def move(self, pos: QPointF, r: QRectF, buttons) -> bool:
        if self.rect is None:
            return False
        if self._drag is None:
            k = self.hit(pos, r)
            cur = {"l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
                   "lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor, "rt": Qt.SizeBDiagCursor,
                   "lb": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor}.get(k)
            self.w.setCursor(cur) if cur is not None else self.w.unsetCursor()
            return True
        k, start, c0 = self._drag
        dx, dy = (pos.x() - start.x()) / r.width(), (pos.y() - start.y()) / r.height()
        x0, y0, x1, y1 = c0
        if k == "move":
            self.rect = _shift_inside(x0 + dx, y0 + dy, x1 - x0, y1 - y0)
        else:
            if "l" in k:
                x0 = min(max(0.0, x0 + dx), x1 - MIN)
            if "r" in k:
                x1 = max(min(1.0, x1 + dx), x0 + MIN)
            if "t" in k:
                y0 = min(max(0.0, y0 + dy), y1 - MIN)
            if "b" in k:
                y1 = max(min(1.0, y1 + dy), y0 + MIN)
            self.rect = fit_aspect([x0, y0, x1, y1], self.rn, k) if self.rn else [x0, y0, x1, y1]
        self.w.update()
        return True

    def release(self) -> bool:
        if self._drag is None:
            return self.rect is not None
        self._drag = None
        if self.on_change:
            self.on_change("end")
        return True

    # ---------------------------------------------------------- dibujo
    def paint(self, p: QPainter, r: QRectF):
        if self.rect is None or r.width() <= 0:
            return
        cr = self._screen(r)
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        shade = QPainterPath()
        shade.addRect(r)
        shade.addRect(cr)
        p.fillPath(shade, QColor(0, 0, 0, 150))
        p.setPen(QPen(QColor(255, 255, 255, 80), 1))
        for i in (1, 2):
            x = cr.left() + cr.width() * i / 3
            y = cr.top() + cr.height() * i / 3
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(cr)
        p.setPen(QPen(QColor(255, 255, 255), 3, Qt.SolidLine, Qt.SquareCap))
        L = min(18.0, cr.width() / 3, cr.height() / 3)
        for x, y, sx, sy in ((cr.left(), cr.top(), 1, 1), (cr.right(), cr.top(), -1, 1),
                             (cr.left(), cr.bottom(), 1, -1), (cr.right(), cr.bottom(), -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + sx * L, y))   # esquinas en L
            p.drawLine(QPointF(x, y), QPointF(x, y + sy * L))
        m = 7
        cx, cy = cr.center().x(), cr.center().y()
        p.drawLine(QPointF(cx - m, cr.top()), QPointF(cx + m, cr.top()))  # asas de los lados
        p.drawLine(QPointF(cx - m, cr.bottom()), QPointF(cx + m, cr.bottom()))
        p.drawLine(QPointF(cr.left(), cy - m), QPointF(cr.left(), cy + m))
        p.drawLine(QPointF(cr.right(), cy - m), QPointF(cr.right(), cy + m))
        p.restore()
