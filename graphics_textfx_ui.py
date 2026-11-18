"""Graphics: sección «Efectos» y «Estilos de texto» del panel Propiedades (como el panel Efectos de Canva).

Rejilla de efectos con miniaturas pintadas por graphics_textfx, controles del efecto elegido (deslizador con su
número, color, pareja de colores) y estilos prefabricados que cambian fuente, color y efecto a la vez; si la
fuente del estilo no está, se descarga de Google Fonts con la pestaña Elementos.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFontDatabase, QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QSlider, QSpinBox, QToolButton,
                               QWidget)

import graphics_engine as G
import graphics_textfx as TFX

TILE = 84
_icons: dict = {}

TILE_QSS = ("QToolButton { background: transparent; border: 2px solid transparent; border-radius: 8px; "
            "padding: 2px; font-size: 8pt; } QToolButton:hover { border-color: #3A3F55; } "
            "QToolButton:checked { border-color: #8B5CF6; }")


def _icon(props: dict, font_ok: bool = True) -> QIcon:
    key = json.dumps(props, sort_keys=True) + str(font_ok)
    ic = _icons.get(key)
    if ic is None:
        p = dict(props)
        if not font_ok:
            p.pop("font", None)
        ic = _icons[key] = QIcon(QPixmap.fromImage(TFX.preview(p, TILE, TILE)))
    return ic


def _tile(text, icon, checked=False, tip=""):
    b = QToolButton()
    b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    b.setIcon(icon)
    b.setIconSize(QSize(TILE - 8, TILE - 8))
    b.setText(text)
    b.setCheckable(True)
    b.setChecked(checked)
    b.setFixedSize(TILE + 8, TILE + 22)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(TILE_QSS)
    if tip:
        b.setToolTip(tip)
    return b


def build_section(view, el: dict) -> None:
    """Añade «Efectos» (y los controles del efecto actual) y «Estilos de texto» al panel de `view`."""
    from graphics import ColorButton
    view._title("Efectos")
    anchor = view.props.itemAt(view.props.count() - 1).widget()
    view._fx_anchor = anchor
    cur = TFX.params(el)
    grid = QGridLayout()
    grid.setSpacing(4)
    base = {"color": "#7C3AED"}
    for i, (k, (label, _)) in enumerate(TFX.FX.items()):
        b = _tile(label, _icon(dict(base, tfx={"type": k})), k == cur["type"])
        b.clicked.connect(lambda _=False, k=k: _choose(view, el, k))
        grid.addWidget(b, i // 3, i % 3)
    view.props.addLayout(grid)

    t = cur["type"]
    if t != "none":
        params = {k: v for k, v in cur.items() if k != "type"}

        def setp(key, val):
            fx = dict(el.get("tfx") or {"type": t})
            fx[key] = val
            view.set_prop(el, "tfx", fx)
        for key, val in params.items():
            if key in TFX.PARAMS:
                lab, lo, hi = TFX.PARAMS[key]
                sl = QSlider(Qt.Horizontal)
                sl.setRange(lo, hi)
                sl.setValue(int(val))
                sb = QSpinBox()
                sb.setRange(lo, hi)
                sb.setValue(int(val))
                sb.setFixedWidth(52)
                if key == "dir":
                    sb.setSuffix("°")
                sl.valueChanged.connect(sb.setValue)
                sb.valueChanged.connect(sl.setValue)
                sb.valueChanged.connect(lambda v, key=key: setp(key, v))
                w = QWidget()
                h = QHBoxLayout(w)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(sl, 1)
                h.addWidget(sb)
                view._row(lab, w)
            elif key == "color":
                cb = ColorButton(val or el.get("color", "#000000"), False, view.design_colors)
                cb.changed.connect(lambda c: setp("color", c))
                view._row("Color", cb)
            elif key == "pair":
                combo = QComboBox()
                for name in TFX.PAIRS:
                    combo.addItem(name.replace("-", " y "), name)
                combo.setCurrentIndex(max(0, list(TFX.PAIRS).index(val) if val in TFX.PAIRS else 0))
                combo.currentIndexChanged.connect(lambda i: setp("pair", combo.itemData(i)))
                view._row("Colores", combo)
        if TFX.has_blur(el):
            note = QLabel("Con desenfoque: en el PDF de imprenta este texto no se convierte a contornos.")
            note.setObjectName("faint")
            note.setWordWrap(True)
            view.props.addWidget(note)

    view._title("Estilos de texto")
    fams = set(QFontDatabase.families())
    grid2 = QGridLayout()
    grid2.setSpacing(4)
    for i, (name, st) in enumerate(TFX.STYLES):
        ok = st.get("font") in fams
        b = _tile(name, _icon(st, ok), False, f"Fuente {st.get('font')}" + ("" if ok else " (se descarga al usarlo)"))
        b.setCheckable(False)
        b.clicked.connect(lambda _=False, st=st: _style(view, el, st))
        grid2.addWidget(b, i // 3, i % 3)
    view.props.addLayout(grid2)


def _rebuild(view):
    QTimer.singleShot(0, view.build_props)   # no borrar el botón dentro de su propia señal


def _choose(view, el, kind):
    if kind == "none":
        view.set_prop(el, "tfx", None)
    else:
        old = el.get("tfx") or {}
        fx = {"type": kind}
        if old.get("type") == kind:
            fx = dict(old)
        view.set_prop(el, "tfx", fx)
    _rebuild(view)


def _style(view, el, st):
    if el.get("locked"):
        return
    font = st.get("font")
    rest = {k: v for k, v in st.items() if k != "font"}
    TFX.apply_style(el, rest)
    if font and font in set(QFontDatabase.families()):
        el["font"] = font
    view.set_prop(el, "tfx", el.get("tfx"), refit_text=True)
    if font and el.get("font") != font and getattr(view, "elements", None) is not None:
        view.elements._apply_font(font)   # descarga y aplica a los textos seleccionados
    _rebuild(view)
