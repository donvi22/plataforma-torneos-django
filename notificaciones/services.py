from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Notificacion, PreferenciasNotificacion, SeguimientoTorneo


class NotificacionError(Exception):
	pass


_TIPOS_OPCIONALES = {
	Notificacion.Tipo.APERTURA_INSCRIPCIONES: 'avisar_apertura_inscripciones',
	Notificacion.Tipo.RESULTADO_OFICIAL: 'avisar_resultados_torneos',
}


def crear_notificacion(destinatario, tipo, titulo, mensaje, *, es_critica=False, clave_evento='', torneo=None, partida=None, invitacion_arbitral=None, denuncia=None):
	if not es_critica and not getattr(PreferenciasNotificacion.para_usuario(destinatario), _TIPOS_OPCIONALES.get(tipo, 'avisar_novedades_torneos')):
		return None
	if clave_evento:
		existente = Notificacion.objects.filter(destinatario=destinatario, clave_evento=clave_evento).first()
		if existente:
			return existente
	try:
		return Notificacion.objects.create(
			destinatario=destinatario,
			tipo=tipo,
			titulo=titulo,
			mensaje=mensaje,
			es_critica=es_critica,
			clave_evento=clave_evento,
			torneo=torneo,
			partida=partida,
			invitacion_arbitral=invitacion_arbitral,
			denuncia=denuncia,
		)
	except IntegrityError:
		return Notificacion.objects.get(destinatario=destinatario, clave_evento=clave_evento)


def seguir_torneo(usuario, torneo):
	if torneo.tipo == torneo.Tipo.PRIVADO and torneo.organizador_id != usuario.pk:
		raise NotificacionError('No puedes seguir un torneo privado ajeno.')
	if torneo.tipo != torneo.Tipo.PRIVADO and not torneo.fecha_publicacion:
		raise NotificacionError('El torneo todavía no es visible.')
	seguimiento, _ = SeguimientoTorneo.objects.get_or_create(usuario=usuario, torneo=torneo)
	return seguimiento


def dejar_de_seguir_torneo(usuario, torneo):
	SeguimientoTorneo.objects.filter(usuario=usuario, torneo=torneo).delete()


def notificar_seguidores(torneo, tipo, titulo, mensaje, *, es_critica=False, clave_evento='', excluir_ids=()):
	for seguimiento in SeguimientoTorneo.objects.filter(torneo=torneo).select_related('usuario'):
		if seguimiento.usuario_id not in excluir_ids:
			crear_notificacion(seguimiento.usuario, tipo, titulo, mensaje, es_critica=es_critica, clave_evento=f'{clave_evento}:{seguimiento.usuario_id}' if clave_evento else '', torneo=torneo)


def notificaciones_de_usuario(usuario):
	return Notificacion.objects.filter(destinatario=usuario)


def no_leidas(usuario):
	return Notificacion.objects.filter(destinatario=usuario, leida=False).count()


def marcar_leida(usuario, notificacion):
	if notificacion.destinatario_id != usuario.pk:
		raise NotificacionError('No puedes modificar notificaciones ajenas.')
	if notificacion.leida:
		return notificacion
	notificacion.leida = True
	notificacion.fecha_lectura = timezone.now()
	notificacion.save(update_fields=('leida', 'fecha_lectura'))
	return notificacion


def marcar_todas_leidas(usuario):
	ahora = timezone.now()
	return Notificacion.objects.filter(destinatario=usuario, leida=False).update(leida=True, fecha_lectura=ahora)