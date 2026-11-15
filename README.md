# EventEdits

Programa de escritorio para Windows que edita de golpe todas las fotos de una sesión: corrige exposición,
limpia el grano, endereza, propone el mejor formato de recorte, aparta las fotos fallidas y exporta en JPG.

**Descarga:** [última versión](https://github.com/davidcaptart-glitch/eventedits/releases/latest) → `EventEdits.exe`.
No necesita instalación. Al no estar firmado, Windows mostrará «Windows protegió su PC» la primera vez:
*Más información → Ejecutar de todas formas*.

## Uso

1. **Carpeta de la sesión**: la que contiene los RAW (`.ARW`, `.CR3`, `.NEF`, `.RAF`, `.DNG`…) o JPG.
2. **1. Analizar sesión**: pasada rápida (unos segundos por foto). Muestra cada foto con su estado, el formato
   sugerido, la exposición que se va a aplicar y el giro. Nada se mueve ni se borra todavía.
3. Revisa la columna **Editar**: marca o desmarca lo que quieras cambiar.
4. **2. Editar y exportar**: revela a resolución completa las marcadas.

### Qué genera

```
Sesión/
├── Descartadas/            originales apartados (nunca se borran)
└── Editadas/
    ├── Sugeridas/          cada foto en su formato sugerido
    ├── Formatos/4x5/ …     los demás formatos que marques
    ├── Originales.zip      originales conservados, sin pérdida
    └── informe.csv         estado, motivo y ajustes de cada foto
```

Cada archivo lleva el formato y el tamaño en el nombre: `DSC03381_4x5_max.jpg`, `DSC03381_4x5_1080.jpg`.

### Formatos y tamaños

4:5, 5:4, 2:3, 3:2, 3:4, 4:3, 5:7, 7:5, 9:16, 16:9, 1:1 y el original. El programa elige como **sugerido** el
recorte que elimina más zona vacía sin perder sujeto, y exporta además todos los que marques.
Tamaños: máxima resolución, 2048 px de lado largo y 1080 px de ancho.

### Descarte

- **Desenfocadas o movidas**: se comparan con la nitidez media de la sesión.
- **Exposición irrecuperable**: más de un 30 % quemado, o haría falta subir más de 7,5 pasos.
- **Casi duplicadas**: de cada ráfaga igual se queda la más nítida.

Los criterios son aproximados: revisa la propuesta antes de exportar. La detección de ojos cerrados no está incluida.

### Originales comprimidos

`Originales.zip` es compresión sin pérdida: al descomprimir se recuperan los RAW idénticos. Los RAW ya vienen
comprimidos de cámara, así que el ahorro ronda el 10 %. Los originales no se tocan; cuando compruebes el ZIP
puedes borrarlos tú.

### Nube

- **Google Drive**: sube `Editadas` a la carpeta de Drive que indiques (pega su enlace), crea el enlace
  «cualquiera con el enlace puede ver» y lo copia al portapapeles. El ZIP de originales va a una carpeta aparte
  que **no** se comparte.
- **Carpeta sincronizada**: copia el resultado a una carpeta local que otra aplicación sube sola. Es la vía para
  **Amazon Photos**, que no ofrece una API pública para subir ni crear enlaces: el enlace se crea desde Amazon Photos.

#### Conectar Google Drive (una sola vez)

1. En <https://console.cloud.google.com/> crea un proyecto y activa **Google Drive API**.
2. *Pantalla de consentimiento de OAuth* → tipo **Externo** → añade tu cuenta como usuario de prueba y luego
   **Publicar aplicación** (si se queda «en pruebas», Google caduca el permiso cada 7 días).
3. *Credenciales → Crear credenciales → ID de cliente de OAuth →* **App de escritorio** → descarga el JSON.
4. En EventEdits: destino **Google Drive** → **Conectar Google Drive…** → elige ese JSON y autoriza en el navegador.

El permiso se guarda en `%APPDATA%\EventEdits` en tu PC.

## Actualizaciones

Al abrirse, el programa consulta la última release de este repositorio. Si hay una versión nueva pregunta,
la descarga, comprueba su SHA-256, se sustituye y se reinicia.

Publicar una versión nueva:

```
git tag v0.2.0
git push origin v0.2.0
```

GitHub Actions pasa las pruebas, compila `EventEdits.exe` en Windows, comprueba que arranca y lo publica.

## Desarrollo

```
pip install -r requirements.txt
python app.py
python -m unittest discover -s tests
```
