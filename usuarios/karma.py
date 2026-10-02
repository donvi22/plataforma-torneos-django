"""Registro y concesion centralizada de Karma por comportamiento fiable."""

from threading import RLock

from django.db import IntegrityError, transaction
from django.db.models import Q

from arbitraje.models import ArbitroTorneo, HistorialAsignacionArbitro
from partidas.models import DeclaracionResultado, ParticipantePartida, ResultadoPartida
from torneos.models import InscripcionTorneo, Torneo
from torneos.services import _es_administrador_autorizado

from .models import HistorialKarma, Usuario


class KarmaError(Exception):
	"""Error de validación o coherencia al registrar Karma."""


KARMA_RECOMPENSAS = {
	HistorialKarma.Tipo.PARTICIPACION_COMPLETADA: 5,
	HistorialKarma.Tipo.DECLARACION_VERAZ: 1,
	HistorialKarma.Tipo.ARBITRAJE_COMPLETADO: 5,
	HistorialKarma.Tipo.ORGANIZACION_COMPLETADA: 10,
}

KARMA_MINIMO = 0
_karma_lock = RLock()


def _mismo_evento(movimiento, *, usuario_id, cantidad_solicitada, tipo, motivo, relaciones, autorizado_por_id):
	return all((
		movimiento.usuario_id == usuario_id,
		movimiento.cantidad_solicitada == cantidad_solicitada,
		movimiento.tipo == tipo,
		movimiento.motivo == motivo,
		movimiento.autorizado_por_id == autorizado_por_id,
		all(getattr(movimiento, campo + '_id') == valor for campo, valor in relaciones.items()),
	))


def _registrar_movimiento(
	usuario,
	cantidad,
	tipo,
	motivo,
	clave_idempotencia,
	*,
	torneo=None,
	partida=None,
	inscripcion=None,
	arbitraje=None,
	historial_asignacion_arbitral=None,
	autorizado_por=None,
):
	if not isinstance(cantidad, int) or isinstance(cantidad, bool) or cantidad == 0:
		raise KarmaError('El cambio solicitado debe ser un entero distinto de cero.')
	if not motivo or not motivo.strip():
		raise KarmaError('El motivo es obligatorio.')
	if not clave_idempotencia or not clave_idempotencia.strip():
		raise KarmaError('La clave de idempotencia es obligatoria.')
	if len(clave_idempotencia) > 200:
		raise KarmaError('La clave de idempotencia supera los 200 caracteres.')
	if tipo in KARMA_RECOMPENSAS and cantidad != KARMA_RECOMPENSAS[tipo]:
		raise KarmaError('La recompensa debe coincidir con la configuración central de Karma.')
	if tipo in (HistorialKarma.Tipo.AJUSTE_ADMINISTRATIVO, HistorialKarma.Tipo.SANCION_CONFIRMADA):
		if not _es_administrador_autorizado(autorizado_por):
			raise KarmaError('Solo un administrador autorizado puede ajustar Karma.')
		if tipo == HistorialKarma.Tipo.SANCION_CONFIRMADA and cantidad >= 0:
			raise KarmaError('Una sanción confirmada requiere una cantidad negativa.')
		if tipo == HistorialKarma.Tipo.AJUSTE_ADMINISTRATIVO and cantidad > 0 and autorizado_por.pk == usuario.pk:
			raise KarmaError('Un administrador no puede concederse Karma a sí mismo.')
	elif autorizado_por is not None:
		raise KarmaError('Los eventos automáticos no admiten un autorizador manual.')
	if tipo not in HistorialKarma.Tipo.values:
		raise KarmaError('El tipo de evento de Karma no es válido.')

	relaciones = {
		'torneo': getattr(torneo, 'pk', None),
		'partida': getattr(partida, 'pk', None),
		'inscripcion': getattr(inscripcion, 'pk', None),
		'arbitraje': getattr(arbitraje, 'pk', None),
		'historial_asignacion_arbitral': getattr(historial_asignacion_arbitral, 'pk', None),
	}
	motivo = motivo.strip()
	with _karma_lock, transaction.atomic():
		existente = HistorialKarma.objects.filter(clave_idempotencia=clave_idempotencia).first()
		if existente:
			if not _mismo_evento(
				existente,
				usuario_id=usuario.pk,
				cantidad_solicitada=cantidad,
				tipo=tipo,
				motivo=motivo,
				relaciones=relaciones,
				autorizado_por_id=getattr(autorizado_por, 'pk', None),
			):
				raise KarmaError('La clave de idempotencia ya pertenece a otro movimiento.')
			return existente, False

		usuario = Usuario.objects.select_for_update().get(pk=usuario.pk)
		karma_antes = usuario.karma_total
		cantidad_aplicada = max(-karma_antes, cantidad) if cantidad < 0 else cantidad
		karma_despues = max(KARMA_MINIMO, karma_antes + cantidad_aplicada)
		movimiento = HistorialKarma(
			usuario=usuario,
			cantidad=cantidad_aplicada,
			cantidad_solicitada=cantidad,
			karma_antes=karma_antes,
			karma_despues=karma_despues,
			tipo=tipo,
			motivo=motivo,
			clave_idempotencia=clave_idempotencia,
			**{campo: objeto for campo, objeto in (
				('torneo', torneo),
				('partida', partida),
				('inscripcion', inscripcion),
				('arbitraje', arbitraje),
				('historial_asignacion_arbitral', historial_asignacion_arbitral),
				('autorizado_por', autorizado_por),
			) if objeto is not None},
		)
		try:
			with transaction.atomic():
				movimiento.full_clean()
				movimiento.save(force_insert=True)
				usuario.karma_total = karma_despues
				usuario.save(update_fields=('karma_total',))
		except IntegrityError as error:
			existente = HistorialKarma.objects.filter(clave_idempotencia=clave_idempotencia).first()
			if existente and _mismo_evento(
				existente,
				usuario_id=usuario.pk,
				cantidad_solicitada=cantidad,
				tipo=tipo,
				motivo=motivo,
				relaciones=relaciones,
				autorizado_por_id=getattr(autorizado_por, 'pk', None),
			):
				return existente, False
			raise KarmaError('No se pudo registrar el movimiento de Karma de forma segura.') from error
		return movimiento, True


def registrar_movimiento_karma(usuario, cantidad, tipo, motivo, clave_idempotencia, **relaciones):
	"""Registra un cambio de Karma y devuelve el movimiento idempotente."""
	return _registrar_movimiento(
		usuario, cantidad, tipo, motivo, clave_idempotencia, **relaciones,
	)[0]


def ajustar_karma(usuario, cantidad, motivo, autorizado_por, clave_idempotencia, **relaciones):
	"""Registra un ajuste o una sanción confirmada con autorización explícita."""
	if not isinstance(cantidad, int) or isinstance(cantidad, bool) or cantidad == 0:
		raise KarmaError('El cambio solicitado debe ser un entero distinto de cero.')
	if not motivo or not motivo.strip():
		raise KarmaError('El motivo es obligatorio.')
	tipo = (
		HistorialKarma.Tipo.SANCION_CONFIRMADA
		if cantidad < 0
		else HistorialKarma.Tipo.AJUSTE_ADMINISTRATIVO
	)
	return registrar_movimiento_karma(
		usuario,
		cantidad,
		tipo,
		motivo,
		clave_idempotencia,
		autorizado_por=autorizado_por,
		**relaciones,
	)


def _crear_recompensa(usuario, cantidad, tipo, motivo, clave, **relaciones):
	movimiento, creado = _registrar_movimiento(
		usuario, cantidad, tipo, motivo, clave, **relaciones,
	)
	return movimiento if creado else None


def _premiar_declaraciones_veraces(resultado):
	partida = resultado.partida
	torneo = partida.torneo
	if (
		torneo.tipo not in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL)
		or resultado.tipo_resultado != ResultadoPartida.TipoResultado.NORMAL
	):
		return []
	inscripciones_por_usuario = {
		participante.inscripcion.usuario_id: participante.inscripcion
		for participante in partida.participantes.select_related('inscripcion__usuario')
	}
	movimientos = []
	declaraciones = DeclaracionResultado.objects.filter(
		partida=partida,
		fecha__lt=resultado.fecha_validacion,
	).select_related('usuario', 'ganador_declarado')
	for declaracion in declaraciones:
		inscripcion = inscripciones_por_usuario.get(declaracion.usuario_id)
		if (
			inscripcion is None
			or declaracion.resultado_declarado != resultado.resultado
			or declaracion.ganador_declarado_id != resultado.ganador_id
		):
			continue
		movimiento = _crear_recompensa(
			declaracion.usuario,
			KARMA_RECOMPENSAS[HistorialKarma.Tipo.DECLARACION_VERAZ],
			HistorialKarma.Tipo.DECLARACION_VERAZ,
			f'Declaración coincidente con el resultado oficial de la partida {partida.pk}.',
			f'declaracion-veraz:{declaracion.pk}',
			torneo=torneo,
			partida=partida,
			inscripcion=inscripcion,
		)
		if movimiento:
			movimientos.append(movimiento)
	return movimientos


def _asignacion_en_validacion(partida, fecha_validacion):
	asignacion = partida.arbitro_asignado
	historiales_posteriores = HistorialAsignacionArbitro.objects.filter(
		partida=partida,
		fecha__gt=fecha_validacion,
	).select_related('arbitro_anterior').order_by('-fecha', '-pk')
	for historial in historiales_posteriores:
		asignacion = historial.arbitro_anterior
	if not asignacion:
		return None, None
	if (
		asignacion.estado_invitacion != ArbitroTorneo.EstadoInvitacion.ACEPTADA
		or asignacion.fecha_respuesta is None
		or asignacion.fecha_respuesta > fecha_validacion
		or (asignacion.fecha_salida and asignacion.fecha_salida <= fecha_validacion)
	):
		return None, None
	if not asignacion.activo_en_torneo and not asignacion.fecha_salida:
		return None, None
	historial = HistorialAsignacionArbitro.objects.filter(
		partida=partida,
		arbitro_nuevo=asignacion,
		fecha__lte=fecha_validacion,
	).order_by('-fecha', '-pk').first()
	return asignacion, historial


def _premiar_arbitraje(resultado):
	partida = resultado.partida
	torneo = partida.torneo
	if (
		torneo.tipo not in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL)
		or resultado.tipo_resultado != ResultadoPartida.TipoResultado.NORMAL
		or not resultado.validado_por_id
	):
		return None
	asignacion, historial = _asignacion_en_validacion(partida, resultado.fecha_validacion)
	if not asignacion or asignacion.usuario_id != resultado.validado_por_id:
		return None
	return _crear_recompensa(
		asignacion.usuario,
		KARMA_RECOMPENSAS[HistorialKarma.Tipo.ARBITRAJE_COMPLETADO],
		HistorialKarma.Tipo.ARBITRAJE_COMPLETADO,
		f'Validó correctamente la partida {partida.pk} como árbitro asignado.',
		f'arbitraje-completado:{partida.pk}',
		torneo=torneo,
		partida=partida,
		arbitraje=asignacion,
		historial_asignacion_arbitral=historial,
	)


def premiar_resultado_validado(resultado_id):
	"""Otorga solo las recompensas de comportamiento derivadas de un resultado oficial."""
	resultado = ResultadoPartida.objects.select_related(
		'partida__torneo', 'partida__arbitro_asignado', 'validado_por',
	).get(pk=resultado_id)
	if resultado.partida.torneo.estado == Torneo.Estado.CANCELADO:
		return []
	movimientos = _premiar_declaraciones_veraces(resultado)
	arbitraje = _premiar_arbitraje(resultado)
	if arbitraje:
		movimientos.append(arbitraje)
	return movimientos


def _inscripcion_elegible_para_participacion(inscripcion, torneo):
	if inscripcion.estado != InscripcionTorneo.Estado.CONFIRMADA:
		return None
	if HistorialKarma.objects.filter(
		usuario=inscripcion.usuario,
		torneo=torneo,
		tipo=HistorialKarma.Tipo.SANCION_CONFIRMADA,
	).filter(Q(inscripcion=inscripcion) | Q(inscripcion__isnull=True)).exists():
		return None
	participacion = ParticipantePartida.objects.filter(
		inscripcion=inscripcion,
		partida__torneo=torneo,
		partida__resultado_oficial__tipo_resultado=ResultadoPartida.TipoResultado.NORMAL,
	).select_related('partida').order_by('partida__numero_ronda', 'partida__numero_orden').first()
	return participacion


def _premiar_participacion_torneo(torneo):
	movimientos = []
	inscripciones = InscripcionTorneo.objects.filter(
		torneo=torneo,
	).select_related('usuario').order_by('pk')
	for inscripcion in inscripciones:
		participacion = _inscripcion_elegible_para_participacion(inscripcion, torneo)
		if not participacion:
			continue
		movimiento = _crear_recompensa(
			inscripcion.usuario,
			KARMA_RECOMPENSAS[HistorialKarma.Tipo.PARTICIPACION_COMPLETADA],
			HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
			f'Completó el torneo {torneo.nombre} sin una infracción confirmada.',
			f'participacion-completada:{torneo.pk}:{inscripcion.pk}',
			torneo=torneo,
			partida=participacion.partida,
			inscripcion=inscripcion,
		)
		if movimiento:
			movimientos.append(movimiento)
	return movimientos


def conceder_karma_torneo(torneo):
	"""Completa de manera recuperable y sin duplicados el Karma de un torneo cerrado."""
	torneo = Torneo.objects.get(pk=torneo.pk)
	if torneo.estado != Torneo.Estado.FINALIZADO:
		raise KarmaError('Solo se concede Karma a torneos finalizados.')
	if torneo.tipo not in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL):
		raise KarmaError('Los torneos privados no generan Karma automático.')
	movimientos = []
	resultados = ResultadoPartida.objects.filter(
		partida__torneo=torneo,
		tipo_resultado=ResultadoPartida.TipoResultado.NORMAL,
	).select_related('partida__torneo', 'partida__arbitro_asignado', 'validado_por')
	for resultado in resultados:
		movimientos.extend(_premiar_declaraciones_veraces(resultado))
		arbitraje = _premiar_arbitraje(resultado)
		if arbitraje:
			movimientos.append(arbitraje)
	movimientos.extend(_premiar_participacion_torneo(torneo))
	if torneo.tipo == Torneo.Tipo.PUBLICO:
		organizador = Usuario.objects.get(pk=torneo.organizador_id)
		organizacion = _crear_recompensa(
			organizador,
			KARMA_RECOMPENSAS[HistorialKarma.Tipo.ORGANIZACION_COMPLETADA],
			HistorialKarma.Tipo.ORGANIZACION_COMPLETADA,
			f'Completó la organización del torneo público {torneo.nombre}.',
			f'organizacion-completada:{torneo.pk}',
			torneo=torneo,
		)
		if organizacion:
			movimientos.append(organizacion)
	return movimientos


def premiar_cierre_torneo(torneo_id):
	"""Callback recuperable ejecutado después de confirmar el cierre en base de datos."""
	torneo = Torneo.objects.get(pk=torneo_id)
	if torneo.estado == Torneo.Estado.FINALIZADO:
		return conceder_karma_torneo(torneo)
	return []