from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Plataforma(models.Model):
	nombre = models.CharField(max_length=100, unique=True)

	class Meta:
		ordering = ('nombre',)

	def __str__(self):
		return self.nombre


class Videojuego(models.Model):
	nombre = models.CharField(max_length=150, unique=True)
	genero = models.CharField(max_length=100)
	imagen = models.ImageField(upload_to='videojuegos/', blank=True, null=True)
	activo = models.BooleanField(default=True)
	fecha_alta = models.DateTimeField(auto_now_add=True)
	instrucciones_competicion = models.TextField(blank=True)
	plataformas = models.ManyToManyField(Plataforma, related_name='videojuegos', blank=True)

	class Meta:
		ordering = ('nombre',)

	def __str__(self):
		return self.nombre


class RangoVideojuego(models.Model):
	videojuego = models.ForeignKey(
		Videojuego,
		on_delete=models.CASCADE,
		related_name='rangos',
	)
	nombre = models.CharField(max_length=100)
	posicion = models.PositiveIntegerField(validators=[MinValueValidator(1)])

	class Meta:
		ordering = ('videojuego', 'posicion')
		constraints = [
			models.UniqueConstraint(
				fields=('videojuego', 'nombre'),
				name='unique_rango_nombre_por_videojuego',
			),
			models.UniqueConstraint(
				fields=('videojuego', 'posicion'),
				name='unique_rango_posicion_por_videojuego',
			),
		]

	def __str__(self):
		return f'{self.videojuego}: {self.nombre}'

	@property
	def posicion_normalizada(self):
		total_rangos = self.videojuego.rangos.count()
		if total_rangos <= 1:
			return 0.0
		return (self.posicion - 1) / (total_rangos - 1)


class PerfilVideojuegoUsuario(models.Model):
	usuario = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name='perfiles_videojuego',
	)
	videojuego = models.ForeignKey(
		Videojuego,
		on_delete=models.CASCADE,
		related_name='perfiles_usuario',
	)
	nick_en_juego = models.CharField(max_length=150)
	rango_declarado = models.ForeignKey(
		RangoVideojuego,
		on_delete=models.SET_NULL,
		related_name='perfiles_usuario',
		blank=True,
		null=True,
	)
	fecha_actualizacion_rango = models.DateTimeField(blank=True, null=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=('usuario', 'videojuego'),
				name='unique_perfil_usuario_videojuego',
			),
		]

	def __str__(self):
		return f'{self.usuario} - {self.videojuego}'

	def clean(self):
		super().clean()
		if (
			self.rango_declarado_id
			and self.videojuego_id
			and self.rango_declarado.videojuego_id != self.videojuego_id
		):
			raise ValidationError({
				'rango_declarado': 'El rango debe pertenecer al videojuego del perfil.',
			})

	def save(self, *args, **kwargs):
		if self.pk and self.rango_declarado_id != type(self).objects.get(pk=self.pk).rango_declarado_id:
			self.fecha_actualizacion_rango = timezone.now()
		super().save(*args, **kwargs)
