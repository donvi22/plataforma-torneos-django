from django.contrib import admin

from torneos.services import _es_administrador_autorizado

from .models import Denuncia, HistorialDenuncia


@admin.register(Denuncia)
class DenunciaAdmin(admin.ModelAdmin):
	list_display = ('id', 'categoria', 'estado', 'denunciante', 'usuario_denunciado', 'torneo', 'fecha_creacion')
	list_filter = ('estado', 'categoria', 'torneo')
	search_fields = ('denunciante__username', 'usuario_denunciado__username', 'torneo__nombre', 'descripcion')
	date_hierarchy = 'fecha_creacion'
	readonly_fields = [field.name for field in Denuncia._meta.fields]

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False

	def has_view_permission(self, request, obj=None):
		return _es_administrador_autorizado(request.user)


@admin.register(HistorialDenuncia)
class HistorialDenunciaAdmin(admin.ModelAdmin):
	list_display = ('denuncia', 'accion', 'estado_anterior', 'estado_nuevo', 'responsable', 'fecha')
	list_filter = ('accion', 'fecha')
	search_fields = ('denuncia__id', 'denuncia__denunciante__username', 'comentario', 'responsable__username')
	date_hierarchy = 'fecha'
	readonly_fields = [field.name for field in HistorialDenuncia._meta.fields]

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False

	def has_view_permission(self, request, obj=None):
		return _es_administrador_autorizado(request.user)