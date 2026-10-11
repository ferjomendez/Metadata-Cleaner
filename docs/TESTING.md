# Pruebas

## Baterías

| Batería | Dónde corre | Contenido |
| --- | --- | --- |
| `backend/tests/common` | imágenes api-test y worker-test | detección por contenido, rechazos (vacío, no admitido, cifrado PDF/OOXML/ODF, Office antiguo y con macros, corrupto), zip bomb (razón, tamaño total, cantidad), zip slip (5 variantes), saneamiento de nombres, límites por archivo/lote/cantidad, cuota y disco, borradores, reclamo atómico concurrente, finalización de lotes, vencimiento de lease y reencolado, lease solo del dueño, retención con historial, borradores vencidos, huérfanos, borrado fallido registrado y reintentado, subidas abandonadas, claves de almacenamiento validadas |
| `backend/tests/api` | imagen api-test | login, cookie HttpOnly/SameSite, mismo error para usuario inexistente, bloqueo, límite por IP, usuario deshabilitado, vencimiento por inactividad, cambio de contraseña, CSRF, rechazo por origen, cabeceras de seguridad, cuerpo JSON grande, SPA y no traversal, flujo completo con descarga e informe, nombres en ZIP, **acceso cruzado entre usuarios y de administradores**, límites de subida, sin Content-Length, cuota 507, borrado del rechazado, descargas vencidas, borrado anticipado, ids y modo inválidos, administración |
| `backend/tests/worker` | imagen worker-test (sin red) | genera fixtures con metadatos sembrados; los 24 formatos en ambos modos sin marcadores según ExifTool/ffprobe; píxeles idénticos en conservador; ICC y orientación conservados; animación GIF; orientación en profundo; límite de píxeles; PDF: texto y anotaciones conservados, rasterizado, cifrado, corrupto; DOCX/XLSX/PPTX/ODF con python-docx, openpyxl y python-pptx; fórmulas; zip bomb; audio con hashes de paquetes; video con copia de flujos y eliminación de SEI de x264; formato inconsistente; worker de punta a punta, borrado de originales, timeout, recuperación tras caída, apagado ordenado |
| `backend/tests/e2e/smoke.py` | contra el despliegue en marcha | 10 formatos x 2 modos por la API real, descarga, verificación con ExifTool, ZIP, borrado anticipado |
| `frontend/src/**/*.test.ts` | build de la imagen api y `npm test` | formato de bytes y cuenta regresiva, rutas, i18n, **cada código del backend tiene traducción**, sin rayas largas |

Todas se ejecutan con `bash scripts/verify.sh`.

## Ejecución manual

```bash
docker build -f backend/Dockerfile --target api-test -t mc-api-test .
docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL mc-api-test

docker build -f backend/Dockerfile.worker --target worker-test -t mc-worker-test .
docker run --rm --network none --read-only --tmpfs /tmp:size=1g --cap-drop ALL mc-worker-test

cd frontend && npm ci && npm test
```

Generar los fixtures para inspeccionarlos: `docker run --rm -v "$PWD/out:/out" -u 0 mc-worker-test python3 -m tests.fixtures.build_fixtures /out`.

## Lo que las pruebas no demuestran

- Que no queden metadatos que ninguna herramienta conoce. Las pruebas buscan los marcadores sembrados y lo que ExifTool/ffprobe leen.
- Que Microsoft Office o LibreOffice abran los documentos sin pedir reparación: se valida la estructura del paquete y se abren con python-docx, openpyxl y python-pptx, no con las suites reales.
- Calidad visual en modo profundo (JPEG recodificado, PDF rasterizado): solo se verifica que el resultado decodifica y conserva dimensiones o páginas.
- Píxeles HEIC idénticos.
- Comportamiento con archivos reales grandes (cientos de MB) en el hardware del servidor: los fixtures son pequeños. Pendiente de prueba manual en el servidor.
- Resistencia frente a exploits reales de los parsers: se mitiga con aislamiento, no se prueba.
- Pruebas de navegador automatizadas no están en el repositorio. La interfaz se revisó con capturas en Edge (escritorio y móvil, tema claro y oscuro) y sin violaciones de CSP en consola, en el PC de desarrollo.
