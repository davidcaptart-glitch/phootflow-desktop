"""Tema visual de PhootFlow.

Negro profundo con un punto azulado (el #05070F del logo) en la interfaz, paneles «de cristal» con bordes de
1 px, chips redondeados que se encienden en magenta y un degradado magenta→violeta en la acción principal.
Lo que hay detrás de la foto es un gris casi negro NEUTRO, para no engañar al juzgar el color.
"""

BG = "#0c0c0e"          # visor / lienzo (detrás de la foto): neutro
CHROME = "#07090f"      # cabecera y fondo de la interfaz (azul noche del logo)
SURFACE = "#0d1018"     # paneles
SURFACE_2 = "#121622"   # campos, bloques
SURFACE_3 = "#1a1f2d"   # botones, hover
BORDER = "#05070f"      # separadores
LINE = "#1f2534"        # líneas finas y bordes de cristal
TEXT = "#d9dce6"
TEXT_STRONG = "#f5f6fa"
MUTED = "#8a90a2"
FAINT = "#555b6e"
ICON = "#aeb3c2"
ACCENT = "#F50D8A"      # magenta PhootFlow
ACCENT_HOVER = "#FF3DA3"
ACCENT_PRESS = "#C90A71"
VIOLET = "#7B2FF7"      # final del degradado de la acción principal
OK = "#4cc38a"
WARN = "#e8b04a"
DANGER = "#e5484d"

# pistas de color de los sliders (como Temperatura/Matiz/HSL en Lightroom y Capture One)
GRADIENTS = {
    "temp": "stop:0 #3b6fd8, stop:0.5 #9a9a9a, stop:1 #e0b232",
    "tint": "stop:0 #3fa64a, stop:0.5 #9a9a9a, stop:1 #c43cc4",
    "saturation": "stop:0 #8a8a8a, stop:1 #e0433c",
    "vibrance": "stop:0 #8a8a8a, stop:1 #e07a3c",
    "exposure": "stop:0 #101010, stop:1 #e8e8e8",
    "hue": "stop:0 #ff0000, stop:0.17 #ffff00, stop:0.33 #00ff00, stop:0.5 #00ffff, stop:0.67 #0000ff, "
           "stop:0.83 #ff00ff, stop:1 #ff0000",
}

QSS = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
    font-size: 9pt;
    color: {TEXT};
}}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QWidget#sidebar, QFrame#sidebar {{ background: {SURFACE}; border: none; }}
QFrame#topbar {{ background: {CHROME}; border-bottom: 1px solid {LINE}; }}
QFrame#previewPane {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
QFrame#toolbar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QFrame#filmstrip {{ background: {CHROME}; border-top: 1px solid {LINE}; }}
QFrame#tooltabs {{ background: {CHROME}; border-left: 1px solid {LINE}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}

/* paneles (Events) como en un editor: planos, separados por una línea */
QFrame#card {{ background: transparent; border: none; border-bottom: 1px solid {LINE}; border-radius: 0; }}
QLabel#cardTitle {{ color: {MUTED}; font-size: 8pt; font-weight: 600; letter-spacing: 1.2px; }}
QLabel {{ background: transparent; }}
QLabel#muted, QLabel#version {{ color: {MUTED}; }}
QLabel#faint {{ color: {FAINT}; }}
QLabel#h1 {{ font-size: 12.5pt; font-weight: 600; color: {TEXT_STRONG}; }}
QLabel#meta {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#chip {{ background: {SURFACE_3}; border-radius: 3px; padding: 2px 8px; color: {MUTED}; font-size: 8.5pt; }}
QLabel#chipAccent {{ background: rgba(245, 13, 138, 0.16); border-radius: 3px; padding: 2px 8px;
                     color: {ACCENT_HOVER}; font-size: 8.5pt; }}
QFrame#banner {{ background: rgba(245, 13, 138, 0.10); border: 1px solid rgba(245, 13, 138, 0.45); border-radius: 4px; }}
QLabel#previewImage {{ background: {BG}; }}

/* módulos (Events · Studio), como el selector de módulos de Lightroom */
QPushButton#module {{ background: transparent; border: none; color: {FAINT}; font-size: 12pt; font-weight: 400;
                      padding: 2px 10px; }}
QPushButton#module:hover {{ color: {TEXT}; }}
QPushButton#module:checked {{ color: {TEXT_STRONG}; font-weight: 600; }}
QLabel#moduleSep {{ color: {FAINT}; font-size: 12pt; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid rgba(245, 13, 138, 0.6); }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {LINE}; selection-background-color: {SURFACE_3};
                               selection-color: {TEXT_STRONG}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 14px; border: none; background: transparent; }}

QPushButton {{ background: {SURFACE_3}; border: 1px solid {LINE}; border-radius: 7px; padding: 6px 13px;
               font-weight: 500; color: {TEXT}; }}
QPushButton:hover {{ background: #222839; border-color: #2c3447; }}
QPushButton:pressed {{ background: #151a26; }}
QPushButton:checked {{ background: #242b3d; border-color: #3a4257; color: {TEXT_STRONG}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: #161b27; background: #10141e; }}
QPushButton#primary {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {VIOLET});
                       border: none; color: white; font-weight: 600; padding: 7px 16px; }}
QPushButton#primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_HOVER}, stop:1 #9150ff); }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESS}; }}
QPushButton#primary:disabled {{ background: #2a1630; color: #7d5a80; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {LINE}; }}
QPushButton#ghost:hover {{ background: {SURFACE_3}; }}
QPushButton#small {{ padding: 3px 9px; }}
QPushButton#flat {{ background: transparent; border: none; color: {MUTED}; padding: 3px 6px; }}
QPushButton#flat:hover {{ color: {TEXT_STRONG}; }}
QPushButton#flat:checked {{ color: {ACCENT_HOVER}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 3px; padding: 4px 10px; color: {MUTED}; }}
QPushButton#seg:checked {{ background: {SURFACE_3}; color: {TEXT_STRONG}; }}
QPushButton#seg:hover {{ color: {TEXT_STRONG}; }}
QFrame#segBar {{ background: #151515; border: 1px solid {LINE}; border-radius: 4px; }}
QPushButton#tooltab {{ background: transparent; border: none; border-left: 2px solid transparent; border-radius: 0;
                       padding: 10px 0; color: {MUTED}; font-size: 15pt; }}
QPushButton#tooltab:hover {{ color: {TEXT}; }}
QPushButton#tooltab:checked {{ color: {TEXT_STRONG}; border-left: 2px solid {ACCENT}; background: {SURFACE}; }}
QPushButton#section {{ background: {SURFACE_2}; border: none; border-top: 1px solid {BORDER}; border-radius: 0;
                       text-align: left; padding: 6px 10px; color: {TEXT}; font-weight: 600; font-size: 8.5pt;
                       letter-spacing: 0.6px; }}
QPushButton#section:hover {{ background: #282828; }}

QCheckBox {{ spacing: 7px; background: transparent; }}

/* chips: opciones que se encienden (magenta) y se apagan con un clic, en vez de casillas */
QPushButton#chip, QCheckBox#chip {{ background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: 13px;
    padding: 4px 11px; color: {MUTED}; font-weight: 500; min-height: 18px; }}
QPushButton#chip:hover, QCheckBox#chip:hover {{ color: {TEXT_STRONG}; border-color: #34405a; }}
QPushButton#chip:checked, QCheckBox#chip:checked {{ background: rgba(245, 13, 138, 0.16);
    border: 1px solid rgba(245, 13, 138, 0.75); color: {TEXT_STRONG}; }}
QPushButton#chip:disabled, QCheckBox#chip:disabled {{ color: {FAINT}; }}
QCheckBox#chip::indicator {{ width: 0; height: 0; border: none; }}
/* botones de solo icono */
QToolButton#iconBtn {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }}
QToolButton#iconBtn:hover {{ background: {SURFACE_3}; border-color: {LINE}; }}
QToolButton#iconBtn:checked {{ background: rgba(245, 13, 138, 0.16); border-color: rgba(245, 13, 138, 0.6); }}
QToolButton#iconBtn:disabled {{ background: transparent; }}
QPushButton#iconBtn {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px 7px; }}
QPushButton#iconBtn:hover {{ background: {SURFACE_3}; border-color: {LINE}; }}
QPushButton#iconBtn:checked {{ background: rgba(245, 13, 138, 0.16); border-color: rgba(245, 13, 138, 0.6); }}
QPushButton#iconBtn:disabled {{ background: transparent; border-color: transparent; }}
/* revisión: aprobar / rechazar bien visibles (rellenos cuando es la decisión de esa foto) */
QPushButton#approve, QPushButton#reject {{ border-radius: 18px; padding: 8px 22px; font-size: 10.5pt;
                                           font-weight: 600; min-height: 20px; }}
QPushButton#approve {{ background: rgba(76, 195, 138, 0.16); border: 1px solid rgba(76, 195, 138, 0.75); color: #e3f7ec; }}
QPushButton#approve:hover {{ background: rgba(76, 195, 138, 0.32); }}
QPushButton#approve:checked {{ background: #2f9e6c; border-color: #4cc38a; color: white; }}
QPushButton#reject {{ background: rgba(229, 72, 77, 0.14); border: 1px solid rgba(229, 72, 77, 0.7); color: #fbe3e4; }}
QPushButton#reject:hover {{ background: rgba(229, 72, 77, 0.30); }}
QPushButton#reject:checked {{ background: #c4373c; border-color: #e5484d; color: white; }}
/* selector de módulos en píldora */
QFrame#modulePill {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 17px; }}
QPushButton#pill {{ background: transparent; border: none; border-radius: 13px; padding: 5px 14px; color: {MUTED};
                    font-weight: 500; }}
QPushButton#pill:hover {{ color: {TEXT_STRONG}; }}
QPushButton#pill:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(245,13,138,0.30),
                            stop:1 rgba(123,47,247,0.30)); color: {TEXT_STRONG}; font-weight: 600; }}
/* pasos Analizar → Revisar → Exportar */
QPushButton#step {{ background: transparent; border: 1px solid {LINE}; border-radius: 16px; padding: 6px 14px;
                    color: {MUTED}; }}
QPushButton#step:hover {{ color: {TEXT_STRONG}; border-color: #34405a; }}
QPushButton#stepNext {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {VIOLET});
                        border: none; border-radius: 16px; padding: 7px 16px; color: white; font-weight: 600; }}
QPushButton#stepNext:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_HOVER}, stop:1 #9150ff); }}
QPushButton#stepDone {{ background: rgba(76, 195, 138, 0.10); border: 1px solid rgba(76, 195, 138, 0.45);
                        border-radius: 16px; padding: 6px 14px; color: #bfe9d3; }}
QLabel#stepLine {{ color: {FAINT}; }}
/* bloques del panel lateral, panel deslizante de ajustes y paleta de órdenes */
QFrame#block {{ background: transparent; border: none; border-bottom: 1px solid {LINE}; }}
QLabel#blockTitle {{ color: {FAINT}; font-size: 7.5pt; font-weight: 700; letter-spacing: 1.6px; }}
QDialog#sheet {{ background: {SURFACE}; border: 1px solid {LINE}; }}
QFrame#sheetHead {{ background: {CHROME}; border-bottom: 1px solid {LINE}; }}
QDialog#palette {{ background: {SURFACE}; border: 1px solid #2c3447; border-radius: 12px; }}
QLineEdit#paletteEdit {{ background: transparent; border: none; font-size: 12pt; padding: 6px; }}
QListWidget#paletteList::item {{ padding: 7px 8px; border-radius: 6px; color: {TEXT}; }}
QListWidget#paletteList::item:selected {{ background: rgba(245, 13, 138, 0.16); color: {TEXT_STRONG}; }}
QFrame#sessionChip {{ background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: 10px; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border-radius: 2px; border: 1px solid #4a4a4a; background: #121212; }}
QCheckBox::indicator:hover {{ border-color: #777; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(:/check.svg); }}

QProgressBar {{ background: #222; border: none; border-radius: 1px; max-height: 3px; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 1px; }}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ color: {MUTED}; padding: 2px; }}
QListWidget::item:hover {{ color: {TEXT_STRONG}; }}
QListWidget::item:selected {{ background: {SURFACE_3}; color: {TEXT_STRONG}; border-radius: 2px; }}
QListWidget#grid {{ background: {BG}; }}
QListWidget#list::item {{ padding: 4px 8px; border-radius: 2px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 4px 10px; }}
QTabBar::tab:selected {{ color: {TEXT_STRONG}; border-bottom: 2px solid {ACCENT}; }}

QSplitter::handle {{ background: {BORDER}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3a3a3a; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4a4a4a; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #3a3a3a; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT_STRONG}; border: 1px solid #2c3447; padding: 5px 8px;
             border-radius: 6px; }}
QMenu {{ background: {SURFACE}; border: 1px solid #2c3447; padding: 5px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {SURFACE_3}; color: {TEXT_STRONG}; }}
QMenu::section {{ color: {FAINT}; font-size: 8pt; padding: 6px 10px 2px; }}
QMessageBox {{ background: {SURFACE}; }}

/* sliders finos con tirador pequeño (sin relleno de color: el valor neutro está en el centro) */
QSlider::groove:horizontal {{ height: 2px; background: #3c3c3c; border-radius: 1px; }}
QSlider::sub-page:horizontal {{ background: #3c3c3c; }}
QSlider::handle:horizontal {{ background: #cfcfcf; width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; }}
QSlider::handle:horizontal:hover {{ background: white; }}
"""

CHECK_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" '
             b'fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def slider_qss(key: str) -> str:
    """Pista de color para los sliders que la llevan en Lightroom/Capture One."""
    g = GRADIENTS.get(key)
    if key.startswith("hsl_") and key.endswith("_h"):
        g = GRADIENTS["hue"]
    elif key.startswith("hsl_") and key.endswith("_s"):
        g = GRADIENTS["saturation"]
    elif key.endswith("_hue"):
        g = GRADIENTS["hue"]
    if not g:
        return ""
    return (f"QSlider::groove:horizontal {{ height: 3px; border-radius: 1px; "
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {g}); }}"
            f"QSlider::sub-page:horizontal {{ background: transparent; }}")
