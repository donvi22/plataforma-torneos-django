from django.contrib import admin

from .models import PerfilVideojuegoUsuario, Plataforma, RangoVideojuego, Videojuego


class CatalogoAdminMixin:
	def has_add_permission(self, request):
		return request.user.is_superuser


@admin.register(Plataforma)
class PlataformaAdmin(CatalogoAdminMixin, admin.ModelAdmin):
	search_fields = ('nombre',)
	ordering = ('nombre',)


@admin.register(RangoVideojuego)
class RangoVideojuegoAdmin(CatalogoAdminMixin, admin.ModelAdmin):
	list_display = ('nombre', 'videojuego', 'posicion')
	list_filter = ('videojuego',)
	search_fields = ('nombre', 'videojuego__nombre')
	ordering = ('videojuego', 'posicion')


@admin.register(Videojuego)
class VideojuegoAdmin(CatalogoAdminMixin, admin.ModelAdmin):
	list_display = ('nombre', 'genero', 'activo', 'fecha_alta')
	list_filter = ('activo', 'genero')
	search_fields = ('nombre',)
	filter_horizontal = ('plataformas',)
	ordering = ('nombre',)


@admin.register(PerfilVideojuegoUsuario)
class PerfilVideojuegoUsuarioAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'videojuego', 'nick_en_juego', 'rango_declarado')
	list_filter = ('videojuego', 'rango_declarado')
	search_fields = ('usuario__username', 'nick_en_juego', 'videojuego__nombre')
	autocomplete_fields = ('usuario', 'videojuego', 'rango_declarado')
