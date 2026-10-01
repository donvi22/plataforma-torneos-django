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

from .models import DeclaracionResultado, Partida
from .services import declarar_resultado, generar_bracket


class ResultadoWebTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(username='org_resultado', email='org_resultado@example.com', password='ClaveSegura123!')
        self.ajeno = Usuario.objects.create_user(username='ajeno_resultado', email='ajeno_resultado@example.com', password='ClaveSegura123!')
        juego = Videojuego.objects.create(nombre='Juego resultado web', genero='Competitivo')
        formato = FormatoCompetitivo.objects.create(nombre='Formato resultado web', min_participantes=2, max_participantes=128)
        self.torneo = Torneo.objects.create(
            nombre='Torneo resultado web', videojuego=juego, organizador=self.organizador,
            tipo=Torneo.Tipo.PUBLICO, formato_competitivo=formato, max_participantes=4,
            estado=Torneo.Estado.INSCRIPCIONES_CERRADAS, fecha_publicacion=timezone.now(),
        )
        self.inscripciones = []
        for indice in range(4):
            usuario = Usuario.objects.create_user(username=f'jugador_resultado_{indice}', email=f'jugador_resultado_{indice}@example.com', password='ClaveSegura123!')
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=juego, nick_en_juego=f'Nick {indice}')
            self.inscripciones.append(InscripcionTorneo.objects.create(torneo=self.torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=f'Nick {indice}'))
        generar_bracket(self.torneo)
        self.torneo.estado = Torneo.Estado.EN_CURSO
        self.torneo.save(update_fields=('estado',))
        self.partida = self.torneo.partidas.get(numero_ronda=1, numero_orden=1)
        self.partida.estado = Partida.Estado.EN_CURSO
        self.partida.save(update_fields=('estado',))
        self.jugador = self.partida.participantes.get(posicion=1).inscripcion.usuario
        self.rival = self.partida.participantes.get(posicion=2).inscripcion.usuario
        self.ganador = self.partida.participantes.get(posicion=1).inscripcion

    def declarar_url(self):
        return reverse('declarar-resultado-partida', args=[self.partida.pk])

    def validar_url(self):
        return reverse('validar-resultado-partida', args=[self.partida.pk])

    def test_participante_declara_por_post_y_pasa_a_revision_sin_avanzar(self):
        self.client.force_login(self.jugador)
        ficha = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertContains(ficha, 'Declarar resultado')
        self.assertContains(ficha, reverse('declarar-resultado-partida', args=[self.partida.pk]))
        self.assertContains(ficha, 'Enviar resultado')
        respuesta = self.client.post(self.declarar_url(), {'resultado': '2-1', 'ganador': self.ganador.pk})
        self.assertRedirects(respuesta, reverse('detalle-partida', args=[self.partida.pk]))
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.PENDIENTE_VALIDACION)
        self.assertFalse(hasattr(self.partida, 'resultado_oficial'))
        self.assertEqual(self.partida.siguiente_partida.participantes.count(), 0)
        self.assertEqual(DeclaracionResultado.objects.get(partida=self.partida).resultado_declarado, '2-1')
        ficha = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertContains(ficha, 'Pendiente de validación')
        self.assertNotContains(ficha, 'Enviar resultado')
        self.client.force_login(self.organizador)
        ficha = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertContains(ficha, 'Validar resultado oficial')
        self.assertEqual(self.client.get(self.declarar_url()).status_code, 404)

    def test_csrf_y_declaraciones_no_validas_se_rechazan(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.jugador)
        self.assertEqual(cliente.post(self.declarar_url(), {'resultado': '2-1', 'ganador': self.ganador.pk}).status_code, 403)
        self.client.force_login(self.jugador)
        self.client.post(self.declarar_url(), {'resultado': 'dos a uno', 'ganador': self.ganador.pk})
        self.assertFalse(DeclaracionResultado.objects.filter(partida=self.partida).exists())
        self.client.force_login(self.ajeno)
        self.assertEqual(self.client.post(self.declarar_url(), {'resultado': '2-1', 'ganador': self.ganador.pk}).status_code, 404)

    def test_responsable_ve_coincidencia_y_valida_avanzando(self):
        declarar_resultado(self.partida, self.jugador, '2-1', self.ganador)
        declarar_resultado(self.partida, self.rival, '2-1', self.ganador)
        self.client.force_login(self.organizador)
        respuesta = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertContains(respuesta, 'Las dos declaraciones coinciden.')
        self.assertContains(respuesta, 'Validar resultado oficial')
        self.client.post(self.validar_url(), {'resultado': '2-1', 'ganador': self.ganador.pk, 'motivo': ''})
        self.partida.refresh_from_db()
        siguiente = self.partida.siguiente_partida
        self.assertEqual(self.partida.estado, Partida.Estado.FINALIZADA)
        self.assertEqual(self.partida.resultado_oficial.ganador, self.ganador)
        self.assertEqual(siguiente.participantes.count(), 1)
        self.client.force_login(self.jugador)
        respuesta = self.client.get(reverse('mis-partidas'))
        self.assertContains(respuesta, reverse('detalle-partida', args=[siguiente.pk]))

    def test_discrepancia_exige_motivo_y_no_valida_por_jugador(self):
        declarar_resultado(self.partida, self.jugador, '2-1', self.ganador)
        ganador_rival = self.partida.participantes.get(posicion=2).inscripcion
        declarar_resultado(self.partida, self.rival, '1-2', ganador_rival)
        self.client.force_login(self.organizador)
        respuesta = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertContains(respuesta, 'Existe una discrepancia')
        self.client.post(self.validar_url(), {'resultado': '2-1', 'ganador': self.ganador.pk, 'motivo': ''})
        self.partida.refresh_from_db()
        self.assertFalse(hasattr(self.partida, 'resultado_oficial'))
        self.client.force_login(self.jugador)
        self.assertEqual(self.client.post(self.validar_url(), {'resultado': '2-1', 'ganador': self.ganador.pk, 'motivo': 'x'}).status_code, 404)

    def test_validacion_rechaza_marcador_invalido(self):
        declarar_resultado(self.partida, self.jugador, '2-1', self.ganador)
        declarar_resultado(self.partida, self.rival, '2-1', self.ganador)
        self.client.force_login(self.organizador)
        self.client.post(self.validar_url(), {'resultado': 'resultado libre', 'ganador': self.ganador.pk})
        self.partida.refresh_from_db()
        self.assertFalse(hasattr(self.partida, 'resultado_oficial'))

    def test_arbitro_asignado_puede_validar_y_otro_no(self):
        arbitro_usuario = Usuario.objects.create_user(username='arbitro_resultado', email='arbitro_resultado@example.com', password='ClaveSegura123!')
        otro_usuario = Usuario.objects.create_user(username='arbitro_ajeno_resultado', email='arbitro_ajeno_resultado@example.com', password='ClaveSegura123!')
        arbitro = ArbitroTorneo.objects.create(torneo=self.torneo, usuario=arbitro_usuario, estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, activo_en_torneo=True)
        ArbitroTorneo.objects.create(torneo=self.torneo, usuario=otro_usuario, estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, activo_en_torneo=True)
        self.partida.arbitro_asignado = arbitro
        self.partida.save(update_fields=('arbitro_asignado',))
        declarar_resultado(self.partida, self.jugador, '2-1', self.ganador)
        declarar_resultado(self.partida, self.rival, '2-1', self.ganador)
        self.client.force_login(otro_usuario)
        self.assertEqual(self.client.post(self.validar_url(), {'resultado': '2-1', 'ganador': self.ganador.pk}).status_code, 404)
        self.client.force_login(arbitro_usuario)
        self.client.post(self.validar_url(), {'resultado': '2-1', 'ganador': self.ganador.pk})
        self.partida.refresh_from_db()
        self.assertTrue(hasattr(self.partida, 'resultado_oficial'))


class DeclaracionPruebaCommandTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(username='org_cmd_resultado', email='org_cmd_resultado@example.com', password='ClaveSegura123!')
        self.ficticio = Usuario.objects.create_user(username='bot_prueba_resultado', email='bot_prueba_resultado@example.invalid', password='ClaveSegura123!')
        self.real = Usuario.objects.create_user(username='real_cmd_resultado', email='real_cmd_resultado@example.com', password='ClaveSegura123!')
        juego = Videojuego.objects.create(nombre='Juego cmd resultado', genero='Competitivo')
        formato = FormatoCompetitivo.objects.create(nombre='Formato cmd resultado', min_participantes=2, max_participantes=128)
        torneo = Torneo.objects.create(nombre='Torneo cmd resultado', videojuego=juego, organizador=self.organizador, tipo=Torneo.Tipo.PUBLICO, formato_competitivo=formato, max_participantes=2, estado=Torneo.Estado.EN_CURSO, fecha_publicacion=timezone.now())
        inscripciones = []
        for usuario in (self.ficticio, self.real):
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=juego, nick_en_juego=usuario.username)
            inscripciones.append(InscripcionTorneo.objects.create(torneo=torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=usuario.username))
        self.partida = Partida.objects.create(torneo=torneo, numero_ronda=1, numero_orden=1, estado=Partida.Estado.EN_CURSO)
        from .models import ParticipantePartida
        for posicion, inscripcion in enumerate(inscripciones, 1):
            ParticipantePartida.objects.create(partida=self.partida, inscripcion=inscripcion, posicion=posicion)
        self.ganador = inscripciones[0]

    @override_settings(DEBUG=True, DEV_LOCAL=True)
    def test_comando_declara_solo_por_cuenta_ficticia(self):
        salida = StringIO()
        call_command('declarar_resultado_prueba', partida=self.partida.pk, usuario=self.ficticio.username, resultado='2-1', ganador=self.ganador.pk, stdout=salida)
        self.assertTrue(DeclaracionResultado.objects.filter(partida=self.partida, usuario=self.ficticio).exists())
        self.assertIn('Resultado declarado', salida.getvalue())
        with self.assertRaises(CommandError):
            call_command('declarar_resultado_prueba', partida=self.partida.pk, usuario=self.real.username, resultado='2-1', ganador=self.ganador.pk)

    @override_settings(DEBUG=True, DEV_LOCAL=False)
    def test_comando_rechaza_entorno_no_local(self):
        with self.assertRaises(CommandError):
            call_command('declarar_resultado_prueba', partida=self.partida.pk, usuario=self.ficticio.username, resultado='2-1', ganador=self.ganador.pk)
