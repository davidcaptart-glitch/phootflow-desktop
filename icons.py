"""Iconos de línea propios de PhootFlow (SVG de 24 px dibujados a mano, sin licencias de terceros).

`icon(nombre)` devuelve un QIcon que cambia de color al activarse (magenta) y al pasar por encima."""
from __future__ import annotations

from functools import lru_cache

_P = {
    # acciones generales
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "open": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7"/>'
            '<path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M16 16l5 5"/>',
    "zap": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "scan": '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/>'
            '<circle cx="12" cy="12" r="3"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "export": '<path d="M12 15V3M7 8l5-5 5 5M5 15v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "crop": '<path d="M6 2v14a2 2 0 0 0 2 2h14M2 6h14a2 2 0 0 1 2 2v14"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "wand": '<path d="M4 20L15 9M14 4v2M19 9h2M17.5 5.5l1.5-1.5M18 4l2 2"/>',
    "lut": '<rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1"/>'
           '<rect x="3" y="13" width="8" height="8" rx="1"/><rect x="13" y="13" width="8" height="8" rx="1"/>',
    "bw": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="COLOR"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/>'
               '<circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "cloud": '<path d="M7 18a4 4 0 0 1-.5-8A6 6 0 0 1 18 9a4.5 4.5 0 0 1-.5 9z"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1.5 0 2-1 2-2s-1-1.5-1-2.5S14 15 15 15h2a4 4 0 0 0 4-4c0-4.4-4-8-9-8z"/>'
               '<circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
    "detail": '<path d="M12 3l8 9-8 9-8-9z"/><path d="M12 3v18M4 12h16"/>',
    "mask": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4" stroke-dasharray="2 2"/>',
    "power": '<path d="M12 3v8"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/>',
    "invert": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="COLOR"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "paste": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1M9 12h6M9 16h4"/>',
    "undo": '<path d="M4 9h11a5 5 0 0 1 0 10H8"/><path d="M8 5L4 9l4 4"/>',
    "sync": '<path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3"/><path d="M18 3v4h-4M6 21v-4h4"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8V3M7 21v-7h10v7"/>',
    # módulos
    "events": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
              '<rect x="4" y="13" width="7" height="7" rx="1.5"/><path d="M16.5 13l1.2 2.3 2.3 1.2-2.3 1.2-1.2 2.3'
              '-1.2-2.3-2.3-1.2 2.3-1.2z"/>',
    "studio": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/>'
              '<circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "edits": '<path d="M15 4l5 5-9 9H6v-5z"/><path d="M13 6l5 5"/>',
    "graphics": '<circle cx="8" cy="8" r="4"/><rect x="13" y="13" width="7" height="7" rx="1"/><path d="M15 3l4 7h-8z"/>',
    # tipos de evento
    "auto": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 1.8 1.8.7-1.8.7'
            '-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    "wedding": '<circle cx="9" cy="14" r="5"/><circle cx="15" cy="14" r="5"/><path d="M10 5l2-2 2 2-2 2z"/>',
    "sport": '<circle cx="12" cy="12" r="9"/><path d="M12 3c-3 3-3 15 0 18M12 3c3 3 3 15 0 18M3.5 9h17M3.5 15h17"/>',
    "party": '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "communion": '<path d="M12 3v18M7 8h10"/>',
    "birthday": '<path d="M4 21h16M5 21v-8h14v8M5 16c2 1.5 4 1.5 7 0s5-1.5 7 0M12 13V9"/><path d="M12 4.5c1 1 1 2.5 0 3'
                '-1-.5-1-2 0-3z"/>',
    "crown": '<path d="M3 18h18M4 18L3 8l5 4 4-7 4 7 5-4-1 10"/>',
    "dance": '<circle cx="13" cy="4.5" r="1.8"/><path d="M8 9l4-2 3 3 3-1M12 7l-1 6 4 3-1 5M11 13l-4 4"/>',
    "studio_photo": '<rect x="3" y="7" width="18" height="13" rx="2"/><circle cx="12" cy="13.5" r="3.5"/>'
                    '<path d="M8 7l1.5-3h5L16 7"/>',
    "star": '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    # selección automática
    "blur": '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="8" stroke-dasharray="2.5 2.5"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
           'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "dupes": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V5a1 1 0 0 1 1-1h11"/>',
    "eyes": '<path d="M2 10s3.5 5 10 5 10-5 10-5M4.5 13l-2 2M19.5 13l2 2M12 15v3M7.5 14.5L6 17M16.5 14.5L18 17"/>',
    "move": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 13h6M13 10.5'
            'l2.5 2.5-2.5 2.5"/>',
    "review": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M8 12l3 3 5-6"/>',
}


def names() -> list[str]:
    return list(_P)


def svg(name: str, color: str = "#e6e8ef", width: float = 1.7) -> bytes:
    body = _P.get(name, _P["info"]).replace("COLOR", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


@lru_cache(maxsize=512)
def pixmap(name: str, size: int = 18, color: str = "#e6e8ef"):
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    pm = QPixmap(size * 2, size * 2)  # a doble resolución: nítido en pantallas con escalado
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(svg(name, color))).render(p)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return pm


def icon(name: str, size: int = 18, color: str | None = None, on: str | None = None):
    """Icono con estados: normal (gris claro), activo/marcado (magenta) y desactivado (apagado)."""
    import theme as T
    from PySide6.QtGui import QIcon
    ic = QIcon()
    base = color or T.ICON
    ic.addPixmap(pixmap(name, size, base), QIcon.Normal, QIcon.Off)
    ic.addPixmap(pixmap(name, size, T.TEXT_STRONG), QIcon.Active, QIcon.Off)
    ic.addPixmap(pixmap(name, size, on or T.ACCENT_HOVER), QIcon.Normal, QIcon.On)
    ic.addPixmap(pixmap(name, size, on or T.ACCENT_HOVER), QIcon.Active, QIcon.On)
    ic.addPixmap(pixmap(name, size, T.FAINT), QIcon.Disabled, QIcon.Off)
    return ic


def for_style(name: str) -> str:
    """Icono para un tipo de sesión (por palabras de su nombre)."""
    n = (name or "").lower()
    for keys, ic in ((("auto",), "auto"), (("boda",), "wedding"), (("deport", "fútbol", "futbol"), "sport"),
                     (("disco", "noche", "fiesta"), "party"), (("comuni", "bautizo"), "communion"),
                     (("cumple",), "birthday"), (("quince", "xv"), "crown"), (("baile", "danza"), "dance"),
                     (("estudio", "retrato"), "studio_photo")):
        if any(k in n for k in keys):
            return ic
    return "star"
