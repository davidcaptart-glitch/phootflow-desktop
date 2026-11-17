"""Revisión antes de exportar (Events): foto a foto con la edición final, aprobar/rechazar, B/N, recortes y
paso a Studio para retocar las que no convencen."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QListView, QListWidget,
                               QListWidgetItem, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

import compare as CMP
import zoomview as Z
import engine as E
import theme as T

ROLE = Qt.UserRole + 11
FILTERS = ["Todas", "Aprobadas", "Rechazadas", "Pendientes de revisar"]


def _pm(a: np.ndarray) -> QPixmap:
    a = np.ascontiguousarray(a if a.dtype == np.uint8 else (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))
    h, w = a.shape[:2]
    return QPixmap.fromImage(QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy())


def crop(a: np.ndarray, box) -> np.ndarray:
    x0, y0, x1, y1 = box
    h, w = a.shape[:2]
    return a[int(round(y0 * h)):max(int(round(y0 * h)) + 1, int(round(y1 * h))),
             int(round(x0 * w)):max(int(round(x0 * w)) + 1, int(round(x1 * w)))]


def _btn(text, kind="", tip=""):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    return b


class StripDelegate(QStyledItemDelegate):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg

    def sizeHint(self, *_):
        return QSize(120, 96)

    def paint(self, p, opt, idx):
        ph = self.dlg.photos[idx.data(ROLE)]
        r = opt.rect.adjusted(3, 3, -3, -3)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        pm = self.dlg.main.thumb_pixmap(ph)
        if pm:
            s = pm.size().scaled(r.size(), Qt.KeepAspectRatio)
            tr = QRectF(r.x() + (r.width() - s.width()) / 2, r.y() + (r.height() - s.height()) / 2,
                        s.width(), s.height())
            p.drawPixmap(tr.toRect(), pm)
            if not ph.keep:
                p.fillRect(tr, QColor(5, 7, 15, 140))
        col = T.OK if ph.keep else T.DANGER
        if not getattr(ph, "reviewed", False):
            col = T.MUTED
        p.setPen(QPen(QColor(col), 3 if opt.state & QStyle.State_Selected else 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(r), 6, 6)
        if self.dlg.is_studio(ph):
            p.setPen(QColor(T.ACCENT))
            p.drawText(r.adjusted(4, 2, -4, -2), Qt.AlignRight | Qt.AlignTop, "S")
        p.restore()


class Big(QWidget):
    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pm, self.text, self.rejected = None, "", False
        self.before = None
        self.cmp = CMP.CompareState()
        self.zoom = Z.Zoom(self)
        self.r = QRectF()

    def set(self, pm, text="", rejected=False, before=None):
        self.pm, self.text, self.rejected, self.before = pm, text, rejected, before
        self.update()

    def zoom_pixmap(self):
        return self.pm

    def _area(self):
        return QRectF(self.rect()).adjusted(12, 12, -12, -12)

    def wheelEvent(self, e):
        if self.pm is not None:
            self.zoom.wheel(e.angleDelta().y(), e.position(), self._area(), self.pm.size())

    def mouseDoubleClickEvent(self, e):
        if self.pm is not None:
            self.zoom.toggle(e.position(), self._area(), self.pm.size())

    def mousePressEvent(self, e):
        if self.zoom.active and (e.button() == Qt.MiddleButton or not self.cmp.split):
            self.zoom.start_pan(e.position())
            return
        if CMP.drag(self.cmp, self.r, e.position().x(), e.position().y()):
            self.cmp.dragging = True
            self.update()

    def mouseMoveEvent(self, e):
        if self.zoom.panning:
            self.zoom.pan(e.position(), self._area(), self.pm.size())
            return self.update()
        if self.cmp.dragging and CMP.drag(self.cmp, self.r, e.position().x(), e.position().y()):
            self.update()

    def mouseReleaseEvent(self, e):
        self.zoom.end_pan()
        self.cmp.dragging = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(T.BG))
        if self.pm:
            area = self._area()
            r = self.zoom.rect(area, self.pm.size())
            self.r = r
            p.setClipRect(area)
            CMP.paint(p, r, self.pm, self.before, self.cmp)
            if not self.cmp.active:
                self.zoom.draw_tile(p, r)
            p.setClipping(False)
            self.zoom.draw_badge(p, area, self.pm.size())
            if self.rejected:
                p.fillRect(r, QColor(5, 7, 15, 110))
                p.setPen(QPen(QColor(T.DANGER), 3))
                p.drawRect(r)
        p.setPen(QColor(T.MUTED))
        p.drawText(self.rect().adjusted(16, 0, -16, -8), Qt.AlignBottom | Qt.AlignLeft, self.text)


class ReviewDialog(QDialog):
    """Ventana de revisión. Resultado: `action` = "export" (exportar aprobadas), "studio" (retocar
    `studio_photo` en Studio) o "" (cerrar sin exportar). Los cambios (aprobar/rechazar, recorte) se
    aplican directamente a las fotos."""
    studio_requested = Signal(object)

    def __init__(self, main, render_fn, studio_render_fn, is_studio_fn, start=0, bw_style="Editorial",
                 bw_on=False, look="Natural", before_fn=None, tile_fn=None):
        super().__init__(main)
        self.setWindowTitle("Revisión antes de exportar")
        self.setWindowState(Qt.WindowMaximized)
        self.main = main
        self.render_fn, self.studio_render_fn, self.is_studio = render_fn, studio_render_fn, is_studio_fn
        self.photos = [p for p in main.photos if p.thumb and not p.error]
        self.action, self.studio_photo = "", None
        self.bw = False
        self.bw_style = bw_style  # estilo de B/N de la sesión
        self.bw_on = bw_on        # ¿se exporta versión B/N?
        self.look = look          # look de color de la sesión
        self.before_fn = before_fn  # foto tal como salió de la cámara (para comparar)
        self.tile_fn = tile_fn      # tile_fn(ph, caja en la foto girada, bw) -> zona a resolución completa
        self._before: dict = {}
        self._cache: dict = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        top = QHBoxLayout()
        self.title = QLabel("Revisión")
        self.title.setObjectName("h1")
        self.counts = QLabel("")
        self.counts.setObjectName("muted")
        self.filter = QComboBox()
        self.filter.addItems(FILTERS)
        self.filter.currentIndexChanged.connect(self.apply_filter)
        self.b_bw = _btn("Ver B/N", "ghost", "Alternar color / blanco y negro editorial (B)")
        self.b_bw.setCheckable(True)
        self.b_bw.toggled.connect(self.set_bw)
        b_close = _btn("Cerrar", "ghost")
        b_close.clicked.connect(self.reject)
        self.b_export = _btn("Exportar aprobadas", "primary")
        self.b_export.clicked.connect(self._export)
        top.addWidget(self.title)
        top.addSpacing(12)
        top.addWidget(self.counts, 1)
        top.addWidget(self.filter)
        self.cmp_bar = CMP.CompareBar()
        self.cmp_bar.changed.connect(self.set_compare)
        top.addWidget(self.cmp_bar)
        top.addWidget(self.b_bw)
        top.addWidget(b_close)
        top.addWidget(self.b_export)
        v.addLayout(top)

        mid = QHBoxLayout()
        self.big = Big()
        mid.addWidget(self.big, 1)
        side = QFrame()
        side.setObjectName("previewPane")
        side.setFixedWidth(330)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 12, 14, 12)
        sv.setSpacing(10)
        self.name = QLabel("")
        self.name.setObjectName("h1")
        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        row = QHBoxLayout()
        self.b_ok = _btn("✓ Aprobar", "primary", "A")
        self.b_no = _btn("✕ Rechazar", "", "R")
        self.b_ok.clicked.connect(lambda: self.decide(True))
        self.b_no.clicked.connect(lambda: self.decide(False))
        row.addWidget(self.b_ok)
        row.addWidget(self.b_no)
        self.b_studio = _btn("Editar en Studio", "ghost", "Retocar esta foto foto a foto en Studio (E)")
        self.b_studio.clicked.connect(self._studio)
        self.bw_t = QLabel("ESTILO B/N DE ESTA FOTO")
        self.bw_t.setObjectName("cardTitle")
        self.bw_list = QListWidget()
        self.bw_list.setViewMode(QListView.IconMode)
        self.bw_list.setResizeMode(QListView.Adjust)
        self.bw_list.setMovement(QListView.Static)
        self.bw_list.setWrapping(True)
        self.bw_list.setIconSize(QSize(88, 70))
        self.bw_list.setGridSize(QSize(100, 96))
        self.bw_list.setFixedHeight(214)
        self.bw_list.itemClicked.connect(self._pick_bw)
        crops_t = QLabel("RECORTE")
        crops_t.setObjectName("cardTitle")
        self.crops = QListWidget()
        self.crops.setViewMode(QListView.IconMode)
        self.crops.setResizeMode(QListView.Adjust)
        self.crops.setMovement(QListView.Static)
        self.crops.setIconSize(QSize(118, 88))
        self.crops.setGridSize(QSize(140, 116))
        self.crops.setSpacing(2)
        self.crops.setWrapping(True)
        self.crops.setFlow(QListView.LeftToRight)
        self.crops.itemClicked.connect(self._pick_crop)
        for w in (self.name, self.state, self.info):
            sv.addWidget(w)
        sv.addLayout(row)
        sv.addWidget(self.b_studio)
        sv.addSpacing(6)
        sv.addWidget(self.bw_t)
        sv.addWidget(self.bw_list)
        sv.addWidget(crops_t)
        sv.addWidget(self.crops, 1)
        mid.addWidget(side)
        v.addLayout(mid, 1)

        self.strip = QListWidget()
        self.strip.setViewMode(QListView.IconMode)
        self.strip.setFlow(QListView.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setFixedHeight(112)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.strip.setItemDelegate(StripDelegate(self))
        self.strip.setUniformItemSizes(True)
        for i, ph in enumerate(self.photos):
            it = QListWidgetItem()
            it.setData(ROLE, i)
            it.setSizeHint(QSize(120, 96))
            self.strip.addItem(it)
        self.strip.currentRowChanged.connect(self.show_photo)
        v.addWidget(self.strip)

        for k, fn in (("A", lambda: self.decide(True)), ("R", lambda: self.decide(False)), ("B", self.b_bw.toggle),
                      ("\\", lambda: self.set_compare("off" if self.big.cmp.mode == "full" else "full")),
                      ("Y", lambda: (self.big.cmp.next_split(), self.set_compare(self.big.cmp.mode))),
                      ("Z", lambda: self.big.zoom.toggle(None, self.big._area(), self.big.pm.size())
                       if self.big.pm is not None else None),
                      ("E", self._studio), (Qt.Key_Right, lambda: self.step(1)), (Qt.Key_Left, lambda: self.step(-1))):
            QShortcut(QKeySequence(k), self, fn)
        self.b_bw.setEnabled(True)
        self.refresh_counts()
        if self.photos:
            self.strip.setCurrentRow(max(0, min(start, len(self.photos) - 1)))

    # ---------------------------------------------------------- navegación
    def cur(self):
        r = self.strip.currentRow()
        return self.photos[self.strip.item(r).data(ROLE)] if r >= 0 else None

    def step(self, d):
        r = self.strip.currentRow()
        n = self.strip.count()
        for _ in range(n):
            r = (r + d) % n
            if not self.strip.item(r).isHidden():
                self.strip.setCurrentRow(r)
                return

    def apply_filter(self):
        f = self.filter.currentIndex()
        for row in range(self.strip.count()):
            ph = self.photos[self.strip.item(row).data(ROLE)]
            rev = getattr(ph, "reviewed", False)
            show = [True, ph.keep, not ph.keep, not rev][f]
            self.strip.item(row).setHidden(not show)

    def refresh_counts(self):
        ok = sum(p.keep for p in self.photos)
        rev = sum(1 for p in self.photos if getattr(p, "reviewed", False))
        self.counts.setText(f"{ok} aprobadas · {len(self.photos) - ok} rechazadas · {len(self.photos) - rev} sin revisar")
        self.b_export.setText(f"Exportar {ok} aprobadas")
        self.b_export.setEnabled(ok > 0)

    # ---------------------------------------------------------- foto actual
    def _base(self, ph):
        """Foto revelada entera (sin recortar) con el estilo de la sesión, en color o B/N."""
        key = (ph.path, round(ph.ev, 2), round(ph.angle, 2), self.is_studio(ph))
        if key not in self._cache:
            a = self.studio_render_fn(ph, False) if self.is_studio(ph) else self.render_fn(ph, False)
            self._cache[key] = a
            if len(self._cache) > 40:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[key]

    def show_photo(self, row=None):
        ph = self.cur()
        if not ph:
            return
        self.title.setText(f"Revisión · {self.strip.currentRow() + 1}/{len(self.photos)}")
        self.name.setText(ph.name)
        studio = self.is_studio(ph)
        st = ("✓ Aprobada" if ph.keep else "✕ Rechazada") + ("" if getattr(ph, "reviewed", False) else " (propuesta)")
        self.state.setText(f"<span style='color:{T.OK if ph.keep else T.DANGER}; font-weight:600'>{st}</span>")
        bits = [ph.reason] if ph.reason else []
        if ph.warn:
            bits.append("⚠ " + ph.warn)
        if studio:
            bits.append("Editada en Studio: se exportará con esa edición.")
        self.info.setText("\n".join(bits))
        base = self._base(ph)
        box = (0.0, 0.0, 1.0, 1.0) if studio else ph.crops.get(ph.best, (0.0, 0.0, 1.0, 1.0))
        color = crop(base, box)
        if not studio:
            color = E.apply_look(color.astype(np.float32) / 255.0 if color.dtype == np.uint8 else color,
                                 self.photo_look(ph))
        img = color
        bws = self.photo_bw(ph)
        label = f"Formato: {'Studio' if studio else ph.best}" + ("" if studio else f"  ·  Look {self.photo_look(ph)}")
        if self.bw:
            if bws == E.BW_NONE:
                label += "  ·  sin versión B/N"
            else:
                img = E.bw_editorial(color, seed=ph.dhash & 0xFFFFFFFF, style=bws)
                label += f"  ·  B/N {bws}"
        before = None
        if self.big.cmp.active and self.before_fn and not studio:
            b = self._before.get(ph.path)
            if b is None:
                b = self._before[ph.path] = self.before_fn(ph)
                if len(self._before) > 20:
                    self._before.pop(next(iter(self._before)))
            before = _pm(crop(b, box))
        self.big.set(_pm(img), label, rejected=not ph.keep, before=before)
        if self.tile_fn and not studio:
            fw, fh = E.full_size(ph.path)
            self.big.zoom.reset(key=(ph.path, ph.best, self.bw, self.photo_look(ph), self.photo_bw(ph)),
                                full_size=(int(fw * (box[2] - box[0])) or fw, fh))
            self.big.zoom.provider = lambda b, ph=ph, box=box: self.tile_fn(
                ph, (box[0] + b[0] * (box[2] - box[0]), box[1] + b[1] * (box[3] - box[1]),
                     box[0] + b[2] * (box[2] - box[0]), box[1] + b[3] * (box[3] - box[1])), self.bw)
            self.big.zoom.schedule()
        else:
            self.big.zoom.provider = None
        self._fill_crops(ph, base, studio)
        self._fill_bw(ph, color if self.bw else crop(base, box), studio)

    def _fill_crops(self, ph, base, studio):
        self.crops.clear()
        if studio:
            self.crops.addItem(QListWidgetItem("El recorte se ajusta en Studio"))
            return
        from PySide6.QtGui import QIcon
        small = base[::4, ::4] if max(base.shape[:2]) > 1200 else base
        for name, box in ph.crops.items():
            it = QListWidgetItem(QIcon(_pm(crop(small, box))), ("● " if name == ph.best else "") + name)
            it.setData(ROLE, name)
            it.setToolTip("Recorte sugerido" if name == ph.best else f"Usar {name}")
            self.crops.addItem(it)

    def photo_bw(self, ph) -> str:
        return ph.bw_style or self.bw_style or "Editorial"

    def photo_look(self, ph) -> str:
        return ph.look or self.look or "Natural"

    def _fill_bw(self, ph, img, studio=False):
        """Propuestas de estilo para esta foto: estilos de B/N en la vista B/N, looks de color en la de color."""
        from PySide6.QtGui import QIcon
        self.bw_list.clear()
        if studio and not self.bw:
            self.bw_t.setText("LOOK: SE AJUSTA EN STUDIO")
            return
        small = img.astype(np.float32) / 255.0 if img.dtype == np.uint8 else img
        f = 160 / max(small.shape[:2])
        if f < 1:
            import cv2
            small = cv2.resize(small, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        if self.bw:
            self.bw_t.setText("ESTILO B/N DE ESTA FOTO")
            cur, session, names = self.photo_bw(ph), self.bw_style, list(E.BW_STYLES) + [E.BW_NONE]
            make = lambda n: small if n == E.BW_NONE else E.bw_editorial(small, seed=1, style=n)  # noqa: E731
        else:
            self.bw_t.setText("LOOK DE COLOR DE ESTA FOTO")
            cur, session, names = self.photo_look(ph), self.look, list(E.COLOR_LOOKS)
            make = lambda n: E.apply_look(small, n)  # noqa: E731
        for name in names:
            label = ("● " if name == cur else "") + name + (" (sesión)" if name == session else "")
            it = QListWidgetItem(QIcon(_pm(make(name))), label)
            it.setData(ROLE, name)
            self.bw_list.addItem(it)

    def _pick_bw(self, it):
        ph = self.cur()
        name = it.data(ROLE)
        if not ph or not name:
            return
        if self.bw:
            ph.bw_style = "" if name == self.bw_style else name
        else:
            ph.look = "" if name == self.look else name
        self.show_photo()

    def _pick_crop(self, it):
        name = it.data(ROLE)
        ph = self.cur()
        if ph and name and name in ph.crops:
            ph.best = name
            self.show_photo()

    def set_compare(self, mode):
        self.big.cmp.mode = mode
        self.cmp_bar.set_mode(mode)
        self.show_photo()

    def decide(self, keep: bool):
        ph = self.cur()
        if not ph:
            return
        ph.keep = keep
        ph.reviewed = True
        ph.reason = "" if keep else (ph.reason or "Rechazada en la revisión")
        self.refresh_counts()
        self.strip.viewport().update()
        self.step(1)

    def set_bw(self, on):
        self.bw = on
        self.show_photo()

    def _studio(self):
        ph = self.cur()
        if ph:
            self.action, self.studio_photo = "studio", ph
            self.accept()

    def _export(self):
        self.action = "export"
        self.accept()

    def current_index(self) -> int:
        ph = self.cur()
        return self.photos.index(ph) if ph in self.photos else 0


def pending_studio_paths(folder: str) -> dict:
    """{ruta: ajustes} de las fotos que se retocaron en Studio para usarlas en Events."""
    import studio_engine as SE
    out = {}
    d = SE.settings_dir(folder)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".json"):
                s = SE.load(folder, f[:-5])
                if s and s.get("_events_override"):
                    out[os.path.normcase(os.path.join(folder, f[:-5]))] = s
    return out


__all__ = ["ReviewDialog", "pending_studio_paths", "E"]
