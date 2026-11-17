"""Tema visual de PhootFlow.

Inspirado en los editores profesionales (Lightroom Classic, Capture One): grises neutros casi negros para que
la foto mande, paneles finos con separadores de 1 px, tipografía pequeña y legible, y el magenta de la marca
reservado a la selección y a la acción principal.
"""

BG = "#141414"          # visor / lienzo (detrás de la foto)
SURFACE = "#1d1d1d"     # paneles
SURFACE_2 = "#232323"   # cabeceras de panel, campos
SURFACE_3 = "#2c2c2c"   # botones, hover
BORDER = "#0b0b0b"      # separadores (oscuros, como en Lightroom)
LINE = "#303030"        # líneas finas dentro de los paneles
TEXT = "#dcdcdc"
TEXT_STRONG = "#f4f4f4"
MUTED = "#8e8e8e"
FAINT = "#5e5e5e"
ACCENT = "#F50D8A"      # magenta PhootFlow
ACCENT_HOVER = "#FF3DA3"
ACCENT_PRESS = "#C90A71"
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
QFrame#topbar {{ background: #0f0f0f; border-bottom: 1px solid {BORDER}; }}
QFrame#previewPane {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
QFrame#toolbar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QFrame#filmstrip {{ background: #111111; border-top: 1px solid {BORDER}; }}
QFrame#tooltabs {{ background: #181818; border-left: 1px solid {BORDER}; }}
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
    background: #121212; border: 1px solid {LINE}; border-radius: 3px; padding: 4px 6px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid #555; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {LINE}; selection-background-color: {SURFACE_3};
                               selection-color: {TEXT_STRONG}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 14px; border: none; background: transparent; }}

QPushButton {{ background: {SURFACE_3}; border: 1px solid #3a3a3a; border-radius: 3px; padding: 5px 12px;
               font-weight: 500; color: {TEXT}; }}
QPushButton:hover {{ background: #363636; border-color: #4a4a4a; }}
QPushButton:pressed {{ background: #262626; }}
QPushButton:checked {{ background: #3a3a3a; border-color: #5a5a5a; color: {TEXT_STRONG}; }}
QPushButton:disabled {{ color: #555; border-color: #2a2a2a; background: #222; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESS}; }}
QPushButton#primary:disabled {{ background: #3a1b2c; border-color: #3a1b2c; color: #8a5a74; }}
QPushButton#ghost {{ background: transparent; border: 1px solid #3a3a3a; }}
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
QToolTip {{ background: #2a2a2a; color: {TEXT_STRONG}; border: 1px solid #3c3c3c; padding: 4px 6px; }}
QMenu {{ background: #252525; border: 1px solid #3a3a3a; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 14px; }}
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
