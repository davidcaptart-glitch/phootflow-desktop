<p align="center"><img src="assets/icon.png" width="96" alt="PhootFlow"></p>

# PhootFlow

Programa de escritorio para Windows que edita **sola** una sesión de fotos entera (500, 1000, 2000 fotos):
corrige exposición, limpia el grano, endereza, propone el mejor recorte, aparta las fallidas, aplica el estilo
del tipo de evento (o **tu propio estilo aprendido**) y exporta en JPG, con versión en blanco y negro editorial
opcional. Antes se llamaba *EventEdits*.

## Instalar

| Opción | Cuándo |
|---|---|
| **`instalar.ps1`** (recomendada) | Cualquier PC con Python 3.11+. Imprescindible si Windows tiene **Smart App Control** activo: bloquea el `.exe` sin firmar. |
| **`PhootFlow.exe`** de la [última versión](https://github.com/davidcaptart-glitch/phootflow-releases/releases/latest) | PCs sin Smart App Control. Al no estar firmado, Windows avisa la primera vez: *Más información → Ejecutar de todas formas*. |

```
powershell -ExecutionPolicy Bypass -File instalar.ps1
```

Las dos se **actualizan solas**: al abrirse (y cada 4 horas) miran si hay una versión nueva, la descargan,
comprueban su SHA-256 y se reinician.

La primera vez, PhootFlow pide una **clave de activación** (te la da David). Queda vinculada a tu equipo y
funciona sin conexión hasta 7 días seguidos. El estado se ve en el botón *Licencia* de la barra lateral.

## Uso

1. **Carpeta de la sesión** (los RAW `.ARW`, `.CR3`, `.NEF`, `.RAF`, `.DNG`… o JPG).
2. **Tipo de sesión**: *Automático* lo detecta (boda, cumpleaños/fiesta, discoteca/noche, deporte, retrato,
   exterior) por EXIF y contenido; o elígelo tú, o elige uno de **tus estilos aprendidos**.
3. **Procesar todo**: analiza, selecciona y exporta sin parar. El PC no se suspende mientras trabaja. Si se
   corta, vuelve a pulsarlo: recupera el análisis y no repite lo ya exportado.
   O, si prefieres revisar: **Analizar** → revisa en la rejilla → **Exportar**.

En la rejilla: clic para ver la foto en grande con el recorte marcado; **P** conservar, **X** descartar,
**espacio** alternar. En el panel derecho se cambian exposición, giro y formato de cada foto.

### Qué genera

```
Sesión/
├── Descartadas/            originales apartados (nunca se borran)
├── DSC0001.xmp             (opcional) ajustes para Lightroom, solo si no existía ya uno
└── Editadas/
    ├── Sugeridas/          cada foto en su formato sugerido
    ├── Formatos/4x5/ …     los demás formatos que marques
    ├── ByN/                (opcional) blanco y negro editorial
    ├── Originales.zip      originales conservados, sin pérdida
    ├── informe.csv         estado, motivo, ajustes y avisos de cada foto
    └── .phootflow/         análisis guardado para reanudar (se puede borrar)
```

## Estilos

- **Presets por tipo de evento**: Boda (luminoso, suave, cálido), Cumpleaños/fiesta, Discoteca/noche (más
  limpieza de grano, negros densos, conserva el color de los focos), Deporte (contraste y nitidez),
  Retrato/estudio y Exterior/paisaje.
- **Aprender mi estilo…**: elige una carpeta con tus fotos **ya editadas** de un tipo de evento y PhootFlow
  aprende tu aspecto final (luces, contraste, color, saturación, cuánto B/N usas y tus formatos de recorte).
  Si además le das la carpeta de **originales** con los mismos nombres de archivo, aprende cuánto transformas
  cada foto: más fiel. Los estilos se guardan en `%APPDATA%\PhootFlow\estilos\`.
- **Blanco y negro editorial** (opción): mezcla de canales pensada para la piel, contraste local, negros densos
  sin empastar, blancos limpios y grano fino.

## Tres entornos: Events, Studio y Edits

El interruptor **Events | Studio | Edits** de la cabecera (Ctrl+Tab) cambia de entorno conservando la carpeta. Al **abrir una
carpeta** se elige en cuál trabajarla.

- **Events**: edición automática de sesiones completas (lo de abajo).
- **Studio**: edición foto a foto como Lightroom — biblioteca con estrellas y marcas (P/X/U, 0–5), revelado
  en vivo (Básico, Presencia, Curva, Mezcla HSL de 8 bandas, Gradación de color, Detalle, Efectos, Enderezar,
  LUT, B/N), antes/después (`\`), historial (Ctrl+Z / Ctrl+Y), copiar/pegar/sincronizar ajustes, presets propios
  y los 18 de LightCraft, y exportación. No destructivo: los ajustes se guardan en `.phootflow/studio/`.
  Fórmulas de revelado portadas de [LightCraft](https://github.com/storytold/lightcraft) (Apache-2.0).
- **Edits**: retoque foto a foto como Photoshop, por capas (ver abajo). Se llega desde la cabecera, desde
  **Editar en Edits** en Studio o desde la revisión de Events (Mayús+E).

### Edits (retoque tipo Photoshop)

Parte de la foto ya revelada: el revelado de Studio si lo tiene y, si no, la edición de Events o un revelado
automático del RAW; un JPG, PNG o TIFF se retoca tal cual. Encima se apilan **capas** con casilla de
visibilidad, opacidad y orden:

- **Herramientas** (barra de la izquierda): **Mover (V)**, **Pincel corrector (J)** para granos, brillos y
  manchas (busca solo el mejor parche y funde tono y textura), **Tampón de clonar (S)** con Alt+clic para el
  origen, **Relleno según contenido (G)** para borrar cables, carteles o gente, **Pincel de máscara (B)**,
  **Ojos y luz (O)** (aclarar ojeras, blanquear dientes y ojos, oscurecer) y **Licuar (W)** (empujar, inflar,
  encoger). Tamaño con `[` `]`.
- **Piel**: suavizado por separación de frecuencias sobre la piel que encuentra la IA (MediaPipe), con
  Suavizado y Textura; con el pincel de máscara se añaden o quitan zonas.
- **Fondo / Quitar fondo**: el sujeto se detecta como **persona (IA)**, **objeto o producto** (GrabCut) o
  **logo o fondo liso** por color (cuentagotas, tolerancia, suavidad y bordes sin halo). El fondo se desenfoca,
  pasa a B/N, a un color o a **transparente** (se exporta en PNG o TIFF con transparencia). El pincel de
  máscara corrige lo que el detector no ve. «＋ Capa → Quitar fondo» lo prepara en un clic: así se limpian
  también logos en JPG.
- **Texto** (fuente, tamaño, color, sombra, posición) y **Logo** (PNG o JPG, con «quitar el fondo del logo»):
  se arrastran con Mover y van siempre encima, sobre la foto ya recortada.
- **Imagen**: **arrastra una imagen desde el Explorador** sobre la foto (o «＋ Capa → Imagen…») y se crea una
  capa donde la sueltas. Se mueve con Mover, cambia de tamaño con Ctrl+rueda o el slider, se gira, se voltea,
  tiene opacidad y se le puede **quitar el fondo** (logo o fondo liso, objeto o persona). La imagen se copia a
  `.phootflow/edits/capas/`, así la edición no depende del archivo original.

Lienzo con zoom (rueda, doble clic 100 %, Ctrl+0 encajar; mano con el botón central o la barra espaciadora),
antes/después (`\` y Y), historial con deshacer y rehacer (Ctrl+Z / Ctrl+Y). Nada toca el original: el trabajo
se guarda en `.phootflow/edits/<foto>.json` y se exporta a la carpeta **Edits** en JPG, TIFF de 16 bits o PNG.
Desde la revisión de Events, «Aprobar y volver a Events» hace que la exportación de Events añada la foto
retocada en «Sugeridas» (`<foto>_edits_<tamaño>.jpg`).

### Máscaras (Studio)

Como en Lightroom: **pincel**, **degradado lineal y radial**, **rango de luz y de color**; con IA local
(MediaPipe): **persona, piel de la cara, piel, pelo, ropa y fondo**, y partes de la cara: **ojos, iris, cejas,
labios y dientes**; **cielo** aproximado. Cada máscara tiene sus ajustes (exposición, contraste, altas luces,
sombras, temperatura, matiz, saturación, claridad, nitidez), cantidad e invertir. **O** muestra la máscara.

### Revisión antes de exportar (Events)

Antes de exportar se abre la revisión: cada foto con su edición final, **Aprobar (A) / Rechazar (R)**, flechas
para moverse, filtros (aprobadas, rechazadas, pendientes), **Ver B/N (B)** y **recortes alternativos** para
elegir otro formato. **Editar en Studio (E)** o **Editar en Edits (Mayús+E)** abre la foto en Studio partiendo de la edición de Events; con
«Usar en Events y volver» esa foto se exportará con la edición de Studio. Se puede desactivar con la casilla
«Revisar antes de exportar».

## Estilos de blanco y negro y looks de color

- **10 estilos de B/N**: Editorial, Alto contraste, Suave mate, Película clásica, Filtro rojo, Filtro verde, Clave alta,
  Noir, Selenio y Sepia.
- **10 looks de color**: Natural, Cálido, Frío, Vivo, Suave, Mate, Cinematográfico, Película, Dorado y Noche azul.

Se elige uno para la sesión y, en cada foto que no convenza, otro distinto (panel de la foto o revisión, con
miniaturas para comparar). Una foto puede quedarse **sin versión B/N**.

## Edición uniforme

La sesión entera se revela con **un único ajuste**, como al sincronizar ajustes en Lightroom: una sola curva y
un solo color para todas las fotos, y el brillo (sobre todo el de las caras) igualado entre fotos del mismo
momento. Por foto solo cambia lo que no altera el aspecto: limpieza del grano según el ruido medido de cada
foto, enderezado, recorte y rescate de luces quemadas por el flash.

- **Look de la cámara** (opción): parte del contraste y el color del JPEG que lleva el RAW (mediana de la sesión).

## LUT por tipo de sesión

En **Tipo de sesión → Importar LUT…** se asocian uno o varios `.cube` (Lightroom, Premiere, DaVinci…) a cada
tipo de sesión (también hay LUT comunes). Se elige el LUT y su intensidad; el programa iguala la sesión, quita
su propia curva y color para no duplicarlos y aplica el mismo LUT a todas las fotos. Las fotos a las que el LUT
les sienta peor que al resto (se queman, se empastan o cambian mucho de brillo) salen en **Avisos** como
«Revisar LUT», y en cada foto se puede bajar la intensidad del LUT solo para ella. Se guardan en
`%APPDATA%\PhootFlow\luts\<tipo>\`.

## IA (local, sin subir fotos)

- **Tipo de evento** con CLIP (ViT-B/32 cuantizado, MIT; 89 MB que se descargan y verifican la primera vez).
  Acertó 15 de 15 sesiones reales del autor.
- **Ojos cerrados** con MediaPipe Face Landmarker (Apache-2.0; 3,6 MB). Avisa, o descarta si se activa la opción;
  en las ráfagas se queda con la foto de ojos abiertos y cara nítida.

Todo funciona sin conexión una vez descargados los modelos; sin ellos se usan los métodos clásicos.

## Selección automática

- Desenfocadas o movidas (relativo a la nitidez media de la sesión).
- Exposición irrecuperable (más de un 30 % quemado o más de 7,5 pasos de subida).
- Casi duplicadas: de cada ráfaga se queda la más nítida.
- **Avisos** (no descartan): ojos quizá cerrados, cara menos nítida que el fondo.

Las caras se detectan con **YuNet** (OpenCV Zoo, licencia MIT), mucho más fiable que Haar con ISO alto y luz
de discoteca.

## Nube

- **Google Drive**: sube `Editadas` a la carpeta que indiques, crea el enlace «cualquiera con el enlace» y lo
  copia al portapapeles. El ZIP de originales va a una carpeta aparte que no se comparte.
- **Carpeta sincronizada**: copia a una carpeta que otra app sube sola (vía para **Amazon Photos**, que no
  tiene API pública).

### Conectar Google Drive (una sola vez)

1. En <https://console.cloud.google.com/> crea un proyecto y activa **Google Drive API**.
2. *Pantalla de consentimiento de OAuth* → **Externo** → añade tu cuenta como usuario de prueba → **Publicar
   aplicación** (si queda «en pruebas», Google caduca el permiso cada 7 días).
3. *Credenciales → ID de cliente de OAuth →* **App de escritorio** → descarga el JSON.
4. En PhootFlow: Nube → **Google Drive** → **Conectar Google Drive…** → elige ese JSON y autoriza.

## Publicar una versión

```
git tag v0.3.0
git push origin v0.3.0
```

GitHub Actions pasa las pruebas, compila `PhootFlow.exe`, comprueba que arranca y procesa, empaqueta
`PhootFlow-src.zip` y lo publica todo con sus SHA-256. Las copias instaladas se actualizan solas.

## Desarrollo

```
pip install -r requirements.txt
python app.py
python app.py --selftest
python -m unittest discover -s tests
```
