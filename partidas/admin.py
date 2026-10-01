from django.contrib import admin

from .models import (
	CheckInPartida,
	DeclaracionResultado,
	HistorialResultadoPartida,
	HistorialProgramacionPartida,
	ParticipantePartida,
	Partida,
	ResultadoPartida,
)


@admin.register(Partida)
class PartidaAdmin(admin.ModelAdmin):
	list_display = ('torneo', 'numero_ronda', 'numero_orden', 'estado', 'siguiente_partida')
	list_filter = ('torneo', 'numero_ronda', 'estado')
	search_fields = ('torneo__nombre', 'codigo_lobby')
	readonly_fields = (
		'torneo',
		'arbitro_asignado',
		'numero_ronda',
		'numero_orden',
		'siguiente_partida',
		'posicion_en_siguiente_partida',
	)

	def has_add_permission(self, request):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(ParticipantePartida)
class ParticipantePartidaAdmin(admin.ModelAdmin):
	list_display = ('partida', 'inscripcion', 'posicion')
	list_filter = ('partida__torneo', 'partida__numero_ronda')
	search_fields = ('partida__torneo__nombre', 'inscripcion__nick_historico')
	readonly_fields = ('partida', 'inscripcion', 'posicion')

	def has_add_permission(self, request):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(DeclaracionResultado)
class DeclaracionResultadoAdmin(admin.ModelAdmin):
	list_display = ('partida', 'usuario', 'resultado_declarado', 'ganador_declarado', 'fecha')
	list_filter = ('partida__torneo',)
	search_fields = ('partida__torneo__nombre', 'usuario__username')
	readonly_fields = ('partida', 'usuario', 'resultado_declarado', 'ganador_declarado', 'fecha')

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(ResultadoPartida)
class ResultadoPartidaAdmin(admin.ModelAdmin):
	list_display = ('partida', 'tipo_resultado', 'ganador', 'validado_por', 'fecha_validacion')
	list_filter = ('tipo_resultado', 'partida__torneo')
	search_fields = ('partida__torneo__nombre', 'ganador__nick_historico')
	readonly_fields = (
		'partida',
		'resultado',
		'ganador',
		'tipo_resultado',
		'validado_por',
		'fecha_validacion',
		'motivo',
	)

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(HistorialResultadoPartida)
class HistorialResultadoPartidaAdmin(admin.ModelAdmin):
	list_display = ('partida', 'tipo_resultado_anterior', 'tipo_resultado_nuevo', 'usuario_responsable', 'fecha')
	list_filter = ('tipo_resultado_nuevo', 'partida__torneo')
	search_fields = ('partida__torneo__nombre', 'motivo')
	readonly_fields = [field.name for field in HistorialResultadoPartida._meta.fields]

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(CheckInPartida)
class CheckInPartidaAdmin(admin.ModelAdmin):
	list_display = ('partida', 'usuario', 'tipo', 'confirmado', 'fecha_confirmacion')
	list_filter = ('partida__torneo', 'tipo', 'confirmado')
	search_fields = ('partida__torneo__nombre', 'usuario__username')
	readonly_fields = ('partida', 'usuario', 'tipo', 'confirmado', 'fecha_confirmacion')

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(HistorialProgramacionPartida)
class HistorialProgramacionPartidaAdmin(admin.ModelAdmin):
	list_display = ('partida', 'fecha_anterior', 'fecha_nueva', 'actor', 'fecha')
	list_filter = ('partida__torneo',)
	search_fields = ('partida__torneo__nombre', 'motivo')
	readonly_fields = [field.name for field in HistorialProgramacionPartida._meta.fields]

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False
