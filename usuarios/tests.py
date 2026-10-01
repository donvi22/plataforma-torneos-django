from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.test import TestCase

from .models import Usuario


class UsuarioModelTests(TestCase):
	def test_crea_usuario_normal_con_valores_iniciales(self):
		usuario = Usuario.objects.create_user(
			username='jugador1',
			email='jugador1@example.com',
			password='clave-segura-123',
		)

		self.assertEqual(str(usuario), 'jugador1')
		self.assertEqual(usuario.xp_total, 0)
		self.assertEqual(usuario.nivel, 1)
		self.assertEqual(usuario.karma_total, 100)
		self.assertEqual(usuario.rol_global, Usuario.RolGlobal.PLAYER)
		self.assertEqual(usuario.estado_cuenta, Usuario.EstadoCuenta.ACTIVA)
		self.assertFalse(usuario.disponible_para_arbitrar)
		self.assertTrue(usuario.mostrar_estado_online)
		self.assertIsNone(usuario.fecha_ultimo_cambio_nick)
		self.assertIsNone(usuario.ultima_actividad)

	def test_username_y_email_son_unicos(self):
		Usuario.objects.create_user(
			username='jugador1',
			email='jugador1@example.com',
			password='clave-segura-123',
		)

		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				Usuario.objects.create_user(
					username='jugador1',
					email='otro@example.com',
					password='clave-segura-123',
				)

		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				Usuario.objects.create_user(
					username='jugador2',
					email='jugador1@example.com',
					password='clave-segura-123',
				)

	def test_nivel_no_puede_ser_inferior_a_uno(self):
		usuario = Usuario(username='jugador1', email='jugador1@example.com', nivel=0)

		with self.assertRaises(ValidationError):
			usuario.full_clean()

	def test_karma_no_puede_ser_negativo(self):
		usuario = Usuario(username='jugador1', email='jugador1@example.com', karma_total=-1)

		with self.assertRaises(ValidationError):
			usuario.full_clean()

	def test_superusuario_tiene_rol_admin(self):
		usuario = Usuario.objects.create_superuser(
			username='admin',
			email='admin@example.com',
			password='clave-segura-123',
		)

		self.assertTrue(usuario.is_superuser)
		self.assertTrue(usuario.is_staff)
		self.assertEqual(usuario.rol_global, Usuario.RolGlobal.ADMIN)
