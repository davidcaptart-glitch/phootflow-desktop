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
