from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import CheckInPartida, Partida, ParticipantePartida
from .scheduling import confirmar_checkin, preparar_partida


class CheckInWebTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(username='org_checkin', email='org_checkin@example.com', password='ClaveSegura123!')
        self.jugador = Usuario.objects.create_user(username='jugador_checkin', email='jugador_checkin@example.com', password='ClaveSegura123!')
        self.rival = Usuario.objects.create_user(username='rival_checkin', email='rival_checkin@example.com', password='ClaveSegura123!')
        self.ajeno = Usuario.objects.create_user(username='ajeno_checkin', email='ajeno_checkin@example.com', password='ClaveSegura123!')
        self.videojuego = Videojuego.objects.create(nombre='Juego checkin web', genero='Competitivo')
        formato = FormatoCompetitivo.objects.create(nombre='Formato checkin web', min_participantes=2, max_participantes=128)
        self.torneo = Torneo.objects.create(
            nombre='Torneo checkin web', videojuego=self.videojuego, organizador=self.organizador,
            tipo=Torneo.Tipo.PUBLICO, formato_competitivo=formato, max_participantes=2,
            estado=Torneo.Estado.PREPARADO, fecha_publicacion=timezone.now(), duracion_checkin_min=10,
        )
        inscripciones = []
        for usuario in (self.jugador, self.rival):
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=self.videojuego, nick_en_juego=usuario.username)
            inscripciones.append(InscripcionTorneo.objects.create(torneo=self.torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=usuario.username))
        self.partida = Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=1)
        for posicion, inscripcion in enumerate(inscripciones, 1):
            ParticipantePartida.objects.create(partida=self.partida, inscripcion=inscripcion, posicion=posicion)
        preparar_partida(self.partida)
        self.partida.refresh_from_db()

    def test_post_del_participante_confirma_y_get_es_rechazado(self):
        self.client.force_login(self.jugador)
        url = reverse('confirmar-checkin-partida', args=[self.partida.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        respuesta = self.client.post(url)
        self.assertRedirects(respuesta, reverse('detalle-partida', args=[self.partida.pk]))
        checkin = CheckInPartida.objects.get(partida=self.partida, usuario=self.jugador)
        self.assertEqual(checkin.tipo, CheckInPartida.Tipo.PARTICIPANTE)
        self.assertTrue(checkin.confirmado)
        self.assertIsNotNone(checkin.fecha_confirmacion)

    def test_post_exige_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.jugador)
        respuesta = cliente.post(reverse('confirmar-checkin-partida', args=[self.partida.pk]))
        self.assertEqual(respuesta.status_code, 403)

    def test_ajeno_y_checkin_vencido_no_pueden_confirmar(self):
        self.client.force_login(self.ajeno)
        url = reverse('confirmar-checkin-partida', args=[self.partida.pk])
        self.assertEqual(self.client.post(url).status_code, 404)
        self.partida.fecha_hora_apertura_checkin = timezone.now() - timedelta(minutes=11)
        self.partida.save(update_fields=('fecha_hora_apertura_checkin',))
        self.client.force_login(self.jugador)
        self.client.post(url)
        self.assertFalse(CheckInPartida.objects.filter(partida=self.partida, usuario=self.jugador, confirmado=True).exists())

    def test_arbitro_asignado_y_organizador_de_respaldo(self):
        arbitro_usuario = Usuario.objects.create_user(username='arbitro_checkin', email='arbitro_checkin@example.com', password='ClaveSegura123!')
        no_asignado_usuario = Usuario.objects.create_user(username='arbitro_no_asignado', email='arbitro_no_asignado@example.com', password='ClaveSegura123!')
        arbitro = ArbitroTorneo.objects.create(
            torneo=self.torneo, usuario=arbitro_usuario,
            estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, activo_en_torneo=True,
        )
        ArbitroTorneo.objects.create(
            torneo=self.torneo, usuario=no_asignado_usuario,
            estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, activo_en_torneo=True,
        )
        self.partida.arbitro_asignado = arbitro
        self.partida.save(update_fields=('arbitro_asignado',))
        self.client.force_login(arbitro_usuario)
        self.client.post(reverse('confirmar-checkin-partida', args=[self.partida.pk]))
        self.assertTrue(CheckInPartida.objects.filter(partida=self.partida, usuario=arbitro_usuario, confirmado=True).exists())
        self.client.force_login(self.organizador)
        self.assertEqual(self.client.post(reverse('confirmar-checkin-partida', args=[self.partida.pk])).status_code, 404)
        self.client.force_login(no_asignado_usuario)
        self.assertEqual(self.client.post(reverse('confirmar-checkin-partida', args=[self.partida.pk])).status_code, 404)

        otra = Partida.objects.create(torneo=self.torneo, numero_ronda=1, numero_orden=2)
        for posicion, participante in enumerate(self.partida.participantes.all(), 1):
            ParticipantePartida.objects.create(partida=otra, inscripcion=participante.inscripcion, posicion=posicion)
        preparar_partida(otra)
        self.client.force_login(self.organizador)
        self.client.post(reverse('confirmar-checkin-partida', args=[otra.pk]))
        self.assertTrue(CheckInPartida.objects.filter(partida=otra, usuario=self.organizador, confirmado=True).exists())

    def test_completar_checkins_inicia_partida_y_torneo_una_sola_vez(self):
        confirmar_checkin(self.partida, self.jugador, CheckInPartida.Tipo.PARTICIPANTE)
        confirmar_checkin(self.partida, self.rival, CheckInPartida.Tipo.PARTICIPANTE)
        confirmar_checkin(self.partida, self.organizador, CheckInPartida.Tipo.ARBITRO)
        self.partida.refresh_from_db()
        self.torneo.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.EN_CURSO)
        self.assertEqual(self.torneo.estado, Torneo.Estado.EN_CURSO)
        self.assertIsNotNone(self.partida.fecha_hora_inicio_real)
        self.assertEqual(self.torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.EN_CURSO).count(), 1)

    def test_ronda_posterior_no_abre_antes_del_descanso(self):
        self.partida.numero_ronda = 2
        self.partida.estado = Partida.Estado.PENDIENTE
        self.partida.fecha_hora_rivales_confirmados = timezone.now()
        self.partida.fecha_hora_apertura_checkin = None
        self.partida.save(update_fields=('numero_ronda', 'estado', 'fecha_hora_rivales_confirmados', 'fecha_hora_apertura_checkin'))
        preparar_partida(self.partida, ahora=timezone.now() + timedelta(minutes=4))
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.PENDIENTE)


class CheckInPruebaCommandTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(username='org_comando_checkin', email='org_comando_checkin@example.com', password='ClaveSegura123!')
        self.ficticio = Usuario.objects.create_user(username='bot_prueba_checkin', email='bot_prueba_checkin@example.invalid', password='ClaveSegura123!')
        self.real = Usuario.objects.create_user(username='real_comando_checkin', email='real_comando_checkin@example.com', password='ClaveSegura123!')
        videojuego = Videojuego.objects.create(nombre='Juego comando checkin', genero='Competitivo')
        formato = FormatoCompetitivo.objects.create(nombre='Formato comando checkin', min_participantes=2, max_participantes=128)
        torneo = Torneo.objects.create(nombre='Torneo comando checkin', videojuego=videojuego, organizador=self.organizador, tipo=Torneo.Tipo.PUBLICO, formato_competitivo=formato, max_participantes=2, estado=Torneo.Estado.PREPARADO, fecha_publicacion=timezone.now())
        self.partida = Partida.objects.create(torneo=torneo, numero_ronda=1, numero_orden=1)
        for posicion, usuario in enumerate((self.ficticio, self.real), 1):
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=videojuego, nick_en_juego=usuario.username)
            inscripcion = InscripcionTorneo.objects.create(torneo=torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=usuario.username)
            ParticipantePartida.objects.create(partida=self.partida, inscripcion=inscripcion, posicion=posicion)
        preparar_partida(self.partida)

    @override_settings(DEBUG=True, DEV_LOCAL=True)
    def test_comando_confirma_solo_ficticio_participante(self):
        salida = StringIO()
        call_command('confirmar_checkin_prueba', partida=self.partida.pk, usuario=self.ficticio.username, stdout=salida)
        self.assertTrue(CheckInPartida.objects.filter(partida=self.partida, usuario=self.ficticio, confirmado=True).exists())
        self.assertIn('Check-in confirmado', salida.getvalue())
        with self.assertRaises(CommandError):
            call_command('confirmar_checkin_prueba', partida=self.partida.pk, usuario=self.real.username)

    @override_settings(DEBUG=True, DEV_LOCAL=False)
    def test_comando_rechaza_entorno_no_local(self):
        with self.assertRaises(CommandError):
            call_command('confirmar_checkin_prueba', partida=self.partida.pk, usuario=self.ficticio.username)
