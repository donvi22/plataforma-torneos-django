from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from arbitraje.services import aceptar_invitacion, invitar_arbitro
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from torneos.services import procesar_calendario
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import CheckInPartida, Partida
from .scheduling import (
    CheckInError,
    confirmar_checkin,
    detectar_ausencias,
    iniciar_partida,
    preparar_partida,
    procesar_checkins,
    reprogramar_partida,
)
from .services import ResultadoError, validar_resultado


class CheckInSchedulingTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_superuser(
            username='organizador',
            email='organizador@example.com',
            password='clave-segura-123',
        )
        self.videojuego = Videojuego.objects.create(nombre='Arena Battle', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(
            nombre='Eliminacion directa', min_participantes=2, max_participantes=128,
        )

    def crear_partida(self, tipo=Torneo.Tipo.PUBLICO, estado=Torneo.Estado.PREPARADO):
        torneo = Torneo.objects.create(
            nombre=f'Torneo {Torneo.objects.count()}',
            videojuego=self.videojuego,
            organizador=self.organizador,
            tipo=tipo,
            formato_competitivo=self.formato,
            max_participantes=2,
            estado=estado,
            duracion_checkin_min=10,
            descanso_entre_partidas_min=5,
        )
        participantes = []
        for indice in range(2):
            usuario = Usuario.objects.create_user(
                username=f'jugador{Torneo.objects.count()}_{indice}',
                email=f'jugador{Torneo.objects.count()}_{indice}@example.com',
                password='clave-segura-123',
            )
            perfil = PerfilVideojuegoUsuario.objects.create(
                usuario=usuario, videojuego=self.videojuego, nick_en_juego=usuario.username,
            )
            participantes.append(InscripcionTorneo.objects.create(
                torneo=torneo, usuario=usuario, perfil_videojuego=perfil,
                nick_historico=usuario.username, estado=InscripcionTorneo.Estado.CONFIRMADA,
            ))
        partida = Partida.objects.create(torneo=torneo, numero_ronda=1, numero_orden=1)
        from .models import ParticipantePartida
        for posicion, inscripcion in enumerate(participantes, 1):
            ParticipantePartida.objects.create(partida=partida, inscripcion=inscripcion, posicion=posicion)
        return torneo, partida, participantes

    def test_publica_abre_checkin_y_permite_inicio_anticipado(self):
        torneo, partida, participantes = self.crear_partida()
        preparar_partida(partida, ahora=timezone.now() + timedelta(minutes=6))
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.CHECK_IN)
        confirmar_checkin(partida, participantes[0].usuario, CheckInPartida.Tipo.PARTICIPANTE)
        confirmar_checkin(partida, participantes[1].usuario, CheckInPartida.Tipo.PARTICIPANTE)
        confirmar_checkin(partida, self.organizador, CheckInPartida.Tipo.ARBITRO)
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.EN_CURSO)
        self.assertIsNotNone(partida.fecha_hora_inicio_real)

    def test_primera_ronda_publica_abre_sin_descanso_adicional(self):
        _, partida, _ = self.crear_partida()
        preparar_partida(partida, ahora=timezone.now())
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.CHECK_IN)
        self.assertIsNotNone(partida.fecha_hora_apertura_checkin)

    def test_primera_ronda_no_abre_antes_de_preparar_el_torneo(self):
        torneo, partida, _ = self.crear_partida(estado=Torneo.Estado.INSCRIPCIONES_CERRADAS)
        with self.assertRaises(CheckInError):
            preparar_partida(partida)
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.PENDIENTE)

    def test_procesar_calendario_abre_checkin_de_primera_ronda_preparada(self):
        torneo, partida, _ = self.crear_partida()
        self.assertEqual(procesar_calendario(), 0)
        partida.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.PREPARADO)
        self.assertEqual(partida.estado, Partida.Estado.CHECK_IN)

    def test_rechaza_checkin_antes_de_abrir_y_duplicado(self):
        torneo, partida, participantes = self.crear_partida()
        partida.numero_ronda = 2
        partida.fecha_hora_rivales_confirmados = timezone.now()
        partida.save(update_fields=('numero_ronda', 'fecha_hora_rivales_confirmados'))
        preparar_partida(partida, ahora=timezone.now() - timedelta(minutes=1))
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.PENDIENTE)
        preparar_partida(partida, ahora=timezone.now() + timedelta(minutes=6))
        confirmar_checkin(partida, participantes[0].usuario, CheckInPartida.Tipo.PARTICIPANTE)
        with self.assertRaises(CheckInError):
            confirmar_checkin(partida, participantes[0].usuario, CheckInPartida.Tipo.PARTICIPANTE)
        with self.assertRaises(CheckInError):
            confirmar_checkin(partida, self.organizador, CheckInPartida.Tipo.PARTICIPANTE)

    def test_publica_ronda_posterior_respeta_descanso(self):
        torneo, partida, _ = self.crear_partida()
        partida.numero_ronda = 2
        partida.fecha_hora_rivales_confirmados = timezone.now()
        partida.save(update_fields=('numero_ronda', 'fecha_hora_rivales_confirmados'))
        preparar_partida(partida, ahora=timezone.now() + timedelta(minutes=4))
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.PENDIENTE)
        preparar_partida(partida, ahora=timezone.now() + timedelta(minutes=6))
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.CHECK_IN)

    def test_oficial_abre_diez_minutos_antes_y_no_se_adelanta(self):
        torneo, partida, participantes = self.crear_partida(Torneo.Tipo.OFICIAL)
        programada = timezone.now() + timedelta(hours=1)
        partida.fecha_hora_programada = programada
        partida.save(update_fields=('fecha_hora_programada',))
        preparar_partida(partida, ahora=programada - timedelta(minutes=10))
        confirmar_checkin(partida, participantes[0].usuario, CheckInPartida.Tipo.PARTICIPANTE, ahora=programada - timedelta(minutes=9))
        confirmar_checkin(partida, participantes[1].usuario, CheckInPartida.Tipo.PARTICIPANTE, ahora=programada - timedelta(minutes=9))
        confirmar_checkin(partida, self.organizador, CheckInPartida.Tipo.ARBITRO, ahora=programada - timedelta(minutes=9))
        partida.refresh_from_db()
        with self.assertRaises(CheckInError):
            iniciar_partida(partida, ahora=programada - timedelta(minutes=1))
        iniciar_partida(partida, ahora=programada)
        partida.refresh_from_db()
        self.assertEqual(partida.estado, Partida.Estado.EN_CURSO)

    def test_privado_no_exige_checkin(self):
        _, partida, _ = self.crear_partida(Torneo.Tipo.PRIVADO)
        self.assertEqual(preparar_partida(partida).estado, Partida.Estado.PENDIENTE)
        with self.assertRaises(CheckInError):
            confirmar_checkin(partida, self.organizador, CheckInPartida.Tipo.ARBITRO)

    def test_detecta_ausencias_sin_resolver_resultado(self):
        _, partida, participantes = self.crear_partida()
        ahora = timezone.now()
        preparar_partida(partida, ahora=ahora)
        confirmar_checkin(partida, participantes[0].usuario, CheckInPartida.Tipo.PARTICIPANTE, ahora=ahora)
        resultado = detectar_ausencias(partida, ahora=ahora + timedelta(minutes=11))
        self.assertEqual(resultado['participantes_ausentes'], [participantes[1].usuario_id])
        self.assertTrue(resultado['arbitro_ausente'])
        partida.refresh_from_db()
        self.assertFalse(hasattr(partida, 'resultado_oficial'))

    def test_resultado_normal_publico_requiere_partida_en_curso(self):
        torneo, partida, participantes = self.crear_partida()
        with self.assertRaises(ResultadoError):
            validar_resultado(partida, self.organizador, '1-0', participantes[0])

    def test_reprogramacion_oficial_guarda_historial(self):
        torneo, partida, _ = self.crear_partida(Torneo.Tipo.OFICIAL)
        partida.fecha_hora_programada = timezone.now() + timedelta(hours=1)
        partida.save(update_fields=('fecha_hora_programada',))
        nueva = timezone.now() + timedelta(hours=2)
        reprogramar_partida(partida, nueva, self.organizador, 'Cambio de jornada')
        self.assertEqual(partida.historial_programacion.count(), 1)
        partida.refresh_from_db()
        self.assertEqual(partida.fecha_hora_programada, nueva)

    def test_procesador_temporal_es_idempotente(self):
        _, partida, _ = self.crear_partida()
        self.assertEqual(procesar_checkins(), 1)
        self.assertEqual(procesar_checkins(), 0)
