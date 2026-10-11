# Privacidad

## Principios

- Todo el procesamiento ocurre en este servidor. Ningún archivo ni metadato se envía a terceros; el contenedor que procesa archivos no tiene red.
- Almacenamiento temporal: lo mínimo, el menor tiempo posible.
- Sin promesas de anonimato: la herramienta elimina metadatos conocidos, no analiza el contenido.

## Qué se guarda y cuánto tiempo

| Dato | Dónde | Duración |
| --- | --- | --- |
| Archivo original | volumen `mc_work` | Se borra en cuanto termina su procesamiento, con éxito o con error. Si la subida es rechazada, se borra de inmediato. |
| Archivo limpio | volumen `mc_work` | `RETENTION_MINUTES` (60 por defecto) después de terminar el lote, o antes si el usuario pulsa "Eliminar ahora". |
| Informe detallado (etiquetas **con valores**) | junto al archivo limpio | Igual que el archivo limpio. |
| Lote sin iniciar (borrador) | `mc_work` | Vence a los `RETENTION_MINUTES` desde su creación. |
| Historial: nombre del archivo, formato, modo, estado, fechas, resumen con **nombres** de etiquetas (sin valores) | base SQLite | `HISTORY_RETENTION_DAYS` (30 por defecto). |
| Cuenta de usuario y hash de contraseña | base SQLite | Hasta que un administrador elimine el usuario. |
| Sesiones (solo hash del token) | base SQLite | Hasta 24 h o 8 h de inactividad. |
| Registros de los contenedores | Docker (json-file) | Rotación 3 x 10 MB; no contienen nombres de archivo ni metadatos. |

La interfaz muestra en cada lote la cuenta regresiva hasta la eliminación automática y el aviso correspondiente.

## Borrado verificado

Cada borrado se comprueba. Si falla, se registra en `deletion_failures`, el lote queda marcado como "eliminación pendiente" (visible para el usuario y en la página de administración) y se reintenta en cada ciclo de limpieza (cada minuto). Nunca se informa como borrado algo que sigue en disco.

Los directorios huérfanos (por ejemplo, tras un corte de energía) se eliminan al arrancar y periódicamente.

Nota: el borrado es a nivel de sistema de archivos. Un disco no cifrado puede conservar datos recuperables forenses después de borrar. Si eso importa, use cifrado de disco en el servidor.

## Respaldos

Solo se respalda la base de datos y `.env`. El volumen `mc_work` (archivos de usuarios) queda excluido por diseño. Ver OPERATIONS.md.

## Administradores

Los administradores gestionan cuentas y ven el estado del sistema, pero **no** pueden ver ni descargar archivos de otros usuarios desde la aplicación. Quien tenga acceso de administrador al servidor sí podría leer los volúmenes mientras los archivos existan.
