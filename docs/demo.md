# Demo de Ranked Kingdom

## Base aislada y ejecucion

El modo demo utiliza `db_demo.sqlite3`, nunca la base habitual `db.sqlite3`.
Activalo en una consola dedicada de PowerShell, desde la raiz del proyecto:

```powershell
$env:TORNEO_DEMO = "1"
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8022 --noreload
```

Abre http://127.0.0.1:8022/. Para volver al entorno habitual, detiene el
servidor y ejecuta `Remove-Item Env:TORNEO_DEMO` en esa consola.
No actives este modo en un despliegue. El comando requiere `DEBUG=True`.
La base demo y sus archivos auxiliares estan excluidos de Git.

El comando es atomico e idempotente. Un grupo reservado sin permisos,
`ranked-kingdom-demo-v1`, identifica las cuentas creadas por la carga.
Una segunda ejecucion encuentra los mismos datos, sin desplazar fechas,
restablecer contrasenas, duplicar avisos ni deshacer cambios de una demo funcional.
No hay opcion de borrado o reset. Una demo incompleta o una colision se rechaza
en lugar de sobrescribir datos. Antes de la primera carga se rechaza una base
que contenga torneos ajenos, porque el calendario de dominio es global.
El catalogo y usuarios ajenos sin colisiones se conservan.

## Cuentas

Contrasena inicial compartida, exclusivamente de desarrollo:
`DEMO-RankedKingdom-2026!`. Nunca reutilizarla fuera de esta base.

| Funcion | Usernames |
| --- | --- |
| Administracion staff/superuser | `demo_KingdomAdmin` |
| Organizadores | `demo_NovaForge`, `demo_ArcByte` |
| Jugadores | `demo_NovaStrike`, `demo_Vortex`, `demo_NightFox`, `demo_RiftOne`, `demo_Pulse`, `demo_ShadowCore`, `demo_Zenith`, `demo_EchoVector` |
| Arbitros | `demo_FairSignal`, `demo_ClearOrbit`, `demo_TruePulse` |

Los correos utilizan el dominio reservado `demo.example.invalid`.
Los saldos iniciales de XP son ficticios y su nivel se calcula con
`nivel_para_xp`; no se fabrican premios historicos para justificar ese saldo.
Los premios de los torneos finalizados generan historiales reales de XP.
El Karma alto inicial se concede como ajuste administrativo identificado DEMO;
las declaraciones, participaciones y arbitrajes generan premios por servicios.
La sancion de moderacion tambien pasa por el servicio real.

## Escenarios

Todos los nombres de torneo llevan el prefijo `DEMO -`.

| Torneo | Tipo | Escenario inicial |
| --- | --- | --- |
| Kingdom Invitational | Oficial, 4 plazas | Finalizado con campeon, clasificacion, XP y Karma |
| Kingdom Masters | Publico, 8 plazas | Finalizado con campeon, clasificacion, XP y Karma |
| Vortex Circuit | Publico, 8 plazas | En curso; bracket parcial, resultados validados, uno declarado y una partida en curso |
| Community Clash | Publico, 8 plazas | Completo y preparado; check-in de primera ronda y rondas pendientes |
| Zenith Scheduled Cup | Oficial, 8 plazas | Programado para el dia siguiente; una partida reprogramada con historial |
| Pulse Check-in Cup | Oficial, 4 plazas | Check-in parcial para una partida en unos 30 minutos, otra futura y lobby DEMO |
| NightFox Private Arena | Privado, 8 plazas | Inscripciones abiertas, cuatro participantes y acceso mediante invitacion |
| Ranked Kingdom Open | Publico, 8 plazas | Inscripciones abiertas, tres participantes y una inscripcion cancelada |

El comando imprime el enlace privado generado por el modelo; no utiliza PKs
ni codigos de acceso fijos. Los privados no generan XP ni Karma competitivo.
FairSignal tiene asignaciones actuales; ClearOrbit tiene actividad historica
y futura; TruePulse esta disponible, con invitacion pendiente y sin asignacion.
Zenith esta disponible para arbitrar y tiene Karma suficiente, pero su
participacion impide que arbitre esos mismos torneos, conforme a la regla normal.

Se incluyen una denuncia abierta y otra resuelta con medida leve, con textos
neutros. Los avisos de invitacion, asignacion, programacion, check-in y resultado
son generados por sus servicios; se marcan dos como leidos. No se envian correos
ni se descargan imagenes. Se utilizan los fallbacks visuales de la aplicacion.

## Catalogo y limites

Se reutilizan hasta tres videojuegos activos existentes, sus plataformas y sus
rangos, sin cambiar esos registros. Si faltan juegos, se intentan crear Valorant,
Rocket League y League of Legends, con catalogo local de plataformas/rangos.
No se reactiva un juego inactivo. Si un juego reutilizado no tiene rangos, el
perfil queda sin rango, como permite el modelo.

El MVP solo soporta competicion individual: estos escenarios son duelos, no
equipos. El perfil personal tiene nick y rango; no existe plataforma individual
en ese modelo. Solo se muestran las plataformas compatibles del videojuego.

Las fechas son relativas a la primera ejecucion. El reloj local del seeder
reconstruye el pasado mediante servicios, incluidos los valores por defecto de
fechas; no modifica el reloj del sistema ni los modelos o servicios productivos.
Los torneos activos conservan las ventanas temporales reales: la inscripcion
publica del Open cierra aproximadamente 40 minutos despues y el check-in de
Pulse termina aproximadamente 30 minutos despues. La duracion configurada de
check-in es 60 minutos. No se congela la aplicacion ni se evita su calendario.

Prepara la base cerca de la presentacion. Para repetir una presentacion desde
cero otro dia, utiliza una base demo nueva: con el servidor detenido, archiva
`db_demo.sqlite3` fuera del proyecto y vuelve a ejecutar `migrate` y `seed_demo`.
La carga nunca archiva ni elimina bases por su cuenta. Si cambias nicknames o
eliminas torneos esenciales manualmente, una repeticion puede rechazar esa demo
incompleta; no recrea silenciosamente identidades ni sustituye relaciones.

## Validacion focal

```powershell
.\.venv\Scripts\python.exe manage.py test torneos.test_seed_demo --parallel 1
.\.venv\Scripts\python.exe manage.py check
```

Los tests usan una base temporal: verifican protecciones, escenarios,
idempotencia de todos los modelos involucrados, rollback y conservacion de
datos ajenos. No es necesario poblar la base habitual para probar el comando.