"""Panel «Personas» de Events: caras de cada persona de la sesión, filtro de la rejilla, unir grupos, sacar caras,
poner nombre y exportar solo sus fotos (o una carpeta por persona). La lógica está en `people.py`."""
from __future__ import annotations

import os

import cv2
import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame,
                               QHBoxLayout, QInputDialog, QLabel, QListView, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

import engine as E
import icons as I
import people as P
import ui as UI

FACE = 52          # lado de la cara de muestra en el panel
FACE_BIG = 96      # … y en la ventana de caras


def _preview_bgr(ph) -> np.ndarray | None:
    if ph.preview_path and os.path.exists(ph.preview_path):
        return cv2.imread(ph.preview_path, cv2.IMREAD_COLOR)
    if ph.preview:
        return cv2.imdecode(np.frombuffer(ph.preview, np.uint8), cv2.IMREAD_COLOR)
    if ph.thumb:
        return cv2.imdecode(np.frombuffer(ph.thumb, np.uint8), cv2.IMREAD_COLOR)
    return None


def face_pixmap(img: np.ndarray, box, size: int, round_: bool = True) -> QPixmap:
    """Recorte cuadrado de la cara (con aire) en `size` px, redondo si `round_`."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box[0] * w, box[1] * h, box[2] * w, box[3] * h
    cx, cy, side = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) * 1.45
    a, b = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    c, d = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
    crop = img[b:d, a:c] if d > b and c > a else img
    crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    rgb = np.ascontiguousarray(crop[..., ::-1])
    qi = QImage(rgb.data, size, size, 3 * size, QImage.Format_RGB888).copy()
    pm = QPixmap.fromImage(qi)
    if not round_:
        return pm
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


class FaceCache:
    """Caras recortadas de las vistas previas (una vista previa se lee una vez por tanda)."""

    def __init__(self, photos):
        self.by_key = {os.path.basename(p.path): p for p in photos}
        self.pm: dict[tuple, QPixmap] = {}
        self._img: tuple[str, np.ndarray | None] = ("", None)

    def get(self, people: P.People, key: str, size: int, round_: bool = True) -> QPixmap | None:
        k = (key, size, round_)
        if k in self.pm:
            return self.pm[k]
        pk = P.photo_of(key)
        ph = self.by_key.get(pk)
        if ph is None or key not in people.faces:
            return None
        if self._img[0] != pk:
            self._img = (pk, _preview_bgr(ph))
        img = self._img[1]
        if img is None:
            return None
        pm = self.pm[k] = face_pixmap(img, people.faces[key]["box"], size, round_)
        return pm


class PeopleBar(QFrame):
    """Fila «Personas» bajo los filtros: una ficha por persona (clic = filtrar; clic derecho = nombre, caras,
    unir, exportar). `changed` se emite al cambiar el filtro."""
    changed = Signal()
    export_requested = Signal(list, bool, bool, bool)  # personas, una carpeta por persona, solo aprobadas, juntas
    backfill_requested = Signal()

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("peopleBar")
        self.people: P.People | None = None
        self.known: P.Known | None = None  # personas con nombre de otras sesiones (este equipo)
        self.selected: list[str] = []
        self._index: dict[str, set] = {}
        self._cache: FaceCache | None = None
        self._btn: dict[str, QToolButton] = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 20, 0)
        v.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = QLabel("Personas")
        self.title.setObjectName("muted")
        self.b_fold = UI.icon_button("eye", "Mostrar u ocultar las caras", checkable=True, size=16)
        self.b_fold.setChecked(True)
        self.b_fold.toggled.connect(lambda on: self.scroll.setVisible(on))
        self.c_together = UI.chip("Juntas", tip="Con varias personas elegidas: solo las fotos en que salen todas "
                                                 "(apagado: las fotos de cualquiera de ellas)")
        self.c_together.toggled.connect(lambda _: self._refilter())
        self.b_clear = QPushButton("Quitar filtro")
        self.b_clear.setObjectName("ghost")
        self.b_clear.clicked.connect(self.clear)
        self.b_export = QPushButton("  Exportar sus fotos…")
        self.b_export.setObjectName("ghost")
        self.b_export.setIcon(I.icon("export", 16))
        self.b_export.clicked.connect(self.ask_export)
        self.b_more = UI.icon_button("menu", "Más: unir las elegidas, reagrupar, buscar caras", size=16)
        self.b_more.clicked.connect(self._more_menu)
        self.info = QLabel()
        self.info.setObjectName("muted")
        head.addWidget(self.b_fold)
        head.addWidget(self.title)
        head.addWidget(self.info, 1)
        for w_ in (self.c_together, self.b_clear, self.b_export, self.b_more):
            head.addWidget(w_)
        v.addLayout(head)
        self.row = QWidget()
        self.row_l = QHBoxLayout(self.row)
        self.row_l.setContentsMargins(0, 0, 0, 0)
        self.row_l.setSpacing(4)
        self.row_l.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.row)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(FACE + 46)
        v.addWidget(self.scroll)
        self.hide()

    # ---------------------------------------------------------- datos
    def work_dir(self) -> str:
        return os.path.join(self.main.out_dir(), ".phootflow")

    def rebuild(self, regroup: bool = False):
        """Agrupa de nuevo con lo guardado en personas.json (lo hecho a mano se respeta)."""
        photos = [p for p in self.main.photos if p.thumb and not p.error]
        with_faces = [p for p in photos if p.people]
        if not with_faces:
            self.people = None
            self.selected = []
            self._index = {}
            self.hide()
            return
        try:
            self.known = P.Known()
        except Exception:
            self.known = None
        self.people = P.People().build(photos, P.load(self.work_dir()), regroup=regroup, library=self.known)
        self._save()
        self._cache = FaceCache(photos)
        self.selected = [p for p in self.selected if p in self.people.assign.values()]
        self.render()
        self.show()
        self._refilter()

    def _save(self):
        if self.people is not None:
            try:
                self.people.save(self.work_dir())
                if self.known is not None and self.known.sync(P.session_key(self.main.out_dir()), self.people):
                    self.known.save()  # lo nombrado aquí se reconocerá en las próximas sesiones
            except OSError as e:
                self.main.status.setText(f"No se pudo guardar personas.json: {e}")
        self._index = self.people.index() if self.people else {}

    def match(self, ph) -> bool:
        if not self.selected or self.people is None:
            return True
        have = self._index.get(os.path.basename(ph.path), set())
        sel = set(self.selected)
        return sel <= have if self.c_together.isChecked() else bool(have & sel)

    def clear(self):
        self.selected = []
        for b in self._btn.values():
            b.setChecked(False)
        self._refilter()

    def _refilter(self):
        n = len(self.selected)
        self.b_clear.setVisible(bool(n))
        self.c_together.setVisible(n > 1)
        self.b_export.setEnabled(bool(n))
        if self.people is not None:
            shown = len(self.people.groups())
            missing = sum(1 for p in self.main.photos if p.thumb and not p.error and not p.people)
            txt = f"{shown} en la sesión"
            auto = len(self.people.auto)
            if auto:
                txt += f" · {auto} reconocidas de otras sesiones (clic derecho para confirmar)"
            if n:
                k = sum(1 for p in self.main.photos if p.thumb and self.match(p))
                txt += f" · {k} fotos con " + (" y ".join if self.c_together.isChecked() else " o ".join)(
                    self.people.name(pid) for pid in self.selected)
            if missing:
                txt += f" · {missing} fotos sin mirar (menú ⋯)"
            self.info.setText(txt)
        self.changed.emit()

    # ---------------------------------------------------------- fichas
    def render(self):
        while self.row_l.count() > 1:
            it = self.row_l.takeAt(0)
            if it.widget():
                it.widget().hide()  # deleteLater tarda: sin esto la ficha vieja se pinta bajo la nueva
                it.widget().setParent(None)
                it.widget().deleteLater()
        self._btn = {}
        if self.people is None:
            return
        for pid in self.people.groups():
            n = len(self.people.photos_of(pid))
            b = QToolButton()
            b.setObjectName("personChip")
            b.setCheckable(True)
            b.setChecked(pid in self.selected)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(FACE, FACE))
            b.setCursor(Qt.PointingHandCursor)
            name = self.people.name(pid)
            auto = pid in self.people.auto
            short = name if len(name) <= 12 else name[:11] + "…"
            b.setText(f"{short + ' ?' if auto else short}\n{n} fotos")
            b.setToolTip(f"{name} · {n} fotos" + ("\nReconocida de otra sesión: confírmalo con clic derecho"
                                                   if auto else "") +
                         "\nClic: ver solo sus fotos · clic derecho: nombre, caras, unir…")
            pm = self._cache.get(self.people, self.people.sample(pid), FACE) if self._cache else None
            if pm is not None:
                b.setIcon(QIcon(pm))
            b.toggled.connect(lambda on, p=pid: self._toggle(p, on))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _, p=pid, w=b: self._menu(p, w))
            self.row_l.insertWidget(self.row_l.count() - 1, b)
            self._btn[pid] = b

    def _toggle(self, pid: str, on: bool):
        if on and pid not in self.selected:
            self.selected.append(pid)
        elif not on and pid in self.selected:
            self.selected.remove(pid)
        self._refilter()

    def _menu(self, pid: str, w: QWidget):
        m = QMenu(self)
        if pid in self.people.auto:
            nm = self.people.names.get(pid, "")
            m.addAction(f"Sí, es {nm}", lambda: self._edit(lambda: self.people.accept(pid)))
            m.addAction(f"No es {nm}", lambda: self._edit(lambda: self.people.reject(pid)))
            m.addSeparator()
        m.addAction("Poner nombre…", lambda: self.rename(pid))
        m.addAction("Ver sus caras (sacar las que no son)…", lambda: self.show_faces(pid))
        sub = m.addMenu("Unir con")
        for other in self.people.groups():
            if other != pid:
                sub.addAction(self.people.name(other), lambda o=other: self.merge(pid, o))
        sub.setEnabled(len(self.people.groups()) > 1)
        m.addSeparator()
        m.addAction("Exportar sus fotos…", lambda: self.ask_export([pid]))
        m.exec(w.mapToGlobal(w.rect().bottomLeft()))

    def _more_menu(self):
        m = QMenu(self)
        a = m.addAction("Unir las personas elegidas", self.merge_selected)
        a.setEnabled(len(self.selected) > 1)
        m.addAction("Reagrupar (se conservan las personas con nombre)", lambda: self.rebuild(regroup=True))
        a = m.addAction("Olvidar a una persona conocida…", self.forget)
        a.setEnabled(bool(self.known and self.known.names()))
        missing = sum(1 for p in self.main.photos if p.thumb and not p.error and not p.people)
        a = m.addAction(f"Buscar caras en las fotos sin mirar ({missing})", self.backfill_requested.emit)
        a.setEnabled(bool(missing) and E.FACES["ready"])
        m.exec(self.b_more.mapToGlobal(self.b_more.rect().bottomLeft()))

    # ---------------------------------------------------------- cambios a mano
    def _edit(self, fn):
        fn()
        self._save()
        self.render()
        self._refilter()

    def forget(self):
        """Quitar a alguien de las personas conocidas (no se reconocerá más en otras sesiones)."""
        names = self.known.names() if self.known else []
        if not names:
            return
        name, ok = QInputDialog.getItem(self, "Olvidar", "Persona que no se volverá a reconocer en otras "
                                                         "sesiones:", names, 0, False)
        if ok and name:
            self.known.forget(name)
            for pid in [p for p in self.people.auto if self.people.names.get(p) == name]:
                self.people.reject(pid)
            self.known.save()
            self._edit(lambda: None)

    def rename(self, pid: str):
        cur = self.people.names.get(pid, "")
        known = self.known.names() if self.known else []
        if known:  # nombres de otras sesiones a mano (y se puede escribir uno nuevo)
            items = ([cur] if cur and cur not in known else []) + known
            name, ok = QInputDialog.getItem(self, "Nombre", "Nombre de la persona (vacío = sin nombre):", items,
                                            items.index(cur) if cur in items else 0, True)
        else:
            name, ok = QInputDialog.getText(self, "Nombre", "Nombre de la persona (vacío = sin nombre):",
                                            text=cur)
        if not ok:
            return
        name = name.strip()
        same = [p for p, n in self.people.names.items() if p != pid and name and n.lower() == name.lower()]
        if same:  # mismo nombre que otra: es la misma persona
            self.people.merge(same[0], pid)
            self.selected = [same[0] if p == pid else p for p in self.selected]
        else:
            self.people.rename(pid, name)
        self._save()
        self.render()
        self._refilter()

    def merge(self, keep: str, other: str):
        self.people.merge(keep, other)
        self.selected = list(dict.fromkeys(keep if p == other else p for p in self.selected))
        self._save()
        self.render()
        self._refilter()

    def merge_selected(self):
        if len(self.selected) < 2:
            return
        keep = next((p for p in self.selected if p in self.people.names), self.selected[0])
        for o in list(self.selected):
            if o != keep:
                self.people.merge(keep, o)
        self.selected = [keep]
        self._save()
        self.render()
        self._refilter()

    def show_faces(self, pid: str):
        dlg = FacesDialog(self, pid)
        dlg.exec()
        if dlg.dirty:
            self._save()
            self.render()
            self._refilter()

    def ask_export(self, pids: list | None = None):
        pids = list(pids or self.selected)
        if not pids or self.people is None:
            return
        dlg = ExportPeopleDialog(self, pids)
        if dlg.exec() == QDialog.Accepted:
            self.export_requested.emit(dlg.chosen(), dlg.c_folders.isChecked(), dlg.c_keep.isChecked(),
                                       dlg.c_together.isChecked())

    def plan(self, pids: list, only_keep: bool, together: bool) -> tuple[list, dict]:
        """Fotos de esas personas (en el orden de la sesión) y carpeta de cada persona: nombre → nombres de foto."""
        idx = self._index
        photos, folders = [], {}
        sel = set(pids)
        for ph in self.main.photos:
            if not ph.thumb or ph.error or (only_keep and not ph.keep):
                continue
            have = idx.get(os.path.basename(ph.path), set()) & sel
            if not have or (together and have != sel):
                continue
            photos.append(ph)
            for pid in have:
                folders.setdefault(self.people.name(pid), []).append(ph.name)
        return photos, folders


class FacesDialog(QDialog):
    """Caras de una persona: elegir las que no son y sacarlas del grupo o pasarlas a otra persona."""

    def __init__(self, bar: PeopleBar, pid: str):
        super().__init__(bar)
        self.bar, self.pid, self.dirty = bar, pid, False
        ppl = bar.people
        self.setWindowTitle(f"Caras de {ppl.name(pid)}")
        self.resize(760, 560)
        v = QVBoxLayout(self)
        tip = QLabel("Elige las caras que no son de esta persona (Ctrl o Mayús para varias).")
        tip.setObjectName("muted")
        v.addWidget(tip)
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(FACE_BIG, FACE_BIG))
        self.list.setGridSize(QSize(FACE_BIG + 14, FACE_BIG + 30))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.b_out = QPushButton("Sacar del grupo")
        self.b_out.clicked.connect(self.take_out)
        self.to = QComboBox()
        self.to.addItem("Pasar a…", "")
        for o in ppl.groups(1):
            if o != pid:
                self.to.addItem(ppl.name(o), o)
        self.to.activated.connect(self.move_to)
        row.addWidget(self.b_out)
        row.addWidget(self.to)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Cerrar")
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        v.addLayout(row)
        self._todo = ppl.members(pid)
        for k in self._todo:
            it = QListWidgetItem(P.photo_of(k).rsplit(".", 1)[0])
            it.setData(Qt.UserRole, k)
            it.setToolTip(P.photo_of(k))
            self.list.addItem(it)
        self._next = 0
        QTimer.singleShot(0, self._load_some)

    def _load_some(self):
        """Las caras se recortan poco a poco para que la ventana se abra al momento con cientos."""
        end = min(self._next + 24, self.list.count())
        for i in range(self._next, end):
            it = self.list.item(i)
            if it is None:
                continue
            pm = self.bar._cache.get(self.bar.people, it.data(Qt.UserRole), FACE_BIG, round_=False)
            if pm is not None:
                it.setIcon(QIcon(pm))
        self._next = end
        if end < self.list.count():
            QTimer.singleShot(0, self._load_some)

    def _picked(self) -> list[str]:
        return [it.data(Qt.UserRole) for it in self.list.selectedItems()]

    def _drop(self, keys):
        for it in list(self.list.selectedItems()):
            self.list.takeItem(self.list.row(it))
        self.dirty = self.dirty or bool(keys)

    def take_out(self):
        keys = self._picked()
        for k in keys:
            self.bar.people.remove_face(k)
        self._drop(keys)

    def move_to(self, i):
        pid = self.to.itemData(i)
        self.to.setCurrentIndex(0)
        keys = self._picked()
        if not pid or not keys:
            return
        for k in keys:
            self.bar.people.move_face(k, pid)
        self._drop(keys)


class ExportPeopleDialog(QDialog):
    """Exportar solo las fotos de unas personas; con «una carpeta por persona», además `Personas/<nombre>/`."""

    def __init__(self, bar: PeopleBar, pids: list):
        super().__init__(bar)
        self.setWindowTitle("Exportar las fotos de unas personas")
        self.bar = bar
        ppl = bar.people
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Personas:"))
        self.list = QListWidget()
        for pid in ppl.groups():
            it = QListWidgetItem(f"{ppl.name(pid)}  ·  {len(ppl.photos_of(pid))} fotos")
            it.setData(Qt.UserRole, pid)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if pid in pids else Qt.Unchecked)
            pm = bar._cache.get(ppl, ppl.sample(pid), 32) if bar._cache else None
            if pm is not None:
                it.setIcon(QIcon(pm))
            self.list.addItem(it)
        self.list.setIconSize(QSize(32, 32))
        self.list.itemChanged.connect(lambda _: self._count())
        v.addWidget(self.list, 1)
        self.c_folders = QCheckBox("Una carpeta por persona  (Personas/<nombre>/, copia del JPG de entrega)")
        self.c_folders.setChecked(True)
        self.c_keep = QCheckBox("Solo las fotos aprobadas")
        self.c_keep.setChecked(True)
        self.c_together = QCheckBox("Solo las fotos en que salen todas juntas")
        self.c_together.setChecked(bar.c_together.isChecked() and len(pids) > 1)
        for c in (self.c_folders, self.c_keep, self.c_together):
            c.toggled.connect(lambda _: self._count())
            v.addWidget(c)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        v.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Exportar")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.ok = bb.button(QDialogButtonBox.Ok)
        v.addWidget(bb)
        self.resize(460, 480)
        self._count()

    def chosen(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _count(self):
        pids = self.chosen()
        photos, _ = self.bar.plan(pids, self.c_keep.isChecked(), self.c_together.isChecked())
        self.ok.setEnabled(bool(photos))
        self.note.setText(f"{len(photos)} fotos. Las que ya estén exportadas no se revelan otra vez: "
                          "se copia su JPG de «Entrega».")
