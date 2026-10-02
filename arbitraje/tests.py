from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from partidas.models import Partida, ParticipantePartida
from partidas.services import ResultadoError, validar_resultado
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from torneos.services import InscripcionError, inscribir_usuario
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego
from notificaciones.models import Notificacion

from .models import ArbitroTorneo, HistorialAsignacionArbitro
from .services import (
	ArbitrajeError,
	aceptar_invitacion,
	asignar_arbitro,
	asignar_arbitros_pendientes,
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


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ArbitrajeWebTests(TestCase):
	def setUp(self):
		self.organizador = self.crear_usuario('web_org')
		self.admin = Usuario.objects.create_superuser(
			username='web_admin', email='web_admin@example.com', password='test',
		)
		self.arbitro = self.crear_usuario('web_arbitro')
		self.otro_arbitro = self.crear_usuario('web_otro_arbitro')
		self.jugador = self.crear_usuario('web_jugador')
		self.videojuego = Videojuego.objects.create(nombre='Web Game', genero='Competitivo')
		self.formato = FormatoCompetitivo.objects.create(
			nombre='Web Format', min_participantes=2, max_participantes=128,
		)
		self.torneo = self.crear_torneo()

	def crear_usuario(self, username, karma=150, disponible=True):
		return Usuario.objects.create_user(
			username=username,
			email=f'{username}@example.com',
			password='test-password',
			karma_total=karma,
			disponible_para_arbitrar=disponible,
		)

	def crear_torneo(self, tipo=Torneo.Tipo.PUBLICO, nombre='Web torneo'):
		return Torneo.objects.create(
			nombre=nombre,
			videojuego=self.videojuego,
			organizador=self.organizador,
			tipo=tipo,
			formato_competitivo=self.formato,
			max_participantes=16,
			estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
			fecha_publicacion=timezone.now(),
		)

	def invitar(self, torneo=None, arbitro=None):
		return invitar_arbitro(torneo or self.torneo, arbitro or self.arbitro, self.organizador)

	def aceptar_como(self, arbitro=None):
		arbitro = arbitro or self.arbitro
		invitacion = self.invitar(arbitro=arbitro)
		return aceptar_invitacion(invitacion, arbitro)

	def test_panel_exige_login_y_lista_solo_invitaciones_propias(self):
		invitacion_propia = self.invitar()
		otro_torneo = self.crear_torneo(nombre='Torneo ajeno al panel')
		self.invitar(otro_torneo, self.otro_arbitro)
		url = reverse('centro-arbitraje')
		respuesta_anonima = self.client.get(url)
		self.assertRedirects(respuesta_anonima, f'{reverse("login")}?next={url}', fetch_redirect_response=False)
		self.client.force_login(self.arbitro)
		respuesta = self.client.get(url)
		self.assertContains(respuesta, 'Web torneo')
		self.assertNotContains(respuesta, 'Torneo ajeno al panel')
		self.assertEqual(list(respuesta.context['invitaciones_pendientes']), [invitacion_propia])

	def test_aceptar_y_rechazar_invitaciones_usa_servicios_y_solo_post(self):
		aceptada = self.invitar()
		rechazada = self.invitar(arbitro=self.otro_arbitro)
		self.client.force_login(self.arbitro)
		url_aceptar = reverse('responder-invitacion-arbitral', args=(aceptada.pk, 'aceptar'))
		self.assertEqual(self.client.get(url_aceptar).status_code, 405)
		self.client.force_login(self.organizador)
		self.assertEqual(self.client.post(url_aceptar).status_code, 404)
		self.client.force_login(self.arbitro)
		self.client.post(url_aceptar)
		aceptada.refresh_from_db()
		self.assertEqual(aceptada.estado_invitacion, ArbitroTorneo.EstadoInvitacion.ACEPTADA)
		self.assertTrue(aceptada.activo_en_torneo)
		self.assertTrue(Notificacion.objects.filter(
			destinatario=self.arbitro, tipo=Notificacion.Tipo.INVITACION_ARBITRAL,
		).exists())
		self.client.force_login(self.otro_arbitro)
		self.client.post(reverse('responder-invitacion-arbitral', args=(rechazada.pk, 'rechazar')))
		rechazada.refresh_from_db()
		self.assertEqual(rechazada.estado_invitacion, ArbitroTorneo.EstadoInvitacion.RECHAZADA)

	def test_aceptacion_rechaza_participante_y_karma_insuficiente(self):
		perfil = PerfilVideojuegoUsuario.objects.create(
			usuario=self.jugador, videojuego=self.videojuego, nick_en_juego='Jugador',
		)
		InscripcionTorneo.objects.create(
			torneo=self.torneo, usuario=self.jugador, perfil_videojuego=perfil,
			nick_historico='Jugador', estado=InscripcionTorneo.Estado.CONFIRMADA,
		)
		invitacion_jugador = ArbitroTorneo.objects.create(torneo=self.torneo, usuario=self.jugador)
		self.client.force_login(self.jugador)
		self.client.post(reverse('responder-invitacion-arbitral', args=(invitacion_jugador.pk, 'aceptar')))
		invitacion_jugador.refresh_from_db()
		self.assertEqual(invitacion_jugador.estado_invitacion, ArbitroTorneo.EstadoInvitacion.PENDIENTE)
		self.assertContains(self.client.get(reverse('centro-arbitraje')), 'Un participante confirmado')

		invitacion_bajo_karma = self.invitar()
		self.arbitro.karma_total = 149
		self.arbitro.save(update_fields=('karma_total',))
		self.client.force_login(self.arbitro)
		self.client.post(reverse('responder-invitacion-arbitral', args=(invitacion_bajo_karma.pk, 'aceptar')))
		invitacion_bajo_karma.refresh_from_db()
		self.assertEqual(invitacion_bajo_karma.estado_invitacion, ArbitroTorneo.EstadoInvitacion.PENDIENTE)

	def test_disponibilidad_valida_karma_y_preserva_asignaciones(self):
		self.arbitro.karma_total = 149
		self.arbitro.disponible_para_arbitrar = False
		self.arbitro.save(update_fields=('karma_total', 'disponible_para_arbitrar'))
		self.client.force_login(self.arbitro)
		url = reverse('cambiar-disponibilidad-arbitraje')
		self.client.post(url, {'disponible': '1'})
		self.arbitro.refresh_from_db()
		self.assertFalse(self.arbitro.disponible_para_arbitrar)

		self.arbitro.karma_total = 150
		self.arbitro.save(update_fields=('karma_total',))
		self.client.post(url, {'disponible': '1'})
		self.arbitro.refresh_from_db()
		self.assertTrue(self.arbitro.disponible_para_arbitrar)
		invitacion = self.invitar()
		aceptar_invitacion(invitacion, self.arbitro)
		partida_actual = Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=1)
		asignar_arbitro(partida_actual, arbitro=invitacion)

		self.client.post(url, {'disponible': '0'})
		partida_actual.refresh_from_db()
		self.assertFalse(self.arbitro.__class__.objects.get(pk=self.arbitro.pk).disponible_para_arbitrar)
		self.assertEqual(partida_actual.arbitro_asignado_id, invitacion.pk)
		partida_nueva = Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=2)
		self.assertIsNone(asignar_arbitro(partida_nueva))
		with self.assertRaises(ArbitrajeError):
			asignar_arbitro(partida_nueva, arbitro=invitacion)

	def test_arbitro_aceptado_inactivo_permanece_en_historial(self):
		invitacion = self.aceptar_como()
		solicitar_salida(invitacion, 'Cambio de disponibilidad')
		self.client.force_login(self.arbitro)

		respuesta = self.client.get(reverse('centro-arbitraje'))

		self.assertContains(respuesta, 'Invitaciones anteriores')
		self.assertContains(respuesta, 'Aceptada · Inactivo')

	def test_partidas_asignadas_priorizan_validacion_y_historial_reconstruye_reasignacion(self):
		primera = self.aceptar_como(self.arbitro)
		segunda = self.aceptar_como(self.otro_arbitro)
		pendiente = Partida.objects.create(
			torneo=self.torneo, numero_ronda=1, numero_orden=1,
			estado=Partida.Estado.PENDIENTE_VALIDACION,
		)
		revision_abierta = Partida.objects.create(
			torneo=self.torneo, numero_ronda=1, numero_orden=3,
			estado=Partida.Estado.PENDIENTE_VALIDACION,
		)
		en_curso = Partida.objects.create(
			torneo=self.torneo, numero_ronda=1, numero_orden=2,
			estado=Partida.Estado.EN_CURSO,
		)
		asignar_arbitro(pendiente, arbitro=primera)
		asignar_arbitro(revision_abierta, arbitro=primera)
		asignar_arbitro(en_curso, arbitro=primera)
		participantes = []
		for indice in range(2):
			usuario = self.crear_usuario(f'hist_jugador_{indice}')
			perfil = PerfilVideojuegoUsuario.objects.create(
				usuario=usuario, videojuego=self.videojuego, nick_en_juego=usuario.username,
			)
			participantes.append(InscripcionTorneo.objects.create(
				torneo=self.torneo, usuario=usuario, perfil_videojuego=perfil,
				nick_historico=usuario.username,
			))
		for indice, inscripcion in enumerate(participantes, start=1):
			ParticipantePartida.objects.create(partida=pendiente, inscripcion=inscripcion, posicion=indice)
		validar_resultado(pendiente, self.arbitro, '2-1', participantes[0])
		reasignar_arbitro(pendiente, segunda, self.organizador, 'Cobertura histórica de la prueba')
		self.client.force_login(self.arbitro)
		respuesta = self.client.get(reverse('centro-arbitraje'))
		partidas = list(respuesta.context['partidas_asignadas'])
		self.assertEqual(partidas[0].pk, revision_abierta.pk)
		self.assertEqual(partidas[1].pk, en_curso.pk)
		self.assertIn('needs-review', respuesta.content.decode())
		historial = respuesta.context['historial_reciente']
		self.assertEqual(historial[0]['partida'].pk, pendiente.pk)
		self.assertTrue(historial[0]['reasignada'])

	def test_gestion_de_torneo_autoriza_y_invita_por_username_sin_email(self):
		url = reverse('gestionar-arbitros-torneo', args=(self.torneo.pk,))
		self.client.force_login(self.jugador)
		self.assertEqual(self.client.get(url).status_code, 404)
		self.client.force_login(self.organizador)
		self.assertEqual(self.client.get(url).status_code, 200)
		respuesta = self.client.post(url, {'username': self.arbitro.username})
		self.assertEqual(respuesta.status_code, 302)
		self.assertTrue(ArbitroTorneo.objects.filter(torneo=self.torneo, usuario=self.arbitro).exists())
		self.assertNotContains(self.client.get(url), self.arbitro.email)
		self.client.post(url, {'username': self.arbitro.username})
		self.assertEqual(ArbitroTorneo.objects.filter(torneo=self.torneo, usuario=self.arbitro).count(), 1)

	def test_admin_puede_gestionar_oficial_y_torneo_privado_sigue_fuera(self):
		ofical = self.crear_torneo(Torneo.Tipo.OFICIAL, 'Oficial web')
		url_oficial = reverse('gestionar-arbitros-torneo', args=(ofical.pk,))
		self.client.force_login(self.organizador)
		self.assertContains(self.client.get(url_oficial), 'Solo un administrador autorizado')
		self.assertEqual(self.client.post(url_oficial, {'username': self.arbitro.username}).status_code, 404)
		self.client.force_login(self.admin)
		self.client.post(url_oficial, {'username': self.arbitro.username})
		self.assertTrue(ArbitroTorneo.objects.filter(torneo=ofical, usuario=self.arbitro).exists())
		privado = self.crear_torneo(Torneo.Tipo.PRIVADO, 'Privado web')
		self.assertEqual(
			self.client.get(reverse('gestionar-arbitros-torneo', args=(privado.pk,))).status_code,
			404,
		)

	def test_paginacion_en_centro_y_gestion_de_arbitros(self):
		for indice in range(21):
			torneo = self.crear_torneo(nombre=f'Página invitación {indice}')
			ArbitroTorneo.objects.create(torneo=torneo, usuario=self.arbitro)
		self.client.force_login(self.arbitro)
		centro_primero = self.client.get(reverse('centro-arbitraje'))
		centro_segundo = self.client.get(reverse('centro-arbitraje'), {'invitaciones_page': 2})
		self.assertEqual(centro_primero.context['invitaciones_pendientes'].paginator.count, 21)
		self.assertEqual(len(centro_primero.context['invitaciones_pendientes']), 20)
		self.assertEqual(len(centro_segundo.context['invitaciones_pendientes']), 1)

		for indice in range(21):
			arbitro = self.crear_usuario(f'gestion_ref_{indice}')
			ArbitroTorneo.objects.create(torneo=self.torneo, usuario=arbitro)
		self.client.force_login(self.organizador)
		url = reverse('gestionar-arbitros-torneo', args=(self.torneo.pk,))
		primera = self.client.get(url)
		segunda = self.client.get(url, {'page': 2})
		self.assertEqual(primera.context['arbitros_page'].paginator.count, 21)
		self.assertEqual(len(primera.context['arbitros_page']), 20)
		self.assertEqual(len(segunda.context['arbitros_page']), 1)

	def test_asignacion_automatica_equilibrada_es_idempotente_y_registra_historial(self):
		self.torneo.estado = Torneo.Estado.PREPARADO
		self.torneo.save(update_fields=('estado',))
		arbitros = [self.crear_usuario(f'balance_{indice}') for indice in range(2)]
		invitaciones = [self.invitar(arbitro=arbitro) for arbitro in arbitros]
		for invitacion, arbitro in zip(invitaciones, arbitros):
			aceptar_invitacion(invitacion, arbitro)
		partidas = [
			Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=indice)
			for indice in range(1, 7)
		]

		asignaciones = asignar_arbitros_pendientes(self.torneo)

		self.assertEqual(len(asignaciones), len(partidas))
		cargas = [ArbitroTorneo.objects.get(pk=invitacion.pk).partidas_asignadas.count() for invitacion in invitaciones]
		self.assertLessEqual(max(cargas) - min(cargas), 1)
		self.assertEqual(HistorialAsignacionArbitro.objects.filter(partida__in=partidas).count(), 6)
		self.assertEqual(asignar_arbitros_pendientes(self.torneo), [])
		self.assertEqual(HistorialAsignacionArbitro.objects.filter(partida__in=partidas).count(), 6)

	def test_asignacion_existente_valida_no_se_sustituye(self):
		self.torneo.estado = Torneo.Estado.PREPARADO
		self.torneo.save(update_fields=('estado',))
		primera = self.crear_usuario('existente_1')
		segunda = self.crear_usuario('existente_2')
		invitacion_1 = self.invitar(arbitro=primera)
		invitacion_2 = self.invitar(arbitro=segunda)
		aceptar_invitacion(invitacion_1, primera)
		aceptar_invitacion(invitacion_2, segunda)
		partida = Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=1)
		asignar_arbitro(partida, arbitro=invitacion_1)
		historial_antes = HistorialAsignacionArbitro.objects.count()

		asignar_arbitros_pendientes(self.torneo)

		partida.refresh_from_db()
		self.assertEqual(partida.arbitro_asignado_id, invitacion_1.pk)
		self.assertEqual(HistorialAsignacionArbitro.objects.count(), historial_antes)

	def test_participante_no_recibe_asignacion_automatica(self):
		self.torneo.estado = Torneo.Estado.PREPARADO
		self.torneo.save(update_fields=('estado',))
		perfil = PerfilVideojuegoUsuario.objects.create(
			usuario=self.arbitro, videojuego=self.videojuego, nick_en_juego='Participante',
		)
		InscripcionTorneo.objects.create(
			torneo=self.torneo, usuario=self.arbitro, perfil_videojuego=perfil,
			nick_historico='Participante', estado=InscripcionTorneo.Estado.CONFIRMADA,
		)
		ArbitroTorneo.objects.create(
			torneo=self.torneo, usuario=self.arbitro,
			estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA,
			activo_en_torneo=True,
		)
		partida = Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=1)

		self.assertEqual(asignar_arbitros_pendientes(self.torneo), [])
		partida.refresh_from_db()
		self.assertIsNone(partida.arbitro_asignado_id)

	def test_sin_arbitros_disponibles_se_conserva_fallback_del_organizador(self):
		from partidas.scheduling import _responsable
		self.torneo.estado = Torneo.Estado.PREPARADO
		self.torneo.save(update_fields=('estado',))
		partida = Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=1)

		self.assertEqual(asignar_arbitros_pendientes(self.torneo), [])
		self.assertEqual(_responsable(partida), self.organizador)
