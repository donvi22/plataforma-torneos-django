from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class UsuarioManager(UserManager):
	def create_superuser(self, username, email=None, password=None, **extra_fields):
		user = super().create_superuser(
			username,
			email=email,
			password=password,
			**extra_fields,
		)
		if user.rol_global != Usuario.RolGlobal.ADMIN:
			user.rol_global = Usuario.RolGlobal.ADMIN
			user.save(update_fields=['rol_global'])
		return user


class Usuario(AbstractUser):
	class RolGlobal(models.TextChoices):
		PLAYER = 'PLAYER', 'Jugador'
		ADMIN = 'ADMIN', 'Administrador'

	class EstadoCuenta(models.TextChoices):
		ACTIVA = 'ACTIVA', 'Activa'
		SUSPENDIDA = 'SUSPENDIDA', 'Suspendida'
		BLOQUEADA = 'BLOQUEADA', 'Bloqueada'
		ELIMINADA = 'ELIMINADA', 'Eliminada'

	username = models.CharField(max_length=150, unique=True)
	email = models.EmailField(unique=True)
	first_name = None
	last_name = None
	avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
	xp_total = models.PositiveIntegerField(default=0)
	nivel = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
	karma_total = models.PositiveIntegerField(default=100)
	rol_global = models.CharField(
		max_length=10,
		choices=RolGlobal.choices,
		default=RolGlobal.PLAYER,
	)
	estado_cuenta = models.CharField(
		max_length=10,
		choices=EstadoCuenta.choices,
		default=EstadoCuenta.ACTIVA,
	)
	disponible_para_arbitrar = models.BooleanField(default=False)
	fecha_ultimo_cambio_nick = models.DateTimeField(blank=True, null=True)
	fecha_eliminacion = models.DateTimeField(blank=True, null=True)
	mostrar_ultima_conexion = models.BooleanField(default=False)
	ultima_actividad = models.DateTimeField(blank=True, null=True)
	mostrar_estado_online = models.BooleanField(default=True)

	objects = UsuarioManager()

	@property
	def puede_operar(self):
		return self.is_active and self.estado_cuenta == self.EstadoCuenta.ACTIVA

	def __str__(self):
		return self.username


class HistorialXP(models.Model):
	usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name='historial_xp')
	torneo = models.ForeignKey('torneos.Torneo', on_delete=models.PROTECT, related_name='historial_xp')
	inscripcion = models.ForeignKey('torneos.InscripcionTorneo', on_delete=models.PROTECT, related_name='historial_xp')
	clasificacion = models.ForeignKey('torneos.ClasificacionTorneo', on_delete=models.PROTECT, related_name='historial_xp')
	xp_concedida = models.PositiveIntegerField()
	xp_anterior = models.PositiveIntegerField()
	xp_posterior = models.PositiveIntegerField()
	nivel_anterior = models.PositiveIntegerField(validators=[MinValueValidator(1)])
	nivel_posterior = models.PositiveIntegerField(validators=[MinValueValidator(1)])
	posicion_final = models.PositiveIntegerField(validators=[MinValueValidator(1)])
	tamano_torneo = models.PositiveIntegerField(validators=[MinValueValidator(2)])
	coeficiente_dificultad = models.DecimalField(max_digits=6, decimal_places=3)
	multiplicador_especial = models.DecimalField(max_digits=6, decimal_places=2)
	motivo = models.CharField(max_length=200)
	fecha = models.DateTimeField(auto_now_add=True)
    
	class Meta:
		ordering = ('-fecha', '-pk')
		constraints = [
			models.UniqueConstraint(fields=('inscripcion',), name='unique_recompensa_xp_por_inscripcion'),
		]
    
	def clean(self):
		super().clean()
		if self.inscripcion_id and self.usuario_id and self.inscripcion.usuario_id != self.usuario_id:
			raise ValidationError({'usuario': 'El usuario debe coincidir con la inscripción.'})
		if self.inscripcion_id and self.torneo_id and self.inscripcion.torneo_id != self.torneo_id:
			raise ValidationError({'inscripcion': 'La inscripción debe pertenecer al torneo.'})
		if self.clasificacion_id and self.inscripcion_id and self.clasificacion.inscripcion_id != self.inscripcion_id:
			raise ValidationError({'clasificacion': 'La clasificación debe justificar la inscripción.'})
    
	def save(self, *args, **kwargs):
		if self.pk:
			raise ValidationError('Un movimiento de XP histórico no puede modificarse.')
		super().save(*args, **kwargs)
    
	def delete(self, *args, **kwargs):
		raise ValidationError('Un movimiento de XP histórico no puede eliminarse.')


class HistorialKarma(models.Model):
	class Tipo(models.TextChoices):
		PARTICIPACION_COMPLETADA = 'PARTICIPACION_COMPLETADA', 'Participación completada'
		DECLARACION_VERAZ = 'DECLARACION_VERAZ', 'Declaración veraz'
		ARBITRAJE_COMPLETADO = 'ARBITRAJE_COMPLETADO', 'Arbitraje completado'
		ORGANIZACION_COMPLETADA = 'ORGANIZACION_COMPLETADA', 'Organización completada'
		AJUSTE_ADMINISTRATIVO = 'AJUSTE_ADMINISTRATIVO', 'Ajuste administrativo'
		SANCION_CONFIRMADA = 'SANCION_CONFIRMADA', 'Sanción confirmada'

	usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name='historial_karma')
	cantidad = models.IntegerField()
	cantidad_solicitada = models.IntegerField()
	karma_antes = models.PositiveIntegerField(validators=[MinValueValidator(0)])
	karma_despues = models.PositiveIntegerField(validators=[MinValueValidator(0)])
	tipo = models.CharField(max_length=32, choices=Tipo.choices)
	motivo = models.CharField(max_length=300)
	fecha = models.DateTimeField(auto_now_add=True)
	torneo = models.ForeignKey(
		'torneos.Torneo', on_delete=models.PROTECT, related_name='historial_karma', blank=True, null=True,
	)
	partida = models.ForeignKey(
		'partidas.Partida', on_delete=models.PROTECT, related_name='historial_karma', blank=True, null=True,
	)
	inscripcion = models.ForeignKey(
		'torneos.InscripcionTorneo', on_delete=models.PROTECT, related_name='historial_karma', blank=True, null=True,
	)
	arbitraje = models.ForeignKey(
		'arbitraje.ArbitroTorneo', on_delete=models.PROTECT, related_name='historial_karma', blank=True, null=True,
	)
	historial_asignacion_arbitral = models.ForeignKey(
		'arbitraje.HistorialAsignacionArbitro', on_delete=models.PROTECT,
		related_name='historial_karma', blank=True, null=True,
	)
	autorizado_por = models.ForeignKey(
		Usuario, on_delete=models.PROTECT, related_name='ajustes_karma_autorizados', blank=True, null=True,
	)
	clave_idempotencia = models.CharField(max_length=200, unique=True)

	class Meta:
		ordering = ('-fecha', '-pk')

	def clean(self):
		super().clean()
		errores = {}
		if self.cantidad_solicitada == 0:
			errores['cantidad_solicitada'] = 'El cambio solicitado no puede ser cero.'
		if self.karma_antes is not None and self.karma_despues is not None and self.cantidad is not None:
			if self.karma_despues != max(0, self.karma_antes + self.cantidad):
				errores['karma_despues'] = 'El saldo posterior no coincide con el cambio aplicado.'
			if self.cantidad_solicitada and self.cantidad_solicitada > 0 and self.cantidad != self.cantidad_solicitada:
				errores['cantidad'] = 'Un cambio positivo debe aplicarse íntegramente.'
			if self.cantidad_solicitada and self.cantidad_solicitada < 0:
				if not self.cantidad_solicitada <= self.cantidad <= 0:
					errores['cantidad'] = 'El cambio aplicado supera la sanción solicitada.'
		if not self.motivo or not self.motivo.strip():
			errores['motivo'] = 'El motivo es obligatorio.'
		if self.tipo in (self.Tipo.AJUSTE_ADMINISTRATIVO, self.Tipo.SANCION_CONFIRMADA):
			if not self.autorizado_por_id:
				errores['autorizado_por'] = 'El movimiento requiere autorización administrativa.'
		elif self.autorizado_por_id:
			errores['autorizado_por'] = 'Solo los ajustes administrativos tienen autorizador.'
		if self.tipo == self.Tipo.SANCION_CONFIRMADA and self.cantidad_solicitada >= 0:
			errores['cantidad_solicitada'] = 'Una sanción confirmada debe ser negativa.'
		if self.inscripcion_id and self.usuario_id and self.inscripcion.usuario_id != self.usuario_id:
			errores['inscripcion'] = 'La inscripción debe pertenecer al usuario.'
		if self.inscripcion_id and self.torneo_id and self.inscripcion.torneo_id != self.torneo_id:
			errores['inscripcion'] = 'La inscripción debe pertenecer al torneo.'
		if self.partida_id and self.torneo_id and self.partida.torneo_id != self.torneo_id:
			errores['partida'] = 'La partida debe pertenecer al torneo.'
		if self.arbitraje_id and self.torneo_id and self.arbitraje.torneo_id != self.torneo_id:
			errores['arbitraje'] = 'La actuación arbitral debe pertenecer al torneo.'
		if self.historial_asignacion_arbitral_id and self.partida_id:
			if self.historial_asignacion_arbitral.partida_id != self.partida_id:
				errores['historial_asignacion_arbitral'] = 'La asignación debe pertenecer a la partida.'
		if errores:
			raise ValidationError(errores)

	def save(self, *args, **kwargs):
		if self.pk:
			raise ValidationError('Un movimiento histórico de Karma no puede modificarse.')
		super().save(*args, **kwargs)

	def delete(self, *args, **kwargs):
		raise ValidationError('Un movimiento histórico de Karma no puede eliminarse.')
