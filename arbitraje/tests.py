from django.core.exceptions import ValidationError
from django.test import TestCase

from partidas.models import Partida
from partidas.services import ResultadoError, validar_resultado
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from torneos.services import InscripcionError, inscribir_usuario
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import ArbitroTorneo, HistorialAsignacionArbitro
from .services import (
	ArbitrajeError,
	aceptar_invitacion,
	asignar_arbitro,
	invitar_arbitro,
	plazas_arbitrales_necesarias,
	reasignar_arbitro,
	rechazar_invitacion,
	solicitar_reasignacion,
	solicitar_salida,
)


class ArbitrajeTests(TestCase):
	def setUp(self):
		self.organizador = Usuario.objects.create_user(
			username='organizador',
			email='organizador@example.com',
			password='clave-segura-123',
		)
		self.videojuego = Videojuego.objects.create(nombre='Arena Battle', genero='Competitivo')
		self.formato = FormatoCompetitivo.objects.create(
			nombre='Eliminacion directa', min_participantes=2, max_participantes=128,
		)

	def crear_torneo(self, tipo=Torneo.Tipo.PUBLICO, max_participantes=8):
		return Torneo.objects.create(
			nombre=f'Torneo {Torneo.objects.count()}',
			videojuego=self.videojuego,
			organizador=self.organizador,
			tipo=tipo,
			formato_competitivo=self.formato,
			max_participantes=max_participantes,
			estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
		)

	def crear_usuario(self, nombre, disponible=True, karma=150):
		return Usuario.objects.create_user(
			username=nombre,
			email=f'{nombre}@example.com',
			password='clave-segura-123',
			disponible_para_arbitrar=disponible,
			karma_total=karma,
		)

	def test_plazas_arbitrales(self):
		esperadas = {2: 1, 8: 1, 9: 2, 16: 2, 17: 3, 32: 3, 33: 4, 48: 4, 49: 5, 64: 5, 65: 6, 96: 6, 97: 8, 128: 8}
		for participantes, plazas in esperadas.items():
			self.assertEqual(plazas_arbitrales_necesarias(participantes), plazas)

	def test_invitacion_publica_antes_de_abrir_y_requisitos(self):
		torneo = self.crear_torneo()
		arbitro = self.crear_usuario('arbitro')
		invitacion = invitar_arbitro(torneo, arbitro, self.organizador)
		self.assertEqual(invitacion.estado_invitacion, ArbitroTorneo.EstadoInvitacion.PENDIENTE)

		malo = self.crear_usuario('malo', disponible=False)
		with self.assertRaises(ArbitrajeError):
			invitar_arbitro(torneo, malo, self.organizador)
		karma_bajo = self.crear_usuario('karma_bajo', karma=149)
		with self.assertRaises(ArbitrajeError):
			invitar_arbitro(torneo, karma_bajo, self.organizador)

	def test_rechaza_torneos_privados_y_limite_pendientes(self):
		privado = self.crear_torneo(Torneo.Tipo.PRIVADO)
		arbitro = self.crear_usuario('privado')
		with self.assertRaises(ArbitrajeError):
			invitar_arbitro(privado, arbitro, self.organizador)

		torneo = self.crear_torneo(max_participantes=8)
		for indice in range(2):
			invitar_arbitro(torneo, self.crear_usuario(f'pendiente{indice}'), self.organizador)
		self.assertEqual(torneo.arbitros.filter(estado_invitacion='PENDIENTE').count(), 2)
		with self.assertRaises(ArbitrajeError):
			invitar_arbitro(torneo, self.crear_usuario('pendiente_extra'), self.organizador)

	def test_aceptar_rechazar_y_cancelar_pendientes_al_completar(self):
		torneo = self.crear_torneo(max_participantes=16)
		arbitros = [self.crear_usuario(f'arbitro{indice}') for indice in range(3)]
		invitaciones = [invitar_arbitro(torneo, arbitro, self.organizador) for arbitro in arbitros]
		aceptar_invitacion(invitaciones[0], arbitros[0])
		rechazar_invitacion(invitaciones[1], arbitros[1])
		self.assertFalse(invitaciones[1].activo_en_torneo)
		aceptar_invitacion(invitaciones[2], arbitros[2])
		self.assertEqual(torneo.arbitros.filter(estado_invitacion='ACEPTADA').count(), 2)

		torneo2 = self.crear_torneo(max_participantes=2)
		uno = self.crear_usuario('uno')
		dos = self.crear_usuario('dos')
		primera = invitar_arbitro(torneo2, uno, self.organizador)
		segunda = invitar_arbitro(torneo2, dos, self.organizador)
		aceptar_invitacion(primera, uno)
		segunda.refresh_from_db()
		self.assertEqual(segunda.estado_invitacion, ArbitroTorneo.EstadoInvitacion.CANCELADA)

	def test_incompatibilidad_en_ambos_sentidos(self):
		torneo = self.crear_torneo()
		jugador = self.crear_usuario('jugador')
		perfil = PerfilVideojuegoUsuario.objects.create(
			usuario=jugador, videojuego=self.videojuego, nick_en_juego='Jugador',
		)
		inscripcion = InscripcionTorneo.objects.create(
			torneo=torneo, usuario=jugador, perfil_videojuego=perfil,
			nick_historico='Jugador', estado=InscripcionTorneo.Estado.CONFIRMADA,
		)
		with self.assertRaises(ArbitrajeError):
			invitar_arbitro(torneo, jugador, self.organizador)

		otro = self.crear_usuario('arbitro_activo')
		invitacion = invitar_arbitro(torneo, otro, self.organizador)
		aceptar_invitacion(invitacion, otro)
		perfil_otro = PerfilVideojuegoUsuario.objects.create(
			usuario=otro, videojuego=self.videojuego, nick_en_juego='Otro',
		)
		with self.assertRaises(InscripcionError):
			inscribir_usuario(torneo, otro)

	def crear_partida_con_arbitros(self):
		torneo = self.crear_torneo(max_participantes=16)
		arbitro1 = self.crear_usuario('asignado1')
		arbitro2 = self.crear_usuario('asignado2')
		invitacion1 = invitar_arbitro(torneo, arbitro1, self.organizador)
		invitacion2 = invitar_arbitro(torneo, arbitro2, self.organizador)
		aceptar_invitacion(invitacion1, arbitro1)
		aceptar_invitacion(invitacion2, arbitro2)
		partida = Partida.objects.create(torneo=torneo, numero_ronda=1, numero_orden=1)
		return partida, invitacion1, invitacion2

	def test_asignacion_equilibrada_y_reasignacion(self):
		partida, arbitro1, arbitro2 = self.crear_partida_con_arbitros()
		asignado = asignar_arbitro(partida)
		self.assertIn(asignado.pk, (arbitro1.pk, arbitro2.pk))
		self.assertEqual(HistorialAsignacionArbitro.objects.count(), 1)
		self.assertEqual(asignar_arbitro(partida).pk, asignado.pk)
		with self.assertRaises(ArbitrajeError):
			solicitar_reasignacion(partida, asignado, '')
		self.assertTrue(solicitar_reasignacion(partida, asignado, 'Conflicto de horario'))
		nuevo = arbitro2 if asignado.pk == arbitro1.pk else arbitro1
		reasignar_arbitro(partida, nuevo, self.organizador, 'Conflicto de horario')
		partida.refresh_from_db()
		self.assertEqual(partida.arbitro_asignado_id, nuevo.pk)
		self.assertEqual(HistorialAsignacionArbitro.objects.count(), 2)

	def test_rechaza_arbitro_de_otro_torneo_y_autoasignacion(self):
		partida, arbitro, _ = self.crear_partida_con_arbitros()
		otro_torneo = self.crear_torneo(max_participantes=2)
		otro = self.crear_usuario('otro_torneo')
		invitacion = invitar_arbitro(otro_torneo, otro, self.organizador)
		aceptar_invitacion(invitacion, otro)
		with self.assertRaises(ArbitrajeError):
			reasignar_arbitro(partida, invitacion, self.organizador, 'Cambio')
		with self.assertRaises(ArbitrajeError):
			asignar_arbitro(partida, actor=arbitro.usuario, arbitro=arbitro)

	def test_arbitro_asignado_puede_validar_y_otro_no(self):
		partida, arbitro, otro = self.crear_partida_con_arbitros()
		asignar_arbitro(partida, arbitro=arbitro)
		usuario1 = self.crear_usuario('jugador_resultado')
		usuario2 = self.crear_usuario('jugador_resultado2')
		inscripciones = []
		for usuario in (usuario1, usuario2):
			perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=self.videojuego, nick_en_juego=usuario.username)
			inscripciones.append(InscripcionTorneo.objects.create(torneo=partida.torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=usuario.username, estado=InscripcionTorneo.Estado.CONFIRMADA))
		from partidas.models import ParticipantePartida
		ParticipantePartida.objects.create(partida=partida, inscripcion=inscripciones[0], posicion=1)
		ParticipantePartida.objects.create(partida=partida, inscripcion=inscripciones[1], posicion=2)
		partida.estado = Partida.Estado.EN_CURSO
		partida.save(update_fields=('estado',))
		with self.assertRaises(ResultadoError):
			validar_resultado(partida, otro.usuario, '1-0', inscripciones[0])
		validar_resultado(partida, arbitro.usuario, '1-0', inscripciones[0])
		self.assertEqual(partida.resultado_oficial.ganador_id, inscripciones[0].pk)
