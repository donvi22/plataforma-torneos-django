from io import StringIO

from django.contrib import admin
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from arbitraje.services import asignar_arbitro
from partidas.models import Partida, ResultadoPartida
from partidas.services import declarar_resultado, generar_bracket, validar_resultado
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from torneos.services import cerrar_torneo
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import HistorialKarma, Usuario
from .karma import (
    KARMA_RECOMPENSAS,
    KarmaError,
    ajustar_karma,
    conceder_karma_torneo,
    premiar_cierre_torneo,
    registrar_movimiento_karma,
)


class KarmaLedgerTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            username='karma_ledger',
            email='karma_ledger@example.com',
            password=None,
        )

    def test_registro_actualiza_saldo_y_repite_idempotencia(self):
        self.assertEqual(self.usuario.karma_total, 100)

        movimiento = registrar_movimiento_karma(
            self.usuario,
            cantidad=5,
            tipo=HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
            motivo='Completó la competición.',
            clave_idempotencia='participacion:1',
        )
        repetido = registrar_movimiento_karma(
            self.usuario,
            cantidad=5,
            tipo=HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
            motivo='Completó la competición.',
            clave_idempotencia='participacion:1',
        )

        self.usuario.refresh_from_db()
        self.assertEqual(movimiento.pk, repetido.pk)
        self.assertEqual((movimiento.karma_antes, movimiento.karma_despues), (100, 105))
        self.assertEqual(self.usuario.karma_total, 105)
        self.assertEqual(HistorialKarma.objects.count(), 1)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class KarmaEventTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='karma_admin', email='karma_admin@example.com', password='admin-test',
        )
        self.organizador = self.crear_usuario('karma_org')
        self.jugadores = [self.crear_usuario(f'karma_player_{indice}') for indice in range(4)]
        self.arbitro = self.crear_usuario('karma_ref')
        self.videojuego = Videojuego.objects.create(nombre='Karma Game', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(
            nombre='Karma Format', min_participantes=2, max_participantes=128,
        )

    def crear_usuario(self, username):
        return Usuario.objects.create_user(
            username=username, email=f'{username}@example.com', password='test-password',
        )

    def crear_torneo(self, tipo=Torneo.Tipo.PUBLICO, cantidad=2):
        torneo = Torneo.objects.create(
            nombre=f'Karma torneo {tipo} {cantidad}', videojuego=self.videojuego,
            organizador=self.organizador, tipo=tipo, formato_competitivo=self.formato,
            max_participantes=cantidad, estado=Torneo.Estado.INSCRIPCIONES_CERRADAS,
            fecha_publicacion=timezone.now(),
        )
        inscripciones = []
        for indice, usuario in enumerate(self.jugadores[:cantidad]):
            perfil = PerfilVideojuegoUsuario.objects.create(
                usuario=usuario, videojuego=self.videojuego, nick_en_juego=f'karma{indice}',
            )
            inscripciones.append(InscripcionTorneo.objects.create(
                torneo=torneo, usuario=usuario, perfil_videojuego=perfil,
                nick_historico=f'karma{indice}',
            ))
        generar_bracket(torneo)
        torneo.refresh_from_db()
        torneo.estado = Torneo.Estado.EN_CURSO
        torneo.save(update_fields=('estado',))
        partida = torneo.partidas.get(numero_ronda=1, numero_orden=1)
        partida.estado = Partida.Estado.EN_CURSO
        partida.save(update_fields=('estado',))
        return torneo, inscripciones, partida

    def asignar_arbitro(self, torneo, partida):
        asignacion = ArbitroTorneo.objects.create(
            usuario=self.arbitro, torneo=torneo,
            estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA,
            activo_en_torneo=True, fecha_respuesta=timezone.now(),
        )
        asignacion = asignar_arbitro(partida, actor=self.organizador, arbitro=asignacion)
        partida.refresh_from_db()
        return asignacion

    def declarar_y_validar(self, torneo, inscripciones, partida, actor=None, declaraciones=()):
        for usuario, marcador, ganador in declaraciones:
            declarar_resultado(partida, usuario, marcador, ganador)
        with self.captureOnCommitCallbacks(execute=True):
            resultado = validar_resultado(
                partida, actor or self.organizador, '2-1', inscripciones[0],
            )
        torneo.refresh_from_db()
        return resultado

    def test_recompensas_iniciales_estan_centralizadas(self):
        self.assertEqual(self.jugadores[0].karma_total, 100)
        self.assertEqual(KARMA_RECOMPENSAS[HistorialKarma.Tipo.PARTICIPACION_COMPLETADA], 5)
        self.assertEqual(KARMA_RECOMPENSAS[HistorialKarma.Tipo.DECLARACION_VERAZ], 1)
        self.assertEqual(KARMA_RECOMPENSAS[HistorialKarma.Tipo.ARBITRAJE_COMPLETADO], 5)
        self.assertEqual(KARMA_RECOMPENSAS[HistorialKarma.Tipo.ORGANIZACION_COMPLETADA], 10)

    def test_cierre_publico_premia_participacion_y_organizacion_una_vez(self):
        torneo, inscripciones, partida = self.crear_torneo()
        self.declarar_y_validar(torneo, inscripciones, partida)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.FINALIZADO)

        self.jugadores[0].refresh_from_db()
        self.organizador.refresh_from_db()
        self.assertEqual(self.jugadores[0].karma_total, 105)
        self.assertEqual(self.organizador.karma_total, 110)
        self.assertEqual(HistorialKarma.objects.filter(torneo=torneo).count(), 3)
        self.assertEqual(conceder_karma_torneo(torneo), [])
        self.assertEqual(HistorialKarma.objects.filter(torneo=torneo).count(), 3)

    def test_declaraciones_correctas_ganan_un_punto_y_las_incorrectas_no_penalizan(self):
        torneo, inscripciones, partida = self.crear_torneo()
        declaraciones = (
            (self.jugadores[0], '2-1', inscripciones[0]),
            (self.jugadores[1], '1-2', inscripciones[1]),
        )
        self.declarar_y_validar(torneo, inscripciones, partida, declaraciones=declaraciones)

        self.jugadores[0].refresh_from_db()
        self.jugadores[1].refresh_from_db()
        self.assertEqual(self.jugadores[0].karma_total, 106)
        self.assertEqual(self.jugadores[1].karma_total, 105)
        self.assertEqual(
            HistorialKarma.objects.filter(tipo=HistorialKarma.Tipo.DECLARACION_VERAZ).count(), 1,
        )
        self.assertFalse(HistorialKarma.objects.filter(cantidad__lt=0).exists())

    def test_ambas_declaraciones_coincidentes_se_premian_sin_tocar_xp(self):
        torneo, inscripciones, partida = self.crear_torneo()
        declaraciones = tuple(
            (self.jugadores[indice], '2-1', inscripciones[0]) for indice in range(2)
        )
        self.declarar_y_validar(torneo, inscripciones, partida, declaraciones=declaraciones)

        xp_antes_reintento = []
        for jugador in self.jugadores[:2]:
            jugador.refresh_from_db()
            self.assertEqual(jugador.karma_total, 106)
            xp_antes_reintento.append(jugador.xp_total)
        self.assertEqual(conceder_karma_torneo(torneo), [])
        self.assertEqual(
            [Usuario.objects.get(pk=jugador.pk).xp_total for jugador in self.jugadores[:2]],
            xp_antes_reintento,
        )
        self.assertEqual(
            HistorialKarma.objects.filter(tipo=HistorialKarma.Tipo.DECLARACION_VERAZ).count(), 2,
        )

    def test_arbitro_asignado_que_valida_recibe_cinco(self):
        torneo, inscripciones, partida = self.crear_torneo()
        asignacion = self.asignar_arbitro(torneo, partida)
        declaraciones = ((self.jugadores[0], '2-1', inscripciones[0]),)
        self.declarar_y_validar(torneo, inscripciones, partida, self.arbitro, declaraciones)

        self.arbitro.refresh_from_db()
        movimiento = HistorialKarma.objects.get(tipo=HistorialKarma.Tipo.ARBITRAJE_COMPLETADO)
        self.assertEqual(self.arbitro.karma_total, 105)
        self.assertEqual(movimiento.arbitraje_id, asignacion.pk)
        self.assertEqual(movimiento.historial_asignacion_arbitral.arbitro_nuevo_id, asignacion.pk)

    def test_organizador_fallback_no_recibe_recompensa_de_arbitraje(self):
        torneo, inscripciones, partida = self.crear_torneo()
        self.declarar_y_validar(torneo, inscripciones, partida, actor=self.organizador)

        self.organizador.refresh_from_db()
        self.assertEqual(self.organizador.karma_total, 110)
        self.assertFalse(HistorialKarma.objects.filter(
            tipo=HistorialKarma.Tipo.ARBITRAJE_COMPLETADO,
        ).exists())

    def test_torneo_oficial_no_premia_al_organizador_administrativo(self):
        torneo, inscripciones, partida = self.crear_torneo(Torneo.Tipo.OFICIAL)
        self.declarar_y_validar(torneo, inscripciones, partida)
        with self.captureOnCommitCallbacks(execute=True):
            cerrar_torneo(torneo, actor=self.admin)

        self.organizador.refresh_from_db()
        self.assertEqual(self.organizador.karma_total, 100)
        self.assertFalse(HistorialKarma.objects.filter(
            tipo=HistorialKarma.Tipo.ORGANIZACION_COMPLETADA,
        ).exists())

    def test_torneo_privado_no_genera_karma(self):
        torneo, inscripciones, partida = self.crear_torneo(Torneo.Tipo.PRIVADO)
        declarar_resultado(partida, self.jugadores[0], '2-1', inscripciones[0])
        validar_resultado(partida, self.organizador, '2-1', inscripciones[0])
        torneo.refresh_from_db()
        cerrar_torneo(torneo, actor=self.organizador)

        self.assertFalse(HistorialKarma.objects.exists())

    def test_torneo_cancelado_no_genera_recompensa_de_cierre(self):
        torneo, _, _ = self.crear_torneo()
        torneo.estado = Torneo.Estado.CANCELADO
        torneo.save(update_fields=('estado',))

        self.assertEqual(premiar_cierre_torneo(torneo.pk), [])
        self.assertFalse(HistorialKarma.objects.exists())

    def test_comando_historico_es_idempotente(self):
        torneo, inscripciones, partida = self.crear_torneo()
        ResultadoPartida.objects.create(
            partida=partida, resultado='2-1', ganador=inscripciones[0],
            tipo_resultado=ResultadoPartida.TipoResultado.NORMAL, validado_por=self.organizador,
        )
        partida.estado = Partida.Estado.FINALIZADA
        partida.save(update_fields=('estado',))
        torneo.estado = Torneo.Estado.FINALIZADO
        torneo.save(update_fields=('estado',))
        primera_salida = StringIO()
        segunda_salida = StringIO()

        call_command('conceder_karma_torneo', torneo=torneo.pk, stdout=primera_salida)
        cantidad_primera = HistorialKarma.objects.filter(torneo=torneo).count()
        call_command('conceder_karma_torneo', torneo=torneo.pk, stdout=segunda_salida)

        self.assertEqual(cantidad_primera, 3)
        self.assertEqual(HistorialKarma.objects.filter(torneo=torneo).count(), cantidad_primera)
        self.assertIn('0.', segunda_salida.getvalue())

    def test_comando_rechaza_torneo_no_finalizado_y_privado(self):
        torneo, _, _ = self.crear_torneo()
        with self.assertRaises(CommandError):
            call_command('conceder_karma_torneo', torneo=torneo.pk)
        torneo.tipo = Torneo.Tipo.PRIVADO
        torneo.estado = Torneo.Estado.FINALIZADO
        torneo.save(update_fields=('tipo', 'estado'))
        with self.assertRaises(CommandError):
            call_command('conceder_karma_torneo', torneo=torneo.pk)

    def test_participacion_exige_resultado_normal_y_inscripcion_vigente(self):
        torneo, inscripciones, partida = self.crear_torneo(cantidad=4)
        resultado = ResultadoPartida(
            partida=partida, resultado='2-1', ganador=partida.participantes.first().inscripcion,
            tipo_resultado=ResultadoPartida.TipoResultado.NORMAL, validado_por=self.organizador,
        )
        resultado.full_clean()
        resultado.save()
        partida.estado = Partida.Estado.FINALIZADA
        partida.save(update_fields=('estado',))
        participantes_jugada = set(partida.participantes.values_list('inscripcion_id', flat=True))
        sin_participar = [inscripcion for inscripcion in inscripciones if inscripcion.pk not in participantes_jugada]
        sin_participar[0].estado = InscripcionTorneo.Estado.CANCELADA
        sin_participar[0].save(update_fields=('estado',))
        sin_participar[1].estado = InscripcionTorneo.Estado.DESCALIFICADA
        sin_participar[1].save(update_fields=('estado',))
        torneo.estado = Torneo.Estado.FINALIZADO
        torneo.save(update_fields=('estado',))

        conceder_karma_torneo(torneo)

        self.assertEqual(HistorialKarma.objects.filter(
            tipo=HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
        ).count(), 2)
        self.assertFalse(HistorialKarma.objects.filter(inscripcion__in=sin_participar).exists())

    def test_inscripcion_sancionada_no_recibe_recompensa_de_participacion(self):
        torneo, _, partida = self.crear_torneo(cantidad=4)
        resultado = ResultadoPartida(
            partida=partida, resultado='2-1', ganador=partida.participantes.first().inscripcion,
            tipo_resultado=ResultadoPartida.TipoResultado.NORMAL, validado_por=self.organizador,
        )
        resultado.full_clean()
        resultado.save()
        torneo.estado = Torneo.Estado.FINALIZADO
        torneo.save(update_fields=('estado',))
        sancionada = partida.participantes.first().inscripcion
        ajustar_karma(
            sancionada.usuario, -1, 'Infracción confirmada.', self.admin,
            f'sancion:{sancionada.pk}', torneo=torneo, inscripcion=sancionada,
        )

        conceder_karma_torneo(torneo)

        self.assertFalse(HistorialKarma.objects.filter(
            usuario=sancionada.usuario,
            tipo=HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
        ).exists())

    def test_sancion_admin_aplica_hasta_el_suelo_y_conserva_cambio_solicitado(self):
        usuario = self.jugadores[0]
        movimiento = ajustar_karma(usuario, -110, 'Infracción confirmada.', self.admin, 'sancion:suelo')

        usuario.refresh_from_db()
        self.assertEqual(usuario.karma_total, 0)
        self.assertEqual(movimiento.cantidad_solicitada, -110)
        self.assertEqual(movimiento.cantidad, -100)
        self.assertEqual((movimiento.karma_antes, movimiento.karma_despues), (100, 0))
        self.assertEqual(movimiento.tipo, HistorialKarma.Tipo.SANCION_CONFIRMADA)
        self.assertEqual(movimiento.autorizado_por, self.admin)

    def test_sancion_rechaza_usuario_no_autorizado_y_motivo_vacio(self):
        with self.assertRaises(KarmaError):
            ajustar_karma(self.jugadores[0], -1, 'Motivo.', self.organizador, 'sancion:no-admin')
        with self.assertRaises(KarmaError):
            ajustar_karma(self.jugadores[0], -1, '  ', self.admin, 'sancion:sin-motivo')
        self.assertFalse(HistorialKarma.objects.exists())

    def test_admin_no_puede_concederse_karma_y_el_historial_es_inmutable(self):
        with self.assertRaises(KarmaError):
            ajustar_karma(self.admin, 5, 'Autoasignación.', self.admin, 'admin:auto')
        movimiento = registrar_movimiento_karma(
            self.jugadores[0], 5, HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
            'Participación.', 'inmutable:karma',
        )
        movimiento.motivo = 'Editado'
        with self.assertRaises(ValidationError):
            movimiento.save()
        with self.assertRaises(ValidationError):
            movimiento.delete()
        self.assertEqual(HistorialKarma.objects.count(), 1)

    def test_clave_idempotente_no_puede_reutilizarse_para_otro_evento(self):
        usuario = self.jugadores[0]
        registrar_movimiento_karma(
            usuario, 5, HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
            'Participación.', 'idempotencia:conflicto',
        )
        with self.assertRaises(KarmaError):
            registrar_movimiento_karma(
                usuario, 1, HistorialKarma.Tipo.DECLARACION_VERAZ,
                'Declaración.', 'idempotencia:conflicto',
            )
        self.assertEqual(HistorialKarma.objects.count(), 1)

    def test_historial_es_privado_y_admin_solo_lectura(self):
        movimiento = registrar_movimiento_karma(
            self.jugadores[0], 5, HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
            'Completó su torneo.', 'privacidad:karma',
        )
        self.client.force_login(self.jugadores[0])
        respuesta = self.client.get(reverse('historial-karma'))
        self.assertContains(respuesta, 'Completó su torneo.')
        self.assertContains(respuesta, '+5 Karma')
        self.assertContains(respuesta, '105')
        perfil_propio = self.client.get(reverse('perfil', args=[self.jugadores[0].pk]))
        self.assertContains(perfil_propio, 'El Karma refleja fiabilidad')
        self.assertContains(perfil_propio, reverse('historial-karma'))
        self.client.force_login(self.jugadores[1])
        self.assertNotContains(self.client.get(reverse('historial-karma')), 'Completó su torneo.')
        perfil_ajeno = self.client.get(reverse('perfil', args=[self.jugadores[0].pk]))
        self.assertContains(perfil_ajeno, 'Karma')
        self.assertNotContains(perfil_ajeno, reverse('historial-karma'))

        admin_request = RequestFactory().get('/admin/')
        admin_request.user = self.admin
        model_admin = admin.site._registry[HistorialKarma]
        self.assertFalse(model_admin.has_add_permission(admin_request))
        self.assertTrue(model_admin.has_change_permission(admin_request, movimiento))
        self.assertFalse(model_admin.has_delete_permission(admin_request, movimiento))
        self.assertEqual(
            set(model_admin.get_readonly_fields(admin_request, movimiento)),
            {field.name for field in HistorialKarma._meta.fields},
        )

    def test_movimientos_distintos_se_conservan_independientes_y_no_cambian_xp(self):
        usuario = self.jugadores[0]
        xp_antes = usuario.xp_total
        registrar_movimiento_karma(
            usuario, 5, HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
            'Participación.', 'eventos:participacion',
        )
        registrar_movimiento_karma(
            usuario, 1, HistorialKarma.Tipo.DECLARACION_VERAZ,
            'Declaración.', 'eventos:declaracion',
        )

        usuario.refresh_from_db()
        self.assertEqual(usuario.karma_total, 106)
        self.assertEqual(usuario.xp_total, xp_antes)
        self.assertEqual(HistorialKarma.objects.filter(usuario=usuario).count(), 2)