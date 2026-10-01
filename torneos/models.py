from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from videojuegos.models import RangoVideojuego, Videojuego


class FormatoCompetitivo(models.Model):
	class TipoReglaParticipantes(models.TextChoices):
		ELIMINACION_DIRECTA = 'ELIMINACION_DIRECTA', 'Eliminación directa'

	nombre = models.CharField(max_length=100, unique=True)
	tipo_regla_participantes = models.CharField(
		max_length=30,
		choices=TipoReglaParticipantes.choices,
		default=TipoReglaParticipantes.ELIMINACION_DIRECTA,
	)
	min_participantes = models.PositiveIntegerField(
		default=2,
		validators=[MinValueValidator(1)],
	)
	max_participantes = models.PositiveIntegerField(
		default=128,
		validators=[MinValueValidator(1)],
	)

	def __str__(self):
		return self.nombre

	def es_tamano_valido(self, numero_participantes):
		if not 2 <= numero_participantes <= 128:
			return False
		if not self.min_participantes <= numero_participantes <= self.max_participantes:
			return False
		if self.tipo_regla_participantes == self.TipoReglaParticipantes.ELIMINACION_DIRECTA:
			return numero_participantes >= 2 and numero_participantes & (numero_participantes - 1) == 0
		return False

	@property
	def tamanos_participantes_validos(self):
		return [
			tamano for tamano in (2, 4, 8, 16, 32, 64, 128)
			if self.es_tamano_valido(tamano)
		]


class Torneo(models.Model):
	class Tipo(models.TextChoices):
		PRIVADO = 'PRIVADO', 'Privado'
		PUBLICO = 'PUBLICO', 'Público'
		OFICIAL = 'OFICIAL', 'Oficial'

	class TipoParticipante(models.TextChoices):
		INDIVIDUAL = 'INDIVIDUAL', 'Individual'
		EQUIPO = 'EQUIPO', 'Equipo'

	class Estado(models.TextChoices):
		BORRADOR = 'BORRADOR', 'Borrador'
		PROXIMAMENTE = 'PROXIMAMENTE', 'Próximamente'
		INSCRIPCIONES_ABIERTAS = 'INSCRIPCIONES_ABIERTAS', 'Inscripciones abiertas'
		PRORROGA = 'PRORROGA', 'Prórroga'
		INSCRIPCIONES_CERRADAS = 'INSCRIPCIONES_CERRADAS', 'Inscripciones cerradas'
		PREPARADO = 'PREPARADO', 'Preparado'
		EN_CURSO = 'EN_CURSO', 'En curso'
		FINALIZADO = 'FINALIZADO', 'Finalizado'
		CANCELADO = 'CANCELADO', 'Cancelado'

	class ModoXP(models.TextChoices):
		AUTOMATICA = 'AUTOMATICA', 'Automática'
		ESPECIAL = 'ESPECIAL', 'Especial'

	nombre = models.CharField(max_length=200)
	videojuego = models.ForeignKey(
		Videojuego,
		on_delete=models.PROTECT,
		related_name='torneos',
	)
	organizador = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='torneos_organizados',
	)
	tipo = models.CharField(max_length=10, choices=Tipo.choices)
	formato_competitivo = models.ForeignKey(
		FormatoCompetitivo,
		on_delete=models.PROTECT,
		related_name='torneos',
	)
	tipo_participante = models.CharField(
		max_length=10,
		choices=TipoParticipante.choices,
		default=TipoParticipante.INDIVIDUAL,
	)
	tamano_equipo = models.PositiveIntegerField(
		default=1,
		validators=[MinValueValidator(1)],
	)
	max_participantes = models.PositiveIntegerField(validators=[MinValueValidator(1)])
	imagen_banner = models.ImageField(upload_to='torneos/', blank=True, null=True)
	reglas = models.TextField(blank=True)
	codigo_acceso = models.CharField(max_length=100, blank=True)
	nivel_minimo = models.PositiveIntegerField(blank=True, null=True, validators=[MinValueValidator(1)])
	rango_minimo = models.ForeignKey(
		RangoVideojuego,
		on_delete=models.SET_NULL,
		related_name='torneos_como_minimo',
		blank=True,
		null=True,
	)
	rango_maximo = models.ForeignKey(
		RangoVideojuego,
		on_delete=models.SET_NULL,
		related_name='torneos_como_maximo',
		blank=True,
		null=True,
	)
	fecha_apertura_inscripciones = models.DateTimeField(blank=True, null=True)
	fecha_cierre_inscripciones = models.DateTimeField(blank=True, null=True)
	fecha_publicacion = models.DateTimeField(blank=True, null=True)
	duracion_prorroga_min = models.PositiveIntegerField(blank=True, null=True)
	duracion_checkin_min = models.PositiveIntegerField(default=10, validators=[MinValueValidator(1)])
	descanso_entre_partidas_min = models.PositiveIntegerField(blank=True, null=True)
	fecha_inicio_prevista = models.DateTimeField(blank=True, null=True)
	fecha_inicio_real = models.DateTimeField(blank=True, null=True)
	fecha_fin_prevista = models.DateTimeField(blank=True, null=True)
	fecha_fin_real = models.DateTimeField(blank=True, null=True)
	estado = models.CharField(
		max_length=25,
		choices=Estado.choices,
		default=Estado.BORRADOR,
	)
	motivo_cancelacion = models.TextField(blank=True)
	fecha_creacion = models.DateTimeField(auto_now_add=True)
	fecha_actualizacion = models.DateTimeField(auto_now=True)
	coeficiente_dificultad = models.DecimalField(max_digits=6, decimal_places=3, blank=True, null=True)
	modo_xp = models.CharField(
		max_length=10,
		choices=ModoXP.choices,
		default=ModoXP.AUTOMATICA,
	)
	multiplicador_xp_especial = models.DecimalField(
		max_digits=6,
		decimal_places=2,
		blank=True,
		null=True,
	)

	class Meta:
		ordering = ('-fecha_creacion',)

	def __str__(self):
		return self.nombre

	def clean(self):
		super().clean()
		errores = {}

		if self.formato_competitivo_id and self.max_participantes:
			if not self.formato_competitivo.es_tamano_valido(self.max_participantes):
				errores['max_participantes'] = (
					'El número máximo no es válido para el formato competitivo seleccionado.'
				)

		if self.tipo_participante != self.TipoParticipante.INDIVIDUAL or self.tamano_equipo != 1:
			errores['tipo_participante'] = 'El MVP solo permite participantes individuales.'

		if self.videojuego_id and not self.videojuego.activo and not self.pk:
			errores['videojuego'] = 'No se puede crear un torneo para un videojuego inactivo.'

		if self.rango_minimo_id and self.rango_minimo.videojuego_id != self.videojuego_id:
			errores['rango_minimo'] = 'El rango mínimo debe pertenecer al videojuego del torneo.'
		if self.rango_maximo_id and self.rango_maximo.videojuego_id != self.videojuego_id:
			errores['rango_maximo'] = 'El rango máximo debe pertenecer al videojuego del torneo.'
		if self.rango_minimo_id and self.rango_maximo_id:
			if self.rango_minimo.posicion > self.rango_maximo.posicion:
				errores['rango_minimo'] = 'El rango mínimo no puede ser superior al máximo.'

		if (
			self.fecha_apertura_inscripciones
			and self.fecha_cierre_inscripciones
			and self.fecha_apertura_inscripciones > self.fecha_cierre_inscripciones
		):
			errores['fecha_cierre_inscripciones'] = 'El cierre no puede ser anterior a la apertura.'

		if self.tipo == self.Tipo.PUBLICO:
			if self.modo_xp == self.ModoXP.ESPECIAL:
				errores['modo_xp'] = 'Los torneos públicos no pueden utilizar XP especial.'
			if (
				self.estado != self.Estado.BORRADOR
				and (not self.fecha_apertura_inscripciones or not self.fecha_cierre_inscripciones)
			):
				errores['fecha_apertura_inscripciones'] = (
					'Los torneos públicos requieren apertura y cierre de inscripciones.'
				)
			elif self.fecha_apertura_inscripciones and self.fecha_cierre_inscripciones:
				duracion = self.fecha_cierre_inscripciones - self.fecha_apertura_inscripciones
				if duracion.total_seconds() < 15 * 60 or duracion.total_seconds() > 60 * 60:
					errores['fecha_cierre_inscripciones'] = (
						'La inscripción pública debe durar entre 15 y 60 minutos.'
					)
			if self.duracion_prorroga_min is not None and not 10 <= self.duracion_prorroga_min <= 30:
				errores['duracion_prorroga_min'] = 'La prórroga pública debe durar entre 10 y 30 minutos.'
			if self.descanso_entre_partidas_min is not None and not 5 <= self.descanso_entre_partidas_min <= 10:
				errores['descanso_entre_partidas_min'] = 'El descanso público debe estar entre 5 y 10 minutos.'

		if self.modo_xp == self.ModoXP.ESPECIAL and self.tipo != self.Tipo.OFICIAL:
			errores['modo_xp'] = 'El XP especial solo está permitido en torneos oficiales.'
		if self.tipo == self.Tipo.OFICIAL and self.modo_xp == self.ModoXP.ESPECIAL:
			if self.multiplicador_xp_especial is None:
				errores['multiplicador_xp_especial'] = 'El XP especial oficial requiere un multiplicador.'

		if self.tipo == self.Tipo.PUBLICO and self.estado != self.Estado.BORRADOR and self.fecha_apertura_inscripciones:
			if self.fecha_apertura_inscripciones > timezone.now() + timedelta(hours=24):
				errores['fecha_apertura_inscripciones'] = (
					'Las inscripciones públicas no pueden programarse con más de 24 horas de antelación.'
				)

		if self.fecha_inicio_prevista and self.fecha_cierre_inscripciones:
			cierre_referencia = self.fecha_cierre_inscripciones
			if self.tipo == self.Tipo.PUBLICO and self.duracion_prorroga_min:
				cierre_referencia += timedelta(minutes=self.duracion_prorroga_min)
			if self.fecha_inicio_prevista < cierre_referencia:
				errores['fecha_inicio_prevista'] = (
					'El inicio previsto no puede ser anterior al cierre de inscripciones'
					+ (' y la prórroga.' if self.tipo == self.Tipo.PUBLICO else '.')
				)
		if errores:
			raise ValidationError(errores)

	@property
	def participantes_confirmados(self):
		return self.inscripciones.filter(estado=InscripcionTorneo.Estado.CONFIRMADA).count()

	@property
	def plazas_disponibles(self):
		return max(self.max_participantes - self.participantes_confirmados, 0)


class PremioTorneo(models.Model):
	torneo = models.ForeignKey(Torneo, on_delete=models.CASCADE, related_name='premios')
	descripcion = models.TextField()
	imagen = models.ImageField(upload_to='premios/', blank=True, null=True)

	def __str__(self):
		return f'Premio de {self.torneo}'

	def clean(self):
		super().clean()
		if self.torneo_id and self.torneo.tipo != Torneo.Tipo.OFICIAL:
			raise ValidationError({'torneo': 'Los premios del MVP están reservados a torneos oficiales.'})


class ClasificacionTorneo(models.Model):
	torneo = models.ForeignKey(Torneo, on_delete=models.CASCADE, related_name='clasificaciones')
	inscripcion = models.ForeignKey(
		'InscripcionTorneo',
		on_delete=models.PROTECT,
		related_name='clasificaciones_finales',
	)
	posicion = models.PositiveIntegerField(validators=[MinValueValidator(1)])
	ronda_eliminado = models.PositiveIntegerField(blank=True, null=True, validators=[MinValueValidator(1)])
	es_campeon = models.BooleanField(default=False)
	fecha_generacion = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ('posicion', 'inscripcion__nick_historico')
		constraints = [
			models.UniqueConstraint(
				fields=('torneo', 'inscripcion'),
				name='unique_clasificacion_torneo_inscripcion',
			),
		]

	def clean(self):
		super().clean()
		if self.torneo_id and self.inscripcion_id and self.inscripcion.torneo_id != self.torneo_id:
			raise ValidationError({'inscripcion': 'La inscripción debe pertenecer al torneo.'})
		if self.es_campeon and self.ronda_eliminado is not None:
			raise ValidationError({'ronda_eliminado': 'El campeón no puede tener ronda de eliminación.'})

	def __str__(self):
		return f'{self.torneo} · {self.inscripcion.nick_historico} · #{self.posicion}'


class HistorialEstadoTorneo(models.Model):
	torneo = models.ForeignKey(Torneo, on_delete=models.CASCADE, related_name='historial_estados')
	estado_anterior = models.CharField(max_length=25, choices=Torneo.Estado.choices, blank=True)
	estado_nuevo = models.CharField(max_length=25, choices=Torneo.Estado.choices)
	actor = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		related_name='cambios_estado_torneo',
		blank=True,
		null=True,
	)
	motivo = models.TextField(blank=True)
	fecha = models.DateTimeField(default=timezone.now)

	class Meta:
		ordering = ('-fecha',)

	def __str__(self):
		return f'{self.torneo}: {self.estado_nuevo}'


class InscripcionTorneo(models.Model):
	class Estado(models.TextChoices):
		CONFIRMADA = 'CONFIRMADA', 'Confirmada'
		CANCELADA = 'CANCELADA', 'Cancelada'
		DESCALIFICADA = 'DESCALIFICADA', 'Descalificada'

	torneo = models.ForeignKey(Torneo, on_delete=models.CASCADE, related_name='inscripciones')
	usuario = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='inscripciones_torneo',
	)
	perfil_videojuego = models.ForeignKey(
		'videojuegos.PerfilVideojuegoUsuario',
		on_delete=models.PROTECT,
		related_name='inscripciones',
	)
	nick_historico = models.CharField(max_length=150)
	rango_declarado_al_inscribirse = models.ForeignKey(
		RangoVideojuego,
		on_delete=models.SET_NULL,
		related_name='inscripciones_historicas',
		blank=True,
		null=True,
	)
	estado = models.CharField(
		max_length=15,
		choices=Estado.choices,
		default=Estado.CONFIRMADA,
	)
	fecha_inscripcion = models.DateTimeField(auto_now_add=True)
	fecha_confirmacion = models.DateTimeField(default=timezone.now)
	fecha_cancelacion = models.DateTimeField(blank=True, null=True)
	motivo_cancelacion = models.TextField(blank=True)
	fecha_descalificacion = models.DateTimeField(blank=True, null=True)
	motivo_descalificacion = models.TextField(blank=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=('torneo', 'usuario'),
				name='unique_inscripcion_torneo_usuario',
			),
		]
		ordering = ('fecha_inscripcion',)

	def __str__(self):
		return f'{self.nick_historico} - {self.torneo}'

	def clean(self):
		super().clean()
		errores = {}
		if self.perfil_videojuego_id and self.usuario_id:
			if self.perfil_videojuego.usuario_id != self.usuario_id:
				errores['perfil_videojuego'] = 'El perfil no pertenece al usuario inscrito.'
		if self.perfil_videojuego_id and self.torneo_id:
			if self.perfil_videojuego.videojuego_id != self.torneo.videojuego_id:
				errores['perfil_videojuego'] = 'El perfil no corresponde al videojuego del torneo.'
		if self.rango_declarado_al_inscribirse_id and self.torneo_id:
			if self.rango_declarado_al_inscribirse.videojuego_id != self.torneo.videojuego_id:
				errores['rango_declarado_al_inscribirse'] = (
					'El rango histórico no corresponde al videojuego del torneo.'
				)
		if errores:
			raise ValidationError(errores)
