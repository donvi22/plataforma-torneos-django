from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .models import HistorialKarma, HistorialXP, Usuario


class UsuarioCreationForm(forms.ModelForm):
	password1 = forms.CharField(label='Contraseña', widget=forms.PasswordInput)
	password2 = forms.CharField(label='Confirmar contraseña', widget=forms.PasswordInput)

	class Meta:
		model = Usuario
		fields = ('username', 'email')

	def clean_password2(self):
		password1 = self.cleaned_data.get('password1')
		password2 = self.cleaned_data.get('password2')
		if password1 and password2 and password1 != password2:
			raise forms.ValidationError('Las contraseñas no coinciden.')
		return password2

	def save(self, commit=True):
		user = super().save(commit=False)
		user.set_password(self.cleaned_data['password1'])
		if commit:
			user.save()
		return user


class UsuarioChangeForm(forms.ModelForm):
	password = ReadOnlyPasswordHashField(label='Contraseña')

	class Meta:
		model = Usuario
		fields = '__all__'


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
	add_form = UsuarioCreationForm
	form = UsuarioChangeForm
	model = Usuario
	list_display = ('username', 'email', 'rol_global', 'estado_cuenta', 'is_staff', 'is_active')
	list_filter = ('rol_global', 'estado_cuenta', 'is_staff', 'is_active')
	search_fields = ('username', 'email')
	ordering = ('username',)
	readonly_fields = ('xp_total', 'nivel', 'karma_total')
	fieldsets = (
		(None, {'fields': ('username', 'password')}),
		('Datos de contacto', {'fields': ('email', 'avatar')}),
		('Progreso y reputación', {'fields': ('xp_total', 'nivel', 'karma_total')}),
		('Cuenta', {'fields': ('rol_global', 'estado_cuenta', 'fecha_ultimo_cambio_nick', 'fecha_eliminacion')}),
		('Actividad y preferencias', {
			'fields': (
				'disponible_para_arbitrar',
				'mostrar_ultima_conexion',
				'ultima_actividad',
				'mostrar_estado_online',
			),
		}),
		('Permisos', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
		('Fechas', {'fields': ('last_login', 'date_joined')}),
	)
	add_fieldsets = (
		(None, {
			'classes': ('wide',),
			'fields': ('username', 'email', 'password1', 'password2'),
		}),
	)

	def save_model(self, request, obj, form, change):
		previous = Usuario.objects.get(pk=obj.pk) if change else None
		if previous and previous.estado_cuenta == Usuario.EstadoCuenta.ELIMINADA:
			obj.username = previous.username
			obj.email = previous.email
			obj.avatar = previous.avatar
			obj.estado_cuenta = Usuario.EstadoCuenta.ELIMINADA
			obj.fecha_eliminacion = previous.fecha_eliminacion
			obj.is_active = False
			obj.is_staff = False
			obj.is_superuser = False
			obj.rol_global = Usuario.RolGlobal.PLAYER
			obj.set_unusable_password()
		if not request.user.is_superuser:
			if change:
				previous = previous or Usuario.objects.get(pk=obj.pk)
				obj.rol_global = previous.rol_global
				obj.is_staff = previous.is_staff
				obj.is_superuser = previous.is_superuser
			else:
				obj.rol_global = Usuario.RolGlobal.PLAYER
				obj.is_staff = False
				obj.is_superuser = False
		elif obj.is_superuser:
			obj.rol_global = Usuario.RolGlobal.ADMIN
		if request.user.is_superuser:
			obj.is_staff = obj.rol_global == Usuario.RolGlobal.ADMIN
		obj.save()

	def get_readonly_fields(self, request, obj=None):
		campos = list(super().get_readonly_fields(request, obj))
		if obj and obj.estado_cuenta == Usuario.EstadoCuenta.ELIMINADA:
			campos.extend(field.name for field in Usuario._meta.fields)
			campos.extend(('groups', 'user_permissions'))
		return tuple(dict.fromkeys(campos))

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(HistorialXP)
class HistorialXPAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'torneo', 'xp_concedida', 'xp_anterior', 'xp_posterior', 'nivel_posterior', 'fecha')
	list_filter = ('torneo', 'fecha')
	search_fields = ('usuario__username', 'torneo__nombre', 'inscripcion__nick_historico')
	readonly_fields = [field.name for field in HistorialXP._meta.fields]

	def has_add_permission(self, request):
		return False


@admin.register(HistorialKarma)
class HistorialKarmaAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'tipo', 'cantidad', 'karma_antes', 'karma_despues', 'fecha')
	list_filter = ('tipo', 'fecha')
	search_fields = ('usuario__username', 'motivo', 'torneo__nombre', 'partida__id')
	date_hierarchy = 'fecha'
	readonly_fields = [field.name for field in HistorialKarma._meta.fields]

	def has_add_permission(self, request):
		return False

	def has_delete_permission(self, request, obj=None):
		return False
