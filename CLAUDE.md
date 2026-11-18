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
python -m unittest discover -s tests   # 55 pruebas, sin fotos reales ni red
```

- `opencv-python-headless` < 5 (la 5.x no trae los Haar cascades).
- En el PC de David hay **Smart App Control** activo: bloqueó la DLL de `rawpy` 0.28 (0.27.0 sí carga) y
  bloqueará un `.exe` sin firmar. Por eso existe el modo de instalación «source» (`instalar.ps1`).

## Arquitectura

- `engine.py` — procesado sin interfaz. `analyze()` a media resolución (≈1 s/foto con 3 hilos): exposición
  (`estimate_ev` + `_face_ev`), giro, nitidez, dHash, caras (YuNet, `assets/face_detection_yunet_2023mar.onnx`;
  Haar de respaldo), avisos (ojos, cara blanda), rasgos de escena, recortes. `cull()` marca descartes.
  `export_many()` revela en paralelo (≈15 s/foto con 2 hilos) aplicando `Settings.style`.
  Caché de análisis y reanudación en `<salida>/.phootflow/` (`save_session`/`load_session`, `exportadas.txt`).
- `styles.py` — presets por tipo de evento, `detect_scene()` heurístico, `apply_color()` y aprendizaje:
  `learn_style()` (parejas original↔edición, curva L por CDF + color) y `learn_from_finals()` (solo acabadas:
  distribución objetivo). Estilos en `%APPDATA%\PhootFlow\estilos\*.json`.
- `app.py` — ventana `Main` (barra lateral + rejilla con `ThumbDelegate` + vista previa), `Worker`
  (`analyze`/`export`/`auto`), `LearnDialog`, banner de actualización, `selftest()`.
- `theme.py` — QSS: interfaz negro azulado del logo (`#05070F`), lienzo neutro, chips y degradado magenta→violeta.
- `icons.py` (iconos SVG propios, `icon(nombre)`) y `ui.py` (chips, `ComboChips` ligado a un QComboBox, `SideSheet` ⚙,
  `CommandPalette` Ctrl+K, `FlowLayout`). En Events los controles de siempre guardan el estado; los chips son su cara visible.
- `updater.py` — modos `exe` (sustituye el .exe con un .bat), `source` (descarga `PhootFlow-src.zip`,
  verifica SHA-256, copia encima, pip si cambió `requirements.txt`, reinicia) y `dev` (clon git: solo avisa).
- `studio.py` / `studio_engine.py` — entorno **Studio** (tipo Lightroom): `StudioView` (biblioteca, lienzo con
  antes/después, histograma, paneles de sliders, historial, presets, exportar) y el revelado no destructivo
  `render(lin, ajustes)`. Ajustes por foto en `<carpeta>/.phootflow/studio/<foto>.json`. Proxy de 2048 px y
  borrador de 1024 px mientras se arrastra. `Shell` (app.py) aloja Events (`Main`) y Studio con el interruptor.
- `edits.py` / `edits_engine.py` — entorno **Edits** (tipo Photoshop): `EditsView` (herramientas, lienzo con zoom,
  capas, propiedades, historial, exportar) y el motor de capas paramétricas (`compose`, `Compositor` con caché
  por prefijo): corrector/tampón/relleno, piel, ojos y luz, licuar, fondo (persona IA, objeto GrabCut o color),
  texto, logo e imagen (arrastrada desde el Explorador; copia en `edits/capas/`). Trabajo en
  `<carpeta>/.phootflow/edits/<foto>.json`. Fotomontaje: las capas de imagen van en su sitio de la pila (los
  ajustes de encima las afectan), con máscara de capa, máscara de recorte (`clip`), transformación libre
  (`quad`, Ctrl+T), lienzos en blanco (`new_canvas`, en `edits/lienzos/`, fuera de Events), capas de pintura
  (`paint`: pincel B y borrador E, presión de tableta, caché incremental) grupos (`group` + `parent`, Ctrl+G), efectos de capa (`fx`: sombra, resplandor, desenfoque y movimiento)
  y rayos de luz (`rays`). Las imágenes y pinturas se componen solo en su caja; la caché retoma el paso guardado
  más alto y guarda el de debajo de la capa elegida (`Compositor.focus`); al arrastrar, borrador a 1024 px. `attach_studio_button` añade «Editar en Edits» a Studio sin tocar
  studio.py.
  Interfaz al estilo de PhotoCraft (ArtCraft): `edits_ui.py` (panel de capas, Curvas/Niveles, pestañas) y
  `edits_adjust.py` (capas de ajuste y modos de fusión portados de PhotoCraft, MIT/Apache-2.0); selecciones
  y máscaras con `edits_engine.selection_mask`.
- `graphics.py` / `graphics_engine.py` — entorno **Graphics** (Canva + Illustrator): diseños JSON en
  `%APPDATA%\PhootFlow\diseños`, varias páginas, kit de marca (`diseños\marca.json`), plantillas, capas,
  grupos, imán, texto curvo, pluma y nodos, Buscatrazos, QR, quitar fondo
  (PNG aparte en `diseños\recortes`), vectorizar (OpenCV) y PDF de imprenta (sangrado, marcas, CMYK simple).
  Un solo pintor (`paint_element`) para lienzo y exportación.
  `graphics_templates.py`: 47 plantillas propias por grupos (bodas, comunión, fiestas, deporte, negocio, redes,
  impresión, copias) con fuentes de Windows; se registran en `G.TEMPLATES`/`TEMPLATE_SIZE`/`TEMPLATE_GROUP`.
  Pestaña **Elementos** (`graphics_elements.py` + `graphics_online.py`): gráficos y stickers de Iconify (solo
  colecciones de licencia comercial; GPL y animadas fuera), convertidos a vector editable con `svg_to_vector`
  (degradados/máscaras → PNG), fotos de Openverse (CC comercial) y Pexels (clave del usuario), fuentes de Google
  Fonts (TTF); caché en `%APPDATA%\PhootFlow\online`, crédito de autor y licencia en `el["credit"]`.
- `cropview.py` — marco de recorte interactivo común (Studio con R; revisión de Events con C: recorte «Personalizado»).
- `propagate.py` — propagar la edición de Studio/Edits de una foto a otras de Events (`Propagator`: grupos de ajustes y capas de Edits).
  La revisión de Events se guarda sola en `<salida>/.phootflow/revision.json` (`save_review`/`load_review`) y sigue por la primera sin revisar.
- `luts.py` — LUT .cube por tipo de sesión; `eyes.py` (MediaPipe) y `scene_ai.py` (CLIP, onnxruntime): IA local.
- `drive.py` — OAuth de escritorio con PKCE y Drive v3 por REST.
- `license.py` — licencias: `ensure()` en `main()` (pide clave, valida en la Edge Function `licencias` de
  Supabase, token Ed25519 verificado en Python puro, 7 días sin conexión, huella del equipo), `watch()` cada 6 h,
  `show_status()` (botón «Licencia» del pie de la barra lateral). Servidor en `servidor_licencias/`,
  administración con `admin_licencias_gui.py` (ventana) o `admin_licencias.py` (consola); ninguno va en el paquete. Ver `LICENCIAS.md`. El selftest no usa licencia.
- `.github/workflows/build.yml` — en etiquetas `v*`: pruebas, PyInstaller, selftest del .exe, zip de código,
  sumas y release.

## Reglas que no se rompen

- Nunca borrar originales: los descartes se **mueven** a `Descartadas/`; el ZIP no toca los RAW.
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
