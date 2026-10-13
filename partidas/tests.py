from django.core.exceptions import ValidationError
from django.test import TestCase

from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import ParticipantePartida, Partida
from .services import BracketError, PreparacionBracketError, generar_bracket, preparar_bracket


class BracketServiceTests(TestCase):
	def setUp(self):
		self.organizador = Usuario.objects.create_user(
			username='organizador',
			email='organizador@example.com',
			password='clave-segura-123',
		)
		self.videojuego = Videojuego.objects.create(
			nombre='Arena Battle',
			genero='Competitivo',
		)
		self.formato = FormatoCompetitivo.objects.create(
			nombre='Eliminacion directa',
			min_participantes=2,
			max_participantes=128,
		)

	def crear_torneo_con_inscripciones(self, cantidad, estado=Torneo.Estado.INSCRIPCIONES_CERRADAS):
		sufijo = Torneo.objects.count()
		torneo = Torneo.objects.create(
			nombre=f'Torneo {cantidad}',
			videojuego=self.videojuego,
			organizador=self.organizador,
			tipo=Torneo.Tipo.PRIVADO,
			formato_competitivo=self.formato,
			max_participantes=cantidad,
			estado=estado,
		)
		for indice in range(cantidad):
			usuario = Usuario.objects.create_user(
				username=f'jugador_{cantidad}_{sufijo}_{indice}',
				email=f'jugador_{cantidad}_{sufijo}_{indice}@example.com',
				password='clave-segura-123',
			)
			perfil = PerfilVideojuegoUsuario.objects.create(
				usuario=usuario,
				videojuego=self.videojuego,
				nick_en_juego=f'Nick{cantidad}{indice}',
			)
			InscripcionTorneo.objects.create(
				torneo=torneo,
				usuario=usuario,
				perfil_videojuego=perfil,
				nick_historico=perfil.nick_en_juego,
				estado=InscripcionTorneo.Estado.CONFIRMADA,
			)
		return torneo

	def test_genera_todos_los_tamanos_validos(self):
		for cantidad in (2, 4, 8, 16, 32, 64, 128):
			with self.subTest(cantidad=cantidad):
				torneo = self.crear_torneo_con_inscripciones(cantidad)
				partidas = generar_bracket(torneo)
				self.assertEqual(len(partidas), cantidad - 1)
				self.assertEqual(
					Partida.objects.filter(torneo=torneo, numero_ronda=1).count(),
					cantidad // 2,
				)
				self.assertEqual(
					Partida.objects.filter(torneo=torneo, numero_ronda=(cantidad.bit_length() - 1)).count(),
					1,
				)

	def test_relaciones_entre_rondas_y_participantes_iniciales(self):
		torneo = self.crear_torneo_con_inscripciones(8)
		generar_bracket(torneo)
		primera = Partida.objects.filter(torneo=torneo, numero_ronda=1)
		posteriores = Partida.objects.filter(torneo=torneo).exclude(numero_ronda=1)

		self.assertEqual(primera.count(), 4)
		self.assertEqual(Partida.objects.filter(torneo=torneo, numero_ronda=2).count(), 2)
		self.assertEqual(Partida.objects.filter(torneo=torneo, numero_ronda=3).count(), 1)
		self.assertEqual(ParticipantePartida.objects.filter(partida__torneo=torneo).count(), 8)
		self.assertFalse(ParticipantePartida.objects.filter(partida__in=posteriores).exists())
		self.assertEqual(Partida.objects.filter(torneo=torneo, siguiente_partida__isnull=True).count(), 1)

		for partida in primera:
			self.assertIsNotNone(partida.siguiente_partida)
			self.assertIn(partida.posicion_en_siguiente_partida, (1, 2))
			self.assertEqual(partida.participantes.count(), 2)
		for partida in posteriores:
			self.assertEqual(partida.partidas_anteriores.count(), 2)

	def test_distribuye_todas_las_inscripciones_sin_duplicados(self):
		torneo = self.crear_torneo_con_inscripciones(16)
		generar_bracket(torneo)
		participantes = ParticipantePartida.objects.filter(partida__torneo=torneo)
		self.assertEqual(participantes.count(), 16)
		self.assertEqual(participantes.values('inscripcion').distinct().count(), 16)
		self.assertEqual(participantes.values('posicion').distinct().count(), 2)

	def test_rechaza_inscripciones_abiertas_o_torneo_incompleto(self):
		abierto = self.crear_torneo_con_inscripciones(2, Torneo.Estado.INSCRIPCIONES_ABIERTAS)
		with self.assertRaises(BracketError):
			generar_bracket(abierto)

		incompleto = Torneo.objects.create(
			nombre='Incompleto',
			videojuego=self.videojuego,
			organizador=self.organizador,
			tipo=Torneo.Tipo.PRIVADO,
			formato_competitivo=self.formato,
			max_participantes=4,
			estado=Torneo.Estado.INSCRIPCIONES_CERRADAS,
		)
		with self.assertRaises(BracketError):
			generar_bracket(incompleto)

	def test_idempotencia_y_estado_preparado_sin_iniciar(self):
		torneo = self.crear_torneo_con_inscripciones(4)
		primera_generacion = generar_bracket(torneo)
		segunda_generacion = generar_bracket(torneo)

		self.assertEqual(len(primera_generacion), 3)
		self.assertEqual([partida.pk for partida in primera_generacion], [partida.pk for partida in segunda_generacion])
		self.assertEqual(Partida.objects.filter(torneo=torneo).count(), 3)
		torneo.refresh_from_db()
		self.assertEqual(torneo.estado, Torneo.Estado.PREPARADO)
		self.assertNotEqual(torneo.estado, Torneo.Estado.EN_CURSO)
		self.assertEqual(torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.PREPARADO).count(), 1)

	def test_preparacion_repetida_no_cambia_estado_ni_historial(self):
		torneo = self.crear_torneo_con_inscripciones(2)
		partidas = generar_bracket(torneo)
		for estado in (Torneo.Estado.PREPARADO, Torneo.Estado.EN_CURSO):
			Torneo.objects.filter(pk=torneo.pk).update(estado=estado)
			torneo.refresh_from_db()
			historial = torneo.historial_estados.count()
			self.assertEqual([p.pk for p in preparar_bracket(torneo, self.organizador)], [p.pk for p in partidas])
			torneo.refresh_from_db()
			self.assertEqual(torneo.estado, estado)
			self.assertEqual(torneo.historial_estados.count(), historial)

	def test_preparacion_no_reabre_torneos_terminales(self):
		torneo = self.crear_torneo_con_inscripciones(2)
		generar_bracket(torneo)
		for estado in (Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO):
			Torneo.objects.filter(pk=torneo.pk).update(estado=estado)
			torneo.refresh_from_db()
			with self.assertRaises(PreparacionBracketError):
				preparar_bracket(torneo, self.organizador)
			torneo.refresh_from_db()
			self.assertEqual(torneo.estado, estado)

	def test_rechaza_participante_de_otro_torneo(self):
		torneo = self.crear_torneo_con_inscripciones(2)
		otro_torneo = self.crear_torneo_con_inscripciones(2)
		partida = Partida.objects.create(torneo=torneo, numero_ronda=1, numero_orden=1)
		inscripcion = otro_torneo.inscripciones.first()
		participante = ParticipantePartida(
			partida=partida,
			inscripcion=inscripcion,
			posicion=1,
		)

		with self.assertRaises(ValidationError):
			participante.full_clean()

	def test_no_permite_posicion_repetida_ni_tercer_participante(self):
		torneo = self.crear_torneo_con_inscripciones(4)
		partida = Partida.objects.create(torneo=torneo, numero_ronda=1, numero_orden=1)
		inscripciones = list(torneo.inscripciones.all())
		ParticipantePartida.objects.create(partida=partida, inscripcion=inscripciones[0], posicion=1)

		repetida = ParticipantePartida(
			partida=partida,
			inscripcion=inscripciones[1],
			posicion=1,
		)
		with self.assertRaises(ValidationError):
			repetida.full_clean()

		ParticipantePartida.objects.create(partida=partida, inscripcion=inscripciones[1], posicion=2)
		tercero = ParticipantePartida(
			partida=partida,
			inscripcion=inscripciones[2],
			posicion=1,
		)
		with self.assertRaises(ValidationError):
			tercero.full_clean()

	def test_admin_partida_no_modifica_estado_horario_o_lobby_directamente(self):
		from django.contrib import admin
		from django.test import RequestFactory
		from partidas.admin import PartidaAdmin
		usuario = Usuario.objects.create_superuser(username='admin_lectura', email='admin_lectura@example.com', password='test')
		request = RequestFactory().post('/admin/partidas/partida/')
		request.user = usuario
		model_admin = PartidaAdmin(Partida, admin.site)
		self.assertFalse(model_admin.has_change_permission(request))
		self.assertTrue(model_admin.has_view_permission(request))

	def test_rechaza_bracket_incompleto_existente(self):
		torneo = self.crear_torneo_con_inscripciones(4)
		Partida.objects.create(torneo=torneo, numero_ronda=1, numero_orden=1)

		with self.assertRaises(BracketError):
			generar_bracket(torneo)
