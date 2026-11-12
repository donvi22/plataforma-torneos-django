# Fase 3.2: integridad de resultados y progresión

Base revisada: develop, de7bd9dc6834e1832bc18afc56f3f4f3bb972480.
Solo desarrollo; no cambios en producción, Railway, IONOS o credenciales reales.

## Hallazgos y evidencia

1. **Duplicados reproducidos en PostgreSQL 17.11.** DeclaracionResultado no tenía
   una restricción (partida, usuario). El servicio comprobaba exists() y después
   insertaba. Dos procesos independientes sincronizados después de esa consulta
   aceptaron dos declaraciones del mismo usuario y partida (backend PID 22 y 23).
   declarar_resultado no bloqueaba la fila; los RLock de otros servicios no
   protegían esa comprobación ni coordinaban procesos independientes.
2. **Progresión incompleta reproducida en PostgreSQL.** Dos semifinales publicaban
   sus ganadores en posiciones distintas de la final. Una barrera después del
   COUNT de participantes hizo que cada proceso viera solo su propio avance.
   Ambas confirmaron sus resultados, la final quedó con dos rivales, pero
   fecha_hora_rivales_confirmados permaneció NULL (backend PID 30 y 31).
3. **Notificaciones parciales reproducidas por inyección controlada de fallo.**
   El servicio anterior atrapaba Exception y continuaba. Al fallar la segunda
   notificación devolvía un resultado oficial, finalizaba la partida y dejaba
   únicamente la primera notificación. La regresión nueva verifica rollback de
   toda la operación en ese caso.
4. **Riesgos por inspección, sin atribuirles dos decisiones confirmadas en datos
   reales:** _partida_bloqueada no bloqueaba ninguna fila; dos validaciones podían
   superar las comprobaciones y terminar en una carrera de inserción. El OneToOne
   de ResultadoPartida YA impedía dos resultados oficiales persistidos: no se
   afirma que esa unicidad fallara. Faltaba serialización y error de negocio para
   el perdedor. La resolución de ramas vacías y las transiciones de programación
   también podían leer estados previos y omitir una resolución o reabrir un estado.
   Las regresiones concurrentes cubren esos conflictos con la corrección.

No se consultó ninguna base real. No se ha determinado si existen duplicados,
fechas de rivales ausentes o notificaciones parciales históricos en producción.

## Entradas, permisos e idempotencia

- Declaración: vista POST declarar_resultado_partida y comando local de pruebas,
  ambos a través de declarar_resultado. Solo participa una inscripción confirmada,
  con cuenta operativa, partida en juego/pendiente de validación y sin resultado.
  Se mantiene una declaración por usuario y partida; los dos jugadores pueden
  discrepar. Una declaración no valida ni avanza el bracket.
- Validación: vista POST validar_resultado_partida y llamadas de servicios/demos.
  Se conservan organizador, administrador autorizado o árbitro asignado, aceptado
  y activo, y la validación de ganador/tipo/estado. Se vuelve a leer la cuenta
  del actor bajo la transacción, sin confiar en una instancia vieja.
- La revisión web mantiene su requisito de motivo cuando no hay dos declaraciones
  coincidentes. No se han cambiado vistas, reglas de arbitraje ni marcadores.
- Validar de nuevo una partida terminada sigue siendo un error de negocio; no se
  añade una segunda decisión ni se cambia silenciosamente la primera. La
  idempotencia de efectos no implica convertir ese error existente en éxito HTTP.
- _resolver_rama conserva las reglas de avance automático/doble incomparecencia.
  El cierre de una final pública sigue usando cerrar_torneo; no cambian XP/Karma.

## Orden de bloqueo y atomicidad

En PostgreSQL se adquiere primero la fila del torneo y después la de la partida,
con FOR NO KEY UPDATE, sin joins bloqueantes. La lectura inicial de torneo_id
solo localiza el ámbito; los estados se vuelven a leer después de obtener los
bloqueos. Las relaciones de torneo/partida se consideran estructurales e inmutables
por los flujos del MVP, no se soporta su modificación SQL arbitraria en paralelo.

El torneo es la unidad de serialización del bracket. Así las semifinales, ramas
vacías, declaraciones, generación, programación/check-in/inicio y cierre no
adquieren ramas en órdenes cruzados ni deciden sobre avances sin confirmar.
Esto serializa también partidas distintas DEL MISMO torneo: es una elección
conservadora para la propagación recursiva del MVP, no un bloqueo global de todos
los torneos. No hay operaciones de red dentro de esa sección de resultados.

El helper de programación comparte ese orden para impedir que una reprogramación
o inicio calculados desde un estado viejo sobrescriban una finalización.
registrar_inicio_partida, generar_clasificacion_final y cerrar_torneo adquieren
la misma fila de torneo. La generación/preparación del bracket también lo hace.
La concesión de XP mantiene su orden posterior de usuarios por PK sin cambiar
ninguna fórmula ni archivo de XP/Karma. Los callbacks existentes permanecen en
on_commit y no se registran de nuevo por una validación rechazada.

Resultado oficial, estado/fecha final, avance, historial y notificaciones críticas
se escriben en una sola transacción. Un fallo de notificación ya no se silencia:
se propaga y revierte también cualquier notificación anterior de esa operación.
Se conserva la exclusión existente de destinatarios con cuenta eliminada.
Las notificaciones siguen usando la clave por resultado y la restricción
(destinatario, clave_evento), sin cambiar el servicio global de notificaciones.

Las inserciones de declaraciones y resultados usan savepoints para traducir
IntegrityError a ResultadoError sin consultar dentro de una transacción rota.
Se conservan OneToOne de resultado y unicidades de participante/posición; la
restricción nueva refuerza la regla de declaraciones incluso fuera del servicio.

Los bloqueos en memoria se limitan a SQLite como ayuda de desarrollo. SQLite no
ofrece SELECT FOR UPDATE y puede devolver errores de bloqueo entre procesos;
no se presenta como sustituto de las garantías verificadas en PostgreSQL.

## Migración 0006_declaracion_unica_por_usuario

1. RunPython agrupa las declaraciones por partida_id y usuario_id con Count(pk).
2. Si hay grupos duplicados, lanza RuntimeError y detiene la migración. No borra,
   fusiona, elige ganador, cambia fechas ni altera declaraciones históricas.
3. Si no hay duplicados, añade unique_declaracion_partida_usuario.

La migración es atómica. La propia creación de unicidad también comprueba los
datos, de modo que un duplicado introducido por un escritor antiguo durante el
intervalo no se conserva violando la restricción: falla la aplicación y debe
investigarse. No convertir esta protección en una limpieza automática.

Dos pruebas reales de MigrationExecutor comprueban la conservación de todos los
campos de una declaración previa y el rechazo de una copia posterior; además,
crean duplicados sintéticos con el esquema antiguo y verifican que la migración
se detiene conservando ambas filas y sin registrarse como aplicada. Esas pruebas
se han ejecutado tanto en SQLite como en PostgreSQL.

Antes de una integración futura, por separado y con autorización: revisar una
copia consistente, inventariar grupos duplicados, respaldar, decidir su resolución
con trazabilidad y coordinar la ventana sin escritores antiguos. No se ejecutó
esta inspección ni migración sobre producción. Revertir la migración quita solo
la restricción; no restaura ni elimina datos, pues la migración no los modifica.
No volver a código sin los bloqueos como supuesto rollback seguro de concurrencia.

## Pruebas y límites de validación

partidas/test_result_integrity.py incorpora 16 pruebas: siete funcionales/de
integridad, dos de migración y siete multiproceso PostgreSQL. Cubren duplicación
de declaraciones, ambos jugadores, decisiones contradictorias, avance de dos
semifinales, ramas vacías, declaración contra validación y reprogramación contra
validación. Comprueban estados, ganador, posiciones, historial y notificaciones.
También se verifican permisos, cuenta suspendida, constraint fuera del servicio
y rollback ante errores tardíos de historial/notificación.

Los workers son procesos fork independientes con conexiones verificadas mediante
pg_backend_pid(); sincronizan la llegada a la consulta de bloqueo del torneo.
No se mockean base, transacciones ni bloqueos. Se fijan tiempos de espera y se
cierran conexiones/procesos. Los siete casos se omiten en SQLite y en plataformas
sin fork: requieren PostgreSQL sobre Linux/entorno equivalente para esta prueba.

Entorno: Python 3.14.10, Django 6.1.1, psycopg 3.3.8, PostgreSQL 17.11 temporal
solo en loopback; SQLite en memoria; medios temporales y conexiones Python
externas bloqueadas. No hay credenciales de producción ni SFTP real.

Resultados de esta revisión:

- Reproducciones auxiliares del código anterior: dos casos PostgreSQL confirmados.
- Pruebas nuevas PostgreSQL: 16/16 superadas.
- Integración PostgreSQL con pruebas anteriores de resultados, vistas, progresión,
  cierre y XP: 53/53 superadas, 0 fallos, 0 errores, 0 omitidas; 37,235 s.
- Pruebas específicas de migración SQLite: 2/2 superadas.
- check: sin problemas. makemigrations --check --dry-run: sin cambios pendientes.
- uv pip check: dependencias compatibles, sin dependencias nuevas en el proyecto.
- Suite completa SQLite con hasher habitual: 547 pruebas descubiertas, 537
  superadas, 10 omitidas y 0 fallos/errores, en 260,853 s. Las 10 omitidas son
  siete casos PostgreSQL de resultados y tres de XP; todas pasaron en la
  ejecución PostgreSQL de 53 pruebas indicada arriba.
- Reproducción auxiliar de notificación parcial con el servicio anterior: 1/1
  ensayo confirmado por inyección controlada de fallo en SQLite.
- git diff --check limpio; no se eliminaron ni debilitaron pruebas anteriores.
- Aviso no bloqueante existente: staticfiles/ no generado en el entorno aislado.

La verificación se limita a READ COMMITTED y a los escritores que usan estos
servicios. No certifica SQL externo, cambios concurrentes arbitrarios de relaciones,
ni todas las carreras de invitaciones/revocación arbitral o de moderación de
cuentas. Las reglas de autorización se conservan y se consultan en el momento de
la decisión; no se amplía aquí el protocolo de bloqueo de esos subsistemas.
Tampoco se afirma haber reparado datos históricos, probado Railway o realizado
pruebas de carga masiva. La integración real sigue requiriendo autorización.
