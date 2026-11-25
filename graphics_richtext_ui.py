"""Graphics: edición de texto con formato (texto enriquecido de graphics_engine).

- `RichTextBox`: cuadro del panel Propiedades con la fuente, el color y los tramos del texto.
- `InlineTextEditor`: se escribe en el propio lienzo (doble clic en un texto, o al añadir uno): un
  QGraphicsTextItem hijo del elemento, con su fuente, tamaño, giro, alineación, interlineado y lista.
- Los dos convierten el documento a `runs` con `doc_runs` (solo lo que cambia respecto al elemento) y dan formato a
  lo seleccionado (sin selección, a todo) con `_Formatting`. `selection_bar` = botones N K S T, color y A+/A−.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextFormat,
                           QTextListFormat, QTextOption)
from PySide6.QtWidgets import QColorDialog, QGraphicsTextItem, QHBoxLayout, QLabel, QPushButton, QTextEdit

import graphics_engine as G

K_PROP = QTextFormat.UserProperty + 1   # tamaño relativo del tramo (1 = el del elemento)
BASE_PT = 11.0


def doc_runs(doc, el: dict) -> list[dict]:
    """Documento → tramos con lo que cambia respecto al elemento (negrita y cursiva solo si difieren)."""
    out: list[dict] = []
    b = doc.begin()
    first = True
    while b.isValid():
        if not first:
            out.append({"t": "\n"})
        first = False
        text = b.text()
        for fr in b.textFormats():
            f = fr.format
            r = {"t": text[fr.start:fr.start + fr.length]}
            bold = f.fontWeight() >= 600 if f.hasProperty(QTextFormat.FontWeight) else bool(el.get("bold"))
            if bold != bool(el.get("bold")):
                r["b"] = bold
            ital = f.fontItalic() if f.hasProperty(QTextFormat.FontItalic) else bool(el.get("italic"))
            if ital != bool(el.get("italic")):
                r["i"] = ital
            if f.fontUnderline():
                r["u"] = True
            if f.fontStrikeOut():
                r["s"] = True
            if f.hasProperty(QTextFormat.ForegroundBrush):
                r["color"] = f.foreground().color().name().upper()
            if f.hasProperty(K_PROP) and abs(float(f.property(K_PROP)) - 1.0) > 1e-3:
                r["k"] = round(float(f.property(K_PROP)), 3)
            out.append(r)
        b = b.next()
    return out


class _Formatting:
    """Formato de lo seleccionado (sin selección, de todo). Quien lo use da `_doc_cursor()` y `_size(fmt, k)`."""

    def _fmt(self, r: dict) -> QTextCharFormat:
        fmt = QTextCharFormat()
        if "b" in r:
            fmt.setFontWeight(QFont.Bold if r["b"] else QFont.Normal)
        if "i" in r:
            fmt.setFontItalic(bool(r["i"]))
        if r.get("u"):
            fmt.setFontUnderline(True)
        if r.get("s"):
            fmt.setFontStrikeOut(True)
        if r.get("color"):
            fmt.setForeground(G.qcolor(r["color"]))
        if r.get("k"):
            fmt.setProperty(K_PROP, float(r["k"]))
            self._size(fmt, float(r["k"]))
        return fmt

    def _load_runs(self, doc):
        doc.clear()
        cur = QTextCursor(doc)
        for r in G.text_runs(self.el):
            cur.insertText(r.get("t", ""), self._fmt(r))

    def _cursor(self) -> QTextCursor:
        c = self._doc_cursor()
        if not c.hasSelection():
            c.select(QTextCursor.Document)
        return c

    def toggle(self, key: str):
        c = self._cursor()
        f = c.charFormat()
        fmt = QTextCharFormat()
        if key == "b":
            fmt.setFontWeight(QFont.Normal if f.fontWeight() >= 600 else QFont.Bold)
        elif key == "i":
            fmt.setFontItalic(not f.fontItalic())
        elif key == "u":
            fmt.setFontUnderline(not f.fontUnderline())
        elif key == "s":
            fmt.setFontStrikeOut(not f.fontStrikeOut())
        c.mergeCharFormat(fmt)

    def set_color(self, col: str | None):
        c = self._cursor()
        if col is None:   # quitar el color propio: vuelve al del texto
            fmt = c.charFormat()
            fmt.clearForeground()
            c.setCharFormat(fmt)
            return
        fmt = QTextCharFormat()
        fmt.setForeground(G.qcolor(col))
        c.mergeCharFormat(fmt)

    def scale(self, factor: float):
        c = self._cursor()
        k = float(c.charFormat().property(K_PROP) or 1.0)
        k = max(0.3, min(4.0, round(k * factor, 3)))
        fmt = QTextCharFormat()
        fmt.setProperty(K_PROP, k)
        self._size(fmt, k)
        c.mergeCharFormat(fmt)


class RichTextBox(_Formatting, QTextEdit):
    edited = Signal()

    def __init__(self, el: dict):
        super().__init__()
        self.el = el
        self.setAcceptRichText(False)   # lo pegado entra sin formato: el formato se da con los botones
        self.setTabChangesFocus(True)
        col = G.qcolor(el.get("color", "#000000"))
        bg = "#F2F2F4" if col.lightness() < 140 else "#16181F"
        self.setStyleSheet(f"QTextEdit {{ background: {bg}; color: {col.name()}; border: 1px solid #3A3F4B; "
                           "border-radius: 4px; padding: 2px; }")
        f = QFont(el.get("font", "Segoe UI"))
        f.setPointSizeF(BASE_PT)
        f.setBold(bool(el.get("bold")))
        f.setItalic(bool(el.get("italic")))
        self.document().setDefaultFont(f)
        self.load()
        self.textChanged.connect(self.edited.emit)
        # Ctrl+B/I/U: los atajos del editor (GraphicsView.text_style) los pasan aquí si el foco está en el cuadro

    def _doc_cursor(self):
        return self.textCursor()

    def _size(self, fmt, k):
        fmt.setFontPointSize(BASE_PT * k)

    def load(self):
        self.blockSignals(True)
        self._load_runs(self.document())
        self.moveCursor(QTextCursor.Start)
        self.blockSignals(False)

    def runs(self) -> list[dict]:
        return doc_runs(self.document(), self.el)


class InlineTextEditor(_Formatting, QGraphicsTextItem):
    """Escribir en el lienzo: hijo del elemento (hereda posición y giro), con su tipografía. `on_change()` tras
    cada cambio y `on_done()` al salir (Esc, clic fuera)."""

    def __init__(self, parent_item, el: dict, on_change, on_done, select_all: bool = False):
        super().__init__(parent_item)
        self.el = el
        self.on_change, self.on_done = on_change, on_done
        self._closing = False
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        doc = self.document()
        doc.setDocumentMargin(0)
        self.apply_style()
        self._load_runs(doc)
        self._block_format()
        self.setTextWidth(max(1.0, el["w"]))
        self.setPos(0, 0)
        doc.contentsChanged.connect(self._changed)
        self.setFocus(Qt.MouseFocusReason)
        c = self.textCursor()
        c.movePosition(QTextCursor.End)
        if select_all:
            c.select(QTextCursor.Document)
        self.setTextCursor(c)

    def apply_style(self):
        el = self.el
        f = G.text_font(el)
        if el.get("upper"):
            f.setCapitalization(QFont.AllUppercase)
        self.document().setDefaultFont(f)
        self.setDefaultTextColor(G.qcolor(el.get("color", "#000000")))
        opt = QTextOption(G._align_flag(el))
        opt.setWrapMode(QTextOption.WordWrap)
        self.document().setDefaultTextOption(opt)

    def _block_format(self):
        """Interlineado y lista de todos los párrafos (como los pinta el motor)."""
        doc = self.document()
        c = QTextCursor(doc)
        c.select(QTextCursor.Document)
        bf = QTextBlockFormat()
        bf.setLineHeight(float(self.el.get("line", 1.15)) * 100, QTextBlockFormat.ProportionalHeight.value
                         if hasattr(QTextBlockFormat.ProportionalHeight, "value") else 1)
        c.mergeBlockFormat(bf)
        kind = self.el.get("list") or ""
        if kind:
            lf = QTextListFormat()
            lf.setStyle(QTextListFormat.ListDisc if kind == "bullet" else QTextListFormat.ListDecimal)
            lf.setIndent(1)
            doc.setIndentWidth(G.text_font(self.el).pixelSize() * 1.1)
            c.createList(lf)

    def _doc_cursor(self):
        return self.textCursor()

    def _size(self, fmt, k):
        fmt.setProperty(QTextFormat.FontPixelSize, max(1, round(self.el.get("size", 24) * k)))

    def runs(self) -> list[dict]:
        return doc_runs(self.document(), self.el)

    def plain(self) -> str:
        return self.document().toPlainText()

    def _changed(self):
        if not self._closing:
            self.on_change(self)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.finish()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        # el menú contextual o un diálogo de color no cuentan como salir
        if e.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason):
            self.finish()

    def finish(self):
        if self._closing:
            return
        self._closing = True
        self.on_done(self)


def selection_bar(box, colors) -> QHBoxLayout:
    """N K S T, color y A+/A− para lo seleccionado en `box` (sin selección: todo el texto)."""
    r = QHBoxLayout()
    r.setSpacing(3)
    lab = QLabel("Selección")
    lab.setObjectName("muted")
    lab.setToolTip("Da formato solo a lo que selecciones en el cuadro (sin selección, a todo el texto)")
    r.addWidget(lab)
    for key, txt, tip, css in (("b", "N", "Negrita (Ctrl+B)", "font-weight: 700;"),
                               ("i", "K", "Cursiva (Ctrl+I)", "font-style: italic;"),
                               ("u", "S", "Subrayado (Ctrl+U)", "text-decoration: underline;"),
                               ("s", "T", "Tachado", "text-decoration: line-through;")):
        b = QPushButton(txt)
        b.setObjectName("seg")
        b.setToolTip(tip)
        b.setFixedWidth(30)
        b.setStyleSheet(css)
        b.clicked.connect(lambda _=False, k=key: box.toggle(k))
        r.addWidget(b)
    col = QPushButton("A")
    col.setObjectName("seg")
    col.setFixedWidth(30)
    col.setToolTip("Color de lo seleccionado (clic derecho: quitarlo)")
    col.setStyleSheet("font-weight: 700; color: #F50D8A;")

    def pick():
        from PySide6.QtWidgets import QMenu
        from graphics import _swatch_icon
        m = QMenu(col)
        for c in list(dict.fromkeys(list(colors()) + G.PALETTE))[:20]:
            m.addAction(_swatch_icon(c), c[:7]).triggered.connect(lambda _=False, c=c: box.set_color(c))
        m.addSeparator()
        m.addAction("Personalizado…").triggered.connect(lambda: _custom())
        m.addAction("Quitar el color propio").triggered.connect(lambda: box.set_color(None))
        m.exec(col.mapToGlobal(col.rect().bottomLeft()))

    def _custom():
        q = QColorDialog.getColor(QColor("#F50D8A"), col, "Color")
        if q.isValid():
            box.set_color(q.name().upper())
    col.clicked.connect(pick)
    col.setContextMenuPolicy(Qt.CustomContextMenu)
    col.customContextMenuRequested.connect(lambda *_: box.set_color(None))
    r.addWidget(col)
    for txt, f, tip in (("A+", 1.2, "Más grande"), ("A−", 1 / 1.2, "Más pequeño")):
        b = QPushButton(txt)
        b.setObjectName("seg")
        b.setFixedWidth(34)
        b.setToolTip(tip + " (lo seleccionado)")
        b.clicked.connect(lambda _=False, f=f: box.scale(f))
        r.addWidget(b)
    r.addStretch(1)
    return r
