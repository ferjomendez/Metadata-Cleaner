# Formatos y limitaciones

Versiones verificadas: MAT2 0.13.5, ExifTool 13.25, FFmpeg 7.1.5, pikepdf 9.5.2, Pillow 11.1.0 (Debian 13 trixie). El comportamiento descrito se observó con esas versiones en las pruebas automáticas; otra versión puede comportarse distinto.

Reglas generales:

- El tipo se detecta por el contenido (bytes mágicos y estructura), nunca por la extensión. Si la extensión no coincide, el archivo limpio recibe la extensión correcta.
- Cada resultado lista **eliminados**, **retenidos** y **no verificados**. "Retenido" significa que ExifTool (o ffprobe, o la inspección propia del formato) todavía lo ve después de limpiar.
- Nunca se ofrece un resultado que no pasó la verificación (`verification_failed` / `office_output_invalid`).
- Se rechazan: archivos cifrados o con contraseña, Office antiguo (.doc/.xls/.ppt), Office con macros (.docm/.xlsm/.pptm), BigTIFF, ZIP que no sean OOXML/ODF, y cualquier otro formato.
- Ningún modo analiza el **contenido visible** (rostros, texto en la imagen, lugares reconocibles, voces) ni detecta esteganografía o marcas de agua.

## Imágenes

| Formato | Conservador | Profundo |
| --- | --- | --- |
| JPEG | ExifTool `-all=` más trailers; conserva perfil ICC, Orientation y JFIF. Sin recodificar. Píxeles verificados idénticos. | MAT2 redibuja la imagen (GdkPixbuf): **recodifica con pérdida**, elimina ICC. MAT2 aplica la rotación EXIF a los píxeles; si no lo hace, se restaura solo la marca Orientation. |
| PNG | ExifTool; conserva ICC, gAMA, cHRM, sRGB y Orientation. Píxeles idénticos. | MAT2 redibuja (cairo): sin pérdida para 8 bits opacos, pero **la transparencia parcial puede cambiar** (alfa premultiplicado) y **16 bits pasa a 8 bits**. Elimina ICC. |
| TIFF | ExifTool en dos pasadas: `-all=` y luego borrado explícito de etiquetas de IFD0 (Artist, Make, Model, Software, fechas, XP*, etc.), porque ExifTool no puede borrar IFD0 completo en un TIFF. Píxeles idénticos. En TIFF de varias páginas, las etiquetas de las páginas 2+ pueden quedar (aparecen como retenidas). | MAT2 redibuja: **solo se conserva la primera página**, puede cambiar compresión y agregar canal alfa. |
| WebP | ExifTool; conserva ICC y Orientation. Píxeles idénticos. | ExifTool `-all=` (MAT2 0.13.5 no admite WebP): elimina también ICC. No se redibuja. |
| GIF | ExifTool `-all=` (comentarios, XMP). Animación conservada, cuadros idénticos. | Igual que conservador. |
| HEIC/HEIF (experimental) | ExifTool; elimina EXIF y XMP. **No se verifica** que los píxeles no cambien (no hay decodificador HEIC en el worker). | ExifTool `-all=`. Sin redibujar. Mismas limitaciones. |

Límite: `MAX_IMAGE_MEGAPIXELS` (100 MP por defecto) y 2000 cuadros.

## PDF

| | Conservador | Profundo |
| --- | --- | --- |
| Procedimiento | pikepdf/qpdf: elimina diccionario Info, todos los flujos XMP (documento, páginas, imágenes), PieceInfo, LastModified, autor y fechas de anotaciones, fechas de adjuntos y el `/ID`. Reescribe el archivo **sin revisiones incrementales anteriores** (que suelen conservar metadatos viejos). | MAT2 por defecto: **cada página se convierte en imagen** y se reconstruye el PDF; luego pikepdf quita Info, XMP e ID que queden. |
| Se conserva | Texto seleccionable, diseño, fuentes, anotaciones (su texto), formularios, enlaces, marcadores, adjuntos y JavaScript. Todo esto se informa como retenido. | Solo la apariencia de cada página. |
| Se pierde | Firmas digitales (quedan inválidas). | Texto seleccionable y buscable, enlaces, formularios, anotaciones, marcadores, adjuntos, JavaScript, firmas. El archivo suele crecer. |
| Verificación | Flujos de contenido de cada página idénticos (byte a byte, decodificados). | Misma cantidad de páginas; sin fuentes (capa de texto eliminada). |
| No verificado | Texto oculto (blanco, bajo imágenes, fuera de página), metadatos dentro de imágenes incrustadas. | Contenido visible. |

Por qué no MAT2 `--lightweight` para el modo conservador: en 0.13.5 también redibuja las páginas con cairo y elimina anotaciones, enlaces y formularios. Límite para profundo: `MAX_PDF_PAGES` (300).

## Office Open XML (DOCX, XLSX, PPTX)

| | Conservador (implementación propia) | Profundo (MAT2 + reparación + pasada propia) |
| --- | --- | --- |
| Propiedades | `core.xml` y `app.xml` vacíos; `custom.xml` eliminado. | Igual (MAT2). |
| Autores | Autores de comentarios, control de cambios y listas de personas se reemplazan por "Autor 1", "Autor 2"... Se quitan fechas de revisiones (Word), identificadores de cuenta (userId, providerId) y presencia. | Comentarios eliminados; cambios aceptados (Word). Autores restantes reemplazados. |
| Identificadores | rsid de Word eliminados; ruta de guardado de Excel (`x15ac:absPath`) vaciada; ruta de plantilla adjunta de Word eliminada; nombres de usuario de libros compartidos. | MAT2 elimina rsid y aleatoriza contadores; luego la misma pasada propia (MAT2 0.13.5 **no** elimina `absPath`). |
| Otras partes | Miniatura, etiquetas de confidencialidad (`docMetadata`) y firma digital (ya inválida) eliminadas. Imágenes incrustadas limpiadas con ExifTool sin recodificar. | MAT2 elimina además `customXml`, `numbering.xml` (**viñetas y listas numeradas pueden perder formato**), temas, configuración de impresora, glosario, propiedades de vista, y redibuja las imágenes incrustadas (verificado: sin EXIF). Al hacerlo aplica la rotación EXIF a los píxeles mientras el documento conserva el marco original, así que **una foto incrustada con marca de rotación puede verse deformada**. |
| Se conserva | Texto, fórmulas, estilos, diseño, comentarios (texto), cambios, hojas y diapositivas ocultas, objetos incrustados. | Texto, fórmulas, hojas y diapositivas ocultas. |
| Verificación | Estructura del paquete (tipos de contenido, relaciones sin destinos faltantes, XML bien formado) y **contenido visible idéntico** (texto de Word y PowerPoint; fórmulas, valores y cadenas de Excel). | Estructura del paquete; fórmulas de Excel idénticas en cantidad. Las relaciones que MAT2 deja colgando se eliminan; si algo todavía las referencia, el resultado se rechaza. |

Hallazgos observados que motivaron la reparación: MAT2 0.13.5 deja una relación hacia `customXml` eliminado en documentos creados con python-docx (Word puede pedir "reparar"), y en hojas con comentarios de openpyxl aborta por partes desconocidas; se reintenta con `--unknown-members omit`, se elimina el `legacyDrawing` huérfano y se valida.

Ambos modos: búsqueda de posibles datos personales en todo el XML (rutas `C:\Users\...`, `/home/...`, correos). Se informan como "no verificado" con el lugar donde aparecen; no se modifican porque pueden ser contenido legítimo.

No verificado en ambos modos: objetos incrustados (otro documento dentro del documento), contenido oculto, macros (no deberían existir en formatos x; los macro-enabled se rechazan). **No se comprobó con Microsoft Office ni LibreOffice reales**: la validación es estructural y con python-docx, openpyxl y python-pptx.

## OpenDocument (ODT, ODS, ODP)

| | Conservador | Profundo |
| --- | --- | --- |
| Procedimiento | `meta.xml` vacío, miniatura eliminada (y su entrada del manifiesto), autores de anotaciones y cambios reemplazados por seudónimos, sus fechas fijadas a 1970-01-01, rsid de LibreOffice y nombre/configuración de impresora eliminados, imágenes incrustadas limpiadas. `mimetype` sigue siendo la primera entrada sin comprimir. | MAT2 y luego una pasada propia que **elimina las anotaciones** (MAT2 0.13.5 las deja con su autor) y la configuración de impresora. |
| Verificación | Paquete válido (mimetype, manifiesto, XML) y texto visible idéntico. | Paquete válido. |

MAT2 reescribe el manifiesto con prefijos de espacio de nombres genéricos (`ns0:`); es XML válido, pero no se comprobó con LibreOffice real.

## Audio (MP3, WAV, FLAC, M4A, AAC, Ogg Vorbis/Opus)

| | Conservador | Profundo |
| --- | --- | --- |
| MP3, FLAC, Ogg | MAT2 (mutagen) borra los bloques de etiquetas (ID3, Vorbis comments, imágenes) sin tocar los cuadros de audio. | FFmpeg remux con `-map_metadata -1`, `bitexact`, sin carátula; se reconstruye el contenedor. |
| WAV, M4A, AAC | FFmpeg copia del audio sin metadatos (`bitexact`). | Igual que conservador. |
| Verificación | Hash de cada paquete de audio idéntico antes y después (sin recodificar). | Igual. |
| No verificado | Marcas de agua de audio; la cadena de "vendor" del codificador dentro del flujo Vorbis/FLAC. | Igual. |

## Video (MP4, MOV, MKV, WebM, AVI)

| | Conservador | Profundo |
| --- | --- | --- |
| Procedimiento | FFmpeg copia de flujos (sin recodificar) de video, audio y subtítulos; elimina metadatos globales, de flujo y capítulos (incluida ubicación GPS y fechas de creación); descarta pistas de datos (timecode, telemetría GPS de cámaras) y adjuntos. | Solo video y audio. Además elimina las unidades SEI de H.264 (tipo 6) y HEVC (39 y 40) con `filter_units`: quita la configuración del codificador (por ejemplo, la cadena de x264) y datos de usuario; **también puede quitar metadatos HDR**. Sin recodificar. |
| Verificación | Hash idéntico de cada paquete de cada flujo conservado. | Misma cantidad de paquetes de video; audio idéntico. |
| No verificado | Datos del codificador dentro del flujo de video (en conservador se conservan tal cual). | Otros datos dentro del flujo (por ejemplo, metadatos de AV1/VP9 en el flujo). |

No se recodifica video en ningún modo: con 4 núcleos compartidos con Jellyfin el costo sería alto. El worker corre con `nice` e `ionice` idle. Límite: `MAX_MEDIA_DURATION_SECONDS` (4 h) y `FILE_TIMEOUT_SECONDS` (300 s, puede quedarse corto para videos grandes en este servidor; se puede subir).

## Qué no pueden demostrar las pruebas

Las pruebas comprueban que desaparecen los metadatos **sembrados** en los archivos de prueba y los que ExifTool o ffprobe saben leer. No pueden demostrar que un archivo real no contenga otros datos identificables: campos propietarios que ninguna herramienta conoce, datos dentro del contenido, esteganografía, o diferencias de huella del codificador. Por eso nunca se afirma anonimato completo.
