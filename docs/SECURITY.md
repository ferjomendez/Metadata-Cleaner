# Seguridad

## Modelo de amenazas

- Todo archivo subido se trata como hostil (exploits de parsers de imagen, PDF, XML o video; bombas de descompresión; rutas maliciosas en ZIP; consumo de recursos).
- Usuarios autenticados pueden intentar ver archivos de otros usuarios.
- Un navegador en la LAN puede intentar CSRF o clickjacking.
- El servidor es compartido (Jellyfin y otros servicios): el app no debe agotar CPU, RAM ni disco.

Fuera del alcance: atacantes con acceso de administrador al servidor o a Docker, y el tráfico en la LAN mientras no haya HTTPS (ver más abajo).

## Aislamiento del worker y el sandbox de MAT2

MAT2 usa bubblewrap para aislar las herramientas externas que invoca. **Resultado probado** (Docker 29, imágenes de este repositorio, en el PC de desarrollo y en el preflight): dentro de un contenedor sin privilegios bubblewrap falla con `No permissions to create a new namespace` y `unshare --user` falla con `Operation not permitted`, tanto con el perfil seccomp por defecto como con la configuración endurecida. Debe confirmarse en el servidor con `scripts/preflight.sh`.

Decisión, sin ampliar privilegios:

- bubblewrap **no** se instala (la imagen falla al compilar si aparece) y MAT2 se ejecuta con `--no-sandbox`.
- El aislamiento lo da el contenedor `worker`: `network_mode: none`, usuario 10001, raíz de solo lectura, `cap_drop: ALL`, `no-new-privileges`, `pids_limit: 256`, 1,5 GB de RAM sin swap, 1,5 CPU.
- Cada archivo se procesa en un **proceso hijo** con límites `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE`, sin core dumps, `nice 10`, `ionice` idle y un tiempo máximo de pared (`FILE_TIMEOUT_SECONDS`); al vencer se mata todo el grupo de procesos.
- No se usa `privileged`, no se monta el socket de Docker y no se cambia el perfil seccomp.

Alternativa no adoptada: un perfil seccomp propio que permita `unshare(CLONE_NEWUSER)` y `CAP_SYS_ADMIN` o `SYS_ADMIN` para que bubblewrap funcione. Agregaría una capa de aislamiento por herramienta, pero amplía la superficie del kernel expuesta al contenedor. Con el worker ya sin red y sin datos de otros usuarios más allá de la cola, no se considera justificado. Es decisión del propietario si desea explorarlo.

Lo que un exploit exitoso dentro del worker todavía podría hacer: leer y modificar archivos en `mc_work` (originales y resultados de otros trabajos en curso) y la base SQLite en `mc_data` (hashes Argon2id de contraseñas, hashes de sesión). No podría salir por la red, escribir en la imagen ni escalar a root dentro del contenedor.

## Autenticación y sesiones

- Sin registro público. Usuarios creados por un administrador (interfaz o CLI). El primer administrador solo por CLI dentro del contenedor, con contraseña pedida por consola: `docker compose exec api python -m app.cli create-admin`.
- Contraseñas con Argon2id (argon2-cffi, parámetros por defecto: 3 iteraciones, 64 MiB, 4 hilos), mínimo 12 caracteres. Rehash automático si cambian los parámetros. El tiempo de respuesta no revela si el usuario existe.
- Sesiones en servidor: token aleatorio de 256 bits en cookie `HttpOnly`, `SameSite=Strict`, `Secure` si `COOKIE_SECURE=true` (y prefijo `__Host-`). En la base solo se guarda `HMAC-SHA256(SESSION_SECRET, token)`. Vencen por inactividad (8 h) y absoluto (24 h). Cambiar la contraseña, deshabilitar el usuario o restablecerla cierra sus sesiones.
- CSRF: token por sesión exigido en `X-CSRF-Token` en toda petición que modifica estado, más rechazo de peticiones con `Origin` ajeno o `Sec-Fetch-Site: cross-site|same-site` (cubre también el login).
- Límites de tasa: login 10 por IP cada 5 min y 10 por usuario cada 15 min; bloqueo de cuenta tras 5 fallos con espera exponencial (1, 2, 4... hasta 60 min); creación de lotes, subidas, ZIP, cambio de contraseña y acciones de administración limitados por usuario.

## Autorización

- Cada endpoint de lote filtra por `user_id` de la sesión; un lote ajeno responde 404 (no revela que existe). Los administradores **no** tienen acceso a archivos de otros usuarios.
- Pruebas: `tests/api/test_api.py::test_cross_user_access_denied` cubre estado, subida, inicio, borrado, descarga, informe y ZIP para otro usuario, para un administrador y sin sesión.

## Entrada y archivos

- Tipo por contenido (bytes mágicos y estructura) en el api y de nuevo en el worker; si no coinciden, se rechaza.
- Nombres en disco aleatorios (`[0-9a-f]{32}`, validados antes de construir rutas). El nombre del usuario se sanea para mostrar y para las entradas del ZIP (sin rutas, sin caracteres de control, duplicados numerados).
- Subidas: cuerpo binario con `Content-Length` obligatorio, tope por archivo, por lote, por cantidad, cuota global y piso de disco libre, verificados antes de leer el cuerpo. Cuerpos JSON limitados a 64 KB.
- ZIP (OOXML, ODF): antes de procesar se valida el directorio central: cantidad de entradas, tamaño descomprimido declarado, razón de compresión, nombres absolutos o con `..`, barras invertidas, duplicados, cifrado y métodos de compresión. Las lecturas posteriores están acotadas aunque el tamaño declarado mienta. Los archivos nunca se extraen por nombre al disco.
- Imágenes: límite de megapíxeles (Pillow `MAX_IMAGE_PIXELS`) y de cuadros. PDF: límite de páginas en modo profundo. Medios: límite de duración.
- XML: Python 3.13 con expat 2.7 (protección contra amplificación de entidades); ElementTree no resuelve entidades externas.
- Herramientas externas: listas de argumentos, sin shell, entorno mínimo, salida capturada con tope, tiempo máximo, grupo de procesos propio.

## HTTP

- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`. Sin scripts ni estilos en línea, fuentes autoalojadas, sin orígenes de terceros (verificado en el build y sin violaciones en un navegador real).
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy` restrictiva, `Cross-Origin-Opener-Policy` y `Cross-Origin-Resource-Policy: same-origin`, `Cache-Control: no-store` en la API, HSTS solo con HTTPS.
- Descargas con `Content-Disposition: attachment` y `application/octet-stream`.
- Sin documentación OpenAPI expuesta.

## Red y puertos

- Un solo puerto publicado, `BIND_ADDRESS:8480`, nunca `0.0.0.0`. Docker se salta ufw, así que esa ligadura es la restricción de acceso. La base y la API interna no se publican. El worker no tiene red.
- Sin HTTPS, las credenciales viajan en claro por la LAN. Mitigación recomendada: HTTPS mediante Nginx Proxy Manager (ver OPERATIONS.md; requiere tu autorización).

## Registros

Solo identificadores cortos, formato, modo, códigos de error y versiones. Nunca contenido, metadatos extraídos ni nombres de archivo. Rotación 3 x 10 MB por contenedor.

## Secretos

`SESSION_SECRET` en `.env` (ignorado por git, `chmod 600`), generado con `openssl rand -hex 32`. El api no arranca sin él. No hay credenciales por defecto ni en variables de entorno.

## Mantenimiento de seguridad

Mensual, o ante un aviso de seguridad:

1. `git pull` y `docker compose build --pull && docker compose up -d` (recoge parches de Debian dentro de las versiones fijadas).
2. Revisar avisos de: MAT2 (https://0xacab.org/jvoisin/mat2), ExifTool (https://exiftool.org/history.html), FFmpeg (https://ffmpeg.org/security.html), Pillow, pikepdf/qpdf, poppler, librsvg, gdk-pixbuf y Debian (https://www.debian.org/security/), además de FastAPI/Starlette/uvicorn.
3. Auditoría de dependencias (desde el PC de desarrollo):
   - Python: `docker run --rm -v "$PWD/backend:/w" -w /w python:3.13-slim-trixie sh -c "pip install -q pip-audit && pip-audit -r requirements.lock"`
   - npm: `cd frontend && npm audit`
4. Para actualizar versiones fijadas: digests de imágenes base (`docker pull` + `docker image inspect --format '{{index .RepoDigests 0}}'`), versiones Debian en `Dockerfile.worker` (ARG), lockfiles de Python (`pip-compile --generate-hashes` dentro de `python:3.13-slim-trixie`) y `package-lock.json`. Después ejecutar `scripts/verify.sh`.
