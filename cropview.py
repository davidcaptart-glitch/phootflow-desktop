"""Marco de recorte interactivo común (Studio y revisión de Events), como en Lightroom.

Sobre la foto entera se dibuja el marco: se arrastra por dentro para moverlo y por las esquinas o los lados
para cambiar el tamaño, con proporción fija o libre. Las coordenadas van de 0 a 1 sobre la foto mostrada.

Enderezar va en el mismo marco (si el anfitrión pone `on_angle`): arrastrando fuera del marco se gira la foto,
y con Ctrl (o el modo «Nivel») se traza una línea sobre el horizonte o una vertical y la foto se pone recta.
Mientras se gira se ve al momento (la imagen mostrada girada y ampliada para cubrir, con cuadrícula fina) y al
soltar el anfitrión revela con el ángulo nuevo y llama a `rendered(ángulo)`. Ángulos en grados con el
convenio de OpenCV (`engine.rotate`): positivo = antihorario.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

RATIOS = ["Original", "Libre", "1:1", "4:5", "3:2", "4:3", "16:9", "5:7"]
MIN = 0.03
MAX_ANGLE = 45.0


def cover_scale(w: float, h: float, deg: float) -> float:
    """Ampliación para que un rectángulo w×h girado `deg` siga cubriendo el w×h sin girar (sin esquinas vacías)."""
    a = math.radians(abs(deg))
    c, s = math.cos(a), math.sin(a)
    return max(c + (h / max(w, 1e-6)) * s, c + (w / max(h, 1e-6)) * s)


def level_angle(x0, y0, x1, y1) -> float:
    """Giro (convenio de OpenCV) que deja horizontal (o vertical, si está más cerca) la línea trazada en pantalla."""
    phi = math.degrees(math.atan2(y1 - y0, x1 - x0))  # en pantalla, y hacia abajo: positivo = horario
    phi = (phi + 90) % 180 - 90                        # -90..90
    if phi > 45:
        phi -= 90
    elif phi < -45:
        phi += 90
    return phi


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
        self.on_angle = None              # on_angle(ángulo, fase "start"/"end"): enderezar desde el marco
        self.angle = 0.0                  # ángulo con el que está revelada la imagen mostrada
        self.target = 0.0                 # ángulo que se está poniendo (vista previa hasta que llega el revelado)
        self.level_mode = False           # «Nivel»: el próximo arrastre traza la línea del horizonte
        self._level = None                # (inicio, fin) de la línea que se traza
        self._rot = None                  # giro en curso: (ángulo de partida, ángulo del ratón al empezar)

    @property
    def active(self) -> bool:
        return self.rect is not None

    def start(self, rect, rn=None, angle=None):
        if angle is not None:
            self.angle = self.target = float(angle)
        self.rect, self.rn = list(rect or [0.0, 0.0, 1.0, 1.0]), rn
        if rn:
            self.rect = refit(self.rect, rn)
        self.w.update()

    def stop(self):
        self.rect, self._drag, self._rot, self._level, self.level_mode = None, None, None, None, False
        self.target = self.angle
        self.w.unsetCursor()
        self.w.update()

    # ---------------------------------------------------------- enderezar
    @property
    def preview(self) -> float:
        """Giro que falta por revelar (se dibuja girando la imagen mostrada)."""
        return self.target - self.angle

    def rendered(self, angle: float):
        """El anfitrión ya muestra la foto revelada con `angle`."""
        self.angle = float(angle)
        if self._rot is None and self._level is None:
            self.target = self.angle
        self.w.update()

    def set_target(self, angle: float, final: bool = True):
        """Poner un ángulo desde fuera (regulador, Auto, 0°) con la misma vista previa."""
        self.target = max(-MAX_ANGLE, min(MAX_ANGLE, float(angle)))
        if final and self.on_angle:
            self.on_angle(self.target, "end")
        self.w.update()

    @property
    def rotating(self) -> bool:
        return self._rot is not None or self._level is not None or abs(self.preview) > 1e-3

    def paint_image(self, p: QPainter, r: QRectF, pm) -> bool:
        """Dibuja la imagen girada por lo que falta por revelar. False si no hay giro pendiente (dibuja el anfitrión)."""
        d = self.preview
        if self.rect is None or pm is None or abs(d) < 1e-3:
            return False
        k = cover_scale(r.width(), r.height(), d)
        p.save()
        p.setClipRect(r, Qt.IntersectClip)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.translate(r.center())
        p.rotate(-d)            # OpenCV antihorario = Qt negativo
        p.scale(k, k)
        p.drawPixmap(QRectF(-r.width() / 2, -r.height() / 2, r.width(), r.height()), pm, QRectF(pm.rect()))
        p.restore()
        return True

    def _mouse_deg(self, pos: QPointF, r: QRectF) -> float:
        c = self._screen(r).center()
        return math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))


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
        if self.level_mode and self.on_angle:
            return "level"
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
        if cr.contains(pos):
            return "move"
        return "rotate" if self.on_angle else ""

    def press(self, pos: QPointF, r: QRectF, mods=None) -> bool:
        k = self.hit(pos, r)
        if self.on_angle and mods is not None and mods & Qt.ControlModifier:
            k = "level"
        if not k:
            return self.rect is not None  # en modo recorte, un clic fuera no hace nada más
        if k == "rotate":
            self._rot = (self.target, self._mouse_deg(pos, r))
            self.on_angle(self.target, "start")
            self.w.update()
            return True
        if k == "level":
            self._level = (QPointF(pos), QPointF(pos))
            self.w.update()
            return True
        self._drag = (k, QPointF(pos), list(self.rect))
        if self.on_change:
            self.on_change("start")
        return True

    def move(self, pos: QPointF, r: QRectF, buttons) -> bool:
        if self.rect is None:
            return False
        if self._rot is not None:
            a0, m0 = self._rot
            d = self._mouse_deg(pos, r) - m0
            d = (d + 180) % 360 - 180
            self.target = max(-MAX_ANGLE, min(MAX_ANGLE, a0 - d))  # arrastre horario = OpenCV negativo
            self.w.update()
            return True
        if self._level is not None:
            self._level = (self._level[0], QPointF(pos))
            self.w.update()
            return True
        if self._drag is None:
            k = self.hit(pos, r)
            cur = {"l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
                   "lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor, "rt": Qt.SizeBDiagCursor,
                   "lb": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor, "rotate": Qt.PointingHandCursor,
                   "level": Qt.CrossCursor}.get(k)
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
        if self._rot is not None:
            self._rot = None
            self.on_angle(round(self.target, 2), "end")
            self.w.update()
            return True
        if self._level is not None:
            a, b = self._level
            self._level, self.level_mode = None, False
            if math.hypot(b.x() - a.x(), b.y() - a.y()) >= 12:  # un clic suelto no endereza
                self.target = max(-MAX_ANGLE, min(MAX_ANGLE, self.target + level_angle(a.x(), a.y(), b.x(), b.y())))
                self.on_angle(round(self.target, 2), "end")
            self.w.update()
            return True
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
        fine = self._rot is not None or self._level is not None or self.level_mode
        n = 8 if fine else 3  # al girar, cuadrícula fina para alinear con el horizonte
        p.setPen(QPen(QColor(255, 255, 255, 60 if fine else 80), 1))
        for i in range(1, n):
            x = cr.left() + cr.width() * i / n
            y = cr.top() + cr.height() * i / n
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
        if self._level is not None:  # línea del nivel
            a, b = self._level
            for col, ww in ((QColor(0, 0, 0, 160), 4), (QColor(255, 255, 255), 2)):
                p.setPen(QPen(col, ww, Qt.DashLine))
                p.drawLine(a, b)
        if self.on_angle and (self._rot is not None or abs(self.target) > 0.005 or self._level is not None):
            txt = f"{-self.target:+.1f}°"  # en pantalla: positivo = horario, como Lightroom
            p.setRenderHint(QPainter.Antialiasing)
            box = QRectF(cr.center().x() - 34, cr.top() + 8, 68, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QColor(255, 255, 255))
            p.drawText(box, Qt.AlignCenter, txt)
        p.restore()
