"""Graphics: vista de cuadrícula de las páginas (como la de Canva): miniaturas grandes que se ordenan arrastrando,
doble clic para ir a una, y añadir, duplicar o eliminar. Trabaja en vivo sobre el diseño del `GraphicsView`."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QVBoxLayout)

import graphics_engine as G
import graphics_home as GH


def reorder(d: dict, order: list[int]) -> None:
    """Pone las páginas en el orden `order` (índices antiguos); la activa sigue siendo la misma página."""
    G.sync_page(d)
    if sorted(order) != list(range(len(d["pages"]))):
        return
    cur = d.get("page", 0)
    d["pages"] = [d["pages"][i] for i in order]
    d["page"] = order.index(cur)
    G.ensure_pages(d)


class PageGridDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Páginas")
        self.resize(980, 640)
        v = QVBoxLayout(self)
        hint = QLabel("Arrastra para cambiar el orden · doble clic: ir a la página")
        hint.setObjectName("meta")
        v.addWidget(hint)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(180, 180))
        self.list.setGridSize(QSize(200, 214))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Snap)
        self.list.setDragDropMode(QListWidget.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self.list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._moved))
        self.list.itemDoubleClicked.connect(self._open)
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        for txt, fn in (("Añadir página", lambda: self._op("add")), ("Duplicar", lambda: self._op("dup")),
                        ("Eliminar", lambda: self._op("del"))):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Cerrar")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)
        self.fill()

    def fill(self):
        d = self.owner.d
        G.sync_page(d)
        self.list.clear()
        for j in range(len(d["pages"])):
            img = GH.design_thumb(d, 180, self.owner.canvas.cache, seed=j, page=j)
            it = QListWidgetItem(QIcon(QPixmap.fromImage(img)), f"Página {j + 1}")
            it.setData(Qt.UserRole, j)
            self.list.addItem(it)
        self.list.setCurrentRow(d.get("page", 0))

    def _moved(self):
        order = [self.list.item(r).data(Qt.UserRole) for r in range(self.list.count())]
        if order == list(range(len(order))):
            return
        o = self.owner
        reorder(o.d, order)
        o.rebuild(select=[])
        o.commit()
        o.refresh_pages()
        self.fill()

    def _open(self, it):
        self.owner.goto_page(self.list.row(it))
        self.accept()

    def _op(self, op):
        r = self.list.currentRow()
        o = self.owner
        if r >= 0 and r != o.d.get("page", 0):
            o.goto_page(r)
        if op == "del" and len(o.d["pages"]) < 2:
            QMessageBox.information(self, "Páginas", "El diseño necesita al menos una página.")
            return
        o.page_op(op)
        self.fill()
