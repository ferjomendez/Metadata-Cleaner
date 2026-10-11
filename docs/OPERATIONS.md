# Operación

Servidor objetivo: Ubuntu Server 26.04 con CasaOS, Docker 29, Compose v5. Se accede por SSH desde PowerShell en Windows. En los ejemplos, `<usuario>` es tu usuario SSH e `<IP_LAN>` la IP de LAN del servidor (la misma de `BIND_ADDRESS`). Todos los comandos se ejecutan **en el servidor** con tu usuario, salvo que se indique otra cosa. Nada de esto modifica CasaOS, Nginx Proxy Manager, el firewall ni otros contenedores.

## 1. Primer despliegue

```bash
# 1. Conectarse (desde PowerShell en el PC)
ssh <usuario>@<IP_LAN>

# 2. Clonar el repositorio fuera de los directorios de CasaOS
git clone https://github.com/ferjomendez/Metadata-Cleaner.git ~/metadata-cleaner
cd ~/metadata-cleaner

# 3. Configuración
cp .env.example .env
chmod 600 .env
openssl rand -hex 32            # copiar la salida
nano .env                       # SESSION_SECRET=<salida>, BIND_ADDRESS=<IP_LAN>

# 4. Comprobaciones previas (solo lectura)
bash scripts/preflight.sh
```

Salida esperada del preflight: líneas `PASS` para docker, compose, daemon, `.env`, `SESSION_SECRET`, `BIND_ADDRESS ... is configured on this host`, `port 8480 is free`, disco y RAM; un `INFO` que dice que la imagen del worker aún no existe; resumen `0 fail`. Un `WARN` de RAM o disco no impide continuar pero conviene revisarlo.

```bash
# 5. Compilar e iniciar (la primera vez tarda varios minutos: descarga imágenes base,
#    paquetes Debian y dependencias de npm/pip)
docker compose up -d --build
docker compose ps
```

Esperado: `metadata-cleaner-api-1` y `metadata-cleaner-worker-1` con estado `Up ... (healthy)` en menos de un minuto; el api muestra `<IP_LAN>:8480->8000/tcp`.

```bash
# 6. Repetir el preflight: ahora incluye la autoprueba del worker y la prueba de namespaces
bash scripts/preflight.sh

# 7. Crear el primer administrador (pide la contraseña dos veces, mínimo 12 caracteres)
docker compose exec api python -m app.cli create-admin
```

Esperado: `Usuario '<nombre>' creado como administrador.`

```bash
# 8. Verificación completa (pruebas + inspección de contenedores + punta a punta)
bash scripts/verify.sh
```

Esperado al final: `VERIFY PASS: 21/21 checks passed`. Tarda unos minutos (la batería del worker procesa cada formato en ambos modos con prioridad baja). Crea y elimina un usuario temporal `verify-xxxxxxxx`.

9. Abrir `http://<IP_LAN>:8480` en el navegador, iniciar sesión, limpiar un archivo.

## 2. Actualizar

```bash
cd ~/metadata-cleaner
bash scripts/backup.sh
git pull
docker compose up -d --build
docker compose ps
bash scripts/verify.sh
```

Los trabajos en curso se devuelven a la cola durante el reinicio del worker y se reanudan solos. Para volver a una versión anterior: `git checkout <commit>` y `docker compose up -d --build`.

## 3. Usuarios

Interfaz: página "Administración" (crear, deshabilitar, habilitar, dar o quitar administrador, restablecer contraseña, cerrar sesiones, desbloquear).

Línea de comandos:

```bash
docker compose exec api python -m app.cli list-users
docker compose exec api python -m app.cli create-user --username ana
docker compose exec api python -m app.cli reset-password ana
docker compose exec api python -m app.cli disable-user ana
docker compose exec api python -m app.cli enable-user ana
docker compose exec api python -m app.cli delete-user ana --yes
```

Si se pierde el acceso de administrador: `create-admin` con otro nombre o `reset-password` del existente.

## 4. Respaldo y recuperación

Se respaldan **solo** la base de datos y `.env`. Los archivos de usuarios (`mc_work`) son temporales y quedan excluidos por diseño.

```bash
bash scripts/backup.sh          # copia en línea, sin detener el servicio
ls -l backups/
```

Copiar `backups/` fuera del servidor (contiene hashes de contraseñas y el secreto de sesión). Desde PowerShell en el PC:

```powershell
scp -r <usuario>@<IP_LAN>:~/metadata-cleaner/backups .\metadata-cleaner-backups
```

Programar un respaldo diario (opcional, a decisión del propietario): `crontab -e` y agregar
`30 3 * * * cd ~/metadata-cleaner && bash scripts/backup.sh >/dev/null 2>&1 && find backups -mtime +14 -delete`.

Restaurar:

```bash
bash scripts/restore.sh backups/metadata-cleaner-<timestamp>.sqlite3   # pide escribir RESTORE
```

Reconstrucción completa en un servidor nuevo: pasos 2 a 5 de la sección 1 usando el `.env` respaldado, luego `restore.sh` con el último respaldo.

## 5. Monitoreo y problemas

| Síntoma | Qué mirar |
| --- | --- |
| Estado general | Página Administración: cuota, disco libre, cola, latido del worker, última limpieza, fallos de borrado, versiones. |
| Contenedores | `docker compose ps`, `docker compose logs --tail 100 api worker` |
| Worker "sin respuesta" | `docker compose logs worker`; `docker compose restart worker`. Los archivos en curso vuelven a la cola. |
| Subidas rechazadas por cuota o disco | Esperar a que venzan los resultados, o subir `WORK_QUOTA_GB` / bajar `MIN_FREE_DISK_GB` en `.env` y `docker compose up -d`. |
| Videos que fallan por tiempo | Subir `FILE_TIMEOUT_SECONDS` (por ejemplo 900). |
| Fallos de borrado pendientes | Página Administración; se reintentan cada minuto. Si persisten: `docker compose exec worker ls -la /work/results`. |
| Uso de recursos | `docker stats --no-stream metadata-cleaner-api-1 metadata-cleaner-worker-1` |

Desinstalar por completo (borra base y archivos):

```bash
cd ~/metadata-cleaner && docker compose down -v && docker image rm metadata-cleaner-api:local metadata-cleaner-worker:local
```

## 6. Ajustes de recursos

Valores por defecto pensados para 4 núcleos y 7 GB compartidos con Jellyfin: api 512 MB y 1 CPU; worker 1,5 GB sin swap y 1,5 CPU; 1 trabajo simultáneo; `nice` e `ionice` idle para las herramientas. `MAX_CONCURRENT_JOBS=2` duplica el pico de memoria del worker dentro del mismo límite de 1,5 GB: subir también `mem_limit` del worker en `compose.yaml` si se activa.

## 7. HTTPS con Nginx Proxy Manager (fase posterior, requiere autorización)

**No se ha aplicado.** Cambios que implicaría, para decidir:

1. En NPM, nuevo Proxy Host (por ejemplo `cleaner.<tu dominio local>`), destino `http://<IP_LAN>:8480`, certificado SSL, "Force SSL".
2. En la pestaña "Advanced" del Proxy Host, porque NPM limita el cuerpo por defecto y las subidas pueden ser de 500 MB:
   ```nginx
   client_max_body_size 600m;
   proxy_request_buffering off;
   proxy_read_timeout 600s;
   proxy_send_timeout 600s;
   ```
3. En `.env`: `COOKIE_SECURE=true`, `TRUSTED_ORIGINS=https://cleaner.<tu dominio local>`, y `FORWARDED_ALLOW_IPS=<IP desde la que NPM se conecta>` (se ve en `docker compose logs api` como origen de las peticiones) para que los límites por IP usen la IP real del cliente. Luego `docker compose up -d`.
4. Opcional: dejar de exponer `:8480` a la LAN cambiando `BIND_ADDRESS` a una dirección a la que solo NPM llegue. Requiere revisar cómo está conectado NPM; no se hace sin tu permiso.

## 8. Ruta alternativa sin git (zip)

Desde PowerShell en el PC, con el `.zip` entregado:

```powershell
scp .\metadata-cleaner.zip <usuario>@<IP_LAN>:~/
ssh <usuario>@<IP_LAN> "mkdir -p ~/metadata-cleaner && cd ~/metadata-cleaner && unzip -o ~/metadata-cleaner.zip"
```

Luego seguir la sección 1 desde el paso 3. Las actualizaciones por esta vía requieren repetir la copia; se recomienda la ruta git.
