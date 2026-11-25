# Componentes de terceros

- `face_detection_yunet_2023mar.onnx` — YuNet, de [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet). Licencia MIT, Copyright (c) 2020 Shiqi Yu.
- Logotipo de PhootFlow (`Logo/`, `icon.*`, `mark.png`, `wordmark.png`): propiedad de su autor; no incluido en ninguna licencia de código.

- `text_embeddings.npy` — frases de texto codificadas con CLIP ViT-B/32 (OpenAI, MIT). El modelo de imagen (Xenova/clip-vit-base-patch32, MIT) se descarga al usarlo.
- Modelo `face_landmarker.task` de MediaPipe (Google, Apache-2.0): se descarga al usarlo.
- Paquetes: onnxruntime (Microsoft, MIT), mediapipe (Google, Apache-2.0).
- Fórmulas de revelado de Studio (curva base, tono local, OkLab/OkLCh, mezclador HSL, gradación, curva paramétrica) y los 18 presets originales: portados de [LightCraft](https://github.com/storytold/lightcraft) (Apache-2.0).
- Modelo `selfie_multiclass_256x256.tflite` de MediaPipe (Google, Apache-2.0) para las máscaras de personas: se descarga al usarlo.
- Modelo de borrado `lama_fp32.onnx` de Edits: [LaMa](https://github.com/advimman/lama) (Samsung AI Center, Apache-2.0), exportado a ONNX por [Carve](https://huggingface.co/Carve/LaMa-ONNX) (Apache-2.0). Se descarga al usarlo (208 MB) y se comprueba su SHA-256.
- Ajustes de Edits (Brillo/Contraste, Niveles, Curvas, Exposición, Intensidad, Tono/Saturación, Equilibrio de color, Blanco y negro, Filtro de fotografía, Posterizar, Umbral) y la disposición de su interfaz: portados de [PhotoCraft](https://github.com/storytold/photocraft) de ArtCraft (MIT OR Apache-2.0).
- Graphics (diseño): sin código de terceros. Referencias de funciones e interfaz: [canva-clone](https://github.com/Davronov-Alimardon/canva-clone) (Apache-2.0), [OpenDesign](https://github.com/clawnify/OpenDesign) (MIT), [LidoJS](https://github.com/lidojs/canva-clone) (solo como referencia visual: no publica licencia) y [VectorCraft](https://github.com/storytold/vectorcraft) (MIT o Apache-2.0: pluma, selección directa, Buscatrazos, y la distribución y los colores del entorno: menús, columna de herramientas, Propiedades | Capas | Bibliotecas, barra contextual y barra de estado; iconos dibujados de nuevo, sin copiar los suyos). Vectorizado y recorte de fondo con OpenCV (Apache-2.0).
- LUT incluidos (`luts/biblioteca.npz`, 73): [t3mujinpack](https://github.com/t3mujinpack/t3mujinpack) (MIT, João Almeida), [Street Monochrome](https://github.com/sfi0zy/street-monochrome) (CC BY 4.0, Ivan Bogachev), [slay](https://github.com/slaycamera/luts) (CC BY 4.0, slaycamera.com) y [rodaje-filtros](https://github.com/Dathinel/rodaje-filtros) (CC0). Remuestreados a 33³; textos completos y cambios en `luts/LICENCIAS.md`.
