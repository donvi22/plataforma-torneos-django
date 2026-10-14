from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, RangoVideojuego, Videojuego

from .models import FormatoCompetitivo, InscripcionTorneo, Torneo


class EnrollmentViewsTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(
            username='organizador', email='organizador@example.com', password='ClaveSegura123!',
        )
        self.usuario = Usuario.objects.create_user(
            username='jugador', email='jugador@example.com', password='ClaveSegura123!',
            nivel=5, karma_total=150,
        )
        self.otro = Usuario.objects.create_user(
            username='otro', email='otro@example.com', password='ClaveSegura123!',
        )
        self.videojuego = Videojuego.objects.create(nombre='Arena Battle', genero='Competitivo')
        self.rango = RangoVideojuego.objects.create(videojuego=self.videojuego, nombre='Bronce', posicion=1)
        self.formato = FormatoCompetitivo.objects.create(
            nombre='Eliminacion directa', min_participantes=2, max_participantes=128,
        )
        self.perfil = PerfilVideojuegoUsuario.objects.create(
            usuario=self.usuario, videojuego=self.videojuego,
            nick_en_juego='JugadorArena', rango_declarado=self.rango,
        )

    def crear_torneo(self, tipo=Torneo.Tipo.PUBLICO, estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS, max_participantes=2):
        ahora = timezone.now()
        return Torneo.objects.create(
            nombre=f'Torneo {Torneo.objects.count()}', videojuego=self.videojuego,
            organizador=self.organizador, tipo=tipo, formato_competitivo=self.formato,
            max_participantes=max_participantes, estado=estado,
            fecha_publicacion=ahora, fecha_apertura_inscripciones=ahora - timedelta(minutes=5),
            fecha_cierre_inscripciones=ahora + timedelta(minutes=30),
        )

    def test_inscripcion_correcta_desde_ficha_y_snapshot_historico(self):
        torneo = self.crear_torneo()
        self.client.force_login(self.usuario)
        respuesta = self.client.post(reverse('inscribirse-torneo', args=[torneo.pk]), follow=True)
        self.assertContains(respuesta, 'Te has inscrito correctamente')
        inscripcion = InscripcionTorneo.objects.get(torneo=torneo, usuario=self.usuario)
        self.assertEqual(inscripcion.nick_historico, 'JugadorArena')
        self.assertEqual(inscripcion.rango_declarado_al_inscribirse, self.rango)
        self.assertContains(respuesta, '1/2 participantes confirmados')

    def test_get_no_inscribe_y_usuario_anonimo_va_al_login(self):
        torneo = self.crear_torneo()
        self.assertEqual(self.client.get(reverse('inscribirse-torneo', args=[torneo.pk])).status_code, 302)
        self.assertEqual(InscripcionTorneo.objects.count(), 0)
        respuesta = self.client.get(reverse('ficha-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'Inicia sesión para inscribirte')

    def test_no_puede_inscribir_a_otro_usuario_manipulando_parametros(self):
        torneo = self.crear_torneo()
        self.client.force_login(self.usuario)
        respuesta = self.client.post(
            reverse('inscribirse-torneo', args=[torneo.pk]),
            {'usuario': self.otro.pk},
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(InscripcionTorneo.objects.filter(usuario=self.usuario, torneo=torneo).exists())
        self.assertFalse(InscripcionTorneo.objects.filter(usuario=self.otro, torneo=torneo).exists())

    def test_rechaza_duplicada_completa_y_sin_perfil(self):
        torneo = self.crear_torneo()
        self.client.force_login(self.usuario)
        self.client.post(reverse('inscribirse-torneo', args=[torneo.pk]))
        respuesta = self.client.post(reverse('inscribirse-torneo', args=[torneo.pk]), follow=True)
        self.assertContains(respuesta, 'ya tiene una inscripción')
        otro = Usuario.objects.create_user(username='sinperfil', email='sinperfil@example.com', password='ClaveSegura123!')
        self.client.force_login(otro)
        respuesta = self.client.get(reverse('ficha-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'Crear perfil de videojuego')

    def test_cancelacion_post_libera_plaza_y_get_no_cambia(self):
        torneo = self.crear_torneo()
        inscripcion = InscripcionTorneo.objects.create(
            torneo=torneo, usuario=self.usuario, perfil_videojuego=self.perfil,
            nick_historico='JugadorArena', rango_declarado_al_inscribirse=self.rango,
        )
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('cancelar-inscripcion', args=[inscripcion.pk]))
        self.assertEqual(respuesta.status_code, 405)
        inscripcion.refresh_from_db()
        self.assertEqual(inscripcion.estado, InscripcionTorneo.Estado.CONFIRMADA)
        respuesta = self.client.post(reverse('cancelar-inscripcion', args=[inscripcion.pk]), follow=True)
        self.assertContains(respuesta, 'ha cancelado')
        inscripcion.refresh_from_db()
        self.assertEqual(inscripcion.estado, InscripcionTorneo.Estado.CANCELADA)
        self.assertEqual(torneo.participantes_confirmados, 0)

    def test_no_puede_cancelar_inscripcion_ajena_ni_cerrada(self):
        torneo = self.crear_torneo()
        inscripcion = InscripcionTorneo.objects.create(
            torneo=torneo, usuario=self.usuario, perfil_videojuego=self.perfil,
            nick_historico='JugadorArena',
        )
        self.client.force_login(self.otro)
        self.assertEqual(self.client.post(reverse('cancelar-inscripcion', args=[inscripcion.pk])).status_code, 404)
        self.client.force_login(self.usuario)
        torneo.estado = Torneo.Estado.INSCRIPCIONES_CERRADAS
        torneo.save(update_fields=('estado',))
        respuesta = self.client.post(reverse('cancelar-inscripcion', args=[inscripcion.pk]), follow=True)
        self.assertContains(respuesta, 'ya están cerradas')

    def test_reinscripcion_cancelada_se_rechaza_y_se_conserva_en_participaciones(self):
        torneo = self.crear_torneo()
        inscripcion = InscripcionTorneo.objects.create(
            torneo=torneo, usuario=self.usuario, perfil_videojuego=self.perfil,
            nick_historico='JugadorArena', estado=InscripcionTorneo.Estado.CANCELADA,
        )
        self.client.force_login(self.usuario)
        respuesta = self.client.post(reverse('inscribirse-torneo', args=[torneo.pk]), follow=True)
        self.assertContains(respuesta, 'ya tiene una inscripción')
        listado = self.client.get(reverse('mis-participaciones'))
        self.assertContains(listado, 'Cancelada')
        self.assertContains(listado, torneo.nombre)

    def test_privado_no_ofrece_inscripcion_web(self):
        torneo = self.crear_torneo(tipo=Torneo.Tipo.PRIVADO)
        self.assertEqual(self.client.get(reverse('ficha-torneo', args=[torneo.pk])).status_code, 404)
        self.client.force_login(self.organizador)
        respuesta = self.client.get(reverse('ficha-torneo', args=[torneo.pk]))
        self.assertNotContains(respuesta, 'Inscribirme')
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.post(reverse('inscribirse-torneo', args=[torneo.pk])).status_code, 404)

    def test_participaciones_solo_muestra_las_propias(self):
        torneo = self.crear_torneo()
        perfil_otro = PerfilVideojuegoUsuario.objects.create(
            usuario=self.otro, videojuego=self.videojuego, nick_en_juego='Otro',
        )
        InscripcionTorneo.objects.create(
            torneo=torneo, usuario=self.otro, perfil_videojuego=perfil_otro, nick_historico='Otro',
        )
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('mis-participaciones'))
        self.assertNotContains(respuesta, 'Otro')
        self.assertNotContains(respuesta, torneo.nombre)

    def crear_inscripcion(self, torneo, estado=InscripcionTorneo.Estado.CONFIRMADA, **extra):
        return InscripcionTorneo.objects.create(
            torneo=torneo, usuario=self.usuario, perfil_videojuego=self.perfil,
            nick_historico='JugadorArena', estado=estado, **extra,
        )

    def test_participaciones_conserva_tipos_terminales_y_canceladas(self):
        publico = self.crear_inscripcion(self.crear_torneo())
        oficial = self.crear_inscripcion(self.crear_torneo(tipo=Torneo.Tipo.OFICIAL, estado=Torneo.Estado.FINALIZADO))
        privado = self.crear_inscripcion(self.crear_torneo(tipo=Torneo.Tipo.PRIVADO, estado=Torneo.Estado.CANCELADO))
        cancelada = self.crear_inscripcion(self.crear_torneo(), estado=InscripcionTorneo.Estado.CANCELADA)
        descalificada = self.crear_inscripcion(self.crear_torneo(), estado=InscripcionTorneo.Estado.DESCALIFICADA)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('mis-participaciones'))
        self.assertEqual({fila.pk for fila in respuesta.context['inscripciones']}, {fila.pk for fila in (publico, oficial, privado, cancelada, descalificada)})
        for texto in ('Público', 'Oficial', 'Privado', 'Finalizado', 'Cancelado', 'Cancelada', 'Descalificada', 'JugadorArena', 'Estado del torneo', 'Tu inscripción'):
            self.assertContains(respuesta, texto)

    def test_filtros_participaciones_no_confunden_cancelacion_con_activa(self):
        activa = self.crear_inscripcion(self.crear_torneo())
        cancelada = self.crear_inscripcion(self.crear_torneo(), estado=InscripcionTorneo.Estado.CANCELADA)
        finalizada = self.crear_inscripcion(self.crear_torneo(estado=Torneo.Estado.FINALIZADO))
        self.client.force_login(self.usuario)
        url = reverse('mis-participaciones')
        activos = self.client.get(url, {'vista': 'activos'})
        self.assertEqual([fila.pk for fila in activos.context['inscripciones']], [activa.pk])
        finalizados = self.client.get(url, {'vista': 'finalizados'})
        self.assertEqual([fila.pk for fila in finalizados.context['inscripciones']], [finalizada.pk])
        invalido = self.client.get(url, {'vista': 'no-valida', 'usuario': self.otro.pk})
        self.assertEqual(invalido.context['vista'], 'todos')
        self.assertEqual(len(invalido.context['inscripciones']), 3)
        self.assertEqual(InscripcionTorneo.objects.get(pk=cancelada.pk).estado, InscripcionTorneo.Estado.CANCELADA)

    def test_orden_participaciones_prioriza_activos_y_fecha_proxima(self):
        ahora = timezone.now()
        lejano = self.crear_torneo()
        cercano = self.crear_torneo()
        Torneo.objects.filter(pk=lejano.pk).update(fecha_cierre_inscripciones=ahora + timedelta(hours=4))
        Torneo.objects.filter(pk=cercano.pk).update(fecha_cierre_inscripciones=ahora + timedelta(minutes=10))
        lejos = self.crear_inscripcion(lejano)
        cerca = self.crear_inscripcion(cercano)
        curso = self.crear_inscripcion(self.crear_torneo(estado=Torneo.Estado.EN_CURSO))
        final = self.crear_inscripcion(self.crear_torneo(estado=Torneo.Estado.FINALIZADO), fecha_cancelacion=ahora - timedelta(days=20))
        Torneo.objects.filter(pk=final.torneo_id).update(fecha_fin_real=ahora)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('mis-participaciones'))
        filas = list(respuesta.context['inscripciones'])
        self.assertEqual([fila.pk for fila in filas], [curso.pk, cerca.pk, lejos.pk, final.pk])
        self.assertEqual(filas[-1].fecha_historial, ahora)

    def test_participaciones_vacias_y_login(self):
        url = reverse('mis-participaciones')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(url)
        self.assertContains(respuesta, 'Aún no tienes participaciones')
        self.assertContains(respuesta, 'Explorar torneos')
        self.assertContains(self.client.get(url, {'vista': 'activos'}), 'No tienes participaciones activas')

    def test_participaciones_conteo_completo_sin_n_mas_uno(self):
        torneo = self.crear_torneo()
        self.crear_inscripcion(torneo)
        otro_perfil = PerfilVideojuegoUsuario.objects.create(usuario=self.otro, videojuego=self.videojuego, nick_en_juego='Otro')
        InscripcionTorneo.objects.create(torneo=torneo, usuario=self.otro, perfil_videojuego=otro_perfil, nick_historico='Otro')
        self.client.force_login(self.usuario)
        self.client.get(reverse('mis-participaciones'))
        with CaptureQueriesContext(connection) as inicial:
            primera = self.client.get(reverse('mis-participaciones'))
        self.assertEqual(primera.context['inscripciones'][0].confirmados, 2)
        for indice in range(5):
            self.crear_inscripcion(self.crear_torneo())
        with CaptureQueriesContext(connection) as poblado:
            respuesta = self.client.get(reverse('mis-participaciones'))
        self.assertEqual(len(respuesta.context['inscripciones']), 6)
        self.assertEqual(len(poblado), len(inicial))

    def test_miniatura_participacion_reutiliza_banner_y_fallback_de_tarjeta(self):
        torneo = self.crear_torneo()
        self.crear_inscripcion(torneo)
        self.client.force_login(self.usuario)
        for banner, logo in (
            ('torneos/banner-prueba.png', 'videojuegos/logo-prueba.png'),
            ('', 'videojuegos/logo-prueba.png'),
            ('', ''),
        ):
            with self.subTest(banner=banner, logo=logo):
                Torneo.objects.filter(pk=torneo.pk).update(imagen_banner=banner)
                Videojuego.objects.filter(pk=self.videojuego.pk).update(imagen=logo)
                respuesta = self.client.get(reverse('mis-participaciones'))
                self.assertTemplateUsed(respuesta, 'components/torneo_media.html')
                self.assertContains(respuesta, 'rk-participation-thumbnail')
                self.assertContains(respuesta, 'JugadorArena')
                self.assertNotContains(respuesta, 'rk-participation-logo')
                actual = Torneo.objects.select_related('videojuego').get(pk=torneo.pk)
                actual.confirmados = 1
                media = render_to_string('components/torneo_media.html', {'torneo': actual, 'variant': 'thumbnail'})
                tarjeta = render_to_string('components/torneo_card.html', {'torneo': actual, 'variant': 'catalogue'})
                if banner:
                    self.assertContains(respuesta, '/media/' + banner)
                    self.assertIn('/media/' + banner, tarjeta)
                    self.assertNotIn('rk-card-game-image', media)
                elif logo:
                    self.assertContains(respuesta, 'rk-card-media--fallback')
                    self.assertIn('rk-card-game-image', media)
                    self.assertIn('/media/' + logo, tarjeta)
                else:
                    self.assertContains(respuesta, 'rk-card-media-fallback')
                    self.assertIn('rk-card-media-fallback', tarjeta)
