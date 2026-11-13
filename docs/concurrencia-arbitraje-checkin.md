# Fase 3.3 — Arbitraje, programación y check-in

Base auditada: `develop`, `951d97328b9841219f5a220bf6a6aa5ae6a5f899`.
Fecha de validación: 13 de noviembre de 2026. Solo datos sintéticos aislados.

## Defectos y evidencia

Reproducciones sobre la base anterior, PostgreSQL 17.11, dos procesos Python
independientes y conexiones distintas. Barreras SQL después de las lecturas
vulnerables, sin sustituir las escrituras ni el motor:

| Problema | Evidencia previa | Corrección |
| --- | --- | --- |
| Límite de invitaciones pendientes | Con una pendiente y límite 2, dos invitaciones distintas terminan correctamente y quedan **3 pendientes**. | Bloquear y releer el torneo antes de comprobar estado, elegibilidad, duplicados y capacidad. |
| Doble asignación inicial | Dos procesos leen la misma partida sin árbitro y dejan **2 historiales iniciales**, ambos con árbitro anterior nulo; la última escritura decide la asignación. | Bloqueo compartido torneo → partida; la segunda operación conserva la asignación válida existente. |
| Fallo de aviso crítico ocultado | Una excepción sintética al notificar deja la invitación persistida, con **0 notificaciones**. | Aviso y operación dentro de la misma transacción; propagar el fallo y revertir. |

Además, la inspección identificó las mismas ventanas de lectura/escritura al
aceptar o rechazar invitaciones, inscribirse frente a aceptar arbitraje, repartir
la carga y validar resultados frente a reasignar o salir. Las regresiones
concurrentes verifican ahora esos conflictos; no se afirma que todos se hayan
reproducido ejecutando la versión antigua.

Otros defectos identificados por inspección y cubiertos por regresión:

- Una reasignación A→B→A podía reutilizar el check-in antiguo de A.
- La salida arbitral repetida sobrescribía la fecha y motivo originales.
- Apertura de check-in admitía volver desde incidencia o pendiente de validación.
- Ciertas escrituras confiaban en objetos de usuario/torneo recibidos y obsoletos.
- El calendario decidía transiciones sobre torneos sin bloqueo y podía confirmar
  cancelaciones sin todos sus avisos críticos.
- Reinvitar reutilizaba la fila pero no generaba un nuevo aviso; claves basadas
  solo en la invitación/partida impedían distinguir distintos cambios.

## Protocolo global

En PostgreSQL se usa `SELECT ... FOR NO KEY UPDATE`, manteniendo compatible el
bloqueo con las comprobaciones de claves foráneas:

1. **Torneo** como frontera de serialización para sus operaciones competitivas.
2. **Partida**, cuando la operación actúa sobre una partida concreta, usando
   `_partida_bloqueada` de fase 3.2. La lectura inicial solo localiza el torneo;
   los estados se vuelven a leer tras adquirir los bloqueos.
3. **Usuarios por PK ascendente**, si hay que coordinar disponibilidades. La
   asignación bloquea usuarios de candidatos aceptados, después relee sus
   condiciones y calcula la carga. Es compatible con el orden de XP.
4. Escrituras subordinadas de invitaciones, check-ins, historiales y avisos,
   dentro de la transacción que conserva el bloqueo del torneo.

Los escritores de invitaciones y salida arbitral bloquean primero el torneo;
no necesitan bloquear cada invitación para serializar capacidad y estados.
Los cambios de disponibilidad bloquean **solo el usuario** y nunca adquieren
posteriormente torneos o partidas. No cancelan las asignaciones existentes.
El calendario procesa cada torneo en una transacción independiente, ordenada
por PK, sin conservar bloqueos de torneos anteriores hasta acabar el lote.

No se adquieren torneos después de usuarios. Las llamadas anidadas de fase 3.2
vuelven a tomar el mismo torneo ya bloqueado. Evitar envolver servicios de
varios torneos en una transacción exterior que invierta este protocolo.
Los `RLock` arbitrales y de programación solo se utilizan con SQLite; ninguna
garantía PostgreSQL depende de memoria compartida del proceso.

## Reglas conservadas y comportamiento

- Plazas arbitrales y límite de pendientes públicos, Karma mínimo 150 y reglas
  de disponibilidad existentes. No se inventa un máximo de partidas por árbitro:
  el MVP utiliza reparto por menor carga dentro del torneo.
- Unicidad existente `(usuario, torneo)` en `ArbitroTorneo` y
  `(partida, usuario, tipo)` en `CheckInPartida`; no se añade otra migración.
- Un participante confirmado no puede aceptar arbitraje ni un árbitro activo
  inscribirse: ambas operaciones comparten el bloqueo del torneo.
- La asignación inicial válida se conserva; una reasignación autorizada tiene
  historial encadenado y aviso al nuevo árbitro. Un actor manual debe ser
  organizador o administrador autorizado; `actor=None` sigue siendo el acceso
  interno del asignador automático.
- Se conserva la reasignación histórica de partidas finalizadas contemplada por
  los tests originales. No cambia el resultado, su validador, XP, Karma ni bracket.
- Si cambia el responsable antes del inicio, los check-ins arbitrales quedan
  invalidados. Una partida lista vuelve a check-in para exigir al responsable
  actual. Las confirmaciones de participantes se conservan. La salida arbitral
  también invalida su confirmación previa y mantiene fecha/motivo al reintentarse.
- Se conservan ventanas y reglas de inicio/reprogramación del MVP. No se añade
  un requisito temporal inferior nuevo a la confirmación de check-in público.
  Reprogramar invalida las confirmaciones de la ventana anterior, como antes.
- Detección de ausencias usa una lectura coherente bajo torneo → partida y sigue
  siendo informativa: no adjudica automáticamente un no-show. Las decisiones
  oficiales permanecen en el servicio de resultados de fase 3.2.
- Los avisos críticos de invitación, respuesta, asignación, apertura y cancelación
  se confirman junto al cambio. Si falla su persistencia, la operación falla y
  se revierte; no se oculta la excepción. Las claves nuevas distinguen ciclo de
  invitación o historial de asignación; los avisos históricos no se modifican.

## Pruebas y validación

Las pruebas nuevas están en `arbitraje/test_integrity.py`. Los casos multiproceso
usan `TransactionTestCase`, `fork`, barrera antes del bloqueo raíz, PIDs de sesión
PostgreSQL distintos y límites de espera SQL. No mockean servicios ni bloqueos;
los mocks se reservan para fallos sintéticos de notificaciones.

Cubren capacidad pendiente/aceptada, duplicados, aceptación/rechazo, inscripción,
asignación inicial, reparto de carga, reasignaciones, validación oficial, salida,
disponibilidad, apertura, check-in duplicado, comienzo único, reprogramación,
calendario y consistencia de historiales/avisos. Las fases 3.1 y 3.2 se ejecutan
como regresión sin modificar sus pruebas.

Se añaden **38 tests**: 18 regresiones funcionales/rollback y 20 casos
PostgreSQL multiproceso. Estos últimos verifican 40 sesiones independientes;
las 10 pruebas PostgreSQL previas de XP/resultados también se conservan.

| Ejecución final | Descubiertos | Superados | Fallos | Errores | Omitidos | Duración |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Suite completa SQLite, hasher habitual | 585 | 555 | 0 | 0 | 30 | 256,887 s |
| PostgreSQL: `arbitraje partidas usuarios.test_xp_integrity` | 205 | 205 | 0 | 0 | 0 | 147,879 s |
| PostgreSQL: nuevos tests + check-in + integridad de resultados y XP | 81 | 81 | 0 | 0 | 0 | 14,390 s |

Las 30 omisiones de SQLite son exclusivamente PostgreSQL: 20 nuevas, 7 de
resultados y 3 de XP. Todas se ejecutan y pasan en PostgreSQL; no se presentan
como pruebas concurrentes superadas sobre SQLite.

`check`: sin incidencias. `makemigrations --check --dry-run`: sin cambios.
Ambos se ejecutan con SQLite y PostgreSQL aislados. La creación de las bases de
test aplica las migraciones existentes; también pasan los dos casos de migración
de fase 3.2 (preservación de declaraciones y rechazo no destructivo de duplicados).
`git diff --check`: limpio. `uv pip check`: 15 paquetes compatibles.

Ejecuciones intermedias, antes de retirar un control horario adicional que
alteraba el comportamiento público existente: SQLite 583 descubiertos, 553
superados, 0 fallos, 2 errores y 28 omitidos (252,306 s); PostgreSQL relacionado
203 descubiertos, 201 superados y 2 errores (143,808 s). Los dos errores eran
`test_publica_abre_checkin_y_permite_inicio_anticipado` y
`test_rechaza_checkin_antes_de_abrir_y_duplicado`: simulan apertura futura y
confirmación con reloj actual. Se retiró el requisito nuevo; **no se modificaron
las pruebas originales** y se volvió a ejecutar la validación completa.

Entorno: Python 3.14.10, Django 6.1.1, PostgreSQL 17.11 aislado en loopback,
SQLite temporal, archivos multimedia temporales, entorno sin credenciales reales.
Se bloquean conexiones Python fuera de la instancia PostgreSQL local. No se
modifican `PASSWORD_HASHERS`; se utiliza el hasher habitual del proyecto.

Comandos Django, ejecutados con configuración aislada:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python manage.py test arbitraje partidas usuarios.test_xp_integrity
python manage.py test arbitraje.test_integrity partidas.test_result_integrity usuarios.test_xp_integrity
```

Los dos últimos se ejecutan sobre PostgreSQL. Los tests específicos PostgreSQL
se omiten explícitamente en SQLite. También se revisan `git diff --check`, el diff
completo y compatibilidad de las dependencias instaladas.

## Compatibilidad, migraciones y límites

Sin migraciones ni cambios de esquema; no se eliminan ni corrigen automáticamente
filas históricas. No se inspeccionaron bases reales. Antes de integrar en producción,
revisar en un clon autorizado y anonimizado: capacidad ya excedida, árbitros activos
que también sean participantes confirmados, historiales de asignación discontinuos,
check-ins arbitrales de responsables anteriores y avisos críticos ausentes.

Los bloqueos evitan nuevas carreras entre escritores que cumplen el protocolo.
SQL manual, `QuerySet.update()` administrativo o nuevos servicios que lo omitan
pueden vulnerar invariantes agregadas. No se garantiza ausencia universal de
interbloqueos para transacciones externas arbitrarias ni otros niveles de
aislamiento distintos de PostgreSQL READ COMMITTED, usado en estas pruebas.

La serialización por torneo limita paralelismo dentro de un mismo torneo; es
intencionada para preservar capacidad, bracket y estados. No hay prueba de carga
sobre infraestructura pública ni cambio en la automatización del procesador
manual de calendario. Los avisos son registros internos de Django, no correos ni
prueba de entrega externa. Los fallos de persistencia pueden hacer fallar una
operación con rollback; no se introduce una cola externa ni reintentos invisibles.

Preparación futura, **sin ejecutarla**: revisar datos históricos en entorno
separado autorizado, ejecutar la suite contra una copia de PostgreSQL equivalente,
revisar el diff validado y planificar integración/despliegue con autorización
expresa. Las ramas `main` y `deploy-production` quedan fuera de esta fase.


## Revisión y publicación

Diff limitado a cinco archivos: `arbitraje/services.py`,
`arbitraje/test_integrity.py`, `partidas/scheduling.py`, `torneos/services.py`
y este documento. Sin modificaciones en pruebas previas, configuración,
dependencias, modelos ni migraciones. No se utiliza la infraestructura real.

Referencias comprobadas antes de publicar:

- `main`: `76b2f775e0f3a85bf4c79c1bb8276cb8ba58ad80`.
- `deploy-production`: `b9b49cd6a65265f8791c619a5b0eea5a341895f1`.

Publicar únicamente en `develop`; verificar después el árbol Git completo
contra el commit local validado y volver a consultar ambas referencias protegidas.
La publicación no autoriza integración ni despliegue en producción.
