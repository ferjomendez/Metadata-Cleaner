# Arquitectura

## Componentes

```
navegador (LAN) ──HTTP──> BIND_ADDRESS:8480 ──> api (FastAPI + frontend estático)
                                                 │  SQLite (volumen mc_data)
                                                 │  archivos (volumen mc_work)
                                                 ▼
                                     worker (sin red) ── MAT2 / ExifTool / FFmpeg / pikepdf / Pillow
```

| Contenedor | Imagen base | Función | Red | Límites |
| --- | --- | --- | --- | --- |
| `api` | `python:3.13-slim-trixie` (digest fijo) | API JSON, sesiones, subidas, descargas, frontend compilado | puerto 8000 publicado solo en `BIND_ADDRESS:8480` | 512 MB, 1 CPU, 128 pids |
| `worker` | `debian:trixie-slim` (digest fijo) | Limpieza, verificación, retención | `network_mode: none` | 1,5 GB sin swap, 1,5 CPU, 256 pids |

Ambos: usuario 10001, raíz de solo lectura, `cap_drop: ALL`, `no-new-privileges`, `tmpfs` en `/tmp`, rotación de logs (3 x 10 MB), `restart: unless-stopped`, health checks.

No hay contenedor de frontend: Vite compila el frontend en la etapa `frontend` del Dockerfile del api y FastAPI lo sirve.

## Por qué no hay Redis

La cola es la tabla `job_files` de SQLite (modo WAL), compartida por ambos contenedores mediante el volumen `mc_data`.

- **Reclamo atómico**: `UPDATE job_files SET status='processing', lease_owner=?, lease_expires=? WHERE id=(SELECT ... WHERE status='queued' LIMIT 1) AND status='queued' RETURNING *`. Una sola sentencia, sin carreras (probado con 4 hilos concurrentes).
- **Lease y heartbeat**: el worker renueva el lease cada 10 s. Si muere, el lease vence (60 s) y el archivo vuelve a la cola, hasta `MAX_ATTEMPTS` (2); después queda como fallido (`worker_lost`). Al arrancar, el worker recupera de inmediato los ítems de instancias anteriores. Un apagado ordenado (SIGTERM) devuelve los ítems a la cola sin contar el intento.
- **Ventaja decisiva**: el worker no necesita red. Con Redis tendría que estar en una red Docker.
- **Costo**: SQLite admite un solo escritor a la vez. Con 1 o 2 trabajos simultáneos y escrituras breves no es un problema; `busy_timeout` de 15 s absorbe la contención. No escala a muchos workers ni a varios hosts, lo que aquí no se necesita.

## Flujo de un lote

1. `POST /api/jobs` crea un lote en borrador (máximo 5 borradores por usuario; vencen con la retención).
2. Por cada archivo, `POST /api/jobs/{id}/files` con el contenido como cuerpo binario (sin multipart, sin archivos temporales en `/tmp`). Antes de leer el cuerpo se reserva espacio en una transacción `IMMEDIATE` que valida tamaño por archivo, cantidad y tamaño del lote, cuota del área de trabajo y espacio libre mínimo. El cuerpo se escribe en `incoming/<clave_lote>/<clave_archivo>` (0600) y se detecta el tipo por contenido. Los rechazados se borran en el acto.
3. `POST /api/jobs/{id}/start` con el modo encola los archivos válidos.
4. El worker reclama un archivo y lanza `python3 -m app.process_file` como **proceso hijo separado** (si un archivo hostil cuelga o revienta una herramienta, solo muere ese hijo). El hijo aplica `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE`, sin core dumps, `nice 10` e `ionice` idle; no toca la base de datos.
5. El hijo vuelve a detectar el tipo, ejecuta las comprobaciones previas (píxeles, páginas, duración, zip bomb), inspecciona con ExifTool (y ffprobe en medios), ejecuta el procedimiento de la política, verifica el resultado, inspecciona de nuevo y escribe `result.json`.
6. El worker mueve el resultado a `results/<clave_lote>/`, guarda el informe detallado (con valores) junto al resultado y en la base solo un resumen con nombres de etiquetas. **Borra el original siempre**, haya funcionado o no.
7. El frontend consulta el estado cada 1,5 s. Descargas individuales o ZIP (`Content-Disposition: attachment`, `nosniff`).
8. La limpieza periódica borra los resultados al vencer la retención, los directorios huérfanos, las subidas abandonadas, las sesiones vencidas y el historial antiguo. Corre en el worker (cada 60 s) y como respaldo en el api (cada 10 min).

## Almacenamiento

| Ruta | Contenido | Persistencia | Respaldo |
| --- | --- | --- | --- |
| `mc_data:/data/metadata-cleaner.sqlite3` | usuarios, sesiones, lotes, resúmenes, estado del worker | sí | **sí** |
| `mc_work:/work/incoming` | originales subidos | hasta terminar el proceso | **no** |
| `mc_work:/work/processing` | directorio privado por archivo (0700) | solo durante el proceso | **no** |
| `mc_work:/work/results` | resultados, informes, ZIP | hasta `RETENTION_MINUTES` | **no** |
| `.env` | configuración y secreto de sesión | sí | **sí**, guardado aparte |

Todos los nombres en disco son claves hexadecimales aleatorias. Los nombres de archivo del usuario nunca se usan para construir rutas.

## Cuota

El uso contabilizado es la suma, según la base, de originales presentes y subidas en curso, resultados presentes y ZIP generados. Se rechaza una subida si `uso + 2 x tamaño > WORK_QUOTA_GB` o si el espacio libre quedaría bajo `MIN_FREE_DISK_GB` (se planifica el doble porque el resultado puede ser del tamaño del original). La página de administración muestra además el uso medido en disco.

## Módulos

| Archivo | Responsabilidad |
| --- | --- |
| `app/config.py` | variables de entorno |
| `app/db.py` | esquema SQLite, conexiones, transacciones |
| `app/jobs.py` | lotes, reservas, cola, leases |
| `app/storage.py` | rutas, cuota, borrado verificado |
| `app/cleanup.py` | retención y huérfanos |
| `app/detect.py`, `app/zipguard.py` | detección por contenido, defensas ZIP |
| `app/policies.py` | política explícita por formato y modo |
| `app/cleaners/*.py` | un adaptador por familia: `images`, `pdf`, `office`, `odf`, `media` (+ `zipdoc` común) |
| `app/process_file.py` | proceso hijo que limpia un archivo |
| `app/worker.py` | supervisor del worker |
| `app/main.py`, `app/security.py`, `app/auth.py`, `app/routes/*` | API |
| `app/cli.py` | administración por línea de comandos |
| `app/selftest.py`, `app/healthcheck.py` | diagnóstico |
