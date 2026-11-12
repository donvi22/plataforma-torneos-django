# Concurrencia e integridad de XP

## Alcance y causa confirmada

Revisión de develop desde 4d306d4824c63c6350394c87070de13b1507a0a1.
El servicio usuarios.services.conceder_xp_torneo era transaccional, pero leía
Usuario.xp_total sin bloqueo de fila. El RLock solo coordinaba un proceso:
dos workers podían registrar premios de torneos distintos con el mismo saldo
inicial y escribir el mismo saldo final. La unicidad por inscripción no impide
esta pérdida, ya que las inscripciones son distintas.

Reproducción controlada en PostgreSQL 17.11 aislado: se cargó el servicio original
del commit de partida en dos procesos (cada uno con su propio RLock y conexión).
Una barrera tras las lecturas del primer usuario forzó la intercalación:

- Saldo inicial: 90 XP.
- Dos torneos diferentes: 110 XP por torneo para ese usuario.
- Dos movimientos confirmados, ambos con xp_anterior=90 y xp_posterior=200.
- Saldo final observado: 200. Saldo esperado por el historial: 310.
- Incremento perdido demostrado: 110 XP.

El ensayo utilizó fixtures sintéticos, nunca la base de producción.

## Entradas y reglas conservadas

La única función de concesión en la aplicación es conceder_xp_torneo. La invocan:

- torneos.services.cerrar_torneo, tanto al finalizar como al repetir el cierre;
- el cierre automático de torneos públicos desde partidas.services.validar_resultado,
  a través de cerrar_torneo;
- el comando de gestión conceder_xp_torneo, para completar/reintentar recompensas.

seed_demo prepara saldos iniciales de demostración: no es otra entrada de premios
en producción. El administrador expone saldo y nivel como solo lectura.
No se cambian Karma ni resultados, sus callbacks o sus transacciones.

Se conservan los premios base, el factor de tamaño, la dificultad, el
multiplicador oficial, ROUND_HALF_UP, la exclusión de torneos privados y cuentas
inactivas, y la curva de nivel 100 * (nivel - 1)^2. No se modifican modelos ni
migraciones, ni se recalculan saldos o movimientos históricos existentes.

## Protocolo de bloqueo

Dentro de transaction.atomic se verifican el torneo finalizado y su clasificación.
Después se materializan las filas de todos sus usuarios, ordenadas por PK, con
select_for_update(no_key=True), sin joins en la consulta bloqueante.
En PostgreSQL esto usa FOR NO KEY UPDATE: serializa cambios del saldo y permite
los bloqueos KEY SHARE de comprobación de claves foráneas, sin bloquear todos los
torneos ni usuarios ajenos a esa clasificación.

Solo después de adquirir los bloqueos se vuelve a consultar la recompensa por
inscripción. Si otro worker la confirmó mientras esperábamos, se verifica y se
retorna ese mismo movimiento. Se comprueba también que pertenezca al usuario
correcto. La restricción unique_recompensa_xp_por_inscripcion permanece como
última defensa de integridad.

Para cada movimiento nuevo se leen saldo y nivel de la instancia bloqueada;
se insertan los valores anterior/posterior y se actualiza exclusivamente xp_total
y nivel. Todo el lote y sus movimientos se revierten si ocurre un error.
Los bloqueos permanecen hasta el final de la transacción exterior, también cuando
el servicio se invoca durante un cierre. Adquirir todas las filas en orden PK
antes de escribir evita invertir el orden por posiciones o por orden de inscripción.

El protocolo asume PostgreSQL READ COMMITTED (predeterminado en Django),
clasificaciones finales estables y escritores de XP que pasan por este servicio.
No garantiza ausencia de interbloqueos con cualquier código externo que adquiera
las mismas filas en otro orden o encadene concesiones en transacciones arbitrarias.
Una transacción abortada debe reintentarse completa desde su frontera de entrada,
no desde dentro de una transacción marcada para rollback.

## SQLite y límites

SQLite no implementa SELECT FOR UPDATE. Conserva el RLock como ayuda local de
desarrollo, pero esto no constituye una garantía de concurrencia entre procesos.
Una disputa de escritura puede producir OperationalError y requerir reintento de
la transacción completa. Las pruebas concurrentes se omiten expresamente en
SQLite: su ejecución secuencial no se presenta como validación de PostgreSQL.

Los historiales ya incoherentes no se reparan automáticamente. Una auditoría de
datos reales y cualquier reparación requieren alcance, respaldo y autorización
independientes. No se han consultado datos ni credenciales reales.

## Pruebas de regresión

usuarios/test_xp_integrity.py añade siete pruebas de integridad y tres concurrentes:

- importes normales y niveles; encadenamiento desde un saldo previo;
- repetición idempotente y restricción de unicidad en la base;
- rollback de todos los participantes ante fallo tardío del historial o del saldo;
- rechazo de una recompensa enlazada a un usuario diferente;
- torneos simultáneos distintos, mismo torneo simultáneo y participantes invertidos.

Los tres ensayos PostgreSQL usan TransactionTestCase, dos conexiones independientes
verificadas mediante pg_backend_pid(), y una barrera antes de las consultas de
bloqueo. No se mockean la base, los bloqueos ni el servicio. Se limitan los tiempos
de espera y cada worker cierra su conexión. Las pruebas anteriores se conservan.

Para ejecutarlas sobre una configuración PostgreSQL de pruebas aislada:

```sh
python manage.py test usuarios.test_xp usuarios.test_xp_integrity --verbosity 2
```

Nunca apuntar ese comando a producción. Django debe poder crear y destruir una
base de pruebas dedicada. La configuración habitual del proyecto puede requerir
un módulo de settings de pruebas independiente para ese entorno.

## Validación de esta revisión

Python 3.14.10, Django 6.1.1 y psycopg 3.3.8. PostgreSQL 17.11 temporal, accesible
solo en loopback, sin servicios externos ni credenciales reales. Se bloquearon
las conexiones Python externas durante la validación; SFTP continuó simulado.

- PostgreSQL: 16 pruebas del repositorio (6 anteriores y 10 nuevas) superadas.
- Reproducción auxiliar del servicio anterior: un ensayo adicional superado,
  acreditando el incremento perdido descrito arriba. Total de esa ejecución: 17.
- check: sin problemas; makemigrations --check --dry-run: sin cambios.
- Suite completa SQLite con hasher habitual: 531 pruebas descubiertas, 528
  superadas y 3 omitidas (los casos PostgreSQL), sin fallos ni errores, en
  416,447 segundos. Esos tres casos sí se ejecutaron y superaron en PostgreSQL.
- Repetición independiente de los tres casos concurrentes: 3/3 superados.
- Dependencias: uv pip check correcto; diff revisado y git diff --check limpio.
- Aviso no bloqueante existente: staticfiles/ no se ha generado en este entorno
  aislado; no se ejecutó collectstatic ni se alteraron estáticos.

No hay cambios de dependencias del proyecto ni operaciones de despliegue,
configuración de Railway/IONOS, importación de datos o migraciones contra servicios
reales. La preparación de PostgreSQL pertenece únicamente al entorno temporal.
