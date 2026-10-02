from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.urls import reverse
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from arbitraje.services import invitar_arbitro
from partidas.models import Partida
from torneos.models import FormatoCompetitivo, Torneo
from torneos.services import procesar_calendario
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .context_processors import contador_notificaciones
from .models import Notificacion, PreferenciasNotificacion, SeguimientoTorneo
from .services import (
	NotificacionError,
	crear_notificacion,
	dejar_de_seguir_torneo,
	marcar_leida,
	marcar_todas_leidas,
	no_leidas,
	seguir_torneo,
)


class NotificacionesTests(TestCase):
	def setUp(self):
		self.usuario = Usuario.objects.create_user(username='usuario', email='usuario@example.com', password='clave')
		self.otro = Usuario.objects.create_user(username='otro', email='otro@example.com', password='clave')
		videojuego = Videojuego.objects.create(nombre='Juego', genero='Accion')
		formato = FormatoCompetitivo.objects.create(nombre='Formato', min_participantes=2, max_participantes=128)
		self.torneo = Torneo.objects.create(
			nombre='Torneo publico', videojuego=videojuego, organizador=self.usuario,
			tipo=Torneo.Tipo.PUBLICO, formato_competitivo=formato, max_participantes=2,
			estado=Torneo.Estado.PROXIMAMENTE, fecha_publicacion=timezone.now(),
		)

	def test_creacion_deduplicacion_lectura_y_permisos(self):
		primera = crear_notificacion(self.usuario, Notificacion.Tipo.CAMBIO_HORARIO, 'Aviso', 'Mensaje', clave_evento='evento-1', torneo=self.torneo)
		segunda = crear_notificacion(self.usuario, Notificacion.Tipo.CAMBIO_HORARIO, 'Otro', 'Otro', clave_evento='evento-1', torneo=self.torneo)
		self.assertEqual(primera.pk, segunda.pk)
		self.assertEqual(no_leidas(self.usuario), 1)
		marcar_leida(self.usuario, primera)
		primera.refresh_from_db()
		fecha = primera.fecha_lectura
		marcar_leida(self.usuario, primera)
		primera.refresh_from_db()
		self.assertEqual(primera.fecha_lectura, fecha)
		with self.assertRaises(NotificacionError):
			marcar_leida(self.otro, primera)
		crear_notificacion(self.usuario, Notificacion.Tipo.RESULTADO_OFICIAL, 'Resultado', 'Mensaje', es_critica=True)
		self.assertEqual(marcar_todas_leidas(self.usuario), 1)

	def test_preferencias_no_bloquean_criticas(self):
		preferencias = PreferenciasNotificacion.para_usuario(self.usuario)
		preferencias.avisar_novedades_torneos = False
		preferencias.save()
		self.assertIsNone(crear_notificacion(self.usuario, Notificacion.Tipo.CAMBIO_HORARIO, 'Opcional', 'No'))
		critica = crear_notificacion(self.usuario, Notificacion.Tipo.CAMBIO_HORARIO, 'Critica', 'Si', es_critica=True)
		self.assertIsNotNone(critica)

	def test_seguimiento_unico_y_visibilidad(self):
		seguir_torneo(self.otro, self.torneo)
		seguir_torneo(self.otro, self.torneo)
		self.assertEqual(SeguimientoTorneo.objects.filter(usuario=self.otro, torneo=self.torneo).count(), 1)
		dejar_de_seguir_torneo(self.otro, self.torneo)
		privado = Torneo.objects.get(pk=self.torneo.pk)
		privado.tipo = Torneo.Tipo.PRIVADO
		privado.save(update_fields=('tipo',))
		with self.assertRaises(NotificacionError):
			seguir_torneo(self.otro, privado)

	def test_cancelacion_notifica_a_participantes_y_arbitros_sin_duplicar(self):
		participante = Usuario.objects.create_user(
			username='participante', email='participante@example.com', password='clave',
		)
		perfil = PerfilVideojuegoUsuario.objects.create(
			usuario=participante, videojuego=self.torneo.videojuego, nick_en_juego='Participante',
		)
		from torneos.models import InscripcionTorneo
		InscripcionTorneo.objects.create(
			torneo=self.torneo, usuario=participante, perfil_videojuego=perfil,
			nick_historico='Participante', estado=InscripcionTorneo.Estado.CONFIRMADA,
		)
		arbitro = Usuario.objects.create_user(
			username='arbitro', email='arbitro@example.com', password='clave',
		)
		ArbitroTorneo.objects.create(
			torneo=self.torneo, usuario=arbitro,
			estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA,
			activo_en_torneo=True,
		)
		self.torneo.estado = Torneo.Estado.PRORROGA
		self.torneo.fecha_cierre_inscripciones = timezone.now() - timedelta(minutes=20)
		self.torneo.duracion_prorroga_min = 15
		self.torneo.save(update_fields=('estado', 'fecha_cierre_inscripciones', 'duracion_prorroga_min'))

		self.assertEqual(procesar_calendario(timezone.now()), 1)
		self.assertEqual(procesar_calendario(timezone.now()), 0)
		self.assertEqual(Notificacion.objects.filter(tipo=Notificacion.Tipo.CANCELACION_TORNEO).count(), 2)

	def test_notificacion_de_invitacion_arbitral_abre_el_panel(self):
		from arbitraje.services import invitar_arbitro
		self.otro.disponible_para_arbitrar = True
		self.otro.karma_total = 150
		self.otro.save(update_fields=('disponible_para_arbitrar', 'karma_total'))
		invitacion = invitar_arbitro(self.torneo, self.otro, self.usuario)
		notificacion = Notificacion.objects.get(invitacion_arbitral=invitacion)
		self.client.force_login(self.otro)

		respuesta = self.client.post(reverse('abrir-notificacion', args=(notificacion.pk,)))

		self.assertRedirects(respuesta, reverse('centro-arbitraje'), fetch_redirect_response=False)
		notificacion.refresh_from_db()
		self.assertTrue(notificacion.leida)

	def test_notificacion_de_invitacion_abre_el_centro_de_arbitraje(self):
		self.otro.disponible_para_arbitrar = True
		self.otro.karma_total = 150
		self.otro.save(update_fields=('disponible_para_arbitrar', 'karma_total'))
		invitacion = invitar_arbitro(self.torneo, self.otro, self.usuario)
		notificacion = Notificacion.objects.get(invitacion_arbitral=invitacion)
		self.client.force_login(self.otro)

		respuesta = self.client.post(reverse('abrir-notificacion', args=(notificacion.pk,)))

		self.assertRedirects(respuesta, reverse('centro-arbitraje'), fetch_redirect_response=False)
		notificacion.refresh_from_db()
		self.assertTrue(notificacion.leida)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CentroNotificacionesTests(TestCase):
	def setUp(self):
		self.usuario = Usuario.objects.create_user(
			username='centro_usuario', email='centro@example.com', password='clave',
		)
		self.otro = Usuario.objects.create_user(
			username='centro_otro', email='centro_otro@example.com', password='clave',
		)
		videojuego = Videojuego.objects.create(nombre='Centro Juego', genero='Accion')
		formato = FormatoCompetitivo.objects.create(nombre='Centro Formato', min_participantes=2, max_participantes=128)
		self.torneo = Torneo.objects.create(
			nombre='Centro Torneo', videojuego=videojuego, organizador=self.usuario,
			tipo=Torneo.Tipo.PUBLICO, formato_competitivo=formato, max_participantes=2,
			estado=Torneo.Estado.PROXIMAMENTE, fecha_publicacion=timezone.now(),
		)

	def notificacion(self, destinatario=None, *, clave='', torneo=None, partida=None, tipo=None):
		return crear_notificacion(
			destinatario or self.usuario,
			tipo or Notificacion.Tipo.CAMBIO_HORARIO,
			f'Aviso {clave}', f'Mensaje {clave}', clave_evento=clave,
			torneo=torneo, partida=partida,
		)

	def test_contador_oculto_en_cero_y_cuenta_solo_no_leidas(self):
		self.client.force_login(self.usuario)
		url = reverse('centro-notificaciones')
		respuesta = self.client.get(url)
		self.assertEqual(respuesta.context['notificaciones_no_leidas'], 0)
		self.assertNotContains(respuesta, 'notification-count')

		no_leida_1 = self.notificacion(clave='contador-1')
		self.notificacion(clave='contador-2')
		leida = self.notificacion(clave='contador-leida')
		marcar_leida(self.usuario, leida)
		self.notificacion(self.otro, clave='contador-ajena')
		respuesta = self.client.get(url)
		self.assertEqual(respuesta.context['notificaciones_no_leidas'], 2)
		self.assertContains(respuesta, '>2</span>')
		self.assertEqual(no_leidas(self.usuario), 2)
		self.assertFalse(no_leidas(self.otro) == 2)
		self.assertFalse(no_leida_1.leida)

	def test_context_processor_no_consulta_para_usuario_anonimo(self):
		request = RequestFactory().get('/')
		request.user = AnonymousUser()
		with self.assertNumQueries(0):
			self.assertEqual(contador_notificaciones(request), {'notificaciones_no_leidas': 0})

	def test_listado_aislado_ordenado_y_paginado_de_20(self):
		notificaciones = [self.notificacion(clave=f'listado-{indice}') for indice in range(21)]
		Notificacion.objects.filter(pk=notificaciones[0].pk).update(fecha_creacion=timezone.now() - timedelta(days=3))
		self.notificacion(self.otro, clave='ajena-listado')
		self.client.force_login(self.usuario)

		primera = self.client.get(reverse('centro-notificaciones'))
		segunda = self.client.get(reverse('centro-notificaciones'), {'page': 2})
		self.assertEqual(primera.status_code, 200)
		self.assertEqual(primera.context['page_obj'].paginator.count, 21)
		self.assertEqual(len(primera.context['page_obj'].object_list), 20)
		self.assertEqual(len(segunda.context['page_obj'].object_list), 1)
		fechas = [notificacion.fecha_creacion for notificacion in primera.context['page_obj']]
		self.assertEqual(fechas, sorted(fechas, reverse=True))
		self.assertNotContains(primera, 'ajena-listado')
		self.assertContains(primera, 'Siguiente')

	def test_marcar_individual_es_post_idempotente_y_conserva_fecha(self):
		notificacion = self.notificacion(clave='leer-individual')
		self.client.force_login(self.usuario)
		url = reverse('marcar-notificacion-leida', args=(notificacion.pk,))
		respuesta = self.client.post(url)
		self.assertRedirects(respuesta, reverse('centro-notificaciones'), fetch_redirect_response=False)
		notificacion.refresh_from_db()
		fecha_lectura = notificacion.fecha_lectura
		self.assertTrue(notificacion.leida)
		self.assertIsNotNone(fecha_lectura)
		self.client.post(url)
		notificacion.refresh_from_db()
		self.assertEqual(notificacion.fecha_lectura, fecha_lectura)

	def test_marcar_todas_afecta_solo_al_usuario_actual(self):
		propia_1 = self.notificacion(clave='todas-1')
		propia_2 = self.notificacion(clave='todas-2')
		ajena = self.notificacion(self.otro, clave='todas-ajena')
		self.client.force_login(self.usuario)
		respuesta = self.client.post(reverse('marcar-todas-notificaciones-leidas'))
		self.assertRedirects(respuesta, reverse('centro-notificaciones'), fetch_redirect_response=False)
		propia_1.refresh_from_db()
		propia_2.refresh_from_db()
		ajena.refresh_from_db()
		self.assertTrue(propia_1.leida and propia_2.leida)
		self.assertIsNotNone(propia_1.fecha_lectura)
		self.assertFalse(ajena.leida)
		self.assertIsNone(ajena.fecha_lectura)

	def test_usuario_no_puede_abrir_ni_marcar_notificacion_ajena(self):
		ajena = self.notificacion(self.otro, clave='privada-ajena')
		self.client.force_login(self.usuario)
		for nombre in ('abrir-notificacion', 'marcar-notificacion-leida'):
			respuesta = self.client.post(reverse(nombre, args=(ajena.pk,)))
			self.assertEqual(respuesta.status_code, 404)
		ajena.refresh_from_db()
		self.assertFalse(ajena.leida)
		self.assertEqual(self.client.post(reverse('abrir-notificacion', args=(999999,))).status_code, 404)

	def test_usuario_anonimo_va_al_login_y_get_no_cambia_estado(self):
		notificacion = self.notificacion(clave='solo-post')
		respuesta = self.client.get(reverse('centro-notificaciones'))
		self.assertRedirects(
			respuesta,
			f'{reverse("login")}?next={reverse("centro-notificaciones")}',
			fetch_redirect_response=False,
		)
		self.client.force_login(self.usuario)
		self.assertEqual(self.client.get(reverse('abrir-notificacion', args=(notificacion.pk,))).status_code, 405)
		notificacion.refresh_from_db()
		self.assertFalse(notificacion.leida)

	def test_acciones_post_exigen_csrf(self):
		notificacion = self.notificacion(clave='csrf-notificaciones')
		cliente = Client(enforce_csrf_checks=True)
		cliente.force_login(self.usuario)
		self.assertEqual(
			cliente.post(reverse('abrir-notificacion', args=(notificacion.pk,))).status_code,
			403,
		)
		self.assertEqual(
			cliente.post(reverse('marcar-notificacion-leida', args=(notificacion.pk,))).status_code,
			403,
		)
		self.assertEqual(
			cliente.post(reverse('marcar-todas-notificaciones-leidas')).status_code,
			403,
		)
		notificacion.refresh_from_db()
		self.assertFalse(notificacion.leida)

	def test_abrir_destino_real_de_torneo_y_partida(self):
		aviso_torneo = self.notificacion(clave='destino-torneo', torneo=self.torneo)
		partida = Partida.objects.create(
			torneo=self.torneo, numero_ronda=1, numero_orden=1,
		)
		aviso_partida = self.notificacion(clave='destino-partida', partida=partida)
		self.client.force_login(self.usuario)
		for aviso, destino in (
			(aviso_torneo, reverse('ficha-torneo', args=(self.torneo.pk,))),
			(aviso_partida, reverse('detalle-partida', args=(partida.pk,))),
		):
			respuesta = self.client.post(
				reverse('abrir-notificacion', args=(aviso.pk,)),
				{'next': 'https://example.com/externo'},
			)
			self.assertRedirects(respuesta, destino, fetch_redirect_response=False)
			aviso.refresh_from_db()
			self.assertTrue(aviso.leida)

	def test_sin_destino_ignora_next_arbitrario_y_vuelve_al_centro(self):
		aviso = self.notificacion(clave='sin-destino')
		self.client.force_login(self.usuario)
		respuesta = self.client.post(
			reverse('abrir-notificacion', args=(aviso.pk,)),
			{'next': 'https://example.com/externo'},
		)
		self.assertRedirects(respuesta, reverse('centro-notificaciones'), fetch_redirect_response=False)
		aviso.refresh_from_db()
		self.assertTrue(aviso.leida)

	def test_destino_privado_conserva_la_guarda_de_la_vista_existente(self):
		self.torneo.tipo = Torneo.Tipo.PRIVADO
		self.torneo.save(update_fields=('tipo',))
		partida = Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=1)
		aviso = self.notificacion(self.otro, clave='partida-privada', partida=partida)
		self.client.force_login(self.otro)
		respuesta = self.client.post(reverse('abrir-notificacion', args=(aviso.pk,)))
		self.assertRedirects(respuesta, reverse('detalle-partida', args=(partida.pk,)), fetch_redirect_response=False)
		self.assertEqual(self.client.get(respuesta['Location']).status_code, 404)

	def test_integracion_notificacion_real_torneo_y_checkin_partida(self):
		torneo = self.notificacion(
			clave='apertura-torneo-real', torneo=self.torneo,
			tipo=Notificacion.Tipo.APERTURA_INSCRIPCIONES,
		)
		partida = Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=1)
		checkin = crear_notificacion(
			self.usuario, Notificacion.Tipo.APERTURA_CHECKIN, 'Check-in abierto',
			'La partida está lista para confirmar.', es_critica=True,
			clave_evento=f'checkin:{partida.pk}', torneo=self.torneo, partida=partida,
		)
		self.assertEqual(torneo.torneo_id, self.torneo.pk)
		self.assertEqual(checkin.partida_id, partida.pk)
		self.client.force_login(self.usuario)
		self.assertContains(self.client.get(reverse('centro-notificaciones')), 'Check-in abierto')
