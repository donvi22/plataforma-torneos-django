from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import CheckInPartida, Partida, ParticipantePartida


class PartidaViewsTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(username='orgpartida', email='orgpartida@example.com', password='ClaveSegura123!')
        self.jugador = Usuario.objects.create_user(username='jugadorpartida', email='jugadorpartida@example.com', password='ClaveSegura123!')
        self.visitante = Usuario.objects.create_user(username='visitantepartida', email='visitantepartida@example.com', password='ClaveSegura123!')
        self.game = Videojuego.objects.create(nombre='Juego partidas', genero='Competitivo')
        self.format = FormatoCompetitivo.objects.create(nombre='Formato partidas', min_participantes=2, max_participantes=128)
        self.torneo = Torneo.objects.create(
            nombre='Torneo partidas', videojuego=self.game, organizador=self.organizador,
            tipo=Torneo.Tipo.PUBLICO, formato_competitivo=self.format, max_participantes=2,
            estado=Torneo.Estado.PREPARADO, fecha_publicacion=timezone.now(),
        )
        perfiles = []
        for usuario, nick in ((self.jugador, 'Nick histórico'), (self.visitante, 'Nick rival')):
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=self.game, nick_en_juego=nick)
            perfiles.append(InscripcionTorneo.objects.create(torneo=self.torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=nick))
        self.partida = Partida.objects.create(
            torneo=self.torneo, numero_ronda=1, numero_orden=1,
            codigo_lobby='NO-DEBE-SALIR', contrasena_lobby='SECRETO',
            fecha_hora_programada=timezone.now(),
        )
        ParticipantePartida.objects.create(partida=self.partida, inscripcion=perfiles[0], posicion=1)
        ParticipantePartida.objects.create(partida=self.partida, inscripcion=perfiles[1], posicion=2)

    def test_presentacion_detalle_estados_y_acciones_por_rol_sin_filtrar_lobby(self):
        arbitro_usuario = Usuario.objects.create_user(username='arbitro_visual', email='arbitro_visual@example.com', password='test')
        espectador = Usuario.objects.create_user(username='espectador_visual', email='espectador_visual@example.com', password='test')
        arbitro = ArbitroTorneo.objects.create(torneo=self.torneo, usuario=arbitro_usuario, estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, activo_en_torneo=True)
        Partida.objects.filter(pk=self.partida.pk).update(arbitro_asignado=arbitro, fecha_hora_apertura_checkin=timezone.now() - timedelta(minutes=1))
        url = reverse('detalle-partida', args=[self.partida.pk])
        for estado in Partida.Estado.values:
            Partida.objects.filter(pk=self.partida.pk).update(estado=estado)
            self.client.logout()
            publica = self.client.get(url)
            self.assertContains(publica, f'data-state="{estado}"')
            self.assertNotContains(publica, 'NO-DEBE-SALIR')
            self.assertNotContains(publica, 'SECRETO')
            self.assertNotContains(publica, 'id="lobby-title"')
            self.assertNotContains(publica, 'method="post"')
        for estado in (Partida.Estado.CHECK_IN, Partida.Estado.EN_CURSO):
            Partida.objects.filter(pk=self.partida.pk).update(estado=estado)
            for usuario in (self.jugador, self.visitante, arbitro_usuario, self.organizador, espectador):
                with self.subTest(estado=estado, usuario=usuario.username):
                    self.client.force_login(usuario)
                    respuesta = self.client.get(url)
                    puede_confirmar = estado == Partida.Estado.CHECK_IN and usuario in (self.jugador, self.visitante, arbitro_usuario)
                    puede_declarar = estado == Partida.Estado.EN_CURSO and usuario in (self.jugador, self.visitante)
                    puede_validar = estado == Partida.Estado.EN_CURSO and usuario in (arbitro_usuario, self.organizador)
                    for accion, visible in (('confirmar-checkin-partida', puede_confirmar), ('declarar-resultado-partida', puede_declarar), ('validar-resultado-partida', puede_validar), ('editar-lobby-partida', usuario == self.organizador)):
                        objetivo = f'action="{reverse(accion, args=[self.partida.pk])}"'
                        if visible:
                            self.assertContains(respuesta, objetivo, count=1)
                            self.assertContains(respuesta, 'name="csrfmiddlewaretoken"')
                        else:
                            self.assertNotContains(respuesta, objetivo)
                    if usuario == espectador:
                        self.assertNotContains(respuesta, 'NO-DEBE-SALIR')
                        self.assertNotContains(respuesta, 'SECRETO')
                    else:
                        self.assertContains(respuesta, 'NO-DEBE-SALIR')
                        self.assertContains(respuesta, 'SECRETO', count=1)
                        self.assertContains(respuesta, 'data-toggle-password="lobby-password"')
                    self.assertNotContains(respuesta, self.jugador.email)
                    self.assertNotContains(respuesta, arbitro_usuario.email)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.EN_CURSO)
        self.assertEqual(self.partida.arbitro_asignado_id, arbitro.pk)

    def test_presentacion_checkin_confirmado_vencido_y_rivales_pendientes(self):
        url = reverse('detalle-partida', args=[self.partida.pk])
        ParticipantePartida.objects.filter(partida=self.partida, posicion=2).delete()
        InscripcionTorneo.objects.filter(torneo=self.torneo, usuario=self.jugador).update(nick_historico='NicknameHistoricoMuyLargo' * 5)
        self.client.force_login(self.jugador)
        pendiente = self.client.get(url)
        self.assertContains(pendiente, 'Por determinar')
        self.assertContains(pendiente, 'Sin árbitro asignado.')
        self.assertContains(pendiente, 'El check-in no está disponible para confirmar ahora.')
        Partida.objects.filter(pk=self.partida.pk).update(estado=Partida.Estado.CHECK_IN, fecha_hora_apertura_checkin=timezone.now() - timedelta(minutes=1))
        abierta = self.client.get(url)
        self.assertContains(abierta, 'Confirmar disponibilidad')
        CheckInPartida.objects.create(partida=self.partida, usuario=self.jugador, tipo=CheckInPartida.Tipo.PARTICIPANTE, confirmado=True, fecha_confirmacion=timezone.now())
        confirmado = self.client.get(url)
        self.assertContains(confirmado, 'Disponibilidad confirmada')
        self.assertNotContains(confirmado, '>Confirmar disponibilidad</button>')
        CheckInPartida.objects.filter(partida=self.partida).delete()
        Partida.objects.filter(pk=self.partida.pk).update(fecha_hora_apertura_checkin=timezone.now() - timedelta(days=1))
        vencida = self.client.get(url)
        self.assertContains(vencida, 'El plazo de check-in ha finalizado.')
        self.assertNotContains(vencida, '>Confirmar disponibilidad</button>')

    def test_presentacion_declaracion_y_resultado_oficial_con_ganador(self):
        from .services import declarar_resultado, validar_resultado

        Partida.objects.filter(pk=self.partida.pk).update(estado=Partida.Estado.EN_CURSO)
        ganador = self.partida.participantes.get(posicion=1).inscripcion
        declarar_resultado(self.partida, self.jugador, '2-1', ganador)
        self.client.force_login(self.jugador)
        url = reverse('detalle-partida', args=[self.partida.pk])
        declarada = self.client.get(url)
        self.assertContains(declarada, 'Tu declaración: <strong>2-1</strong>')
        self.assertContains(declarada, 'Pendiente de validación')
        self.assertNotContains(declarada, 'id="official-result-title"')
        self.assertNotContains(declarada, 'Enviar resultado')
        self.client.force_login(self.organizador)
        revision = self.client.get(url)
        self.assertContains(revision, 'Declaraciones de jugadores')
        self.assertContains(revision, 'name="motivo"')
        validar_resultado(self.partida, self.organizador, '2-1', ganador, motivo='Resultado revisado.')
        self.client.force_login(self.jugador)
        final = self.client.get(url)
        self.assertContains(final, 'data-state="FINALIZADA"')
        self.assertContains(final, 'rk-match-player--winner', count=1)
        self.assertContains(final, '<strong class="rk-match-score">2-1</strong>')
        self.assertContains(final, f'Ganador: <strong>{ganador.nick_historico}</strong>')
        self.assertNotContains(final, 'SECRETO')
        self.assertNotContains(final, 'NO-DEBE-SALIR')
        self.assertNotContains(final, 'Validar resultado oficial')
        self.assertNotContains(final, 'Pendiente de validación')

    def test_mis_partidas_solo_muestra_partidas_del_usuario(self):
        self.client.force_login(self.jugador)
        respuesta = self.client.get(reverse('mis-partidas'))
        self.assertContains(respuesta, self.torneo.nombre)
        self.assertContains(respuesta, 'Ver partida')
        self.assertEqual(respuesta.content.count(b'Torneo partidas'), 1)

    def test_detalle_publico_muestra_participantes_horario_y_oculta_lobby(self):
        respuesta = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertContains(respuesta, 'Nick histórico')
        self.assertContains(respuesta, 'Nick rival')
        self.assertContains(respuesta, 'Programada')
        self.assertNotContains(respuesta, 'NO-DEBE-SALIR')
        self.assertNotContains(respuesta, 'SECRETO')

    def test_detalle_privado_ajeno_esta_protegido(self):
        self.torneo.tipo = Torneo.Tipo.PRIVADO
        self.torneo.estado = Torneo.Estado.BORRADOR
        self.torneo.save(update_fields=('tipo', 'estado'))
        self.assertEqual(self.client.get(reverse('detalle-partida', args=[self.partida.pk])).status_code, 404)
        self.client.force_login(self.organizador)
        self.assertEqual(self.client.get(reverse('detalle-partida', args=[self.partida.pk])).status_code, 200)

    def test_contexto_distingue_participante_organizador_y_visitante(self):
        self.client.force_login(self.jugador)
        respuesta = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertTrue(respuesta.context['detalle']['es_participante'])
        self.client.force_login(self.organizador)
        respuesta = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertTrue(respuesta.context['detalle']['es_organizador'])
        self.client.force_login(self.visitante)
        respuesta = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertTrue(respuesta.context['detalle']['es_participante'])

    def test_checkin_solo_se_muestra_a_usuario_vinculado(self):
        CheckInPartida.objects.create(partida=self.partida, usuario=self.jugador, tipo=CheckInPartida.Tipo.PARTICIPANTE, confirmado=True)
        self.client.force_login(self.jugador)
        respuesta = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertContains(respuesta, 'Disponibilidad confirmada')
        self.client.force_login(self.visitante)
        respuesta = self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.assertNotContains(respuesta, 'Disponibilidad confirmada')

    def test_get_no_modifica_partida(self):
        estado = self.partida.estado
        self.client.get(reverse('detalle-partida', args=[self.partida.pk]))
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, estado)

    def test_ronda_posterior_sin_participacion_no_aparece_en_mis_partidas(self):
        posterior = Partida.objects.create(torneo=self.torneo, numero_ronda=2, numero_orden=1)
        self.client.force_login(self.jugador)
        respuesta = self.client.get(reverse('mis-partidas'))
        self.assertEqual(respuesta.content.count(b'Ver partida'), 1)

    def crear_encuentro_usuario(self, estado, fecha=None):
        partida = Partida.objects.create(
            torneo=self.torneo, numero_ronda=1, numero_orden=self.torneo.partidas.count() + 1,
            estado=estado, fecha_hora_programada=fecha,
        )
        for participante in self.partida.participantes.all():
            ParticipantePartida.objects.create(partida=partida, inscripcion=participante.inscripcion, posicion=participante.posicion)
        return partida

    def test_mis_partidas_arbitro_solo_asignacion_aceptada_y_activa(self):
        arbitro = ArbitroTorneo.objects.create(
            torneo=self.torneo, usuario=self.organizador,
            estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, activo_en_torneo=True,
        )
        self.partida.arbitro_asignado = arbitro
        self.partida.save(update_fields=('arbitro_asignado',))
        otra = self.crear_encuentro_usuario(Partida.Estado.PROGRAMADA)
        self.client.force_login(self.organizador)
        respuesta = self.client.get(reverse('mis-partidas'))
        self.assertEqual([(f['partida'].pk, f['rol']) for f in respuesta.context['partidas']], [(self.partida.pk, 'Árbitro')])
        self.assertNotContains(respuesta, reverse('detalle-partida', args=[otra.pk]))
        ArbitroTorneo.objects.filter(pk=arbitro.pk).update(estado_invitacion=ArbitroTorneo.EstadoInvitacion.PENDIENTE)
        self.assertEqual(self.client.get(reverse('mis-partidas')).context['partidas'], [])
        ArbitroTorneo.objects.filter(pk=arbitro.pk).update(estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, activo_en_torneo=False)
        self.assertEqual(self.client.get(reverse('mis-partidas')).context['partidas'], [])

    def test_mis_partidas_no_incluye_ajenas_ni_inscripcion_cancelada(self):
        self.client.force_login(self.organizador)
        self.assertEqual(self.client.get(reverse('mis-partidas')).context['partidas'], [])
        InscripcionTorneo.objects.filter(torneo=self.torneo, usuario=self.jugador).update(estado=InscripcionTorneo.Estado.CANCELADA)
        self.client.force_login(self.jugador)
        self.assertEqual(self.client.get(reverse('mis-partidas')).context['partidas'], [])

    def test_mis_partidas_muestra_identidades_y_nunca_carga_lobby(self):
        self.client.force_login(self.jugador)
        respuesta = self.client.get(reverse('mis-partidas'))
        self.assertContains(respuesta, 'Nick histórico')
        self.assertContains(respuesta, '@jugadorpartida')
        self.assertContains(respuesta, 'Nick rival')
        self.assertContains(respuesta, 'Jugador')
        self.assertNotContains(respuesta, 'NO-DEBE-SALIR')
        self.assertNotContains(respuesta, 'SECRETO')
        fila = respuesta.context['partidas'][0]
        self.assertNotIn('lobby', fila)
        self.assertTrue({'codigo_lobby', 'contrasena_lobby', 'nombre_lobby', 'instrucciones_lobby'}.issubset(fila['partida'].get_deferred_fields()))

    def test_mis_partidas_orden_filtros_e_historico(self):
        ahora = timezone.now()
        Partida.objects.filter(pk=self.partida.pk).update(estado=Partida.Estado.EN_CURSO)
        checkin = self.crear_encuentro_usuario(Partida.Estado.CHECK_IN, ahora + timedelta(minutes=5))
        lejano = self.crear_encuentro_usuario(Partida.Estado.PROGRAMADA, ahora + timedelta(days=1))
        cercano = self.crear_encuentro_usuario(Partida.Estado.PROGRAMADA, ahora + timedelta(hours=1))
        sin_hora = self.crear_encuentro_usuario(Partida.Estado.PENDIENTE)
        final = self.crear_encuentro_usuario(Partida.Estado.FINALIZADA, ahora - timedelta(days=1))
        cancelada = self.crear_encuentro_usuario(Partida.Estado.CANCELADA, ahora - timedelta(days=2))
        self.client.force_login(self.jugador)
        url = reverse('mis-partidas')
        respuesta = self.client.get(url)
        self.assertEqual([f['partida'].pk for f in respuesta.context['partidas']], [self.partida.pk, checkin.pk, cercano.pk, lejano.pk, sin_hora.pk, final.pk, cancelada.pk])
        self.assertContains(respuesta, 'Historial de encuentros')
        proximas = self.client.get(url, {'vista': 'proximas'})
        self.assertEqual([f['partida'].pk for f in proximas.context['partidas']], [checkin.pk, cercano.pk, lejano.pk, sin_hora.pk])
        self.assertEqual([f['partida'].pk for f in self.client.get(url, {'vista': 'en_curso'}).context['partidas']], [self.partida.pk])
        self.assertEqual([f['partida'].pk for f in self.client.get(url, {'vista': 'finalizadas'}).context['partidas']], [final.pk])
        self.assertEqual(self.client.get(url, {'vista': 'inventada'}).context['vista'], 'todas')
        final.refresh_from_db()
        self.assertEqual(final.estado, Partida.Estado.FINALIZADA)

    def test_mis_partidas_vacio_y_login(self):
        self.assertEqual(self.client.get(reverse('mis-partidas')).status_code, 302)
        self.client.force_login(self.organizador)
        respuesta = self.client.get(reverse('mis-partidas'))
        self.assertContains(respuesta, 'Aún no tienes partidas')
        self.assertContains(respuesta, reverse('mis-participaciones'))

    def test_mis_partidas_sin_consultas_por_encuentro(self):
        self.client.force_login(self.jugador)
        self.client.get(reverse('mis-partidas'))
        with CaptureQueriesContext(connection) as inicial:
            self.client.get(reverse('mis-partidas'))
        for indice in range(5):
            self.crear_encuentro_usuario(Partida.Estado.PROGRAMADA)
        with CaptureQueriesContext(connection) as poblado:
            respuesta = self.client.get(reverse('mis-partidas'))
        self.assertEqual(len(respuesta.context['partidas']), 6)
        self.assertEqual(len(poblado), len(inicial))
