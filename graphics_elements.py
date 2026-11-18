"""Graphics: pestaña «Elementos» (como la de Canva): gráficos, stickers, fotos y fuentes de internet.

La red va en hilos (`ThreadPoolExecutor`); los resultados llegan por señales al hilo principal. Cada búsqueda
lleva un número de generación: lo que llega de una búsqueda anterior se descarta. Lo descargado se guarda en
`<cfg>/online/` y vale sin conexión (vista «Recientes»). Ver graphics_online.py para fuentes y licencias.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QByteArray, QFileSystemWatcher, QMimeData, QPointF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontDatabase, QFontMetricsF, QGlyphRun, QIcon, QImage,
                           QPainter, QPixmap, QRawFont)
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget)

import graphics_engine as G
import graphics_online as O
import ui as PUI

MIME = "application/x-phootflow-online"
MODES = [("icons", "Gráficos"), ("stickers", "Stickers"), ("photos", "Fotos"), ("fonts", "Fuentes"),
         ("recent", "Recientes")]
THUMB_COLOR = "#E6E8EF"  # los iconos de un color se ven claros sobre el panel oscuro


_registered: dict[str, list[str]] = {}   # archivo → familias (para no registrar dos veces)
FONT_CHIPS = ["Destacadas", "Todas", "Instaladas", "Mías", "Serif", "Sans", "Manuscrita", "Display", "Mono"]
SAMPLE_W, SAMPLE_H = 236, 34


def register_fonts(store: O.Store, files: list[str] | None = None) -> list[str]:
    fams = []
    for f in files if files is not None else O.installed_font_files(store):
        key = os.path.normcase(os.path.abspath(f))
        if key not in _registered:
            fid = QFontDatabase.addApplicationFont(f)
            _registered[key] = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        fams += _registered[key]
    return sorted(set(fams))


def _name_pixmap(name: str, font: QFont | None = None, data: bytes | None = None) -> QPixmap:
    """Nombre de la fuente escrito con ella misma (con una QFont instalada o con el TTF de muestra sin instalar)."""
    pm = QPixmap(SAMPLE_W * 2, SAMPLE_H * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QColor("#E6E8EF"))
    if data is not None:
        raw = QRawFont(QByteArray(data), 21)
        if raw.isValid():
            idx = raw.glyphIndexesForString(name)
            adv = raw.advancesForGlyphIndexes(idx)
            pos, x = [], 4.0
            for a in adv:
                pos.append(QPointF(x, 0))
                x += a.x()
            run = QGlyphRun()
            run.setRawFont(raw)
            run.setGlyphIndexes(idx)
            run.setPositions(pos)
            p.drawGlyphRun(QPointF(0, (SAMPLE_H + raw.ascent() - raw.descent()) / 2), run)
    elif font is not None:
        f = QFont(font)
        f.setPixelSize(21)
        p.setFont(f)
        fm = QFontMetricsF(f)
        p.drawText(QPointF(4, (SAMPLE_H + fm.ascent() - fm.descent()) / 2), name)
    p.end()
    return pm


class ResultList(QListWidget):
    """Rejilla de resultados: se arrastra al lienzo (los datos van en JSON con un tipo propio)."""

    def mimeData(self, items):
        m = QMimeData()
        if items:
            m.setData(MIME, json.dumps(items[0].data(Qt.UserRole)).encode("utf-8"))
        return m


class ElementsPanel(QWidget):
    results = Signal(int, object)        # generación, {"items": [...], "more": bool}
    thumb = Signal(int, str, QImage)     # generación, clave, miniatura
    failed = Signal(int, str)
    ready = Signal(object, object)       # (acción, resultado) para terminar en el hilo principal

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.store = O.Store(owner.cfg_dir)
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.gen = 0
        self.mode = "icons"
        self.query = ""
        self.offset = 0          # iconos: cuántos van; fotos: página
        self.more = False
        self.loading = False
        self.items_by_key: dict[str, QListWidgetItem] = {}
        self._started = False
        self.font_cat = "Destacadas"
        self.catalog: list[dict] = []
        self._catalog_loading = False
        self._samples: dict[str, QPixmap] = {}
        self._sample_pending: set[str] = set()
        self._watch_since = time.time()
        self.watcher = QFileSystemWatcher(self)
        dl = O.downloads_dir()
        if os.path.isdir(dl):
            self.watcher.addPath(dl)
        self.dl_timer = QTimer(self)
        self.dl_timer.setSingleShot(True)
        self.dl_timer.setInterval(1500)      # los navegadores escriben .crdownload y luego renombran
        self.dl_timer.timeout.connect(self._check_downloads)
        self.watcher.directoryChanged.connect(lambda _: self.dl_timer.start())
        self.results.connect(self._on_results)
        self.thumb.connect(self._on_thumb)
        self.failed.connect(self._on_failed)
        self.ready.connect(lambda fn, res: fn(res))
        self._build()

    # ---------------------------------------------------------- construcción
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 8, 4, 4)
        v.setSpacing(6)
        self.mode_grp = QButtonGroup(self)
        self.mode_btn = {}
        for k, t in MODES:
            b = PUI.chip(t)
            b.clicked.connect(lambda _, k=k: self.set_mode(k))
            self.mode_grp.addButton(b)
            self.mode_btn[k] = b
        self.mode_btn["icons"].setChecked(True)
        modes = PUI.flow(list(self.mode_btn.values()), 4)
        modes.setStyleSheet("QPushButton#chip { padding: 4px 7px; }")
        v.addWidget(modes)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar: corazón, flores, boda, balón…")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(lambda: self.run_search(self.search.text()))
        row.addWidget(self.search, 1)
        go = QPushButton("Buscar")
        go.setCursor(Qt.PointingHandCursor)
        go.clicked.connect(lambda: self.run_search(self.search.text()))
        row.addWidget(go)
        v.addLayout(row)

        src = QHBoxLayout()
        self.source = QComboBox()
        self.source.addItems(["Openverse (CC, sin clave)", "Pexels (clave gratuita)"])
        self.source.currentIndexChanged.connect(self._source_changed)
        src.addWidget(self.source, 1)
        self.key_btn = QPushButton("Clave…")
        self.key_btn.setToolTip("Clave gratuita de la API de Pexels (pexels.com/api). Se guarda solo en este equipo.")
        self.key_btn.clicked.connect(self.ask_pexels_key)
        src.addWidget(self.key_btn)
        self.src_row = QWidget()
        self.src_row.setLayout(src)
        src.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.src_row)

        fr = QHBoxLayout()
        fr.setContentsMargins(0, 0, 0, 0)
        add = QPushButton("Añadir fuentes…")
        add.setCursor(Qt.PointingHandCursor)
        add.setToolTip("Tus .ttf / .otf o un .zip. Se copian a PhootFlow (el original no se toca) y, si está "
                       "marcado, se instalan también en Windows.")
        add.clicked.connect(self.add_fonts)
        fr.addWidget(add, 1)
        daf = QPushButton("DaFont ↗")
        daf.setCursor(Qt.PointingHandCursor)
        daf.setToolTip("Abre DaFont en el navegador. Pulsa «Descargar» en la fuente que quieras: PhootFlow la ve "
                       "llegar a Descargas y la añade sola (y la instala en Windows).")
        daf.clicked.connect(self.open_dafont)
        fr.addWidget(daf)
        upd = QPushButton("⟳")
        upd.setFixedWidth(30)
        upd.setToolTip("Vuelve a pedir el catálogo (Google Fonts, Fontsource y Fontshare)")
        upd.clicked.connect(lambda: self.load_catalog(refresh=True))
        fr.addWidget(upd)
        fw = QWidget()
        fv = QVBoxLayout(fw)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(2)
        fv.addLayout(fr)
        self.win_install = QCheckBox("Instalar también en Windows (Word, Photoshop…)")
        self.win_install.setChecked(bool(self.store.settings().get("instalar_windows", True)))
        self.win_install.setToolTip("Solo para tu usuario, sin permisos de administrador (como «Instalar» del "
                                    "Explorador). Afecta a las fuentes que añadas desde ahora.")
        self.win_install.toggled.connect(lambda on: self.store.set_setting("instalar_windows", on))
        fv.addWidget(self.win_install)
        self.font_row = fw
        v.addWidget(self.font_row)

        self.chips_area = QScrollArea()
        self.chips_area.setWidgetResizable(True)
        self.chips_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chips_area.setFrameShape(QScrollArea.NoFrame)
        self.chips_area.setMaximumHeight(96)
        v.addWidget(self.chips_area)

        self.info = QLabel("")
        self.info.setObjectName("faint")
        self.info.setWordWrap(True)
        v.addWidget(self.info)

        self.list = ResultList()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setDragEnabled(True)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(lambda it: self.place(it.data(Qt.UserRole)))
        self.list.verticalScrollBar().valueChanged.connect(self._maybe_more)
        self.sample_timer = QTimer(self)
        self.sample_timer.setSingleShot(True)
        self.sample_timer.setInterval(120)
        self.sample_timer.timeout.connect(self._load_visible_samples)
        self.list.verticalScrollBar().valueChanged.connect(lambda _: self.mode == "fonts" and self.sample_timer.start())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._font_menu)
        v.addWidget(self.list, 1)

        self.more_btn = QPushButton("Cargar más")
        self.more_btn.clicked.connect(self.load_more)
        v.addWidget(self.more_btn)
        self.credit = QLabel("Gráficos: Iconify (colecciones MIT, Apache, OFL, CC BY…) · Fotos: Openverse y Pexels · "
                             "Fuentes: Google Fonts, Fontshare y las tuyas. Doble clic o arrastra al lienzo.")
        self.credit.setObjectName("faint")
        self.credit.setWordWrap(True)
        v.addWidget(self.credit)
        self._layout_for_mode()

    def _chips(self, labels: list[str], current: str = ""):
        w = QWidget()
        lay = PUI.FlowLayout(w, 4)
        grp = QButtonGroup(w)
        for t in labels:
            b = PUI.chip(t)
            b.setChecked(t == current)
            b.clicked.connect(lambda _, t=t: self._chip(t))
            grp.addButton(b)
            lay.addWidget(b)
        self.chips_area.setWidget(w)

    def _layout_for_mode(self):
        m = self.mode
        self.src_row.setVisible(m == "photos")
        self.font_row.setVisible(m == "fonts")
        self.search.setVisible(m != "recent")
        self.more_btn.setVisible(False)
        if m == "photos":
            self.list.setIconSize(QSize(146, 110))
            self.list.setGridSize(QSize(152, 116))
            self.list.setViewMode(QListWidget.IconMode)
            self._chips([c for c, _ in O.PHOTO_CATEGORIES])
            self.search.setPlaceholderText("Buscar fotos: boda, playa, textura de papel…")
        elif m == "fonts":
            self.list.setViewMode(QListWidget.ListMode)
            self.list.setIconSize(QSize(SAMPLE_W, SAMPLE_H))
            self.list.setGridSize(QSize())
            self._chips(FONT_CHIPS, self.font_cat)
            self.search.setPlaceholderText("Buscar entre ~2 000 fuentes libres")
        else:
            self.list.setViewMode(QListWidget.IconMode)
            self.list.setIconSize(QSize(64, 64))
            self.list.setGridSize(QSize(76, 76))
            if m == "recent":
                self.chips_area.setWidget(QWidget())
            else:
                labels = ([] if m == "stickers" else list(O.ICON_SETS)) + [c for c, _ in O.CATEGORIES]
                self._chips(labels)
                self.search.setPlaceholderText("Buscar: corazón, flores, boda, balón…" if m == "icons" else
                                               "Stickers a color: tarta, globo, trofeo…")
        self.chips_area.setVisible(m != "recent")

    # ---------------------------------------------------------- navegación
    def showEvent(self, e):
        super().showEvent(e)
        if not self._started:
            self._started = True
            QTimer.singleShot(0, lambda: self._chip("Formas"))

    def set_mode(self, mode):
        if mode == self.mode and self.list.count():
            return
        self.mode = mode
        self.mode_btn[mode].setChecked(True)
        self.search.clear()
        self._layout_for_mode()
        if mode == "fonts":
            self.show_fonts()
        elif mode == "recent":
            self.show_recent()
        elif mode == "photos":
            self._chip(O.PHOTO_CATEGORIES[0][0])
        else:
            self._chip("Corazones" if mode == "stickers" else "Formas")

    def _chip(self, label):
        if self.mode == "fonts":
            self.font_cat = label
            self.show_fonts()
            return
        w = self.chips_area.widget()
        for b in w.findChildren(QPushButton) if w else []:
            b.setChecked(b.text() == label)
        if self.mode in ("icons", "stickers") and label in O.ICON_SETS:
            self._start()
            ids = O.ICON_SETS[label]
            self._add_icon_items(self.gen, ids)
            self.info.setText(label)
            return
        cats = O.PHOTO_CATEGORIES if self.mode == "photos" else O.CATEGORIES
        q = next((en for t, en in cats if t == label), label)
        self.run_search(q, label)

    def _start(self):
        self.gen += 1
        self.list.clear()
        self.items_by_key.clear()
        self.offset = 0 if self.mode != "photos" else 1
        self.more = False
        self.loading = False
        self.more_btn.setVisible(False)
        return self.gen

    def run_search(self, text: str, label: str = ""):
        text = (text or "").strip()
        if self.mode == "fonts":
            self.show_fonts(text)
            return
        if self.mode == "recent":
            return
        if not text:
            return
        self.query = text
        self._start()
        self.info.setText(f"Buscando «{label or text}»…")
        self._fetch()

    def _fetch(self):
        gen, mode, q, off = self.gen, self.mode, self.query, self.offset
        self.loading = True
        src = "pexels" if self.source.currentIndex() == 1 else "openverse"

        def job():
            try:
                if mode == "photos":
                    res = O.search_photos(self.store, q, off, src)
                else:
                    r = O.search_icons(self.store, q, off, 96, stickers=mode == "stickers")
                    res = {"items": r["ids"], "more": r["more"]}
                self.results.emit(gen, res)
            except Exception as ex:
                self.failed.emit(gen, str(ex))
        self.pool.submit(job)

    def load_more(self):
        if self.more and not self.loading and self.mode in ("icons", "stickers", "photos"):
            self._fetch()

    def _maybe_more(self, v):
        sb = self.list.verticalScrollBar()
        if sb.maximum() > 0 and v >= sb.maximum() - 40:
            self.load_more()

    # ---------------------------------------------------------- resultados
    def _on_results(self, gen, res):
        if gen != self.gen:
            return
        self.loading = False
        items = res.get("items", [])
        self.more = bool(res.get("more"))
        if self.mode == "photos":
            self.offset += 1
            self._add_photo_items(gen, items)
        else:
            self.offset += len(items)
            self._add_icon_items(gen, items)
        n = self.list.count()
        self.info.setText(f"{n} resultados" + (" · baja para ver más" if self.more else "") if n else
                          "Sin resultados. Prueba con otra palabra (también en inglés).")
        self.more_btn.setVisible(self.more)

    def _on_failed(self, gen, msg):
        if gen != self.gen:
            return
        self.loading = False
        if "Pexels" in msg:
            self.info.setText("Pon tu clave gratuita de Pexels con «Clave…» o usa Openverse.")
        else:
            self.info.setText("No se pudo conectar. Lo que ya usaste está en «Recientes».\n" + msg[:120])

    def _placeholder(self, w=64, h=64):
        pm = QPixmap(w, h)
        pm.fill(QColor(255, 255, 255, 14))
        return QIcon(pm)

    def _add_icon_items(self, gen, ids):
        ph = self._placeholder()
        for i in ids:
            if i in self.items_by_key:
                continue
            it = QListWidgetItem(ph, "")
            it.setData(Qt.UserRole, {"kind": "icon", "id": i})
            it.setToolTip(i.replace(":", " · "))
            it.setSizeHint(QSize(76, 76))
            self.list.addItem(it)
            self.items_by_key[i] = it

        def job():
            try:
                svgs = O.fetch_icons(self.store, ids)
            except Exception as ex:
                self.failed.emit(gen, str(ex))
                return
            for i in ids:
                if gen != self.gen:
                    return
                s = svgs.get(i)
                if s:
                    self.thumb.emit(gen, i, O.svg_thumb(O.recolor_svg(s, THUMB_COLOR), 64))
        self.pool.submit(job)

    def _add_photo_items(self, gen, items):
        ph = self._placeholder(146, 110)
        for p in items:
            if p["key"] in self.items_by_key:
                continue
            it = QListWidgetItem(ph, "")
            it.setData(Qt.UserRole, dict(p, kind="photo"))
            it.setToolTip(O.photo_credit(p))
            self.list.addItem(it)
            self.items_by_key[p["key"]] = it

            def job(p=p):
                if gen != self.gen:
                    return
                try:
                    img = QImage.fromData(O.thumb_bytes(p["thumb"]))
                except Exception:
                    return
                if not img.isNull():
                    self.thumb.emit(gen, p["key"], img.scaled(QSize(146, 110), Qt.KeepAspectRatio,
                                                              Qt.SmoothTransformation))
            self.pool.submit(job)

    def _on_thumb(self, gen, key, img):
        if gen != self.gen:
            return
        it = self.items_by_key.get(key)
        if it is not None and not img.isNull():
            it.setIcon(QIcon(QPixmap.fromImage(img)))

    # ---------------------------------------------------------- fuentes
    def load_catalog(self, refresh: bool = False):
        """Catálogo completo en segundo plano; mientras llega, la selección fija."""
        if self._catalog_loading:
            return
        self._catalog_loading = True
        if refresh:
            self.info.setText("Actualizando la lista de fuentes…")

        def job():
            try:
                cat = O.font_catalog(self.store, refresh)
            except Exception:
                cat = []
            self.ready.emit(self._catalog_ready, cat)
        self.pool.submit(job)

    def _catalog_ready(self, cat):
        self._catalog_loading = False
        if cat:
            self.catalog = cat
        if self.mode == "fonts":
            self.show_fonts(self.search.text())

    def _font_items(self) -> list[dict]:
        if self.catalog:
            return self.catalog
        return [{"family": f, "id": f.lower().replace(" ", "-"), "cat": c, "type": "google", "weights": [400, 700],
                 "styles": ["normal"]} for f, c in O.FONTS]

    def show_fonts(self, text: str = ""):
        self._start()
        if not self.catalog:
            self.load_catalog()
        q = O._ascii(text)
        n = 0
        cat = self.font_cat
        if cat == "Mías":
            meta = O.font_meta(self.store)
            for path in O.user_font_files(self.store):
                info = meta.get(os.path.basename(path), {})
                lic = info.get("lic", "?")
                for fam in register_fonts(self.store, [path]):
                    if q and q not in O._ascii(fam):
                        continue
                    mark = {"libre": "✓", "personal": "⚠ personal"}.get(lic, "? licencia")
                    it = QListWidgetItem(QIcon(_name_pixmap(fam, QFont(fam))), mark)
                    it.setData(Qt.UserRole, {"kind": "font", "family": fam, "user": path})
                    it.setToolTip(f"{fam}\n{os.path.basename(path)} · de {info.get('src') or 'tus archivos'}\n"
                                  + {"libre": "Licencia libre según su léeme: vale para clientes.",
                                     "personal": "Solo uso personal según su léeme: para un cliente hay que comprar "
                                                 "la licencia al autor."}.get(
                                      lic, "Licencia sin confirmar: revisa el léeme o la página de la fuente.")
                                  + "\nDoble clic: aplicar · clic derecho: licencia o quitar")
                    self.list.addItem(it)
                    n += 1
            self.info.setText(f"{n} fuentes tuyas." if n else
                              "Aún no has añadido fuentes. Usa «Añadir fuentes…» con tus .ttf, .otf o un .zip.")
            return
        featured = {f for f, _ in O.FONTS}
        for item in self._font_items():
            fam = item["family"]
            if q and q not in O._ascii(fam):
                continue
            inst = O.font_installed(self.store, fam)
            if cat == "Instaladas" and not inst:
                continue
            if cat == "Destacadas" and fam not in featured and not q:
                continue
            if cat in ("Serif", "Sans", "Manuscrita", "Display", "Mono") and item["cat"] != cat:
                continue
            pm = self._samples.get(fam)
            if pm is None and inst:
                pm = self._samples[fam] = _name_pixmap(fam, QFont(fam))
            it = QListWidgetItem(QIcon(pm) if pm is not None else self._placeholder(SAMPLE_W, SAMPLE_H),
                                 "✓" if inst else "⤓")
            it.setData(Qt.UserRole, {"kind": "font", "family": fam, "item": item})
            it.setToolTip(f"{fam} · {item['cat']} · {item.get('license') or 'OFL'}\n"
                          + ("Descargada" if inst else "Doble clic: descargar y aplicar al texto seleccionado"))
            self.list.addItem(it)
            self.items_by_key[fam] = it
            n += 1
            if n >= 2500:
                break
        total = len(self._font_items())
        self.info.setText(f"{n} de {total} fuentes libres (Google Fonts, Fontsource y Fontshare: valen para "
                          "clientes). ✓ = descargada." + ("" if self.catalog else " Cargando el catálogo…"))
        self.sample_timer.start()

    def _load_visible_samples(self):
        """Pide la muestra de las fuentes que se ven en la lista (y un poco más abajo)."""
        if self.mode != "fonts" or not self.list.count():
            return
        vp = self.list.viewport().rect()
        first = self.list.indexAt(vp.topLeft()).row()
        last = self.list.indexAt(vp.bottomLeft()).row()
        first = max(0, first)
        last = self.list.count() - 1 if last < 0 else min(self.list.count() - 1, last + 12)
        gen = self.gen
        for r in range(first, last + 1):
            data = self.list.item(r).data(Qt.UserRole) or {}
            item = data.get("item")
            fam = data.get("family")
            if not item or fam in self._samples or fam in self._sample_pending:
                continue
            self._sample_pending.add(fam)

            def job(item=item, fam=fam):
                try:
                    b = O.font_sample(self.store, item)
                except Exception:
                    b = None
                self.ready.emit(lambda res: self._sample_ready(gen, fam, res), b)
            self.pool.submit(job)

    def _sample_ready(self, gen, fam, data):
        self._sample_pending.discard(fam)
        if not data:
            return
        pm = self._samples[fam] = _name_pixmap(fam, data=data)
        it = self.items_by_key.get(fam)
        if gen == self.gen and it is not None:
            it.setIcon(QIcon(pm))

    def add_fonts(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Añadir fuentes", "",
                                                "Fuentes (*.ttf *.otf *.zip);;Todos los archivos (*.*)")
        if not files:
            return
        self._import(files)

    def _import(self, files, src=""):
        """Copia, registra, (si toca) instala en Windows y enseña «Mías». Devuelve las familias añadidas."""
        try:
            copied = O.import_fonts(self.store, files, src)
        except Exception as ex:
            self.info.setText(f"No se pudieron añadir: {ex}")
            return []
        fams = register_fonts(self.store, copied)
        installed = 0
        if self.win_install.isChecked():
            for c in copied:
                try:
                    installed += bool(O.windows_install(c))
                except Exception:
                    pass
        meta = O.font_meta(self.store)
        personal = any(meta.get(os.path.basename(c), {}).get("lic") == "personal" for c in copied)
        self.font_cat = "Mías"
        if self.mode != "fonts":
            self.set_mode("fonts")
        else:
            self._layout_for_mode()
            self.show_fonts()
        if fams:
            msg = ("Añadida(s): " + ", ".join(fams[:5]) + ("…" if len(fams) > 5 else "") + "."
                   + (f" Instalada(s) en Windows: {installed}." if installed else "")
                   + (" ⚠ Su léeme dice «uso personal»: para un cliente compra la licencia." if personal else ""))
        else:
            msg = "No había ninguna fuente válida (.ttf / .otf) en lo elegido."
        self.info.setText(msg)
        return fams

    def open_dafont(self):
        QDesktopServices.openUrl(QUrl("https://www.dafont.com/es/"))
        self.set_mode("fonts")
        self.info.setText("Elige una fuente en DaFont y pulsa «Descargar»: se añadirá aquí sola. Para trabajos de "
                          "clientes busca las marcadas «100% Gratis» o «Dominio público».")

    def _check_downloads(self):
        files = O.new_font_downloads(self.store, self._watch_since - 2)
        if not files:
            return
        for f in files:
            try:
                O.mark_imported(self.store, f)
            except OSError:
                pass
        fams = self._import(files, "Descargas")
        if fams:
            sel = [e for e in self.owner.selected() if e["type"] == "text" and not e.get("locked")]
            for e in sel:
                self.owner.set_prop(e, "font", fams[0])
            if sel:
                self.owner.build_props()
            win = self.window()
            win.raise_()
            win.activateWindow()

    def personal_families(self) -> set[str]:
        """Familias de fuentes propias marcadas «uso personal» (para avisar al exportar)."""
        meta = O.font_meta(self.store)
        out = set()
        for p in O.user_font_files(self.store):
            if meta.get(os.path.basename(p), {}).get("lic") == "personal":
                out.update(register_fonts(self.store, [p]))
        return out

    def _font_menu(self, pos):
        it = self.list.itemAt(pos)
        if self.mode != "fonts" or it is None:
            return
        data = it.data(Qt.UserRole) or {}
        m = QMenu(self)
        m.addAction("Aplicar al texto seleccionado").triggered.connect(lambda: self.place(data))
        if data.get("user"):
            up = data["user"]
            for lic, txt in (("libre", "Marcar: licencia libre (vale para clientes)"),
                             ("personal", "Marcar: solo uso personal")):
                m.addAction(txt).triggered.connect(lambda _=False, lic=lic: (
                    O.set_font_meta(self.store, up, lic=lic), self.show_fonts(self.search.text())))
            m.addSeparator()
            m.addAction("Quitar de PhootFlow").triggered.connect(lambda: self._remove_user_font(up))
        m.exec(self.list.viewport().mapToGlobal(pos))

    def _remove_user_font(self, path):
        _registered.pop(os.path.normcase(os.path.abspath(path)), None)
        O.remove_user_font(self.store, path)
        self.show_fonts(self.search.text())
        self.info.setText("Quitada. Los diseños que la usaban la verán con otra letra hasta que la vuelvas a añadir "
                          "(al reiniciar PhootFlow deja de estar disponible).")

    # ---------------------------------------------------------- recientes
    def show_recent(self):
        gen = self._start()
        rec = self.store.recent()
        icons = [r["id"] for r in rec if r.get("kind") == "icon"]
        photos = [r for r in rec if r.get("kind") == "photo"]
        if icons:
            self._add_icon_items(gen, icons)
        for p in photos:
            path = p.get("path", "")
            if not os.path.exists(path):
                continue
            img = QImage(path)
            it = QListWidgetItem(QIcon(QPixmap.fromImage(img.scaled(QSize(64, 64), Qt.KeepAspectRatio,
                                                                   Qt.SmoothTransformation))), "")
            it.setData(Qt.UserRole, dict(p, kind="photo"))
            it.setToolTip(O.photo_credit(p))
            it.setSizeHint(QSize(76, 76))
            self.list.addItem(it)
        self.info.setText("Lo último que has usado (funciona sin conexión)." if rec else
                          "Aún no has usado elementos de internet.")

    # ---------------------------------------------------------- Pexels
    def _source_changed(self, i):
        if i == 1 and not self.store.settings().get("pexels_key"):
            self.ask_pexels_key()
        if self.mode == "photos" and self.query:
            self.run_search(self.query)

    def ask_pexels_key(self):
        cur = self.store.settings().get("pexels_key", "")
        key, ok = QInputDialog.getText(self, "Clave de Pexels",
                                       "Crea una clave gratis en pexels.com/api y pégala aquí.\n"
                                       "Se guarda solo en este equipo.", QLineEdit.Password, cur)
        if ok:
            self.store.set_setting("pexels_key", key.strip())

    # ---------------------------------------------------------- colocar en el lienzo
    def drop(self, data: dict, at: QPointF):
        self.place(data, at)

    def place(self, data: dict, at: QPointF | None = None):
        kind = data.get("kind")
        if kind == "icon":
            self._place_icon(data["id"], at)
        elif kind == "photo":
            self._place_photo(data, at)
        elif kind == "font":
            if data.get("user"):
                self._finish_font(data["family"], [data["user"]])
            else:
                self._apply_font(data["family"], data.get("item"))

    def _size(self):
        d = self.owner.d
        return min(d["w"], d["h"]) * 0.28

    def _at(self, el, at):
        d = self.owner.d
        if at is None:
            el["x"], el["y"] = (d["w"] - el["w"]) / 2, (d["h"] - el["h"]) / 2
        else:
            el["x"], el["y"] = at.x() - el["w"] / 2, at.y() - el["h"] / 2

    def _place_icon(self, icon_id: str, at):
        def job():
            try:
                svg = O.fetch_icons(self.store, [icon_id]).get(icon_id)
            except Exception:
                svg = None
            self.ready.emit(lambda s: self._finish_icon(icon_id, s, at), svg)
        self.info.setText("Añadiendo…")
        self.pool.submit(job)

    def _finish_icon(self, icon_id, svg, at):
        if not svg:
            self.info.setText("No se pudo descargar ese gráfico.")
            return
        color = "#FFFFFF" if self.owner._dark_bg() else "#05070F"
        credit = O.icon_credit(self.store, icon_id)
        name = icon_id.split(":", 1)[1].replace("-", " ")
        side = self._size()
        try:
            vec = O.svg_to_vector(svg, color)
            vw, vh = vec["viewbox"]
            k = side / max(vw, vh)
            el = G.vector_el(vec, 0, 0, vw * k, vh * k)
        except O.Unsupported:
            png = os.path.join(self.store.icons, "png", icon_id.replace(":", "__") + ".png")
            if not os.path.exists(png):
                O.render_svg_png(O.recolor_svg(svg, color), png, 1200)
            img = QImage(png)
            asp = img.width() / max(1, img.height())
            el = G.image_el(self.owner.d, png, asp, w=side * min(1.0, asp), h=side / max(1.0, asp))
        el["name"] = name
        el["credit"] = credit
        self._at(el, at)
        self.owner._add(el)
        self.store.add_recent({"key": icon_id, "kind": "icon", "id": icon_id})
        lic = O.collection_info(self.store, icon_id.split(":", 1)[0]).get("license", "")
        self.info.setText(f"Añadido: {name}. " + ("Licencia CC BY: si lo publicas, cita «" + credit + "»."
                                                 if O.needs_attribution(lic) else "Uso libre."))

    def _place_photo(self, item, at):
        path = item.get("path")
        if path and os.path.exists(path):
            self._finish_photo(item, path, at)
            return

        def job():
            try:
                p = O.download_photo(self.store, item)
            except Exception as ex:
                p = ex
            self.ready.emit(lambda r: self._finish_photo(item, r, at), p)
        self.info.setText("Descargando la foto…")
        self.pool.submit(job)

    def _finish_photo(self, item, path, at):
        if isinstance(path, Exception) or not path:
            self.info.setText(f"No se pudo descargar la foto: {path}")
            return
        el = self.owner.place_photo(path, at)
        if el is not None:
            el["credit"] = O.photo_credit(item)
            self.owner.commit()
        rec = {k: item.get(k) for k in ("key", "source", "author", "license", "page", "url", "thumb")}
        self.store.add_recent(dict(rec, kind="photo", path=path))
        lic = item.get("license", "")
        self.info.setText("Foto añadida. " + ("Cita al autor si la publicas: " + O.photo_credit(item)
                                              if O.needs_attribution(lic.replace("CC ", "CC-")) else
                                              "Uso libre (licencia " + (lic or "libre") + ")."))

    def _apply_font(self, family, item: dict | None = None):
        if item is None:
            item = next((x for x in self._font_items() if x["family"].lower() == family.lower()), None)

        def job():
            try:
                files = O.download_font_any(self.store, item) if item else O.download_font(self.store, family)
            except Exception as ex:
                files = ex
            self.ready.emit(lambda r: self._finish_font(family, r), files)
        self.info.setText(f"Descargando «{family}»…")
        self.pool.submit(job)

    def _finish_font(self, family, files):
        if isinstance(files, Exception):
            self.info.setText(str(files))
            return
        fams = register_fonts(self.store, files)
        fam = family if family in fams or not fams else fams[0]
        sel = [e for e in self.owner.selected() if e["type"] == "text" and not e.get("locked")]
        for e in sel:
            self.owner.set_prop(e, "font", fam)
        if sel:
            self.owner.build_props()
        self.info.setText(f"«{fam}» lista" + (f" y aplicada a {len(sel)} texto(s)." if sel else
                                              ". Selecciona un texto y elígela en Propiedades › Fuente."))
        if self.mode == "fonts":
            row = self.list.currentRow()
            self.show_fonts(self.search.text())
            self.list.setCurrentRow(row)

    def shutdown(self):
        self.gen += 1
        self.pool.shutdown(wait=False, cancel_futures=True)
