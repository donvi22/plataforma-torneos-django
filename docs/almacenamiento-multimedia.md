# Multimedia persistente: diseño y preparación de integración

Estado: implementado y probado sin conexiones reales. Solo rama develop.
No se han cambiado Railway, IONOS, credenciales, DNS, archivos remotos ni datos reales.

## Inventario comprobado en el código

| Campo | Antes | Después |
| --- | --- | --- |
| Usuario.avatar | IonosAvatarStorage explícito | Sin cambios |
| Videojuego.imagen | default_storage local | PersistentMediaStorage |
| Torneo.imagen_banner | default_storage local | PersistentMediaStorage |
| PremioTorneo.imagen | default_storage local | PersistentMediaStorage |
| Videojuego.imagen_url | URL externa, sin subida | Sin cambios |

No hay otros FileField/ImageField en modelos en esta revisión. El FileField del
formulario de avatar no es un campo adicional de base de datos. Los archivos de
static/ y collectstatic/WhiteNoise son estáticos; no usan este almacenamiento.

No se inventarió la base pública ni el contenido SFTP. No se puede afirmar que
cada imagen publicada exista físicamente, que las referencias locales tengan
copias en IONOS, ni cuál es la raíz física del dominio público. Los templates
siguen usando las URLs del campo y la prioridad existente de imagen_url en el
componente de videojuegos. No se reescriben esas URLs externas.

## Correspondencia de nombres, rutas y URLs

| Referencia en base de datos | Ruta de archivo | URL |
| --- | --- | --- |
| avatars/foto.png | foto.png relativo al directorio inicial SFTP, comportamiento existente | https://media.rankedkingdom.com/avatars/foto.png |
| torneos/antiguo.png (también videojuegos/ y premios/) | MEDIA_ROOT/torneos/antiguo.png | MEDIA_URL + torneos/antiguo.png, sin cambio |
| ionos_v1/torneos/UUID.png | IONOS_MEDIA_REMOTE_ROOT/torneos/UUID.png | IONOS_MEDIA_PUBLIC_BASE_URL + torneos/UUID.png |

`ionos_v1/` es un identificador lógico persistido, **no** una carpeta que se añade
al destino remoto ni a la URL pública. Las categorías permitidas son torneos,
videojuegos y premios. Los UUID son hexadecimales de 32 caracteres.

El directorio inicial SFTP de avatares y su URL NO demuestran que se pueda
escribir otro contenido junto a ellos. Por eso la nueva raíz requiere una ruta
absoluta explícita dentro del espacio visible por esa cuenta SFTP. Una ruta del
panel web puede diferir de la ruta visible en el chroot SFTP.

## Configuración (preparación, no aplicada)

- DJANGO_MEDIA_STORAGE: local por defecto con DEBUG=True; ionos con DEBUG=False.
  La carga de settings rechaza local en producción. Django test puede anular
  DEBUG, sin cambiar el modo local escogido al cargar settings.
- IONOS_MEDIA_REMOTE_ROOT: directorio absoluto **dedicado y verificado**, sin barra
  final, sin '..', sin directorios vacíos ni enlaces simbólicos.
- IONOS_MEDIA_PUBLIC_BASE_URL: URL HTTPS en media.rankedkingdom.com, carpeta
  dedicada distinta de / y /avatars/, con barra final.
- IONOS_SFTP_USERNAME / IONOS_SFTP_PASSWORD: las variables del transporte existente;
  no se guardan en código. El servidor y la huella SSH siguen fijados por el
  transporte de avatares. Su identidad se comprueba ANTES de enviar credenciales.

Los valores reales de raíz y URL NO están determinados en el repositorio y no
se suministra un valor por defecto inventado. Si faltan, las subidas nuevas en
modo ionos fallan explícitamente; nunca se guardan en disco efímero como fallback.
`manage.py check --deploy` incluye usuarios.E001 para una configuración de mapeo
incompleta/inválida. Ese check es offline: NO acredita la existencia de carpetas,
el funcionamiento de credenciales ni la correspondencia física con el dominio.

Las carpetas deben existir previamente: raíz dedicada y subdirectorios torneos,
videojuegos y premios. El código NO las crea, cambia permisos ni modifica la
configuración del alojamiento. Se comprueba con lstat cada padre de la ruta,
rechazando enlaces simbólicos o componentes que no sean directorios.

## Flujo de escritura y seguridad

PersistentMediaStorage enruta todas las nuevas subidas no-avatar en producción:

1. Valida categoría, nombre original y ausencia de traversal, barras invertidas,
   controles o codificaciones ambiguas. No acepta identificadores remotos como
   nombres originales para una nueva subida.
2. Lee como máximo 5 MB + 1 byte; verifica PNG/JPEG/GIF/WebP con Pillow y un límite
   de 16 megapíxeles. El nombre final y extensión se generan a partir de UUID y
   formato real, sin confiar en nombre/extensión original. No hay archivos de
   premios arbitrarios en el modelo actual: solo imágenes.
3. Abre una conexión SFTP verificada, valida la ruta absoluta y los padres.
4. Rechaza destinos existentes; sube con wx a un temporal único **en la misma
   carpeta del destino**, comprueba tipo regular y tamaño, y usa rename estándar
   SFTP sin sobrescritura. NO usa posix_rename.
5. Devuelve el identificador persistente solo tras publicar el archivo completo.
   El ImageField almacena ese identificador y genera su URL por el mismo router.

Un error no produce fallback a local ni elimina el destino existente. Solo se
intenta retirar el temporal creado por esa operación; si la limpieza falla se
registra, preservando la excepción original. Las vistas de creación y edición
de torneos muestran un error de banner recuperable ante errores de E/S, con
rollback antes de volver a consultar la base de datos.

Lectura, tamaño y eliminación comprueban rutas y tipo de archivo. La lectura
remota está limitada. delete no elimina carpetas ni usa rutas arbitrarias y solo
ignora FileNotFoundError; no oculta errores de permisos. path no está disponible
para archivos remotos (no son archivos del filesystem de Railway).

La sustitución genera un archivo nuevo. No se añade borrado automático del
anterior: podría tener otras referencias. No hay recolector ni migración de
datos. Una subida completada seguida de un fallo de base de datos podría dejar
un archivo huérfano; revisar referencias y backups antes de cualquier limpieza.
Las verificaciones lstat no eliminan carreras frente a un actor con permiso de
modificar simultáneamente el árbol remoto; la raíz debe estar bajo control de la
cuenta del servicio y no ser escribible por terceros.

## Compatibilidad y archivos existentes

- Avatares: clase, nombres, URLs y procedimientos de sustitución/borrado intactos.
- Referencias locales: mantienen ruta, lectura y URL anteriores. No se asume una
  copia remota; los enlaces antiguos ya rotos no se reparan inventando URLs.
- imagen_url: no se descarga, cambia ni migra.
- Los modelos no cambian: no se necesita una migración de esquema. El backend
  predeterminado configurable es usado por los tres ImageField no-avatar.
- Restaurar una copia de base con identificadores ionos_v1 requiere mantener la
  configuración IONOS; esos identificadores no se reinterpretan como locales.
- La configuración local de desarrollo sigue usando MEDIA_ROOT. Los tests
  requieren directorios temporales y SFTP simulado; jamás credenciales reales.

## Pasos antes de una futura integración con producción (no ejecutados)

1. Confirmar que Railway publica solo deploy-production y revisar este cambio
   aparte; no fusionar ni desplegar automáticamente desde develop.
2. Obtener un inventario con una copia autorizada de datos y backups de los
   archivos. Separar avatares, imagen_url, medios locales y rutas desconocidas.
   Comprobar que el prefijo lógico ionos_v1 no se usa ya en referencias heredadas.
3. Identificar con el propietario el chroot/directorio inicial SFTP y la raíz
   física que sirve media.rankedkingdom.com. Elegir una carpeta NUEVA dedicada,
   fuera del directorio de avatares; crear sus tres subdirectorios solo con
   autorización y sin modificar archivos existentes.
4. Verificar el mapeo con un archivo de prueba NUEVO: escribir en esa ruta
   absoluta y comprobar por HTTPS su contenido exacto en la URL correspondiente.
   Una URL que responde 200 o una carpeta del mismo nombre no son prueba suficiente.
   Este ensayo real necesita autorización posterior; no forma parte de los tests.
5. Confirmar huella SSH y disponibilidad IPv6 (el transporte existente usa IPv6),
   permisos mínimos, timeout, límites de espacio, copias de seguridad y que el
   servidor SFTP aplica rename estándar sin sobrescritura.
6. Preparar los valores reales de las variables; mantener las credenciales fuera
   del código. Ejecutar checks de producción simulados y pruebas en entorno
   aislado. Una configuración sintácticamente válida no sustituye al paso 4.
7. Para medios heredados, diseñar por separado una copia no destructiva y un
   manifiesto verificable (referencia, origen, destino, tamaño/hash). No mover,
   borrar ni actualizar filas hasta verificar las copias y disponer de rollback.
   Esta fase NO incluye ni ejecuta ese importador ni cambios de referencias.
8. Solo con autorización posterior: integrar, configurar, ensayar nuevas subidas,
   sustituciones y reinicios, y verificar URLs y persistencia. No borrar los
   archivos previos durante la validación ni durante un rollback.

## Validación automatizada

Las pruebas cubren rutas absolutas y URLs, bytes subidos, identificadores en
ImageField, colisiones, rename concurrente fallido, padres o archivos simbólicos,
subidas parciales, fallos de conexión y limpieza, lectura/tamaño/borrado,
compatibilidad local, reemplazo sin borrado implícito y error recuperable en las
vistas de torneos. Los tests de avatares conservan su cobertura de huella y
credenciales, con mocks. No se ejecutan operaciones contra el servidor real.
