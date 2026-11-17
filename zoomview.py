"""Zoom y desplazamiento comunes a todos los visores (Studio, Events, revisiÃ³n, Edits).

- Rueda: acercar/alejar hacia el cursor (encajar Â· 25 Â· 50 Â· 100 Â· 200 Â· 400 %, sobre la foto a resoluciÃ³n completa).
- Doble clic o Z: encajar â†” 100 % en el punto seÃ±alado.
- Desplazar: arrastrar con la foto ampliada, botÃ³n central, o espacio + arrastrar.
- Detalle real: la vista previa es una versiÃ³n reducida; cuando el zoom supera su resoluciÃ³n se revela a
  resoluciÃ³n completa SOLO la zona visible (`request_tile`), en segundo plano, y se dibuja encima.
"""
from __future__ import annotations

import threading

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

LEVELS = [0.25, 0.5, 1.0, 2.0, 4.0]


def _pixmap(a: np.ndarray) -> QPixmap:
    a = np.ascontiguousarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)) if a.dtype != np.uint8 \
        else np.ascontiguousarray(a)
    h, w = a.shape[:2]
    return QPixmap.fromImage(QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy())


class Zoom(QObject):
    """Estado de zoom de un visor. `full_size` = (ancho, alto) de la foto a resoluciÃ³n completa."""
    tile_ready = Signal(object, object, object)  # clave, pixmap, caja normalizada

    def __init__(self, widget, provider=None):
        super().__init__(widget)
        self.w = widget
        self.provider = provider        # provider(caja_normalizada) -> np.ndarray RGB (zona a resoluciÃ³n completa)
        self.pct: float | None = None   # None = encajar; si no, pÃ­xeles de pantalla por pÃ­xel de la foto
        self.cx, self.cy = 0.5, 0.5     # centro visible (0..1 sobre la foto mostrada)
        self.full_size = (0, 0)
        self.key = None                 # identifica la foto/estado (si cambia, la tesela deja de valer)
        self.tile = None                # (clave, pixmap, caja)
        self.panning = False
        self._last = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fetch)
        self.tile_ready.connect(self._on_tile)
        self._seq = 0

    # ---------------------------------------------------------- geometrÃ­a
    @property
    def active(self) -> bool:
        return self.pct is not None

    def reset(self, key=None, full_size=None):
        if key != self.key:
            self.tile = None
        self.key = key
        if full_size:
            self.full_size = full_size

    def rect(self, area: QRectF, pm_size) -> QRectF:
        """RectÃ¡ngulo (puede salirse del visor) donde se dibuja la foto entera."""
        pw, ph_ = pm_size.width(), pm_size.height()
        if pw <= 0 or ph_ <= 0:
            return QRectF()
        if self.pct is None or not self.full_size[0]:
            s = min(area.width() / pw, area.height() / ph_)
            return QRectF(area.x() + (area.width() - pw * s) / 2, area.y() + (area.height() - ph_ * s) / 2,
                          pw * s, ph_ * s)
        fw = self.full_size[0] * self.pct
        fh = fw * ph_ / pw
        x = area.center().x() - self.cx * fw
        y = area.center().y() - self.cy * fh
        # sin huecos: si la foto es mÃ¡s grande que el visor, que lo cubra
        if fw > area.width():
            x = min(area.x(), max(area.right() - fw, x))
        else:
            x = area.x() + (area.width() - fw) / 2
        if fh > area.height():
            y = min(area.y(), max(area.bottom() - fh, y))
        else:
            y = area.y() + (area.height() - fh) / 2
        return QRectF(x, y, fw, fh)

    def fit_pct(self, area: QRectF, pm_size) -> float:
        pw, ph_ = pm_size.width(), pm_size.height()
        s = min(area.width() / pw, area.height() / ph_)
        return pw * s / max(1, self.full_size[0])

    def visible_box(self, area: QRectF, pm_size):
        r = self.rect(area, pm_size)
        if r.width() <= 0:
            return (0.0, 0.0, 1.0, 1.0)
        x0 = max(0.0, (area.x() - r.x()) / r.width())
        y0 = max(0.0, (area.y() - r.y()) / r.height())
        x1 = min(1.0, (area.right() - r.x()) / r.width())
        y1 = min(1.0, (area.bottom() - r.y()) / r.height())
        return (x0, y0, x1, y1)

    # ---------------------------------------------------------- interacciÃ³n
    def _set_level(self, pct, area, pm_size, at: QPointF | None):
        r = self.rect(area, pm_size)
        if at is not None and r.width() > 0:
            u = (at.x() - r.x()) / r.width()
            v = (at.y() - r.y()) / r.height()
        else:
            u, v = self.cx, self.cy
        self.pct = pct
        if pct is not None:
            fw = self.full_size[0] * pct
            fh = fw * pm_size.height() / max(1, pm_size.width())
            if at is not None:
                self.cx = u - (at.x() - area.center().x()) / fw
                self.cy = v - (at.y() - area.center().y()) / fh
            self.cx, self.cy = min(1, max(0, self.cx)), min(1, max(0, self.cy))
        else:
            self.cx = self.cy = 0.5
        self.schedule()

    def wheel(self, delta: int, at: QPointF, area: QRectF, pm_size):
        if not self.full_size[0]:
            return
        fit = self.fit_pct(area, pm_size)
        cur = self.pct if self.pct is not None else fit
        if delta > 0:
            nxt = next((lv for lv in LEVELS if lv > cur * 1.01 and lv > fit * 1.01), None)
            if nxt is None:
                return
            self._set_level(nxt, area, pm_size, at)
        else:
            nxt = next((lv for lv in reversed(LEVELS) if lv < cur * 0.99), None)
            self._set_level(None if nxt is None or nxt <= fit else nxt, area, pm_size, at)

    def toggle(self, at: QPointF | None, area: QRectF, pm_size):
        if not self.full_size[0]:
            return
        self._set_level(None if self.pct is not None else 1.0, area, pm_size, at)

    def start_pan(self, pos: QPointF):
        self.panning, self._last = True, pos

    def pan(self, pos: QPointF, area: QRectF, pm_size):
        if not self.panning or self._last is None:
            return
        r = self.rect(area, pm_size)
        if r.width() > 0:
            self.cx -= (pos.x() - self._last.x()) / r.width()
            self.cy -= (pos.y() - self._last.y()) / r.height()
            self.cx, self.cy = min(1, max(0, self.cx)), min(1, max(0, self.cy))
        self._last = pos

    def end_pan(self):
        if self.panning:
            self.panning = False
            self.schedule()

    def label(self, area: QRectF, pm_size) -> str:
        if self.pct is None:
            return "Encajar"
        return f"{self.pct * 100:.0f} %"

    # ---------------------------------------------------------- detalle a resoluciÃ³n completa
    def schedule(self):
        self.w.update()
        if self.provider is not None:
            self._timer.start(280)

    def _fetch(self):
        if self.pct is None or self.provider is None or not self.full_size[0]:
            return
        area = QRectF(self.w.rect()).adjusted(12, 12, -12, -12)
        pm = getattr(self.w, "zoom_pixmap", lambda: None)()
        if pm is None:
            return
        # solo hace falta cuando el zoom supera la resoluciÃ³n de la vista previa
        if self.pct * self.full_size[0] <= pm.width() * 1.15:
            return
        box = self.visible_box(area, pm.size())
        m = 0.04  # un poco de margen alrededor para poder desplazar sin ver bordes
        box = (max(0, box[0] - m), max(0, box[1] - m), min(1, box[2] + m), min(1, box[3] + m))
        self._seq += 1
        seq, key = self._seq, self.key

        def job():
            try:
                img = self.provider(box)
            except Exception:
                img = None
            if img is not None and seq == self._seq:
                self.tile_ready.emit(key, _pixmap(img), box)

        threading.Thread(target=job, daemon=True).start()

    def _on_tile(self, key, pm, box):
        if key == self.key:
            self.tile = (key, pm, box)
            self.w.update()

    def draw_tile(self, p: QPainter, r: QRectF):
        if self.tile is None or self.pct is None or self.tile[0] != self.key:
            return
        _, pm, (x0, y0, x1, y1) = self.tile
        p.drawPixmap(QRectF(r.x() + x0 * r.width(), r.y() + y0 * r.height(), (x1 - x0) * r.width(),
                            (y1 - y0) * r.height()).toRect(), pm)

    def draw_badge(self, p: QPainter, area: QRectF, pm_size, busy: bool = False):
        if self.pct is None:
            return
        txt = self.label(area, pm_size) + ("  Â· cargando detalleâ€¦" if busy else "")
        f = QFont(p.font())
        f.setPointSizeF(8.5)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(txt) + 16
        box = QRectF(area.right() - w - 8, area.bottom() - 30, w, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(box, 3, 3)
        p.setPen(QColor(235, 235, 235))
        p.drawText(box, Qt.AlignCenter, txt)


# ------------------------------------------------------------------ zona a resoluciÃ³n completa

def region(lin_full: np.ndarray, angle: float, box_rot: tuple, develop_fn, safe_rect_fn, margin: int = 24):
    """Revela solo la zona `box_rot` (0..1 sobre la foto ya girada y recortada a su rectÃ¡ngulo Ãºtil) a
    resoluciÃ³n completa. `develop_fn(lin_zona)` revela sin girar. Resuelve el giro con una afinidad exacta,
    igual que `engine.rotate` (giro sobre el centro de la foto completa + recorte del rectÃ¡ngulo Ãºtil)."""
    H, W = lin_full.shape[:2]
    sx0, sy0, sx1, sy1 = safe_rect_fn(W, H, angle)
    # esquinas en píxeles enteros de la foto girada: así la zona cae exactamente sobre los píxeles del revelado
    u0 = sx0 + round(box_rot[0] * (sx1 - sx0))
    v0 = sy0 + round(box_rot[1] * (sy1 - sy0))
    u1 = sx0 + round(box_rot[2] * (sx1 - sx0))
    v1 = sy0 + round(box_rot[3] * (sy1 - sy0))
    ow, oh = max(1, int(round(u1 - u0))), max(1, int(round(v1 - v0)))
    if abs(angle) < 1e-3:
        x0, y0 = int(max(0, u0)), int(max(0, v0))
        return np.clip(develop_fn(lin_full[y0:y0 + oh, x0:x0 + ow]), 0, 1)
    M = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)
    Mi = cv2.invertAffineTransform(M)
    pts = np.array([[u0, v0], [u1, v0], [u0, v1], [u1, v1]], np.float32)
    src = pts @ Mi[:, :2].T + Mi[:, 2]
    bx0 = int(max(0, np.floor(src[:, 0].min()) - margin))
    by0 = int(max(0, np.floor(src[:, 1].min()) - margin))
    bx1 = int(min(W, np.ceil(src[:, 0].max()) + margin))
    by1 = int(min(H, np.ceil(src[:, 1].max()) + margin))
    dev = develop_fn(lin_full[by0:by1, bx0:bx1])
    M2 = M.copy()
    M2[:, 2] = M[:, 2] + M[:, :2] @ np.array([bx0, by0], np.float64) - np.array([u0, v0])
    out = cv2.warpAffine(np.ascontiguousarray(dev, dtype=np.float32), M2, (ow, oh), flags=cv2.INTER_LANCZOS4,
                         borderMode=cv2.BORDER_REFLECT)
    return np.clip(out, 0, 1)
