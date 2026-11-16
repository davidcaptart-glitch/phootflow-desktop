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
python -m unittest discover -s tests   # 19 pruebas, sin fotos reales
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
- `studio.py` / `studio_engine.py` — entorno **Studio** (tipo Lightroom): `StudioView` (biblioteca, lienzo con
  antes/después, histograma, paneles de sliders, historial, presets, exportar) y el revelado no destructivo
  `render(lin, ajustes)`. Ajustes por foto en `<carpeta>/.phootflow/studio/<foto>.json`. Proxy de 2048 px y
  borrador de 1024 px mientras se arrastra. `Shell` (app.py) aloja Events (`Main`) y Studio con el interruptor.
- `luts.py` — LUT .cube por tipo de sesión; `eyes.py` (MediaPipe) y `scene_ai.py` (CLIP, onnxruntime): IA local.
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

## Estado (v0.4.0, en curso)

Events: edición uniforme por sesión, LUT por tipo, IA de tipo de evento (15/15 sesiones reales) y ojos cerrados.
Studio: primera versión; fórmulas propias, por afinar con las de LightCraft (Apache-2.0,
github.com/storytold/lightcraft, crates/pipeline). Sin probar: sesión de 2000 fotos, Drive con cuenta real.
