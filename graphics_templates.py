"""Graphics: lote de plantillas propias (bodas, comunión, fiestas, deporte, negocio, redes, impresión y copias).

Cada plantilla es una función `tpl(d) -> elementos` que se adapta al tamaño del diseño (todo en fracciones del
ancho y el alto) y usa solo fuentes que trae Windows, para que funcionen sin conexión. Los textos llevan «fit_h»
(el alto hasta lo siguiente): con un nombre largo la letra baja en vez de montarse encima. Diseño propio: no copia
plantillas de Canva ni de ningún otro servicio.

Se registran en `graphics_engine.TEMPLATES` al importar el motor (`TEMPLATE_SIZE` y `TEMPLATE_GROUP`).
"""
from __future__ import annotations

import graphics_engine as G

# ------------------------------------------------------------------ decoraciones vectoriales propias (viewbox 100)
HEART = ([100, 90], "M 50 88 C 20 65 0 45 0 25 C 0 10 12 0 26 0 C 37 0 45 6 50 15 C 55 6 63 0 74 0 C 88 0 100 10 "
                    "100 25 C 100 45 80 65 50 88 Z")
SPARKLE = ([100, 100], "M 50 0 C 54 30 70 46 100 50 C 70 54 54 70 50 100 C 46 70 30 54 0 50 C 30 46 46 30 50 0 Z")
LEAF = ([100, 50], "M 0 25 C 25 0 75 0 100 25 C 75 50 25 50 0 25 Z")
BLOB = ([100, 100], "M 52 4 C 80 2 99 26 95 54 C 91 82 66 99 40 94 C 14 89 1 66 6 40 C 10 18 28 6 52 4 Z")
WAVE = ([100, 20], "M 0 10 C 15 0 35 0 50 10 C 65 20 85 20 100 10 L 100 20 L 0 20 Z")
ARROW = ([100, 60], "M 0 24 L 68 24 L 68 0 L 100 30 L 68 60 L 68 36 L 0 36 Z")
SWASH = ([100, 12], "M 0 6 C 20 0 30 0 50 6 C 70 12 80 12 100 6 C 80 10 70 10 50 4 C 30 -2 20 -2 0 6 Z")


def V(shape, x, y, w, h, fill, rot=0.0, name="Adorno", **kw):
    vb, dd = shape
    el = G.vector_el({"viewbox": vb, "paths": [{"fill": fill, "d": dd}]}, x, y, w, h, rot=rot, name=name)
    el.update(kw)
    return el


def R(d, x, y, w, h, fill, radius=0.0, stroke="#05070F", stroke_w=0.0, **kw):
    return G.shape_el(d, "rect", x=x, y=y, w=w, h=h, fill=fill, stroke=stroke, stroke_w=stroke_w, radius=radius,
                      **kw)


def O(d, x, y, w, h, fill, **kw):
    return G.shape_el(d, "ellipse", x=x, y=y, w=w, h=h, fill=fill, stroke_w=0.0, **kw)


def STAR(d, x, y, s, fill):
    return G.shape_el(d, "star", x=x, y=y, w=s, h=s, fill=fill, stroke_w=0.0)


def P(d, x, y, w, h, **kw):
    return G.image_el(d, "", x=x, y=y, w=w, h=h, **kw)


def T(d, text, role, x, y, w, fit, **kw):
    el = G._t(d, text, role, x, y, w, fit=fit, **kw)
    # el tamaño elegido nunca es mayor que lo que cabe en una línea de su hueco (así un texto corto recupera
    # siempre el tamaño de la plantilla)
    probe = G.fit_text(dict(el, text="Hg"))
    if probe["size"] < el["size_max"]:
        el["size"] = el["size_max"] = probe["size"]
        G.fit_text(el)
    return el


def stack(d, x, y, w, specs, gap=0.0):
    """Textos uno debajo de otro: specs = [(texto, papel, alto, {props}), …]. Nunca se solapan."""
    out = []
    for text, role, h, kw in specs:
        out.append(T(d, text, role, x, y, w, h, **kw))
        y += h + gap
    return out


def fs(d, k):
    """Tamaño de letra relativo al lado corto."""
    return round(min(d["w"], d["h"]) * k, 1)


# ================================================================== BODAS

def save_the_date(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#1B1B1B"
    return [P(d, 0, 0, W, H),
            R(d, 0, H * 0.58, W, H * 0.42, "#0A0A0ACC"),
            *stack(d, W * 0.08, H * 0.62, W * 0.84, [
                ("Save the date", "title", H * 0.11, dict(font="Gabriola", color="#FFFFFF", bold=False,
                                                          size=fs(d, 0.13))),
                ("ANA & PABLO", "subtitle", H * 0.07, dict(font="Georgia", color="#E8D9B5", spacing=4.0)),
                ("12 · 09 · 2026  ·  Sevilla", "body", H * 0.08, dict(color="#D6D6D6", upper=True,
                                                                     spacing=2.0))])]


def boda_floral(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FBF7F1"
    g, s = "#8A9A86", min(W, H)
    els = [V(LEAF, W * 0.02, H * 0.03, s * 0.34, s * 0.17, g, rot=-35), V(LEAF, W * 0.12, H * 0.10, s * 0.22,
                                                                          s * 0.11, "#B5C2AE", rot=-60),
           V(LEAF, W - s * 0.36, H * 0.03, s * 0.34, s * 0.17, g, rot=35),
           V(LEAF, W - s * 0.34, H * 0.10, s * 0.22, s * 0.11, "#B5C2AE", rot=60),
           V(LEAF, W * 0.02, H - s * 0.22, s * 0.34, s * 0.17, g, rot=35),
           V(LEAF, W - s * 0.36, H - s * 0.22, s * 0.34, s * 0.17, g, rot=-35)]
    els += stack(d, W * 0.12, H * 0.22, W * 0.76, [
        ("Con la alegría de sus familias", "body", H * 0.06, dict(color="#6B6B6B", font="Georgia", italic=True)),
        ("Lucía", "title", H * 0.12, dict(font="Gabriola", color="#3E4A3C", bold=False, size=fs(d, 0.17))),
        ("&", "subtitle", H * 0.06, dict(font="Georgia", color="#C9A86A", italic=True)),
        ("Marcos", "title", H * 0.12, dict(font="Gabriola", color="#3E4A3C", bold=False, size=fs(d, 0.17))),
        ("os invitan a celebrar su boda", "body", H * 0.06, dict(color="#6B6B6B", font="Georgia",
                                                                italic=True))], gap=H * 0.005)
    els.append(R(d, W * 0.42, H * 0.70, W * 0.16, max(2.0, H * 0.003), "#C9A86A"))
    els += stack(d, W * 0.12, H * 0.72, W * 0.76, [
        ("SÁBADO 19 DE SEPTIEMBRE DE 2026", "body", H * 0.05, dict(color="#3E4A3C", spacing=2.0)),
        ("18:30 h · Hacienda Los Olivos · Córdoba", "body", H * 0.05, dict(color="#6B6B6B")),
        ("Confirmad asistencia antes del 1 de agosto", "body", H * 0.045, dict(color="#8A8A8A",
                                                                              italic=True))])
    return els


def boda_menu(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    m = min(W, H) * 0.06
    els = [R(d, m, m, W - 2 * m, H - 2 * m, "#00000000", stroke="#C9A86A", stroke_w=max(2.0, m * 0.05))]
    els += stack(d, W * 0.12, H * 0.07, W * 0.76, [
        ("Menú", "title", H * 0.09, dict(font="Gabriola", color="#2B2B2B", bold=False, size=fs(d, 0.2))),
        ("Lucía & Marcos · 19.09.2026", "body", H * 0.035, dict(color="#8A7350", upper=True, spacing=1.5))])
    y = H * 0.22
    for title, body in (("Entrantes", "Jamón ibérico y queso curado\nSalmorejo con huevo y virutas\nCroquetas de "
                                      "la casa"),
                        ("Principal", "Lubina a la brasa con verduras\no solomillo al Pedro Ximénez"),
                        ("Postre", "Tarta nupcial\nCafé y licores")):
        els.append(R(d, W * 0.44, y, W * 0.12, max(1.5, H * 0.002), "#C9A86A"))
        els += stack(d, W * 0.12, y + H * 0.012, W * 0.76, [
            (title.upper(), "subtitle", H * 0.04, dict(font="Georgia", color="#2B2B2B", spacing=2.0)),
            (body, "body", H * 0.15, dict(color="#5A5A5A", font="Georgia", italic=True, line=1.3))])
        y += H * 0.235
    return els


def boda_gracias(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#F6F1EA"
    return [P(d, W * 0.08, H * 0.05, W * 0.84, H * 0.55, radius=min(W, H) * 0.42),
            *stack(d, W * 0.08, H * 0.63, W * 0.84, [
                ("Gracias", "title", H * 0.14, dict(font="Gabriola", color="#2B2B2B", bold=False,
                                                    size=fs(d, 0.24))),
                ("por acompañarnos en un día tan especial", "body", H * 0.08, dict(color="#6B6B6B",
                                                                                  font="Georgia", italic=True)),
                ("— Lucía & Marcos", "body", H * 0.06, dict(color="#8A7350", upper=True, spacing=1.5))])]


def boda_story(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#000000"
    m = W * 0.05
    return [P(d, 0, 0, W, H),
            R(d, m, m, W - 2 * m, H - 2 * m, "#00000000", stroke="#FFFFFF", stroke_w=max(3.0, W * 0.004)),
            R(d, 0, H * 0.72, W, H * 0.2, "#00000066"),
            *stack(d, W * 0.1, H * 0.735, W * 0.8, [
                ("Lucía & Marcos", "title", H * 0.09, dict(font="Gabriola", color="#FFFFFF", bold=False,
                                                           size=fs(d, 0.16))),
                ("19 · 09 · 2026", "body", H * 0.05, dict(color="#FFFFFF", spacing=3.0))])]


def boda_collage(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FBF7F1"
    g = min(W, H) * 0.03
    return [P(d, g, g, W * 0.58 - 1.5 * g, H - 2 * g),
            P(d, W * 0.58 + g / 2, g, W * 0.42 - 1.5 * g, H * 0.5 - 1.5 * g),
            R(d, W * 0.58 + g / 2, H * 0.5 + g / 2, W * 0.42 - 1.5 * g, H * 0.5 - 1.5 * g, "#3E4A3C"),
            *stack(d, W * 0.6 + g / 2, H * 0.58, W * 0.38 - 2 * g, [
                ("Nuestra boda", "subtitle", H * 0.13, dict(font="Gabriola", color="#FFFFFF", size=fs(d, 0.085))),
                ("Lucía & Marcos", "body", H * 0.07, dict(color="#E8D9B5", font="Georgia", italic=True)),
                ("19.09.2026", "body", H * 0.07, dict(color="#FFFFFF", spacing=2.0))])]


# ================================================================== COMUNIÓN Y BAUTIZO

def comunion(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#EEF3F8"
    s = min(W, H)
    return [O(d, W * 0.5 - s * 0.32, H * 0.08, s * 0.64, s * 0.64, "#FFFFFF"),
            P(d, W * 0.5 - s * 0.29, H * 0.08 + s * 0.03, s * 0.58, s * 0.58, clip="ellipse"),
            V(SPARKLE, W * 0.12, H * 0.10, s * 0.08, s * 0.08, "#C9A86A"),
            V(SPARKLE, W * 0.80, H * 0.24, s * 0.06, s * 0.06, "#C9A86A"),
            V(SPARKLE, W * 0.17, H * 0.40, s * 0.045, s * 0.045, "#9DB4CC"),
            *stack(d, W * 0.1, H * 0.08 + s * 0.70, W * 0.8, [
                ("Mi primera comunión", "subtitle", H * 0.07, dict(font="Gabriola", color="#4F6D8C",
                                                                   size=fs(d, 0.09))),
                ("Daniel", "title", H * 0.1, dict(font="Georgia", color="#2C3E50")),
                ("Parroquia de San Juan · 9 de mayo de 2026", "body", H * 0.06, dict(color="#5F7488"))])]


def bautizo(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FDF3EF"
    s = min(W, H)
    return [V(BLOB, W * 0.5 - s * 0.42, H * 0.05, s * 0.84, s * 0.84, "#F6D9CF"),
            P(d, W * 0.5 - s * 0.33, H * 0.05 + s * 0.09, s * 0.66, s * 0.66, radius=s * 0.06),
            V(HEART, W * 0.08, H * 0.06, s * 0.08, s * 0.072, "#E8A598"),
            V(HEART, W * 0.84, H * 0.5, s * 0.06, s * 0.054, "#E8A598"),
            *stack(d, W * 0.1, H * 0.05 + s * 0.9, W * 0.8, [
                ("Bienvenida al mundo", "subtitle", H * 0.07, dict(font="Gabriola", color="#B56B5C",
                                                                   size=fs(d, 0.085))),
                ("Martina", "title", H * 0.09, dict(font="Georgia", color="#5C3B33")),
                ("Celebramos su bautizo el 14 de junio", "body", H * 0.06, dict(color="#8C6A62"))])]


# ================================================================== CUMPLEAÑOS Y FIESTAS

def cumple_infantil(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFD84D"
    s = min(W, H)
    dots = [(0.06, 0.08, 0.07, "#F50D8A"), (0.85, 0.06, 0.09, "#2E86AB"), (0.9, 0.42, 0.05, "#FFFFFF"),
            (0.05, 0.5, 0.05, "#2E86AB"), (0.12, 0.86, 0.08, "#FFFFFF"), (0.82, 0.84, 0.07, "#F50D8A")]
    els = [O(d, W * x, H * y, s * r, s * r, c) for x, y, r, c in dots]
    els += [O(d, W * 0.5 - s * 0.24, H * 0.12, s * 0.48, s * 0.48, "#FFFFFF"),
            P(d, W * 0.5 - s * 0.22, H * 0.12 + s * 0.02, s * 0.44, s * 0.44, clip="ellipse"),
            O(d, W * 0.5 + s * 0.12, H * 0.10, s * 0.16, s * 0.16, "#F50D8A"),
            T(d, "6", "title", W * 0.5 + s * 0.12, H * 0.10 + s * 0.015, s * 0.16, s * 0.13,
              color="#FFFFFF", font="Arial Black", size=fs(d, 0.1)),
            *stack(d, W * 0.08, H * 0.12 + s * 0.52, W * 0.84, [
                ("¡Feliz cumpleaños, Leo!", "title", H * 0.12, dict(font="Arial Black", color="#1F2A44",
                                                                    size=fs(d, 0.085))),
                ("Sábado 7 de marzo · 17:00 · Parque Central", "body", H * 0.08, dict(color="#1F2A44"))])]
    return els


def fiesta_flyer(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#0B0614"
    s = min(W, H)
    return [O(d, -s * 0.3, -s * 0.25, s * 0.9, s * 0.9, "#F50D8A33"),
            O(d, W - s * 0.55, H * 0.45, s * 0.8, s * 0.8, "#7B2FF733"),
            P(d, W * 0.1, H * 0.12, W * 0.8, H * 0.42, radius=s * 0.03),
            *stack(d, W * 0.1, H * 0.57, W * 0.8, [
                ("NOCHE DE FIESTA", "title", H * 0.1, dict(font="Impact", color="#FFFFFF", bold=False,
                                                           size=fs(d, 0.12), spacing=1.0)),
                ("DJ · Photocall · Barra libre", "subtitle", H * 0.06, dict(color="#F50D8A", upper=True,
                                                                           spacing=2.0))]),
            R(d, W * 0.1, H * 0.76, W * 0.8, H * 0.13, "#F50D8A", radius=s * 0.02),
            *stack(d, W * 0.12, H * 0.775, W * 0.76, [
                ("VIERNES 24 DE OCTUBRE · 23:00", "subtitle", H * 0.055, dict(color="#FFFFFF", bold=True)),
                ("Sala Apolo · Entrada con invitación", "body", H * 0.045, dict(color="#FFFFFF"))])]


def navidad(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#123524"
    s = min(W, H)
    els = [V(SPARKLE, W * x, H * y, s * r, s * r, c) for x, y, r, c in
           ((0.08, 0.07, 0.07, "#E8C872"), (0.85, 0.12, 0.05, "#FFFFFF"), (0.16, 0.62, 0.04, "#FFFFFF"),
            (0.82, 0.58, 0.07, "#E8C872"), (0.5, 0.04, 0.035, "#FFFFFF"))]
    els += [O(d, W * 0.5 - s * 0.27, H * 0.12, s * 0.54, s * 0.54, "#E8C872"),
            P(d, W * 0.5 - s * 0.25, H * 0.12 + s * 0.02, s * 0.5, s * 0.5, clip="ellipse"),
            *stack(d, W * 0.08, H * 0.12 + s * 0.6, W * 0.84, [
                ("Felices fiestas", "title", H * 0.12, dict(font="Gabriola", color="#FFFFFF", bold=False,
                                                            size=fs(d, 0.14))),
                ("Gracias por un año lleno de momentos", "body", H * 0.07, dict(color="#E8C872", italic=True,
                                                                               font="Georgia"))])]
    return els


def dia_madre(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FBE4EA"
    s = min(W, H)
    return [P(d, W * 0.08, H * 0.06, W * 0.84, H * 0.56, radius=s * 0.04),
            V(HEART, W * 0.78, H * 0.56, s * 0.14, s * 0.126, "#E0567A", rot=12),
            V(HEART, W * 0.72, H * 0.53, s * 0.07, s * 0.063, "#F29BB2", rot=-10),
            *stack(d, W * 0.08, H * 0.67, W * 0.84, [
                ("Feliz día, mamá", "title", H * 0.12, dict(font="Gabriola", color="#8E2442", bold=False,
                                                            size=fs(d, 0.15))),
                ("Sesiones de madre e hijos · reserva tu fecha", "body", H * 0.07, dict(color="#8E2442"))])]


# ================================================================== DEPORTE

def deporte_resultado(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#0A0F1E"
    return [P(d, 0, 0, W, H * 0.62),
            R(d, 0, H * 0.55, W, H * 0.45, "#0A0F1E"),
            R(d, 0, H * 0.55, W, max(4.0, H * 0.012), "#F50D8A"),
            T(d, "FINAL DEL PARTIDO", "body", W * 0.06, H * 0.58, W * 0.88, H * 0.05, color="#F50D8A",
              bold=True, spacing=3.0),
            T(d, "C.D. LOCAL", "subtitle", W * 0.04, H * 0.66, W * 0.3, H * 0.12, color="#FFFFFF",
              font="Bahnschrift SemiBold Condensed"),
            T(d, "3 - 1", "title", W * 0.34, H * 0.645, W * 0.32, H * 0.15, color="#FFFFFF", font="Impact",
              bold=False, size=fs(d, 0.17)),
            T(d, "U.D. VISITANTE", "subtitle", W * 0.66, H * 0.66, W * 0.3, H * 0.12, color="#FFFFFF",
              font="Bahnschrift SemiBold Condensed"),
            T(d, "Liga Regional · Jornada 12", "body", W * 0.06, H * 0.86, W * 0.88, H * 0.07, color="#9BA3B5",
              upper=True, spacing=2.0)]


def deporte_mvp(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#0A0F1E"
    s = min(W, H)
    return [P(d, 0, 0, W, H),
            R(d, -W * 0.1, H * 0.66, W * 1.2, H * 0.14, "#F50D8A", rot=-6),
            R(d, -W * 0.1, H * 0.78, W * 1.2, H * 0.3, "#0A0F1E", rot=-6),
            T(d, "MVP", "title", W * 0.06, H * 0.05, W * 0.5, H * 0.14, color="#FFFFFF", font="Impact",
              bold=False, size=fs(d, 0.2), align="left"),
            *stack(d, W * 0.06, H * 0.82, W * 0.88, [
                ("JUGADOR DEL PARTIDO", "body", H * 0.045, dict(color="#F50D8A", bold=True, spacing=3.0,
                                                                align="left")),
                ("Álex Martín · #10", "title", H * 0.09, dict(color="#FFFFFF", font="Bahnschrift SemiBold "
                                                              "Condensed", align="left", size=fs(d, 0.1)))]),
            V(SPARKLE, W - s * 0.16, H * 0.06, s * 0.1, s * 0.1, "#FFFFFF")]


def deporte_proximo(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#0A0F1E"
    s = min(W, H)
    return [P(d, 0, 0, W, H * 0.5),
            R(d, 0, H * 0.46, W, H * 0.54, "#0A0F1E"),
            *stack(d, W * 0.08, H * 0.5, W * 0.84, [
                ("PRÓXIMO PARTIDO", "title", H * 0.07, dict(font="Impact", color="#FFFFFF", bold=False,
                                                            size=fs(d, 0.11))),
                ("C.D. LOCAL", "subtitle", H * 0.06, dict(color="#FFFFFF", font="Bahnschrift SemiBold "
                                                                                "Condensed"))]),
            O(d, W * 0.5 - s * 0.08, H * 0.635, s * 0.16, s * 0.16, "#F50D8A"),
            T(d, "VS", "subtitle", W * 0.5 - s * 0.08, H * 0.635 + s * 0.035, s * 0.16, s * 0.09,
              color="#FFFFFF", font="Impact", bold=False),
            *stack(d, W * 0.08, H * 0.635 + s * 0.19, W * 0.84, [
                ("U.D. VISITANTE", "subtitle", H * 0.06, dict(color="#FFFFFF", font="Bahnschrift SemiBold "
                                                                                    "Condensed")),
                ("Domingo 15 · 12:00 h\nEstadio Municipal", "body", H * 0.1, dict(color="#9BA3B5", line=1.3))])]


def deporte_equipo(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#0A0F1E"
    return [P(d, 0, 0, W, H),
            R(d, 0, H * 0.74, W, H * 0.26, "#0A0F1EE6"),
            R(d, W * 0.04, H * 0.79, max(4.0, W * 0.006), H * 0.16, "#F50D8A"),
            T(d, "PLANTILLA 2026/27", "title", W * 0.065, H * 0.77, W * 0.58, H * 0.12, color="#FFFFFF",
              font="Impact", bold=False, align="left", size=fs(d, 0.1)),
            T(d, "C.D. Local · Categoría Infantil", "body", W * 0.065, H * 0.89, W * 0.58, H * 0.08,
              color="#C9CFDB", align="left"),
            T(d, "#VamosLocal", "subtitle", W * 0.66, H * 0.81, W * 0.3, H * 0.1, color="#F50D8A",
              align="right", bold=True)]


# ================================================================== PARA TU NEGOCIO

def precios(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#111318"
    s = min(W, H)
    els = stack(d, W * 0.08, H * 0.05, W * 0.84, [
        ("Tarifas 2026", "title", H * 0.09, dict(font="Georgia", color="#FFFFFF")),
        ("Fotografía de eventos", "body", H * 0.04, dict(color="#F50D8A", upper=True, spacing=2.0))])
    y = H * 0.2
    for name, desc, price, hi in (("Esencial", "2 h de cobertura · 80 fotos editadas", "250 €", False),
                                  ("Completo", "6 h · 300 fotos · galería online", "650 €", True),
                                  ("Premium", "Día completo · álbum 30×30 · vídeo", "1.200 €", False)):
        els.append(R(d, W * 0.08, y, W * 0.84, H * 0.22, "#F50D8A" if hi else "#1D2029", radius=s * 0.025))
        els += [T(d, name, "subtitle", W * 0.12, y + H * 0.03, W * 0.48, H * 0.07, color="#FFFFFF", bold=True,
                  align="left"),
                T(d, desc, "body", W * 0.12, y + H * 0.11, W * 0.46, H * 0.09, color="#FFFFFF" if hi else
                  "#AEB4C2", align="left"),
                T(d, price, "title", W * 0.6, y + H * 0.06, W * 0.28, H * 0.1, color="#FFFFFF", align="right",
                  size=fs(d, 0.075))]
        y += H * 0.25
    els.append(T(d, "Reserva: 600 000 000 · tuweb.com", "body", W * 0.08, H * 0.94, W * 0.84, H * 0.045,
                 color="#AEB4C2"))
    return els


def antes_despues(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    g = max(2.0, W * 0.004)
    return [P(d, 0, 0, W / 2 - g, H), P(d, W / 2 + g, 0, W / 2 - g, H),
            R(d, W * 0.04, H * 0.04, W * 0.2, H * 0.07, "#05070FCC", radius=H * 0.035),
            T(d, "ANTES", "body", W * 0.04, H * 0.055, W * 0.2, H * 0.045, color="#FFFFFF", bold=True,
              spacing=2.0),
            R(d, W * 0.76, H * 0.04, W * 0.2, H * 0.07, "#F50D8ACC", radius=H * 0.035),
            T(d, "DESPUÉS", "body", W * 0.76, H * 0.055, W * 0.2, H * 0.045, color="#FFFFFF", bold=True,
              spacing=2.0)]


def reserva(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#F4EFE9"
    s = min(W, H)
    return [P(d, W * 0.06, H * 0.04, W * 0.88, H * 0.55, radius=s * 0.04),
            *stack(d, W * 0.08, H * 0.62, W * 0.84, [
                ("¿Hablamos de tu evento?", "title", H * 0.1, dict(font="Georgia", color="#1F2A44")),
                ("Quedan pocas fechas libres para 2026", "body", H * 0.05, dict(color="#5B6275"))]),
            R(d, W * 0.2, H * 0.8, W * 0.6, H * 0.065, "#F50D8A", radius=H * 0.0325),
            T(d, "RESERVA TU FECHA", "body", W * 0.2, H * 0.812, W * 0.6, H * 0.045, color="#FFFFFF",
              bold=True, spacing=2.0),
            T(d, "600 000 000 · @tuestudio", "body", W * 0.08, H * 0.9, W * 0.84, H * 0.05, color="#5B6275")]


def galeria_lista(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    g = min(W, H) * 0.06
    return [P(d, g, g, W - 2 * g, H * 0.62),
            *stack(d, g, H * 0.62 + g * 1.4, W - 2 * g, [
                ("Tu galería ya está lista", "title", H * 0.1, dict(font="Georgia", color="#05070F",
                                                                    size=fs(d, 0.075))),
                ("Descárgala con el enlace que te hemos enviado", "body", H * 0.06, dict(color="#5B6275")),
                ("galeria.tuweb.com", "body", H * 0.05, dict(color="#F50D8A", bold=True))])]


def testimonio(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#1F2A44"
    s = min(W, H)
    els = [STAR(d, W * 0.5 - s * 0.25 + i * s * 0.1, H * 0.1, s * 0.08, "#F2C14E") for i in range(5)]
    els += stack(d, W * 0.1, H * 0.24, W * 0.8, [
        ("“Las fotos de nuestra boda son preciosas. Supo captar cada momento sin que nos diéramos cuenta.”",
         "subtitle", H * 0.42, dict(font="Georgia", color="#FFFFFF", italic=True, line=1.3)),
        ("LAURA Y DAVID · BODA EN TOLEDO", "body", H * 0.06, dict(color="#F2C14E", spacing=2.0))], gap=H * 0.04)
    els.append(O(d, W * 0.5 - s * 0.08, H * 0.79, s * 0.16, s * 0.16, "#F2C14E"))
    els.append(P(d, W * 0.5 - s * 0.07, H * 0.79 + s * 0.01, s * 0.14, s * 0.14, clip="ellipse"))
    return els


def portada_fb(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#05070F"
    return [P(d, W * 0.45, 0, W * 0.55, H),
            R(d, W * 0.06, H * 0.3, max(4.0, W * 0.004), H * 0.4, "#F50D8A"),
            *stack(d, W * 0.085, H * 0.26, W * 0.34, [
                ("Tu Estudio", "title", H * 0.2, dict(font="Georgia", color="#FFFFFF", align="left",
                                                      size=fs(d, 0.18))),
                ("Bodas · Comuniones · Deporte · Eventos", "body", H * 0.12, dict(color="#C9C9C9",
                                                                                  align="left")),
                ("www.tuweb.com", "body", H * 0.1, dict(color="#F50D8A", align="left", bold=True))])]


def miniatura_yt(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#05070F"
    return [P(d, W * 0.4, 0, W * 0.6, H),
            R(d, 0, 0, W * 0.52, H, "#05070F"),
            R(d, W * 0.05, H * 0.62, W * 0.2, H * 0.09, "#F50D8A", radius=H * 0.02),
            T(d, "NUEVO VÍDEO", "body", W * 0.05, H * 0.635, W * 0.2, H * 0.06, color="#FFFFFF", bold=True),
            T(d, "5 TRUCOS PARA FOTOS DE BODA", "title", W * 0.05, H * 0.12, W * 0.44, H * 0.46,
              color="#FFFFFF", font="Impact", bold=False, align="left", size=fs(d, 0.17), line=1.0),
            V(ARROW, W * 0.05, H * 0.78, W * 0.12, W * 0.072, "#F50D8A")]


def tarjeta_regalo(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#1F2A44"
    s = min(W, H)
    return [R(d, W * 0.06, H * 0.08, W * 0.88, H * 0.84, "#00000000", stroke="#C9A86A",
              stroke_w=max(2.0, s * 0.008)),
            V(SPARKLE, W * 0.5 - s * 0.06, H * 0.12, s * 0.12, s * 0.12, "#C9A86A"),
            *stack(d, W * 0.1, H * 0.28, W * 0.8, [
                ("Tarjeta regalo", "title", H * 0.12, dict(font="Gabriola", color="#FFFFFF", bold=False,
                                                           size=fs(d, 0.2))),
                ("SESIÓN DE FOTOS", "subtitle", H * 0.07, dict(color="#C9A86A", spacing=3.0)),
                ("Válida para una sesión de 1 hora en exterior\ncon 25 fotos editadas", "body", H * 0.12,
                 dict(color="#D8DCE6", line=1.3)),
                ("Para: ____________________", "body", H * 0.06, dict(color="#FFFFFF")),
                ("Válida hasta el 31/12/2026 · tuweb.com", "body", H * 0.05, dict(color="#9BA3B5"))],
                gap=H * 0.015)]


def tarjeta_minimal(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    return [R(d, 0, H * 0.9, W, H * 0.1, "#F50D8A"),
            *stack(d, W * 0.08, H * 0.18, W * 0.84, [
                ("NOMBRE APELLIDO", "title", H * 0.16, dict(color="#05070F", align="left", spacing=2.0,
                                                            size=round(H * 0.1, 1))),
                ("Fotógrafo de eventos", "body", H * 0.12, dict(color="#F50D8A", align="left",
                                                                size=round(H * 0.06, 1)))]),
            T(d, "600 000 000\nhola@tuweb.com\ntuweb.com · @tuestudio", "body", W * 0.08, H * 0.5, W * 0.84,
              H * 0.34, color="#4A4A4A", align="left", size=round(H * 0.055, 1), line=1.3)]


# ================================================================== IMPRESIÓN

def poster_expo(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#ECE8E1"
    return [P(d, W * 0.07, H * 0.05, W * 0.86, H * 0.6),
            *stack(d, W * 0.07, H * 0.69, W * 0.86, [
                ("MIRADAS", "title", H * 0.1, dict(font="Bahnschrift", color="#05070F", align="left",
                                                   size=fs(d, 0.17), spacing=6.0)),
                ("Exposición de fotografía", "subtitle", H * 0.05, dict(color="#05070F", align="left",
                                                                        font="Georgia", italic=True))]),
            R(d, W * 0.07, H * 0.86, W * 0.86, max(2.0, H * 0.002), "#05070F"),
            T(d, "Del 3 al 28 de febrero · Casa de la Cultura · Entrada libre", "body", W * 0.07, H * 0.88,
              W * 0.86, H * 0.06, color="#05070F", align="left", upper=True, spacing=1.5)]


def flyer_evento(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    s = min(W, H)
    return [R(d, 0, 0, W, H * 0.5, "#05070F"),
            P(d, W * 0.1, H * 0.08, W * 0.8, H * 0.5, radius=s * 0.02),
            *stack(d, W * 0.1, H * 0.62, W * 0.8, [
                ("JORNADA DE PUERTAS ABIERTAS", "title", H * 0.1, dict(color="#05070F", font="Bahnschrift "
                                                                       "SemiBold Condensed",
                                                                       size=fs(d, 0.09))),
                ("Ven a conocer el estudio y llévate un retrato gratis", "body", H * 0.07, dict(color="#4A4A4A")),
                ("SÁBADO 14 · 10:00 – 14:00", "subtitle", H * 0.06, dict(color="#F50D8A", bold=True)),
                ("Calle Mayor 12 · tuweb.com", "body", H * 0.05, dict(color="#4A4A4A"))], gap=H * 0.01)]


def recordatorio_dl(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FBF7F1"
    return [P(d, 0, 0, W, H * 0.55),
            V(SWASH, W * 0.25, H * 0.585, W * 0.5, W * 0.06, "#C9A86A"),
            *stack(d, W * 0.1, H * 0.63, W * 0.8, [
                ("Recuerdo de mi\nprimera comunión", "subtitle", H * 0.13, dict(font="Gabriola", color="#4F6D8C",
                                                                               size=fs(d, 0.15))),
                ("Sofía", "title", H * 0.09, dict(font="Georgia", color="#2C3E50")),
                ("23 de mayo de 2026", "body", H * 0.06, dict(color="#5F7488"))])]


# ================================================================== COPIAS DE FOTO

def polaroid(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    m = W * 0.07
    return [P(d, m, m, W - 2 * m, W - 2 * m),
            T(d, "Verano 2026 ♥", "subtitle", m, W - m + H * 0.05, W - 2 * m, H - W - H * 0.08,
              font="Ink Free", color="#2B2B2B", size=fs(d, 0.09))]


def marco_pie(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    m = W * 0.08
    return [P(d, m, m, W - 2 * m, H * 0.72),
            *stack(d, m, m + H * 0.75, W - 2 * m, [
                ("Ana & Pablo", "subtitle", H * 0.07, dict(font="Gabriola", color="#2B2B2B", size=fs(d, 0.1))),
                ("12 · 09 · 2026", "body", H * 0.05, dict(color="#8A7350", spacing=3.0))])]


def fotomaton(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#05070F"
    m = W * 0.06
    n = 4
    ph = (H * 0.86 - (n + 1) * m * 0.6) / n
    els = [P(d, m, m * 0.6 + i * (ph + m * 0.6), W - 2 * m, ph) for i in range(n)]
    els.append(T(d, "Boda de Ana & Pablo · 12.09.26", "body", m, H * 0.89, W - 2 * m, H * 0.08,
                 color="#FFFFFF", font="Georgia", italic=True))
    return els


def recuerdo_evento(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    m = W * 0.05
    return [P(d, m, m, W - 2 * m, H * 0.8),
            R(d, m, H * 0.84, W - 2 * m, H * 0.12, "#F50D8A"),
            T(d, "CARRERA SOLIDARIA 2026", "subtitle", m * 1.5, H * 0.855, (W - 3 * m) * 0.65, H * 0.09,
              color="#FFFFFF", bold=True, align="left", font="Bahnschrift SemiBold Condensed"),
            T(d, "tuweb.com", "body", m + (W - 2 * m) * 0.65, H * 0.87, (W - 2 * m) * 0.33, H * 0.06,
              color="#FFFFFF", align="right")]


# ================================================================== REDES SOCIALES

def cita_minimal(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#F4EFE9"
    return [R(d, W * 0.1, H * 0.2, W * 0.1, max(3.0, H * 0.006), "#F50D8A"),
            *stack(d, W * 0.1, H * 0.25, W * 0.8, [
                ("Las mejores fotos no se posan, se viven.", "title", H * 0.42, dict(font="Georgia",
                                                                                     color="#1F2A44",
                                                                                     align="left",
                                                                                     bold=False)),
                ("@tuestudio", "body", H * 0.06, dict(color="#5B6275", align="left", spacing=1.0))], gap=H * 0.05)]


def novedad(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#05070F"
    s = min(W, H)
    return [P(d, 0, 0, W, H * 0.64),
            O(d, W - s * 0.3, H * 0.52, s * 0.24, s * 0.24, "#F50D8A"),
            T(d, "NUEVO", "subtitle", W - s * 0.3, H * 0.52 + s * 0.085, s * 0.24, s * 0.07, color="#FFFFFF",
              bold=True, spacing=2.0),
            *stack(d, W * 0.07, H * 0.68, W * 0.6, [
                ("Álbumes de lino", "title", H * 0.14, dict(font="Georgia", color="#FFFFFF", align="left")),
                ("Ya disponibles en 20×20 y 30×30", "body", H * 0.1, dict(color="#AEB4C2", align="left"))])]


def carrusel_portada(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#F50D8A"
    s = min(W, H)
    return [T(d, "5", "title", W * 0.08, H * 0.08, W * 0.4, H * 0.24, color="#FFFFFF", font="Arial Black",
              align="left", size=fs(d, 0.3)),
            *stack(d, W * 0.08, H * 0.34, W * 0.84, [
                ("consejos para que tus fotos de boda sean perfectas", "title", H * 0.36,
                 dict(color="#FFFFFF", align="left", font="Georgia", size=fs(d, 0.09))),
                ("Desliza para verlos", "body", H * 0.06, dict(color="#FFFFFF", align="left"))], gap=H * 0.04),
            V(ARROW, W * 0.08, H * 0.86, s * 0.14, s * 0.084, "#FFFFFF")]


def story_pregunta(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#1F2A44"
    s = min(W, H)
    return [P(d, 0, 0, W, H * 0.45),
            R(d, W * 0.08, H * 0.4, W * 0.84, H * 0.36, "#FFFFFF", radius=s * 0.05),
            *stack(d, W * 0.12, H * 0.43, W * 0.76, [
                ("PREGÚNTAME", "body", H * 0.035, dict(color="#F50D8A", bold=True, spacing=3.0)),
                ("¿Qué te gustaría saber sobre tu sesión de fotos?", "title", H * 0.2,
                 dict(color="#1F2A44", font="Georgia", size=fs(d, 0.08)))]),
            T(d, "Responde en mis historias", "body", W * 0.1, H * 0.82, W * 0.8, H * 0.05, color="#FFFFFF")]


def oferta(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#05070F"
    s = min(W, H)
    return [O(d, W * 0.5 - s * 0.36, H * 0.12, s * 0.72, s * 0.72, "#F50D8A"),
            *stack(d, W * 0.5 - s * 0.32, H * 0.12 + s * 0.16, s * 0.64, [
                ("-30%", "title", s * 0.26, dict(color="#FFFFFF", font="Arial Black", size=fs(d, 0.2))),
                ("EN SESIONES DE FAMILIA", "body", s * 0.12, dict(color="#FFFFFF", bold=True, spacing=1.0))]),
            *stack(d, W * 0.08, H * 0.12 + s * 0.78, W * 0.84, [
                ("Solo este mes · plazas limitadas", "body", H * 0.06, dict(color="#C9C9C9", upper=True,
                                                                             spacing=1.5))])]


def galeria_grid(d):
    W, H = d["w"], d["h"]
    d["bg"] = "#FFFFFF"
    g = min(W, H) * 0.02
    c = (W - 4 * g) / 3
    els = [P(d, g + i * (c + g), g + j * (c + g), c, c) for j in range(2) for i in range(3)]
    els += stack(d, g * 2, 2 * c + 3 * g + H * 0.03, W - 4 * g, [
        ("Momentos de la temporada", "title", H * 0.1, dict(font="Georgia", color="#05070F",
                                                            size=fs(d, 0.06))),
        ("Bodas · Comuniones · Deporte", "body", H * 0.05, dict(color="#F50D8A", upper=True, spacing=2.0))])
    return els


# ================================================================== registro

# nombre: (función, tamaño recomendado, grupo)
NEW = {
    "Save the date": (save_the_date, "Instagram vertical 4:5", "Bodas"),
    "Invitación floral": (boda_floral, "A5 (flyer)", "Bodas"),
    "Menú de boda": (boda_menu, "Flyer DL", "Bodas"),
    "Tarjeta de agradecimiento": (boda_gracias, "A6 (postal)", "Bodas"),
    "Story de boda": (boda_story, "Story / Reel", "Bodas"),
    "Collage de boda": (boda_collage, "Instagram cuadrado", "Bodas"),
    "Recordatorio de comunión": (comunion, "A6 (postal)", "Comunión y bautizo"),
    "Recordatorio DL": (recordatorio_dl, "Flyer DL", "Comunión y bautizo"),
    "Bautizo": (bautizo, "Instagram vertical 4:5", "Comunión y bautizo"),
    "Cumpleaños infantil": (cumple_infantil, "Instagram cuadrado", "Cumpleaños y fiestas"),
    "Flyer de fiesta": (fiesta_flyer, "A5 (flyer)", "Cumpleaños y fiestas"),
    "Felices fiestas": (navidad, "Instagram cuadrado", "Cumpleaños y fiestas"),
    "Día de la madre": (dia_madre, "Instagram vertical 4:5", "Cumpleaños y fiestas"),
    "Resultado del partido": (deporte_resultado, "Instagram cuadrado", "Deporte"),
    "Jugador del partido": (deporte_mvp, "Instagram vertical 4:5", "Deporte"),
    "Próximo partido": (deporte_proximo, "Story / Reel", "Deporte"),
    "Foto de equipo": (deporte_equipo, "Publicación de Facebook", "Deporte"),
    "Tarifas": (precios, "Instagram vertical 4:5", "Para tu negocio"),
    "Antes y después": (antes_despues, "Instagram cuadrado", "Para tu negocio"),
    "Reserva tu fecha": (reserva, "Story / Reel", "Para tu negocio"),
    "Galería entregada": (galeria_lista, "Instagram cuadrado", "Para tu negocio"),
    "Opinión de cliente": (testimonio, "Instagram cuadrado", "Para tu negocio"),
    "Portada de Facebook": (portada_fb, "Portada de Facebook", "Para tu negocio"),
    "Miniatura de YouTube": (miniatura_yt, "Miniatura de YouTube", "Para tu negocio"),
    "Tarjeta regalo": (tarjeta_regalo, "A6 (postal)", "Para tu negocio"),
    "Tarjeta minimalista": (tarjeta_minimal, "Tarjeta de visita", "Para tu negocio"),
    "Cita minimalista": (cita_minimal, "Instagram cuadrado", "Redes sociales"),
    "Novedad": (novedad, "Instagram vertical 4:5", "Redes sociales"),
    "Portada de carrusel": (carrusel_portada, "Instagram vertical 4:5", "Redes sociales"),
    "Story de preguntas": (story_pregunta, "Story / Reel", "Redes sociales"),
    "Oferta": (oferta, "Instagram cuadrado", "Redes sociales"),
    "Galería de 6 fotos": (galeria_grid, "Instagram vertical 4:5", "Redes sociales"),
    "Póster de exposición": (poster_expo, "Póster A3", "Impresión"),
    "Flyer de puertas abiertas": (flyer_evento, "A5 (flyer)", "Impresión"),
    "Polaroid": (polaroid, "10 × 15 cm", "Copias de foto"),
    "Foto con pie": (marco_pie, "13 × 18 cm", "Copias de foto"),
    "Tira de fotomatón": (fotomaton, "10 × 15 cm", "Copias de foto"),
    "Recuerdo de evento": (recuerdo_evento, "13 × 18 cm", "Copias de foto"),
}

OLD_GROUPS = {"Post de evento": "Redes sociales", "Invitación": "Bodas", "Tarjeta de visita": "Para tu negocio",
              "Collage de 3 fotos": "Redes sociales", "Cita": "Redes sociales",
              "Flyer de servicios": "Para tu negocio", "Presentación": "Presentaciones",
              "Historia de boda": "Bodas", "Banner profesional": "Para tu negocio"}

GROUPS = ["Bodas", "Comunión y bautizo", "Cumpleaños y fiestas", "Deporte", "Para tu negocio", "Redes sociales",
          "Impresión", "Copias de foto", "Presentaciones"]


def register():
    G.TEMPLATE_GROUP.update(OLD_GROUPS)
    for name, (fn, size, group) in NEW.items():
        G.TEMPLATES[name] = fn
        G.TEMPLATE_SIZE[name] = size
        G.TEMPLATE_GROUP[name] = group
