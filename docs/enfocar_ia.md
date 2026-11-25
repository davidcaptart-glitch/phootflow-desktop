# «Mejorar con IA» en PhootFlow: enfocar, caras, ruido y fotos oscuras

Evaluación del 25-11-2026 (sin tocar la app). Objetivo de David: algo como el «Mejorador de imágenes con IA» de
Filmora: un clic que enfoque fotos blandas, restaure caras, quite ruido y artefactos JPEG, aclare las fotos
oscuras y amplíe. Además, Events debería **sugerir** qué fotos de la sesión conviene reparar.
Todo local, sin nube, con `onnxruntime` en CPU.

## Respuesta corta (la honesta)

- **Sí** se puede conseguir que una foto algo blanda *parezca* mucho más nítida, sobre todo las caras. En una
  prueba real (cara con el foco en el balón, no en la cara) GFPGAN devuelve ojos, pestañas, labios y dientes
  nítidos en 1,3 s.
- **No** se recupera el detalle que no quedó grabado: **se inventa**. La IA dibuja pestañas, iris, dientes,
  poros, hilos de la tela o letras bordadas *plausibles*, no los de verdad. En una cara eso puede cambiar un poco
  el gesto o la forma de los ojos («se parece, pero no es del todo él»). En textos y escudos, inventa garabatos.
- Funciona bien con **desenfoque leve** (foco fallado por poco, cara blanda, ruido de ISO alto). Con trepidación
  fuerte o foco muy fallado el resultado se ve artificial o no mejora.
- Ninguna herramienta (Filmora incluida) hace otra cosa: es reconstrucción generativa con una buena conjetura.

## Modelos evaluados

| Modelo | Para qué | Licencia | ONNX (descarga) | Tamaño | ¿Sirve? |
|---|---|---|---|---|---|
| **Real-ESRGAN General x4v3** | Enfocar foco fallado, ruido, JPEG, ampliar | BSD-3 | Ya en `fx_engine.UPSCALE_URL` (Qualcomm AI Hub) | 5 MB | **Sí: motor principal** |
| Real-ESRGAN x4plus | Igual, más fino | BSD-3 | `https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/real_esrgan_x4plus/releases/v0.67.0/real_esrgan_x4plus-onnx-float.zip` | 64 MB | No: 20× más lento, no mejora a x4v3 |
| **GFPGAN 1.4** | Caras blandas | Apache-2.0 (código) — ver aviso | `https://huggingface.co/facefusion/models-3.0.0/resolve/main/gfpgan_1.4.onnx` | 340 MB | **Sí, opcional** |
| RestoreFormer++ | Caras blandas | Apache-2.0 (código) — ver aviso | `https://huggingface.co/facefusion/models-3.0.0/resolve/main/restoreformer_plus_plus.onnx` | 294 MB | Alternativa; más contraste y más artefactos, 2,5× más lento |
| NAFNet (OpenCV Zoo, `deblurring_nafnet_2025may.onnx`) | Trepidación / movimiento | MIT | `https://huggingface.co/opencv/deblurring_nafnet/resolve/main/deblurring_nafnet_2025may.onnx` | 92 MB | Solo en recortes: ganancia pequeña y muy lento |
| NAFNet-DeBlur width64 (Qualcomm) | Trepidación | MIT | `…/nafnet_deblur/releases/v0.67.0/nafnet_deblur-onnx-float.zip` (entrada fija 360×640) | 272 MB | Igual que el anterior, 2× más rápido |
| NAFNet-DeNoise (SIDD, Qualcomm) | Ruido | MIT | `…/nafnet_denoise/releases/v0.67.0/nafnet_denoise-onnx-float.zip` | 464 MB | No probado: pesado; x4v3 ya limpia el ruido |
| Restormer (motion/defocus) | Desenfoque y movimiento | MIT | Sin ONNX (habría que exportar con PyTorch) | ~100 MB | Más adelante; muy lento en CPU |
| Retinexformer | Poca luz | MIT (código); datos LOL de investigación | Sin ONNX | — | No por ahora |
| SwinIR | Ampliar / ruido / JPEG | Apache-2.0 | Sin ONNX oficial | — | No aporta frente a x4v3 y es más lento |
| ~~CodeFormer~~ | Caras | S-Lab 1.0, **no comercial** | — | — | Descartado |
| ~~GPEN~~ | Caras | El autor retiró modelos «por problemas comerciales» | — | — | Descartado |
| ~~DFDNet~~ | Caras | CC BY-NC-SA, **no comercial** | — | — | Descartado |
| ~~Zero-DCE / Zero-DCE++ / SCI~~ | Poca luz | **Sin licencia** en el repositorio (= todos los derechos reservados) | — | — | Descartados |

**Aviso sobre las caras (GFPGAN y RestoreFormer++).** El código es Apache-2.0, pero:
1. El `LICENSE` de GFPGAN incluye componentes de terceros con licencias no comerciales (DFDNet, CC BY-NC-SA, y la
   licencia de NVIDIA de StyleGAN2 para sus operaciones CUDA; la arquitectura «clean» de la v1.3/1.4 no usa esas
   operaciones, pero el aviso está ahí).
2. Ambos se entrenaron con **FFHQ**, cuyas imágenes son CC BY-NC-SA 4.0. Si unos pesos entrenados con datos NC
   heredan la restricción no está resuelto legalmente; se usan en productos comerciales (FaceFusion, ComfyUI…),
   pero es zona gris.
3. Los ONNX de `facefusion/models-3.0.0` no declaran licencia propia (son conversiones de los pesos oficiales).

Recomendación: **no incluirlos en el paquete**; descargarlos solo cuando el usuario active «Restaurar caras», con
una línea que lo diga y el SHA-256 fijado (como `ensure_upscaler`). Para el uso de David (editar sus encargos) el
riesgo es bajo; para vender PhootFlow con ellos, mejor consultarlo. Real-ESRGAN (BSD-3) y NAFNet/Restormer (MIT)
no tienen este problema.

## Cómo se probó

Scripts en el scratchpad de la sesión (no en el repo). PC de David: 12 hilos, `onnxruntime` 1.31 CPU.

1. **Recortes con verdad-terreno**: foto real de deporte reducida ×0,5 (más nítida que el 100 %), 3 recortes de
   512 px con más detalle + un patrón sintético (texto, rayas, textura). Cada uno se emborrona de 4 formas con algo
   de ruido y JPEG 92: gaussiano σ 1,5 (foco algo fallado), disco r 3 (desenfoque óptico), movimiento 9 y 15 px.
   Se restaura con: máscara de enfoque (referencia clásica), NAFNet (dos versiones) y el «truco» con Real-ESRGAN
   (reducir ×0,5 → ampliar ×4 con IA → reducir a ×1). Se mide PSNR/SSIM y se miran tiras al 100 %.
2. **Caras reales**: 13 caras de 180–490 px detectadas con YuNet en 5 fotos de David, alineadas a la plantilla
   FFHQ de 512 px con los 5 puntos de YuNet, restauradas y devueltas con una máscara elíptica difuminada.
3. **Foto oscura**: recorte bajado 3 EV con ruido de disparo y lectura (ISO alto), JPEG 90, y recuperado.
4. **Tiempos**: bloque de 1024² de la foto real, extrapolado a 6192×4128 (A6700, 25,6 MP).

## Resultados

### Tiempo por foto de 26 MP (CPU, 12 hilos)

| Método | Tiempo |
|---|---|
| Máscara de enfoque | ≈1 s |
| **Real-ESRGAN x4v3, truco ×0,5→×4→×1** | **≈65–75 s** |
| Real-ESRGAN x4plus, mismo truco | ≈26 min |
| NAFNet Qualcomm (width64) | ≈7,5 min |
| NAFNet OpenCV | ≈15 min |
| **GFPGAN 1.4, por cara** | **≈1,3 s** (RestoreFormer++ ≈3 s) |

Consecuencia: **caras = barato** (unos segundos por foto, se puede hacer en lote); **foto entera = ≈1 min**
(bien para una foto en Studio o para las 20–100 que sugiera Events, de noche; no para las 2000 de una sesión).

### Calidad

- **Foco algo fallado (gaussiano / disco)**: el truco de Real-ESRGAN x4v3 es lo que más «nitidez» da a la vista
  (bordes limpios, ruido fuera). PSNR +0,5–0,7 dB de media; mezclado al 60 % con la foto, hasta +2,4 dB en
  recortes con mucho detalle. Inventa: los triángulos de la camiseta salen con bordes perfectos, las letras
  bordadas del escudo salen como garabatos, la piel queda algo «de plástico» y desaparece el grano.
- **Movimiento (trepidación)**: nadie lo arregla bien. NAFNet mejora un poco a 15 px (+0,3 dB, +0,04 SSIM) y casi
  nada a 9 px con un borrón sintético; Real-ESRGAN y la máscara de enfoque lo empeoran (afilan el doble contorno).
  NAFNet se entrenó con borrones reales de GoPro y en fotos de verdad suele ir algo mejor que aquí, pero con
  7–15 min por foto solo tiene sentido en un recorte (una cara, ≈20 s).
- **Caras blandas**: el resultado más espectacular. En la cara desenfocada de verdad, GFPGAN devuelve una cara
  nítida y creíble. Pero: pestañas, iris, dientes y textura de piel **son inventados**, la forma de los ojos
  cambia ligeramente, la piel queda lisa y sin grano (se nota junto al pelo y el fondo, que siguen con ruido), y
  en caras ya nítidas «maquilla» la piel sin aportar nada. Mezclar al 50 % da algo intermedio y más natural.
  RestoreFormer++ da más contraste y textura, pero también artefactos (manchas sobre el balón junto a la cara).
- **Oscuras**: subir la exposición (+3 EV) y levantar sombras recupera la imagen pero saca mucho ruido; NLMeans
  apenas ayuda; Real-ESRGAN después de aclarar da con diferencia lo más limpio (PSNR 19,0 → 23,3 dB frente a la
  foto bien expuesta), a costa de alisar texturas finas. En una foto real de ISO alto el efecto es el mismo:
  ruido fuera, bordes limpios, grano y algo de textura perdidos.

## Recomendación

Una sola acción **«Mejorar con IA»** con cuatro casillas (todas activas por defecto según lo que detecte el
análisis) y un regulador de **Intensidad** (por defecto 60 %):

1. **Aclarar** (si la foto está oscura): sin IA. En RAW, la exposición automática de Events (`estimate_ev`,
   `_face_ev`) y la curva filmic ya lo hacen mejor que cualquier modelo, porque el RAW tiene la información de las
   sombras. En JPEG: subir EV en lineal + hombro suave + CLAHE suave en L (como en la prueba).
2. **Quitar ruido y enfocar**: Real-ESRGAN x4v3 (el modelo que ya descarga `fx_engine`) con el truco
   ×0,5→×4→×1, por bloques de 128 px con solape de 12 (`_esrgan_x4` tal cual), mezclado con la original según la
   intensidad. ≈1 min por foto de 26 MP. Si el usuario pide además **Ampliar ×2/×4**, se usa `fx_engine.upscale`
   sin reducir al final (mismo coste ×4).
3. **Restaurar caras** (descarga opcional, ≈340 MB): GFPGAN 1.4 en cada cara de más de ~80 px detectada con YuNet
   (ya está en `engine`), alineada con sus 5 puntos a la plantilla FFHQ 512, mezclada al 50–80 % con una máscara de
   piel/cara (mejor que la elipse: `EE.PARTS`/`masks.py` ya segmentan partes) y **con el grano original
   devuelto** (añadir el residuo de alta frecuencia de la cara original, o el grano de `Settings`) para que no
   desentone. Solo en caras blandas (`face_sharp` bajo), nunca en las nítidas.
4. **Movimiento** (más adelante): NAFNet (MIT, 92 MB) solo sobre un recorte elegido o las caras; aviso de que
   tarda.

Orden en la tubería: después del revelado y la curva base (los modelos esperan sRGB «de pantalla»), antes del
LUT/estilo y de la máscara de enfoque de salida. Resultado en 16 bits (float) y en caché.

## Estado (25-11-2026)

Hecho en **Events**: motor en `fx_engine` (`enhance`, `brighten`, `enhance_detail`, `restore_faces`,
`ensure_face_model`), sugerencias en el análisis (`engine.suggest_fixes`, campo `ph.fix`), tarjeta «Mejorar con
IA» en la revisión (tecla I, casillas, intensidad, «A las sugeridas…», filtro «Para reparar con IA», vista
mejorada y antes/después, zoom al 100 % mejorado) y aplicación al exportar (`ph.enhance`, guardado en
`revision.json`). Con los modelos reales, en el PC de David: 24 s una foto de 6,4 MP con 2 caras y 68 s una de
24 MP con 5 caras (la estimación de `enhance_seconds` acierta). Pruebas en `tests/test_enhance.py`.
Hecho también en **Studio** (sección «Mejorar con IA» del panel Detalle: se ve en la vista definitiva —8–12 s con
caras en el proxy de 1600 px, ≈4 s solo caras, ≈6 s sin caras—, en el zoom al 100 % y al exportar; el resultado se
guarda en caché y cambiar LUT, B/N, recorte o efectos no lo repite) y en **Edits** (capa «Mejorar con IA», con
máscara y opacidad).
Pendiente: el modo «Movimiento» (NAFNet), afinar los umbrales de `suggest_fixes` (`FIX_*`) con sesiones reales.
La IA se ve siempre en la vista previa, antes de exportar: en la ventana principal de Events (casilla «Mejorar con
IA» de la ficha de la foto, ≈4 s), en la revisión (también la de las fotos retocadas en Studio), en Studio y en
Edits, y en el zoom al 100 %.

## Plan de integración

### Motor (`fx_engine.py`, sin Qt)

- `enhance(img, opts, progress, cancel) -> img` con `opts = {"light": bool, "detail": bool, "faces": bool,
  "amount": 0.6, "upscale": 1|2|4}`; reutiliza `_esrgan_x4`, `ensure_upscaler`, `_session`.
- `ensure_face_model()` igual que `ensure_upscaler` (URL + SHA-256 fijos, carpeta `modelos/gfpgan_1.4/`), y
  `face_ready()`.
- `restore_faces(img, faces_5pts, amount)`: alinear, inferir, devolver con máscara y grano.
- `enhance_seconds(h, w, n_faces)` para la estimación que ya muestra la interfaz (`upscale_seconds` sirve de
  base: 0,11 s/bloque).
- Pruebas en `tests/` con imágenes sintéticas y un ONNX falso (identidad), sin descargar nada, como el resto.

### Events (lo que pidió David: que sugiera)

- **Sugerencias en el análisis** (`engine.analyze` ya calcula todo lo necesario, coste cero):
  - *Cara blanda*: ya existe el aviso «La cara está menos nítida que el fondo» (`face_sharp < 0,5·sharp`).
  - *Foto blanda*: `sharp` por debajo de ~0,5× la mediana de la sesión (no un umbral fijo: depende del objetivo
    y del ISO) y sin estar en descartes.
  - *Oscura*: `ev` (corrección necesaria) por encima de ~+1,5 EV, o JPEG con mediana de luminancia baja.
  - *Ruido*: `iso` alto (≥ 6400 en la A6700) o ruido medido en zonas planas.
  - Se guarda en `ph.fix = {"faces", "detail", "light"}` (en la caché de sesión) y **no se mueve ni cambia nada**
    en el análisis (regla de la casa).
- **Interfaz**: filtro nuevo «Reparar (n)» junto a «Avisos», insignia en la miniatura (`ThumbDelegate`) y, en la
  revisión, botón **«Mejorar con IA»** con antes/después partido (arrastrable) y la intensidad. «Aplicar a las
  sugeridas» lanza el lote en el `Worker` con `ExportProgress` (pasos y «quedan ~», cancelable).
- **Lote realista**: caras en todas las sugeridas (segundos); foto entera solo en las marcadas, con el tiempo
  estimado antes de empezar («23 fotos ≈ 25 min»).
- Se aplica al exportar (como la edición uniforme), se guarda en `<salida>/.phootflow/mejorar.json` y la foto
  mejorada va a `Entrega/Color` como las demás. Los originales no se tocan nunca.

### Studio

- En el panel **Detalle**: «Mejorar con IA» (casillas + intensidad), no destructivo: se guarda como
  `ai_enhance` en los ajustes de la foto (`<carpeta>/.phootflow/studio/<foto>.json`) y el resultado en caché
  (`studio/ia/<foto>.npy` o TIFF 16 bits) con clave de los ajustes previos, para no recalcular al mover otros
  reguladores.
- Vista previa: el proxy de 2048 px tarda ≈8 s; mientras, se muestra el borrador. Al exportar se calcula a
  resolución completa (≈1 min, con progreso).
- Ya existe «exportar ×2 con IA»; «Mejorar» comparte modelo y descarga.

### Edits

- Capa de ajuste **«Mejorar con IA»** (píxeles en caché, con máscara de capa como el resto) y, en la capa
  `portrait` de retrato, una casilla **«Restaurar cara (IA)»** con intensidad: encaja con el retoque de un clic
  (`Filtro › Retoque de retrato en un clic`) y con las máscaras por partes que ya hay.

## Límites que conviene decirle al usuario en la propia interfaz

- «La IA reconstruye el detalle que falta: puede cambiar rasgos finos (ojos, dientes) y texturas. Compara antes
  de entregar.» Mostrar siempre antes/después y una intensidad, nunca un resultado al 100 % sin control.
- Trepidación fuerte, foco muy fallado (la cara es una mancha) o caras de menos de ~60 px: no hay arreglo creíble.
- Textos, logotipos y escudos: se inventan; mejor no usar en fotos de producto o con dorsales legibles.
- Grupos de niños: revisar caras una a una (el cambio de identidad es lo más delicado).

## Pendiente

- Probar NAFNet y GFPGAN en fotos de David **movidas de verdad** (no sintéticas) antes de decidir si el modo
  «Movimiento» merece la pena.
- Medir en el portátil de entrega (los tiempos son del PC de 12 hilos).
- Consultar la licencia de GFPGAN/FFHQ si PhootFlow se vende con «Restaurar caras».
- Probar `onnxruntime-directml` (GPU en Windows): podría bajar el minuto por foto a segundos, pero añade
  dependencia y hay que verificarlo con Smart App Control.
