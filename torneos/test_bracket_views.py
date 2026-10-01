from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from partidas.models import Partida, ParticipantePartida, ResultadoPartida
from partidas.services import generar_bracket
from usuarios.models import Usuario
from videojuegos.models import Videojuego

from .models import FormatoCompetitivo, InscripcionTorneo, Torneo


class BracketViewsTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(username='orgbracket', email='orgbracket@example.com', password='ClaveSegura123!')
        self.visitante = Usuario.objects.create_user(username='visitantebracket', email='visitantebracket@example.com', password='ClaveSegura123!')
        self.game = Videojuego.objects.create(nombre='Juego bracket', genero='Competitivo')
        self.format = FormatoCompetitivo.objects.create(nombre='Formato bracket', min_participantes=2, max_participantes=128)

    def crear_torneo(self, tipo=Torneo.Tipo.PUBLICO, estado=Torneo.Estado.INSCRIPCIONES_CERRADAS, max_participantes=8):
        return Torneo.objects.create(
            nombre=f'Torneo {Torneo.objects.count()}', videojuego=self.game, organizador=self.organizador,
            tipo=tipo, formato_competitivo=self.format, max_participantes=max_participantes,
            estado=estado, fecha_publicacion=timezone.now(),
        )

    def test_sin_bracket_muestra_mensaje_y_no_crea_partidas(self):
        torneo = self.crear_torneo()
        respuesta = self.client.get(reverse('bracket-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'cuadro todavía no está disponible')
        self.assertEqual(Partida.objects.filter(torneo=torneo).count(), 0)

    def test_ficha_tiene_un_solo_enlace_al_bracket(self):
        torneo = self.crear_torneo()
        self._crear_inscripciones(torneo, 8)
        generar_bracket(torneo)
        respuesta = self.client.get(reverse('ficha-torneo', args=[torneo.pk]))
        self.assertEqual(respuesta.content.count(b'Ver bracket'), 1)

    def test_publico_y_oficial_publicados_pueden_consultar(self):
        for tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL):
            torneo = self.crear_torneo(tipo=tipo)
            self.assertEqual(self.client.get(reverse('bracket-torneo', args=[torneo.pk])).status_code, 200)

    def test_privado_y_borrador_estan_protegidos(self):
        privado = self.crear_torneo(tipo=Torneo.Tipo.PRIVADO, estado=Torneo.Estado.BORRADOR)
        self.assertEqual(self.client.get(reverse('bracket-torneo', args=[privado.pk])).status_code, 404)
        self.client.force_login(self.organizador)
        self.assertEqual(self.client.get(reverse('bracket-torneo', args=[privado.pk])).status_code, 200)

    def test_muestra_rondas_participantes_historicos_y_resultado(self):
        torneo = self.crear_torneo()
        self._crear_inscripciones(torneo, 8)
        generar_bracket(torneo)
        respuesta = self.client.get(reverse('bracket-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'Final')
        self.assertContains(respuesta, 'Semifinales')
        self.assertContains(respuesta, 'Nick histórico 0')
        self.assertNotContains(respuesta, 'contraseña')
        self.assertEqual(respuesta.content.count(b'bracket-round'), 3)
        self.assertEqual(respuesta.content.count(b'match-card'), 7)
        self.assertEqual(respuesta.content.count(b'match-slot'), 14)
        self.assertContains(respuesta, reverse('detalle-partida', args=[torneo.partidas.first().pk]))

    def _crear_inscripciones(self, torneo, cantidad):
        for index in range(cantidad):
            usuario = Usuario.objects.create_user(username=f'jugadorbracket{index}', email=f'jugadorbracket{index}@example.com', password='ClaveSegura123!')
            from videojuegos.models import PerfilVideojuegoUsuario
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=self.game, nick_en_juego=f'Nick{index}')
            InscripcionTorneo.objects.create(torneo=torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=f'Nick histórico {index}')

    def test_resultado_oficial_muestra_ganador_y_no_declaracion(self):
        torneo = self.crear_torneo(max_participantes=4)
        inscripciones = []
        for index in range(4):
            usuario = Usuario.objects.create_user(username=f'ganador{index}', email=f'ganador{index}@example.com', password='ClaveSegura123!')
            from videojuegos.models import PerfilVideojuegoUsuario
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=self.game, nick_en_juego=f'Nick{index}')
            inscripciones.append(InscripcionTorneo.objects.create(torneo=torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=f'Nick histórico {index}'))
        generar_bracket(torneo)
        final = Partida.objects.get(torneo=torneo, numero_ronda=2)
        ganador = inscripciones[0]
        ParticipantePartida.objects.create(partida=final, inscripcion=ganador, posicion=1)
        ParticipantePartida.objects.create(partida=final, inscripcion=inscripciones[1], posicion=2)
        ResultadoPartida.objects.create(partida=final, ganador=ganador, tipo_resultado=ResultadoPartida.TipoResultado.NORMAL, resultado='2-0')
        respuesta = self.client.get(reverse('bracket-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'Nick histórico 0')
        self.assertContains(respuesta, 'Campeón')
