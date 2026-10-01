from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import (
	DeclaracionResultado,
	Partida,
	ResultadoPartida,
)
from .services import ResultadoError, declarar_resultado, generar_bracket, validar_resultado


class ResultadoServiceTests(TestCase):
	def setUp(self):
		self.organizador = Usuario.objects.create_user(
			username='organizador',
			email='organizador@example.com',
			password='clave-segura-123',
		)
		self.ajeno = Usuario.objects.create_user(
			username='ajeno',
			email='ajeno@example.com',
			password='clave-segura-123',
		)
		self.videojuego = Videojuego.objects.create(nombre='Arena Battle', genero='Competitivo')
		self.formato = FormatoCompetitivo.objects.create(
			nombre='Eliminacion directa',
			min_participantes=2,
			max_participantes=128,
		)

	def crear_bracket(self, cantidad=4):
		torneo = Torneo.objects.create(
			nombre=f'Torneo {cantidad}',
			videojuego=self.videojuego,
			organizador=self.organizador,
			tipo=Torneo.Tipo.PRIVADO,
			formato_competitivo=self.formato,
			max_participantes=cantidad,
			estado=Torneo.Estado.INSCRIPCIONES_CERRADAS,
		)
		for indice in range(cantidad):
			usuario = Usuario.objects.create_user(
				username=f'jugador{indice}',
				email=f'jugador{indice}@example.com',
				password='clave-segura-123',
			)
			perfil = PerfilVideojuegoUsuario.objects.create(
				usuario=usuario,
				videojuego=self.videojuego,
				nick_en_juego=f'Nick{indice}',
			)
			InscripcionTorneo.objects.create(
				torneo=torneo,
				usuario=usuario,
				perfil_videojuego=perfil,
				nick_historico=perfil.nick_en_juego,
				estado=InscripcionTorneo.Estado.CONFIRMADA,
			)
		generar_bracket(torneo)
		return torneo

	def test_declaracion_valida_no_avanza_ni_crea_resultado_oficial(self):
		torneo = self.crear_bracket()
		partida = torneo.partidas.get(numero_ronda=1, numero_orden=1)
		partida.estado = Partida.Estado.EN_CURSO
		partida.save(update_fields=('estado',))
		participante = partida.participantes.first()
		declaracion = declarar_resultado(
			partida,
			participante.inscripcion.usuario,
			'2-1',
			participante.inscripcion,
		)

		self.assertIsInstance(declaracion, DeclaracionResultado)
		self.assertFalse(hasattr(partida, 'resultado_oficial'))
		self.assertEqual(partida.siguiente_partida.participantes.count(), 0)

	def test_rechaza_declaracion_ajena_y_ganador_ajeno(self):
		torneo = self.crear_bracket()
		partida = torneo.partidas.get(numero_ronda=1, numero_orden=1)
		participante = partida.participantes.first()
		with self.assertRaises(ResultadoError):
			declarar_resultado(partida, self.ajeno, '2-0')

		otra_partida = torneo.partidas.get(numero_ronda=1, numero_orden=2)
		with self.assertRaises(ResultadoError):
			declarar_resultado(
				partida,
				participante.inscripcion.usuario,
				'2-0',
				otra_partida.participantes.first().inscripcion,
			)

	def test_solo_organizador_puede_validar_y_no_se_puede_validar_dos_veces(self):
		torneo = self.crear_bracket()
		partida = torneo.partidas.get(numero_ronda=1, numero_orden=1)
		ganador = partida.participantes.first().inscripcion
		with self.assertRaises(ResultadoError):
			validar_resultado(partida, self.ajeno, '2-0', ganador)

		resultado = validar_resultado(partida, self.organizador, '2-0', ganador)
		self.assertEqual(resultado.tipo_resultado, ResultadoPartida.TipoResultado.NORMAL)
		partida.refresh_from_db()
		self.assertEqual(partida.estado, Partida.Estado.FINALIZADA)
		self.assertIsNotNone(partida.fecha_hora_fin_real)
		with self.assertRaises(ResultadoError):
			validar_resultado(partida, self.organizador, '3-0', ganador)

	def test_ganador_avanza_y_fecha_de_rivales_se_registra(self):
		torneo = self.crear_bracket()
		primera = torneo.partidas.get(numero_ronda=1, numero_orden=1)
		segunda = torneo.partidas.get(numero_ronda=1, numero_orden=2)
		ganador_primera = primera.participantes.first().inscripcion
		ganador_segunda = segunda.participantes.first().inscripcion
		validar_resultado(primera, self.organizador, '1-0', ganador_primera)
		self.assertEqual(primera.siguiente_partida.participantes.count(), 1)
		validar_resultado(segunda, self.organizador, '1-0', ganador_segunda)
		final = primera.siguiente_partida
		final.refresh_from_db()
		self.assertEqual(final.participantes.count(), 2)
		self.assertIsNotNone(final.fecha_hora_rivales_confirmados)

	def test_finaliza_la_final_sin_intentar_avanzar(self):
		torneo = self.crear_bracket(2)
		final = torneo.partidas.get(numero_ronda=1, numero_orden=1)
		ganador = final.participantes.first().inscripcion
		validar_resultado(final, self.organizador, '1-0', ganador)
		self.assertIsNone(final.siguiente_partida)
		self.assertEqual(final.resultado_oficial.ganador, ganador)

	def test_validar_no_modifica_fecha_programada_oficial(self):
		torneo = self.crear_bracket()
		torneo.tipo = Torneo.Tipo.OFICIAL
		torneo.save(update_fields=('tipo',))
		partida = torneo.partidas.get(numero_ronda=1, numero_orden=1)
		fecha_programada = partida.fecha_hora_programada
		partida.fecha_hora_programada = partida.fecha_hora_programada or timezone.now()
		partida.save(update_fields=('fecha_hora_programada',))
		fecha_programada = partida.fecha_hora_programada
		partida.estado = Partida.Estado.EN_CURSO
		partida.save(update_fields=('estado',))
		validar_resultado(
			partida,
			self.organizador,
			'1-0',
			partida.participantes.first().inscripcion,
		)
		partida.refresh_from_db()
		self.assertEqual(partida.fecha_hora_programada, fecha_programada)

	def test_doble_incomparecencia_no_tiene_ganador(self):
		torneo = self.crear_bracket(2)
		final = torneo.partidas.get(numero_ronda=1, numero_orden=1)
		resultado = validar_resultado(
			final,
			self.organizador,
			'',
			tipo_resultado=ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA,
			motivo='Ningún participante compareció.',
		)
		self.assertIsNone(resultado.ganador)
		self.assertIsNone(final.siguiente_partida)

	def test_rama_vacia_genera_avance_automatico(self):
		torneo = self.crear_bracket()
		primera = torneo.partidas.get(numero_ronda=1, numero_orden=1)
		segunda = torneo.partidas.get(numero_ronda=1, numero_orden=2)
		validar_resultado(
			segunda,
			self.organizador,
			'',
			tipo_resultado=ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA,
		)
		ganador = primera.participantes.first().inscripcion
		validar_resultado(primera, self.organizador, '1-0', ganador)
		final = primera.siguiente_partida
		final.refresh_from_db()
		self.assertEqual(final.resultado_oficial.tipo_resultado, ResultadoPartida.TipoResultado.AVANCE_AUTOMATICO)
		self.assertEqual(final.resultado_oficial.ganador, ganador)

	def test_dos_ramas_vacias_resuelven_sin_campeon_ficticio(self):
		torneo = self.crear_bracket()
		for partida in torneo.partidas.filter(numero_ronda=1):
			validar_resultado(
				partida,
				self.organizador,
				'',
				tipo_resultado=ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA,
			)
		final = torneo.partidas.get(numero_ronda=2, numero_orden=1)
		final.refresh_from_db()
		self.assertEqual(final.resultado_oficial.tipo_resultado, ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA)
		self.assertIsNone(final.resultado_oficial.ganador)