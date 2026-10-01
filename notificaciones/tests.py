from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from torneos.models import FormatoCompetitivo, Torneo
from torneos.services import procesar_calendario
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

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
