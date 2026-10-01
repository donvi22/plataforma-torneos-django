from django.contrib import admin

from .models import (
	ClasificacionTorneo,
	FormatoCompetitivo,
	HistorialEstadoTorneo,
	InscripcionTorneo,
	PremioTorneo,
	Torneo,
)


class SuperuserOnlyAdminMixin:
	def has_add_permission(self, request):
		return request.user.is_superuser

	def has_change_permission(self, request, obj=None):
		return request.user.is_superuser

	def has_delete_permission(self, request, obj=None):
		return request.user.is_superuser


@admin.register(FormatoCompetitivo)
class FormatoCompetitivoAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
	list_display = ('nombre', 'tipo_regla_participantes', 'min_participantes', 'max_participantes')
	list_filter = ('tipo_regla_participantes',)
	search_fields = ('nombre',)


@admin.register(Torneo)
class TorneoAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
	list_display = ('nombre', 'videojuego', 'organizador', 'tipo', 'estado', 'max_participantes')
	list_filter = ('videojuego', 'tipo', 'estado', 'organizador')
	search_fields = ('nombre', 'videojuego__nombre', 'organizador__username')
	readonly_fields = ('fecha_creacion', 'fecha_actualizacion')
	filter_horizontal = ()


@admin.register(PremioTorneo)
class PremioTorneoAdmin(admin.ModelAdmin):
	list_display = ('torneo', 'descripcion')
	list_filter = ('torneo__tipo',)
	search_fields = ('torneo__nombre', 'descripcion')


@admin.register(HistorialEstadoTorneo)
class HistorialEstadoTorneoAdmin(admin.ModelAdmin):
	list_display = ('torneo', 'estado_anterior', 'estado_nuevo', 'actor', 'fecha')
	list_filter = ('estado_nuevo', 'torneo__tipo')
	search_fields = ('torneo__nombre', 'actor__username', 'motivo')
	readonly_fields = ('fecha',)


@admin.register(ClasificacionTorneo)
class ClasificacionTorneoAdmin(admin.ModelAdmin):
	list_display = ('torneo', 'inscripcion', 'posicion', 'ronda_eliminado', 'es_campeon', 'fecha_generacion')
	list_filter = ('torneo', 'es_campeon', 'posicion')
	search_fields = ('torneo__nombre', 'inscripcion__nick_historico')
	readonly_fields = [field.name for field in ClasificacionTorneo._meta.fields]

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(InscripcionTorneo)
class InscripcionTorneoAdmin(admin.ModelAdmin):
	list_display = (
		'torneo',
		'usuario',
		'nick_historico',
		'rango_declarado_al_inscribirse',
		'estado',
		'fecha_inscripcion',
		'fecha_confirmacion',
		'fecha_cancelacion',
	)
	list_filter = ('torneo', 'usuario', 'estado')
	search_fields = ('torneo__nombre', 'usuario__username', 'nick_historico')
	readonly_fields = (
		'nick_historico',
		'rango_declarado_al_inscribirse',
		'fecha_inscripcion',
		'fecha_confirmacion',
		'fecha_cancelacion',
		'fecha_descalificacion',
	)

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False
