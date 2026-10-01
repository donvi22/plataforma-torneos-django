from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator, MinValueValidator
from django.db import models

from torneos.models import InscripcionTorneo, Torneo


class Partida(models.Model):
	class Estado(models.TextChoices):
		PENDIENTE = 'PENDIENTE', 'Pendiente'
		PROGRAMADA = 'PROGRAMADA', 'Programada'
		CHECK_IN = 'CHECK_IN', 'Check-in'
		LISTA_PARA_COMENZAR = 'LISTA_PARA_COMENZAR', 'Lista para comenzar'
		EN_CURSO = 'EN_CURSO', 'En curso'
		PENDIENTE_VALIDACION = 'PENDIENTE_VALIDACION', 'Pendiente de validación'
		FINALIZADA = 'FINALIZADA', 'Finalizada'
		CANCELADA = 'CANCELADA', 'Cancelada'
		INCIDENCIA = 'INCIDENCIA', 'Incidencia'

	torneo = models.ForeignKey(Torneo, on_delete=models.CASCADE, related_name='partidas')
	arbitro_asignado = models.ForeignKey(
		'arbitraje.ArbitroTorneo',
		on_delete=models.SET_NULL,
		related_name='partidas_asignadas',
		blank=True,
		null=True,
	)
	numero_ronda = models.PositiveIntegerField(validators=[MinValueValidator(1)])
	numero_orden = models.PositiveIntegerField(validators=[MinValueValidator(1)])
	siguiente_partida = models.ForeignKey(
		'self',
		on_delete=models.PROTECT,
		related_name='partidas_anteriores',
		blank=True,
		null=True,
	)
	posicion_en_siguiente_partida = models.PositiveSmallIntegerField(
		blank=True,
		null=True,
		validators=[MinValueValidator(1)],
	)
	estado = models.CharField(
		max_length=25,
		choices=Estado.choices,
		default=Estado.PENDIENTE,
	)
	fecha_hora_programada = models.DateTimeField(blank=True, null=True)
	fecha_hora_rivales_confirmados = models.DateTimeField(blank=True, null=True)
	fecha_hora_apertura_checkin = models.DateTimeField(blank=True, null=True)
	fecha_hora_inicio_real = models.DateTimeField(blank=True, null=True)
	fecha_hora_fin_real = models.DateTimeField(blank=True, null=True)
	codigo_lobby = models.CharField(max_length=150, blank=True)
	contrasena_lobby = models.CharField(max_length=150, blank=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=('torneo', 'numero_ronda', 'numero_orden'),
				name='unique_partida_torneo_ronda_orden',
			),
		]
		ordering = ('numero_ronda', 'numero_orden')

	def __str__(self):
		return f'{self.torneo} - R{self.numero_ronda}P{self.numero_orden}'

	def clean(self):
		super().clean()
		errores = {}
		if self.siguiente_partida_id:
			if self.siguiente_partida.torneo_id != self.torneo_id:
				errores['siguiente_partida'] = 'La siguiente partida debe pertenecer al mismo torneo.'
			if self.siguiente_partida_id == self.pk:
				errores['siguiente_partida'] = 'Una partida no puede apuntar a sí misma.'
			if self.posicion_en_siguiente_partida not in (1, 2):
				errores['posicion_en_siguiente_partida'] = 'La posición debe ser 1 o 2.'
		elif self.posicion_en_siguiente_partida is not None:
			errores['posicion_en_siguiente_partida'] = (
				'Una partida final no puede tener posición de siguiente partida.'
			)
		if self.arbitro_asignado_id:
			if self.arbitro_asignado.torneo_id != self.torneo_id:
				errores['arbitro_asignado'] = 'El árbitro debe pertenecer al mismo torneo.'
			elif self.arbitro_asignado.estado_invitacion != 'ACEPTADA' or not self.arbitro_asignado.activo_en_torneo:
				errores['arbitro_asignado'] = 'El árbitro debe estar aceptado y activo.'
		if errores:
			raise ValidationError(errores)


class ParticipantePartida(models.Model):
	partida = models.ForeignKey(Partida, on_delete=models.CASCADE, related_name='participantes')
	inscripcion = models.ForeignKey(
		InscripcionTorneo,
		on_delete=models.PROTECT,
		related_name='participaciones_partida',
	)
	posicion = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=('partida', 'inscripcion'),
				name='unique_inscripcion_por_partida',
			),
			models.UniqueConstraint(
				fields=('partida', 'posicion'),
				name='unique_posicion_por_partida',
			),
		]

	def __str__(self):
		return f'{self.partida} - {self.inscripcion.nick_historico}'

	def clean(self):
		super().clean()
		errores = {}
		if self.posicion not in (1, 2):
			errores['posicion'] = 'La posición debe ser 1 o 2.'
		if self.partida_id and self.inscripcion_id:
			if self.partida.torneo_id != self.inscripcion.torneo_id:
				errores['inscripcion'] = 'La inscripción debe pertenecer al torneo de la partida.'
			if self.partida.participantes.exclude(pk=self.pk).filter(
				inscripcion=self.inscripcion,
			).exists():
				errores['inscripcion'] = 'La inscripción ya participa en esta partida.'
			if self.partida.participantes.exclude(pk=self.pk).filter(
				posicion=self.posicion,
			).exists():
				errores['posicion'] = 'La posición ya está ocupada en esta partida.'
			if self.partida.participantes.exclude(pk=self.pk).count() >= 2:
				errores['partida'] = 'El MVP solo permite dos participantes por partida.'
		if errores:
			raise ValidationError(errores)


class CheckInPartida(models.Model):
	class Tipo(models.TextChoices):
		PARTICIPANTE = 'PARTICIPANTE', 'Participante'
		ARBITRO = 'ARBITRO', 'Árbitro o responsable'

	partida = models.ForeignKey(Partida, on_delete=models.CASCADE, related_name='checkins')
	usuario = models.ForeignKey(
		'usuarios.Usuario',
		on_delete=models.PROTECT,
		related_name='checkins_partida',
	)
	tipo = models.CharField(max_length=15, choices=Tipo.choices)
	confirmado = models.BooleanField(default=False)
	fecha_confirmacion = models.DateTimeField(blank=True, null=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=('partida', 'usuario', 'tipo'),
				name='unique_checkin_partida_usuario_tipo',
			),
		]

	def clean(self):
		super().clean()
		if self.tipo == self.Tipo.PARTICIPANTE:
			if not self.partida.participantes.filter(inscripcion__usuario=self.usuario).exists():
				raise ValidationError({'usuario': 'El usuario no participa en esta partida.'})
		elif self.tipo == self.Tipo.ARBITRO:
			arbitro = self.partida.arbitro_asignado
			organizador_responsable = (
				self.partida.arbitro_asignado_id is None
				and self.partida.torneo.organizador_id == self.usuario_id
			)
			if not organizador_responsable and not (
				arbitro
				and arbitro.usuario_id == self.usuario_id
				and arbitro.estado_invitacion == 'ACEPTADA'
				and arbitro.activo_en_torneo
			):
				raise ValidationError({'usuario': 'El usuario no es el responsable de esta partida.'})


class HistorialProgramacionPartida(models.Model):
	partida = models.ForeignKey(Partida, on_delete=models.PROTECT, related_name='historial_programacion')
	fecha_anterior = models.DateTimeField(blank=True, null=True)
	fecha_nueva = models.DateTimeField(blank=True, null=True)
	actor = models.ForeignKey(
		'usuarios.Usuario',
		on_delete=models.PROTECT,
		related_name='cambios_programacion_partida',
	)
	motivo = models.TextField(validators=[MinLengthValidator(1)])
	fecha = models.DateTimeField(auto_now_add=True)


class DeclaracionResultado(models.Model):
	partida = models.ForeignKey(Partida, on_delete=models.CASCADE, related_name='declaraciones_resultado')
	usuario = models.ForeignKey(
		'usuarios.Usuario',
		on_delete=models.PROTECT,
		related_name='declaraciones_resultado',
	)
	resultado_declarado = models.CharField(max_length=20, validators=[MinLengthValidator(1)])
	ganador_declarado = models.ForeignKey(
		InscripcionTorneo,
		on_delete=models.PROTECT,
		related_name='declaraciones_como_ganador',
		blank=True,
		null=True,
	)
	fecha = models.DateTimeField(auto_now_add=True)

	def clean(self):
		super().clean()
		if self.partida_id and not self.partida.participantes.filter(inscripcion__usuario=self.usuario).exists():
			raise ValidationError({'usuario': 'El usuario no participa en esta partida.'})
		if self.ganador_declarado_id and not self.partida.participantes.filter(
			inscripcion=self.ganador_declarado,
		).exists():
			raise ValidationError({'ganador_declarado': 'El ganador debe pertenecer a esta partida.'})


class ResultadoPartida(models.Model):
	class TipoResultado(models.TextChoices):
		NORMAL = 'NORMAL', 'Normal'
		INCOMPARECENCIA = 'INCOMPARECENCIA', 'Incomparecencia'
		DOBLE_INCOMPARECENCIA = 'DOBLE_INCOMPARECENCIA', 'Doble incomparecencia'
		AVANCE_AUTOMATICO = 'AVANCE_AUTOMATICO', 'Avance automático'

	partida = models.OneToOneField(Partida, on_delete=models.PROTECT, related_name='resultado_oficial')
	resultado = models.CharField(max_length=20, blank=True)
	ganador = models.ForeignKey(
		InscripcionTorneo,
		on_delete=models.PROTECT,
		related_name='resultados_ganados',
		blank=True,
		null=True,
	)
	tipo_resultado = models.CharField(max_length=25, choices=TipoResultado.choices)
	validado_por = models.ForeignKey(
		'usuarios.Usuario',
		on_delete=models.PROTECT,
		related_name='resultados_validados',
		blank=True,
		null=True,
	)
	fecha_validacion = models.DateTimeField(auto_now_add=True)
	motivo = models.TextField(blank=True)

	def clean(self):
		super().clean()
		if self.ganador_id and not self.partida.participantes.filter(inscripcion=self.ganador).exists():
			raise ValidationError({'ganador': 'El ganador debe pertenecer a la partida.'})
		if self.tipo_resultado == self.TipoResultado.DOBLE_INCOMPARECENCIA and self.ganador_id:
			raise ValidationError({'ganador': 'La doble incomparecencia no tiene ganador.'})
		if self.tipo_resultado == self.TipoResultado.AVANCE_AUTOMATICO and not self.ganador_id:
			raise ValidationError({'ganador': 'El avance automático necesita un ganador.'})

	def save(self, *args, **kwargs):
		if self.pk:
			raise ValidationError('Un resultado oficial validado no puede modificarse directamente.')
		super().save(*args, **kwargs)

	def delete(self, *args, **kwargs):
		raise ValidationError('Un resultado oficial validado no puede eliminarse directamente.')


class HistorialResultadoPartida(models.Model):
	partida = models.ForeignKey(Partida, on_delete=models.PROTECT, related_name='historial_resultados')
	resultado_anterior = models.CharField(max_length=20, blank=True)
	resultado_nuevo = models.CharField(max_length=20, blank=True)
	ganador_anterior = models.ForeignKey(
		InscripcionTorneo,
		on_delete=models.PROTECT,
		related_name='historial_como_ganador_anterior',
		blank=True,
		null=True,
	)
	ganador_nuevo = models.ForeignKey(
		InscripcionTorneo,
		on_delete=models.PROTECT,
		related_name='historial_como_ganador_nuevo',
		blank=True,
		null=True,
	)
	tipo_resultado_anterior = models.CharField(max_length=25, blank=True)
	tipo_resultado_nuevo = models.CharField(max_length=25)
	usuario_responsable = models.ForeignKey(
		'usuarios.Usuario',
		on_delete=models.PROTECT,
		related_name='historiales_resultado_modificados',
	)
	motivo = models.TextField(validators=[MinLengthValidator(1)])
	fecha = models.DateTimeField(auto_now_add=True)
