from django.contrib import admin

from .models import Notificacion, PreferenciasNotificacion, SeguimientoTorneo


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
	list_display = ('destinatario', 'tipo', 'titulo', 'leida', 'es_critica', 'fecha_creacion')
	list_filter = ('tipo', 'leida', 'es_critica', 'fecha_creacion')
	search_fields = ('destinatario__username', 'titulo', 'mensaje', 'clave_evento')
	readonly_fields = [field.name for field in Notificacion._meta.fields]

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(SeguimientoTorneo)
class SeguimientoTorneoAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'torneo', 'fecha_seguimiento')
	list_filter = ('torneo',)
	search_fields = ('usuario__username', 'torneo__nombre')
	readonly_fields = [field.name for field in SeguimientoTorneo._meta.fields]


@admin.register(PreferenciasNotificacion)
class PreferenciasNotificacionAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'avisar_apertura_inscripciones', 'avisar_novedades_torneos', 'avisar_resultados_torneos')
	search_fields = ('usuario__username',)
