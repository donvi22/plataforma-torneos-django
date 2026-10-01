from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from usuarios.models import Usuario

from .models import PerfilVideojuegoUsuario, Plataforma, RangoVideojuego, Videojuego


class VideojuegosModelTests(TestCase):
	def setUp(self):
		self.usuario = Usuario.objects.create_user(
			username='jugador1',
			email='jugador1@example.com',
			password='clave-segura-123',
		)
		self.videojuego = Videojuego.objects.create(
			nombre='Arena Battle',
			genero='Competitivo',
		)

	def test_crea_videojuego_y_plataformas_relacionadas(self):
		pc = Plataforma.objects.create(nombre='PC')
		consola = Plataforma.objects.create(nombre='Consola')
		self.videojuego.plataformas.add(pc, consola)

		self.assertEqual(self.videojuego.plataformas.count(), 2)
		self.assertEqual(set(self.videojuego.plataformas.all()), {pc, consola})

	def test_rangos_son_unicos_por_nombre_y_posicion_en_cada_juego(self):
		RangoVideojuego.objects.create(videojuego=self.videojuego, nombre='Bronce', posicion=1)
		RangoVideojuego.objects.create(videojuego=self.videojuego, nombre='Plata', posicion=2)

		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				RangoVideojuego.objects.create(
					videojuego=self.videojuego,
					nombre='Bronce',
					posicion=3,
				)

		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				RangoVideojuego.objects.create(
					videojuego=self.videojuego,
					nombre='Oro',
					posicion=2,
				)

	def test_normaliza_la_posicion_del_rango(self):
		bronce = RangoVideojuego.objects.create(
			videojuego=self.videojuego,
			nombre='Bronce',
			posicion=1,
		)
		oro = RangoVideojuego.objects.create(
			videojuego=self.videojuego,
			nombre='Oro',
			posicion=2,
		)
		diamante = RangoVideojuego.objects.create(
			videojuego=self.videojuego,
			nombre='Diamante',
			posicion=3,
		)

		self.assertEqual(bronce.posicion_normalizada, 0.0)
		self.assertEqual(oro.posicion_normalizada, 0.5)
		self.assertEqual(diamante.posicion_normalizada, 1.0)

		videojuego_unico = Videojuego.objects.create(nombre='Juego Único', genero='Estrategia')
		rango_unico = RangoVideojuego.objects.create(
			videojuego=videojuego_unico,
			nombre='Único',
			posicion=1,
		)
		self.assertEqual(rango_unico.posicion_normalizada, 0.0)

	def test_un_usuario_solo_tiene_un_perfil_por_videojuego(self):
		PerfilVideojuegoUsuario.objects.create(
			usuario=self.usuario,
			videojuego=self.videojuego,
			nick_en_juego='JugadorPro',
		)

		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				PerfilVideojuegoUsuario.objects.create(
					usuario=self.usuario,
					videojuego=self.videojuego,
					nick_en_juego='OtroNick',
				)

	def test_rechaza_un_rango_de_otro_videojuego(self):
		otro_videojuego = Videojuego.objects.create(nombre='Otro Juego', genero='Acción')
		rango = RangoVideojuego.objects.create(
			videojuego=otro_videojuego,
			nombre='Inicial',
			posicion=1,
		)
		perfil = PerfilVideojuegoUsuario(
			usuario=self.usuario,
			videojuego=self.videojuego,
			nick_en_juego='JugadorPro',
			rango_declarado=rango,
		)

		with self.assertRaises(ValidationError):
			perfil.full_clean()

	def test_actualizar_rango_registra_la_fecha(self):
		inicial = RangoVideojuego.objects.create(
			videojuego=self.videojuego,
			nombre='Inicial',
			posicion=1,
		)
		avanzado = RangoVideojuego.objects.create(
			videojuego=self.videojuego,
			nombre='Avanzado',
			posicion=2,
		)
		perfil = PerfilVideojuegoUsuario.objects.create(
			usuario=self.usuario,
			videojuego=self.videojuego,
			nick_en_juego='JugadorPro',
			rango_declarado=inicial,
		)

		self.assertIsNone(perfil.fecha_actualizacion_rango)
		perfil.rango_declarado = avanzado
		perfil.save()
		perfil.refresh_from_db()

		self.assertIsNotNone(perfil.fecha_actualizacion_rango)
