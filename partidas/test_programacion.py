from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from notificaciones.models import Notificacion
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from torneos.services import procesar_calendario
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .lobby import puede_ver_lobby
from .models import CheckInPartida, Partida, ParticipantePartida, ResultadoPartida
from .scheduling import CheckInError, confirmar_checkin, iniciar_partida, preparar_partida, procesar_checkins, reprogramar_partida

CLAVE = 'ClaveSegura123!'
SECRETO = 'CLAVE-LOBBY-9X'


class ProgramacionBase(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(username='adminorg', email='adminorg@example.com', password=CLAVE)
        self.otro_admin = Usuario.objects.create_superuser(username='adminotro', email='adminotro@example.com', password=CLAVE)
        self.ajeno = self.usuario('ajeno')
        self.juego = Videojuego.objects.create(nombre='Juego programacion', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(nombre='Formato programacion', min_participantes=2, max_participantes=128)
        self.torneo = self.crear_torneo(Torneo.Tipo.OFICIAL, Torneo.Estado.PREPARADO)
        self.inscripciones = [self.inscribir(self.torneo, self.usuario(f'jugador{i}'), f'Nick{i}') for i in (1, 2)]
        self.jugador1, self.jugador2 = (i.usuario for i in self.inscripciones)
        self.arbitro_usuario = self.usuario('arbitro')
        self.arbitro = ArbitroTorneo.objects.create(
            usuario=self.arbitro_usuario, torneo=self.torneo,
            estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, activo_en_torneo=True,
        )
        self.partida = self.crear_partida(self.torneo, 1, 1, self.inscripciones, arbitro=self.arbitro)

    def usuario(self, nombre):
        return Usuario.objects.create_user(username=nombre, email=f'{nombre}@example.com', password=CLAVE)

    def crear_torneo(self, tipo, estado, **extra):
        return Torneo.objects.create(
            nombre=f'Torneo {Torneo.objects.count()}', videojuego=self.juego, organizador=self.admin,
            tipo=tipo, formato_competitivo=self.formato, max_participantes=2, estado=estado,
            fecha_publicacion=timezone.now(), **extra,
        )

    def inscribir(self, torneo, usuario, nick):
        perfil = PerfilVideojuegoUsuario.objects.get_or_create(
            usuario=usuario, videojuego=self.juego, defaults={'nick_en_juego': nick},
        )[0]
        return InscripcionTorneo.objects.create(torneo=torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=nick)

    def crear_partida(self, torneo, ronda, orden, inscripciones, arbitro=None, **extra):
        partida = Partida.objects.create(torneo=torneo, numero_ronda=ronda, numero_orden=orden, arbitro_asignado=arbitro, **extra)
        for posicion, inscripcion in enumerate(inscripciones, 1):
            ParticipantePartida.objects.create(partida=partida, inscripcion=inscripcion, posicion=posicion)
        return partida

    def programar_web(self, usuario, fecha, motivo='', partida=None):
        self.client.force_login(usuario)
        return self.client.post(
            reverse('programar-partida', args=[(partida or self.partida).pk]),
            {'fecha_hora': timezone.localtime(fecha).strftime('%Y-%m-%dT%H:%M'), 'motivo': motivo},
        )

    def futuro(self, **delta):
        return (timezone.now() + timedelta(**delta)).replace(second=0, microsecond=0)

    def mensajes(self, usuario):
        return list(Notificacion.objects.filter(destinatario=usuario, partida=self.partida))


class ProgramacionOficialTests(ProgramacionBase):
    def test_admin_global_programa_partida_y_se_notifica(self):
        fecha = self.futuro(days=2)
        respuesta = self.programar_web(self.otro_admin, fecha)
        self.assertRedirects(respuesta, reverse('calendario-partidas', args=[self.torneo.pk]), fetch_redirect_response=False)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.PROGRAMADA)
        self.assertEqual(self.partida.fecha_hora_programada.replace(microsecond=0), fecha.replace(microsecond=0))
        historial = self.partida.historial_programacion.get()
        self.assertIsNone(historial.fecha_anterior)
        self.assertEqual(historial.actor, self.otro_admin)
        for destinatario in (self.jugador1, self.jugador2, self.arbitro_usuario, self.admin):
            avisos = self.mensajes(destinatario)
            self.assertEqual(len(avisos), 1, destinatario)
            self.assertEqual(avisos[0].titulo, 'Partida programada')

    def test_organizador_programa_sin_notificarse_a_si_mismo(self):
        self.programar_web(self.admin, timezone.now() + timedelta(days=2))
        self.assertEqual(self.mensajes(self.admin), [])
        self.assertEqual(len(self.mensajes(self.jugador1)), 1)

    def test_notificacion_lleva_al_detalle_y_no_incluye_lobby(self):
        Partida.objects.filter(pk=self.partida.pk).update(codigo_lobby='SALA', contrasena_lobby=SECRETO)
        self.programar_web(self.admin, timezone.now() + timedelta(days=2))
        aviso = self.mensajes(self.jugador1)[0]
        self.client.force_login(self.jugador1)
        respuesta = self.client.post(reverse('abrir-notificacion', args=[aviso.pk]))
        self.assertRedirects(respuesta, reverse('detalle-partida', args=[self.partida.pk]), fetch_redirect_response=False)
        self.assertNotIn(SECRETO, aviso.mensaje + aviso.titulo)

    def test_jugador_arbitro_y_ajeno_no_programan(self):
        fecha = timezone.now() + timedelta(days=2)
        for usuario in (self.jugador1, self.arbitro_usuario, self.ajeno):
            self.assertEqual(self.programar_web(usuario, fecha).status_code, 404)
            with self.assertRaises(CheckInError):
                reprogramar_partida(self.partida, fecha, usuario, 'x')
        self.partida.refresh_from_db()
        self.assertIsNone(self.partida.fecha_hora_programada)
        self.assertEqual(self.partida.historial_programacion.count(), 0)

    def test_programar_solo_por_post(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('programar-partida', args=[self.partida.pk])).status_code, 404)

    def test_reprogramacion_exige_motivo_y_guarda_historial(self):
        primera = self.futuro(days=2)
        segunda = primera + timedelta(hours=3)
        self.programar_web(self.admin, primera)
        self.programar_web(self.admin, segunda)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.fecha_hora_programada.replace(microsecond=0), primera.replace(microsecond=0))
        self.programar_web(self.admin, segunda, motivo='Retraso de la jornada')
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.fecha_hora_programada.replace(microsecond=0), segunda.replace(microsecond=0))
        historial = list(self.partida.historial_programacion.order_by('pk'))
        self.assertEqual(len(historial), 2)
        self.assertEqual(historial[1].fecha_anterior.replace(microsecond=0), primera.replace(microsecond=0))
        self.assertEqual(historial[1].motivo, 'Retraso de la jornada')
        self.assertEqual({m.titulo for m in self.mensajes(self.jugador1)}, {'Partida programada', 'Partida reprogramada'})
        self.assertEqual(len(self.mensajes(self.jugador1)), 2)

    def test_misma_hora_no_duplica_historial_ni_notificaciones(self):
        fecha = timezone.now() + timedelta(days=2)
        reprogramar_partida(self.partida, fecha, self.admin, '')
        reprogramar_partida(self.partida, fecha, self.admin, 'sin cambios')
        self.assertEqual(self.partida.historial_programacion.count(), 1)
        self.assertEqual(len(self.mensajes(self.jugador1)), 1)

    def test_volver_a_una_hora_anterior_notifica_de_nuevo(self):
        a = timezone.now() + timedelta(days=2)
        reprogramar_partida(self.partida, a, self.admin, '')
        reprogramar_partida(self.partida, a + timedelta(hours=1), self.admin, 'cambio')
        reprogramar_partida(self.partida, a, self.admin, 'vuelta')
        self.assertEqual(len(self.mensajes(self.jugador1)), 3)

    def test_estados_no_reprogramables_se_rechazan(self):
        fecha = timezone.now() + timedelta(days=2)
        for estado in (Partida.Estado.FINALIZADA, Partida.Estado.EN_CURSO):
            Partida.objects.filter(pk=self.partida.pk).update(estado=estado)
            with self.assertRaises(CheckInError):
                reprogramar_partida(self.partida, fecha, self.admin, 'x')
            self.assertEqual(self.programar_web(self.admin, fecha, 'x').status_code, 302)
        self.assertEqual(self.partida.historial_programacion.count(), 0)

    def test_fecha_pasada_y_limites_del_torneo_se_rechazan(self):
        ahora = timezone.now()
        with self.assertRaises(CheckInError):
            reprogramar_partida(self.partida, ahora - timedelta(minutes=1), self.admin, '')
        with self.assertRaises(CheckInError):
            reprogramar_partida(self.partida, ahora + timedelta(days=800), self.admin, '')
        Torneo.objects.filter(pk=self.torneo.pk).update(
            fecha_inicio_prevista=ahora + timedelta(days=1), fecha_fin_prevista=ahora + timedelta(days=2),
        )
        with self.assertRaises(CheckInError):
            reprogramar_partida(self.partida, ahora + timedelta(hours=2), self.admin, '')
        with self.assertRaises(CheckInError):
            reprogramar_partida(self.partida, ahora + timedelta(days=3), self.admin, '')
        reprogramar_partida(self.partida, ahora + timedelta(days=1, hours=5), self.admin, '')

    def test_datetime_naive_se_interpreta_en_europe_madrid(self):
        local = timezone.localtime(timezone.now() + timedelta(days=3)).replace(hour=21, minute=30, second=0, microsecond=0)
        self.programar_web(self.admin, local)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.fecha_hora_programada, local)
        self.assertEqual(str(timezone.get_current_timezone()), 'Europe/Madrid')
        self.assertEqual(timezone.localtime(self.partida.fecha_hora_programada).strftime('%H:%M'), '21:30')
        with self.assertRaises(CheckInError):
            reprogramar_partida(self.partida, local.replace(tzinfo=None, day=local.day) + timedelta(days=1), self.admin, 'x')

    def test_publicos_y_privados_no_usan_programacion_oficial(self):
        for tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.PRIVADO):
            torneo = self.crear_torneo(tipo, Torneo.Estado.PREPARADO)
            partida = self.crear_partida(torneo, 1, 1, [])
            with self.assertRaises(CheckInError):
                reprogramar_partida(partida, timezone.now() + timedelta(days=2), self.admin, 'x')


class CheckinOficialTests(ProgramacionBase):
    def setUp(self):
        super().setUp()
        self.programada = timezone.now() + timedelta(hours=3)
        reprogramar_partida(self.partida, self.programada, self.admin, '')

    def abrir_y_confirmar(self, ahora):
        preparar_partida(self.partida, ahora=ahora)
        confirmar_checkin(self.partida, self.jugador1, CheckInPartida.Tipo.PARTICIPANTE, ahora=ahora)
        confirmar_checkin(self.partida, self.jugador2, CheckInPartida.Tipo.PARTICIPANTE, ahora=ahora)
        confirmar_checkin(self.partida, self.arbitro_usuario, CheckInPartida.Tipo.ARBITRO, ahora=ahora)

    def test_checkin_abre_diez_minutos_antes(self):
        preparar_partida(self.partida, ahora=self.programada - timedelta(minutes=11))
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.PROGRAMADA)
        preparar_partida(self.partida, ahora=self.programada - timedelta(minutes=10))
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.CHECK_IN)

    def test_no_empieza_antes_de_la_hora_aunque_todos_confirmen(self):
        self.abrir_y_confirmar(self.programada - timedelta(minutes=9))
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.LISTA_PARA_COMENZAR)
        with self.assertRaises(CheckInError):
            iniciar_partida(self.partida, ahora=self.programada - timedelta(seconds=1))
        iniciar_partida(self.partida, ahora=self.programada)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.EN_CURSO)
        self.assertIsNotNone(self.partida.fecha_hora_inicio_real)

    def test_empieza_al_confirmar_cuando_ya_llego_la_hora(self):
        ahora = self.programada - timedelta(minutes=10)
        preparar_partida(self.partida, ahora=ahora)
        confirmar_checkin(self.partida, self.jugador1, CheckInPartida.Tipo.PARTICIPANTE, ahora=ahora)
        confirmar_checkin(self.partida, self.jugador2, CheckInPartida.Tipo.PARTICIPANTE, ahora=ahora)
        confirmar_checkin(self.partida, self.arbitro_usuario, CheckInPartida.Tipo.ARBITRO, ahora=self.programada)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.EN_CURSO)

    def test_reprogramar_durante_checkin_invalida_confirmaciones(self):
        apertura = self.programada - timedelta(minutes=8)
        preparar_partida(self.partida, ahora=apertura)
        confirmar_checkin(self.partida, self.jugador1, CheckInPartida.Tipo.PARTICIPANTE, ahora=apertura)
        confirmar_checkin(self.partida, self.arbitro_usuario, CheckInPartida.Tipo.ARBITRO, ahora=apertura)
        nueva = self.programada + timedelta(hours=2)
        reprogramar_partida(self.partida, nueva, self.admin, 'Aplazada', ahora=apertura)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.PROGRAMADA)
        self.assertIsNone(self.partida.fecha_hora_apertura_checkin)
        self.assertFalse(self.partida.checkins.filter(confirmado=True).exists())
        self.assertEqual(self.partida.historial_programacion.order_by('pk').last().checkins_invalidados, 2)
        with self.assertRaises(CheckInError):
            confirmar_checkin(self.partida, self.jugador2, CheckInPartida.Tipo.PARTICIPANTE, ahora=apertura)
        self.abrir_y_confirmar(nueva - timedelta(minutes=5))
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, Partida.Estado.LISTA_PARA_COMENZAR)

    def test_oficial_sin_hora_o_sin_rivales_no_rompe_el_procesador(self):
        sin_hora = self.crear_partida(self.torneo, 2, 1, self.inscripciones[:1])
        self.assertEqual(procesar_checkins(ahora=self.programada - timedelta(minutes=5)), 1)
        sin_hora.refresh_from_db()
        self.assertEqual(sin_hora.estado, Partida.Estado.PENDIENTE)
        self.assertEqual(procesar_calendario(ahora=self.programada), 0)


class LobbyTests(ProgramacionBase):
    def configurar(self, **extra):
        datos = {'codigo_lobby': 'SALA-77', 'contrasena_lobby': SECRETO, 'nombre_lobby': 'Sala final', 'instrucciones_lobby': 'Entrad a tiempo'}
        datos.update(extra)
        Partida.objects.filter(pk=self.partida.pk).update(**datos)

    def detalle(self, usuario=None):
        if usuario:
            self.client.force_login(usuario)
        else:
            self.client.logout()
        return self.client.get(reverse('detalle-partida', args=[self.partida.pk]))

    def test_oficial_visibilidad_por_rol_y_ventana(self):
        self.configurar()
        Partida.objects.filter(pk=self.partida.pk).update(fecha_hora_programada=timezone.now() + timedelta(hours=2))
        for usuario in (self.jugador1, self.jugador2, self.ajeno, None):
            self.assertNotContains(self.detalle(usuario), SECRETO)
        for usuario in (self.admin, self.otro_admin, self.arbitro_usuario):
            self.assertContains(self.detalle(usuario), SECRETO)
        Partida.objects.filter(pk=self.partida.pk).update(fecha_hora_programada=timezone.now() + timedelta(minutes=20))
        for usuario in (self.jugador1, self.jugador2):
            respuesta = self.detalle(usuario)
            self.assertContains(respuesta, SECRETO)
            self.assertContains(respuesta, 'Sala final')
            self.assertContains(respuesta, 'type="password"')
        for usuario in (self.ajeno, None):
            respuesta = self.detalle(usuario)
            self.assertNotContains(respuesta, SECRETO)
            self.assertNotContains(respuesta, 'SALA-77')
            self.assertNotContains(respuesta, 'Información de partida')

    def test_jugador_sin_hora_programada_no_ve_lobby(self):
        self.configurar()
        self.assertNotContains(self.detalle(self.jugador1), SECRETO)

    def test_sin_configurar_muestra_mensaje_a_autorizados(self):
        self.assertContains(self.detalle(self.admin), 'La información de la partida todavía no está disponible.')
        self.assertNotContains(self.detalle(self.ajeno), 'La información de la partida todavía no está disponible.')

    def test_semifinal_futura_no_revela_y_el_avance_concede_acceso(self):
        semifinal = self.crear_partida(self.torneo, 2, 1, self.inscripciones[:1], codigo_lobby='SEMI', contrasena_lobby=SECRETO,
                                       fecha_hora_programada=timezone.now() + timedelta(minutes=10))
        self.assertFalse(puede_ver_lobby(semifinal, self.jugador1))
        self.client.force_login(self.jugador1)
        self.assertNotContains(self.client.get(reverse('detalle-partida', args=[semifinal.pk])), SECRETO)
        ParticipantePartida.objects.create(partida=semifinal, inscripcion=self.inscripciones[1], posicion=2)
        self.assertTrue(puede_ver_lobby(semifinal, self.jugador1))
        self.assertContains(self.client.get(reverse('detalle-partida', args=[semifinal.pk])), SECRETO)
        nuevo = self.usuario('nuevo')
        self.assertFalse(puede_ver_lobby(semifinal, nuevo))

    def test_finalizada_oculta_lobby_a_jugadores(self):
        self.configurar(fecha_hora_programada=timezone.now() + timedelta(minutes=5), estado=Partida.Estado.FINALIZADA)
        self.assertNotContains(self.detalle(self.jugador1), SECRETO)

    def test_publico_visible_con_checkin_abierto_y_privado_con_torneo_preparado(self):
        publico = self.crear_torneo(Torneo.Tipo.PUBLICO, Torneo.Estado.PREPARADO)
        ins = [self.inscribir(publico, self.jugador1, 'Nick1'), self.inscribir(publico, self.jugador2, 'Nick2')]
        partida = self.crear_partida(publico, 1, 1, ins, codigo_lobby='PUB', contrasena_lobby=SECRETO)
        self.assertFalse(puede_ver_lobby(partida, self.jugador1))
        Partida.objects.filter(pk=partida.pk).update(estado=Partida.Estado.CHECK_IN)
        partida.refresh_from_db()
        self.assertTrue(puede_ver_lobby(partida, self.jugador1))
        self.assertFalse(puede_ver_lobby(partida, self.ajeno))
        privado = self.crear_torneo(Torneo.Tipo.PRIVADO, Torneo.Estado.PREPARADO)
        ins = [self.inscribir(privado, self.jugador1, 'Nick1'), self.inscribir(privado, self.jugador2, 'Nick2')]
        partida = self.crear_partida(privado, 1, 1, ins, codigo_lobby='PRI', contrasena_lobby=SECRETO)
        self.assertTrue(puede_ver_lobby(partida, self.jugador2))
        Torneo.objects.filter(pk=privado.pk).update(estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS)
        partida = Partida.objects.get(pk=partida.pk)
        self.assertFalse(puede_ver_lobby(partida, self.jugador2))

    def test_contrasena_no_aparece_en_vistas_publicas(self):
        self.configurar(fecha_hora_programada=timezone.now() + timedelta(minutes=5))
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('bracket-torneo', args=[self.torneo.pk])), SECRETO)
        self.assertNotContains(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])), SECRETO)
        self.client.force_login(self.ajeno)
        self.assertNotContains(self.client.get(reverse('mis-partidas')), SECRETO)
        self.client.force_login(self.jugador1)
        self.assertNotContains(self.client.get(reverse('mis-partidas')), SECRETO)
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse('calendario-partidas', args=[self.torneo.pk])), SECRETO)

    def test_solo_organizador_y_admin_editan_lobby_por_post(self):
        url = reverse('editar-lobby-partida', args=[self.partida.pk])
        datos = {'nombre_lobby': 'Sala', 'codigo_lobby': 'ABC', 'contrasena_lobby': SECRETO, 'instrucciones_lobby': 'Hola'}
        for usuario in (self.jugador1, self.arbitro_usuario, self.ajeno):
            self.client.force_login(usuario)
            self.assertEqual(self.client.post(url, datos).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.post(url, datos)
        self.partida.refresh_from_db()
        self.assertEqual((self.partida.codigo_lobby, self.partida.contrasena_lobby, self.partida.nombre_lobby), ('ABC', SECRETO, 'Sala'))
        self.client.force_login(self.otro_admin)
        self.client.post(url, {**datos, 'codigo_lobby': 'XYZ'})
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.codigo_lobby, 'XYZ')

    def test_contrasena_vacia_conserva_y_quitar_la_elimina(self):
        self.configurar()
        url = reverse('editar-lobby-partida', args=[self.partida.pk])
        self.client.force_login(self.admin)
        self.client.post(url, {'nombre_lobby': 'Nueva', 'codigo_lobby': 'N', 'contrasena_lobby': '', 'instrucciones_lobby': ''})
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.contrasena_lobby, SECRETO)
        formulario = self.client.get(reverse('detalle-partida', args=[self.partida.pk])).content.decode()
        self.assertEqual(formulario.count(SECRETO), 1)
        self.client.post(url, {'nombre_lobby': 'Nueva', 'codigo_lobby': 'N', 'quitar_contrasena': 'on'})
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.contrasena_lobby, '')

    def test_no_se_edita_lobby_de_partida_finalizada(self):
        Partida.objects.filter(pk=self.partida.pk).update(estado=Partida.Estado.FINALIZADA)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('editar-lobby-partida', args=[self.partida.pk]), {'codigo_lobby': 'A'}).status_code, 404)


class CalendarioTests(ProgramacionBase):
    def test_calendario_ordena_y_distingue_sin_programar(self):
        tarde = timezone.now() + timedelta(days=3)
        pronto = timezone.now() + timedelta(days=1)
        a = self.crear_partida(self.torneo, 1, 2, [], fecha_hora_programada=tarde)
        b = self.crear_partida(self.torneo, 1, 3, [], fecha_hora_programada=pronto)
        sin = self.crear_partida(self.torneo, 2, 1, [])
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('calendario-partidas', args=[self.torneo.pk]))
        self.assertContains(respuesta, 'Calendario de partidas')
        self.assertContains(respuesta, 'Rivales por determinar')
        self.assertContains(respuesta, 'Sin programar')
        orden = [fila['partida'].pk for fila in respuesta.context['filas']]
        self.assertEqual(orden, [b.pk, a.pk, self.partida.pk, sin.pk])
        self.assertContains(respuesta, reverse('detalle-partida', args=[a.pk]))
        self.assertContains(respuesta, reverse('programar-partida', args=[a.pk]))

    def test_calendario_solo_para_gestores_de_torneo_oficial(self):
        url = reverse('calendario-partidas', args=[self.torneo.pk])
        for usuario in (self.jugador1, self.arbitro_usuario, self.ajeno):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.otro_admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        publico = self.crear_torneo(Torneo.Tipo.PUBLICO, Torneo.Estado.PREPARADO)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('calendario-partidas', args=[publico.pk])).status_code, 404)

    def test_ficha_oficial_enlaza_al_calendario_solo_a_gestores(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])), reverse('calendario-partidas', args=[self.torneo.pk]))
        self.client.force_login(self.jugador1)
        self.assertNotContains(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])), reverse('calendario-partidas', args=[self.torneo.pk]))

    def test_calendario_no_ofrece_editar_finalizadas(self):
        Partida.objects.filter(pk=self.partida.pk).update(estado=Partida.Estado.FINALIZADA)
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('calendario-partidas', args=[self.torneo.pk]))
        self.assertNotContains(respuesta, reverse('programar-partida', args=[self.partida.pk]))
        self.assertContains(respuesta, 'No se puede modificar la programación.')
