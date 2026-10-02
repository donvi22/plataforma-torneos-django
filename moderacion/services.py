"""Servicios auditables para denuncias y resoluciones administrativas."""

from threading import RLock

from django.db import transaction
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from torneos.models import InscripcionTorneo, Torneo
from torneos.services import _es_administrador_autorizado

from .models import Denuncia, HistorialDenuncia


class ModeracionError(Exception):
	"""Error de negocio en la creación o resolución de una denuncia."""


SANCIONES_KARMA = {
	Denuncia.TipoSancion.ADVERTENCIA: 0,
	Denuncia.TipoSancion.LEVE: -5,
	Denuncia.TipoSancion.MEDIA: -15,
	Denuncia.TipoSancion.GRAVE: -30,
}

ESTADOS_ABIERTOS = (Denuncia.Estado.ABIERTA, Denuncia.Estado.EN_REVISION)
_moderacion_lock = RLock()


def _es_contexto_accesible(denunciante, *, torneo=None, partida=None):
	if partida is not None:
		from partidas.views import _puede_ver_partida
		if not _puede_ver_partida(partida, denunciante):
			return False
	if torneo is not None:
		if partida is not None and partida.torneo_id != torneo.pk:
			return False
		if torneo.tipo == Torneo.Tipo.PRIVADO or torneo.estado == Torneo.Estado.BORRADOR:
			from torneos.public_views import _puede_ver_privado
			return _puede_ver_privado(torneo, denunciante)
		from torneos.public_views import PUBLIC_STATES
		return bool(
			torneo.tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL)
			and torneo.fecha_publicacion
			and torneo.estado in PUBLIC_STATES
			and torneo.videojuego.activo
		)
	return partida is not None


def validar_contexto_denuncia(denunciante, *, torneo=None, partida=None):
	if not _es_contexto_accesible(denunciante, torneo=torneo, partida=partida):
		raise ModeracionError('No tienes acceso al recurso relacionado con la denuncia.')


def _usuario_relacionado(usuario, torneo, partida=None):
	if usuario is None:
		return True
	if torneo.organizador_id == usuario.pk:
		return True
	if InscripcionTorneo.objects.filter(torneo=torneo, usuario=usuario).exists():
		return True
	if ArbitroTorneo.objects.filter(torneo=torneo, usuario=usuario).exists():
		return True
	return bool(
		partida
		and partida.historial_asignaciones_arbitro.filter(
			arbitro_anterior__usuario=usuario,
		).exists()
	)


def _denuncia_abierta_equivalente(denunciante, categoria, usuario_denunciado, torneo, partida):
	queryset = Denuncia.objects.filter(
		denunciante=denunciante,
		categoria=categoria,
		usuario_denunciado=usuario_denunciado,
		estado__in=ESTADOS_ABIERTOS,
	)
	if partida is not None:
		return queryset.filter(partida=partida).first()
	return queryset.filter(torneo=torneo, partida__isnull=True).first()


def _registrar_historial(denuncia, accion, estado_anterior, responsable, comentario):
	HistorialDenuncia.objects.create(
		denuncia=denuncia,
		accion=accion,
		estado_anterior=estado_anterior or '',
		estado_nuevo=denuncia.estado,
		responsable=responsable,
		comentario=comentario.strip(),
	)


def crear_denuncia(denunciante, categoria, descripcion, *, torneo=None, partida=None, usuario_denunciado=None):
	if not denunciante or not denunciante.is_active:
		raise ModeracionError('La cuenta debe estar activa para enviar una denuncia.')
	if categoria not in Denuncia.Categoria.values:
		raise ModeracionError('Selecciona una categoría válida.')
	if not descripcion or not descripcion.strip():
		raise ModeracionError('La descripción es obligatoria.')
	if len(descripcion.strip()) > 10000:
		raise ModeracionError('La descripción supera el límite permitido.')
	if partida is not None:
		if torneo is not None and partida.torneo_id != torneo.pk:
			raise ModeracionError('La partida no pertenece al torneo seleccionado.')
		torneo = partida.torneo
	if torneo is None and partida is None:
		raise ModeracionError('La denuncia debe estar relacionada con un torneo o una partida.')
	if usuario_denunciado is not None:
		if usuario_denunciado.pk == denunciante.pk:
			raise ModeracionError('No puedes denunciarte a ti mismo como usuario relacionado.')
		if not _usuario_relacionado(usuario_denunciado, torneo, partida):
			raise ModeracionError('El usuario indicado no está relacionado con este torneo o partida.')
	validar_contexto_denuncia(denunciante, torneo=torneo, partida=partida)

	with _moderacion_lock, transaction.atomic():
		existente = _denuncia_abierta_equivalente(
			denunciante, categoria, usuario_denunciado, torneo, partida,
		)
		if existente:
			raise ModeracionError('Ya tienes una denuncia abierta equivalente para este caso.')
		denuncia = Denuncia.objects.create(
			denunciante=denunciante,
			usuario_denunciado=usuario_denunciado,
			torneo=torneo,
			partida=partida,
			categoria=categoria,
			descripcion=descripcion.strip(),
		)
		_registrar_historial(
			denuncia,
			HistorialDenuncia.Accion.CREACION,
			'',
			denunciante,
			'Denuncia recibida; la alegación está pendiente de revisión.',
		)
		return denuncia


def _validar_administrador(administrador):
	if not _es_administrador_autorizado(administrador):
		raise ModeracionError('Se requiere un administrador autorizado.')


def tomar_en_revision(denuncia, administrador, comentario='Asignada para revisión administrativa.'):
	_validar_administrador(administrador)
	if not comentario or not comentario.strip():
		raise ModeracionError('El comentario de revisión es obligatorio.')
	with _moderacion_lock, transaction.atomic():
		denuncia = Denuncia.objects.select_for_update().get(pk=denuncia.pk)
		if denuncia.estado == Denuncia.Estado.EN_REVISION:
			if denuncia.asignada_a_id == administrador.pk:
				return denuncia, False
			raise ModeracionError('La denuncia ya está siendo revisada por otro administrador.')
		if denuncia.estado != Denuncia.Estado.ABIERTA:
			raise ModeracionError('Solo se pueden tomar denuncias abiertas.')
		anterior = denuncia.estado
		denuncia.estado = Denuncia.Estado.EN_REVISION
		denuncia.asignada_a = administrador
		denuncia.save(update_fields=('estado', 'asignada_a', 'fecha_actualizacion'))
		_registrar_historial(
			denuncia, HistorialDenuncia.Accion.TOMA_REVISION, anterior,
			administrador, comentario,
		)
		return denuncia, True


def _notificar_denunciante(denuncia, *, desestimada=False):
	from notificaciones.models import Notificacion
	from notificaciones.services import crear_notificacion

	estado = 'desestimada' if desestimada else 'resuelta'
	crear_notificacion(
		denuncia.denunciante,
		Notificacion.Tipo.INTERVENCION_REQUERIDA,
		'Denuncia ' + estado,
		f'Tu denuncia #{denuncia.pk} ha sido {estado}. {denuncia.resolucion}',
		es_critica=True,
		clave_evento=f'denuncia:{denuncia.pk}:{estado}',
		torneo=denuncia.torneo,
		partida=denuncia.partida,
		denuncia=denuncia,
	)


def resolver_denuncia(
	denuncia,
	administrador,
	*,
	estado,
	resolucion,
	notas_internas='',
	tipo_sancion='',
):
	_validar_administrador(administrador)
	if estado not in (Denuncia.Estado.RESUELTA, Denuncia.Estado.DESESTIMADA):
		raise ModeracionError('La resolución debe confirmar el problema o desestimar la denuncia.')
	if not resolucion or not resolucion.strip():
		raise ModeracionError('La resolución visible es obligatoria.')
	if tipo_sancion and tipo_sancion not in SANCIONES_KARMA:
		raise ModeracionError('Selecciona una sanción de la lista permitida.')
	if tipo_sancion and denuncia.usuario_denunciado_id is None:
		raise ModeracionError('Para aplicar una medida debe existir un usuario relacionado.')
	if estado == Denuncia.Estado.DESESTIMADA and tipo_sancion:
		raise ModeracionError('No se puede sancionar al desestimar una denuncia.')
	karma_solicitado = SANCIONES_KARMA.get(tipo_sancion, 0)

	with _moderacion_lock, transaction.atomic():
		denuncia = Denuncia.objects.select_for_update().get(pk=denuncia.pk)
		if denuncia.estado in (Denuncia.Estado.RESUELTA, Denuncia.Estado.DESESTIMADA):
			if (
				denuncia.estado == estado
				and denuncia.resuelta_por_id == administrador.pk
				and denuncia.resolucion == resolucion.strip()
				and denuncia.tipo_sancion == tipo_sancion
				and denuncia.notas_internas == notas_internas.strip()
			):
				return denuncia, False
			raise ModeracionError('La denuncia ya tiene una resolución final.')
		if denuncia.estado != Denuncia.Estado.EN_REVISION:
			raise ModeracionError('La denuncia debe estar en revisión antes de resolverla.')
		if denuncia.asignada_a_id != administrador.pk:
			raise ModeracionError('Solo el administrador asignado puede resolver esta denuncia.')

		estado_anterior = denuncia.estado
		denuncia.estado = estado
		denuncia.resuelta_por = administrador
		denuncia.fecha_resolucion = timezone.now()
		denuncia.resolucion = resolucion.strip()
		denuncia.notas_internas = notas_internas.strip()
		denuncia.tipo_sancion = tipo_sancion
		denuncia.karma_solicitado = karma_solicitado
		denuncia.hubo_sancion = bool(tipo_sancion)
		if karma_solicitado < 0:
			from usuarios.karma import ajustar_karma

			historial_karma = ajustar_karma(
				denuncia.usuario_denunciado,
				karma_solicitado,
				f'Denuncia #{denuncia.pk}: {denuncia.resolucion}',
				administrador,
				f'moderacion-denuncia:{denuncia.pk}:sancion',
				torneo=denuncia.torneo,
				partida=denuncia.partida,
			)
			denuncia.historial_karma = historial_karma
		denuncia.save(update_fields=(
			'estado', 'resuelta_por', 'fecha_resolucion', 'resolucion', 'notas_internas',
			'tipo_sancion', 'karma_solicitado', 'hubo_sancion', 'historial_karma', 'fecha_actualizacion',
		))
		accion = (
			HistorialDenuncia.Accion.DESESTIMACION
			if estado == Denuncia.Estado.DESESTIMADA
			else HistorialDenuncia.Accion.RESOLUCION
		)
		_registrar_historial(denuncia, accion, estado_anterior, administrador, denuncia.resolucion)
		if tipo_sancion:
			_registrar_historial(
				denuncia,
				HistorialDenuncia.Accion.SANCION_APLICADA,
				estado,
				administrador,
				f'Sanción aplicada: {Denuncia.TipoSancion(tipo_sancion).label} ({karma_solicitado} Karma).',
			)
		_notificar_denunciante(denuncia, desestimada=estado == Denuncia.Estado.DESESTIMADA)
		return denuncia, True