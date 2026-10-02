"""Servicios de inscripción.

En SQLite se usa ``BEGIN IMMEDIATE`` para tomar el bloqueo de escritura de la
base de datos antes de contar plazas, y un ``RLock`` serializa llamadas del
mismo proceso. Esto evita que dos solicitudes de este proceso ocupen la última
plaza. La garantía entre procesos depende del bloqueo de escritura de SQLite;
para producción con alta concurrencia se deberá usar un motor con bloqueos de
filas y ``select_for_update``.
"""

from contextlib import contextmanager
from datetime import timedelta
from threading import RLock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from .models import ClasificacionTorneo, InscripcionTorneo, Torneo


class InscripcionError(Exception):
	"""Error de negocio al crear o cancelar una inscripción."""


class CierreTorneoError(Exception):
	"""Error de coherencia, autorización o estado al cerrar un torneo."""


class EstadoInscripciones:
	PROXIMAMENTE = 'PROXIMAMENTE'
	ABIERTAS = 'ABIERTAS'
	COMPLETAS = 'COMPLETAS'
	PRORROGA = 'PRORROGA'
	CERRADAS = 'CERRADAS'
	CANCELADAS = 'CANCELADAS'


def estado_inscripciones(torneo, ahora=None):
	"""Devuelve el estado temporal efectivo sin mutar el torneo."""
	ahora = ahora or timezone.now()
	if torneo.estado in (Torneo.Estado.CANCELADO, Torneo.Estado.FINALIZADO):
		return EstadoInscripciones.CANCELADAS if torneo.estado == Torneo.Estado.CANCELADO else EstadoInscripciones.CERRADAS
	if torneo.estado in (
		Torneo.Estado.INSCRIPCIONES_CERRADAS,
		Torneo.Estado.PREPARADO,
		Torneo.Estado.EN_CURSO,
	):
		return EstadoInscripciones.CERRADAS
	if torneo.tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL):
		apertura = torneo.fecha_apertura_inscripciones
		cierre = torneo.fecha_cierre_inscripciones
		if apertura and ahora < apertura:
			return EstadoInscripciones.PROXIMAMENTE
		if cierre and ahora <= cierre:
			return EstadoInscripciones.COMPLETAS if torneo.participantes_confirmados >= torneo.max_participantes else EstadoInscripciones.ABIERTAS
		if cierre and torneo.duracion_prorroga_min:
			fin_prorroga = cierre + timedelta(minutes=torneo.duracion_prorroga_min)
			if ahora <= fin_prorroga:
				return EstadoInscripciones.COMPLETAS if torneo.participantes_confirmados >= torneo.max_participantes else EstadoInscripciones.PRORROGA
		return EstadoInscripciones.CERRADAS
	if torneo.estado in (Torneo.Estado.PROXIMAMENTE,) and torneo.fecha_apertura_inscripciones and ahora < torneo.fecha_apertura_inscripciones:
		return EstadoInscripciones.PROXIMAMENTE
	if torneo.estado in (Torneo.Estado.INSCRIPCIONES_ABIERTAS, Torneo.Estado.PRORROGA):
		return EstadoInscripciones.COMPLETAS if torneo.participantes_confirmados >= torneo.max_participantes else EstadoInscripciones.ABIERTAS
	return EstadoInscripciones.CERRADAS


_sqlite_inscripcion_lock = RLock()


@contextmanager
def _bloqueo_de_inscripcion():
	"""Serializa escrituras en SQLite y deja select_for_update para otros motores."""
	conexion = connection
	transaccion_externa = conexion.in_atomic_block
	with _sqlite_inscripcion_lock:
		if conexion.vendor == 'sqlite' and not transaccion_externa:
			conexion.cursor().execute('BEGIN IMMEDIATE')
			try:
				yield
			except Exception:
				conexion.rollback()
				raise
			else:
				conexion.commit()
		else:
			with transaction.atomic():
				yield


def _torneo_bloqueado(torneo_id):
	if connection.vendor == 'sqlite':
		return Torneo.objects.get(pk=torneo_id)
	return Torneo.objects.select_for_update().get(pk=torneo_id)


def _inscripciones_abiertas(torneo, ahora):
	return estado_inscripciones(torneo, ahora) in (EstadoInscripciones.ABIERTAS, EstadoInscripciones.PRORROGA)


def _es_administrador_autorizado(usuario):
	return bool(
		usuario
		and usuario.is_active
		and usuario.is_staff
		and usuario.rol_global == 'ADMIN'
		and (usuario.is_superuser or usuario.has_perm('torneos.add_torneo'))
	)


def _registrar_transicion(torneo, nuevo_estado, actor=None, motivo=''):
	if torneo.estado == nuevo_estado:
		return False
	from .models import HistorialEstadoTorneo
	estado_anterior = torneo.estado
	torneo.estado = nuevo_estado
	torneo.save(update_fields=('estado', 'fecha_actualizacion'))
	HistorialEstadoTorneo.objects.create(
		torneo=torneo,
		estado_anterior=estado_anterior,
		estado_nuevo=nuevo_estado,
		actor=actor,
		motivo=motivo,
	)
	return True


def _generar_bracket_si_completo(torneo):
	"""Genera el cuadro tras el cierre definitivo, dejando propagar errores."""
	if torneo.participantes_confirmados != torneo.max_participantes:
		return False
	from partidas.services import generar_bracket
	generar_bracket(torneo)
	return True


def registrar_inicio_partida(torneo):
	"""Marca el torneo en curso al iniciar realmente su primera partida."""
	with transaction.atomic():
		torneo = Torneo.objects.get(pk=torneo.pk)
		if torneo.estado != Torneo.Estado.PREPARADO:
			return False
		return _registrar_transicion(
			torneo,
			Torneo.Estado.EN_CURSO,
			motivo='Inicio de la primera partida.',
		)


def _posicion_por_ronda(ronda, total_rondas):
	return 2 ** (total_rondas - ronda) + 1


def _clasificacion_calculada(torneo):
	from partidas.models import Partida

	if torneo.tipo_participante != Torneo.TipoParticipante.INDIVIDUAL:
		raise CierreTorneoError('La clasificación solo está disponible para torneos individuales.')
	if torneo.formato_competitivo.tipo_regla_participantes != torneo.formato_competitivo.TipoReglaParticipantes.ELIMINACION_DIRECTA:
		raise CierreTorneoError('El formato del torneo no permite esta clasificación.')
	inscripciones = list(torneo.inscripciones.filter(
		estado=InscripcionTorneo.Estado.CONFIRMADA,
	).order_by('pk'))
	if len(inscripciones) != torneo.max_participantes:
		raise CierreTorneoError('Las inscripciones confirmadas no coinciden con el tamaño del torneo.')
	partidas = list(torneo.partidas.select_related('resultado_oficial__ganador').prefetch_related('participantes').order_by('numero_ronda', 'numero_orden'))
	if len(partidas) != torneo.max_participantes - 1:
		raise CierreTorneoError('El bracket no tiene la estructura completa esperada.')
	total_rondas = torneo.max_participantes.bit_length() - 1
	por_clave = {(partida.numero_ronda, partida.numero_orden): partida for partida in partidas}
	for ronda in range(1, total_rondas + 1):
		for orden in range(1, torneo.max_participantes // (2 ** ronda) + 1):
			partida = por_clave.get((ronda, orden))
			if partida is None:
				raise CierreTorneoError('El bracket tiene rondas u órdenes incoherentes.')
			if ronda < total_rondas:
				siguiente = por_clave.get((ronda + 1, (orden + 1) // 2))
				if partida.siguiente_partida_id != getattr(siguiente, 'pk', None):
					raise CierreTorneoError('Las relaciones de avance del bracket son incoherentes.')
			elif partida.siguiente_partida_id is not None:
				raise CierreTorneoError('La final no puede tener una partida posterior.')
			if partida.estado != Partida.Estado.FINALIZADA or not hasattr(partida, 'resultado_oficial'):
				raise CierreTorneoError('Todas las partidas deben tener un resultado oficial finalizado.')
	final = por_clave[(total_rondas, 1)]
	if not final.resultado_oficial.ganador_id:
		raise CierreTorneoError('La final no tiene un campeón identificable y requiere revisión administrativa.')
	perdidas = {}
	for partida in partidas:
		resultado = partida.resultado_oficial
		participantes = list(partida.participantes.all())
		if resultado.ganador_id and not any(participante.inscripcion_id == resultado.ganador_id for participante in participantes):
			raise CierreTorneoError('Un ganador oficial no pertenece a su partida.')
		for participante in participantes:
			if participante.inscripcion_id != resultado.ganador_id:
				if participante.inscripcion_id in perdidas:
					raise CierreTorneoError('Un participante figura eliminado en más de una ronda.')
				perdidas[participante.inscripcion_id] = partida.numero_ronda
	campeon_id = final.resultado_oficial.ganador_id
	esperadas = {inscripcion.pk for inscripcion in inscripciones}
	if campeon_id not in esperadas or set(perdidas) | {campeon_id} != esperadas:
		raise CierreTorneoError('Los resultados oficiales no permiten una clasificación completa coherente.')
	return [
		{
			'inscripcion_id': inscripcion.pk,
			'posicion': 1 if inscripcion.pk == campeon_id else _posicion_por_ronda(perdidas[inscripcion.pk], total_rondas),
			'ronda_eliminado': None if inscripcion.pk == campeon_id else perdidas[inscripcion.pk],
			'es_campeon': inscripcion.pk == campeon_id,
		}
		for inscripcion in inscripciones
	]


def generar_clasificacion_final(torneo):
	"""Genera una clasificación inmutable o verifica la existente."""
	with transaction.atomic():
		torneo = Torneo.objects.get(pk=torneo.pk)
		esperada = _clasificacion_calculada(torneo)
		existentes = list(torneo.clasificaciones.values(
			'inscripcion_id', 'posicion', 'ronda_eliminado', 'es_campeon',
		))
		if existentes:
			actual = {
				(fila['inscripcion_id'], fila['posicion'], fila['ronda_eliminado'], fila['es_campeon'])
				for fila in existentes
			}
			esperado = {
				(fila['inscripcion_id'], fila['posicion'], fila['ronda_eliminado'], fila['es_campeon'])
				for fila in esperada
			}
			if len(existentes) != len(esperada) or actual != esperado:
				raise CierreTorneoError('La clasificación existente es parcial o contradictoria y requiere revisión.')
			return list(torneo.clasificaciones.select_related('inscripcion').all())
		ClasificacionTorneo.objects.bulk_create([
			ClasificacionTorneo(torneo=torneo, **fila) for fila in esperada
		])
		return list(torneo.clasificaciones.select_related('inscripcion').all())


def cerrar_torneo(torneo, actor=None, automatico=False):
	"""Genera clasificación definitiva y registra el cierre una sola vez."""
	with transaction.atomic():
		torneo = Torneo.objects.get(pk=torneo.pk)
		if automatico:
			if torneo.tipo != Torneo.Tipo.PUBLICO:
				raise CierreTorneoError('Solo los torneos públicos se cierran automáticamente.')
		elif torneo.tipo == Torneo.Tipo.OFICIAL:
			if not _es_administrador_autorizado(actor):
				raise CierreTorneoError('Solo la administración puede cerrar un torneo oficial.')
		elif torneo.tipo == Torneo.Tipo.PRIVADO:
			if not actor or (torneo.organizador_id != actor.pk and not _es_administrador_autorizado(actor)):
				raise CierreTorneoError('Solo el organizador puede cerrar este torneo privado.')
		else:
			raise CierreTorneoError('Los torneos públicos se cierran automáticamente al validar la final.')
		clasificacion = generar_clasificacion_final(torneo)
		if torneo.estado == Torneo.Estado.FINALIZADO:
			if torneo.tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL):
				from usuarios.services import conceder_xp_torneo
				conceder_xp_torneo(torneo)
				from usuarios.karma import premiar_cierre_torneo
				transaction.on_commit(
					lambda torneo_id=torneo.pk: premiar_cierre_torneo(torneo_id), robust=True,
				)
			return clasificacion
		if torneo.estado != Torneo.Estado.EN_CURSO:
			raise CierreTorneoError('El torneo debe estar en curso para cerrarse.')
		torneo.fecha_fin_real = timezone.now()
		torneo.save(update_fields=('fecha_fin_real', 'fecha_actualizacion'))
		_registrar_transicion(torneo, Torneo.Estado.FINALIZADO, actor=actor, motivo='Clasificación final generada y cierre competitivo confirmado.')
		if torneo.tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL):
			from usuarios.services import conceder_xp_torneo
			conceder_xp_torneo(torneo)
			from usuarios.karma import premiar_cierre_torneo
			transaction.on_commit(
				lambda torneo_id=torneo.pk: premiar_cierre_torneo(torneo_id), robust=True,
			)
		return clasificacion


def crear_torneo(organizador, **datos):
	"""Crea un torneo preparado, usando siempre al usuario autenticado."""
	if not organizador or not organizador.is_active:
		raise InscripcionError('La cuenta del organizador no está activa.')
	tipo = datos.get('tipo')
	if tipo == Torneo.Tipo.OFICIAL and not _es_administrador_autorizado(organizador):
		raise InscripcionError('Solo un administrador autorizado puede crear torneos oficiales.')
	if tipo == Torneo.Tipo.PUBLICO:
		if organizador.nivel < 5:
			raise InscripcionError('Se necesita nivel 5 para crear torneos públicos.')
		if organizador.karma_total < 150:
			raise InscripcionError('Se necesitan al menos 150 puntos de Karma para crear torneos públicos.')
		activos = Torneo.objects.filter(
			organizador=organizador,
			tipo=Torneo.Tipo.PUBLICO,
		).exclude(estado__in=(Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO)).count()
		if activos >= 2:
			raise InscripcionError('El organizador ya tiene dos torneos públicos activos.')
		datos['modo_xp'] = Torneo.ModoXP.AUTOMATICA
	if tipo == Torneo.Tipo.PRIVADO:
		activos = Torneo.objects.filter(
			organizador=organizador,
			tipo=Torneo.Tipo.PRIVADO,
		).exclude(estado__in=(Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO)).count()
		if activos >= 1:
			raise InscripcionError('El organizador ya tiene un torneo privado activo.')
		datos['modo_xp'] = Torneo.ModoXP.AUTOMATICA
		datos['estado'] = Torneo.Estado.BORRADOR
	if tipo != Torneo.Tipo.OFICIAL and datos.get('modo_xp') == Torneo.ModoXP.ESPECIAL:
		raise InscripcionError('El XP especial solo está permitido en torneos oficiales.')
	with transaction.atomic():
		torneo = Torneo(organizador=organizador, **datos)
		torneo.full_clean()
		torneo.save()
	return torneo


def publicar_torneo(torneo, actor, ahora=None):
	"""Publica un borrador y abre inmediatamente o programa sus inscripciones."""
	ahora = ahora or timezone.now()
	if torneo.organizador_id != actor.pk and not _es_administrador_autorizado(actor):
		raise InscripcionError('Solo el organizador o un administrador puede publicar este torneo.')
	if torneo.tipo == Torneo.Tipo.OFICIAL:
		if not _es_administrador_autorizado(actor):
			raise InscripcionError('Solo un administrador puede publicar torneos oficiales.')
		if torneo.estado != Torneo.Estado.BORRADOR:
			raise InscripcionError('Solo se pueden publicar torneos oficiales en borrador.')
		with transaction.atomic():
			torneo.full_clean()
			torneo.fecha_publicacion = ahora
			torneo.save(update_fields=('fecha_publicacion', 'fecha_actualizacion'))
			_registrar_transicion(torneo, Torneo.Estado.PROXIMAMENTE, actor=actor, motivo='Publicación oficial.')
		return torneo
	if torneo.tipo != Torneo.Tipo.PUBLICO:
		raise InscripcionError('Solo los torneos públicos y oficiales utilizan esta publicación.')
	if torneo.estado != Torneo.Estado.BORRADOR:
		raise InscripcionError('Solo se pueden publicar torneos en borrador.')
	if not torneo.fecha_apertura_inscripciones or not torneo.fecha_cierre_inscripciones:
		raise InscripcionError('El torneo público necesita apertura y cierre de inscripciones.')
	if torneo.fecha_apertura_inscripciones > ahora + timedelta(hours=24):
		raise InscripcionError('La apertura no puede programarse con más de 24 horas de antelación.')
	if torneo.fecha_cierre_inscripciones < torneo.fecha_apertura_inscripciones:
		raise InscripcionError('El cierre no puede ser anterior a la apertura.')
	duracion = torneo.fecha_cierre_inscripciones - torneo.fecha_apertura_inscripciones
	if not timedelta(minutes=15) <= duracion <= timedelta(minutes=60):
		raise InscripcionError('La inscripción pública debe durar entre 15 y 60 minutos.')
	with transaction.atomic():
		torneo.full_clean()
		torneo.fecha_publicacion = ahora
		torneo.modo_xp = Torneo.ModoXP.AUTOMATICA
		torneo.save(update_fields=('fecha_publicacion', 'modo_xp', 'fecha_actualizacion'))
		nuevo_estado = (
			Torneo.Estado.INSCRIPCIONES_ABIERTAS
			if torneo.fecha_apertura_inscripciones <= ahora
			else Torneo.Estado.PROXIMAMENTE
		)
		_registrar_transicion(torneo, nuevo_estado, actor=actor, motivo='Publicación del torneo.')
		if nuevo_estado == Torneo.Estado.INSCRIPCIONES_ABIERTAS:
			try:
				from notificaciones.models import Notificacion
				from notificaciones.services import notificar_seguidores
				notificar_seguidores(torneo, Notificacion.Tipo.APERTURA_INSCRIPCIONES, 'Inscripciones abiertas', f'Ya puedes inscribirte en {torneo.nombre}.', clave_evento=f'apertura:{torneo.pk}:{torneo.fecha_publicacion}', torneo=torneo, excluir_ids=torneo.inscripciones.filter(estado=InscripcionTorneo.Estado.CONFIRMADA).values_list('usuario_id', flat=True))
			except Exception:
				pass
	return torneo


def procesar_calendario(ahora=None):
	"""Aplica transiciones temporales pendientes; es idempotente y manual."""
	ahora = ahora or timezone.now()
	transiciones = 0
	with transaction.atomic():
		for torneo in Torneo.objects.filter(
			tipo__in=(Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL),
		).order_by('pk'):
			if (
				torneo.tipo == Torneo.Tipo.PUBLICO
				and torneo.estado == Torneo.Estado.INSCRIPCIONES_CERRADAS
				and torneo.participantes_confirmados == torneo.max_participantes
				and not torneo.partidas.exists()
			):
				_generar_bracket_si_completo(torneo)
				transiciones += 1
			elif torneo.estado == Torneo.Estado.PROXIMAMENTE and torneo.fecha_apertura_inscripciones <= ahora:
				fin_prorroga = (
					torneo.fecha_cierre_inscripciones + timedelta(minutes=torneo.duracion_prorroga_min)
					if torneo.fecha_cierre_inscripciones and torneo.duracion_prorroga_min else torneo.fecha_cierre_inscripciones
				)
				if torneo.fecha_cierre_inscripciones and ahora > torneo.fecha_cierre_inscripciones:
					if torneo.participantes_confirmados >= torneo.max_participantes:
						nuevo_estado = Torneo.Estado.INSCRIPCIONES_CERRADAS
					elif fin_prorroga and ahora <= fin_prorroga:
						nuevo_estado = Torneo.Estado.PRORROGA
					else:
						nuevo_estado = Torneo.Estado.CANCELADO if torneo.duracion_prorroga_min else Torneo.Estado.INSCRIPCIONES_CERRADAS
				else:
					nuevo_estado = Torneo.Estado.INSCRIPCIONES_ABIERTAS
				cambio = _registrar_transicion(torneo, nuevo_estado, motivo='Procesamiento de calendario.')
				transiciones += cambio
				if cambio and nuevo_estado == Torneo.Estado.INSCRIPCIONES_CERRADAS:
					_generar_bracket_si_completo(torneo)
				if cambio:
					try:
						from notificaciones.models import Notificacion
						from notificaciones.services import notificar_seguidores
						notificar_seguidores(torneo, Notificacion.Tipo.APERTURA_INSCRIPCIONES, 'Inscripciones abiertas', f'Ya puedes inscribirte en {torneo.nombre}.', clave_evento=f'apertura:{torneo.pk}:{torneo.fecha_publicacion}', torneo=torneo)
					except Exception:
						pass
			elif torneo.estado == Torneo.Estado.INSCRIPCIONES_ABIERTAS and torneo.fecha_cierre_inscripciones and torneo.fecha_cierre_inscripciones <= ahora:
				if torneo.participantes_confirmados >= torneo.max_participantes:
					cambio = _registrar_transicion(torneo, Torneo.Estado.INSCRIPCIONES_CERRADAS, motivo='Plazas completas al cierre.')
					transiciones += cambio
					if cambio:
						_generar_bracket_si_completo(torneo)
				elif torneo.duracion_prorroga_min:
					transiciones += _registrar_transicion(torneo, Torneo.Estado.PRORROGA, motivo='Inicio de la prórroga.')
				else:
					transiciones += _registrar_transicion(torneo, Torneo.Estado.INSCRIPCIONES_CERRADAS, motivo='Cierre de inscripciones.')
			elif torneo.estado == Torneo.Estado.PRORROGA:
				if torneo.participantes_confirmados >= torneo.max_participantes:
						cambio = _registrar_transicion(torneo, Torneo.Estado.INSCRIPCIONES_CERRADAS, motivo='Plazas completas durante la prórroga.')
						transiciones += cambio
						if cambio:
							_generar_bracket_si_completo(torneo)
				elif torneo.fecha_cierre_inscripciones + timedelta(minutes=torneo.duracion_prorroga_min) <= ahora:
					cambio = _registrar_transicion(torneo, Torneo.Estado.CANCELADO, motivo='Prórroga finalizada sin completar las plazas.')
					transiciones += cambio
					if cambio:
						try:
							from notificaciones.models import Notificacion
							from notificaciones.services import crear_notificacion
							usuarios = set(torneo.inscripciones.filter(
								estado=InscripcionTorneo.Estado.CONFIRMADA,
							).values_list('usuario_id', flat=True))
							usuarios.update(torneo.arbitros.filter(
								estado_invitacion='ACEPTADA',
								activo_en_torneo=True,
							).values_list('usuario_id', flat=True))
							for usuario_id in usuarios:
								usuario = torneo.organizador.__class__.objects.get(pk=usuario_id)
								crear_notificacion(
									usuario,
									Notificacion.Tipo.CANCELACION_TORNEO,
									'Torneo cancelado',
									f'El torneo {torneo.nombre} ha sido cancelado.',
									es_critica=True,
									clave_evento=f'cancelacion:{torneo.pk}',
									torneo=torneo,
								)
						except Exception:
							pass
	from partidas.scheduling import procesar_checkins
	procesar_checkins(ahora=ahora)
	return transiciones


def inscribir_usuario(torneo, usuario):
	with _bloqueo_de_inscripcion():
		torneo = _torneo_bloqueado(torneo.pk)
		ahora = timezone.now()
		if not usuario.is_active:
			raise InscripcionError('La cuenta del usuario no está activa.')
		if torneo.organizador_id == usuario.pk:
			raise InscripcionError('No puedes inscribirte como participante en un torneo que organizas.')
		if hasattr(torneo, 'arbitros') and torneo.arbitros.filter(
			usuario=usuario,
			estado_invitacion='ACEPTADA',
			activo_en_torneo=True,
		).exists():
			raise InscripcionError('Un árbitro activo no puede inscribirse en su propio torneo.')
		if not _inscripciones_abiertas(torneo, ahora):
			raise InscripcionError('Las inscripciones no están abiertas en este momento.')
		if torneo.participantes_confirmados >= torneo.max_participantes:
			raise InscripcionError('El torneo ya no tiene plazas disponibles.')
		if InscripcionTorneo.objects.filter(torneo=torneo, usuario=usuario).exists():
			raise InscripcionError('El usuario ya tiene una inscripción en este torneo.')

		try:
			perfil = usuario.perfiles_videojuego.get(videojuego=torneo.videojuego)
		except usuario.perfiles_videojuego.model.DoesNotExist:
			raise InscripcionError('El usuario no tiene perfil para este videojuego.')

		if torneo.tipo != Torneo.Tipo.PRIVADO:
			if torneo.nivel_minimo is not None and usuario.nivel < torneo.nivel_minimo:
				raise InscripcionError('El nivel del usuario no alcanza el mínimo del torneo.')
			rango = perfil.rango_declarado
			if torneo.rango_minimo_id and (rango is None or rango.posicion < torneo.rango_minimo.posicion):
				raise InscripcionError('El rango del usuario no alcanza el mínimo del torneo.')
			if torneo.rango_maximo_id and (rango is None or rango.posicion > torneo.rango_maximo.posicion):
				raise InscripcionError('El rango del usuario supera el máximo del torneo.')

		try:
			inscripcion = InscripcionTorneo.objects.create(
				torneo=torneo,
				usuario=usuario,
				perfil_videojuego=perfil,
				nick_historico=perfil.nick_en_juego,
				rango_declarado_al_inscribirse=perfil.rango_declarado,
				estado=InscripcionTorneo.Estado.CONFIRMADA,
				fecha_confirmacion=ahora,
			)
		except (IntegrityError, ValidationError) as error:
			raise InscripcionError('No se pudo crear la inscripción; quizá ya existe.') from error
		if torneo.estado == Torneo.Estado.PRORROGA and torneo.participantes_confirmados >= torneo.max_participantes:
			_registrar_transicion(
				torneo,
				Torneo.Estado.INSCRIPCIONES_CERRADAS,
				motivo='Plazas completas durante la prórroga.',
			)
		return inscripcion


def cancelar_inscripcion(inscripcion, motivo=''):
	with _bloqueo_de_inscripcion():
		inscripcion = InscripcionTorneo.objects.select_related('torneo').get(pk=inscripcion.pk)
		ahora = timezone.now()
		if inscripcion.estado != InscripcionTorneo.Estado.CONFIRMADA:
			raise InscripcionError('Solo se puede cancelar una inscripción confirmada.')
		if not _inscripciones_abiertas(inscripcion.torneo, ahora):
			raise InscripcionError('Las inscripciones ya están cerradas; no es una cancelación normal.')
		inscripcion.estado = InscripcionTorneo.Estado.CANCELADA
		inscripcion.fecha_cancelacion = ahora
		inscripcion.motivo_cancelacion = motivo
		inscripcion.save(update_fields=('estado', 'fecha_cancelacion', 'motivo_cancelacion'))
		return inscripcion