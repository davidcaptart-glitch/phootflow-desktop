# Técnicas de retoque de Whitewall frente a PhootFlow

Artículo: «Técnicas avanzadas de retoque» (Whitewall Magazine, Katharina Wergen):
https://www.whitewall.com/es/magazine/inspiration/fotografia-para-avanzados/postproduccion-y-edicion-de-imagenes/tecnicas-avanzadas-de-retoque

El artículo describe cuatro bloques: separación de frecuencias, dodge and burn (sobreexponer/subexponer), retoque del
cabello y retoque de belleza (dientes, ojos, ojeras, retoque con IA tipo Radiant Photo). Después, añadir o quitar
elementos (grafitis, cubo de basura, fondo desenfocado). No da valores concretos; solo el 50 % de gris de la capa de
dodge and burn.

Leyenda: **Ya está**: existe y hace lo que pide el artículo. **Parcial**: existe, pero solo a mano o falta una parte.
**Falta**: no hay nada equivalente.
«Auto» quiere decir que, una vez añadida la capa o el ajuste, la zona se encuentra sola (IA o malla facial) y no hace
falta pincel.

## 1. Tabla: técnica → estado → propuesta

| # | Técnica del artículo | Estado | Dónde está en PhootFlow | ¿Sola o a mano? | Propuesta para que sea casi automática |
|---|---|---|---|---|---|
| 1 | **Separación de frecuencias: alisar el tono de la piel** (capa de baja frecuencia) | Ya está | Edits › Filtro › «Suavizar piel», capa `skin` (`edits_engine.apply_skin`: filtro guiado + textura fina devuelta; máscara de piel con IA `skin_mask`) | Auto (máscara por IA); la capa se añade a mano o con «Retoque de retrato en un clic» | Sugerirla cuando hay caras grandes (ver §3, «Sugerencias») y aplicarla por lotes desde Events (§3, punto B). |
| 2 | **Separación de frecuencias: quitar granos, arrugas y poros con tampón en la capa de detalle** | Parcial | Edits › Corrector (J) con modos «Color y textura / Solo color / Solo textura» (`_apply_op`, `op["freq"]`); el origen se busca solo (`find_source`). Parche (Mayús+J). Tampón (S) | Manual: hay que pintar cada grano | **Quitar imperfecciones automático**: detector de granos y manchas dentro de la máscara de piel (diferencia de gaussianas sobre L de Lab y rojez en a*, tamaño entre ≈0,4 y 2 % del ancho de la cara, lejos de ojos, cejas y labios con `part_mask`) → genera operaciones `heal` normales con `find_source`, en la capa de retoque. Se pueden borrar o ajustar luego como si se hubieran pintado. Botón «Quitar imperfecciones» en Filtro y en el retoque en un clic. |
| 3 | **Dodge and burn para dar volumen a la cara** | Ya está | Edits › capa `portrait`, regulador «Volumen (dodge and burn)» (`SCULPT`, `_blobs`): luces y sombras en los puntos típicos de la cara | Auto en todas las caras | Nada que añadir; como mucho, poner el valor por defecto según el tamaño de la cara. |
| 4 | **Dodge and burn manual (pintar luces y sombras)** | Ya está | Edits › Ojos y luz (O): Aclarar / Blanquear / Oscurecer (`apply_light`). Studio: máscara de pincel con exposición | Manual (es lo que pide el artículo) | — |
| 5 | **Ajustes locales tipo Lightroom: degradado radial o lineal** (p. ej. aclarar el primer plano) | Ya está | Studio › máscaras `radial`, `linear`, `brush`, rango de luz y de color; IA: persona, cara, piel, pelo, ropa, fondo; `sky` aproximado (`masks.py`). Edits › Volver a iluminar (`relight`) | Manual (se coloca la máscara) | **Sugerencia de cielo**: si `masks._sky` cubre más del 15 % y está muy claro o recortado, ofrecer «Recuperar el cielo» (máscara Cielo con −0,5 EV, +neblina y algo de saturación del azul). **Sugerencia de sujeto**: «Luz al sujeto» = máscara Persona +0,2 EV y su inversa −0,2 EV. |
| 6 | **U Point de DxO** (punto que aclara u oscurece lo parecido de alrededor) | Ya está | Studio › máscara «Punto de control (U Point)» (`masks.point_alpha`) | Manual | — |
| 7 | **Viñeteado** (oscurecer esquinas) | Ya está | Studio › «Viñeta»; Edits › Filtro › Viñeteado (`edits_adjust.vignette`); presets de Studio y estilos de Events | Auto si va en el preset o el estilo | — |
| 8 | **Retoque del cabello: quitar pelos sueltos** | Parcial | Edits › Corrector, Tampón y Relleno con IA (LaMa, `_apply_lama`) | Manual y lento: cada pelo con el pincel | **Pincel «Pelos sueltos»** (semiautomático): se pasa una brocha gruesa por el borde del pelo y solo se borran las líneas finas que haya dentro (filtro de crestas / black-hat sobre L, restando la máscara de pelo principal de MediaPipe), rellenando con `_inpaint_low` o LaMa. Detección 100 % automática en todo el contorno: posible después, con más riesgo de comerse mechones buenos. |
| 9 | **Retoque del cabello: volumen y mechones con Licuar** | Ya está | Edits › Licuar (Mayús+Ctrl+X): empujar, inflar, encoger (`apply_liquify`) | Manual (el artículo también) | — |
| 10 | **Pelo: brillo y suavidad** (no está en el artículo, pero es parte del retoque de pelo) | Ya está | Edits › Filtro › «Pelo sedoso» y «Barba» (`edits_adjust.hair`, máscara de pelo/barba por IA) | Auto | — |
| 11 | **Blanquear dientes** | Ya está | Edits › capa `portrait`, «Blanquear dientes» (40 por defecto, máscara `teeth` de la malla facial). También Ojos y luz › Blanquear | Auto | **Sugerirlo solo cuando hace falta**: medir b* (amarillo) de la zona `teeth`; si es alto, la sugerencia dice «Dientes amarillentos». |
| 12 | **Ojos: nitidez y luz del iris, venas rojas, blanco turbio** | Ya está | Edits › `portrait`: «Iris: luz y nitidez», «Blanco de los ojos» y color del iris | Auto | **Sugerencia de «Ojos apagados»**: L media del iris baja frente a la piel → subir «Iris: luz y nitidez». |
| 13 | **Agrandar ojos o iris** | Parcial (a propósito) | Licuar › Inflar, manual | Manual | No automatizar: el propio artículo dice que queda poco natural. |
| 14 | **Quitar ojeras** (dodge suave bajo los ojos) | Parcial | Edits › Ojos y luz › «Aclarar (ojeras)»; corrector en modo «Solo color» | Manual: **la capa `portrait` no las trata** | **Regulador «Ojeras» en el retrato automático**: zona bajo el párpado inferior a partir de la malla facial (MediaPipe ya está cargada para `portrait`), aclarar L solo en sus sombras y llevar a*/b* hacia el color de la mejilla (como «Solo color»), con borde muy suave. 20–30 por defecto. Esfuerzo bajo. |
| 15 | **Labios** (resaltarlos) | Ya está | Edits › `portrait`: intensidad y color de labios | Auto | — |
| 16 | **Retoque de belleza con IA en un clic** (Radiant Photo) | Ya está | Edits › Filtro › «Retoque de retrato en un clic» (`portrait_one_click`: piel, retrato, pelo, barba, enfoque de la cara) | Un clic, foto a foto | **Por lotes en Events** (§3, punto B): hoy solo se puede foto a foto. |
| 17 | **Quitar elementos (grafiti, cubo de basura): clonar / relleno generativo** | Parcial | Edits › Relleno según contenido con IA (G, LaMa local), Tampón (S), Parche, Corrector | Manual: hay que pintar lo que se quita | **Sugerir distracciones de tipo persona**: personas pequeñas en los bordes, que no son el sujeto (segmentación de persona + sujeto principal de `engine.main_subject`), con un botón «Quitar» que lanza LaMa sobre su máscara. Objetos sueltos (papeleras, carteles): sin un detector de objetos no compensa; seguir a mano. |
| 18 | **Fondo muy desenfocado (bokeh) para ocultar lo que molesta** | Ya está | Edits › Filtro › «Desenfocar fondo», capa `background` (persona por IA, `apply_background`) | Auto (máscara IA) | Sugerirlo en retratos con fondo muy nítido (nitidez del fondo mucho mayor que la de la cara: `ph.face_sharp` frente a `ph.sharp`, ya medido). |

### Lo que se pidió revisar además del artículo

| Problema | Estado | Dónde | Propuesta |
|---|---|---|---|
| Horizonte torcido | Ya está y automático | `engine.detect_tilt` en el análisis (Events lo aplica); Studio › Auto en el recorte; marco con «Nivel» (`cropview.py`) | — |
| Dominantes de color | Parcial | Events: la piel que se va a magenta o morado se corrige al exportar (`engine.py`, `SKIN_CR/SKIN_CB`); Studio: temperatura y matiz a mano; el Auto de Studio (`SE.auto_settings`) **no toca el balance** | **Balance automático sugerido** en Studio: a partir del tono de la piel (si hay caras) o de los grises de la escena, proponer `temp`/`tint`. Se ofrece como sugerencia («La foto tira a verde: −12 matiz») y no se aplica sola, porque con la luz de los eventos la dominante suele ser buscada. |
| Ojos cerrados | Ya está (aviso) | `engine.analyze` → `ph.warn`, descarte en `cull` | — |
| Cara menos nítida que el fondo | Ya está (aviso) | `engine.analyze` → «La cara está menos nítida que el fondo» | Enlazar el aviso con «Enfoque suavizado (solo la cara)» de Edits. |

## 2. Resumen

- Lo **ya cubierto y automático** es la mayor parte del retoque de belleza: suavizar la piel, el volumen de la cara,
  dientes, ojos, labios, pelo y barba, el fondo desenfocado y el enderezado.
- **Falta o es solo manual**: quitar granos uno a uno (2), ojeras (14), pelos sueltos (8), quitar distracciones (17)
  y el balance de color (dominantes).
- **El hueco más grande no es una técnica, es el flujo**: todo lo automático está en Edits, **foto a foto**, y nada
  avisa de qué foto lo necesita. Para una boda de 1 500 fotos lo que más ahorra es que PhootFlow diga «estas 40 tienen
  caras en primer plano, ¿retoco el retrato?» y lo haga en lote.

## 3. Cómo hacerlas casi automáticas

### A. Sugerencias en Edits y Studio (`retouch_hints.py`, módulo nuevo sin Qt)

`hints(img, key) -> list[dict]`: cada sugerencia lleva `id`, texto («Piel con imperfecciones», «Ojeras marcadas»,
«Dientes amarillentos», «Ojos apagados», «Cielo quemado», «Tira a verde») y lo que hace al aplicarla (capas o ajustes
que se añaden). Se calcula a 1 024 px en un hilo al abrir la foto, reutilizando `masks.faces_scaled`, `part_mask` y
`masks._sky` (ya en caché por `key`).

- **Edits**: una franja fina sobre el lienzo con chips «Ojeras · Aplicar», «Imperfecciones · Aplicar», «Aplicar
  todo». Al aplicar se crean capas normales, que se pueden ajustar o borrar.
- **Studio**: lo mismo con «Cielo» y «Balance», que se convierten en máscaras o ajustes de sliders.
- Archivos: `retouch_hints.py` (nuevo), `edits.py` (franja), `studio.py` (franja; **tiene cambios sin commit de otra
  sesión**, se tocaría al final y con cuidado), `tests/test_retouch_hints.py` (caras y cielos sintéticos).
- Esfuerzo: **medio** (1–2 días con todas las detecciones; la franja con 2–3 sugerencias, medio día).

### B. Retoque de retrato por lotes desde Events

Las capas `skin` y `portrait` no dependen de la posición en la foto (por eso `propagate.EDITS_COPYABLE` ya las copia
de una foto a otra), así que se pueden crear en lote:

- En la revisión de Events, acción «Retocar retratos…»: elige las fotos conservadas con caras grandes (`ph.faces` y
  el tamaño de cara que ya mide el análisis), crea en cada una su documento de Edits con «Suavizar piel» + «Retrato
  automático» (+ «Ojeras» cuando exista) mediante `EE.add_layer`/`EE.save`, y la exportación ya las recoge
  (`EE.pending_events` / `export_for_events`). Opción en ⚙ para hacerlo siempre al exportar.
- Archivos: `review.py` o `app.py` (acción), `edits_engine.py` (función `auto_portrait_doc(folder, path)` sin Qt),
  pruebas. **No toca `engine.py`**, que está cambiando la sesión del recorte inteligente.
- Esfuerzo: **bajo-medio** (medio día). Hay que comprobar el tiempo por foto a tamaño completo (la malla facial y la
  segmentación están en caché a 1 024 px, pero el filtro guiado de la piel se hace a tamaño completo).

### C. Ojeras en el retrato automático

Nueva clave `undereye` en `PORTRAIT_DEFAULTS` y `PORTRAIT_LABELS`; zona `undereye` en `masks.py` (polígono entre el
párpado inferior y el pómulo en la malla de 468 puntos); en `apply_portrait`, aclarar L solo en las sombras de esa
zona y llevar a*/b* hacia la mejilla. Archivos: `edits_engine.py`, `masks.py`, `edits_ui.py` (sale sola como
regulador si se genera desde las etiquetas), pruebas con una cara sintética. Esfuerzo: **bajo** (2–3 h).

### D. Quitar imperfecciones automático

`blemish_ops(img, key) -> list[op]` en `edits_engine.py`: máscara de piel de la cara sin ojos, cejas, labios ni
fosas nasales; candidatos por diferencia de gaussianas en L (puntos oscuros) y en a* (rojeces); filtrar por tamaño,
redondez y contraste; como mucho N por cara. Cada candidato se convierte en una operación `heal` con
`find_source`, en una capa «Imperfecciones» que se puede repasar. Esfuerzo: **medio** (1 día con los ajustes de
umbrales; hay que probarlo con fotos reales de David fuera del repo).

### E. Más arriesgadas (después)

- **Pelos sueltos** (pincel semiautomático): medio-alto.
- **Distracciones de tipo persona** con LaMa: medio-alto; depende de `engine.main_subject` (sesión del recorte).
- **Balance automático sugerido**: medio; conviene probarlo con sesiones reales antes de activarlo por defecto.

## 4. Recomendación: por dónde empezar

1. **C. Ojeras en el retrato automático**: es lo único del artículo que falta en el retoque en un clic, es pequeño
   y no toca archivos que estén cambiando otras sesiones.
2. **B. Retoque de retrato por lotes desde Events**: es lo que más tiempo ahorra en una boda; reutiliza todo lo
   que ya existe.
3. **A. Franja de sugerencias en Edits** con ojeras, dientes, ojos e imperfecciones (cuando exista D).
4. **D. Quitar imperfecciones automático**, y luego Studio (cielo y balance) y lo de §3.E.

## 5. Hecho (25-11-2026)

- **C. Ojeras**: regulador «Ojeras» (`undereye`, 30) en el retrato automático de Edits (`undereye_alpha`,
  `_undereye` en `edits_engine.py`). Las capas de retrato guardadas antes valen 0 y no cambian.
- **B. Dodge and burn automático en Events («Retocar retratos»)**: chip en ⚙ › Al exportar, encendido por
  defecto (apagado en los perfiles Deporte, Entrega rápida y Archivo completo). Las fotos con caras de al menos el
  5 % del ancho llevan piel + retrato automático (volumen/dodge and burn, ojeras, dientes, ojos y labios)
  **sobre el revelado de Events** (`engine.portrait_fx`): el mismo aspecto que el resto de la sesión, y en todos
  los formatos y el B/N. En la revisión, el selector «Retrato: de la sesión / sin retocar / retocar» de cada foto,
  con vista previa. Coste medido en 24 MP sintéticos: 3–4,5 s por foto en el peor caso (piel en toda la foto);
  ahora la piel se calcula solo en su caja, así que en fotos reales debería ser menos (sin medir).
- **D. Quitar imperfecciones automático** (Edits › Filtro): granos, puntos y rojeces → trazos del corrector en la
  capa «Imperfecciones (automático)»; también dentro de «Retoque de retrato en un clic». Deja fuera lunares
  grandes, pelos y arrugas (manchas alargadas). **No va en Events** hasta probarlo con fotos reales.
- **A. Franja de sugerencias en Edits**: al abrir una foto sin retocar, si ve granos, ojeras marcadas o dientes
  amarillentos lo dice, con «Retoque en un clic» y «Solo imperfecciones».
- Pruebas: `tests/test_retouch_auto.py` (11, sintéticas).
- Sin probar con fotos reales: el aspecto de las ojeras, los umbrales de granos (`find_blemishes`), ojeras
  (`UNDEREYE_HINT` = 6) y dientes (`TEETH_HINT` = 16), y el tiempo de exportación con muchas caras.

Quedan lo de §3.E (pelos sueltos, distracciones, balance sugerido) y las sugerencias de Studio (cielo).
