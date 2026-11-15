"""FotoLote — edición por lotes de sesiones de fotos (interfaz de escritorio)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

import drive as gdrive
import engine as E
import updater
from version import __version__

APP = "FotoLote"
CFG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.config"), APP)
CLOUDS = ["No subir", "Google Drive (sube y crea enlace)", "Carpeta sincronizada (Amazon Photos u otra)"]


class Worker(QThread):
    progress = Signal(int, int, str)
    photo = Signal(int)
    done = Signal(str, str)  # resumen, enlace
    failed = Signal(str)

    def __init__(self, mode, photos, st, src, out, cloud=None):
        super().__init__()
        self.mode, self.photos, self.st, self.src, self.out, self.cloud = mode, photos, st, src, out, cloud or {}
        self.cancel = False

    def run(self):
        try:
            self.analyze() if self.mode == "analyze" else self.export()
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc(limit=3)}")

    def analyze(self):
        n = len(self.photos)
        for i, ph in enumerate(self.photos):
            if self.cancel:
                break
            self.progress.emit(i, n, f"Analizando {ph.name}…")
            E.analyze(ph, self.st)
            self.photo.emit(i)
        E.cull(self.photos, self.st)
        keep = sum(p.keep for p in self.photos)
        self.done.emit(f"Análisis terminado: {keep} para editar, {n - keep} propuestas para descartar.", "")

    def export(self):
        keep = [p for p in self.photos if p.keep]
        t0, files = time.time(), 0
        for i, ph in enumerate(keep):
            if self.cancel:
                self.done.emit(f"Cancelado: {i} fotos exportadas.", "")
                return
            left = f" · quedan ~{(time.time() - t0) / i * (len(keep) - i) / 60:.0f} min" if i else ""
            self.progress.emit(i, len(keep), f"Editando {ph.name} ({i + 1}/{len(keep)}){left}")
            try:
                files += len(E.export(ph, self.st, self.out))
            except Exception as e:
                ph.error = f"{type(e).__name__}: {e}"
        E.write_report(self.photos, self.out)
        zip_path = None
        if self.st.archive_originals and keep:
            self.progress.emit(0, 0, "Comprimiendo originales…")
            zip_path = E.archive_originals(
                self.photos, self.out, lambda a, b: self.progress.emit(a, b, f"Comprimiendo originales ({a}/{b})"))
        link = self.upload(zip_path)
        moved = E.move_discards(self.photos, self.src) if self.st.move_discards else 0
        errs = sum(1 for p in keep if p.error)
        msg = f"Listo: {len(keep) - errs} fotos editadas ({files} archivos JPG)."
        if moved:
            msg += f" {moved} originales descartados movidos a «Descartadas»."
        if errs:
            msg += f" {errs} fotos fallaron (ver informe.csv)."
        self.done.emit(msg, link)

    def upload(self, zip_path) -> str:
        kind = self.cloud.get("kind", 0)
        session = os.path.basename(os.path.normpath(self.src))
        tree = [(r, f) for sub in ("Sugeridas", "Formatos") for r, _, fs in os.walk(os.path.join(self.out, sub))
                for f in fs]
        if kind == 2:  # carpeta local que sincroniza otra aplicación
            dest = os.path.join(self.cloud["folder"], session)
            for j, (r, f) in enumerate(tree):
                d = os.path.join(dest, "Editadas", os.path.relpath(r, self.out))
                os.makedirs(d, exist_ok=True)
                shutil.copy2(os.path.join(r, f), os.path.join(d, f))
                self.progress.emit(j + 1, len(tree), "Copiando a la carpeta sincronizada…")
            if zip_path and self.cloud.get("originals"):
                os.makedirs(os.path.join(dest, "Originales"), exist_ok=True)
                shutil.copy2(zip_path, os.path.join(dest, "Originales", os.path.basename(zip_path)))
            return ""
        if kind != 1:
            return ""
        d = gdrive.Drive(CFG_DIR)
        root = d.ensure_folder(session, gdrive.folder_id_from(self.cloud["folder"]))
        edited = d.ensure_folder("Editadas", root)
        cache = {".": edited}
        for j, (r, f) in enumerate(tree):
            rel, parent = os.path.relpath(r, self.out), edited
            acc = ""
            for part in rel.split(os.sep):
                acc = os.path.join(acc, part)
                if acc not in cache:
                    cache[acc] = d.ensure_folder(part, parent)
                parent = cache[acc]
            self.progress.emit(j, len(tree), f"Subiendo a Drive {f} ({j + 1}/{len(tree)})")
            d.upload(os.path.join(r, f), parent)
        if zip_path and self.cloud.get("originals"):
            orig = d.ensure_folder("Originales", root)
            d.upload(zip_path, orig, lambda a, b: self.progress.emit(
                int(a / 2 ** 20), int(b / 2 ** 20), "Subiendo originales comprimidos a Drive (MB)…"))
        return d.share_link(edited)


class Main(QMainWindow):
    update_found = Signal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP} {__version__}")
        self.resize(1180, 820)
        self.s = QSettings(APP, APP)
        self.photos, self.worker = [], None
        self.drive = gdrive.Drive(CFG_DIR)
        w = QWidget()
        self.setCentralWidget(w)
        root = QVBoxLayout(w)

        g = QGridLayout()
        self.src, self.out = QLineEdit(), QLineEdit()
        self.out.setPlaceholderText("Por defecto: subcarpeta «Editadas» dentro de la sesión")
        for row, (label, edit) in enumerate([("Carpeta de la sesión", self.src), ("Carpeta de salida", self.out)]):
            b = QPushButton("Elegir…")
            b.clicked.connect(lambda _, e=edit: self.pick(e))
            g.addWidget(QLabel(label), row, 0)
            g.addWidget(edit, row, 1)
            g.addWidget(b, row, 2)
        root.addLayout(g)

        opts = QHBoxLayout()
        root.addLayout(opts)
        # --- edición
        box = QGroupBox("Edición")
        v = QGridLayout(box)
        self.nr = QComboBox()
        self.nr.addItems(list(E.NR_LEVELS))
        self.nr.setCurrentText("Normal")
        self.evc = QDoubleSpinBox()
        self.evc.setRange(-1.5, 1.5)
        self.evc.setSingleStep(0.1)
        self.evc.setSuffix(" EV")
        self.straight = QCheckBox("Enderezar automáticamente")
        self.straight.setChecked(True)
        v.addWidget(QLabel("Reducción de grano"), 0, 0)
        v.addWidget(self.nr, 0, 1)
        v.addWidget(QLabel("Compensar exposición"), 1, 0)
        v.addWidget(self.evc, 1, 1)
        v.addWidget(self.straight, 2, 0, 1, 2)
        opts.addWidget(box)
        # --- descarte
        box = QGroupBox("Descarte")
        v = QGridLayout(box)
        self.c_focus = QCheckBox("Desenfocadas o movidas")
        self.c_exp = QCheckBox("Exposición irrecuperable")
        self.c_dup = QCheckBox("Casi duplicadas (deja la más nítida)")
        self.c_move = QCheckBox("Al exportar, mover descartes a «Descartadas»")
        for i, c in enumerate([self.c_focus, self.c_exp, self.c_dup, self.c_move]):
            c.setChecked(True)
            v.addWidget(c, i, 0, 1, 2)
        self.sens = QComboBox()
        self.sens.addItems(list(E.SENS))
        self.sens.setCurrentText("Normal")
        v.addWidget(QLabel("Exigencia"), 4, 0)
        v.addWidget(self.sens, 4, 1)
        opts.addWidget(box)
        # --- formatos
        box = QGroupBox("Formatos y tamaños")
        v = QGridLayout(box)
        v.addWidget(QLabel("El formato sugerido se exporta siempre. Además:"), 0, 0, 1, 6)
        self.ratio_cb = {}
        self.r_orig = QCheckBox("Original")
        self.r_orig.setChecked(True)
        v.addWidget(self.r_orig, 1, 0)
        for i, name in enumerate(E.RATIOS, start=1):
            c = QCheckBox(name)
            self.ratio_cb[name] = c
            v.addWidget(c, 1 + i // 6, i % 6)
        self.size_cb = {}
        for i, (k, label) in enumerate([("max", "Máxima resolución"), ("2048", "2048 px lado largo"),
                                        ("1080", "1080 px de ancho")]):
            c = QCheckBox(label)
            c.setChecked(k != "2048")
            self.size_cb[k] = c
            v.addWidget(c, 3, i * 2, 1, 2)
        self.q = QSpinBox()
        self.q.setRange(70, 100)
        self.q.setValue(95)
        v.addWidget(QLabel("Calidad JPG"), 4, 0)
        v.addWidget(self.q, 4, 1)
        opts.addWidget(box, 1)

        # --- originales y nube
        box = QGroupBox("Originales y nube")
        h = QGridLayout(box)
        self.c_zip = QCheckBox("Guardar los originales conservados en un ZIP sin pérdida (Originales.zip)")
        self.c_zip.setChecked(True)
        self.cloud = QComboBox()
        self.cloud.addItems(CLOUDS)
        self.cloud_folder = QLineEdit()
        self.cloud_btn = QPushButton()
        self.cloud_btn.clicked.connect(self.cloud_action)
        self.c_up_orig = QCheckBox("Subir también el ZIP de originales (queda privado)")
        self.c_up_orig.setChecked(True)
        self.cloud.currentIndexChanged.connect(self.cloud_ui)
        h.addWidget(self.c_zip, 0, 0, 1, 4)
        h.addWidget(QLabel("Destino"), 1, 0)
        h.addWidget(self.cloud, 1, 1)
        h.addWidget(self.cloud_folder, 1, 2)
        h.addWidget(self.cloud_btn, 1, 3)
        h.addWidget(self.c_up_orig, 2, 1, 1, 3)
        h.setColumnStretch(2, 1)
        root.addWidget(box)

        bar = QHBoxLayout()
        self.b_an = QPushButton("1. Analizar sesión")
        self.b_ex = QPushButton("2. Editar y exportar")
        self.b_ca = QPushButton("Cancelar")
        self.b_open = QPushButton("Abrir carpeta de salida")
        self.b_an.clicked.connect(self.start_analyze)
        self.b_ex.clicked.connect(self.start_export)
        self.b_ca.clicked.connect(self.do_cancel)
        self.b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_dir())))
        for b in (self.b_an, self.b_ex, self.b_ca, self.b_open):
            bar.addWidget(b)
        bar.addStretch(1)
        root.addLayout(bar)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Editar", "Foto", "Archivo", "Estado", "Formato sugerido",
                                              "Exposición", "Giro"])
        self.table.setIconSize(QSize(110, 110))
        self.table.verticalHeader().setDefaultSectionSize(116)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 124)
        self.table.setColumnWidth(4, 130)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemChanged.connect(self.item_changed)
        root.addWidget(self.table, 1)

        self.pb = QProgressBar()
        self.status = QLabel("Elige la carpeta de la sesión y pulsa «Analizar».")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.pb)
        root.addWidget(self.status)

        self.load()
        self.cloud_ui()
        self.busy(False)
        self.update_found.connect(self.offer_update)
        if updater.can_self_update():
            threading.Thread(target=self.check_update, daemon=True).start()

    # ---------------------------------------------------------- ajustes
    def load(self):
        g = self.s.value
        self.src.setText(g("src", ""))
        self.out.setText(g("out", ""))
        self.nr.setCurrentText(g("nr", "Normal"))
        self.sens.setCurrentText(g("sens", "Normal"))
        ratios = (g("ratios", "4:5") or "").split(",")
        for k, c in self.ratio_cb.items():
            c.setChecked(k in ratios)
        sizes = (g("sizes", "max,1080") or "").split(",")
        for k, c in self.size_cb.items():
            c.setChecked(k in sizes)
        self.cloud.setCurrentIndex(int(g("cloud", 0)))
        self.cloud_folder.setText(g("cloud_folder", ""))

    def save(self):
        st = self.settings()
        for k, v in [("src", self.src.text()), ("out", self.out.text()), ("nr", st.nr), ("sens", st.sensitivity),
                     ("ratios", ",".join(st.ratios)), ("sizes", ",".join(st.sizes)),
                     ("cloud", self.cloud.currentIndex()), ("cloud_folder", self.cloud_folder.text())]:
            self.s.setValue(k, v)

    def settings(self) -> E.Settings:
        return E.Settings(
            nr=self.nr.currentText(), straighten=self.straight.isChecked(), ev_comp=self.evc.value(),
            cull_focus=self.c_focus.isChecked(), cull_exposure=self.c_exp.isChecked(),
            cull_dupes=self.c_dup.isChecked(), sensitivity=self.sens.currentText(),
            ratios=[k for k, c in self.ratio_cb.items() if c.isChecked()],
            export_original_ratio=self.r_orig.isChecked(),
            sizes=[k for k, c in self.size_cb.items() if c.isChecked()] or ["max"],
            quality=self.q.value(), move_discards=self.c_move.isChecked(),
            archive_originals=self.c_zip.isChecked())

    def out_dir(self) -> str:
        return self.out.text().strip() or os.path.join(self.src.text().strip(), "Editadas")

    def pick(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Elegir carpeta", edit.text())
        if d:
            edit.setText(os.path.normpath(d))

    # ------------------------------------------------------------- nube
    def cloud_ui(self):
        k = self.cloud.currentIndex()
        self.cloud_folder.setVisible(k > 0)
        self.cloud_btn.setVisible(k > 0)
        self.c_up_orig.setVisible(k > 0)
        if k == 1:
            self.cloud_folder.setPlaceholderText("Pega aquí el enlace de la carpeta de Google Drive")
            self.cloud_btn.setText("Drive conectado ✓ (desconectar)" if self.drive.connected() else "Conectar Google Drive…")
        elif k == 2:
            self.cloud_folder.setPlaceholderText("Carpeta de tu PC que Amazon Photos (u otra app) sube sola")
            self.cloud_btn.setText("Elegir…")

    def cloud_action(self):
        if self.cloud.currentIndex() == 2:
            return self.pick(self.cloud_folder)
        try:
            if self.drive.connected():
                self.drive.disconnect()
            else:
                if not self.drive.has_client():
                    QMessageBox.information(self, APP, "La primera vez hace falta el archivo de credenciales de "
                                            "Google (client_secret.json, tipo «App de escritorio»). "
                                            "Las instrucciones están en el README del proyecto.")
                    f, _ = QFileDialog.getOpenFileName(self, "client_secret.json", "", "JSON (*.json)")
                    if not f:
                        return
                    self.drive.install_client(f)
                self.status.setText("Autoriza el acceso en el navegador que se acaba de abrir…")
                QApplication.processEvents()
                self.drive.connect()
                self.status.setText("Google Drive conectado.")
        except Exception as e:
            QMessageBox.warning(self, APP, str(e))
        self.cloud_ui()

    # ---------------------------------------------------------- acciones
    def busy(self, on: bool):
        self.b_an.setEnabled(not on)
        self.b_ex.setEnabled(not on and any(p.keep for p in self.photos))
        self.b_ca.setEnabled(on)

    def start_analyze(self):
        src = self.src.text().strip()
        if not os.path.isdir(src):
            return QMessageBox.warning(self, APP, "Elige primero la carpeta de la sesión.")
        self.photos = E.list_photos(src)
        if not self.photos:
            return QMessageBox.information(self, APP, "No hay fotos RAW ni JPG en esa carpeta.")
        self.save()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.photos))
        for i, ph in enumerate(self.photos):
            self.table.setItem(i, 2, QTableWidgetItem(os.path.basename(ph.path)))
        self.table.blockSignals(False)
        self.run(Worker("analyze", self.photos, self.settings(), src, self.out_dir()))

    def start_export(self):
        st = self.settings()
        cloud = {"kind": self.cloud.currentIndex(), "folder": self.cloud_folder.text().strip(),
                 "originals": self.c_up_orig.isChecked()}
        try:
            if cloud["kind"] == 1:
                if not self.drive.connected():
                    raise gdrive.DriveError("Conecta Google Drive antes de exportar, o elige «No subir».")
                self.drive.check_folder(gdrive.folder_id_from(cloud["folder"]))
            if cloud["kind"] == 2 and not os.path.isdir(cloud["folder"]):
                raise gdrive.DriveError("Elige la carpeta sincronizada, o cambia el destino a «No subir».")
        except Exception as e:
            return QMessageBox.warning(self, APP, str(e))
        self.save()
        self.run(Worker("export", self.photos, st, self.src.text().strip(), self.out_dir(), cloud))

    def run(self, worker):
        self.worker = worker
        worker.progress.connect(self.on_progress)
        worker.photo.connect(self.fill_row)
        worker.done.connect(self.on_done)
        worker.failed.connect(lambda m: (self.busy(False), QMessageBox.critical(self, APP, m)))
        self.busy(True)
        worker.start()

    def do_cancel(self):
        if self.worker:
            self.worker.cancel = True
            self.status.setText("Cancelando al terminar la foto en curso…")

    def on_progress(self, i, n, text):
        self.pb.setMaximum(n)
        self.pb.setValue(i)
        self.status.setText(text)

    def fill_row(self, i):
        ph = self.photos[i]
        self.table.blockSignals(True)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk.setCheckState(Qt.Checked if ph.keep else Qt.Unchecked)
        self.table.setItem(i, 0, chk)
        pic = QTableWidgetItem()
        if ph.thumb:
            pm = QPixmap()
            pm.loadFromData(ph.thumb)
            pic.setIcon(QIcon(pm))
        self.table.setItem(i, 1, pic)
        self.table.setItem(i, 3, QTableWidgetItem(ph.error or ph.reason or "Para editar"))
        self.table.setItem(i, 4, QTableWidgetItem(ph.best))
        self.table.setItem(i, 5, QTableWidgetItem(f"{ph.ev:+.1f} EV"))
        self.table.setItem(i, 6, QTableWidgetItem(f"{ph.angle:+.1f}°" if ph.angle else "—"))
        self.table.blockSignals(False)

    def item_changed(self, item):
        if item.column() == 0 and item.row() < len(self.photos):
            ph = self.photos[item.row()]
            if ph.error:
                return
            ph.keep = item.checkState() == Qt.Checked
            if ph.keep:
                ph.reason = ""
            elif not ph.reason:
                ph.reason = "Descartada a mano"
            self.table.blockSignals(True)
            self.table.item(item.row(), 3).setText(ph.reason or "Para editar")
            self.table.blockSignals(False)
            self.b_ex.setEnabled(any(p.keep for p in self.photos))

    def on_done(self, msg, link):
        for i in range(len(self.photos)):
            self.fill_row(i)
        self.pb.setMaximum(1)
        self.pb.setValue(1)
        self.status.setText(msg + (f"  Enlace para compartir: {link}" if link else ""))
        self.busy(False)
        if link:
            QApplication.clipboard().setText(link)
            QMessageBox.information(self, APP, f"{msg}\n\nEnlace para compartir (ya copiado al portapapeles):\n{link}")

    # ----------------------------------------------------- actualización
    def check_update(self):
        info = updater.latest()
        if info and updater.is_newer(info["version"], __version__):
            self.update_found.emit(info)

    def offer_update(self, info):
        if QMessageBox.question(self, APP, f"Hay una versión nueva ({info['version']}; tienes la {__version__}).\n"
                                "¿Actualizar ahora? El programa se reiniciará solo.") != QMessageBox.Yes:
            return
        try:
            self.status.setText("Descargando la actualización…")
            QApplication.processEvents()
            new = updater.download(info)
            updater.swap_and_restart(new)
            QApplication.quit()
        except Exception as e:
            QMessageBox.warning(self, APP, f"No se pudo actualizar: {e}")


def selftest() -> int:
    """Comprobación que ejecuta el compilado en GitHub antes de publicar una versión."""
    log = os.path.join(tempfile.gettempdir(), "fotolote_selftest.log")
    try:
        import cv2
        import numpy as np
        import rawpy
        from PIL import Image
        d = tempfile.mkdtemp(prefix="fotolote_")
        rng = np.random.default_rng(1)
        for k in range(2):
            a = np.full((900, 600, 3), 18, np.uint8)
            cv2.rectangle(a, (150, 250 + 40 * k), (450, 700), (200, 190, 170), -1)
            for x in range(40, 600, 70):
                cv2.line(a, (x, 0), (x + 16, 899), (90, 90, 90), 3)
            a = np.clip(a + rng.normal(0, 4, a.shape), 0, 255).astype(np.uint8)
            Image.fromarray(a).save(os.path.join(d, f"t{k}.jpg"), quality=92)
        st = E.Settings(ratios=["4:5", "16:9"], sizes=["max", "1080"])
        ps = [E.analyze(p, st) for p in E.list_photos(d)]
        assert len(ps) == 2 and not any(p.error for p in ps), [p.error for p in ps]
        assert not cv2.CascadeClassifier(
            os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")).empty()
        E.cull(ps, st)
        for p in ps:
            p.keep = True
        out = os.path.join(d, "Editadas")
        n = sum(len(E.export(p, st, out)) for p in ps)
        assert n >= 8, n
        assert os.path.getsize(E.archive_originals(ps, out)) > 0
        E.write_report(ps, out)
        app = QApplication.instance() or QApplication(sys.argv)
        Main()
        with open(log, "w", encoding="utf-8") as f:
            f.write(f"OK {__version__} libraw {rawpy.libraw_version} archivos {n}\n")
        return 0
    except Exception:
        with open(log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        return 1


def main():
    if "--selftest" in sys.argv:
        code = selftest()
        sys.stdout.flush()
        os._exit(code)
    app = QApplication(sys.argv)
    m = Main()
    m.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
