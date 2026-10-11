# Licencias

## Licencia de este repositorio: pendiente de decisión

El repositorio es público y **no tiene licencia**: el código se puede ver, pero nadie más tiene permiso para usarlo, copiarlo ni modificarlo (todos los derechos reservados). Así queda hasta que el propietario decida. Opciones habituales:

| Licencia | Efecto |
| --- | --- |
| MIT o Apache-2.0 | Permisivas: cualquiera puede reutilizar, incluso en software cerrado. Apache-2.0 agrega licencia de patentes. |
| GPL-3.0 | Las versiones distribuidas deben publicarse bajo GPL. |
| AGPL-3.0 | Como GPL, y además obliga a publicar el código si se ofrece como servicio en red. Común en software autoalojado. |
| Ninguna (actual) | Uso privado del propietario; terceros no tienen permisos. |

Para aplicarla: agregar un archivo `LICENSE` con el texto oficial y actualizar el README.

## Dependencias

El repositorio no incluye binarios de terceros. Las imágenes se compilan en el servidor desde paquetes Debian, PyPI y npm. Uso privado autoalojado sin redistribución: ninguna de estas licencias impone obligaciones adicionales. Si algún día se **publican imágenes Docker** (redistribución), se deben respetar las obligaciones de GPL/LGPL (ofrecer el código fuente correspondiente; Debian lo publica para sus paquetes) y conservar avisos de licencia.

| Componente | Licencia | Uso |
| --- | --- | --- |
| MAT2 0.13.5 | LGPL-3.0-or-later | ejecutado como programa (sin enlazar) |
| ExifTool 13.25 | Artistic License o GPL-1.0-or-later (términos de Perl) | ejecutado como programa |
| FFmpeg 7.1.5 (Debian) | LGPL-2.1+ en general; el build de Debian incluye componentes GPL (x264, x265...), por lo que el binario es GPL-2.0-or-later | ejecutado como programa |
| pikepdf 9.5 / qpdf | MPL-2.0 / Apache-2.0 | importado en el worker |
| Pillow 11.1 | MIT-CMU (HPND) | importado en el worker |
| poppler, cairo, librsvg, GdkPixbuf (dependencias de MAT2) | GPL-2/LGPL/MPL según componente | vía MAT2 |
| FastAPI, Starlette, uvicorn, Pydantic, argon2-cffi | MIT / BSD-3-Clause | api |
| React, Vite, TypeScript | MIT / Apache-2.0 | frontend |
| Fraunces, Instrument Sans, JetBrains Mono (Fontsource) | SIL Open Font License 1.1 | fuentes autoalojadas |
| Imágenes base Debian / Python / Node | licencias de sus paquetes | compilación |

Dependencias solo de pruebas: pytest (MIT), httpx (BSD-3), python-docx, openpyxl, python-pptx (MIT), libheif (LGPL-3.0).

Conclusión: compatible con uso privado autoalojado. La elección de licencia del repositorio no está restringida por estas dependencias porque se usan como programas externos o bibliotecas con licencias permisivas o débilmente copyleft; con AGPL/GPL tampoco hay conflicto.
