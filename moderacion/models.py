from django.conf import settings
from django.core.validators import MinLengthValidator
from django.db import models


class Denuncia(models.Model):
	class Categoria(models.TextChoices):
		RESULTADO_INCORRECTO = 'RESULTADO_INCORRECTO', 'Resultado incorrecto'
		INCOMPARECENCIA = 'INCOMPARECENCIA', 'Incomparecencia'
		CONDUCTA_INAPROPIADA = 'CONDUCTA_INAPROPIADA', 'Conducta inapropiada'
		TRAMPA = 'TRAMPA', 'Posible trampa'
		PROBLEMA_ARBITRAJE = 'PROBLEMA_ARBITRAJE', 'Problema de arbitraje'
		PROBLEMA_ORGANIZACION = 'PROBLEMA_ORGANIZACION', 'Problema de organización'
		OTRO = 'OTRO', 'Otro problema'

	class Estado(models.TextChoices):
		ABIERTA = 'ABIERTA', 'Abierta'
		EN_REVISION = 'EN_REVISION', 'En revisión'
		RESUELTA = 'RESUELTA', 'Resuelta'
		DESESTIMADA = 'DESESTIMADA', 'Desestimada'

	class TipoSancion(models.TextChoices):
		ADVERTENCIA = 'ADVERTENCIA', 'Advertencia'
		LEVE = 'LEVE', 'Leve'
		MEDIA = 'MEDIA', 'Media'
		GRAVE = 'GRAVE', 'Grave'

	denunciante = models.ForeignKey(
		settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='denuncias_creadas',
	)
	usuario_denunciado = models.ForeignKey(
		settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='denuncias_recibidas',
		blank=True, null=True,
	)
	torneo = models.ForeignKey(
		'torneos.Torneo', on_delete=models.PROTECT, related_name='denuncias', blank=True, null=True,
	)
	partida = models.ForeignKey(
		'partidas.Partida', on_delete=models.PROTECT, related_name='denuncias', blank=True, null=True,
	)
	categoria = models.CharField(max_length=32, choices=Categoria.choices)
	descripcion = models.TextField(validators=[MinLengthValidator(1)])
	estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.ABIERTA)
	fecha_creacion = models.DateTimeField(auto_now_add=True)
	fecha_actualizacion = models.DateTimeField(auto_now=True)
	asignada_a = models.ForeignKey(
		settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='denuncias_en_revision',
		blank=True, null=True,
	)
	resuelta_por = models.ForeignKey(
		settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='denuncias_resueltas',
		blank=True, null=True,
	)
	fecha_resolucion = models.DateTimeField(blank=True, null=True)
	resolucion = models.TextField(blank=True)
	notas_internas = models.TextField(blank=True)
	hubo_sancion = models.BooleanField(default=False)
	tipo_sancion = models.CharField(max_length=12, choices=TipoSancion.choices, blank=True)
	karma_solicitado = models.SmallIntegerField(default=0)
	historial_karma = models.OneToOneField(
		'usuarios.HistorialKarma', on_delete=models.PROTECT,
		related_name='denuncia_resuelta', blank=True, null=True,
	)

	class Meta:
		ordering = ('-fecha_creacion', '-pk')

	def __str__(self):
		return f'Denuncia #{self.pk} · {self.get_categoria_display()}'


class HistorialDenuncia(models.Model):
	class Accion(models.TextChoices):
		CREACION = 'CREACION', 'Creación'
		TOMA_REVISION = 'TOMA_REVISION', 'Tomada en revisión'
		RESOLUCION = 'RESOLUCION', 'Resolución'
		DESESTIMACION = 'DESESTIMACION', 'Desestimación'
		SANCION_APLICADA = 'SANCION_APLICADA', 'Sanción aplicada'

	denuncia = models.ForeignKey(Denuncia, on_delete=models.PROTECT, related_name='historial')
	accion = models.CharField(max_length=20, choices=Accion.choices)
	estado_anterior = models.CharField(max_length=12, choices=Denuncia.Estado.choices, blank=True)
	estado_nuevo = models.CharField(max_length=12, choices=Denuncia.Estado.choices)
	responsable = models.ForeignKey(
		settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='acciones_moderacion',
	)
	fecha = models.DateTimeField(auto_now_add=True)
	comentario = models.TextField(validators=[MinLengthValidator(1)])

	class Meta:
		ordering = ('fecha', 'pk')

	def save(self, *args, **kwargs):
		if self.pk:
			from django.core.exceptions import ValidationError
			raise ValidationError('El historial de moderación es inmutable.')
		super().save(*args, **kwargs)

	def delete(self, *args, **kwargs):
		from django.core.exceptions import ValidationError
		raise ValidationError('El historial de moderación es inmutable.')