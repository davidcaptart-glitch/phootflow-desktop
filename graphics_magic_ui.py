"""Graphics: «Estilos» del panel Diseño y diálogo «Diseñar con las fotos de la sesión» (interfaz de graphics_magic).

- `StylesStrip`: miniaturas de la página actual con otras paletas y fuentes (y en espejo); un clic las aplica a
  todo el diseño (se puede deshacer). «Mezclar» propone otras.
- `SessionDialog`: las fotos de la sesión de Events de mejor a peor (las 12 primeras marcadas), y a la derecha
  propuestas ya rellenas con ellas: carrusel, álbum y las plantillas con huecos. Las vistas previas usan las
  miniaturas de Events (no revelan RAW); al elegir una se crea el diseño con las fotos de verdad.
"""
from __future__ import annotations

import copy
import os
import random

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

import graphics_engine as G
import graphics_home as GH
import graphics_magic as M


class StylesStrip(QWidget):
    """Variantes de estilo del diseño abierto (paleta + fuentes, y composición en espejo)."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.seed = 1
        self.cache = G.ImageCache(limit=12)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        row = QHBoxLayout()
        t = QLabel("ESTILOS PARA ESTE DISEÑO")
        t.setObjectName("cardTitle")
        row.addWidget(t)
        row.addStretch(1)
        mix = QPushButton("Mezclar")
        mix.setObjectName("small")
        mix.setToolTip("Otras combinaciones de colores, fuentes y composición")
        mix.setCursor(Qt.PointingHandCursor)
        mix.clicked.connect(self.shuffle)
        row.addWidget(mix)
        v.addLayout(row)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setFlow(QListWidget.LeftToRight)
        self.list.setWrapping(False)
        self.list.setMovement(QListWidget.Static)
        self.list.setIconSize(QSize(84, 84))
        self.list.setGridSize(QSize(96, 112))
        self.list.setFixedHeight(130)
        self.list.setWordWrap(True)
        self.list.setToolTip("Clic: aplicar a todo el diseño (Ctrl+Z lo deshace)")
        self.list.itemClicked.connect(self._apply)
        v.addWidget(self.list)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self._vars = []

    def schedule(self, ms: int = 700):
        if self.isVisible():
            self.timer.start(ms)

    def shuffle(self):
        self.seed = random.randrange(1 << 30)
        self.refresh()

    def showEvent(self, e):
        super().showEvent(e)
        self.schedule(50)

    def refresh(self):
        d = self.owner.d
        G.sync_page(d)
        one = copy.deepcopy(d)
        one["pages"] = [copy.deepcopy(d["pages"][d.get("page", 0)])]
        one["page"] = 0
        G.ensure_pages(one)
        self._vars = M.variants(one, 6, self.seed)
        self.list.clear()
        for i, (label, v) in enumerate(self._vars):
            img = GH.design_thumb(v, 84, self.cache, seed=i)
            it = QListWidgetItem(QIcon(QPixmap.fromImage(img)), label)
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)

    def _apply(self, it):
        i = it.data(Qt.UserRole)
        if i is None or i >= len(self._vars):
            return
        label, _ = self._vars[i]
        name = label.split(" · ")[0]
        o = self.owner
        G.sync_page(o.d)
        M.restyle(o.d, name)
        if label.endswith("espejo"):
            M.mirror(o.d)
        o.canvas.resetCachedContent()
        o.rebuild()
        o.commit()
        o.refresh_pages()
        o.saved_lbl.setText(f"Estilo «{label}» aplicado · Ctrl+Z para deshacer")
        self.schedule(300)


class SessionDialog(QDialog):
    """Diseños hechos con las fotos de la sesión."""

    def __init__(self, owner, folder: str):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Diseñar con las fotos de la sesión")
        self.resize(1180, 760)
        self.folder = folder
        self.result_design = None
        self.cache = G.ImageCache(limit=60)
        self.aspects: dict = {}
        self.photos: list[dict] = []
        self._todo: list = []
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        self.info = QLabel("")
        self.info.setObjectName("meta")
        self.info.setWordWrap(True)
        top.addWidget(self.info, 1)
        other = QPushButton("Otra carpeta…")
        other.clicked.connect(self.pick_folder)
        top.addWidget(other)
        outer.addLayout(top)
        body = QHBoxLayout()
        outer.addLayout(body, 1)
        left = QVBoxLayout()
        lt = QLabel("FOTOS (de mejor a peor según el análisis)")
        lt.setObjectName("cardTitle")
        left.addWidget(lt)
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(QSize(96, 96))
        self.grid.setGridSize(QSize(108, 112))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setFixedWidth(370)
        self.grid.itemChanged.connect(lambda *_: self.timer.start(450))
        left.addWidget(self.grid, 1)
        r = QHBoxLayout()
        for n in (6, 12, 24):
            b = QPushButton(f"Las {n} mejores")
            b.setObjectName("small")
            b.clicked.connect(lambda _=False, n=n: self.check_top(n))
            r.addWidget(b)
        none = QPushButton("Ninguna")
        none.setObjectName("small")
        none.clicked.connect(lambda: self.check_top(0))
        r.addWidget(none)
        left.addLayout(r)
        body.addLayout(left)
        right = QVBoxLayout()
        form = QHBoxLayout()
        self.title = QLineEdit(os.path.basename(folder.rstrip("\\/")) if folder else "")
        self.title.setPlaceholderText("Título de la portada")
        self.title.textChanged.connect(lambda *_: self.timer.start(600))
        self.sub = QLineEdit("")
        self.sub.setPlaceholderText("Subtítulo (fecha, lugar…)")
        self.sub.textChanged.connect(lambda *_: self.timer.start(600))
        self.style = QComboBox()
        self.style.addItems(M.STYLE_NAMES)
        self.style.currentIndexChanged.connect(lambda *_: self.timer.start(100))
        form.addWidget(self.title, 2)
        form.addWidget(self.sub, 2)
        form.addWidget(QLabel("Estilo"))
        form.addWidget(self.style, 1)
        right.addLayout(form)
        rt = QLabel("PROPUESTAS · clic para abrir en el editor")
        rt.setObjectName("cardTitle")
        right.addWidget(rt)
        self.props = QListWidget()
        self.props.setViewMode(QListWidget.IconMode)
        self.props.setIconSize(QSize(170, 170))
        self.props.setGridSize(QSize(186, 210))
        self.props.setResizeMode(QListWidget.Adjust)
        self.props.setMovement(QListWidget.Static)
        self.props.setWordWrap(True)
        self.props.itemClicked.connect(self.choose)
        right.addWidget(self.props, 1)
        body.addLayout(right, 1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.build_proposals)
        self.load(folder)

    # ---------------------------------------------------------- fotos
    def pick_folder(self):
        f = QFileDialog.getExistingDirectory(self, "Carpeta de la sesión", self.folder or "")
        if f:
            self.load(f)

    def load(self, folder: str):
        self.folder = folder
        self.photos = M.session_photos(folder) if folder else []
        if not self.title.text().strip() and folder:
            self.title.setText(os.path.basename(folder.rstrip("\\/")))
        analysed = bool(M.work_dirs(folder)) if folder else False
        if not folder:
            self.info.setText("Elige la carpeta de una sesión («Otra carpeta…»).")
        elif not self.photos:
            self.info.setText(f"No hay fotos en «{os.path.basename(folder)}».")
        else:
            ed = sum(1 for p in self.photos if p.get("edited"))
            self.info.setText(f"{len(self.photos)} fotos de «{os.path.basename(folder)}»"
                              + (f" · ordenadas por el análisis de Events · {ed} ya editadas (se usan esas)"
                                 if analysed else " · sin analizar en Events: en el orden de la carpeta"))
        self.grid.blockSignals(True)
        self.grid.clear()
        ph = QPixmap(96, 96)
        ph.fill(QColor("#222"))
        for i, p in enumerate(self.photos):
            it = QListWidgetItem(QIcon(ph), "")
            it.setData(Qt.UserRole, i)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if i < 12 and p.get("keep", True) else Qt.Unchecked)
            it.setToolTip(p["name"] + ("" if p.get("keep", True) else " · descartada en Events"))
            self.grid.addItem(it)
        self.grid.blockSignals(False)
        self._thumb_i = 0
        QTimer.singleShot(0, self._next_thumb)
        self.build_proposals()

    def _next_thumb(self):
        if self._thumb_i >= self.grid.count() or not self.isVisible() and self._thumb_i > 0:
            return
        i = self._thumb_i
        self._thumb_i += 1
        p = self.photos[i]
        pm = QPixmap(p["thumb"]) if p.get("thumb") else QPixmap()
        if pm.isNull():
            try:
                from studio import quick_thumb
                pm.loadFromData(quick_thumb(p["path"], 192))
            except Exception:
                pass
        if not pm.isNull():
            self.grid.item(i).setIcon(QIcon(pm))
        QTimer.singleShot(0, self._next_thumb)

    def check_top(self, n: int):
        self.grid.blockSignals(True)
        k = 0
        for i in range(self.grid.count()):
            ok = k < n and self.photos[i].get("keep", True)
            self.grid.item(i).setCheckState(Qt.Checked if ok else Qt.Unchecked)
            k += ok
        self.grid.blockSignals(False)
        self.build_proposals()

    def chosen(self) -> list[dict]:
        return [self.photos[i] for i in range(self.grid.count()) if self.grid.item(i).checkState() == Qt.Checked]

    # ---------------------------------------------------------- propuestas
    def _make(self, key, use_thumbs: bool):
        ph = self.chosen()
        kind, name = key
        if kind == "album":
            return M.album(ph, name, self.title.text().strip(), self.sub.text().strip(),
                           self.style.currentText(), self.aspects, use_thumbs)
        d = G.design_from_preset(G.TEMPLATE_SIZE[name])
        d["name"] = (self.title.text().strip() + " · " if self.title.text().strip() else "") + name
        G.apply_template(d, name)
        M.fill_placeholders(d, ph, self.aspects, use_thumbs)
        return d

    def build_proposals(self):
        ph = self.chosen()
        self.props.clear()
        self._todo = []
        if not ph:
            return
        keys = [("album", k) for k in M.ALBUMS]
        for name, _ in M.proposals(ph[:8], self.aspects, use_thumbs=True):
            keys.append(("tpl", name))
        blank = QPixmap(170, 170)
        blank.fill(QColor(255, 255, 255, 14))
        for key in keys:
            if key[0] == "album":
                n = len(M.album(ph, key[1], aspects=self.aspects, use_thumbs=True)["pages"])
                label = f"{key[1]} · {n} págs."
            else:
                label = key[1]
            it = QListWidgetItem(QIcon(blank), label)
            it.setData(Qt.UserRole, key)
            self.props.addItem(it)
            self._todo.append(it)
        QTimer.singleShot(0, self._next_prop)

    def _next_prop(self):
        if not self._todo:
            return
        it = self._todo.pop(0)
        try:
            d = self._make(it.data(Qt.UserRole), use_thumbs=True)
            it.setIcon(QIcon(QPixmap.fromImage(GH.design_thumb(d, 170, self.cache, page=0))))
        except RuntimeError:   # el diálogo ya se cerró
            return
        QTimer.singleShot(0, self._next_prop)

    def choose(self, it):
        key = it.data(Qt.UserRole)
        if not key:
            return
        self.result_design = self._make(key, use_thumbs=False)
        self.accept()
