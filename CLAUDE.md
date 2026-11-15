# EventEdits

Programa de escritorio para Windows (Python + PySide6) que edita por lotes sesiones de fotos de eventos:
exposición, grano, enderezado, sugerencia de formato, descarte, ZIP de originales, subida a Drive y
autoactualización. Uso personal de David (fotógrafo de eventos y bodas; Sony ILCE-6700, RAW `.ARW`).
Interfaz, comentarios y mensajes en español.

## Comandos

```
pip install -r requirements.txt
python app.py                          # interfaz
python app.py --selftest               # prueba de humo (la usa el build); log en %TEMP%\eventedits_selftest.log
python -m unittest discover -s tests   # 7 pruebas, sin fotos reales
```

`opencv-python-headless` debe ser < 5: la 5.x no trae los Haar cascades que usa la detección de caras.

## Arquitectura

- `engine.py` — todo el procesado, sin interfaz. Dos fases:
  - `analyze()` rápido a media resolución (≈3 s/foto): exposición (`estimate_ev` + `_face_ev`), giro
    (`detect_tilt`), nitidez, dHash, caras, recortes (`plan_crops`). `cull()` marca descartes; no mueve nada.
  - `export()` → `develop()` a resolución completa (≈25 s/foto en 2 núcleos): NLM en luma, croma a media
    resolución, contraste, nitidez, giro; luego recortes y tamaños. `move_discards()`, `archive_originals()`,
    `write_report()`.
- `app.py` — ventana `Main`, hilo `Worker` (modos `analyze` / `export`), subida a la nube, `selftest()`.
- `drive.py` — OAuth de escritorio con PKCE y Drive v3 por REST con `urllib` (sin dependencias).
- `updater.py` — consulta `releases/latest` del repo, descarga, verifica SHA-256 y se sustituye con un `.bat`.
- `.github/workflows/build.yml` — al empujar una etiqueta `v*`: pruebas, PyInstaller onefile, selftest del
  `.exe`, release con `EventEdits.exe` y `.sha256`. La versión sale de la etiqueta (sobrescribe `version.py`).

## Reglas que no se rompen

- Nunca borrar originales: los descartes se **mueven** a `Descartadas/`; el ZIP no toca los RAW.
- Nada se mueve en el análisis; solo al exportar y tras la revisión del usuario.
- No subir fotos reales al repositorio (es público). Las pruebas generan imágenes sintéticas.
- No guardar credenciales en el repo (`client_secret*.json`, `drive_token.json` están en `.gitignore`).

## Estado (v0.1.0, sin publicar)

Probado: motor e interfaz (en Linux, sin pantalla), 7 pruebas, y una sesión real de 90 RAW de un cumpleaños
en discoteca (ISO 2500–6400, 3–5 pasos subexpuesta, flash directo).

Sin probar: el `.exe` en Windows (el workflow aún no ha corrido), el actualizador en real, Drive contra una
cuenta real (solo contra un servidor simulado en las pruebas).

Pendiente para publicar: crear el repo público `davidcaptart-glitch/eventedits` (el nombre está en
`updater.REPO`), `git push`, `git tag v0.1.0 && git push origin v0.1.0`.

## Lo que se aprendió con la sesión real

- La exposición por percentil 99 se queda corta con sujetos oscuros; por eso existe `_face_ev` (lleva la piel
  a `FACE_TARGET`). Tope `EV_MAX = 5.0`; `EV_HOPELESS = 7.5` solo cazó un fotograma negro.
- El umbral absoluto de nitidez descartaba fotos válidas algo blandas: ahora manda el relativo a la mediana de
  la sesión (`SENS`). En exigencia Normal no descartó ninguna de esa sesión.
- El enderezado funciona bien con paredes de listones, barandillas y cortinas (0,4°–4,8°). Estima
  `desviación = giro + convergencia·x` con ajuste robusto; varía ±0,4° según qué líneas detecte.
- Con más de +4 EV el grano es el límite: bien para web, piel lavada al 100 %.

## Mejoras pendientes (ideas, sin priorizar)

- Ignorar archivos ya editados en la carpeta (`*_editada.jpg`, salidas propias) para no reeditarlos.
- Exposición y giro editables por foto en la tabla antes de exportar; vista previa grande al hacer clic.
- Detección de ojos cerrados (no incluida; haría falta un modelo de puntos faciales).
- Nitidez medida en las caras, no en la zona más nítida del encuadre.
- Mejor detector de caras (YuNet de OpenCV Zoo) en lugar de Haar.
- Reducción de grano de más calidad para las fotos muy subexpuestas; velocidad (procesar en paralelo).
- Recorte sugerido: hoy puntúa `f⁴/√área` con interés = bordes + brillo + caras; afinado con pocas fotos.
- Corrección de distorsión y viñeteo de la lente; balance de blancos con luces de colores.
- Conservar EXIF en los JPG exportados.
- Amazon Photos no tiene API pública: solo se copia a una carpeta que su app sincroniza.
- Firmar el `.exe` para evitar el aviso de SmartScreen.
