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
