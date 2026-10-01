from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import CheckInPartida, Partida
from .scheduling import confirmar_checkin, preparar_partida
from .services import generar_bracket, validar_resultado


class ProgresionCompletaTorneoPublicoTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(
            username='organizador_progresion', email='organizador_progresion@example.com', password='ClaveSegura123!',
        )
        juego = Videojuego.objects.create(nombre='Juego progresion', genero='Competitivo')
        formato = FormatoCompetitivo.objects.create(nombre='Formato progresion', min_participantes=2, max_participantes=128)
        self.torneo = Torneo.objects.create(
            nombre='Torneo progresion', videojuego=juego, organizador=self.organizador,
            tipo=Torneo.Tipo.PUBLICO, formato_competitivo=formato, max_participantes=8,
            estado=Torneo.Estado.INSCRIPCIONES_CERRADAS, duracion_checkin_min=10,
            descanso_entre_partidas_min=5,
        )
        for indice in range(8):
            usuario = Usuario.objects.create_user(
                username=f'jugador_progresion_{indice}', email=f'progresion_{indice}@example.com', password='ClaveSegura123!',
            )
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=juego, nick_en_juego=usuario.username)
            InscripcionTorneo.objects.create(
                torneo=self.torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=usuario.username,
            )
        generar_bracket(self.torneo)
        self.torneo.refresh_from_db()
        self.ahora = timezone.now()

    def _abrir_confirmar_y_validar(self, partida, ahora):
        preparar_partida(partida, ahora=ahora)
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.CHECK_IN)
        for participante in partida.participantes.select_related('inscripcion__usuario'):
            confirmar_checkin(partida, participante.inscripcion.usuario, CheckInPartida.Tipo.PARTICIPANTE, ahora=ahora)
        confirmar_checkin(partida, self.organizador, CheckInPartida.Tipo.ARBITRO, ahora=ahora)
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.EN_CURSO)
        ganador = partida.participantes.get(posicion=1).inscripcion
        validar_resultado(partida, self.organizador, '2-1', ganador)
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.FINALIZADA)
        return ganador

    def test_avance_controlado_hasta_final_respeta_descansos_y_no_duplica(self):
        primeras = list(self.torneo.partidas.filter(numero_ronda=1).order_by('numero_orden'))

        # Abrir todas las partidas iniciales antes de que el primer inicio cambie el torneo a EN_CURSO.
        for partida in primeras:
            preparar_partida(partida, ahora=self.ahora)
        for partida in primeras:
            for participante in partida.participantes.select_related('inscripcion__usuario'):
                confirmar_checkin(partida, participante.inscripcion.usuario, CheckInPartida.Tipo.PARTICIPANTE, ahora=self.ahora)
            confirmar_checkin(partida, self.organizador, CheckInPartida.Tipo.ARBITRO, ahora=self.ahora)
            partida.refresh_from_db()
            self.assertEqual(partida.estado, Partida.Estado.EN_CURSO)

        ganadores = []
        for partida in primeras:
            ganador = partida.participantes.get(posicion=1).inscripcion
            validar_resultado(partida, self.organizador, '2-1', ganador)
            ganadores.append(ganador)

        semifinales = list(self.torneo.partidas.filter(numero_ronda=2).order_by('numero_orden'))
        for semifinal in semifinales:
            semifinal.refresh_from_db()
            self.assertEqual(semifinal.participantes.count(), 2)
            self.assertIsNotNone(semifinal.fecha_hora_rivales_confirmados)
            preparar_partida(semifinal, ahora=semifinal.fecha_hora_rivales_confirmados + timedelta(minutes=4))
            semifinal.refresh_from_db()
            self.assertEqual(semifinal.estado, Partida.Estado.PENDIENTE)

        for semifinal in semifinales:
            momento = semifinal.fecha_hora_rivales_confirmados + timedelta(minutes=6)
            self._abrir_confirmar_y_validar(semifinal, momento)

        final = self.torneo.partidas.get(numero_ronda=3, numero_orden=1)
        final.refresh_from_db()
        self.assertEqual(final.participantes.count(), 2)
        self.assertIsNotNone(final.fecha_hora_rivales_confirmados)
        preparar_partida(final, ahora=final.fecha_hora_rivales_confirmados + timedelta(minutes=4))
        final.refresh_from_db()
        self.assertEqual(final.estado, Partida.Estado.PENDIENTE)

        ganador_final = self._abrir_confirmar_y_validar(
            final,
            final.fecha_hora_rivales_confirmados + timedelta(minutes=6),
        )
        final.refresh_from_db()
        self.assertEqual(final.resultado_oficial.ganador, ganador_final)
        self.assertIsNone(final.siguiente_partida)
        self.assertEqual(self.torneo.partidas.filter(resultado_oficial__isnull=False).count(), 7)
        self.torneo.refresh_from_db()
        self.assertEqual(self.torneo.estado, Torneo.Estado.FINALIZADO)
        self.assertEqual(self.torneo.clasificaciones.count(), 8)
