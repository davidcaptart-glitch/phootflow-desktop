# Fuentes de LUT (.cube) y presets de Lightroom (.xmp) para PhootFlow

Investigación del 25-11-2026. Solo se leyeron páginas de licencia, términos, README y descarga; no se bajó ni
se rastreó ningún pack. **No es asesoramiento legal.** Donde no se encontró licencia rige el copyright por
defecto: **no se puede redistribuir**.

Criterio (el mismo que Graphics sigue con Iconify/Openverse/Fontsource):

- **A · Incluir en PhootFlow**: licencia libre que permite redistribuir dentro de un programa y uso comercial
  (MIT, BSD, CC0, CC BY; CC BY-SA con matices). Se empaquetan con su aviso de licencia y crédito.
- **B · Ofrecer para descargar desde el programa**: la licencia lo permite o el fabricante publica el archivo sin
  registro en una URL estable; PhootFlow lo baja **del servidor original** cuando el usuario lo pide, lo guarda en
  `%APPDATA%\PhootFlow\luts\` y enseña el enlace a la página oficial. PhootFlow nunca lo aloja ni lo empaqueta.
- **C · Solo enlace**: hay registro, email, «Acepto» o la licencia prohíbe redistribuir. Se abre la página en el
  navegador y el usuario importa el archivo con «Añadir LUT…» (`luts.import_luts`).

---

## Resumen

| Fuente | LUT | Tipo | Licencia | Categoría |
|---|---|---|---|---|
| t3mujinpack | 51 | Emulación de película (B/N, negativo, diapositiva) | MIT | **A** |
| slaycamera/luts | 5 | Looks creativos | CC BY 4.0 | **A** |
| sfi0zy/street-monochrome | 4 (+12 estilos darktable) | B/N editorial | CC BY 4.0 | **A** |
| priikone/aces-looks | 51 | Looks ACES (ACEScct/AP1) | BSD-3 | A (legal) pero mal encaje técnico |
| Dathinel/rodaje-filtros | 13 | Looks por fórmula | CC0 | **A** (generar con su script) |
| FreshLUTs | miles (comunidad) | Creativos/película | CC0 según sus términos | **A** con revisión una a una |
| Lutify.me gratis | 5 | Creativos (scene-referred) | CC BY-SA 4.0 | A/B (bajar exige cuenta fylm.ai) |
| NatronGitHub/clut (Pat David, PictureFX, Juan Melara) | 347 | Emulación de película + creativos | CC BY-SA 4.0 | **B** (o A asumiendo el riesgo de procedencia) |
| spektrafilm | 1 (alfa) | Emulación espectral | CC BY-SA 4.0 + preámbulo | B cuando haya más |
| Fujifilm F-Log/F-Log2 | 25 zips por cámara | Log→709 + simulaciones | Sin licencia propia | **B** (técnica) |
| ARRI Look Library | 87 | Looks creativos LogC | Sin licencia | **B** |
| Panasonic V-Log→V-709 | 1 | Técnica | Sin licencia | **B** |
| GoPro Labs GP-Log2 | generador | Técnica | Apache-2.0 | **A** (el generador/LUT propios) |
| RawTherapee HaldCLUT.zip | ~300 | Película + creativos | No verificada | **C** |
| G'MIC color presets | 1100+ | Varios | Sin licencia para los CLUT | **C** |
| Q-DDL «800+ free LUTs» | 800+ | Varios | CC BY 4.0 según prensa; web caída | **C** / descartar |
| Sony, Canon, Nikon, DJI, RED, Leica, Apple, Blackmagic | — | Técnica/creativa | Prohíben o no conceden | **C** |
| IWLTBAP, RocketStock/PremiumBeat, ON1, Motion Array, Phlearn, Lens Distortions, freevisuals, ArtStation P3DI, SmallHD, CGC, Juan Melara (web), Adobe | — | Creativos | Prohíben redistribuir o sin licencia | **C** |

Presets `.xmp`:

| Fuente | Presets | Licencia | Categoría |
|---|---|---|---|
| peva3/Lightroom-Presets | 451 `.xmp` + `presets.json` | MIT | **A** |
| t3mujinpack / street-monochrome (estilos darktable) | 51 / 12 | MIT / CC BY 4.0 | **A** tras convertir |
| Adobe Premium, freepresets.com, PresetPro, PresetLove, Unsplash | — | Prohibido o sin licencia | **C** (o leer los que el usuario ya tiene) |
| deep5050/awesome-lightroom-presets | «5000» | Unlicense sobre contenido ajeno | **Evitar** |

---

## A · Se pueden incluir en PhootFlow

### t3mujinpack — la mejor base de emulación de película
- URL: https://github.com/t3mujinpack/t3mujinpack (también aparece como `t3mujin/t3mujinpack`).
- 51 emulaciones: 12 B/N, 24 negativo color, 15 diapositiva. Estilos darktable `.dtstyle` **y** carpeta
  `haldcluts/` con 51 PNG HaldCLUT nivel 12 (144³; muestra de 1728×1728 px).
- Licencia **MIT** (LICENSE.txt). Uso comercial y redistribución sí; conservar el aviso MIT.
- Descarga: releases de GitHub (p. ej. `t3mujinpack_0.6.0_flat.zip`), estable.
- Integración: convertir los HaldCLUT a `.cube` 33³ o 64³ en el build. Los nombres aluden a películas
  comerciales (Kodak, Fuji…): usar nombres propios o «inspirado en», no la marca como título.

### slaycamera/luts
- https://github.com/slaycamera/luts — 5 `.cube` 33³ propios (amber, fade, neon, film, portrait).
- **CC BY 4.0** con LICENSE: «Free to use in personal and commercial work… just credit». Crédito obligatorio.

### sfi0zy/street-monochrome
- https://github.com/sfi0zy/street-monochrome — 4 looks B/N `.cube` (Standard, Flat, Landscape, Eerie) y 12 estilos
  darktable. **CC BY 4.0**, © Ivan Bogachev 2026. Encaja con el B/N editorial de Events.

### priikone/aces-looks
- https://github.com/priikone/aces-looks — 51 `.cube`, **BSD-3**. Son LMT de ACES (entrada ACEScct, gamut AP1):
  no sirven directamente sobre sRGB; habría que envolverlos en la conversión ACES. Legalmente válidos, técnicamente
  poco prácticos.

### Dathinel/rodaje-filtros
- 13 looks 33³ generados por fórmula, **CC0**. Los `.cube` salen de su script `generar.ps1` o de los releases.

### FreshLUTs (con revisión manual)
- https://freshluts.com/ — términos: https://freshluts.com/termsandconditions
- Biblioteca comunitaria muy grande (sin cifra oficial), mayormente `.cube`, tamaño variable.
- Los términos dicen que los LUT se publican bajo **Creative Commons CC0**; uso comercial sin atribución (el enlace
  «se agradece»). Hay a la vez una cláusula genérica de copyright del sitio que choca con eso.
- Riesgo: lo sube cualquiera; puede haber LUT comerciales copiados. **No** importar el catálogo entero: elegir a
  mano unos cuantos de autores con obra propia identificable y guardar la URL de cada uno.
- robots.txt sin reglas y sin cláusula anti-rastreo, pero igualmente: selección manual, no bot.

### Lutify.me (pack gratuito)
- https://lutify.me/free-luts/ — licencia: https://lutify.me/license/
- 5 LUT scene-referred. Los gratuitos son **CC BY-SA 4.0** («for any purpose, even commercially»); el EULA de pago
  no aplica a estos. Hace falta cuenta gratuita de fylm.ai para bajarlos, así que se baja a mano una vez y se
  incluye con crédito y licencia. No usar la copia que hay en ON1 (va con los términos de ON1).

### GoPro Labs GP-Log2
- https://gopro.github.io/labs/log/ — repo https://github.com/gopro/labs, **Apache-2.0**. Generador GP-Log2→Rec.709
  (salida `.cube` 16–65). Se puede portar con el aviso Apache o generar los LUT nosotros.

### LUT propios generados por PhootFlow (recomendado)
- **Conversiones técnicas Log→Rec.709**: las curvas y matrices de gamut están publicadas en los white papers de cada
  marca (S-Log3/S-Gamut3, V-Log, F-Log2, Canon Log 2/3, N-Log, LogC3/4, D-Log, Apple Log, L-Log). Generarlas con
  numpy es código propio, no copia de su archivo. Así se cubre todo lo técnico sin depender de los fabricantes.
- **Looks creativos**: PhootFlow ya tiene `styles.py`, `A.LOOKS` de Edits y los estilos aprendidos; exportar cada
  uno como `.cube` da una biblioteca propia, sin licencias ajenas. `lutgen` (https://github.com/ozwaldorf/lutgen-rs,
  MIT, Rust) sirve para looks de paleta, pero un generador en numpy es más simple y con propiedad limpia.
- No imitar con el nombre del fabricante las simulaciones de Fujifilm ni otros looks de marca.

---

## B · Se pueden ofrecer para descargar desde el programa

### NatronGitHub/clut — 347 looks de película (Pat David, PictureFX, Juan Melara)
- https://github.com/NatronGitHub/clut (original) y su fork https://github.com/cyangraphics/clut
- 347 PNG HaldCLUT nivel 8 (512×512 = 64³) en bw, colorslide, instant_consumer, instant_pro, negative_color,
  negative_new, negative_old, picturefx, print y various.
- **CC BY-SA 4.0** declarado en el README (no hay archivo LICENSE aparte). Uso comercial y redistribución sí,
  atribución por autor obligatoria. **ShareAlike**: los LUT y sus conversiones a `.cube` siguen siendo CC BY-SA;
  el código de PhootFlow y las fotos del usuario **no** quedan afectados (es «agregación»), siempre que vayan como
  archivos aparte con su aviso.
- Dudas: en pixls.us Pat David reconoce que algunos se generaron «a la inversa» de otros paquetes de curvas, y los
  nombres coinciden con presets de VSCO; la carpeta «various» no tiene autoría documentada.
- Descarga estable: zip de `codeload.github.com` de la rama master o `raw.githubusercontent.com`.
- Recomendación: botón «Descargar biblioteca de película (347 LUT, CC BY-SA)» que baja el zip de GitHub, convierte a
  `.cube` en `luts\_comunes\` y escribe `LICENCIA.txt` + créditos. Así se esquiva el riesgo de procedencia en el
  paquete del programa. Excluir «various».

### spektrafilm
- https://github.com/andreavolpato/spektrafilm — licencia LUT:
  `SPEKTRAFILM_LICENSE.txt` (CC BY-SA 4.0 con preámbulo: crédito a Andrea Volpato, enlace, marcar cambios; pide no
  venderlos ni usar el nombre en la marca). El código es **GPL-3.0**: no meterlo en PhootFlow.
- Hoy solo hay 1 LUT alfa (V-Log → sRGB, 33³, subido al foro). Volver a mirar cuando haya un juego estable.

### Fujifilm (F-Log / F-Log2)
- https://www.fujifilm-x.com/en-gb/support/download/lut/ — 25 zips por cámara (6 GFX, 19 serie X) con `.cube`
  3D: F-Log/F-Log2→BT.709 y las simulaciones (Provia, Eterna, Classic Chrome…).
- Sin registro; URLs estables y versionadas (`https://dl.fujifilm-x.com/lut/x-t5-3d-lut-v100.zip`).
- Sin licencia propia de los LUT; las condiciones generales de Fujifilm limitan a uso privado no comercial.
  **No incluir**; como mucho, descarga desde su servidor a petición del usuario y con enlace a la página. Opción más
  prudente: solo enlace.

### ARRI Look Library
- https://www.arri.com/look-library — 87 looks creativos (ALF4, `.aml`, `.cube` LogC3→Rec.709 33³). Sin login,
  enlaces `arri.com/resource/blob/…zip`. Sin texto de licencia («free of charge»). Pensados para material LogC:
  poco útiles sobre fotos. Descarga a petición o enlace.

### Panasonic V-Log → V-709
- https://av.jpn.support.panasonic.com/support/global/cs/dsc/download/lut/index.html — 1 `.cube`
  (`VLog_to_V709_forV35_EN.zip`, 369 KB), sin registro. Sin licencia. Los «Creator's LUT» y LUMIX Lab: solo enlace.

---

## C · Solo enlace (el usuario los baja y los añade)

### Fabricantes
| Marca | URL | Notas |
|---|---|---|
| Sony | https://sony-cinematography.com/resources/luts/ · https://alphauniverse.com/resources/luts · https://pro.sony/ue_US/technology/professional-video-lut-look-up-table | 44 en cinematography (3 técnicos S-Log3→s709, resto creativos de coloristas). Formulario o cuenta Alpha Universe/My Sony. Sin licencia publicada. |
| Canon | https://asia.canon/en/support/0200751202 | Canon Log→BT.709/2020/PQ/HLG, `.cube` 17/33. Casilla de aceptación: «You shall not distribute… the Content»; uso personal no comercial. **Prohibido.** |
| Nikon | https://downloadcenter.nikonimglib.com/en/download/sw/230.html · …/258.html | N-Log→709 33³ (7 + 5, 4 looks de RED). Instalador con acuerdo de licencia. |
| DJI | https://www.dji.com/lut | ~41 D-Log/D-Log M→Rec.709. Términos del sitio: no reproducir ni distribuir. |
| RED | https://www.reddigitalcinema.com/download/ipp2-output-presets | 32 LUT IPP2, con «I agree». |
| Blackmagic | (dentro de DaVinci Resolve) | Licencia de Resolve. PhootFlow puede leer la carpeta de LUT de Resolve si está instalado. |
| Leica | página de descargas de cada cámara (p. ej. SL3) | L-Log y «Leica Looks», sin licencia. |
| Apple | developer.apple.com/download (Apple ID) | Apple Log→709. Generar el nuestro desde el white paper. |
| BBC HLG | https://www.tvbeurope.com/media-consumption/bbc-releases-luts-under-zero-cost-licence | Gratis para producción, **de pago para fabricantes** que los incluyan; son conversiones HDR, no looks. |

### Colecciones abiertas con dudas
- **RawTherapee Film Simulation** (`http://rawtherapee.com/shared/HaldCLUT.zip`, ~300 HaldCLUT): no se encontró
  licencia (RawPedia https://rawpedia.rawtherapee.com/Film_Simulation no la indica). Procedencia dudosa (ver arriba).
  Evitar también repos que lo reempaquetan con otra licencia (`scernst13/HaldCLUT-Cube-Files` «CC0»,
  `MokshC/Film-Emulation-LUTs` MPL): no pueden relicenciar lo que no es suyo.
- **G'MIC** (https://gmic.eu/color_presets/, 1100+ CLUT en `.png` y `.cube`): G'MIC es CeCILL pero los CLUT no
  llevan licencia; muchos packs son regalos de YouTubers/tiendas (Kyler Holland, Shamoon Abbasi, InAvision…).
- **Q-DDL «800+ free 3D LUTs»** (2020): la prensa (https://80.lv/articles/800-free-luts-released,
  https://cgpress.org/archives/800-free-luts.html) dice CC BY 4.0, pero `quickddl.net` ya no existe y se descargaba
  por acortadores con publicidad. Sin fuente primaria ni mirror fiable: descartar.

### Sitios comerciales con LUT gratuitos
| Fuente | URL | Licencia / motivo |
|---|---|---|
| IWLTBAP | https://luts.iwltbap.com/terms-of-sale.html | «cannot be included in an application»; ni compartir ni redistribuir. (Su LUT Generator gratuito convierte presets de Lightroom en LUT: se puede recomendar, no incluir.) |
| RocketStock / PremiumBeat | https://www.premiumbeat.com/blog/29-free-luts-for-video/ | Packs hermanos: «not to resell or redistribute». |
| ON1 | https://www.on1.com/free/luts/ | ~100 LUT, email obligatorio, sin licencia de redistribución. |
| Motion Array | https://motionarray.com/luts/free/ | Cuenta; licencia intransferible; prohíbe scraping. |
| Phlearn | https://phlearn.com/?p=1261944 | «may not be… shared, copied or redistributed». |
| Lens Distortions | https://lensdistortions.com/finishing-luts/ | Prohíbe redistribuir; borrar si caduca la cuenta. |
| freevisuals.net | https://www.freevisuals.net/terms-of-service | §3.3 prohíbe compartir sueltos; §6 prohíbe bots. |
| Perfect 3D Imperfections (ArtStation) | https://www.artstation.com/marketplace/p/7OPv0 | Licencia comercial extendida, sin redistribución. |
| SmallHD, Color Grading Central, Juan Melara, Ground Control, Triune | — | Sin licencia o solo copias de terceros (Triune/Ground Control: evitar, abundan copias piratas). |
| Adobe (Lumetri/SpeedLooks de Premiere) | — | Solo como parte del producto Adobe. PhootFlow puede leer la carpeta local si el usuario los tiene. |

---

## Presets de Lightroom (.xmp)

### Incluir
- **peva3/Lightroom-Presets** — https://github.com/peva3/Lightroom-Presets — 451 presets en 13 categorías, `.xmp`
  (ProcessVersion 15.x) + catálogo `presets.json`. **MIT** («Copyright (c) 2026 peva3»). ZIP en Releases. Revisar
  por muestreo que no haya textos copiados de packs comerciales (sus notas citan «recetas» de la comunidad).
- **t3mujinpack** (MIT, 51) y **street-monochrome** (CC BY 4.0, 12): son estilos darktable; hay que traducirlos a
  ajustes de Studio. Sus HaldCLUT/`.cube` sirven directamente como LUT.

### Solo enlace / importar lo que el usuario ya tenga
- **Adobe Premium presets** y perfiles creativos: las condiciones de Adobe solo permiten distribuirlos como parte
  de un producto propio, nunca sueltos. PhootFlow puede **leer** los que el usuario tenga en
  `%APPDATA%\Adobe\CameraRaw\Settings` (igual que ya lee los DCP de `CameraProfiles`).
- **freepresets.com, PresetPro, PresetLove, Unsplash «Lightroom Presets Vol. 1»**: sin licencia verificable o
  gancho de email; lo habitual es «uso en tus fotos sí, redistribuir no».
- **xsynaptic/synaptic-lightroom-presets**: el autor desaconseja redistribuir.
- **deep5050/awesome-lightroom-presets** («5000»): Unlicense sobre presets recopilados de otros; **no usar ni
  enlazar**.

### Formato (para un importador)
- `.xmp`: RDF/XML con el espacio `crs` = `http://ns.adobe.com/camera-raw-settings/1.0/` (`crs:Exposure2012`,
  `crs:Contrast2012`, `crs:ToneCurvePV2012*`, HSL `crs:HueAdjustmentRed`…, `crs:ColorGrade*`, `crs:Look`,
  `crs:PresetType="Normal"`/`"Look"`, `crs:Name`/`crs:Group` en `rdf:Alt`). No hay especificación oficial; la mejor
  referencia es la lista de ExifTool: https://exiftool.org/TagNames/XMP.html#crs. Basta un parser XML.
- `.lrtemplate` (Lightroom ≤ 7.2): tabla Lua `s = { title=…, value = { settings = { Exposure2012=… } } }`, mismos
  nombres de clave.
- Perfiles con LUT incrustado (`crs:Table_<md5>`, `RGBTable`): formato sin documentar (codificación tipo Ascii85
  comprimida del `dng_big_table` del DNG SDK). El DNG SDK tiene licencia propia de Adobe (gratuita, exige el crédito
  «This product includes DNG technology under license by Adobe»). Escribir un decodificador propio evita copiar
  código, pero es trabajo.
- Los **valores** numéricos de un preset probablemente no son protegibles (idea/expresión), pero sí el archivo,
  nombres, descripciones e imágenes, y las marcas de película. Recrear un look midiendo o desde «recetas» publicadas
  es de bajo riesgo; copiar el `.xmp` de un vendedor, no.

---

## Recomendación para una biblioteca integrada de cientos de LUT

1. **Núcleo incluido (A), ~80–120 LUT con licencia limpia**: t3mujinpack (51, MIT) + slaycamera (5) +
   street-monochrome (4) + rodaje-filtros (13, CC0) + Lutify (5, CC BY-SA) + una selección revisada de FreshLUTs
   (CC0). Convertidos a `.cube` 33³ en `luts/_comunes/<colección>/`, con `LICENCIA.txt` y créditos por colección y
   una pantalla «Créditos de LUT» (como `el["credit"]` en Graphics).
2. **Generados por PhootFlow, ~100+ sin dependencia legal**: todas las conversiones Log→Rec.709 de las marcas desde
   sus fórmulas publicadas (útil también para vídeo y fotos en log), más cada estilo de `styles.py`, `A.LOOKS` y los
   estilos aprendidos exportados a `.cube`, con variantes de intensidad/temperatura. Es la parte que más «biblioteca»
   aporta sin riesgo.
3. **Descarga opcional desde el programa (B), +347**: el juego CC BY-SA de NatronGitHub/clut bajado de GitHub al
   pulsar un botón, convertido a `.cube` y guardado aparte con su licencia (sin «various»). Con esto se superan los
   500 LUT. Fujifilm/ARRI/Panasonic, si acaso, como descarga desde su propio servidor o mejor solo enlace.
4. **Lo demás, solo enlace + «Añadir LUT…»** (ya existe `luts.import_luts`): una lista de enlaces en la ventana a
   Sony, Canon, Nikon, DJI, IWLTBAP, ON1, etc., y que PhootFlow detecte las carpetas locales de Resolve, Premiere y
   Camera Raw para usar lo que el usuario ya tiene instalado sin copiarlo al paquete.
5. **Presets `.xmp`**: incluir peva3/Lightroom-Presets (MIT, 451) cuando exista un importador `crs:` → ajustes de
   Studio, y permitir importar los `.xmp`/`.lrtemplate` del usuario.

Pendiente de confirmar a mano: licencia dentro de `HaldCLUT.zip` de RawTherapee; texto actual del DNG SDK;
procedencia de algunos presets de peva3; tamaño 3D de los `.cube` de G'MIC (irrelevante si se queda en C).
Nombres de película (Kodak Portra, Fuji Velvia…) solo como «inspirado en», nunca como nombre del producto.
