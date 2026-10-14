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

    def test_mis_torneos_solo_inscripciones_confirmadas_en_todos_los_tipos(self):
        inscritos = [self.torneo(f'Inscrito {tipo}', tipo=tipo) for tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL, Torneo.Tipo.PRIVADO)]
        for torneo in inscritos:
            self.inscribir(torneo, self.usuario)
        self.inscribir(inscritos[0], self.rival)
        self.torneo('Organizado sin jugar', organizador=self.usuario, tipo=Torneo.Tipo.PRIVADO)
        cancelado = self.torneo('Inscripción cancelada', tipo=Torneo.Tipo.PRIVADO)
        self.inscribir(cancelado, self.usuario, InscripcionTorneo.Estado.CANCELADA)
        descalificado = self.torneo('Inscripción descalificada', tipo=Torneo.Tipo.PRIVADO)
        self.inscribir(descalificado, self.usuario, InscripcionTorneo.Estado.DESCALIFICADA)
        self.torneo('Privado de otro', tipo=Torneo.Tipo.PRIVADO)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertEqual({t.pk for t in respuesta.context['mis_torneos']}, {t.pk for t in inscritos})
        self.assertEqual(next(t.confirmados for t in respuesta.context['mis_torneos'] if t.pk == inscritos[0].pk), 2)
        self.assertContains(respuesta, '<a class="rk-section-link" href="' + reverse('mis-participaciones') + '">Ver todos')
        self.assertNotContains(respuesta, '<a class="rk-section-link" href="' + reverse('mis-torneos') + '">Ver todos')
        for nombre in ('Organizado sin jugar', 'Inscripción cancelada', 'Inscripción descalificada', 'Privado de otro'):
            self.assertNotContains(respuesta, nombre)

    def test_organizar_no_basta_para_aparecer_en_mis_torneos(self):
        self.torneo('Solo organizado', organizador=self.usuario)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(self.url).context['mis_torneos'], [])

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
            self.inscribir(self.torneo(f'Inscrito {indice}'), self.usuario)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertEqual([f['partida'].pk for f in respuesta.context['proximas_partidas']], [pronto.pk, tarde.pk, sin_hora.pk])
        self.assertEqual(len(respuesta.context['destacados']), 3)
        self.assertContains(respuesta, 'rk-tournament-card--catalogue', count=3)
        self.assertContains(respuesta, 'rk-card-banner-badges', count=3)
        self.assertNotContains(respuesta, 'css/catalogue.css')
        self.assertEqual(len(respuesta.context['mis_torneos']), 3)
        self.assertContains(respuesta, 'class="rk-compact-row"', count=3)
        self.assertContains(respuesta, 'class="rk-match-row"', count=3)

    def test_mis_torneos_prioriza_activos_sobre_terminales_recientes(self):
        activos = [self.torneo(f'Activo {indice}') for indice in range(3)]
        terminales = [self.torneo('Finalizado reciente', estado=Torneo.Estado.FINALIZADO), self.torneo('Cancelado reciente', estado=Torneo.Estado.CANCELADO)]
        for torneo in activos + terminales:
            self.inscribir(torneo, self.usuario)
        self.client.force_login(self.usuario)
        resumen = self.client.get(self.url).context['mis_torneos']
        self.assertEqual({torneo.pk for torneo in resumen}, {torneo.pk for torneo in activos})
        self.assertEqual(InscripcionTorneo.objects.filter(usuario=self.usuario).count(), 5)

    def test_mis_torneos_prioriza_en_curso_y_fecha_de_atencion(self):
        ahora = timezone.now()
        en_curso = self.torneo('En curso', estado=Torneo.Estado.EN_CURSO)
        apertura = self.torneo('Apertura próxima', estado=Torneo.Estado.PROXIMAMENTE, fecha_apertura_inscripciones=ahora + timedelta(hours=1))
        cierre = self.torneo('Cierre próximo', fecha_cierre_inscripciones=ahora + timedelta(minutes=20))
        posterior = self.torneo('Inicio posterior', estado=Torneo.Estado.PREPARADO, fecha_inicio_prevista=ahora + timedelta(hours=2))
        sin_fechas = self.torneo('Borrador sin fechas', estado=Torneo.Estado.BORRADOR, fecha_apertura_inscripciones=None)
        for torneo in (en_curso, apertura, cierre, posterior, sin_fechas):
            self.inscribir(torneo, self.usuario)
        self.client.force_login(self.usuario)
        resumen = self.client.get(self.url).context['mis_torneos']
        self.assertEqual([torneo.pk for torneo in resumen], [en_curso.pk, cierre.pk, apertura.pk])

    def test_mis_torneos_sin_fechas_desempata_por_mas_recientes(self):
        torneos = [self.torneo(f'Borrador {indice}', estado=Torneo.Estado.BORRADOR, fecha_apertura_inscripciones=None) for indice in range(4)]
        for torneo in torneos:
            self.inscribir(torneo, self.usuario)
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
        self.assertContains(respuesta, 'Aún no estás inscrito en ningún torneo.')
        self.assertContains(respuesta, 'Sin partidas pendientes')
        self.assertContains(respuesta, 'No hay torneos destacados disponibles ahora.')
        self.assertTrue(respuesta.context['puede_crear'])
        Usuario.objects.filter(pk=self.usuario.pk).update(estado_cuenta=Usuario.EstadoCuenta.SUSPENDIDA)
        self.assertFalse(self.client.get(self.url).context['puede_crear'])

    def test_rival_por_determinar_y_css_compartido_no_afecta_ficha(self):
        partida = self.partida('Rival futuro')
        partida.participantes.filter(posicion=2).delete()
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertContains(respuesta, 'Rival por determinar')
        self.assertContains(respuesta, 'css/dashboard.css')
        self.assertContains(self.client.get(reverse('catalogo-torneos')), 'css/dashboard.css')
        self.assertNotContains(self.client.get(reverse('ficha-torneo', args=[partida.torneo_id])), 'css/dashboard.css')

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