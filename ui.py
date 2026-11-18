"""Piezas de interfaz comunes: chips (opciones que se encienden y apagan), grupos de chips ligados a un
desplegable, botones de icono, panel lateral de ajustes y paleta de órdenes (Ctrl+K)."""
from __future__ import annotations

import unicodedata

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel,
                               QLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

import icons as I


class FlowLayout(QLayout):
    """Coloca los elementos en filas y salta de línea cuando no caben (para filas de chips)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._place(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._place(r, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _place(self, r, test):
        x, y, line = r.x(), r.y(), 0
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            sz = it.sizeHint()
            if x + sz.width() > r.right() + 1 and line > 0:
                x, y, line = r.x(), y + line + self._sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x += sz.width() + self._sp
            line = max(line, sz.height())
        return y + line - r.y()


def flow(widgets, spacing: int = 6) -> QWidget:
    w = QWidget()
    lay = FlowLayout(w, spacing)
    for x in widgets:
        lay.addWidget(x)
    return w


def as_chip(b: QAbstractButton, icon: str | None = None, tip: str = "") -> QAbstractButton:
    """Convierte una casilla (o un botón) en chip: misma función, se enciende y se apaga con un clic."""
    b.setObjectName("chip")
    b.setCursor(Qt.PointingHandCursor)
    if icon:
        b.setIcon(I.icon(icon, 16))
        b.setIconSize(QSize(16, 16))
    if tip:
        b.setToolTip(tip)
    return b


def chip(text: str, icon: str | None = None, tip: str = "", checkable: bool = True) -> QPushButton:
    b = QPushButton(text)
    b.setCheckable(checkable)
    return as_chip(b, icon, tip)


def icon_button(name: str, tip: str, checkable: bool = False, size: int = 18, kind: str = "iconBtn") -> QToolButton:
    b = QToolButton()
    b.setObjectName(kind)
    b.setIcon(I.icon(name, size))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    b.setAutoRaise(True)
    return b


class ComboChips(QWidget):
    """Grupo de chips (uno elegido) que hace de cara visible de un QComboBox existente: el desplegable sigue
    guardando el valor (ajustes, carga y guardado no cambian) y los chips se mantienen sincronizados."""

    def __init__(self, combo, icon_for=None, labels=None, tips=None, label_fn=None, tip_fn=None):
        super().__init__()
        self.combo, self.icon_for, self.labels, self.tips = combo, icon_for, labels or {}, tips or {}
        self.label_fn, self.tip_fn = label_fn or (lambda t: t), tip_fn or (lambda t: "")
        self.lay = FlowLayout(self, 6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        combo.currentTextChanged.connect(self._sync)
        combo.model().rowsInserted.connect(lambda *_: self.rebuild())
        combo.model().rowsRemoved.connect(lambda *_: self.rebuild())
        self.rebuild()

    def rebuild(self):
        for b in self.buttons.values():
            self.group.removeButton(b)
            b.deleteLater()
        self.buttons = {}
        for i in range(self.combo.count()):
            t = self.combo.itemText(i)
            b = chip(self.labels.get(t, self.label_fn(t)), self.icon_for(t) if self.icon_for else None,
                     self.tips.get(t, self.tip_fn(t)))
            b.clicked.connect(lambda _, t=t: self.combo.setCurrentText(t))
            self.group.addButton(b)
            self.lay.addWidget(b)
            self.buttons[t] = b
        self._sync(self.combo.currentText())

    def _sync(self, text):
        b = self.buttons.get(text)
        if b is not None and not b.isChecked():
            b.setChecked(True)


class SideSheet(QDialog):
    """Panel de ajustes que se desliza sobre la ventana (los ajustes que no se tocan en cada sesión)."""

    def __init__(self, parent, title: str, width: int = 400):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("sheet")
        self.setModal(True)
        self._w = width
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QFrame()
        head.setObjectName("sheetHead")
        hh = QHBoxLayout(head)
        hh.setContentsMargins(18, 12, 10, 12)
        t = QLabel(title)
        t.setObjectName("h1")
        close = icon_button("x", "Cerrar (Esc)")
        close.clicked.connect(self.accept)
        hh.addWidget(t, 1)
        hh.addWidget(close)
        v.addWidget(head)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 0, 12)
        self.lay.setSpacing(0)
        sc.setWidget(self.body)
        v.addWidget(sc, 1)

    def add(self, w):
        self.lay.addWidget(w)

    def open_at(self, anchor: QWidget, side: str = "left"):
        win = anchor.window()
        g = win.geometry()
        x = g.x() if side == "left" else g.right() - self._w
        self.setGeometry(x, g.y() + 48, self._w, g.height() - 48)
        self.exec()


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


class CommandPalette(QDialog):
    """Ctrl+K: escribe lo que quieres hacer («analizar», «b/n», «recortar», «licencia»…) y pulsa Intro."""

    def __init__(self, parent, actions: list[tuple[str, str, str, object]]):
        """`actions`: (icono, nombre, palabras clave, función)."""
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("palette")
        self.actions = actions
        self.resize(560, 420)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        row = QHBoxLayout()
        lab = QLabel()
        lab.setPixmap(I.pixmap("search", 18))
        self.edit = QLineEdit()
        self.edit.setObjectName("paletteEdit")
        self.edit.setPlaceholderText("¿Qué quieres hacer?  (analizar, revisar, recortar, B/N, licencia…)")
        row.addWidget(lab)
        row.addWidget(self.edit, 1)
        v.addLayout(row)
        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.setIconSize(QSize(18, 18))
        v.addWidget(self.list, 1)
        self.edit.textChanged.connect(self.refill)
        self.edit.returnPressed.connect(self.run)
        self.list.itemActivated.connect(lambda _: self.run())
        self.edit.installEventFilter(self)
        self.refill("")

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.edit and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Down, Qt.Key_Up):
            r = self.list.currentRow() + (1 if ev.key() == Qt.Key_Down else -1)
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, r)))
            return True
        return super().eventFilter(obj, ev)

    def refill(self, text):
        q = _fold(text).split()
        self.list.clear()
        for ic, name, keys, fn in self.actions:
            hay = _fold(name + " " + keys)
            if all(w in hay for w in q):
                it = QListWidgetItem(I.icon(ic, 18), name)
                it.setData(Qt.UserRole, fn)
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def run(self):
        it = self.list.currentItem()
        if it is None:
            return
        fn = it.data(Qt.UserRole)
        self.accept()
        fn()

    def show_centered(self):
        win = self.parent().window()
        g = win.geometry()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 90)
        self.edit.setFocus()
        self.exec()


def collapse_menubar(mb, host: QWidget, layout, index: int = 0) -> QToolButton:
    """Esconde una barra de menús clásica (Archivo, Edición…) tras un botón ☰ puesto en `layout`. Los atajos
    de teclado de sus acciones siguen funcionando (se añaden también a `host`)."""
    from PySide6.QtWidgets import QMenu

    b = icon_button("menu", "Menús: Archivo, Edición, Imagen, Capa… (o busca la acción con Ctrl+K)")
    b.setPopupMode(QToolButton.InstantPopup)
    b.setStyleSheet("QToolButton::menu-indicator { image: none; }")
    m = QMenu(b)
    stack = []
    for a in mb.actions():
        if a.menu() is not None:
            m.addMenu(a.menu())
            stack.append(a.menu())
    while stack:
        menu = stack.pop()
        for a in menu.actions():
            if a.menu() is not None:
                stack.append(a.menu())
            elif not a.shortcut().isEmpty():
                host.addAction(a)  # el atajo vale aunque la barra esté oculta
    b.setMenu(m)
    mb.hide()
    if layout is not None:
        layout.insertWidget(index, b)
    return b


def section(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Bloque del panel lateral: título pequeño en mayúsculas y contenido, sin cajas ni flechas."""
    f = QFrame()
    f.setObjectName("block")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 12, 16, 12)
    v.setSpacing(8)
    t = QLabel(title.upper())
    t.setObjectName("blockTitle")
    v.addWidget(t)
    return f, v


__all__ = ["FlowLayout", "flow", "as_chip", "chip", "icon_button", "ComboChips", "SideSheet", "CommandPalette",
           "section", "QCheckBox", "QSizePolicy"]
