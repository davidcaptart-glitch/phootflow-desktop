"""Revisión antes de exportar (Events): foto a foto con la edición final, aprobar/rechazar, B/N, recortes y
paso a Studio para retocar las que no convencen."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
                               QLabel, QListView, QListWidget, QListWidgetItem, QPushButton, QRadioButton,
                               QSizePolicy, QSlider, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

import compare as CMP
import cropview as CV
import icons as I
import zoomview as Z
import engine as E
import theme as T

ROLE = Qt.UserRole + 11
FILTERS = ["Todas", "Aprobadas", "Rechazadas", "Pendientes de revisar"]


def _pm(a: np.ndarray) -> QPixmap:
    a = np.ascontiguousarray(a if a.dtype == np.uint8 else (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))
    h, w = a.shape[:2]
    return QPixmap.fromImage(QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy())


STUDIO_COLOR = "Color"


def crop(a: np.ndarray, box) -> np.ndarray:
    x0, y0, x1, y1 = box
    h, w = a.shape[:2]
    return a[int(round(y0 * h)):max(int(round(y0 * h)) + 1, int(round(y1 * h))),
             int(round(x0 * w)):max(int(round(x0 * w)) + 1, int(round(x1 * w)))]


def _ibtn(icon, tip, checkable=False, text="", kind=""):
    b = _btn(("  " + text) if text else "", kind or ("iconBtn" if not text else "ghost"), tip)
    b.setIcon(I.icon(icon, 18))
    b.setIconSize(QSize(18, 18))
    b.setCheckable(checkable)
    return b


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
        self.crop = CV.CropTool(self)
        self.setMouseTracking(True)
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
        if self.crop.active and e.button() == Qt.LeftButton:
            self.crop.press(e.position(), self.r, e.modifiers())
            return
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
        if self.crop.active:
            self.crop.move(e.position(), self.r, e.buttons())
            return
        if self.cmp.dragging and CMP.drag(self.cmp, self.r, e.position().x(), e.position().y()):
            self.update()

    def mouseReleaseEvent(self, e):
        if self.crop.active:
            self.crop.release()
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
            if not (self.crop.active and self.crop.paint_image(p, r, self.pm)):
                CMP.paint(p, r, self.pm, self.before, self.cmp)
            if not self.cmp.active and not self.crop.active:
                self.zoom.draw_tile(p, r)
            self.crop.paint(p, r)
            p.setClipping(False)
            self.zoom.draw_badge(p, area, self.pm.size())
            if self.rejected:
                p.fillRect(r, QColor(5, 7, 15, 110))
                p.setPen(QPen(QColor(T.DANGER), 3))
                p.drawRect(r)
        p.setPen(QColor(T.MUTED))
        p.drawText(self.rect().adjusted(16, 0, -16, -8), Qt.AlignBottom | Qt.AlignLeft, self.text)


class ReviewDialog(QDialog):
    """Ventana de revisión. Resultado: `action` = "export" (exportar aprobadas), "studio" / "edits" (retocar
    `studio_photo` en Studio o en Edits) o "" (cerrar sin exportar). Los cambios (aprobar/rechazar, recorte) se
    aplican directamente a las fotos."""
    studio_requested = Signal(object)

    def __init__(self, main, render_fn, studio_render_fn, is_studio_fn, start=0, bw_style="Editorial",
                 bw_on=False, look="Natural", before_fn=None, tile_fn=None, on_change=None, prop=None):
        super().__init__(main)
        self.on_change = on_change or (lambda: None)  # guarda la revisión (se puede seguir otro día)
        self.prop = prop                              # propagate.Propagator: copiar la edición a otras fotos
        # portapapeles de ediciones, como el de Windows: dura mientras la carpeta esté abierta (aunque se cierre
        # y se vuelva a abrir la revisión o se pase por Studio), se pega cuantas veces haga falta y copiar otra
        # edición lo sustituye
        held = getattr(main, "_edit_clip", None)
        self.clip = held[1] if held and prop is not None and held[0] == prop.folder else None
        self.crop_on, self._crop_for = False, None
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
        self.b_bw = _ibtn("bw", "Ver en blanco y negro editorial (B)", True)
        self.b_bw.toggled.connect(self.set_bw)
        b_close = _btn("  Guardar y cerrar", "ghost", "Todo lo decidido se guarda solo: al volver a abrir la carpeta "
                                                    "y pulsar «Revisar», sigues por la primera sin revisar")
        b_close.clicked.connect(self.reject)
        b_close.setIcon(I.icon("check", 16))
        self.b_export = _btn("Exportar aprobadas", "primary")
        self.b_export.setIcon(I.icon("export", 16, "#ffffff"))
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
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.big, 1)
        self.dock = QFrame()
        self.dock.setObjectName("modulePill")
        dk = QHBoxLayout(self.dock)
        dk.setContentsMargins(6, 5, 6, 5)
        dk.setSpacing(4)
        drow = QHBoxLayout()
        drow.addStretch(1)
        drow.addWidget(self.dock)
        drow.addStretch(1)
        left.addLayout(drow)
        mid.addLayout(left, 1)
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
        self.b_ok = _btn("  Aprobar", "approve", "Aprobar y pasar a la siguiente (A)")
        self.b_no = _btn("  Rechazar", "reject", "Rechazar y pasar a la siguiente (R)")
        for b_, ic in ((self.b_ok, "check"), (self.b_no, "x")):
            b_.setIcon(I.icon(ic, 18, "#ffffff", "#ffffff"))
            b_.setIconSize(QSize(18, 18))
            b_.setCheckable(True)
            b_.setMinimumWidth(150)
        self.b_ok.clicked.connect(lambda: self.decide(True))
        self.b_no.clicked.connect(lambda: self.decide(False))
        row.addWidget(self.b_ok)
        row.addWidget(self.b_no)
        self.b_studio = _ibtn("studio", "Editar en Studio: revelado foto a foto (E)", text="Editar en Studio")
        self.b_studio.clicked.connect(self._studio)
        self.b_edits = _ibtn("edits", "Editar en Edits: corrector, piel, licuar, texto… (Mayús+E)", text="Editar en Edits")
        self.b_edits.clicked.connect(self._edits)
        self.prop_t = QLabel("PROPAGAR LA EDICIÓN")
        self.prop_t.setObjectName("cardTitle")
        prow = QHBoxLayout()
        self.b_pcopy = _ibtn("dupes", "Copiar la edición de Studio/Edits de esta foto, eligiendo qué (Ctrl+C)",
                             text="Copiar")
        self.b_ppaste = _ibtn("paste", "Pegar la edición copiada en esta foto y aprobarla; te quedas en ella para ver el resultado (Ctrl+V)",
                              text="Pegar")
        self.b_papply = _ibtn("sync", "Aplicar la edición copiada a varias fotos a la vez", text="A varias…")
        self.b_pcopy.clicked.connect(self._copy_edit)
        self.b_ppaste.clicked.connect(self._paste_edit)
        self.b_papply.clicked.connect(self._apply_edit)
        for b in (self.b_pcopy, self.b_ppaste, self.b_papply):
            prow.addWidget(b)
        self.prop_info = QLabel("")
        self.prop_info.setObjectName("muted")
        self.prop_info.setWordWrap(True)
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
        crow = QHBoxLayout()
        self.b_crop = _ibtn("crop", "Ajustar el recorte: marco sobre la foto entera, muévelo y cambia el tamaño (C). "
                                    "Intro: listo", True, text="Recortar")
        self.b_crop.toggled.connect(self.set_crop_mode)
        self.ratio_cb = QComboBox()
        self.ratio_cb.addItems(CV.RATIOS)
        self.ratio_cb.setToolTip("Proporción del marco")
        self.ratio_cb.activated.connect(self._ratio_changed)
        self.b_flip = _ibtn("sync", "Vertical / horizontal", True)
        self.b_flip.clicked.connect(self._ratio_changed)
        crow.addWidget(self.ratio_cb, 1)
        crow.addWidget(self.b_flip)
        self.big.crop.on_change = self._on_crop
        self.big.crop.on_angle = self._crop_angle
        arow = QHBoxLayout()
        self.ang = QSlider(Qt.Horizontal)
        self.ang.setRange(-450, 450)  # décimas de grado
        self.ang.setToolTip("Enderezar (también arrastrando fuera del marco)")
        self.ang.valueChanged.connect(self._ang_moved)
        self.ang.sliderReleased.connect(lambda: self._crop_angle(-self.ang.value() / 10, "end"))
        self.ang_lbl = QLabel("0,0°")
        self.ang_lbl.setObjectName("muted")
        self.ang_lbl.setFixedWidth(44)
        self.b_level = _ibtn("sliders", "Nivel: traza una línea sobre el horizonte o una vertical (o Ctrl + arrastrar)",
                             True)
        self.b_level.toggled.connect(self._level_mode)
        b_auto = _ibtn("auto", "Enderezar solo (busca las verticales)")
        b_auto.clicked.connect(self._auto_straighten)
        b_zero = _btn("0°", "ghost", "Quitar el giro")
        b_zero.clicked.connect(lambda: self._crop_angle(0.0, "end"))
        arow.addWidget(QLabel("Enderezar"))
        arow.addWidget(self.ang, 1)
        arow.addWidget(self.ang_lbl)
        for b_ in (self.b_level, b_auto, b_zero):
            arow.addWidget(b_)
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
        # el dock: aprobar / rechazar, y las acciones de esta foto como iconos
        dk.addWidget(self.b_ok)
        dk.addWidget(self.b_no)
        sep = QLabel("│")
        sep.setObjectName("stepLine")
        dk.addWidget(sep)
        for b_ in (self.b_crop, self.b_studio, self.b_edits):
            dk.addWidget(b_)
        if self.prop is not None:
            sv.addWidget(self.prop_t)
            sv.addLayout(prow)
            sv.addWidget(self.prop_info)
        sv.addSpacing(6)
        sv.addWidget(self.bw_t)
        sv.addWidget(self.bw_list)
        sv.addWidget(crops_t)
        sv.addLayout(crow)
        sv.addLayout(arow)
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
        self.strip.currentRowChanged.connect(lambda _: self._state_buttons())
        from PySide6.QtWidgets import QProgressBar
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        v.addWidget(self.progress)
        v.addWidget(self.strip)

        for k, fn in (("A", lambda: self.decide(True)), ("R", lambda: self.decide(False)), ("B", self.b_bw.toggle),
                      ("\\", lambda: self.set_compare("off" if self.big.cmp.mode == "full" else "full")),
                      ("Y", lambda: (self.big.cmp.next_split(), self.set_compare(self.big.cmp.mode))),
                      ("Z", lambda: self.big.zoom.toggle(None, self.big._area(), self.big.pm.size())
                       if self.big.pm is not None else None),
                      ("E", self._studio), ("Shift+E", self._edits), ("C", self.b_crop.toggle),
                      ("Ctrl+C", self._copy_edit), ("Ctrl+V", self._paste_edit), (Qt.Key_Right, lambda: self.step(1)), (Qt.Key_Left, lambda: self.step(-1))):
            QShortcut(QKeySequence(k), self, fn)
        self._done_keys = []  # Intro / Esc: terminar de ajustar el recorte
        for k in ("Return", "Enter", "Escape"):
            sc = QShortcut(QKeySequence(k), self, lambda: self.b_crop.setChecked(False))
            sc.setEnabled(False)
            self._done_keys.append(sc)
        self.b_bw.setEnabled(True)
        self._refresh_prop()
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

    def _state_buttons(self):
        ph = self.cur()
        rev = bool(ph and ph.reviewed)
        self.b_ok.setChecked(bool(ph and rev and ph.keep))
        self.b_no.setChecked(bool(ph and rev and not ph.keep))

    def refresh_counts(self):
        ok = sum(p.keep for p in self.photos)
        rev = sum(1 for p in self.photos if getattr(p, "reviewed", False))
        if hasattr(self, "progress"):
            self.progress.setMaximum(max(1, len(self.photos)))
            self.progress.setValue(rev)
            self.progress.setToolTip(f"{rev} de {len(self.photos)} revisadas")
        self.counts.setText(f"{ok} aprobadas · {len(self.photos) - ok} rechazadas · {len(self.photos) - rev} sin revisar")
        self.b_export.setText(f"Exportar {ok} aprobadas")
        self.b_export.setEnabled(ok > 0)

    # ---------------------------------------------------------- foto actual
    def _base(self, ph):
        """Foto revelada entera (sin recortar) con el estilo de la sesión, en color o B/N."""
        key = (ph.path, round(ph.ev, 2), round(self._angle_of(ph), 2), self.is_studio(ph))
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
        self.main._review_pos = self.strip.currentRow()  # para volver a esta foto al recuperar la sesión
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
        ss = self._studio_s(ph) if studio else None
        box = (0.0, 0.0, 1.0, 1.0) if self.crop_on else self._cur_box(ph, ss)
        color = crop(base, box)
        if not studio:
            color = E.apply_look(color.astype(np.float32) / 255.0 if color.dtype == np.uint8 else color,
                                 self.photo_look(ph))
        img = color
        bws = self.photo_bw(ph)
        label = f"Formato: {'Studio' if studio else ph.best}" + ("" if studio else f"  ·  Look {self.photo_look(ph)}")
        if not studio and ph.crop_why and ph.best == E.suggested_crop(ph):
            label += f"  ·  encuadra {ph.crop_why}"
        if ss is not None and ss.get("bw"):  # B/N hecho en Studio: se ve (y se exporta) tal cual
            img = crop(self._studio_bw(ph, ss), box)
            label += f"  ·  B/N {ss.get('bw_style') or 'Editorial'}"
        elif studio:
            label += "  ·  color"
        elif self.bw:
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
        self.b_crop.setEnabled(not studio or ss is not None)
        if studio and ss is None and self.crop_on:
            self.b_crop.setChecked(False)
        if self.crop_on and self._crop_for != ph.path:
            self._crop_for = ph.path
            size = (base.shape[1], base.shape[0])
            cur = self._cur_box(ph, ss)
            full = tuple(cur) == (0.0, 0.0, 1.0, 1.0)
            orig = (ss.get("crop_ratio") == "Original" or full) if ss is not None else ph.best == "Original"
            name, flip = ("Original", False) if orig else CV.guess_ratio(cur, size)
            self.ratio_cb.setCurrentText(name)
            self.b_flip.setChecked(flip)
            self.big.crop.start(list(cur), angle=self._angle_of(ph))
            self.big.crop.rn = self._ratio()
        if self.crop_on:
            self.big.zoom.pct, self.big.zoom.provider = None, None
        elif self.tile_fn and not studio:
            fw, fh = E.full_size(ph.path)
            self.big.zoom.reset(key=(ph.path, ph.best, self.bw, self.photo_look(ph), self.photo_bw(ph)),
                                full_size=(int(fw * (box[2] - box[0])) or fw, fh))
            self.big.zoom.provider = lambda b, ph=ph, box=box: self.tile_fn(
                ph, (box[0] + b[0] * (box[2] - box[0]), box[1] + b[1] * (box[3] - box[1]),
                     box[0] + b[2] * (box[2] - box[0]), box[1] + b[3] * (box[3] - box[1])), self.bw)
            self.big.zoom.schedule()
        else:
            self.big.zoom.provider = None
        self._show_angle(self._angle_of(ph))
        self._fill_crops(ph, base, ss)
        self._fill_bw(ph, color if self.bw and not studio else crop(base, box), studio, ss)
        self._refresh_prop()

    def _studio_s(self, ph):
        """Ajustes de Studio de la foto (los mismos que se exportarán) o None."""
        return self.prop.studio_of(ph) if self.prop is not None else None

    def _studio_bw(self, ph, ss):
        """Revelado de Studio en B/N (sin recortar), tal como se exportará."""
        key = (ph.path, "bw", ss.get("bw_style"), round(float(ss.get("angle", 0) or 0), 2))
        if key not in self._cache:
            self._cache[key] = self.studio_render_fn(ph, True)
            if len(self._cache) > 40:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[key]

    def _cur_box(self, ph, ss=None):
        """Recorte vigente: el de Studio si la foto está editada allí; si no, el elegido en Events."""
        if ss is not None:
            return tuple(float(v) for v in (ss.get("crop") or (0.0, 0.0, 1.0, 1.0)))
        return ph.crops.get(ph.best, (0.0, 0.0, 1.0, 1.0))

    def _set_studio_crop(self, ph, ss, box, ratio):
        """El recorte hecho aquí en una foto editada en Studio va a su edición de Studio (y se exporta así)."""
        import studio_engine as SE
        ss["crop"] = [round(float(v), 4) for v in box]
        ss["crop_ratio"] = ratio
        try:
            SE.save(self.prop.folder, ph.path, ss)
        except OSError:
            pass

    def _fill_crops(self, ph, base, ss=None):
        self.crops.clear()
        from PySide6.QtGui import QIcon
        small = base[::4, ::4] if max(base.shape[:2]) > 1200 else base
        cur = self._cur_box(ph, ss)
        auto = E.suggested_crop(ph)
        for name, box in ph.crops.items():
            sel = (name == ph.best) if ss is None else                 all(abs(float(a) - float(b)) < 1e-3 for a, b in zip(box, cur))
            it = QListWidgetItem(QIcon(_pm(crop(small, box))), ("● " if sel else "") + name + (" ★" if name == auto else ""))
            it.setData(ROLE, name)
            tip = "Recorte sugerido (★)" + (f": encuadra {ph.crop_why}" if ph.crop_why else "")
            it.setToolTip(tip if name == auto else f"Usar {name}" + (" (elegido)" if name == ph.best else ""))
            self.crops.addItem(it)

    def photo_bw(self, ph) -> str:
        return ph.bw_style or self.bw_style or "Editorial"

    def photo_look(self, ph) -> str:
        return ph.look or self.look or "Natural"

    def _fill_bw(self, ph, img, studio=False, ss=None):
        """Propuestas de estilo para esta foto: estilos de B/N en la vista B/N, looks de color en la de color.
        En las editadas en Studio: color o un estilo de B/N, que se guarda en su edición de Studio."""
        from PySide6.QtGui import QIcon
        self.bw_list.clear()
        if studio and ss is None:
            self.bw_t.setText("LOOK: SE AJUSTA EN STUDIO")
            return
        small = img.astype(np.float32) / 255.0 if img.dtype == np.uint8 else img
        f = 160 / max(small.shape[:2])
        if f < 1:
            import cv2
            small = cv2.resize(small, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        if ss is not None:
            self.bw_t.setText("COLOR O BLANCO Y NEGRO (STUDIO)")
            cur = (ss.get("bw_style") or "Editorial") if ss.get("bw") else STUDIO_COLOR
            for name in [STUDIO_COLOR] + list(E.BW_STYLES):
                pic = small if name == STUDIO_COLOR else E.bw_editorial(small, seed=1, style=name)
                it = QListWidgetItem(QIcon(_pm(pic)), ("● " if name == cur else "") + name)
                it.setData(ROLE, name)
                it.setToolTip("Retócala luego en Studio (botón B/N, tecla V)" if name != STUDIO_COLOR else
                              "Dejarla en color")
                self.bw_list.addItem(it)
            return
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
        ss = self._studio_s(ph) if self.is_studio(ph) else None
        if ss is not None:
            import studio_engine as SE
            ss["bw"] = name != STUDIO_COLOR
            if ss["bw"]:
                ss["bw_style"] = name
            try:
                SE.save(self.prop.folder, ph.path, ss)
            except OSError:
                pass
        elif self.bw:
            ph.bw_style = "" if name == self.bw_style else name
        else:
            ph.look = "" if name == self.look else name
        self.on_change()
        self.show_photo()

    def _pick_crop(self, it):
        name = it.data(ROLE)
        ph = self.cur()
        if ph and name and name in ph.crops:
            ss = self._studio_s(ph) if self.is_studio(ph) else None
            if ss is not None:
                self._set_studio_crop(ph, ss, ph.crops[name], name)
            else:
                ph.best = name
            self._crop_for = None
            self.on_change()
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
        self.on_change()
        self.refresh_counts()
        self.strip.viewport().update()
        self.step(1)
        self._state_buttons()

    def set_bw(self, on):
        self.bw = on
        self.show_photo()

    def _studio(self):
        ph = self.cur()
        if ph:
            self.action, self.studio_photo = "studio", ph
            self.accept()

    def _edits(self):
        ph = self.cur()
        if ph:
            self.action, self.studio_photo = "edits", ph
            self.accept()

    def _export(self):
        self.action = "export"
        self.accept()

    # ---------------------------------------------------------- recorte a mano
    def _ratio(self):
        ph = self.cur()
        if not ph:
            return None
        b = self._base(ph)
        size = (b.shape[1], b.shape[0])
        v = CV.ratio_value(self.ratio_cb.currentText(), self.b_flip.isChecked(), size)
        return None if v is None else v * size[1] / size[0]

    # ---------------------------------------------------------- enderezar (parte del recorte)
    def _angle_of(self, ph) -> float:
        ss = self._studio_s(ph) if self.is_studio(ph) else None
        return float(ss.get("angle", 0) or 0) if ss is not None else float(ph.angle or 0)

    def _show_angle(self, a):
        self.ang.blockSignals(True)
        self.ang.setValue(int(round(-a * 10)))  # en pantalla: a la derecha = horario, como Lightroom
        self.ang.blockSignals(False)
        self.ang_lbl.setText(f"{-a:+.1f}°".replace(".", ","))

    def _ang_moved(self, v):
        a = -v / 10
        self.ang_lbl.setText(f"{-a:+.1f}°".replace(".", ","))
        if not self.crop_on:
            self.b_crop.setChecked(True)
        self.big.crop.target = a  # vista previa al momento
        self.big.update()
        if not self.ang.isSliderDown():  # teclado o rueda: se aplica ya
            self._crop_angle(a, "end")

    def _crop_angle(self, a, phase):
        """Ángulo nuevo desde el marco (girar fuera de él, nivel), el regulador, Auto o 0°."""
        ph = self.cur()
        if not ph or phase != "end":
            return
        a = round(max(-45.0, min(45.0, float(a))), 2)
        if not self.crop_on:
            self.b_crop.setChecked(True)
        if abs(a - self._angle_of(ph)) < 1e-3:
            self.big.crop.rendered(a)
            return self._show_angle(a)
        ss = self._studio_s(ph) if self.is_studio(ph) else None
        if ss is not None:
            import studio_engine as SE
            ss["angle"] = a
            try:
                SE.save(self.prop.folder, ph.path, ss)
            except OSError:
                pass
        else:
            ph.angle = a
        self.big.crop.target = a
        self._show_angle(a)
        self.on_change()
        self.big.repaint()  # se ve girada mientras se revela
        self.show_photo()
        self.big.crop.rendered(a)

    def _level_mode(self, on):
        if on and not self.crop_on:
            self.b_crop.setChecked(True)
        self.big.crop.level_mode = bool(on) and self.crop_on
        self.big.update()

    def _auto_straighten(self):
        ph = self.cur()
        if not ph:
            return
        import cv2
        base = self._base(ph)  # ya girada: lo que falte se suma al giro actual
        g = base.astype(np.float32) / 255.0 if base.dtype == np.uint8 else base
        f = 1600 / max(g.shape[:2])
        if f < 1:
            g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        g8 = (np.clip(cv2.cvtColor(np.ascontiguousarray(g, dtype=np.float32), cv2.COLOR_RGB2GRAY), 0, 1) * 255
              ).astype(np.uint8)
        self._crop_angle(self._angle_of(ph) + E.detect_tilt(g8), "end")

    def set_crop_mode(self, on):
        self.crop_on, self._crop_for = on, None
        if not on and hasattr(self, "b_level"):
            self.b_level.setChecked(False)
        for sc in self._done_keys:
            sc.setEnabled(on)
        if not on:
            self.big.crop.stop()
        elif self.big.cmp.mode != "off":
            self.set_compare("off")
        self.show_photo()

    def _ratio_changed(self, *_):
        if not self.crop_on:
            self.b_crop.setChecked(True)
        self.big.crop.set_ratio(self._ratio())
        self._on_crop("end")

    def _on_crop(self, phase):
        ph = self.cur()
        if phase != "end" or not ph or self.big.crop.rect is None:
            return
        ss = self._studio_s(ph) if self.is_studio(ph) else None
        if ss is not None:
            self._set_studio_crop(ph, ss, self.big.crop.rect, self.ratio_cb.currentText())
        else:
            ph.crops[E.CUSTOM_CROP] = tuple(round(float(v), 4) for v in self.big.crop.rect)
            ph.best = E.CUSTOM_CROP
        self.on_change()
        self._fill_crops(ph, self._base(ph), ss)

    # ---------------------------------------------------------- propagar la edición de Studio / Edits
    def _refresh_prop(self):
        if self.prop is None:
            return
        ph = self.cur()
        has = bool(ph and self.prop.has_edit(ph))
        self.b_pcopy.setEnabled(has)
        self.b_ppaste.setEnabled(self.clip is not None and ph is not None)
        self.b_papply.setEnabled(self.clip is not None)
        if self.clip:
            self.prop_info.setText(f"Copiada de {self.clip['src']}: {self.clip['desc']}. Pégala (Ctrl+V) en "
                                   "todas las que quieras; si copias otra, sustituye a esta.")
        else:
            self.prop_info.setText("Edita una foto en Studio o en Edits, apruébala y pulsa «Copiar…» para "
                                   "llevar esa edición a otras fotos." if not has else
                                   "Esta foto tiene edición propia: puedes copiarla.")

    def _copy_edit(self):
        ph = self.cur()
        if self.prop is None or not ph or not self.prop.has_edit(ph):
            return
        d = CopyDialog(self, self.prop, ph)
        if d.exec() == QDialog.Accepted:
            clip = self.prop.make_clip(ph, d.groups(), d.layer_ids())
            if clip:  # la nueva sustituye a la anterior
                self.clip = clip
                self.main._edit_clip = (self.prop.folder, clip)
            self._refresh_prop()

    def _after_apply(self, n):
        self._cache.clear()
        self.on_change()
        self.strip.viewport().update()
        self.refresh_counts()

    def _paste_edit(self):
        ph = self.cur()
        if self.prop is None or not self.clip or not ph:
            return
        self.prop.apply(self.clip, [ph])
        ph.keep, ph.reviewed, ph.reason = True, True, ""
        self._after_apply(1)
        self.show_photo()  # te quedas en la foto para ver el resultado (→ para pasar a la siguiente)
        self._state_buttons()
        self.prop_info.setText(f"Pegada en {ph.name}. Pasa a la siguiente con → y vuelve a pegar (Ctrl+V).")

    def _apply_edit(self):
        if self.prop is None or not self.clip:
            return
        d = ApplyDialog(self, self.clip)
        if d.exec() == QDialog.Accepted:
            targets = d.targets()
            n = self.prop.apply(self.clip, targets)
            self._after_apply(n)
            self.show_photo()
            self.prop_info.setText(f"Edición aplicada a {n} foto(s). Se exportarán con ella.")

    def current_index(self) -> int:
        ph = self.cur()
        return self.photos.index(ph) if ph in self.photos else 0


class CopyDialog(QDialog):
    """Qué copiar de la edición de esta foto (como «Copiar ajustes» de Lightroom)."""

    def __init__(self, parent, prop, ph):
        import propagate as P
        super().__init__(parent)
        self.setWindowTitle(f"Copiar la edición de {ph.name}")
        v = QVBoxLayout(self)
        self.checks, self.layers = {}, {}
        if prop.studio_of(ph) is not None:
            t = QLabel("AJUSTES DE STUDIO")
            t.setObjectName("cardTitle")
            v.addWidget(t)
            for gid, label, _, on in P.STUDIO_GROUPS:
                c = QCheckBox(label)
                c.setChecked(on)
                v.addWidget(c)
                self.checks[gid] = c
        layers = prop.edits_of(ph)
        if layers:
            t = QLabel("CAPAS DE EDITS")
            t.setObjectName("cardTitle")
            v.addWidget(t)
            for l in layers:
                c = QCheckBox(l.get("name") or l["kind"])
                c.setChecked(True)
                v.addWidget(c)
                self.layers[l["id"]] = c
        note = QLabel("Los retoques de zonas concretas (corrector, tampón, licuar) no se copian: solo valen para "
                      "esta foto.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Copiar")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def groups(self):
        return {g for g, c in self.checks.items() if c.isChecked()}

    def layer_ids(self):
        return [i for i, c in self.layers.items() if c.isChecked()]


class ApplyDialog(QDialog):
    """A qué fotos aplicar la edición copiada."""

    def __init__(self, dlg, clip):
        super().__init__(dlg)
        self.dlg = dlg
        self.setWindowTitle("Aplicar la edición a…")
        self.resize(460, 560)
        photos, cur = dlg.photos, dlg.cur()
        i = photos.index(cur) if cur in photos else 0
        src = clip["src"]
        own = lambda p: dlg.prop.has_edit(p)  # noqa: E731
        self.options = [
            ("Todas las aprobadas", [p for p in photos if p.keep]),
            ("Las aprobadas que no tienen edición propia", [p for p in photos if p.keep and not own(p)]),
            ("Las siguientes a esta (aprobadas)", [p for p in photos[i + 1:] if p.keep]),
            ("Elegir fotos", None),
        ]
        v = QVBoxLayout(self)
        info = QLabel(f"Edición de <b>{src}</b>: {clip['desc']}.")
        info.setWordWrap(True)
        v.addWidget(info)
        self.group = QButtonGroup(self)
        for k, (label, lst) in enumerate(self.options):
            lst = [p for p in lst if os.path.basename(p.path) != src] if lst is not None else None
            self.options[k] = (label, lst)
            r = QRadioButton(label + (f"  ({len(lst)})" if lst is not None else ""))
            self.group.addButton(r, k)
            v.addWidget(r)
        self.group.button(1).setChecked(True)
        self.pick = QListWidget()
        self.pick.setIconSize(QSize(64, 48))
        for p in photos:
            if os.path.basename(p.path) == src:
                continue
            it = QListWidgetItem(p.name + ("" if p.keep else "  (rechazada)"))
            pm = dlg.main.thumb_pixmap(p)
            if pm:
                from PySide6.QtGui import QIcon
                it.setIcon(QIcon(pm))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setData(ROLE, p)
            self.pick.addItem(it)
        self.pick.setEnabled(False)
        self.group.idToggled.connect(lambda k, on: self.pick.setEnabled(k == 3 and on) if k == 3 else None)
        v.addWidget(self.pick, 1)
        note = QLabel("No cambia si están aprobadas o rechazadas. Se puede volver a aplicar: sustituye lo pegado antes.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Aplicar")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def targets(self):
        k = self.group.checkedId()
        if k == 3:
            return [self.pick.item(r).data(ROLE) for r in range(self.pick.count())
                    if self.pick.item(r).checkState() == Qt.Checked]
        return list(self.options[k][1] or [])


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
