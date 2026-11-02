from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from partidas.models import Partida, ParticipantePartida, ResultadoPartida
from partidas.services import generar_bracket
from usuarios.models import Usuario
from videojuegos.models import Videojuego

from .models import ClasificacionTorneo, FormatoCompetitivo, InscripcionTorneo, Torneo


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

    def test_presentacion_estados_horario_y_datos_reservados(self):
        torneo = self.crear_torneo(max_participantes=2)
        self._crear_inscripciones(torneo, 2)
        generar_bracket(torneo)
        partida = torneo.partidas.get()
        partida.codigo_lobby = 'CODIGO-RESERVADO'
        partida.contrasena_lobby = 'CLAVE-RESERVADA'
        partida.instrucciones_lobby = 'INSTRUCCIONES-RESERVADAS'
        partida.fecha_hora_programada = timezone.now()
        partida.save(update_fields=('codigo_lobby', 'contrasena_lobby', 'instrucciones_lobby', 'fecha_hora_programada'))
        for estado in Partida.Estado.values:
            with self.subTest(estado=estado):
                Partida.objects.filter(pk=partida.pk).update(estado=estado)
                respuesta = self.client.get(reverse('bracket-torneo', args=[torneo.pk]))
                self.assertContains(respuesta, f'data-state="{estado}"')
                self.assertContains(respuesta, '2/2 participantes')
                self.assertContains(respuesta, 'Volver al torneo')
                self.assertContains(respuesta, '<time>')
                self.assertContains(respuesta, 'tabindex="0" role="region" aria-label="Rondas del bracket"')
                for reservado in ('CODIGO-RESERVADO', 'CLAVE-RESERVADA', 'INSTRUCCIONES-RESERVADAS', self.organizador.email):
                    self.assertNotContains(respuesta, reservado)
                self.assertNotContains(respuesta, 'method="post"')
                partida.refresh_from_db()
                self.assertEqual(partida.estado, estado)

    def test_placeholders_del_backend_y_enlaces_sin_modificar_relaciones(self):
        torneo = self.crear_torneo(max_participantes=4)
        self._crear_inscripciones(torneo, 4)
        generar_bracket(torneo)
        alimentadora = torneo.partidas.filter(numero_ronda=1).first()
        ResultadoPartida.objects.create(
            partida=alimentadora, ganador=None,
            tipo_resultado=ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA,
        )
        estructura = list(torneo.partidas.values('pk', 'estado', 'siguiente_partida_id', 'posicion_en_siguiente_partida'))
        respuesta = self.client.get(reverse('bracket-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'Por determinar')
        self.assertContains(respuesta, 'Sin participante')
        self.assertContains(respuesta, 'Sin marcador')
        for partida in torneo.partidas.all():
            self.assertContains(respuesta, f'href="{reverse("detalle-partida", args=[partida.pk])}"', count=1)
        self.assertEqual(list(torneo.partidas.values('pk', 'estado', 'siguiente_partida_id', 'posicion_en_siguiente_partida')), estructura)

    def test_campeon_y_clasificacion_despues_del_cuadro(self):
        torneo = self.crear_torneo(max_participantes=2)
        self._crear_inscripciones(torneo, 2)
        generar_bracket(torneo)
        ganador = torneo.inscripciones.first()
        ResultadoPartida.objects.create(
            partida=torneo.partidas.get(), ganador=ganador,
            tipo_resultado=ResultadoPartida.TipoResultado.NORMAL, resultado='2-1',
        )
        ClasificacionTorneo.objects.create(torneo=torneo, inscripcion=ganador, posicion=1, es_campeon=True)
        Torneo.objects.filter(pk=torneo.pk).update(estado=Torneo.Estado.FINALIZADO)
        respuesta = self.client.get(reverse('bracket-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'Marcador: <strong>2-1</strong>')
        self.assertContains(respuesta, 'Clasificación final')
        contenido = respuesta.content.decode()
        self.assertLess(contenido.index('class="bracket-scroll"'), contenido.index('class="rk-bracket-champion"'))
        self.assertLess(contenido.index('class="rk-bracket-champion"'), contenido.index('id="standings-title"'))
        self.assertEqual(respuesta.context['campeon'], ganador.nick_historico)
        self.assertEqual(respuesta.context['clasificacion'].count(), 1)
