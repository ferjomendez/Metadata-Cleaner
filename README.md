# Metadata Cleaner

Aplicación web autoalojada para eliminar metadatos de archivos. Usuarios autorizados suben archivos, el servidor los limpia localmente con MAT2, ExifTool, FFmpeg y pikepdf, muestra qué se eliminó, qué se retuvo y qué no se pudo verificar, y entrega los archivos limpios. Nada se envía a servicios de terceros.

La limpieza reduce los metadatos conocidos. **No garantiza anonimato**: el contenido visible (rostros, texto, voces, lugares) y las marcas ocultas no se modifican.

## Características

- Dos modos por formato, con políticas explícitas (`backend/app/policies.py`):
  - **Conservador**: conserva calidad, contenido y funciones; sin recodificar; verifica que píxeles, texto o pistas no cambien.
  - **Profundo**: prioriza privacidad sobre fidelidad (por ejemplo, rasteriza PDF, acepta cambios en Office, quita SEI de video) y advierte las pérdidas.
- Formatos: JPEG, PNG, TIFF, WebP, GIF, HEIC (experimental), PDF, DOCX, XLSX, PPTX, ODT, ODS, ODP, MP3, WAV, FLAC, M4A, AAC, Ogg, MP4, MOV, MKV, WebM, AVI. Detalle y límites en [docs/FORMATS.md](docs/FORMATS.md).
- Informe por archivo: eliminados, retenidos, no verificados, advertencias y verificaciones automáticas.
- Retención corta: originales borrados al terminar; resultados borrados a los 60 minutos (configurable); historial sin archivos.
- Seguridad: login obligatorio (Argon2id), sin registro público, sesiones en servidor, CSRF, límites de tasa, CSP estricta, detección de tipo por contenido, defensas contra zip bombs y rutas maliciosas, worker sin red, sin privilegios y con sistema de archivos de solo lectura.
- Interfaz en español (Chile), tema claro y oscuro, accesible, usable en móvil.

## Arquitectura en una línea

Dos contenedores: `api` (FastAPI + frontend React compilado, único puerto publicado y solo en la IP de LAN) y `worker` (herramientas de limpieza, `network_mode: none`). La cola es una tabla SQLite. Ver [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Despliegue rápido (servidor Ubuntu con Docker)

```bash
git clone https://github.com/ferjomendez/Metadata-Cleaner.git ~/metadata-cleaner
cd ~/metadata-cleaner
cp .env.example .env && chmod 600 .env
nano .env                      # BIND_ADDRESS = IP de LAN; SESSION_SECRET = salida de: openssl rand -hex 32
bash scripts/preflight.sh      # comprobaciones de solo lectura
docker compose up -d --build
docker compose exec api python -m app.cli create-admin
bash scripts/verify.sh         # pruebas + prueba de punta a punta
```

Abrir `http://<BIND_ADDRESS>:8480`. Guía completa, actualización, respaldo y recuperación: [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Documentación

| Documento | Contenido |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Componentes, cola SQLite, flujo de un archivo, almacenamiento |
| [docs/FORMATS.md](docs/FORMATS.md) | Qué se limpia y qué no, por formato y modo |
| [docs/SECURITY.md](docs/SECURITY.md) | Modelo de amenazas, controles, sandbox de MAT2, mantenimiento |
| [docs/PRIVACY.md](docs/PRIVACY.md) | Retención, qué se guarda y qué no |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Despliegue, actualización, respaldo, recuperación, HTTPS opcional |
| [docs/TESTING.md](docs/TESTING.md) | Pruebas, cómo ejecutarlas y qué no pueden demostrar |
| [docs/LICENSING.md](docs/LICENSING.md) | Licencia del repositorio (pendiente) y licencias de dependencias |

## Desarrollo

```bash
# backend: pruebas dentro de las imágenes (necesita Docker)
docker build -f backend/Dockerfile --target api-test -t mc-api-test . && docker run --rm mc-api-test
docker build -f backend/Dockerfile.worker --target worker-test -t mc-worker-test . && docker run --rm --network none mc-worker-test

# frontend
cd frontend && npm ci && npm test && npm run dev    # proxy de /api a 127.0.0.1:8000
```

## Licencia

Sin licencia por ahora (todos los derechos reservados) hasta que el propietario decida. Ver [docs/LICENSING.md](docs/LICENSING.md).
