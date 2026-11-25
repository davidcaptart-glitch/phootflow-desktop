"""Diálogos de los efectos «de plugin» (fx_engine), comunes a Graphics, Edits y Studio.

- `UpscaleDialog`: ampliar ×2/×3/×4 con IA (Real-ESRGAN) o clásica, con tiempo estimado, progreso y cancelar.
- `GeneratorDialog`: texturas, partículas (nieve, confeti…) y efectos (cromo, fuego…) con vista previa.
- `GridDialog`: retícula y guías (columnas, filas, medianil, márgenes y guías sueltas), como GuideGuide.
- `DoubleExposureDialog`: elegir la segunda foto y el modo de la doble exposición.
- `ParticleControls`: controles de una capa de partículas (los usa el panel de propiedades de Edits).
"""
from __future__ import annotations

import os
import time

import numpy as np
from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

import fx_engine as FX


def to_qimage(a: np.ndarray) -> QImage:
    """float 0..1 o uint8, RGB o RGBA → QImage (copia)."""
    if a.dtype != np.uint8:
        a = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    a = np.ascontiguousarray(a)
    h, w, ch = a.shape
    fmt = QImage.Format_RGBA8888 if ch == 4 else QImage.Format_RGB888
    return QImage(a.data, w, h, ch * w, fmt).copy()


def checker(w: int, h: int, cell: int = 10) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((yy // cell) + (xx // cell)) % 2).astype(np.float32)
    return np.repeat((0.78 + 0.12 * c)[..., None], 3, 2)


def save_png(rgba: np.ndarray, path: str) -> str:
    from PIL import Image
    a = rgba if rgba.dtype == np.uint8 else (np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8)
    Image.fromarray(a, "RGBA" if a.shape[2] == 4 else "RGB").save(path)
    return path


def unique_path(folder: str, stem: str, ext: str) -> str:
    os.makedirs(folder, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in stem)[:40] or "fx"
    return os.path.join(folder, f"{safe}_{int(time.time() * 1000) % 10 ** 10}.{ext}")


class _Job(QThread):
    progress = Signal(int, int, str)
    done = Signal(object, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.cancelled = False

    def run(self):
        try:
            self.done.emit(self.fn(self), "")
        except Exception as ex:  # noqa: BLE001 — se muestra al usuario
            self.done.emit(None, str(ex))


# ------------------------------------------------------------------ ampliar con IA

class UpscaleDialog(QDialog):
    """Pide el factor y amplía `load()` (función que devuelve la imagen float 0..1 a tamaño completo)."""

    def __init__(self, parent, load, size: tuple[int, int], title="Ampliar con IA"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.load, self.size_ = load, size
        self.result = None
        self.factor = 2.0
        v = QVBoxLayout(self)
        w0, h0 = size
        v.addWidget(QLabel(f"Tamaño actual: {w0} × {h0} px ({w0 * h0 / 1e6:.1f} MP)"))
        row = QHBoxLayout()
        self.rb = {}
        for f in (2, 3, 4):
            r = QRadioButton(f"×{f}")
            r.toggled.connect(lambda on, f=f: on and self._set_factor(f))
            row.addWidget(r)
            self.rb[f] = r
        row.addStretch(1)
        v.addLayout(row)
        self.ai = QCheckBox("Con IA (Real-ESRGAN: bordes nítidos, sin dientes de sierra). La primera vez se "
                            f"descarga el modelo ({FX.UPSCALE_MB} MB).")
        self.ai.setChecked(True)
        self.ai.toggled.connect(lambda *_: self._info())
        v.addWidget(self.ai)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        self.bar = QProgressBar()
        self.bar.hide()
        v.addWidget(self.bar)
        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.button(QDialogButtonBox.Ok).setText("Ampliar")
        self.bb.accepted.connect(self._go)
        self.bb.rejected.connect(self._cancel)
        v.addWidget(self.bb)
        self.job = None
        self.rb[2].setChecked(True)

    def _set_factor(self, f):
        self.factor = float(f)
        self._info()

    def _info(self):
        w0, h0 = self.size_
        w1, h1 = int(w0 * self.factor), int(h0 * self.factor)
        t = FX.upscale_seconds(h0, w0) if self.ai.isChecked() else w1 * h1 / 40e6
        mins = f"{t / 60:.0f} min" if t >= 90 else f"{max(1, t):.0f} s"
        warn = "  ·  Muy grande: puede ir lento y ocupar mucha memoria." if w1 * h1 > 150e6 else ""
        self.info.setText(f"Resultado: {w1} × {h1} px ({w1 * h1 / 1e6:.0f} MP) · unos {mins}{warn}")

    def _go(self):
        ai, factor = self.ai.isChecked(), self.factor
        self.bb.button(QDialogButtonBox.Ok).setEnabled(False)
        self.bar.show()
        self.bar.setRange(0, 0)

        def work(job):
            if ai and not FX.upscaler_ready():
                job.progress.emit(0, 0, "Descargando el modelo de ampliación…")
                FX.ensure_upscaler(lambda d, t: job.progress.emit(d, t, "Descargando el modelo…"))
            job.progress.emit(0, 0, "Abriendo la foto…")
            img = self.load()
            if img is None:
                raise RuntimeError("No se pudo abrir la imagen")
            return FX.upscale(img, factor, ai, lambda k, n: job.progress.emit(k, n, "Ampliando…"),
                              lambda: job.cancelled)

        self.job = _Job(work)
        self.job.progress.connect(self._prog)
        self.job.done.connect(self._done)
        self.job.start()

    def _prog(self, k, n, text):
        self.info.setText(text)
        if n:
            self.bar.setRange(0, n)
            self.bar.setValue(k)
        else:
            self.bar.setRange(0, 0)

    def _done(self, res, err):
        self.job = None
        if err:
            if err != "Cancelado":
                QMessageBox.warning(self, "Ampliar", f"No se pudo ampliar: {err}")
            self.reject()
            return
        self.result = res
        self.accept()

    def _cancel(self):
        if self.job is not None:
            self.job.cancelled = True
            self.info.setText("Cancelando…")
            return
        self.reject()


# ------------------------------------------------------------------ controles de partículas

def _slider(lo, hi, val):
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(int(val))
    return s


class ParticleControls(QWidget):
    """Tipo, densidad, tamaño, profundidad, ángulo, movimiento, opacidad, colores y semilla."""
    changed = Signal(dict, bool)  # parámetros, final (al soltar)

    def __init__(self, p: dict | None = None):
        super().__init__()
        self.p = dict(p or FX.particle_defaults("snow"))
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox()
        for k, t in FX.PARTICLES.items():
            self.kind.addItem(t, k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(self.p.get("kind", "snow"))))
        self.kind.currentIndexChanged.connect(self._kind)
        f.addRow("Tipo", self.kind)
        self.sl = {}
        for key, lab, lo, hi in (("density", "Cantidad", 0, 100), ("size", "Tamaño", 0, 100),
                                 ("depth", "Profundidad", 0, 100), ("angle", "Ángulo", -60, 60),
                                 ("motion", "Movimiento", 0, 100), ("opacity", "Opacidad", 0, 100)):
            s = _slider(lo, hi, self.p.get(key, 50))
            s.valueChanged.connect(lambda v, key=key: self._set(key, v, False))
            s.sliderReleased.connect(lambda: self.changed.emit(dict(self.p), True))
            f.addRow(lab, s)
            self.sl[key] = s
        row = QHBoxLayout()
        self.colbtn = QPushButton("Colores…")
        self.colbtn.clicked.connect(self._colors)
        seed = QPushButton("Otra variación")
        seed.setToolTip("Reparte las partículas de otra manera")
        seed.clicked.connect(self._reseed)
        row.addWidget(self.colbtn)
        row.addWidget(seed)
        f.addRow(row)
        self._paint_colors()

    def set_params(self, p: dict | None):
        """Muestra los parámetros de otra capa sin emitir cambios."""
        self.p = dict(p or FX.particle_defaults("snow"))
        self.kind.blockSignals(True)
        self.kind.setCurrentIndex(max(0, self.kind.findData(self.p.get("kind", "snow"))))
        self.kind.blockSignals(False)
        for key, s in self.sl.items():
            s.blockSignals(True)
            s.setValue(int(self.p.get(key, 50)))
            s.blockSignals(False)
        self._paint_colors()

    def _set(self, key, v, final):
        self.p[key] = float(v)
        self.changed.emit(dict(self.p), final)

    def _kind(self, *_):
        k = self.kind.currentData()
        d = FX.particle_defaults(k)
        self.p.update(kind=k, colors=d["colors"], angle=d["angle"], motion=d["motion"])
        for key in ("angle", "motion"):
            self.sl[key].blockSignals(True)
            self.sl[key].setValue(int(self.p[key]))
            self.sl[key].blockSignals(False)
        self._paint_colors()
        self.changed.emit(dict(self.p), True)

    def _colors(self):
        cols = list(self.p.get("colors") or ["#ffffff"])
        c = QColorDialog.getColor(QColor(cols[0]), self, "Color principal (los demás se mantienen)")
        if c.isValid():
            cols[0] = c.name()
            self.p["colors"] = cols
            self._paint_colors()
            self.changed.emit(dict(self.p), True)

    def _paint_colors(self):
        cols = self.p.get("colors") or ["#ffffff"]
        self.colbtn.setStyleSheet(f"border-left: 14px solid {cols[0]};")

    def _reseed(self):
        self.p["seed"] = int(self.p.get("seed", 7)) + 1
        self.changed.emit(dict(self.p), True)


# ------------------------------------------------------------------ generador

class GeneratorDialog(QDialog):
    """Texturas, partículas y efectos con vista previa. `effect_alpha(px)` (opcional) devuelve el alfa de la
    forma seleccionada con margen para los efectos; `size` = (ancho, alto) en px de la salida."""

    def __init__(self, parent, size: tuple[int, int], effect_alpha=None, start: str = "texture"):
        super().__init__(parent)
        self.setWindowTitle("Texturas, partículas y efectos")
        self.size_ = size
        self.effect_alpha = effect_alpha
        self.result = None  # RGBA uint8
        self.kind = start
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.tabs = QTabWidget()
        # texturas
        tw = QWidget()
        tf = QFormLayout(tw)
        self.tex = QComboBox()
        for k, t in FX.TEXTURES.items():
            self.tex.addItem(t, k)
        self.tex.currentIndexChanged.connect(self._tex_kind)
        tf.addRow("Textura", self.tex)
        self.c1, self.c2 = QPushButton(), QPushButton()
        for b in (self.c1, self.c2):
            b.clicked.connect(lambda _=False, b=b: self._pick(b))
        cr = QHBoxLayout()
        cr.addWidget(self.c1)
        cr.addWidget(self.c2)
        tf.addRow("Colores", cr)
        self.scale = _slider(1, 100, 45)
        self.detail = _slider(0, 100, 50)
        for s in (self.scale, self.detail):
            s.valueChanged.connect(self._later)
        tf.addRow("Tamaño", self.scale)
        tf.addRow("Detalle", self.detail)
        self.tseed = QSpinBox()
        self.tseed.setRange(1, 9999)
        self.tseed.setValue(1)
        self.tseed.valueChanged.connect(self._later)
        tf.addRow("Variación", self.tseed)
        self.tabs.addTab(tw, "Texturas")
        # partículas
        self.pc = ParticleControls(FX.particle_defaults("snow"))
        self.pc.changed.connect(lambda *_: self._later())
        pw = QWidget()
        pv = QVBoxLayout(pw)
        pv.addWidget(self.pc)
        hint = QLabel("Se añade como imagen transparente del tamaño de la página (encima de todo).")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        pv.addWidget(hint)
        pv.addStretch(1)
        self.tabs.addTab(pw, "Partículas")
        # efectos
        ew = QWidget()
        ef = QFormLayout(ew)
        self.fx = QComboBox()
        for k, t in FX.EFFECTS.items():
            self.fx.addItem(t, k)
        self.fx.currentIndexChanged.connect(self._later)
        ef.addRow("Efecto", self.fx)
        self.fxcol = QPushButton()
        self.fxcol.clicked.connect(lambda: self._pick(self.fxcol))
        self._set_btn(self.fxcol, "#7fd4ff")
        ef.addRow("Color (rayos, neón)", self.fxcol)
        self.fseed = QSpinBox()
        self.fseed.setRange(1, 9999)
        self.fseed.valueChanged.connect(self._later)
        ef.addRow("Variación", self.fseed)
        note = QLabel("Sobre el texto o la forma seleccionada: se crea una imagen encima y el original se oculta "
                      "(sigue en Capas para editarlo y volver a aplicar el efecto).")
        note.setObjectName("muted")
        note.setWordWrap(True)
        ef.addRow(note)
        self.tabs.addTab(ew, "Efectos")
        self.tabs.setTabEnabled(2, effect_alpha is not None)
        self.tabs.currentChanged.connect(self._later)
        left.addWidget(self.tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Añadir al diseño")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        left.addWidget(bb)
        lay.addLayout(left, 1)
        self.prev = QLabel()
        self.prev.setFixedSize(380, 380)
        self.prev.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.prev)
        self._timer = QTimer(self, singleShot=True, interval=160)
        self._timer.timeout.connect(self._preview)
        self._tex_kind()
        self.tabs.setCurrentIndex({"texture": 0, "particles": 1, "effect": 2}.get(start, 0)
                                  if (start != "effect" or effect_alpha is not None) else 0)

    @staticmethod
    def _set_btn(b, col):
        b.setProperty("col", col)
        b.setText(col)
        b.setStyleSheet(f"border-left: 16px solid {col};")

    def _pick(self, b):
        c = QColorDialog.getColor(QColor(b.property("col") or "#ffffff"), self)
        if c.isValid():
            self._set_btn(b, c.name())
            self._later()

    def _tex_kind(self, *_):
        a, b = FX.TEXTURE_COLORS.get(self.tex.currentData(), ("#000000", "#ffffff"))
        self._set_btn(self.c1, a)
        self._set_btn(self.c2, b)
        self._later()

    def _later(self, *_):
        self._timer.start()

    def _render(self, w, h, preview=False):
        i = self.tabs.currentIndex()
        if i == 0:
            rgb = FX.texture(self.tex.currentData(), w, h, self.c1.property("col"), self.c2.property("col"),
                             self.scale.value(), self.detail.value(), self.tseed.value())
            return np.dstack([rgb, np.ones(rgb.shape[:2], np.float32)])
        if i == 1:
            return FX.particles_rgba(h, w, self.pc.p)
        a = self.effect_alpha(max(w, h))
        return FX.effect(self.fx.currentData(), a, self.fseed.value(), self.fxcol.property("col"))

    def _preview(self):
        W, H = self.size_
        k = 380 / max(W, H, 1)
        w, h = max(8, int(W * k)), max(8, int(H * k))
        try:
            if self.tabs.currentIndex() == 2:
                rgba = self._render(380, 380, True)
                h, w = rgba.shape[:2]
            else:
                rgba = self._render(w, h, True)
        except Exception as ex:  # noqa: BLE001
            self.prev.setText(f"No se pudo: {ex}")
            return
        bg = checker(w, h) if self.tabs.currentIndex() != 0 else np.zeros((h, w, 3), np.float32)
        if self.tabs.currentIndex() == 1:
            bg = np.dstack([np.linspace(0.18, 0.42, h)[:, None].repeat(w, 1)] * 3).astype(np.float32)
        img = FX.over(bg, rgba, FX.PARTICLE_BLEND.get(self.pc.p.get("kind"), "normal")
                      if self.tabs.currentIndex() == 1 else "normal")
        self.prev.setPixmap(QPixmap.fromImage(to_qimage(img)))

    def _ok(self):
        from PySide6.QtWidgets import QApplication
        W, H = self.size_
        k = min(1.0, 4000 / max(W, H, 1))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rgba = self._render(max(8, int(W * k)), max(8, int(H * k)))
        finally:
            QApplication.restoreOverrideCursor()
        self.kind = ("texture", "particles", "effect")[self.tabs.currentIndex()]
        self.label = {0: self.tex.currentText(), 1: self.pc.kind.currentText(), 2: self.fx.currentText()}[
            self.tabs.currentIndex()]
        self.blend = FX.PARTICLE_BLEND.get(self.pc.p.get("kind"), "normal") if self.kind == "particles" else "normal"
        self.result = (np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8)
        self.accept()


# ------------------------------------------------------------------ retícula y guías

class GridDialog(QDialog):
    """Retícula como GuideGuide. Trabaja en la unidad del diseño (`to_u` / `from_u` convierten desde y a px)."""

    PRESETS = {"Sin retícula": dict(cols=0, rows=0, gutter=0, margin=0, mid=False),
               "Tercios (3 × 3)": dict(cols=3, rows=3, gutter=0, margin=0, mid=False),
               "Centro": dict(cols=0, rows=0, gutter=0, margin=0, mid=True),
               "12 columnas (web)": dict(cols=12, rows=0, gutter=0.015, margin=0.05, mid=False),
               "6 columnas con márgenes": dict(cols=6, rows=0, gutter=0.02, margin=0.06, mid=False),
               "Revista 2 columnas": dict(cols=2, rows=0, gutter=0.03, margin=0.08, mid=False),
               "Cuadrícula 4 × 4": dict(cols=4, rows=4, gutter=0.02, margin=0.04, mid=False)}

    def __init__(self, parent, grid: dict | None, page: tuple[float, float], to_u, from_u, unit: str):
        super().__init__(parent)
        self.setWindowTitle("Retícula y guías")
        self.page, self.to_u, self.from_u = page, to_u, from_u
        g = dict(grid or {})
        f = QFormLayout(self)
        self.preset = QComboBox()
        self.preset.addItem("Elegir una plantilla…")
        self.preset.addItems(list(self.PRESETS))
        self.preset.currentTextChanged.connect(self._preset)
        f.addRow("Plantilla", self.preset)
        self.cols, self.rows = QSpinBox(), QSpinBox()
        for sb, key in ((self.cols, "cols"), (self.rows, "rows")):
            sb.setRange(0, 48)
            sb.setValue(int(g.get(key, 0)))
        f.addRow("Columnas", self.cols)
        f.addRow("Filas", self.rows)
        suf = f" {unit}"
        self.gut = self._dspin(to_u(g.get("gutter", 0.0)), suf)
        f.addRow("Medianil", self.gut)
        m = (list(g.get("margin") or [0, 0, 0, 0]) + [0, 0, 0, 0])[:4]
        self.marg = [self._dspin(to_u(v), suf) for v in m]
        mr = QGridLayout()
        for i, (lab, sb) in enumerate(zip(("Arriba", "Derecha", "Abajo", "Izquierda"), self.marg)):
            mr.addWidget(QLabel(lab), 0, i)
            mr.addWidget(sb, 1, i)
        f.addRow("Márgenes", mr)
        self.mid = QCheckBox("Guías en el centro de la página")
        self.mid.setChecked(bool(g.get("mid")))
        f.addRow(self.mid)
        self.vx = QLineEdit(", ".join(f"{to_u(v):g}" for v in g.get("vx", [])))
        self.hy = QLineEdit(", ".join(f"{to_u(v):g}" for v in g.get("hy", [])))
        self.vx.setPlaceholderText("p. ej. 20, 105, 190")
        self.hy.setPlaceholderText("p. ej. 30, 150")
        f.addRow(f"Guías verticales ({unit})", self.vx)
        f.addRow(f"Guías horizontales ({unit})", self.hy)
        self.show_cb = QCheckBox("Mostrar las guías (el imán las usa siempre que estén)")
        self.show_cb.setChecked(g.get("show", True))
        f.addRow(self.show_cb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
        bb.button(QDialogButtonBox.Reset).setText("Quitar todo")
        bb.button(QDialogButtonBox.Reset).clicked.connect(self._clear)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.cleared = False

    @staticmethod
    def _dspin(v, suf):
        sb = QDoubleSpinBox()
        sb.setRange(0, 100000)
        sb.setDecimals(1)
        sb.setValue(float(v))
        sb.setSuffix(suf)
        return sb

    def _preset(self, name):
        p = self.PRESETS.get(name)
        if not p:
            return
        side = min(self.page)
        self.cols.setValue(p["cols"])
        self.rows.setValue(p["rows"])
        self.gut.setValue(self.to_u(p["gutter"] * side))
        for sb in self.marg:
            sb.setValue(self.to_u(p["margin"] * side))
        self.mid.setChecked(p["mid"])

    def _clear(self):
        self.cleared = True
        self.accept()

    @staticmethod
    def _nums(text):
        out = []
        for t in text.replace(";", ",").split(","):
            t = t.strip().replace(" ", "")
            if t:
                try:
                    out.append(float(t.replace(",", ".")))
                except ValueError:
                    pass
        return out

    def grid(self) -> dict | None:
        if self.cleared:
            return None
        import graphics_engine as G
        return G.new_grid(show=self.show_cb.isChecked(), cols=self.cols.value(), rows=self.rows.value(),
                          gutter=self.from_u(self.gut.value()), margin=[self.from_u(sb.value()) for sb in self.marg],
                          vx=[self.from_u(v) for v in self._nums(self.vx.text())],
                          hy=[self.from_u(v) for v in self._nums(self.hy.text())], mid=self.mid.isChecked())


# ------------------------------------------------------------------ doble exposición

class DoubleExposureDialog(QDialog):
    """Elige la segunda foto (paisaje, ciudad, bosque…) y cómo se mezcla con el retrato."""

    def __init__(self, parent, folder: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Doble exposición")
        self.path = ""
        f = QFormLayout(self)
        intro = QLabel("El retrato se rellena con la segunda foto y el fondo se aclara (como el efecto clásico de "
                       "doble exposición). Se crean capas normales que puedes retocar luego: la foto encima, en modo "
                       "Trama y recortada a la silueta de la persona.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        f.addRow(intro)
        row = QHBoxLayout()
        self.lab = QLabel("(ninguna)")
        b = QPushButton("Elegir foto…")
        b.clicked.connect(lambda: self._pick(folder))
        row.addWidget(self.lab, 1)
        row.addWidget(b)
        f.addRow("Segunda foto", row)
        self.mode = QComboBox()
        for k, t in (("screen", "Trama (clásico, luminoso)"), ("lighten", "Aclarar"), ("multiply", "Multiplicar"),
                     ("overlay", "Superponer")):
            self.mode.addItem(t, k)
        f.addRow("Mezcla", self.mode)
        self.bw = QCheckBox("Retrato en blanco y negro")
        self.bw.setChecked(True)
        f.addRow(self.bw)
        self.bg = QCheckBox("Fondo blanco (aclara todo lo que no es la persona)")
        self.bg.setChecked(True)
        f.addRow(self.bg)
        self.amount = _slider(0, 100, 90)
        f.addRow("Intensidad", self.amount)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Crear")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _pick(self, folder):
        import engine as E
        p, _ = QFileDialog.getOpenFileName(self, "Segunda foto", folder, E.file_filter(label="Imágenes"))
        if p:
            self.path = p
            self.lab.setText(os.path.basename(p))

    def _ok(self):
        if not self.path:
            QMessageBox.information(self, "Doble exposición", "Elige primero la segunda foto.")
            return
        self.accept()


def ensure_enhance_models(parent, opts_list) -> bool:
    """«Mejorar con IA»: descarga (preguntando antes) los modelos que pidan estas opciones. True si están."""
    import threading
    up = any(o.get("detail") for o in opts_list) and not FX.upscaler_ready()
    fc = any(o.get("faces") for o in opts_list) and not FX.face_ready()
    if not (up or fc):
        return True
    what = []
    if up:
        what.append(f"• Real-ESRGAN para enfocar y limpiar (≈{FX.UPSCALE_MB} MB, licencia BSD-3)")
    if fc:
        what.append(f"• GFPGAN 1.4 para las caras (≈{FX.FACE_MB} MB; código Apache-2.0, entrenado con FFHQ, "
                    "de uso no comercial: ver docs/enfocar_ia.md)")
    if QMessageBox.question(parent, "Mejorar con IA", "Hay que descargar una vez:\n\n" + "\n".join(what) +
                            "\n\nSe guardan en la carpeta de modelos de PhootFlow. ¿Descargar ahora?") \
            != QMessageBox.Yes:
        return False
    from PySide6.QtWidgets import QApplication, QProgressDialog
    dlg = QProgressDialog("Descargando los modelos de IA…", "Cancelar", 0, 1000, parent)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    stt = {"done": 0, "total": 1, "err": None, "end": False, "cancel": False}

    def prog(done, total):
        stt["done"], stt["total"] = done, max(1, total)

    def job():
        try:
            if up:
                FX.ensure_upscaler(prog)
            if fc:
                FX.ensure_face_model(prog, cancel=lambda: stt["cancel"])
        except Exception as e:
            stt["err"] = e
        finally:
            stt["end"] = True

    threading.Thread(target=job, daemon=True).start()
    while not stt["end"]:
        QApplication.processEvents()
        dlg.setValue(int(1000 * stt["done"] / stt["total"]))
        if dlg.wasCanceled():
            stt["cancel"] = True
        time.sleep(0.05)
    dlg.close()
    if stt["err"] is not None:
        if not stt["cancel"]:
            QMessageBox.warning(parent, "Mejorar con IA", f"No se pudo descargar: {stt['err']}")
        return False
    return True
