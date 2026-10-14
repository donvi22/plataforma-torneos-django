from datetime import timedelta

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from partidas.models import Partida, ParticipantePartida
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import Usuario


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class HomeDashboardTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(username='dashboard', email='privado@example.com', password='test')
        self.organizador = Usuario.objects.create_user(username='organizador', email='org@example.com', password='test')
        self.rival = Usuario.objects.create_user(username='rival', email='rival@example.com', password='test')
        self.juego = Videojuego.objects.create(nombre='Juego real', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(nombre='Eliminación directa')
        self.url = reverse('inicio')

    def torneo(self, nombre, **extra):
        datos = {
            'nombre': nombre, 'videojuego': self.juego, 'organizador': self.organizador,
            'tipo': Torneo.Tipo.PUBLICO, 'formato_competitivo': self.formato, 'max_participantes': 4,
            'estado': Torneo.Estado.INSCRIPCIONES_ABIERTAS, 'fecha_publicacion': timezone.now(),
            'fecha_apertura_inscripciones': timezone.now() - timedelta(minutes=5),
            'fecha_cierre_inscripciones': timezone.now() + timedelta(minutes=25),
        }
        datos.update(extra)
        return Torneo.objects.create(**datos)

    def inscribir(self, torneo, usuario, estado=InscripcionTorneo.Estado.CONFIRMADA):
        perfil, _ = PerfilVideojuegoUsuario.objects.get_or_create(
            usuario=usuario, videojuego=self.juego, defaults={'nick_en_juego': f'Juego_{usuario.username}'},
        )
        return InscripcionTorneo.objects.create(
            torneo=torneo, usuario=usuario, perfil_videojuego=perfil,
            nick_historico=perfil.nick_en_juego, estado=estado,
        )

    def partida(self, nombre, rol='Jugador', **extra):
        torneo = self.torneo(nombre, estado=Torneo.Estado.PREPARADO)
        if rol == 'Árbitro':
            arbitro = ArbitroTorneo.objects.create(
                torneo=torneo, usuario=self.usuario,
                estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, activo_en_torneo=True,
            )
            extra['arbitro_asignado'] = arbitro
        partida = Partida.objects.create(torneo=torneo, numero_ronda=1, numero_orden=1, **extra)
        if rol == 'Jugador':
            ParticipantePartida.objects.create(partida=partida, inscripcion=self.inscribir(torneo, self.usuario), posicion=1)
        ParticipantePartida.objects.create(partida=partida, inscripcion=self.inscribir(torneo, self.rival), posicion=2)
        return partida

    def test_destacados_solo_catalogo_visible_y_no_terminal(self):
        publico = self.torneo('Público visible')
        oficial = self.torneo('Oficial visible', tipo=Torneo.Tipo.OFICIAL)
        self.torneo('Privado secreto', tipo=Torneo.Tipo.PRIVADO)
        self.torneo('Borrador oculto', estado=Torneo.Estado.BORRADOR)
        self.torneo('Sin publicar', fecha_publicacion=None)
        self.torneo('Finalizado', estado=Torneo.Estado.FINALIZADO)
        self.torneo('Cancelado', estado=Torneo.Estado.CANCELADO)
        inactivo = Videojuego.objects.create(nombre='Inactivo', genero='X', activo=False)
        self.torneo('Juego oculto', videojuego=inactivo)
        respuesta = self.client.get(self.url)
        self.assertEqual({t.pk for t in respuesta.context['destacados']}, {publico.pk, oficial.pk})
        self.assertContains(respuesta, 'Público visible')
        self.assertNotContains(respuesta, 'Privado secreto')

    def test_plazas_cuentan_solo_inscripciones_confirmadas(self):
        torneo = self.torneo('Plazas reales')
        self.inscribir(torneo, self.usuario)
        self.inscribir(torneo, self.rival, InscripcionTorneo.Estado.CANCELADA)
        self.assertContains(self.client.get(self.url), '1/4 participantes')

    def test_mis_torneos_incluye_privados_propios_no_ajenos(self):
        propio = self.torneo('Mi privado', organizador=self.usuario, tipo=Torneo.Tipo.PRIVADO)
        self.torneo('Privado de otro', tipo=Torneo.Tipo.PRIVADO)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertEqual([t.pk for t in respuesta.context['mis_torneos']], [propio.pk])
        self.assertContains(respuesta, 'Mi privado')
        self.assertNotContains(respuesta, 'Privado de otro')

    def test_proximas_partidas_separan_rol_y_no_exponen_lobby(self):
        jugador = self.partida('Torneo jugador', codigo_lobby='LOBBY-PRIVADO', contrasena_lobby='CLAVE-SECRETA')
        arbitro = self.partida('Torneo arbitral', rol='Árbitro')
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        roles = {fila['partida'].pk: fila['rol'] for fila in respuesta.context['proximas_partidas']}
        self.assertEqual(roles, {jugador.pk: 'Jugador', arbitro.pk: 'Árbitro'})
        self.assertContains(respuesta, 'Juego_rival')
        self.assertContains(respuesta, reverse('detalle-partida', args=[jugador.pk]))
        for secreto in ('CLAVE-SECRETA', 'LOBBY-PRIVADO', self.rival.email):
            self.assertNotContains(respuesta, secreto)

    def test_proximas_excluye_cancelados_finalizados_y_roles_no_activos(self):
        cancelada = self.partida('Cancelada', estado=Partida.Estado.CANCELADA)
        self.partida('Finalizada', estado=Partida.Estado.FINALIZADA)
        baja = self.partida('Inscripción cancelada')
        InscripcionTorneo.objects.filter(torneo=baja.torneo, usuario=self.usuario).update(estado=InscripcionTorneo.Estado.CANCELADA)
        salida = self.partida('Árbitro retirado', rol='Árbitro')
        ArbitroTorneo.objects.filter(pk=salida.arbitro_asignado_id).update(activo_en_torneo=False)
        Torneo.objects.filter(pk=cancelada.torneo_id).update(estado=Torneo.Estado.CANCELADO)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(self.url).context['proximas_partidas'], [])

    def test_orden_y_limites_dashboard(self):
        sin_hora = self.partida('Sin horario')
        tarde = self.partida('Más tarde', fecha_hora_programada=timezone.now() + timedelta(days=2))
        pronto = self.partida('Primero', fecha_hora_programada=timezone.now() + timedelta(hours=2))
        self.partida('Sin horario adicional')
        for indice in range(7):
            self.torneo(f'Propio {indice}', organizador=self.usuario)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertEqual([f['partida'].pk for f in respuesta.context['proximas_partidas']], [pronto.pk, tarde.pk, sin_hora.pk])
        self.assertEqual(len(respuesta.context['destacados']), 3)
        self.assertEqual(len(respuesta.context['mis_torneos']), 3)
        self.assertContains(respuesta, 'class="rk-compact-row"', count=3)
        self.assertContains(respuesta, 'class="rk-match-row"', count=3)

    def test_mis_torneos_prioriza_activos_sobre_terminales_recientes(self):
        activos = [self.torneo(f'Activo {indice}', organizador=self.usuario) for indice in range(3)]
        self.torneo('Finalizado reciente', organizador=self.usuario, estado=Torneo.Estado.FINALIZADO)
        self.torneo('Cancelado reciente', organizador=self.usuario, estado=Torneo.Estado.CANCELADO)
        self.client.force_login(self.usuario)
        resumen = self.client.get(self.url).context['mis_torneos']
        self.assertEqual({torneo.pk for torneo in resumen}, {torneo.pk for torneo in activos})
        self.assertEqual(Torneo.objects.filter(organizador=self.usuario).count(), 5)

    def test_mis_torneos_prioriza_en_curso_y_fecha_de_atencion(self):
        ahora = timezone.now()
        en_curso = self.torneo('En curso', organizador=self.usuario, estado=Torneo.Estado.EN_CURSO)
        apertura = self.torneo('Apertura próxima', organizador=self.usuario, estado=Torneo.Estado.PROXIMAMENTE, fecha_apertura_inscripciones=ahora + timedelta(hours=1))
        cierre = self.torneo('Cierre próximo', organizador=self.usuario, fecha_cierre_inscripciones=ahora + timedelta(minutes=20))
        self.torneo('Inicio posterior', organizador=self.usuario, estado=Torneo.Estado.PREPARADO, fecha_inicio_prevista=ahora + timedelta(hours=2))
        self.torneo('Borrador sin fechas', organizador=self.usuario, estado=Torneo.Estado.BORRADOR, fecha_apertura_inscripciones=None)
        self.client.force_login(self.usuario)
        resumen = self.client.get(self.url).context['mis_torneos']
        self.assertEqual([torneo.pk for torneo in resumen], [en_curso.pk, cierre.pk, apertura.pk])

    def test_mis_torneos_sin_fechas_desempata_por_mas_recientes(self):
        torneos = [self.torneo(f'Borrador {indice}', organizador=self.usuario, estado=Torneo.Estado.BORRADOR, fecha_apertura_inscripciones=None) for indice in range(4)]
        self.client.force_login(self.usuario)
        resumen = self.client.get(self.url).context['mis_torneos']
        self.assertEqual([torneo.pk for torneo in resumen], [torneo.pk for torneo in reversed(torneos[1:])])

    def test_proximas_limita_tres_fechas_mas_cercanas_con_ambos_roles(self):
        ahora = timezone.now()
        self.partida('Sin horario')
        self.partida('Más lejana', fecha_hora_programada=ahora + timedelta(days=2))
        tercera = self.partida('Tercera', fecha_hora_programada=ahora + timedelta(hours=3))
        primera = self.partida('Primera', rol='Árbitro', fecha_hora_programada=ahora + timedelta(hours=1))
        segunda = self.partida('Segunda', fecha_hora_programada=ahora + timedelta(hours=2))
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        filas = respuesta.context['proximas_partidas']
        self.assertEqual([fila['partida'].pk for fila in filas], [primera.pk, segunda.pk, tercera.pk])
        self.assertEqual([fila['rol'] for fila in filas], ['Árbitro', 'Jugador', 'Jugador'])
        self.assertContains(respuesta, reverse('mis-torneos'))
        self.assertContains(respuesta, reverse('mis-partidas'))

    def test_estados_vacios_y_cuenta_no_operativa(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, 'Aún no has creado ningún torneo.')
        self.assertContains(respuesta, 'Sin partidas pendientes')
        self.assertContains(respuesta, 'No hay torneos destacados disponibles ahora.')
        self.assertTrue(respuesta.context['puede_crear'])
        Usuario.objects.filter(pk=self.usuario.pk).update(estado_cuenta=Usuario.EstadoCuenta.SUSPENDIDA)
        self.assertFalse(self.client.get(self.url).context['puede_crear'])

    def test_rival_por_determinar_y_css_solo_en_home(self):
        partida = self.partida('Rival futuro')
        partida.participantes.filter(posicion=2).delete()
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, 'Rival por determinar')
        self.assertContains(respuesta, 'css/dashboard.css')
        self.assertNotContains(self.client.get(reverse('catalogo-torneos')), 'css/dashboard.css')

    def test_consultas_no_crecen_por_tarjeta_o_participante(self):
        self.partida('Primera')
        self.client.force_login(self.usuario)
        self.client.get(self.url)
        with CaptureQueriesContext(connection) as inicial:
            self.client.get(self.url)
        for indice in range(5):
            self.partida(f'Adicional {indice}')
        with CaptureQueriesContext(connection) as poblado:
            respuesta = self.client.get(self.url)
        self.assertEqual(len(respuesta.context['proximas_partidas']), 3)
        self.assertEqual(len(poblado), len(inicial))