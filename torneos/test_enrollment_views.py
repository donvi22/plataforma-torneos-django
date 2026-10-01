from datetime import timedelta

from django.test import TestCase
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
