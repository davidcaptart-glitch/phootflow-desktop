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
python -m unittest discover -s tests   # 11 pruebas, sin fotos reales
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
- `theme.py` — QSS con los colores del logo (`#05070F`, magenta `#F50D8A`, blanco).
- `updater.py` — modos `exe` (sustituye el .exe con un .bat), `source` (descarga `PhootFlow-src.zip`,
  verifica SHA-256, copia encima, pip si cambió `requirements.txt`, reinicia) y `dev` (clon git: solo avisa).
- `drive.py` — OAuth de escritorio con PKCE y Drive v3 por REST.
- `.github/workflows/build.yml` — en etiquetas `v*`: pruebas, PyInstaller, selftest del .exe, zip de código,
  sumas y release.

## Reglas que no se rompen

- Nunca borrar originales: los descartes se **mueven** a `Descartadas/`; el ZIP no toca los RAW.
- Nada se mueve en el análisis. Los XMP nunca pisan uno existente.
- No subir fotos reales al repositorio (es público). Las pruebas usan imágenes sintéticas.
- No guardar credenciales en el repo.
- Los detectores de OpenCV se crean en el hilo principal (`prepare_detectors`) y se reparten por hilo:
  un `CascadeClassifier` creado en un hilo secundario tumba el proceso al salir (Windows).
  No usar `os._exit` con la ventana Qt viva: también da access violation; salir con `sys.exit`.

## Estado (v0.2.0)

Probado en el PC de David: motor con 8 ARW reales del cumpleaños (ISO 2500–6400), selftest, 11 pruebas.
Estilos «Deporte» y «Boda» aprendidos de sus fotos acabadas en Drive (muestra de ≈40 por carpeta, en
`%LOCALAPPDATA%\PhootFlow\aprendizaje`).

Sin probar: el `.exe` en un PC sin Smart App Control, Drive contra cuenta real, sesión completa de 2000 fotos.

## Ideas pendientes

- Curva base «look de cámara» desde el JPEG embebido del RAW (`rawpy.extract_thumb`, igualación de CDF).
- Ojos cerrados con puntos faciales (MediaPipe Face Landmarker, blendshape `eyeBlink`) en lugar de Haar.
- Ráfagas por hora EXIF (≤1 s) + puntuación combinada (nitidez de cara, ojos, exposición).
- Reducción de ruido según el ruido medido de cada foto; balance de blancos con luces de colores.
- Detección del tipo de escena con un modelo (CLIP/SigLIP) en vez de heurística.
- Firmar el `.exe` (Azure Trusted Signing) para evitar SmartScreen y Smart App Control.
