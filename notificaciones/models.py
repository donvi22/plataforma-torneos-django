from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Notificacion(models.Model):
	class Tipo(models.TextChoices):
		APERTURA_INSCRIPCIONES = 'APERTURA_INSCRIPCIONES', 'Apertura de inscripciones'
		INVITACION_ARBITRAL = 'INVITACION_ARBITRAL', 'Invitación arbitral'
		RESPUESTA_INVITACION = 'RESPUESTA_INVITACION', 'Respuesta a invitación'
		ASIGNACION_PARTIDA = 'ASIGNACION_PARTIDA', 'Asignación de partida'
		REASIGNACION_PARTIDA = 'REASIGNACION_PARTIDA', 'Reasignación de partida'
		APERTURA_CHECKIN = 'APERTURA_CHECKIN', 'Apertura de check-in'
		CAMBIO_HORARIO = 'CAMBIO_HORARIO', 'Cambio de horario'
		RESULTADO_OFICIAL = 'RESULTADO_OFICIAL', 'Resultado oficial'
		CANCELACION_TORNEO = 'CANCELACION_TORNEO', 'Cancelación de torneo'
		INTERVENCION_REQUERIDA = 'INTERVENCION_REQUERIDA', 'Intervención requerida'

	destinatario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notificaciones')
	tipo = models.CharField(max_length=30, choices=Tipo.choices)
	titulo = models.CharField(max_length=200)
	mensaje = models.TextField()
	torneo = models.ForeignKey('torneos.Torneo', on_delete=models.CASCADE, blank=True, null=True, related_name='notificaciones')
	partida = models.ForeignKey('partidas.Partida', on_delete=models.CASCADE, blank=True, null=True, related_name='notificaciones')
	invitacion_arbitral = models.ForeignKey('arbitraje.ArbitroTorneo', on_delete=models.CASCADE, blank=True, null=True, related_name='notificaciones')
	denuncia = models.ForeignKey('moderacion.Denuncia', on_delete=models.SET_NULL, blank=True, null=True, related_name='notificaciones')
	es_critica = models.BooleanField(default=False)
	leida = models.BooleanField(default=False)
	fecha_creacion = models.DateTimeField(auto_now_add=True)
	fecha_lectura = models.DateTimeField(blank=True, null=True)
	clave_evento = models.CharField(max_length=200, blank=True)

	class Meta:
		ordering = ('-fecha_creacion', '-pk')
		constraints = [
			models.UniqueConstraint(
				fields=('destinatario', 'clave_evento'),
				condition=~Q(clave_evento=''),
				name='unique_notificacion_evento_destinatario',
			),
		]


class SeguimientoTorneo(models.Model):
	usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='seguimientos_torneo')
	torneo = models.ForeignKey('torneos.Torneo', on_delete=models.CASCADE, related_name='seguidores')
	fecha_seguimiento = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [models.UniqueConstraint(fields=('usuario', 'torneo'), name='unique_seguimiento_usuario_torneo')]


class PreferenciasNotificacion(models.Model):
	usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='preferencias_notificacion')
	avisar_apertura_inscripciones = models.BooleanField(default=True)
	avisar_novedades_torneos = models.BooleanField(default=True)
	avisar_resultados_torneos = models.BooleanField(default=True)

	@classmethod
	def para_usuario(cls, usuario):
		preferencias, _ = cls.objects.get_or_create(usuario=usuario)
		return preferencias
