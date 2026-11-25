"""Panel «¿Cómo quieres editar esta sesión?» de Events (se abre al elegir la carpeta, antes de analizar, y luego
cuando se quiera con el botón de la barra lateral, el menú ⚙ o Ctrl+K).

Trabaja sobre los mismos valores que los «Ajustes guardados» (`Main.snapshot` → aquí → `Main.apply_snapshot`):
a la izquierda las preconfiguraciones (las de la sesión, las últimas usadas, las predefinidas y las tuyas), a la
derecha todo lo que se aplica a la sesión entera en tarjetas y chips. El tipo de sesión propone lo demás
(`session_setup.propose`). Lo que no está aquí (carpetas, hilos, carpeta de la nube…) sigue en el panel ⚙.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

import dcp as DCP
import engine as E
import icons as I
import luts as L
import session_presets as SP
import session_setup as SS
import styles as S
import theme as T
import ui as UI

APP = "PhootFlow"
LAST = "Los últimos usados"

_QSS = f"""
QPushButton#optCard {{ text-align: left; background: {T.SURFACE_2}; border: 1px solid {T.LINE}; border-radius: 8px;
    padding: 0; }}
QPushButton#optCard:hover {{ border-color: #34405a; }}
QPushButton#optCard:checked {{ background: rgba(245, 13, 138, 0.12); border: 1px solid {T.ACCENT}; }}
QLabel#optTitle {{ color: {T.TEXT_STRONG}; font-weight: 600; background: transparent; }}
QLabel#optText {{ color: {T.MUTED}; font-size: 8.5pt; background: transparent; }}
QLabel#setupSection {{ color: {T.MUTED}; font-size: 8pt; font-weight: 600; letter-spacing: 1.2px; }}
QFrame#presetCol {{ background: {T.SURFACE}; border-right: 1px solid {T.LINE}; }}
"""


class OptionCard(QPushButton):
    """Tarjeta que se enciende y se apaga: icono, título y una frase de qué hace."""

    def __init__(self, icon: str, title: str, text: str, width: int = 0):
        super().__init__()
        self.setObjectName("optCard")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 9, 12, 9)
        h.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(I.pixmap(icon, 20, T.ACCENT_HOVER))
        ic.setAlignment(Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("optTitle")
        self.text = QLabel(text)
        self.text.setObjectName("optText")
        self.text.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.text)
        h.addWidget(ic)
        h.addLayout(col, 1)
        for w in (ic, self.title, self.text):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        if width:
            self.setFixedWidth(width)
        self.setMinimumHeight(58)
        self.setSizePolicy(QSizePolicy.Fixed if width else QSizePolicy.Preferred, QSizePolicy.Fixed)

    def sizeHint(self):
        lay = self.layout()
        w = self.maximumWidth() if self.minimumWidth() == self.maximumWidth() else lay.sizeHint().width()
        h = lay.heightForWidth(w) if lay.hasHeightForWidth() else lay.sizeHint().height()
        return QSize(w, max(h, self.minimumHeight()))


def _section(title: str) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 14, 0, 4)
    v.setSpacing(8)
    t = QLabel(title.upper())
    t.setObjectName("setupSection")
    v.addWidget(t)
    return w, v


def _meta(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("meta")
    return lab


class ChipChoice(QWidget):
    """Chips de una sola elección ligados a una clave de los valores."""

    def __init__(self, options: list[tuple[str, str]], on_pick, icon_for=None):
        super().__init__()
        self.lay = UI.FlowLayout(self, 6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict = {}
        for value, label in options:
            b = UI.chip(label, icon_for(value) if icon_for else None)
            b.clicked.connect(lambda _, v=value: on_pick(v))
            self.group.addButton(b)
            self.lay.addWidget(b)
            self.buttons[value] = b

    def set(self, value):
        b = self.buttons.get(value)
        if b is not None:
            b.setChecked(True)
        else:  # ninguno: quitar la marca
            self.group.setExclusive(False)
            for x in self.buttons.values():
                x.setChecked(False)
            self.group.setExclusive(True)


class SessionSetupDialog(QDialog):
    """`main` es la ventana de Events. `start`: se abre al elegir la carpeta (el botón principal analiza)."""

    def __init__(self, main, cfg_dir: str, start: bool = False, clouds: list[str] | None = None):
        super().__init__(main)
        self.main, self.cfg_dir, self.start = main, cfg_dir, start
        self.qs = QSettings(APP, APP)
        self.setWindowTitle("¿Cómo quieres editar esta sesión?")
        self.setStyleSheet(_QSS)
        self.resize(1120, 780)
        self.styles = S.all_styles(cfg_dir)
        self.current = dict(main.snapshot())      # lo que hay ahora en Events
        self.v = dict(self.current)
        self.v.setdefault("crop", True)
        self.v.setdefault("enhance", False)
        self.v.setdefault("portrait", False)
        self.preset_name = getattr(main, "preset_name", "") or ""
        self.clouds = clouds or []
        self._busy = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_presets())
        right = QVBoxLayout()
        right.setContentsMargins(22, 16, 22, 14)
        right.setSpacing(6)
        t = QLabel("¿Cómo quieres editar esta sesión?")
        t.setObjectName("h1")
        folder = main.src.text().strip()
        sub = QLabel((f"<b>{os.path.basename(os.path.normpath(folder))}</b> · " if folder else "") +
                     "Todo lo que elijas se aplica a la sesión entera; en la revisión puedes cambiar cualquier foto.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        right.addWidget(t)
        right.addWidget(sub)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 0, 10, 0)
        self.body.setSpacing(0)
        sc.setWidget(body)
        right.addWidget(sc, 1)
        self._build_options()
        self.body.addStretch(1)

        self.sum = QLabel("")
        self.sum.setObjectName("muted")
        self.sum.setWordWrap(True)
        right.addSpacing(6)
        right.addWidget(self.sum)
        foot = QHBoxLayout()
        foot.addStretch(1)
        b_save = UI.chip("Guardar como perfil…", "save", "Guarda esta configuración con un nombre para otras sesiones",
                         checkable=False)
        b_save.clicked.connect(self.save_as)
        b_more = UI.chip("Más ajustes", "gear", "Carpetas, calidad, hilos, carpeta de la nube… (panel ⚙)",
                         checkable=False)
        b_more.clicked.connect(self.more)
        b_cancel = QPushButton("Ahora no" if start else "Cancelar")
        b_cancel.setObjectName("ghost")
        b_cancel.clicked.connect(self.reject)
        self.b_ok = QPushButton("  Analizar la sesión" if start else "  Aplicar a la sesión")
        self.b_ok.setObjectName("primary")
        self.b_ok.setIcon(I.icon("scan" if start else "check", 16))
        self.b_ok.setDefault(True)
        self.b_ok.clicked.connect(self.accept)
        for b in (b_save, b_more, b_cancel, self.b_ok):
            foot.addWidget(b)
        right.addLayout(foot)
        wrap = QWidget()
        wrap.setLayout(right)
        root.addWidget(wrap, 1)
        self.refresh()

    # ------------------------------------------------------------ preconfiguraciones
    def _build_presets(self) -> QWidget:
        col = QFrame()
        col.setObjectName("presetCol")
        col.setFixedWidth(290)
        outer = QVBoxLayout(col)
        outer.setContentsMargins(14, 16, 10, 14)
        outer.setSpacing(6)
        t = QLabel("PRECONFIGURACIONES")
        t.setObjectName("setupSection")
        outer.addWidget(t)
        hint = QLabel("Empieza por una y cambia lo que quieras a la derecha.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.p_lay = QVBoxLayout(inner)
        self.p_lay.setContentsMargins(0, 4, 4, 4)
        self.p_lay.setSpacing(6)
        sc.setWidget(inner)
        outer.addWidget(sc, 1)
        self.p_group = QButtonGroup(self)
        self.p_group.setExclusive(True)
        self.p_cards: dict[str, OptionCard] = {}
        self.fill_presets()
        return col

    def fill_presets(self):
        for c in self.p_cards.values():
            self.p_group.removeButton(c)
            c.deleteLater()
        self.p_cards = {}
        while self.p_lay.count():
            it = self.p_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.sources: dict[str, dict] = {}
        folder = self.main.src.text().strip()
        own = None
        if folder:
            try:
                own = SP.load_for_session(os.path.join(self.main.out_dir(), ".phootflow"))
            except Exception:
                own = None
        if own:
            self.sources[SP.OWN] = (own, "Los que tenía esta sesión la última vez" +
                                    (f" ({own['_preset']})." if own.get("_preset") else "."), "folder")
        self.sources[LAST] = (self.current, "Lo que tienes ahora en Events, sin cambiar nada.", "refresh")
        for name, p in SP.all_presets(self.cfg_dir).items():
            mine = SP.is_user(self.cfg_dir, name) and name not in SP.BUILTIN
            self.sources[name] = (p, SP.describe(p) + (" (tuyo)" if mine else ""),
                                  "star" if mine else I.for_style(p.get("style") or name))
        for name, (_p, text, icon) in self.sources.items():
            c = OptionCard(icon, name, text, 262)
            c.clicked.connect(lambda _, n=name: self.pick_preset(n))
            self.p_group.addButton(c)
            self.p_lay.addWidget(c)
            self.p_cards[name] = c
        self.p_lay.addStretch(1)
        sel = self.preset_name if self.preset_name in self.p_cards else LAST
        self.p_cards[sel].setChecked(True)

    def pick_preset(self, name: str):
        vals = self.sources.get(name, ({},))[0]
        base = dict(self.current)  # lo que el perfil no trae (nube, hilos…) se queda como está
        base.setdefault("crop", True)
        base.setdefault("enhance", False)
        base.update(SP.clean(vals))
        self.v = base
        self.preset_name = "" if name in (LAST, SP.OWN) else name
        if name == SP.OWN:
            self.preset_name = vals.get("_preset", "")
        self.refresh()

    # ------------------------------------------------------------ opciones
    def _toggle(self, key: str, icon: str, title: str, text: str) -> OptionCard:
        c = OptionCard(icon, title, text, 300)
        c.toggled.connect(lambda on, k=key: self._set(k, on))
        self.toggles[key] = c
        return c

    def _build_options(self):
        self.toggles: dict[str, OptionCard] = {}
        self.choices: dict[str, ChipChoice] = {}

        # tipo de sesión
        w, v = _section("Tipo de sesión")
        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        self.type_cards: dict[str, OptionCard] = {}
        grid = UI.FlowLayout(None, 8)
        gw = QWidget()
        gw.setLayout(grid)
        for t in SS.TYPES:
            c = OptionCard(t.icon, t.label, t.text, 300)
            c.clicked.connect(lambda _, k=t.key: self.pick_type(k))
            self.type_group.addButton(c)
            grid.addWidget(c)
            self.type_cards[t.key] = c
        for name in self.styles:
            if name.startswith("Mi estilo: "):
                c = OptionCard("wand", name.replace("Mi estilo: ", "") + " ✦",
                               "Tu estilo aprendido de tus sesiones ya editadas.", 300)
                c.clicked.connect(lambda _, n=name: self.pick_learned(n))
                self.type_group.addButton(c)
                grid.addWidget(c)
                self.type_cards[name] = c
        v.addWidget(gw)
        v.addWidget(_meta("Al elegir el tipo se proponen los valores de abajo; luego puedes cambiar cada uno."))
        self.body.addWidget(w)

        # encuadre
        w, v = _section("Encuadre y formatos")
        v.addWidget(UI.flow([self._toggle("crop", "crop", "Recorte inteligente",
                                          "Cada foto con el mejor encuadre: la pareja, la pelota y quien la "
                                          "disputa, o lo que destaca, con reglas de composición."),
                             self._toggle("straight", "crop", "Enderezar",
                                          "Endereza el horizonte y las verticales torcidas.")], 8))
        v.addWidget(_meta("Además de la entrega, exportar en"))
        self.ratio_chips = {}
        chips = [UI.chip("Original", tip="La proporción de la cámara")]
        self.ratio_chips["orig"] = chips[0]
        chips[0].toggled.connect(lambda on: self._set("orig", on))
        for r in E.RATIOS:
            b = UI.chip(r, tip=f"Exportar también en {r}")
            b.toggled.connect(lambda on, r=r: self._set_list("ratios", r, on))
            self.ratio_chips[r] = b
            chips.append(b)
        v.addWidget(UI.flow(chips))
        self.body.addWidget(w)

        # retoque
        w, v = _section("Retoque y mejoras")
        v.addWidget(UI.flow([
            self._toggle("portrait", "eyes", "Retocar retratos",
                         "Piel suavizada, volumen, ojeras, dientes y ojos en las fotos con caras grandes."),
            self._toggle("enhance", "wand", "Mejorar con IA",
                         "Enfoca y limpia las fotos blandas, con ruido o caras poco nítidas que lo piden "
                         "(lento: ≈1 min por foto)."),
        ], 8))
        v.addWidget(_meta("Desenfocar el fondo (solo retratos y parejas; el sujeto queda nítido)"))
        import bokeh as BK
        self.choices["bokeh"] = ChipChoice([("", "No")] + [(lv, f"{lv} ≈{BK.LEVEL_FSTOP[lv]}") for lv in BK.LEVELS],
                                           lambda val: self._set("bokeh", val))
        v.addWidget(self.choices["bokeh"])
        self.body.addWidget(w)

        # color
        w, v = _section("Color y look")
        v.addWidget(UI.flow([
            self._toggle("cam_prof", "palette", "Color de la cámara",
                         "Revela los RAW con el perfil del estilo que tenía la cámara (Estándar, Vívido…)."),
            self._toggle("uniform", "sliders", "Edición uniforme",
                         "Una sola curva y un solo color para toda la sesión, con el mismo brillo en las caras."),
            self._toggle("cam", "studio_photo", "Look de la cámara",
                         "Parte del contraste y el color del JPEG que lleva el RAW."),
        ], 8))
        v.addWidget(_meta("Look"))
        self.choices["look"] = ChipChoice([(k, k) for k in E.COLOR_LOOKS], lambda val: self._set("look", val))
        v.addWidget(self.choices["look"])
        row = QHBoxLayout()
        row.setSpacing(8)
        self.lut_cb = QComboBox()
        self.lut_cb.setToolTip("LUT de este tipo de sesión y los comunes (incluida la biblioteca de PhootFlow).")
        self.lut_cb.currentIndexChanged.connect(self._lut_changed)
        self.lut_k = QSpinBox()
        self.lut_k.setRange(0, 100)
        self.lut_k.setSuffix(" %")
        self.lut_k.setToolTip("Intensidad del LUT")
        self.lut_k.valueChanged.connect(lambda val: self._set("lutk", int(val)))
        row.addWidget(_meta("LUT y gradación"))
        row.addWidget(self.lut_cb, 1)
        row.addWidget(self.lut_k)
        v.addLayout(row)
        v.addWidget(UI.flow([self._toggle("bw", "bw", "Versión en blanco y negro",
                                          "Además del color, una versión B/N de cada foto en su carpeta.")], 8))
        self.choices["bw_style"] = ChipChoice([(k, k) for k in E.BW_STYLES], lambda val: self._set("bw_style", val))
        v.addWidget(self.choices["bw_style"])
        self.body.addWidget(w)

        # descartes
        w, v = _section("Descartar automáticamente")
        v.addWidget(UI.flow([
            self._toggle("focus", "blur", "Desenfocadas", "Las movidas o desenfocadas (mira la nitidez de las caras)."),
            self._toggle("eyes", "eyes", "Ojos cerrados", "Las de ojos cerrados claros (apagado: solo avisa)."),
            self._toggle("dup", "dupes", "Repetidas", "De cada ráfaga, solo la más nítida."),
            self._toggle("exp", "sun", "Mal expuestas", "Las de exposición irrecuperable."),
        ], 8))
        row = QHBoxLayout()
        row.addWidget(_meta("Exigencia"))
        self.choices["sens"] = ChipChoice([(k, k) for k in E.SENS], lambda val: self._set("sens", val))
        row.addWidget(self.choices["sens"], 1)
        v.addLayout(row)
        v.addWidget(_meta("Los descartes nunca se borran: se mueven a «Descartadas» al exportar."))
        self.body.addWidget(w)

        # revelado
        w, v = _section("Reducción de grano")
        self.choices["nr"] = ChipChoice([(k, k) for k in E.NR_LEVELS], lambda val: self._set("nr", val))
        v.addWidget(self.choices["nr"])
        self.body.addWidget(w)

        # entrega
        w, v = _section("Qué exportar")
        v.addWidget(_meta("Tamaños"))
        self.size_chips = {}
        chips = []
        for k, label in (("max", "Máxima"), ("2048", "2048 px"), ("1080", "1080 px (redes)")):
            b = UI.chip(label)
            b.toggled.connect(lambda on, k=k: self._set_list("sizes", k, on))
            self.size_chips[k] = b
            chips.append(b)
        v.addWidget(UI.flow(chips))
        v.addWidget(UI.flow([
            self._toggle("review", "review", "Revisar antes de exportar",
                         "Abre la revisión para aprobar, recortar o retocar antes de exportar."),
            self._toggle("zip", "archive", "ZIP de originales",
                         "Solo las aprobadas: sus originales en un ZIP sin pérdida, junto a «Editadas»."),
            self._toggle("trash_orig", "archive", "Quitar los originales tras el ZIP",
                         "Sin duplicados: los que el ZIP guarda idénticos (comprobado uno a uno) salen de la carpeta. "
                         "Al volver a abrir la sesión se recuperan del ZIP."),
            self._toggle("move", "move", "Apartar las descartadas",
                         "Al terminar, sus originales se mueven a «Descartadas» (no se borra nada)."),
            self._toggle("del_disc", "trash", "Borrar las descartadas",
                         "Al terminar de exportar, borra para siempre sus originales. Se pregunta antes, cada vez."),
            self._toggle("xmp", "sliders", "XMP para Lightroom", "Exposición, giro, recorte y descartes; nunca pisa uno existente."),
            self._toggle("exif", "info", "Conservar EXIF", "Cámara, objetivo y fecha en las editadas."),
        ], 8))
        row = QHBoxLayout()
        row.addWidget(_meta("Nube"))
        self.cloud_cb = QComboBox()
        self.cloud_cb.addItems(self.clouds or ["No subir"])
        self.cloud_cb.setToolTip("La carpeta de destino y la cuenta se configuran en ⚙ › Nube.")
        self.cloud_cb.currentIndexChanged.connect(lambda i: self._set("cloud", int(i)))
        row.addWidget(self.cloud_cb, 1)
        row.addSpacing(12)
        row.addWidget(_meta("Calidad JPG"))
        self.q = QSpinBox()
        self.q.setRange(70, 100)
        self.q.valueChanged.connect(lambda val: self._set("quality", int(val)))
        row.addWidget(self.q)
        v.addLayout(row)
        self.body.addWidget(w)

    # ------------------------------------------------------------ cambios
    def _set(self, key, value):
        if self._busy:
            return
        if key == "cam_prof":
            prev = self.current.get("cam_prof")
            value = (prev if prev and prev != DCP.NONE else DCP.AUTO) if value else DCP.NONE
        self.v[key] = value
        if key == "trash_orig" and value:  # quitar los originales solo con el ZIP hecho y comprobado
            self.v["zip"] = True
        elif key == "zip" and not value:
            self.v["trash_orig"] = False
        self.refresh()

    def _set_list(self, key, item, on):
        if self._busy:
            return
        cur = [x for x in (self.v.get(key) or []) if x != item]
        if on:
            cur.append(item)
        self.v[key] = cur
        self.refresh()

    def _lut_changed(self, _i):
        if self._busy:
            return
        self.v["lut"] = self.lut_cb.currentData() or ""
        self.refresh()

    def pick_type(self, key: str):
        self.v = SS.propose(key, self.v)
        self.qs.setValue("setup/type", key)
        self.v["lut"] = ""  # el LUT es de cada tipo
        self.refresh()

    def pick_learned(self, name: str):
        self.v["style"] = name
        self.v["lut"] = ""
        self.refresh()

    def _lut_kind(self) -> str:
        name = self.v.get("style") or S.AUTO
        if name == S.AUTO:
            return L.COMMON
        if name.startswith("Mi estilo: "):
            return self.styles.get(name, {}).get("_base") or L.COMMON
        return name

    def refresh(self):
        self._busy = True
        try:
            style = self.v.get("style") or S.AUTO
            key = style if style in self.type_cards else SS.type_of(self.v, self.styles)
            if key in self.type_cards:
                self.type_cards[key].setChecked(True)
            for k, c in self.toggles.items():
                on = (self.v.get(k) not in (DCP.NONE, "", None)) if k == "cam_prof" else bool(self.v.get(k))
                c.setChecked(on)
            for k, ch in self.choices.items():
                ch.set(self.v.get(k, ""))
            self.choices["bw_style"].setEnabled(bool(self.v.get("bw")))
            ratios = self.v.get("ratios") or []
            for r, b in self.ratio_chips.items():
                b.setChecked(bool(self.v.get("orig")) if r == "orig" else r in ratios)
            sizes = self.v.get("sizes") or []
            for k, b in self.size_chips.items():
                b.setChecked(k in sizes)
            # LUT del tipo
            self.lut_cb.clear()
            self.lut_cb.addItem("Sin LUT", None)
            try:
                for label, path in L.available(self.cfg_dir, self._lut_kind()):
                    self.lut_cb.addItem(label, path)
            except Exception:
                pass
            want = self.v.get("lut") or None
            idx = self.lut_cb.findData(want) if want else 0
            if idx < 0 and want:
                self.lut_cb.addItem(os.path.splitext(os.path.basename(str(want)))[0], want)
                idx = self.lut_cb.count() - 1
            self.lut_cb.setCurrentIndex(max(0, idx))
            self.lut_k.setValue(int(self.v.get("lutk", 100) or 100))
            self.lut_k.setEnabled(bool(want))
            self.cloud_cb.setCurrentIndex(max(0, min(self.cloud_cb.count() - 1, int(self.v.get("cloud") or 0))))
            self.q.setValue(int(self.v.get("quality", 95) or 95))
            t = SS.BY_KEY.get(SS.type_of(self.v, self.styles))
            head = (style.replace("Mi estilo: ", "") + " ✦") if style.startswith("Mi estilo: ") else (t.label if t else style)
            self.sum.setText(f"<b>{head}</b> · " + SS.summary(self.v))
        finally:
            self._busy = False

    # ------------------------------------------------------------ acciones
    def values(self) -> dict:
        return SP.clean(self.v)

    def save_as(self):
        name, ok = QInputDialog.getText(self, APP, "Nombre de esta configuración (p. ej. «Bodas con LUT cálido»):",
                                        text=self.preset_name)
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in SP.all_presets(self.cfg_dir) and QMessageBox.question(
                self, APP, f"Ya hay una configuración «{name}». ¿Sustituirla?") != QMessageBox.Yes:
            return
        try:
            SP.save_preset(self.cfg_dir, name, self.values())
        except (OSError, ValueError) as e:
            return QMessageBox.warning(self, APP, f"No se pudo guardar: {e}")
        self.preset_name = name
        self.fill_presets()
        if hasattr(self.main, "reload_presets"):
            self.main.reload_presets(name)

    def more(self):
        """Aplica lo elegido y abre el panel ⚙ (carpetas, hilos, nube…); al cerrarlo, vuelve aquí."""
        self.main.apply_snapshot(self.values())
        self.main.open_settings()
        self.current = dict(self.main.snapshot())
        self.v.update({k: self.current[k] for k in ("cloud", "cloud_folder", "quality", "workers") if k in self.current})
        self.refresh()
