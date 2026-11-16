"""Tema visual de PhootFlow: colores sacados del logo (negro azulado, magenta y blanco)."""

BG = "#05070F"         # fondo del logo
SURFACE = "#0C0F1C"
SURFACE_2 = "#131729"
SURFACE_3 = "#1B2036"
BORDER = "#242A44"
TEXT = "#F5F6FB"
MUTED = "#8C92B0"
ACCENT = "#F50D8A"     # magenta del logo
ACCENT_HOVER = "#FF3DA3"
ACCENT_PRESS = "#C90A71"
OK = "#3DDC97"
WARN = "#FFB547"
DANGER = "#FF5470"

QSS = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
    color: {TEXT};
}}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QListWidget {{ background: {BG}; border: none; outline: none; }}
QListWidget::item {{ color: {MUTED}; }}
QListWidget::item:selected {{ background: {SURFACE_3}; color: {TEXT}; border-radius: 6px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 6px 10px; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QWidget#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QFrame#card {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel#cardTitle {{
    color: {MUTED}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px;
    background: transparent;
}}
QLabel {{ background: transparent; }}
QLabel#muted, QLabel#version {{ color: {MUTED}; }}
QLabel#h1 {{ font-size: 15pt; font-weight: 600; }}
QLabel#chip {{
    background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 11px;
    padding: 3px 10px; color: {MUTED}; font-size: 9pt;
}}
QLabel#chipAccent {{
    background: rgba(245, 13, 138, 0.14); border: 1px solid rgba(245, 13, 138, 0.45);
    border-radius: 11px; padding: 3px 10px; color: {ACCENT_HOVER}; font-size: 9pt;
}}
QFrame#banner {{
    background: rgba(245, 13, 138, 0.12); border: 1px solid rgba(245, 13, 138, 0.5); border-radius: 10px;
}}
QFrame#previewPane {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
QLabel#previewImage {{ background: {BG}; border-radius: 10px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px; border: none; background: transparent;
}}

QPushButton {{
    background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 9px; padding: 8px 14px;
    font-weight: 600;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {SURFACE_2}; }}
QPushButton:disabled {{ color: #4B5070; border-color: {SURFACE_3}; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESS}; }}
QPushButton#primary:disabled {{ background: #3A1630; border-color: #3A1630; color: #8A5577; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {BORDER}; }}
QPushButton#ghost:hover {{ border-color: {ACCENT}; color: {ACCENT_HOVER}; }}
QPushButton#small {{ padding: 5px 10px; font-weight: 500; }}
QPushButton#seg {{
    background: transparent; border: none; border-radius: 7px; padding: 5px 12px; color: {MUTED};
    font-weight: 500;
}}
QPushButton#seg:checked {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton#seg:hover {{ color: {TEXT}; }}
QFrame#segBar {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 9px; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid #3A4163; background: {BG};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(:/check.svg); }}

QProgressBar {{
    background: {SURFACE_2}; border: none; border-radius: 3px; max-height: 6px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_PRESS}, stop:1 {ACCENT_HOVER});
}}
QListWidget#grid {{ background: {BG}; border: none; outline: none; }}
QSplitter::handle {{ background: {BORDER}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #2C3352; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QMessageBox {{ background: {SURFACE}; }}
QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
"""

CHECK_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" '
             b'fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')
