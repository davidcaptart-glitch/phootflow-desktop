# PhootFlow

Programa de escritorio para Windows (Python + PySide6) que edita por lotes sesiones de fotos de eventos de
forma desatendida (500–2000 fotos): exposición, grano, enderezado, recorte sugerido, descarte, estilos por tipo
de evento y aprendidos, B/N editorial, XMP para Lightroom, ZIP de originales, Drive y autoactualización.
Antes «EventEdits». Uso personal de David (fotógrafo de eventos, bodas, deporte; Sony ILCE-6700 `.ARW`,
también Nikon `.NEF`). Interfaz, comentarios y mensajes en español.

## Comandos

```
pip install -r requirements.txt
python app.py
python app.py --selftest               # prueba de humo (la usa el build); log en %TEMP%\phootflow_selftest.log
python -m unittest discover -s tests   # 143 pruebas, sin fotos reales ni red
```

- `opencv-python-headless` < 5 (la 5.x no trae los Haar cascades).
- En el PC de David hay **Smart App Control** activo: bloqueó la DLL de `rawpy` 0.28 (0.27.0 sí carga) y
  bloqueará un `.exe` sin firmar. Por eso existe el modo de instalación «source» (`instalar.ps1`).

## Arquitectura

- `engine.py` — procesado sin interfaz. `analyze()` a media resolución (≈1 s/foto con 3 hilos): exposición
  (`estimate_ev` + `_face_ev`), giro, nitidez, dHash, caras (YuNet, `assets/face_detection_yunet_2023mar.onnx`;
  Haar de respaldo), avisos (ojos, cara blanda), rasgos de escena, recortes (`smartcrop.py`). `cull()` marca descartes.
  `export_many()` revela en paralelo (≈15 s/foto con 2 hilos) aplicando `Settings.style`.
  Caché de análisis y reanudación en `<salida>/.phootflow/` (`save_session`/`load_session`, `exportadas.txt`).
  Formatos (`RAW_EXT`, `IMG_EXT`, `file_filter`): RAW de todas las marcas que lee LibRaw, JPEG, PNG, TIFF (16 bits
  sin pasar por 8, `_read_16bit`), WebP, BMP, AVIF y HEIC/HIF (`pillow-heif`, opcional: `HEIF`). Un RAW que LibRaw
  no revela (NEF «High Efficiency»/TicoRAW, cámaras nuevas) se convierte con el Adobe DNG Converter si está
  instalado (`dng_converter`, `converted_dng`: DNG 1.4 en `<carpeta>/.phootflow/dng`, `_DNG_OF`); si no, se usa su JPEG
  incrustado (`PREVIEW_ONLY`, aviso `PREVIEW_WARN`, y `Main._warn_dng` ofrece descargarlo).
  Salida: `Entrega/Color` y `Entrega/Blanco y negro` (`<foto>.jpg`, tamaño de entrega = max; `delivery_path`,
  `save_delivery`), `Otros tamaños/<n> px/…` y `Formatos/<proporción>/`. Edits sustituye la foto en Entrega/Color.
- `smartcrop.py` — recorte inteligente de Events (sin Qt): personas y pelota con YOLOX-tiny (Apache-2.0, ONNX de la
  release oficial, 20 MB en `%APPDATA%\PhootFlow\modelos`, SHA-256; `E.setup_detector`/`E.DET`, ≈90 ms por foto en
  un hilo, sin cambio medible a 3 hilos), caras YuNet y saliencia clásica (residuo espectral + enfoque, `saliency`).
  `analyze` guarda `objects`, `face_boxes`, `sal` (mapa de 48 px en base64) y `aspect` en la caché; `SC.plan` da el
  recorte de cada formato de `RATIOS` (contiene lo destacado con aire, área mínima por tipo en `RULES`, sin partir
  a nadie) y elige formato y orientación (`ph.best`, `ph.crop_why`). Tras detectar el tipo de sesión,
  `E.smart_crops` lo rehace sin abrir las fotos: deporte = pelota + jugadores que la disputan; boda = la pareja;
  resto = persona, grupo o saliencia. Respeta «Personalizado» y lo revisado. Revisión: ★ = sugerido
  (`E.suggested_crop`), con qué encuadra. Análisis antiguos sin `aspect` conservan sus recortes.
  Composición (de dZoom, Format y Club de Fotografía): `_anchor` (la pelota, los ojos, el sujeto o lo que destaca) y
  `_placement` colocan cada recorte con tercios, punto áureo, simetría (retrato de frente), aire hacia donde mira la
  cara (`face_yaw` con los puntos de YuNet, 5.º valor de `face_boxes`) y horizonte en un tercio (`horizon`,
  `ph.horizon`). `explain` dice qué reglas cumple y por qué (además: rellenar el encuadre, espacio negativo,
  simplicidad, número impar, orientación, sin recortar): `ph.crop_rules` del sugerido, `E.crop_rules(ph, formato)`
  para otro; se ve en la revisión (bloque «Composición» y ayuda de cada formato), en la ficha y en `informe.csv`
  (columna `composicion`). Líneas guía, ángulo y color no se miden: los decide la toma, no el recorte.
- `dcp.py` — perfiles de cámara DCP (sin Qt): `_develop_raw` revela en el RGB de la cámara (`output_color=raw`,
  balance de la toma) y `dcp.apply` aplica ForwardMatrix (interpolada por temperatura entre los dos iluminantes),
  HueSatMap, BaselineExposureOffset y LookTable en ProPhoto lineal → sRGB lineal, por franjas. La ProfileToneCurve no
  se usa (la curva base es la filmic de `tone()`/`_tonemap_lut`). Perfiles en `%ProgramData%\Adobe\CameraRaw\
  CameraProfiles` (los instala el DNG Converter), `%APPDATA%\Adobe\…` y `%APPDATA%\PhootFlow\perfiles`; se buscan
  por `UniqueCameraModel`. Por defecto en todo PhootFlow, `E.CAMERA_PROFILE = dcp.AUTO`: el estilo con que se
  disparó (`E.camera_style`, de las MakerNotes: Sony CreativeStyle — «Off» en automático inteligente —, Nikon
  PictureControl, Fujifilm FilmMode) -> su perfil «Camera …» (`dcp.style_profile`; los de B/N, en color con el
  Estándar; sin estilo, el Estándar; sin perfiles de cámara, Adobe Standard; sin ninguno, LibRaw). Con un NEF
  convertido a DNG se lee el estilo del NEF original (`_develop_raw(src=…)`). Única opción en la ventana: chip
  «Color de la cámara» (⚙ › Revelado, `Main.c_prof`; apagado = `dcp.NONE`), guardado como `cam_prof` en la sesión
  (`Settings.camera_profile`). Graphics (`load_rgb`) revela los RAW igual que Events (`load_linear` + `tone`).
- `session_file.py` — «<sesión>.phootflow» en la carpeta de la sesión (JSON, salida relativa): con doble clic abre
  PhootFlow en esa sesión (`SF.register()` asocia la extensión en HKCU al arrancar; `main()` lee el argumento y
  `Shell.open_session_file`). Se escribe tras analizar y exportar y oculta las carpetas `.phootflow`.
  `Originales.zip` va junto a «Editadas» (`E.originals_zip`; el de dentro de Editadas de versiones anteriores se
  traslada), con RAW, JPG gemelo y XMP; `originales.json` guarda la fecha de cada uno para que `restore_originals`
  los devuelva tal cual y el análisis valga («Recuperar sesión desde el ZIP», o se ofrece al abrir).
- `session_presets.py` — «Ajustes guardados» de Events (sin Qt): perfiles con nombre de LUT, revelado, B/N,
  formatos, cómo exportar, nube y calidad (`KEYS`; las claves que faltan no se tocan). 5 predefinidos en `BUILTIN`;
  los tuyos en `%APPDATA%\PhootFlow\ajustes_sesion\*.json` (uno con el nombre de un predefinido lo sustituye).
  Cada sesión guarda los suyos en `<salida>/.phootflow/ajustes.json` (al aplicar un perfil y al analizar/exportar)
  y `open_session` los recupera. En la ventana: `Main.snapshot`/`apply_snapshot`, tarjeta en el panel ⚙ y
  selector en «Abrir carpeta» (`ModeDialog.preset_choice`: los de la sesión, los últimos usados o un perfil).
- `session_setup.py` (sin Qt) + `session_setup_ui.py` — panel «¿Cómo quieres editar esta sesión?» de Events: al
  elegir una carpeta nueva en «Abrir carpeta» (antes de analizar; con trabajo guardado se reanuda sin él) y luego
  con «¿Cómo editar esta sesión?» de la barra lateral, el menú ⚙, Ctrl+K o Ctrl+Mayús+E (`Main.open_setup`). A la
  izquierda las preconfiguraciones (las de la sesión, las últimas usadas y `SP.all_presets`; «Guardar como
  perfil…»); a la derecha, en tarjetas con icono y una frase y en chips: tipo de sesión (`SS.TYPES`: Automático con
  la IA, boda, comunión/bautizo, fiesta, discoteca, deporte, retrato, exterior y los estilos aprendidos;
  `SS.propose` propone el resto sin tocar nube, calidad, hilos, perfil de cámara ni LUT), recorte inteligente y
  formatos, retratos, «Mejorar con IA», desenfoque de fondo, color de la cámara, look, LUT, B/N, descartes, grano y
  qué exportar. Trabaja sobre `Main.snapshot`/`apply_snapshot` (`Main.apply_setup`; con la sesión ya analizada
  rehace al momento el recorte y la IA de las no revisadas). Claves nuevas `crop` y `enhance` (`Settings.smart_crop`
  y `auto_enhance`, chips en ⚙ › Al exportar): tras el análisis, `SS.apply_rules` deja la entrega en «Original» si
  no hay recorte automático y marca `ph.enhance` en las conservadas con `ph.fix`. Carpetas, hilos y carpeta de la
  nube siguen en ⚙.
- `styles.py` — presets por tipo de evento, `detect_scene()` heurístico, `apply_color()` y aprendizaje:
  `learn_style()` (parejas original↔edición, curva L por CDF + color) y `learn_from_finals()` (solo acabadas:
  distribución objetivo). Estilos en `%APPDATA%\PhootFlow\estilos\*.json`.
- `app.py` — ventana `Main` (barra lateral + rejilla con `ThumbDelegate` + vista previa), `Worker`
  (`analyze`/`export`/`auto`), `LearnDialog`, banner de actualización, `selftest()`.
- `progress_panel.py` — `ExportProgress`: ventana no modal al exportar o procesar todo (pasos con ○/◐/✓, barra
  general con «quedan ~», fotos en curso y terminadas con su miniatura, Cancelar, Abrir la carpeta, seguir en
  segundo plano; «Ver progreso» la vuelve a abrir). El `Worker` emite `plan`, `step` e `item`
  (`E.export_many(on_event=…)`, `EE.events_todo`).
- `theme.py` — QSS: interfaz negro azulado del logo (`#05070F`), lienzo neutro, chips y degradado magenta→violeta.
- `icons.py` (iconos SVG propios, `icon(nombre)`) y `ui.py` (chips, `ComboChips` ligado a un QComboBox, `SideSheet` ⚙,
  `CommandPalette` Ctrl+K, `FlowLayout`). En Events los controles de siempre guardan el estado; los chips son su cara visible.
- `updater.py` — modos `exe` (sustituye el .exe con un .bat), `source` (descarga `PhootFlow-src.zip`,
  verifica SHA-256, copia encima, pip si cambió `requirements.txt`, reinicia) y `dev` (clon git: solo avisa).
- `studio.py` / `studio_engine.py` — entorno **Studio** (tipo Lightroom): `StudioView` (biblioteca, lienzo con
  antes/después, histograma, paneles de sliders, historial, presets, exportar) y el revelado no destructivo
  `render(lin, ajustes)`. Ajustes por foto en `<carpeta>/.phootflow/studio/<foto>.json`. Las retocadas para
  Events (`_events_override`) se exportan con `export_events`: recorte de Studio en Entrega, los
  demás formatos en Formatos, como el resto; `fit_ratio` mantiene la proporción aunque cambie el giro. Proxy de 2048 px y
  borrador de 1024 px mientras se arrastra. `Shell` (app.py) aloja Events (`Main`) y Studio con el interruptor.
  Máscaras (`masks.py`): zonas en `m["parts"]` de cualquier tipo que suman, restan o intersecan (`build` combina,
  `build_one` una zona), `invert` por zona e `invert_all` para la máscara; color dentro de la máscara (HSL por
  colores, gradación por tonos, presencia y curva tonal); guías sobre el lienzo (`_paint_gizmo`: líneas de la lineal,
  elipse de la radial, círculo del punto) que se arrastran. Botón «B/N» (V) en la barra. En la revisión de Events,
  las fotos de Studio se recortan y pasan a B/N guardándolo en su edición de Studio.
  Color selectivo (botón «Color», C; `splash*` en los ajustes, `splash_stage`): los tonos elegidos con el cuentagotas
  o los botones de color (OkLCh, `splash_hues`) y/o una máscara (`splash_mode`/`splash_mask`) se quedan en color y
  el resto pasa al B/N del estilo elegido; «Proteger la piel», y el ojo muestra la selección (`_splash_view`).
- `edits.py` / `edits_engine.py` — entorno **Edits** (tipo Photoshop): `EditsView` (herramientas, lienzo con zoom,
  capas, propiedades, historial, exportar) y el motor de capas paramétricas (`compose`, `Compositor` con caché
  por prefijo): corrector/tampón/relleno, piel, ojos y luz, licuar, fondo (persona IA, objeto GrabCut o color),
  texto, logo e imagen (arrastrada desde el Explorador; copia en `edits/capas/`). Trabajo en
  `<carpeta>/.phootflow/edits/<foto>.json`. Fotomontaje: las capas de imagen van en su sitio de la pila (los
  ajustes de encima las afectan), con máscara de capa, máscara de recorte (`clip`), transformación libre
  (`quad`, Ctrl+T), lienzos en blanco (`new_canvas`, en `edits/lienzos/`, fuera de Events), capas de pintura
  (`paint`: pincel B y borrador E, presión de tableta, caché incremental) grupos (`group` + `parent`, Ctrl+G), efectos de capa (`fx`: sombra, resplandor, desenfoque y movimiento)
  y rayos de luz (`rays`). Retrato: capa `portrait` (automática en todas las caras: dientes, ojos, iris, labios,
  colorete, igualar tono y dodge and burn guiado `SCULPT`), Filtro › «Retoque de retrato en un clic», selecciones
  y máscaras por partes con IA (`{"type": "part"}`, `EE.PARTS`; la barba es `masks.beard_alpha`), ajustes
  `sharpen`, `vignette` y `hair` (Pelo y barba) y corrector con `freq` color/textura. Las imágenes y pinturas se componen solo en su caja; la caché retoma el paso guardado
  más alto y guarda el de debajo de la capa elegida (`Compositor.focus`); al arrastrar, borrador a 1024 px. `attach_studio_button` añade «Editar en Edits» a Studio sin tocar
  studio.py.
  Interfaz al estilo de PhotoCraft (ArtCraft): `edits_ui.py` (panel de capas, Curvas/Niveles, pestañas) y
  `edits_adjust.py` (capas de ajuste y modos de fusión portados de PhotoCraft, MIT/Apache-2.0); selecciones
  y máscaras con `edits_engine.selection_mask`.
  Herramientas al estilo del Photoshop actual, sin IA en la nube: ajustes Claridad, Borrar neblina (canal oscuro
  + filtro guiado), Granulado, Volver a iluminar (foco movible con V), Reajuste (`A.LOOKS` con un regulador) y
  Relleno de color; varita (`wand_mask`, a 1024 px), bote de pintura (capa «solid» con máscara de varita), parche
  (op `heal` con `sel` y `src`), «Armonizar» en capas de imagen (`harmonize`, Lab), pinceles de medios naturales
  (`BRUSHES`, `st["b"]`) y exportar «Ampliar ×2» (`upscale`, Lanczos + borde, por franjas). Pruebas en
  `tests/test_edits_tools.py`.
- `graphics.py` / `graphics_engine.py` — entorno **Graphics** (Canva + Illustrator): diseños JSON en
  `%APPDATA%\PhootFlow\diseños`, varias páginas, kit de marca (`diseños\marca.json`), plantillas, capas,
  grupos, imán, texto curvo, pluma y nodos, Buscatrazos, QR, quitar fondo
  (PNG aparte en `diseños\recortes`), vectorizar (OpenCV) y PDF de imprenta (sangrado, marcas, CMYK simple).
  Un solo pintor (`paint_element`) para lienzo y exportación.
  Páginas seguidas en el lienzo como en Canva: la activa en (0, 0) con sus elementos vivos, las demás encima y
  debajo como imágenes en caché (`Canvas.page_rect`/`_page_image`); clic o desplazarse a otra la activa sin saltar
  (`keep_view_switch`). Rueda = desplazar, Ctrl+rueda = zoom. Cuadrícula para ordenarlas (`graphics_pages.py`).
  El historial no guarda la página activa (`_snap`); deshacer va a la página que cambia.
  Texto enriquecido: `el["runs"]` (tramos con b/i/u/s/color/k; si no suman `el["text"]` se ignoran) y
  `el["list"]` (viñetas o números con sangría francesa), en `_text_layout` con FormatRange; contornos por color
  (`text_color_paths`). Editor en Propiedades: `graphics_richtext_ui.py`.
  `graphics_magic.py` (+ `_ui`): «Estilos» (paleta + fuentes por papel de cada color, y espejo) en el panel Diseño,
  y «Diseñar con las fotos de la sesión»: lee el análisis de Events (`Editadas/.phootflow`), ordena las fotos
  (nitidez, ojos, caras; descartadas al final; gemelas por dHash detrás; usa la editada de Entrega/Color si
  existe) y propone carrusel, álbumes y plantillas rellenas (vistas previas con las miniaturas de Events).
  Lienzo al estilo Canva: barra contextual fija encima (`TaskBar`: fuente, tamaño, color, N K S, lista, efectos,
  recortar, editar foto, voltear, transparencia, animar, posición), escribir en el lienzo con doble clic
  (`InlineTextEditor` en graphics_richtext_ui), recortar la foto con doble clic (`Canvas.cropping`, rueda = zoom)
  y ajustes rápidos (`el["adj"]`, `G.adjust_rgb`; `flip_x`/`flip_y`). `ImageCache` decodifica (o revela el RAW)
  una vez a ≥ 2048 px como base y saca de ahí tamaños y filtros; el lienzo pide sin esperar (`wait=False`, hilo y
  señal `_Loader`). Imán con varios y al escalar, distancias al mover, Alt+arrastrar duplica, Ctrl+C/V con el
  portapapeles de Windows (`CLIP_MIME`), selector de color con cuentagotas, Subidos (`diseños\subidos`, RAW
  enlazados en `enlaces.json`), paletas de marca (`brand["palettes"]`, `M.style_from_palette`), capas con
  miniatura/ojo/candado y grupos plegables, «Redimensionar» con reflujo (`G.reflow`) y atajos en todo el editor
  (`shortcut_table`). Animaciones de entrada (`el["anim"]`, `G.ANIMS`, `anim_state`), duración por página
  (`pages[i]["dur"]`), vista previa ▶ y vídeo MP4 (`export_video`, mp4v de OpenCV) o GIF/WebP animado.
  Foto + formas/textos: «Recortar con la forma» (intersect) o «Restar de la foto» (`G.cut_image`, `el["cuts"]`
  en coordenadas de la foto, siguen al escalarla; `cut_src` para «Quitar recortes»). «Intercambiar» (`swap_places`):
  dos fotos cambian de foto, el resto de sitio. Asas: `ElementItem.shape` une las piezas (con addEllipse sobre
  addRect la regla par-impar dejaba un agujero en cada asa y no se podía escalar).
  `graphics_templates.py`: 47 plantillas propias por grupos (bodas, comunión, fiestas, deporte, negocio, redes,
  impresión, copias) con fuentes de Windows; se registran en `G.TEMPLATES`/`TEMPLATE_SIZE`/`TEMPLATE_GROUP`.
  Pestaña **Elementos** (`graphics_elements.py` + `graphics_online.py`): gráficos y stickers de Iconify (solo
  colecciones de licencia comercial; GPL y animadas fuera), convertidos a vector editable con `svg_to_vector`
  (degradados/máscaras → PNG), fotos de Openverse (CC comercial) y Pexels (clave del usuario), fuentes de Google
  Fonts (TTF); caché en `%APPDATA%\PhootFlow\online`, crédito de autor y licencia en `el["credit"]`.
  Fuentes: catálogo completo de Fontsource (~2 000, Google y otras libres) + Fontshare (ITF FFL), cada nombre
  pintado con una muestra TTF que se baja al verse; «Mías» (`online\fuentes\_mias`, licencia «libre/personal/?»
  sacada del léeme en `licencias.json`) por «Añadir fuentes…» o vigilando Descargas (`QFileSystemWatcher`: el
  zip que bajas de DaFont entra solo; DaFont prohíbe buscar/descargar por programa en su robots.txt, no se
  raspa). Opción de instalar en Windows por usuario (HKCU, sin admin). Exportar avisa si hay fuente «personal».
  Efectos de texto (`graphics_textfx.py`, `el["tfx"]`; panel en `graphics_textfx_ui.py`): sombra, elevar, eco,
  contorno, fondo, desalineado, hueco, neón, distorsión, resplandor y 3D, más 18 estilos (fuente + color + efecto).
  Los desenfocados no pasan a contornos en el PDF.
- `export_formats.py` — formatos de salida comunes (sin Qt salvo `qimage_to_array`): imagen (JPG, PNG, WebP, AVIF,
  TIFF LZW 8/16 bits o CMYK, GIF, BMP, PDF de imagen), GIF/WebP animado (páginas = fotogramas) y el vectorizado
  (`graphics_engine.vectorize`) a SVG, PDF y EPS escritos a mano (CMYK real con `k`/`setcmykcolor`) y DXF R12 en mm
  (una capa por color). Graphics: `ExportDialog`/`EXPORT_FORMATS` (incluye «Vectorizado para imprenta»: la página
  entera a trazados con `VectorizeDialog(export_only=True)` al ancho real en mm). Edits: `EE.FORMATS` ampliado,
  `EE.render_final` y botón «Vectorizar para imprenta…».
- `fx_engine.py` / `fx_ui.py` — efectos «de plugin» (ideas de plugins de Photoshop, código propio): ampliar con IA
  (Real-ESRGAN General x4v3, BSD-3, ONNX de Qualcomm AI Hub, 5 MB en `modelos/`, bloques de 128 px con solape; sin
  modelo, Lanczos), partículas (nieve, lluvia, confeti, pétalos, bokeh, destellos, polvo, corazones), filtros
  artísticos (acuarela, óleo, cómic, tinta, lápiz, pop art), texturas, efectos tipo Eye Candy (cromo, oro, fuego,
  rayos, hielo, neón), bordes artísticos y doble exposición. Dónde: Studio (máscara «Punto de control», U Point, en
  `masks.py`; exportar ×2 con IA), Graphics (foto: `el["art"]`/`el["edge"]`; Objeto: Ampliar con IA, Texturas,
  partículas y efectos; Ver: Retícula y guías `d["grid"]`, como GuideGuide; ficha de capa, como Ink, en Capas y
  Ctrl+Mayús+I), Edits (capas `particles` y `artistic`, Doble exposición con capas normales, exportar ×2 con IA,
  ficha de capa al pasar el ratón).
- `bokeh.py` — desenfocar fondo y efecto velocidad (sin Qt). Sujeto: persona con `masks.segment` (IA), si no
  GrabCut desde la saliencia de `smartcrop`, o una máscara dada; a ≤1024 px (`subject_lowres`, caché por foto) y borde
  ajustado al pelo con filtro guiado rápido (coeficientes a ≤2048 px aplicados por franjas a resolución completa).
  Fondo rellenado bajo el sujeto (pull-push, sin halos), profundidad aproximada (`depth_map`: lejos del sujeto y por
  encima de sus pies = más), 4 capas de disco en luz lineal con las luces realzadas (discos de bokeh) a ≤1600 px.
  `speed`: barrido direccional (`auto_angle` por la forma y la posición del sujeto, o a mano) y estela detrás.
  Cantidad 0..100 (radio máx. 3 % del lado largo); `LEVELS` Suave/Medio/Fuerte/Muy fuerte (≈f/4…f/1.2). 26 MP:
  ≈1,3 s la máscara + ≈2,2 s el efecto. Studio: sección «Desenfocar fondo y velocidad» en Detalle (`bokeh`,
  `bokeh_hl`, `speed`, `speed_auto`/`speed_angle`, `speed_trail`, `blur_subject`/`blur_mask`; `SE.blur_stage` tras
  las máscaras y antes del LUT). Edits: capa `speed`. Events: regla de sesión «Desenfocar fondo» (⚙ › Al exportar,
  `Settings.bokeh`, clave `bokeh` de los ajustes guardados, por defecto «No») solo en fotos con sujeto claro
  (`BK.events_eligible`: 1–2 personas o caras, no grupos ni paisajes); en la revisión, por foto `ph.bokeh` (de la
  sesión, `BOKEH_NONE` o un nivel) y `ph.speed` (manual), guardados en revision.json; `E.blur_fx` al exportar.
- `cropview.py` — marco de recorte interactivo común (Studio con R; revisión de Events con C: recorte «Personalizado»).
  Enderezar es parte del marco (`on_angle`): arrastrar fuera del marco gira, Ctrl+arrastrar o «Nivel» traza el
  horizonte (`level_angle`), vista previa instantánea girando la imagen mostrada (`paint_image`, `cover_scale`)
  hasta que el anfitrión revela y llama a `rendered(ángulo)`. Regulador, Auto (`E.detect_tilt`) y 0° junto al
  recorte en Studio (sincronizado con Geometría) y en la revisión (`ph.angle`, o el ángulo de Studio).
- `propagate.py` — propagar la edición de Studio/Edits de una foto a otras de Events (`Propagator`: grupos de ajustes y capas de Edits).
  La revisión de Events se guarda sola en `<salida>/.phootflow/revision.json` (`save_review`/`load_review`) y sigue por la primera sin revisar.
- `luts.py` — LUT .cube por tipo de sesión y biblioteca incluida (`assets/luts/biblioteca.npz`, 73 LUT de
  t3mujinpack MIT, Street Monochrome y slay CC BY, rodaje-filtros CC0; rutas simbólicas `@phootflow/…` que
  `load` entiende; salen como comunes en Studio y Events; se regenera con `tools/crear_biblioteca_luts.py`;
  licencias en `assets/luts/LICENCIAS.md`, fuentes en `docs/fuentes_luts.md`); `eyes.py` (MediaPipe) y `scene_ai.py` (CLIP, onnxruntime): IA local.
- `drive.py` — OAuth de escritorio con PKCE y Drive v3 por REST.
- `license.py` — licencias: `ensure()` en `main()` (pide clave, valida en la Edge Function `licencias` de
  Supabase, token Ed25519 verificado en Python puro, 7 días sin conexión, huella del equipo), `watch()` cada 6 h,
  `show_status()` (botón «Licencia» del pie de la barra lateral). Servidor en `servidor_licencias/`,
  administración con `admin_licencias_gui.py` (ventana) o `admin_licencias.py` (consola); ninguno va en el paquete. Ver `LICENCIAS.md`. El selftest no usa licencia.
- `.github/workflows/build.yml` — en etiquetas `v*`: pruebas, PyInstaller, selftest del .exe, zip de código,
  sumas y release.

## Reglas que no se rompen

- Nunca borrar originales: los descartes se **mueven** a `Descartadas/`; el ZIP no toca los RAW. Única excepción,
  opcional y desactivada por defecto («Quitar los RAW tras el ZIP», `trash_originals`): los conservados sin error
  que el ZIP guarda idénticos (tamaño + CRC, `trash_archived_originals`) van a la **Papelera**, nunca borrado directo.
  La otra, también opcional y con confirmación cada vez: «Borrar las descartadas» (`delete_discards`) borra para
  siempre los originales descartados y lo que haya en `Descartadas/` (nunca los de una foto conservada).
- Nada se mueve en el análisis. Los XMP nunca pisan uno existente.
- No subir fotos reales al repositorio (es público). Las pruebas usan imágenes sintéticas.
- No guardar credenciales en el repo (la clave secreta de Supabase va en `%APPDATA%\PhootFlow\admin.env`).
  No borrar la tabla `ajustes` de Supabase: guarda la clave de firma de las licencias.
- Los detectores de OpenCV se crean en el hilo principal (`prepare_detectors`) y se reparten por hilo:
  un `CascadeClassifier` creado en un hilo secundario tumba el proceso al salir (Windows).
  No usar `os._exit` con la ventana Qt viva: también da access violation; salir con `sys.exit`.

## Estado (v0.4.0, en curso)

Events: edición uniforme por sesión, LUT por tipo, IA de tipo de evento (15/15 sesiones reales) y ojos cerrados.
Studio: primera versión; fórmulas propias, por afinar con las de LightCraft (Apache-2.0,
github.com/storytold/lightcraft, crates/pipeline). Sin probar: sesión de 2000 fotos, Drive con cuenta real.
