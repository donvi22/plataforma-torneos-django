from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from arbitraje.models import ArbitroTorneo, HistorialAsignacionArbitro
from arbitraje.services import aceptar_invitacion, invitar_arbitro
from partidas.models import CheckInPartida, Partida
from partidas.scheduling import preparar_partida
from partidas.services import generar_bracket
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import FormatoCompetitivo, InscripcionTorneo, Torneo
from .services import EstadoInscripciones, estado_inscripciones, procesar_calendario


class TemporalInscripcionesTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(username='org', email='org@example.com', password='clave')
        self.videojuego = Videojuego.objects.create(nombre='Juego temporal', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(nombre='Formato temporal', min_participantes=2, max_participantes=128)

    def torneo(self, apertura, cierre, estado=Torneo.Estado.PROXIMAMENTE, prorroga=15):
        return Torneo.objects.create(
            nombre='Temporal', videojuego=self.videojuego, organizador=self.usuario,
            tipo=Torneo.Tipo.PUBLICO, formato_competitivo=self.formato, max_participantes=2,
            estado=estado, fecha_publicacion=apertura - timedelta(hours=1),
            fecha_apertura_inscripciones=apertura, fecha_cierre_inscripciones=cierre,
            duracion_prorroga_min=prorroga,
        )

    def test_antes_durante_y_despues_de_la_ventana(self):
        base = timezone.now()
        torneo = self.torneo(base + timedelta(minutes=5), base + timedelta(minutes=20))
        self.assertEqual(estado_inscripciones(torneo, base), EstadoInscripciones.PROXIMAMENTE)
        self.assertEqual(estado_inscripciones(torneo, base + timedelta(minutes=10)), EstadoInscripciones.ABIERTAS)
        self.assertEqual(estado_inscripciones(torneo, base + timedelta(minutes=21)), EstadoInscripciones.PRORROGA)
        self.assertEqual(estado_inscripciones(torneo, base + timedelta(minutes=36)), EstadoInscripciones.CERRADAS)

    def test_procesador_abre_y_no_reabre_una_ventana_totalmente_vencida(self):
        base = timezone.now()
        torneo = self.torneo(base - timedelta(minutes=40), base - timedelta(minutes=20))
        self.assertEqual(procesar_calendario(ahora=base), 1)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.CANCELADO)
        self.assertEqual(procesar_calendario(ahora=base + timedelta(minutes=1)), 0)
        self.assertEqual(torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.CANCELADO).count(), 1)

    def test_get_de_ficha_no_modifica_estado(self):
        base = timezone.now()
        torneo = self.torneo(base - timedelta(minutes=5), base + timedelta(minutes=10))
        from django.test import Client
        respuesta = Client().get(f'/torneos/{torneo.pk}/')
        self.assertEqual(respuesta.status_code, 200)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.PROXIMAMENTE)

    def test_ficha_muestra_abiertas_si_el_estado_persistido_aun_es_proximamente(self):
        base = timezone.now()
        torneo = self.torneo(base - timedelta(minutes=5), base + timedelta(minutes=10))
        respuesta = self.client.get(f'/torneos/{torneo.pk}/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Inscripciones abiertas')
        self.assertNotContains(respuesta, 'Las inscripciones todavía no han abierto')
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.PROXIMAMENTE)

    def test_oficial_proximamente_se_abre_por_fecha_sin_restricciones_publicas(self):
        base = timezone.now()
        torneo = Torneo.objects.create(
            nombre='Oficial temporal', videojuego=self.videojuego, organizador=self.usuario,
            tipo=Torneo.Tipo.OFICIAL, formato_competitivo=self.formato, max_participantes=2,
            estado=Torneo.Estado.PROXIMAMENTE,
            fecha_publicacion=base - timedelta(hours=2),
            fecha_apertura_inscripciones=base - timedelta(minutes=5),
            fecha_cierre_inscripciones=base + timedelta(hours=2),
        )
        self.assertEqual(estado_inscripciones(torneo, base), EstadoInscripciones.ABIERTAS)
        self.assertEqual(procesar_calendario(ahora=base), 1)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.INSCRIPCIONES_ABIERTAS)

    def test_comando_procesa_una_vez_y_es_idempotente(self):
        base = timezone.now()
        torneo = self.torneo(base - timedelta(minutes=1), base + timedelta(minutes=10))
        from io import StringIO
        salida = StringIO()
        call_command('procesar_calendario', stdout=salida)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.INSCRIPCIONES_ABIERTAS)
        self.assertIn('Torneos actualizados: 1', salida.getvalue())
        salida = StringIO()
        call_command('procesar_calendario', stdout=salida)
        self.assertIn('Torneos actualizados: 0', salida.getvalue())


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ArbitrajeCalendarioTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(
            username='arb_calendar_org', email='arb_calendar_org@example.com', password='test',
        )
        self.arbitro = Usuario.objects.create_user(
            username='arb_calendar_ref', email='arb_calendar_ref@example.com', password='test',
            karma_total=150, disponible_para_arbitrar=True,
        )
        self.videojuego = Videojuego.objects.create(nombre='Arbitraje calendario', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(
            nombre='Formato arbitraje calendario', min_participantes=2, max_participantes=128,
        )
        self.torneo = Torneo.objects.create(
            nombre='Torneo arbitraje calendario', videojuego=self.videojuego,
            organizador=self.organizador, tipo=Torneo.Tipo.PUBLICO,
            formato_competitivo=self.formato, max_participantes=8,
            estado=Torneo.Estado.INSCRIPCIONES_CERRADAS, fecha_publicacion=timezone.now(),
        )
        self.inscripciones = []
        for indice in range(8):
            usuario = Usuario.objects.create_user(
                username=f'arb_calendar_player_{indice}',
                email=f'arb_calendar_player_{indice}@example.com',
                password='test',
            )
            perfil = PerfilVideojuegoUsuario.objects.create(
                usuario=usuario, videojuego=self.videojuego, nick_en_juego=usuario.username,
            )
            self.inscripciones.append(InscripcionTorneo.objects.create(
                torneo=self.torneo, usuario=usuario, perfil_videojuego=perfil,
                nick_historico=usuario.username,
            ))
        invitacion = invitar_arbitro(self.torneo, self.arbitro, self.organizador)
        aceptar_invitacion(invitacion, self.arbitro)
        self.invitacion = invitacion

    def test_procesador_genera_bracket_y_asigna_automaticamente_idempotente(self):
        ahora = timezone.now()

        self.assertEqual(procesar_calendario(ahora=ahora), 1)
        self.torneo.refresh_from_db()
        self.assertEqual(self.torneo.estado, Torneo.Estado.PREPARADO)
        self.assertEqual(self.torneo.partidas.count(), 7)
        self.assertEqual(self.torneo.partidas.filter(numero_ronda=1).count(), 4)
        self.assertEqual(
            self.torneo.partidas.exclude(arbitro_asignado=self.invitacion).count(), 0,
        )
        self.assertEqual(HistorialAsignacionArbitro.objects.filter(partida__torneo=self.torneo).count(), 7)
        self.assertEqual(self.torneo.partidas.filter(numero_ronda=1, estado=Partida.Estado.CHECK_IN).count(), 4)

        fechas_checkin = list(self.torneo.partidas.filter(numero_ronda=1).values_list('pk', 'fecha_hora_apertura_checkin'))
        procesar_calendario(ahora=ahora + timedelta(minutes=1))
        self.assertEqual(HistorialAsignacionArbitro.objects.filter(partida__torneo=self.torneo).count(), 7)
        self.assertEqual(
            list(self.torneo.partidas.filter(numero_ronda=1).values_list('pk', 'fecha_hora_apertura_checkin')),
            fechas_checkin,
        )

    def test_torneo_ya_preparado_y_checkin_abierto_recibe_asignaciones_sin_tocarlo(self):
        generar_bracket(self.torneo)
        self.torneo.refresh_from_db()
        primeras = list(self.torneo.partidas.filter(numero_ronda=1).order_by('numero_orden'))
        for partida in primeras:
            preparar_partida(partida, ahora=timezone.now())
        fechas_checkin = list(self.torneo.partidas.filter(numero_ronda=1).values_list('pk', 'fecha_hora_apertura_checkin'))
        count_checkins = CheckInPartida.objects.filter(partida__torneo=self.torneo).count()

        procesar_calendario()

        self.assertEqual(self.torneo.partidas.filter(arbitro_asignado=self.invitacion).count(), 7)
        self.assertEqual(HistorialAsignacionArbitro.objects.filter(partida__torneo=self.torneo).count(), 7)
        self.assertEqual(
            list(self.torneo.partidas.filter(numero_ronda=1).values_list('pk', 'fecha_hora_apertura_checkin')),
            fechas_checkin,
        )
        self.assertEqual(CheckInPartida.objects.filter(partida__torneo=self.torneo).count(), count_checkins)
        self.assertEqual(
            self.torneo.partidas.filter(numero_ronda=1, estado=Partida.Estado.CHECK_IN).count(), 4,
        )