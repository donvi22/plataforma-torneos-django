from django.contrib import admin

from .models import ArbitroTorneo, HistorialAsignacionArbitro


@admin.register(ArbitroTorneo)
class ArbitroTorneoAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'torneo', 'estado_invitacion', 'activo_en_torneo', 'fecha_invitacion', 'fecha_salida')
	list_filter = ('torneo', 'estado_invitacion', 'activo_en_torneo')
	search_fields = ('usuario__username', 'torneo__nombre')
	readonly_fields = ('fecha_invitacion', 'fecha_respuesta', 'fecha_salida')

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(HistorialAsignacionArbitro)
class HistorialAsignacionArbitroAdmin(admin.ModelAdmin):
	list_display = ('partida', 'arbitro_anterior', 'arbitro_nuevo', 'actor', 'fecha')
	list_filter = ('partida__torneo',)
	search_fields = ('partida__torneo__nombre', 'motivo')
	readonly_fields = [field.name for field in HistorialAsignacionArbitro._meta.fields]

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False
