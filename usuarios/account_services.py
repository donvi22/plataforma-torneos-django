"""Servicios de nickname y cierre irreversible/anónimo de cuenta."""

from datetime import timedelta
from math import ceil
from threading import RLock
from uuid import uuid4

from django.core.files.storage import default_storage
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import transaction
from django.utils import timezone

from notificaciones.models import Notificacion, PreferenciasNotificacion, SeguimientoTorneo

from .models import Usuario


DIAS_ENTRE_CAMBIOS_NICK = 30
_account_lock = RLock()


class AccountLifecycleError(Exception):
	"""Error al cambiar nickname o cerrar una cuenta."""


class NicknameCooldownError(AccountLifecycleError):
	def __init__(self, dias_restantes):
		self.dias_restantes = dias_restantes
		super().__init__(f'Podrás volver a cambiar el nickname dentro de {dias_restantes} días.')


def cambiar_nickname(usuario, nuevo_username, ahora=None):
	"""Cambia únicamente el username actual; los snapshots de inscripción no se editan."""
	ahora = ahora or timezone.now()
	nuevo_username = (nuevo_username or '').strip()
	if not nuevo_username:
		raise AccountLifecycleError('El nickname no puede estar vacío.')
	if len(nuevo_username) > 150:
		raise AccountLifecycleError('El nickname no puede superar los 150 caracteres.')
	try:
		UnicodeUsernameValidator()(nuevo_username)
	except Exception as error:
		raise AccountLifecycleError('El nickname contiene caracteres no permitidos.') from error

	with _account_lock, transaction.atomic():
		usuario = Usuario.objects.select_for_update().get(pk=usuario.pk)
		if not usuario.puede_operar:
			raise AccountLifecycleError('La cuenta no está activa.')
		if nuevo_username == usuario.username:
			return usuario, False
		if Usuario.objects.filter(username__iexact=nuevo_username).exclude(pk=usuario.pk).exists():
			raise AccountLifecycleError('Este nickname ya existe.')
		if usuario.fecha_ultimo_cambio_nick:
			proximo_cambio = usuario.fecha_ultimo_cambio_nick + timedelta(days=DIAS_ENTRE_CAMBIOS_NICK)
			if ahora < proximo_cambio:
				dias_restantes = max(1, ceil((proximo_cambio - ahora).total_seconds() / 86400))
				raise NicknameCooldownError(dias_restantes)
		usuario.username = nuevo_username
		usuario.fecha_ultimo_cambio_nick = ahora
		usuario.save(update_fields=('username', 'fecha_ultimo_cambio_nick'))
		return usuario, True


def eliminar_cuenta(usuario, password, confirmacion):
	"""Anonimiza una cuenta sin borrar claves ni snapshots competitivos."""
	with _account_lock, transaction.atomic():
		usuario = Usuario.objects.select_for_update().get(pk=usuario.pk)
		if usuario.estado_cuenta == Usuario.EstadoCuenta.ELIMINADA:
			return usuario, False
		if confirmacion != 'ELIMINAR':
			raise AccountLifecycleError('La confirmación debe ser ELIMINAR.')
		if not password or not usuario.check_password(password):
			raise AccountLifecycleError('La contraseña actual no es correcta.')

		avatar_name = usuario.avatar.name if usuario.avatar else ''
		avatar_storage = usuario.avatar.storage if usuario.avatar else default_storage
		username = f'usuario_eliminado_{usuario.pk}'
		if Usuario.objects.filter(username=username).exclude(pk=usuario.pk).exists():
			username = f'{username}_{uuid4().hex[:8]}'
		email = f'usuario_eliminado_{usuario.pk}_{uuid4().hex}@example.invalid'

		Notificacion.objects.filter(destinatario=usuario).delete()
		PreferenciasNotificacion.objects.filter(usuario=usuario).delete()
		SeguimientoTorneo.objects.filter(usuario=usuario).delete()
		from videojuegos.models import PerfilVideojuegoUsuario
		PerfilVideojuegoUsuario.objects.filter(usuario=usuario).update(
			nick_en_juego=username,
			rango_declarado=None,
			fecha_actualizacion_rango=None,
		)

		usuario.username = username
		usuario.email = email
		usuario.avatar = None
		usuario.estado_cuenta = Usuario.EstadoCuenta.ELIMINADA
		usuario.fecha_eliminacion = timezone.now()
		usuario.fecha_ultimo_cambio_nick = None
		usuario.disponible_para_arbitrar = False
		usuario.mostrar_ultima_conexion = False
		usuario.mostrar_estado_online = False
		usuario.ultima_actividad = None
		usuario.is_active = False
		usuario.is_staff = False
		usuario.is_superuser = False
		usuario.rol_global = Usuario.RolGlobal.PLAYER
		usuario.last_login = None
		usuario.set_unusable_password()
		usuario.groups.clear()
		usuario.user_permissions.clear()
		usuario.save(update_fields=(
			'username', 'email', 'avatar', 'estado_cuenta', 'fecha_eliminacion',
			'fecha_ultimo_cambio_nick', 'disponible_para_arbitrar',
			'mostrar_ultima_conexion', 'mostrar_estado_online', 'ultima_actividad',
			'is_active', 'is_staff', 'is_superuser', 'rol_global', 'last_login', 'password',
		))
		if avatar_name and not Usuario.objects.filter(avatar=avatar_name).exclude(pk=usuario.pk).exists():
			transaction.on_commit(lambda: avatar_storage.delete(avatar_name), robust=True)
		return usuario, True