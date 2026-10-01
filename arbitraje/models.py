from django.conf import settings
from django.core.validators import MinLengthValidator
from django.db import models
from django.utils import timezone

from torneos.models import Torneo


class ArbitroTorneo(models.Model):
	class EstadoInvitacion(models.TextChoices):
		PENDIENTE = 'PENDIENTE', 'Pendiente'
		ACEPTADA = 'ACEPTADA', 'Aceptada'
		RECHAZADA = 'RECHAZADA', 'Rechazada'
		CANCELADA = 'CANCELADA', 'Cancelada'

	usuario = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='invitaciones_arbitraje',
	)
	torneo = models.ForeignKey(Torneo, on_delete=models.CASCADE, related_name='arbitros')
	estado_invitacion = models.CharField(
		max_length=10,
		choices=EstadoInvitacion.choices,
		default=EstadoInvitacion.PENDIENTE,
	)
	fecha_invitacion = models.DateTimeField(default=timezone.now)
	fecha_respuesta = models.DateTimeField(blank=True, null=True)
	activo_en_torneo = models.BooleanField(default=False)
	fecha_salida = models.DateTimeField(blank=True, null=True)
	motivo_salida = models.TextField(blank=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=('usuario', 'torneo'),
				name='unique_arbitro_por_torneo',
			),
		]

	def __str__(self):
		return f'{self.usuario} - {self.torneo}'


class HistorialAsignacionArbitro(models.Model):
	partida = models.ForeignKey(
		'partidas.Partida',
		on_delete=models.PROTECT,
		related_name='historial_asignaciones_arbitro',
	)
	arbitro_anterior = models.ForeignKey(
		ArbitroTorneo,
		on_delete=models.PROTECT,
		related_name='historiales_como_anterior',
		blank=True,
		null=True,
	)
	arbitro_nuevo = models.ForeignKey(
		ArbitroTorneo,
		on_delete=models.PROTECT,
		related_name='historiales_como_nuevo',
		blank=True,
		null=True,
	)
	actor = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='historiales_asignacion_arbitro',
		blank=True,
		null=True,
	)
	motivo = models.TextField(validators=[MinLengthValidator(1)])
	fecha = models.DateTimeField(default=timezone.now)
