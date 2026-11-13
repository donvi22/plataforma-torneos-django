# Fase 4 — CI y preparación de producción

**Preparación únicamente. No autoriza despliegues, cambios de infraestructura ni
consultas a bases reales.** Fecha: 2026-11-13.

## Estado y alcance comprobados

- Base `develop`: `fa5479cb55729e2644ea676087f25c1233e85c2d`.
- `deploy-production`: `b9b49cd6a65265f8791c619a5b0eea5a341895f1`.
- `main`: `76b2f775e0f3a85bf4c79c1bb8276cb8ba58ad80`, reservada al curso.
- Comparación GitHub: develop 21 commits por delante, ninguno por detrás,
  24 archivos diferentes antes de esta fase. No se modifica ninguna otra rama.
- No hay `.github/workflows` en la base auditada. La API de ejecuciones devuelve
  cero ejecuciones; el endpoint de catálogo de workflows no está soportado por
  el conector. Esto no acredita las políticas de Actions de la cuenta.
- No se han consultado Railway, IONOS, DNS, variables, archivos ni bases reales.

Pendiente de integrar: aislamiento de tests de avatares; almacenamiento persistente
no-avatar; credenciales multimedia separadas; manejo recuperable de errores de
banners; integridad concurrente de XP; declaraciones/resultados/bracket y migración
0006; arbitraje, calendario y check-in; esta CI y sus documentos.
El único cambio de esquema acumulado respecto de producción es
`partidas/0006_declaracion_unica_por_usuario.py` (comprobarlo de nuevo al integrar).
No hay migración multimedia ni reparación automática de datos históricos.

## Diseño de CI

`.github/workflows/ci.yml` se activa por push a develop y PR cuyo destino es
develop. El disparo manual solo admite la referencia develop. No usa
`pull_request_target`, secretos, environments de producción, SSH, comandos de
despliegue ni un token con escritura. `contents: read` y checkout sin persistir
credenciales; actions oficiales fijadas por SHA. Runner Ubuntu 24.04,
Python 3.14.10, servicio PostgreSQL 17.11-bookworm con healthcheck y puerto loopback.
Las credenciales literales `ci-only-not-a-secret` pertenecen exclusivamente al
contenedor efímero, no a una cuenta externa.

`requirements-ci.lock` fija dependencias directas y transitivas con hashes,
derivadas de requirements.txt, que no cambia. Instalación con `--require-hashes`
y `--only-binary=:all:`; `pip check` detecta incompatibilidades y un cotejo de versiones instaladas con
requirements.txt impide aceptar un lock desactualizado respecto a sus seis entradas. Para actualizar,
regenerar en Python 3.14, revisar versiones/hashes y repetir CI:

```sh
uv pip compile requirements.txt --python-version 3.14 --generate-hashes \
  --default-index https://pypi.org/simple --no-emit-index-url --no-emit-find-links \
  --output-file requirements-ci.lock
```

La configuración `config.ci_settings` es exclusivamente de pruebas: host
127.0.0.1, base rankedkingdom_ci, base test test_rankedkingdom_ci, usuario fijo,
puerto local configurable por CI_POSTGRES_PORT, almacenamiento temporal local y
mailer en memoria. Rechaza variables PG*, DATABASE_URL, IONOS_* y RAILWAY_*
ambientales y DEBUG de producción. No usarla como configuración del servicio web.
No modifica PASSWORD_HASHERS. Las excepciones locales de hash rápido que ya
existían en algunas clases de tests permanecen intactas.

`config.ci_runner.StrictPostgresRunner` comprueba PostgreSQL, nombres/host de la
base aislada y soporte fork. Exige descubrir al menos las 3 pruebas concurrentes
de XP, 7 de resultados y 20 de arbitraje; rechaza sus skips y convierte cualquier
omisión final en fallo. Las conexiones de socket Python externas se bloquean
durante tests (también en hijos fork); libpq usa la configuración fija loopback.
No es un cortafuegos del sistema ni una defensa ante código de test malicioso.
La instalación desde PyPI y la descarga de actions suceden antes de esta guarda.

Se ejecutan check, makemigrations --check --dry-run y **una sola suite completa
PostgreSQL**, sin duplicarla con otra ejecución concurrente parcial ni SQLite.
Django crea/migra/destruye su base de test; no hay un paso migrate/predeploy ni
conexión a producción. Cada comando falla normalmente: sin continue-on-error,
sin `|| true`, ni filtros que conviertan errores en éxito. Timeout 25 minutos y
cancelación de ejecuciones antiguas de la misma referencia.

El nombre del job es `django-postgresql`. Exigirlo antes de integrar es un requisito
operativo; cambiar protección de ramas/permisos NO forma parte de esta tarea.
El disparo manual puede no aparecer hasta que GitHub tenga el workflow en la rama
predeterminada: no cambiar main para resolverlo. El push a develop es el mecanismo
previsto. Si GitHub rechaza escribir workflows, detener la publicación, no cambiar
permisos ni intentar otra vía.

### Estado real de validación

Validación real en Work, con los archivos de CI de esta fase:

| Comprobación | Resultado real |
| --- | --- |
| Instalación de requirements-ci.lock con hashes y solo wheels | Correcta; 15 dependencias, sin cambiar requirements.txt |
| pip check y cotejo de las seis dependencias directas | Correctos |
| Suite completa PostgreSQL 17.11, Python 3.14.10, hasher habitual | **596/596 superadas**, 0 fallos, 0 errores, 0 omitidas; 266,258 s |
| Cobertura concurrente descubierta y ejecutada | 3 XP + 7 resultados + 20 arbitraje; todas superadas dentro de la misma suite |
| Pruebas nuevas de guardas de CI | 11, incluidas en las 596; no se modifican pruebas anteriores |
| check y makemigrations --check --dry-run | Sin incidencias; sin cambios de migraciones |
| actionlint 1.7.11, binario oficial con SHA-256 comprobado | Workflow válido; revisión adicional manual de permisos/triggers/destinos |
| Auditoría SQL en PostgreSQL sintético | Ejecutable antes/después de 0006; detecta duplicados/XP/capacidad/incompatibilidad/check-in sintéticos sin cambiar filas |
| Protección READ ONLY de PostgreSQL | Rechaza una escritura sintética dentro de transacción de lectura |
| git diff --check | Limpio |
| Ejecución real GitHub Actions | **No ejecutada/no verificada todavía** |

Work usa el servidor PostgreSQL aislado en 127.0.0.1:55439 (el workflow usa 5432),
con el mismo módulo de settings y runner. No se ejecutó el contenedor Docker del
workflow en Work; el arranque/imagen del servicio en Actions debe comprobarse en
un run remoto. No se realizaron ensayos de carga sostenida ni restauración de
copias reales. El YAML y los pasos se validan; no se declara CI operativa sin run.

YAML válido y comandos locales verdes no prueban una ejecución real de Actions.
Antes de considerar CI operativa, debe existir un run de GitHub ligado al SHA
publicado con checkout/setup/servicio/install/check/migrations/tests verdes y
cero tests omitidos. No existe aún esa evidencia al preparar este documento.

## Requisitos obligatorios antes de publicar la aplicación

| ID | Evidencia exigida | Estado / motivo |
| --- | --- | --- |
| G1 | Diff completo e integración exacta revisados, CI del SHA objetivo verde | Pendiente de ejecución remota y autorización de integración |
| G2 | Versión/configuración PostgreSQL destino identificada sin exponer secretos | No consultada; repetir validación sobre versión equivalente |
| G3 | Auditoría histórica en clon autorizado, migración 0006 ensayada y decisiones de remediación aprobadas | No hay datos reales disponibles; prohibido resolver duplicados automáticamente |
| G4 | Copias DB y multimedia verificadas, restauración ensayada y RPO/RTO aprobados | No verificado |
| G5 | Cuenta SFTP independiente y raíz/URL/huella/permisos demostrados | No verificado; bloquea nuevas subidas multimedia |
| G6 | Inventario y recuperación de medios locales heredados | No inventariados; Railway es efímero |
| G7 | Variables de producción revisadas por propietario, sin valores en Git | No consultadas ni configuradas |
| G8 | Ensayo de carga/concurrencia aislado y sin pérdidas/duplicados/bloqueos persistentes | Regresiones concurrentes existentes; carga sostenida pendiente |
| G9 | Ventana de mantenimiento, responsable, criterios de abortar y rollback compatible | Procedimiento propuesto abajo, no ejecutado |
| G10 | Autorización expresa de integración y despliegue | Esta fase no la concede |

### IONOS y multimedia: bloqueo de activación

Los siguientes datos son **evidencia documental de una investigación anterior**
en [almacenamiento-multimedia.md](almacenamiento-multimedia.md), no una consulta
nueva: el panel mostraba el dominio multimedia dirigido a `/media`, Explorer
`/home/www/media`, carpetas avatars/videojuegos y cuenta existente acotada a
`/media/avatars`. Esto NO demuestra el path visible en el chroot del nuevo acceso,
ni correspondencia de bytes entre SFTP y HTTPS. No se da por válida la ruta
`/home/www/media/uploads` ni se propone mover archivos existentes.

Obligatorio, posteriormente y con autorización específica:

- Conservar cuenta, directorio y variables IONOS_SFTP_USERNAME/PASSWORD de avatares.
- Preparar cuenta independiente de nuevos medios con permisos mínimos en carpeta
  dedicada. Verificar raíz absoluta visible SFTP, padres sin symlinks y subcarpetas
  torneos/videojuegos/premios existentes. El código no crea estas carpetas.
- Determinar IONOS_MEDIA_REMOTE_ROOT y su correspondencia exacta con
  IONOS_MEDIA_PUBLIC_BASE_URL: HTTPS en media.rankedkingdom.com y carpeta dedicada.
  `ionos_v1/` es un prefijo lógico de DB, no una carpeta remota adicional.
- Confirmar identidad/huella SSH fijada antes de autenticar, conectividad IPv6
  del transporte actual, cuota, permisos de lectura/escritura/rename exclusivo,
  tiempos de espera y tratamiento de subidas parciales. No desactivar la huella.
- Verificar TLS público y del origen: el documento anterior detectó ausencia de
  certificado asignado en el panel; no demuestra el estado efectivo de TLS actual.
- Solo con autorización para una prueba real: subir un archivo NUEVO a esa raíz,
  recuperar por HTTPS sin caché, comparar bytes/hash, comprobar sustitución y
  persistencia tras un reinicio controlado. Autorizar por separado su limpieza;
  nunca sobrescribir/borrar archivos existentes para probar.
- Inventariar referencias locales antiguas y copias existentes. Diseñar copia
  no destructiva con manifiesto de origen/destino/tamaño/hash y rollback antes
  de actualizar referencias. No migrar por cambiar únicamente URLs.

### Variables y dependencias externas

Revisar nombres y presencia sin exportar valores al informe/logs:

- DJANGO_DEBUG=False; DJANGO_SECRET_KEY privada y fuerte; DJANGO_ALLOWED_HOSTS
  explícitos y DJANGO_CSRF_TRUSTED_ORIGINS HTTPS válidos.
- PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD y PGSSLMODE según conectividad/TLS
  verificada; no copiar `sslmode=disable` ni el usuario/clave de CI a producción.
- DJANGO_MEDIA_STORAGE=ionos; IONOS_MEDIA_REMOTE_ROOT;
  IONOS_MEDIA_PUBLIC_BASE_URL; IONOS_MEDIA_SFTP_USERNAME/PASSWORD independientes;
  IONOS_SFTP_USERNAME/PASSWORD originales intactos.
- TORNEO_DEMO=0; TORNEO_DEV_LOCAL=0; PORT gestionado por la plataforma.
- Mantener política de correo existente. Esta fase no configura SMTP ni implementa
  correos; no habilitar flujos que requieran entrega externa sin validación aparte.

Dependencias: Railway/imagen Linux, PostgreSQL, IONOS SFTP + HTTPS, rutas heredadas,
PyPI/GitHub/Docker Hub para CI. No se añade proveedor de pago. La versión PostgreSQL
real, configuración efectiva y backups son desconocidos en esta revisión.

## Auditoría histórica de solo lectura (no ejecutada sobre datos reales)

Usar [auditoria-previa-produccion.sql](auditoria-previa-produccion.sql) primero en
un clon restaurado y anonimizado, con usuario solo lectura y autorización de
acceso. Modo READ ONLY + REPEATABLE READ, límites de 30 s/3 s, agregados sin nombres,
correos, claves ni contraseñas. No SELECT FOR UPDATE ni llamadas a servicios de
concesión/validación/procesamiento. Verificar primero esquema hasta 0005.
Ejemplo posterior, sin credenciales en argumentos ni documentos:

```sh
psql -X -v ON_ERROR_STOP=1 -f docs/auditoria-previa-produccion.sql
```

El operador debe escoger/verificar el destino autorizado en su entorno seguro.
No ejecutar con variables o accesos de producción heredados por comodidad. Si un
statement falla, abortar y cerrar/rollback la transacción de lectura; no continuar
con resultados parciales. Las consultas pueden leer muchas filas: ensayar tamaño
y tiempos en clon antes de aprobar una ventana de lectura en el servicio real.

Interpretación obligatoria:

- `bloqueante_declaraciones_duplicadas` debe ser 0. La 0006 se detiene preservando
  registros si encuentra duplicados por partida/usuario. No elegir el último ni
  borrar datos automáticamente. Resolver por procedimiento independiente aprobado,
  respaldado y con evidencia de qué declaración se conserva y por qué.
- Revisar aritmética, continuidad, relaciones y saldo/nivel de XP. **No comparar
  sin más saldo con SUM(xp_concedida): puede existir XP inicial anterior al historial.**
  La primera fila no demuestra saldo inicial cero. Las recompensas ausentes son
  candidatos: actividad/elegibilidad actual no demuestra elegibilidad al cierre.
- Revisar capacidades arbitrales, participante/árbitro activo, cadena de asignación
  y responsable. Una suspensión posterior puede explicar inactividad; no equivale
  por sí sola a una carrera ni autoriza reasignación automática.
- Revisar confirmaciones de responsables anteriores solo antes de comenzar,
  fechas de confirmación, partidas listas incompletas y resultados/progresión.
  La reasignación histórica tras finalizar es comportamiento permitido del MVP.
- Los conteos de medios son inventario lógico, no prueba de existencia física o
  entrega HTTPS. No se leen archivos privados ni se hacen peticiones remotas.
- Los avisos críticos históricos ausentes no se reconstruyen automáticamente.
  En clon, contrastar por evento/usuario con sus políticas (cuentas eliminadas
  excluidas) y decisiones de remediación; las claves antiguas y nuevas difieren.

La auditoría no certifica por sí sola todos los historiales ni recalcula reglas de
cada torneo. Para profundizar se necesitaría otra autorización y consultas
acotadas con IDs mínimos, nunca dump de contraseñas/lobbies o datos personales.

## Ensayo aislado de migración, carga y restauración

1. Restaurar una copia autorizada en infraestructura separada, sin salidas SFTP,
   SMTP o servicios reales. Aplicar políticas de anonimización y acceso mínimo.
2. Confirmar versión mayor, collation, extensiones, READ COMMITTED y permisos del
   destino; ejecutar check, plan de migraciones y auditoría de lectura en el clon.
3. Ensayar 0006 sobre datos representativos: duración, volumen, espacio, espera y
   bloqueos. Crear la restricción puede bloquear escrituras; no es un índice
   concurrente. Si hay duplicados, abortar sin modificar historia.
4. Ensayar carga con 2, 4 y 8 workers/procesos independientes: varias partidas del
   mismo torneo, torneos distintos con usuarios compartidos, XP simultánea,
   aceptar/inscribir, asignar/reasignar, programar/check-in/resultado. Propuesta:
   50 operaciones por escenario y nivel de concurrencia, datos sintéticos y
   límites lock_timeout/statement_timeout. Nunca contra endpoints públicos.
5. Registrar p50/p95, errores, deadlocks, timeouts, conteos y correspondencia de
   saldo/historial, resultados/bracket y avisos. Tolerancia de integridad: 0 pérdidas,
   duplicados o decisiones contradictorias. Los conflictos de negocio esperados
   se distinguen de errores técnicos. Acordar presupuesto de latencia y ensayo
   de picos antes de certificar capacidad; no afirmar una cifra de usuarios reales.
6. Restaurar backups DB y multimedia en otro destino temporal y verificar versiones,
   cantidades, hashes y muestra de referencias. Documentar RPO/RTO alcanzados y
   responsable. Una copia creada sin restauración comprobada no cumple G4.

## Orden recomendado del despliegue futuro (NO ejecutar ahora)

1. Obtener autorización, responsables y ventana; cerrar G1–G10. Fijar SHA objetivo
   de develop y registrar SHA de la versión pública. No mezclar cambios sin revisar.
2. Preparar y verificar infraestructura/mapeo multimedia autorizado antes de que
   el nuevo código acepte subidas. Mantener los archivos y acceso de avatares.
3. Acordar el control de auto-deploy antes de tocar deploy-production para que
   ninguna integración dispare un proceso fuera de esta secuencia. Bloquear
   escrituras competitivas y subidas por un mecanismo de mantenimiento
   previamente ensayado; detener jobs manuales/cron/workers que escriban. El MVP
   no aporta aquí un nuevo interruptor de mantenimiento: elegir mecanismo válido
   en el ensayo. Evitar convivencia de writers antiguos sin bloqueos y nuevos.
4. Tomar backup final consistente de DB y manifiesto/copias multimedia; registrar
   punto de corte, versión y comprobación de restaurabilidad. Repetir auditoría
   de duplicados y confirmar plan de rollback.
5. Preparar la configuración del nuevo código en el contexto controlado previsto;
   ejecutar check --deploy y revisar cada advertencia (HSTS es una decisión
   explícita, no silenciarla). Revisar plan de migraciones y prechecks históricos
   con el punto de corte sin escrituras. No continuar ante cualquier bloqueo.
6. Integrar exactamente el SHA revisado en deploy-production **solo con autorización
   posterior**. Según [railway.md](railway.md): build valida entorno y ejecuta
   collectstatic; no migra. El único pre-deploy autorizado ejecuta migrate y aplica
   0006 después de los prechecks. No migrar desde start ni en cada réplica.
   Si falla la migración, abortar sin borrar declaraciones ni fingir su aplicación.
7. Arrancar nueva versión, comprobar salud y checklist siguiente con escrituras
   todavía restringidas. Liberar tráfico/jobs gradualmente solo tras aprobación.

## Checklist posterior futuro

- SHA/imagen y migraciones aplicadas coinciden con lo aprobado; logs sin secretos.
- HTTPS/proxy/hosts/CSRF, login y permisos; estáticos con manifiesto correcto.
- Avatares históricos accesibles y comportamiento intacto; medios heredados
  inventariados; prueba autorizada de nueva subida con bytes/URL/persistencia.
- Torneo de verificación expresamente autorizado o entorno de ensayo: inscripción,
  arbitraje, horarios, check-in, declaración, validación y bracket; XP/Karma y
  notificaciones consistentes. No crear torneos/pruebas reales sin ese permiso.
- Lectura acotada autorizada de invariantes después del corte; monitorizar latencia,
  errores 5xx, deadlocks, lock waits, fallos SFTP, espacio y avisos críticos.
- Jobs manuales/cron restaurados una sola vez; no duplicar procesadores.
- Conservar respaldos y versión compatible hasta finalizar ventana de observación.

## Rollback y criterios de abortar

Abortar ante 0006 fallida, mapeo multimedia no demostrado, pérdida/duplicación,
resultados contradictorios, fallos persistentes de SFTP, errores 5xx o presupuesto
de espera/latencia incumplido en ensayo. Mantener escrituras pausadas y preservar
logs sin secretos, backup y manifiestos.

**No volver ciegamente al SHA público antiguo**: no sabe leer `ionos_v1/` y sus
writers no respetan el nuevo protocolo; 0006 inversa quita una defensa de unicidad.
Antes de integrar hay que preparar una versión de reversión compatible que
conserve el lector/configuración multimedia, las credenciales separadas y el
esquema, o decidir una restauración completa a un punto de corte aprobado.

- Si falla antes de migrar/aceptar escrituras nuevas: conservar datos/archivos y
  arrancar versión previamente ensayada compatible con referencias existentes.
- Si 0006 falla por duplicados: PostgreSQL revierte su migración atómica; verificar
  registro/constraint antes de decidir. No fingirla aplicada ni borrar historia.
- Si 0006 termina y falla la aplicación: preferir rollback de código compatible,
  manteniendo el esquema. No ejecutar migrate partidas 0005 automáticamente.
- Si hubo escrituras nuevas: mantener pausa y elegir recuperación hacia delante o
  restauración consistente DB+media con aprobación de pérdida potencial (RPO).
  Nunca restaurar una DB antigua sobre archivos eliminados ni borrar nuevas
  imágenes por no aparecer en el backup. Conservar todas las generaciones.
- Restauración se ensaya en destino separado; corte de recuperación requiere
  autorización. Verificar invariantes, URLs, permisos y SHA antes de reabrir.

## Mejoras opcionales (no bloquean por sí solas)

Informes JUnit/artefactos de tests sin datos sensibles, métricas históricas de CI,
automatización de actualización de dependencias revisada, pin de imagen PostgreSQL
por digest verificado, avisos de salud/colas, observabilidad sostenida y futuros
ensayos de carga mayores. No implementar equipos ni correo en esta fase.

La mejora opcional no sustituye ningún requisito G1–G10. Ninguna instrucción de
este documento autoriza operar producción ni cambiar permisos/servicios.


## Resultado de publicación de esta fase

La integración GitHub rechazó la creación del árbol que contenía
`.github/workflows/ci.yml`: HTTP **403**, `Resource not accessible by integration`,
endpoint `POST /repos/donvi22/plataforma-torneos-django/git/trees`.
No permite determinar si falta permiso general de escritura o uno específico
para workflows. No se solicitaron permisos nuevos ni se intentó el navegador u
otra vía después del rechazo, conforme al límite expreso de esta fase.

**No hay commits de fase 4 publicados ni workflow activo/verificado.** El código,
lock y documentos quedan en develop local, validados, con parche para revisión.
Última comprobación remota después del bloqueo: develop sigue en fa5479c; main
sigue en 76b2f775 y deploy-production en b9b49cd6. La API Actions sigue devolviendo
0 ejecuciones. El siguiente paso pendiente es resolver la publicación por el
propietario dentro de sus procedimientos autorizados; no se debe considerar
cerrado G1 ni habilitar integración/despliegue por estos resultados locales.
