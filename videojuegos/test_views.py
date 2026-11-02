from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from usuarios.models import Usuario
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo

from .models import PerfilVideojuegoUsuario, Plataforma, RangoVideojuego, Videojuego


class VideojuegosViewsTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            username='jugador', email='jugador@example.com', password='ClaveSegura123!',
        )
        self.otro = Usuario.objects.create_user(
            username='otro', email='otro@example.com', password='ClaveSegura123!',
        )
        self.pc = Plataforma.objects.create(nombre='PC')
        self.consola = Plataforma.objects.create(nombre='Consola')
        self.videojuego = Videojuego.objects.create(nombre='Arena Battle', genero='Competitivo')
        self.videojuego.plataformas.add(self.pc)
        self.inactivo = Videojuego.objects.create(nombre='Juego Inactivo', genero='Acción', activo=False)
        self.bronce = RangoVideojuego.objects.create(videojuego=self.videojuego, nombre='Bronce', posicion=1)
        self.plata = RangoVideojuego.objects.create(videojuego=self.videojuego, nombre='Plata', posicion=2)
        self.otro_juego = Videojuego.objects.create(nombre='Otro Juego', genero='Estrategia')
        self.rango_otro = RangoVideojuego.objects.create(videojuego=self.otro_juego, nombre='Inicial', posicion=1)

    def test_presentacion_catalogo_reutiliza_imagen_fallback_y_filtros(self):
        self.videojuego.nombre = 'VideojuegoConNombreLargoSinEspacios' * 4
        self.videojuego.imagen = 'videojuegos/visual-juego.png'
        self.videojuego.save(update_fields=('nombre', 'imagen'))
        url = reverse('catalogo-videojuegos')
        completa = self.client.get(url)
        self.assertTemplateUsed(completa, 'components/videojuego_media.html')
        self.assertContains(completa, 'class="rk-tournament-card rk-game-card"', count=2)
        self.assertContains(completa, '/media/videojuegos/visual-juego.png')
        self.assertContains(completa, 'rk-card-media-fallback')
        self.assertContains(completa, 'id="id_q" name="q"', count=1)
        self.assertContains(completa, 'name="plataforma"', count=1)
        self.assertNotContains(completa, 'Juego Inactivo')
        filtrada = self.client.get(url, {'q': 'VideojuegoConNombreLargo', 'plataforma': self.pc.pk})
        self.assertEqual(list(filtrada.context['videojuegos']), [self.videojuego])
        self.assertContains(filtrada, 'value="VideojuegoConNombreLargo"')
        self.assertContains(filtrada, f'value="{self.pc.pk}" selected')
        vacia = self.client.get(url, {'q': 'Sin coincidencias', 'plataforma': self.pc.pk})
        self.assertContains(vacia, 'No encontramos videojuegos')
        self.assertNotContains(vacia, 'class="rk-tournament-card rk-game-card"')
        self.assertNotContains(vacia, 'Limpiar filtros')

    def test_presentacion_ficha_separa_informacion_publica_y_perfil_personal(self):
        self.videojuego.imagen = 'videojuegos/visual-juego.png'
        self.videojuego.instrucciones_competicion = 'InstruccionesLargasSinEspacios' * 30
        self.videojuego.save(update_fields=('imagen', 'instrucciones_competicion'))
        self.videojuego.plataformas.add(self.consola)
        url = reverse('ficha-videojuego', args=[self.videojuego.pk])
        anonima = self.client.get(url)
        self.assertContains(anonima, self.videojuego.instrucciones_competicion)
        self.assertContains(anonima, 'Plataformas compatibles del juego')
        self.assertContains(anonima, 'PC')
        self.assertContains(anonima, 'Consola')
        self.assertNotContains(anonima, 'id="personal-title"')
        self.assertNotContains(anonima, reverse('crear-perfil-videojuego', args=[self.videojuego.pk]))
        self.assertNotContains(anonima, reverse('editar-perfil-videojuego', args=[self.videojuego.pk]))
        self.client.force_login(self.usuario)
        sin_perfil = self.client.get(url)
        self.assertContains(sin_perfil, reverse('crear-perfil-videojuego', args=[self.videojuego.pk]))
        self.assertNotContains(sin_perfil, reverse('editar-perfil-videojuego', args=[self.videojuego.pk]))
        perfil = PerfilVideojuegoUsuario.objects.create(usuario=self.usuario, videojuego=self.videojuego, nick_en_juego='NicknameCompetitivoLargo' * 5, rango_declarado=self.plata)
        con_perfil = self.client.get(url)
        self.assertContains(con_perfil, perfil.nick_en_juego)
        self.assertContains(con_perfil, reverse('editar-perfil-videojuego', args=[self.videojuego.pk]))
        self.assertNotContains(con_perfil, reverse('crear-perfil-videojuego', args=[self.videojuego.pk]))
        self.assertNotContains(con_perfil, self.usuario.email)
        self.assertNotContains(con_perfil, 'Torneos relacionados')
        self.client.force_login(self.otro)
        ajena = self.client.get(url)
        self.assertNotContains(ajena, perfil.nick_en_juego)
        self.assertContains(ajena, reverse('crear-perfil-videojuego', args=[self.videojuego.pk]))
        self.client.logout()
        sin_configurar = Videojuego.objects.create(nombre='Juego sin rangos', genero='Acción')
        sin_rangos = self.client.get(reverse('ficha-videojuego', args=[sin_configurar.pk]))
        self.assertContains(sin_rangos, 'rk-card-media-fallback')
        self.assertContains(sin_rangos, 'El rango competitivo no es necesario para este videojuego.')
        self.assertContains(sin_rangos, 'Plataforma por definir')

    def test_presentacion_ficha_muchos_rangos_conserva_orden_y_perfil_sin_rango(self):
        self.client.force_login(self.usuario)
        PerfilVideojuegoUsuario.objects.create(usuario=self.usuario, videojuego=self.videojuego, nick_en_juego='JugadorSinRango')
        RangoVideojuego.objects.bulk_create([RangoVideojuego(videojuego=self.videojuego, nombre=f'Rango visual {posicion}', posicion=posicion) for posicion in range(3, 25)])
        respuesta = self.client.get(reverse('ficha-videojuego', args=[self.videojuego.pk]))
        self.assertContains(respuesta, 'Sin rango declarado')
        self.assertContains(respuesta, 'JugadorSinRango')
        contenido = respuesta.content.decode()
        anterior = contenido.index('<strong>Plata</strong>')
        for posicion in range(3, 25):
            actual = contenido.index(f'<strong>Rango visual {posicion}</strong>')
            self.assertLess(anterior, actual)
            anterior = actual

    def test_catalogo_solo_muestra_activos_y_filtra(self):
        respuesta = self.client.get(reverse('catalogo-videojuegos'))
        self.assertContains(respuesta, 'Arena Battle')
        self.assertNotContains(respuesta, 'Juego Inactivo')
        respuesta = self.client.get(reverse('catalogo-videojuegos'), {'q': 'Arena'})
        self.assertContains(respuesta, 'Arena Battle')
        respuesta = self.client.get(reverse('catalogo-videojuegos'), {'plataforma': self.pc.pk})
        self.assertContains(respuesta, 'Arena Battle')
        respuesta = self.client.get(reverse('catalogo-videojuegos'), {'q': 'No existe'})
        self.assertContains(respuesta, 'No encontramos videojuegos')

    def test_ficha_activa_muestra_rangos_en_orden_y_ficha_inactiva_no_es_publica(self):
        respuesta = self.client.get(reverse('ficha-videojuego', args=[self.videojuego.pk]))
        self.assertContains(respuesta, 'Arena Battle')
        self.assertLess(respuesta.content.index(b'<strong>Bronce</strong>'), respuesta.content.index(b'<strong>Plata</strong>'))
        self.assertEqual(self.client.get(reverse('ficha-videojuego', args=[self.inactivo.pk])).status_code, 404)

    def test_crear_perfil_requiere_login_y_restringe_rangos_al_juego(self):
        url = reverse('crear-perfil-videojuego', args=[self.videojuego.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.usuario)
        respuesta = self.client.post(url, {'nick_en_juego': 'ArenaNick', 'rango_declarado': self.rango_otro.pk})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Selecciona un rango perteneciente')
        respuesta = self.client.post(url, {'nick_en_juego': 'ArenaNick', 'rango_declarado': self.bronce.pk})
        self.assertRedirects(respuesta, reverse('ficha-videojuego', args=[self.videojuego.pk]))
        self.assertTrue(PerfilVideojuegoUsuario.objects.filter(usuario=self.usuario, videojuego=self.videojuego).exists())

    def test_no_se_pueden_crear_dos_perfiles_y_edicion_actualiza_rango(self):
        perfil = PerfilVideojuegoUsuario.objects.create(
            usuario=self.usuario, videojuego=self.videojuego, nick_en_juego='Antes', rango_declarado=self.bronce,
        )
        self.client.force_login(self.usuario)
        crear_url = reverse('crear-perfil-videojuego', args=[self.videojuego.pk])
        respuesta = self.client.get(crear_url)
        self.assertRedirects(respuesta, reverse('editar-perfil-videojuego', args=[self.videojuego.pk]))
        fecha_anterior = timezone.now() - timedelta(days=1)
        PerfilVideojuegoUsuario.objects.filter(pk=perfil.pk).update(fecha_actualizacion_rango=fecha_anterior)
        respuesta = self.client.post(
            reverse('editar-perfil-videojuego', args=[self.videojuego.pk]),
            {'nick_en_juego': 'Despues', 'rango_declarado': self.plata.pk},
        )
        self.assertRedirects(respuesta, reverse('ficha-videojuego', args=[self.videojuego.pk]))
        perfil.refresh_from_db()
        self.assertEqual(perfil.nick_en_juego, 'Despues')
        self.assertEqual(perfil.rango_declarado, self.plata)
        self.assertGreater(perfil.fecha_actualizacion_rango, fecha_anterior)

    def test_edicion_solo_afecta_al_propietario_y_no_cambia_nickname_publico(self):
        perfil_otro = PerfilVideojuegoUsuario.objects.create(
            usuario=self.otro, videojuego=self.videojuego, nick_en_juego='NickOtro', rango_declarado=self.bronce,
        )
        self.client.force_login(self.usuario)
        respuesta = self.client.post(
            reverse('editar-perfil-videojuego', args=[self.videojuego.pk]),
            {'nick_en_juego': 'NickPropio', 'rango_declarado': self.bronce.pk},
        )
        self.assertEqual(respuesta.status_code, 404)
        perfil_otro.refresh_from_db()
        self.assertEqual(perfil_otro.nick_en_juego, 'NickOtro')

    def test_perfil_publico_muestra_solo_perfiles_de_juegos_activos_y_sin_edicion_ajena(self):
        PerfilVideojuegoUsuario.objects.create(
            usuario=self.usuario, videojuego=self.videojuego, nick_en_juego='ArenaNick', rango_declarado=self.plata,
        )
        PerfilVideojuegoUsuario.objects.create(
            usuario=self.usuario, videojuego=self.inactivo, nick_en_juego='Oculto',
        )
        respuesta = self.client.get(reverse('perfil', args=[self.usuario.pk]))
        self.assertContains(respuesta, 'ArenaNick')
        self.assertNotContains(respuesta, 'Oculto')
        self.assertNotContains(respuesta, 'Editar perfil de juego')
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('perfil-propio'), follow=True)
        self.assertContains(respuesta, 'Editar perfil de juego')

    def test_historial_de_inscripcion_no_cambia_al_editar_perfil(self):
        perfil = PerfilVideojuegoUsuario.objects.create(
            usuario=self.usuario, videojuego=self.videojuego, nick_en_juego='Historico', rango_declarado=self.bronce,
        )
        formato = FormatoCompetitivo.objects.create(nombre='Formato test', min_participantes=2, max_participantes=128)
        torneo = Torneo.objects.create(
            nombre='Torneo', videojuego=self.videojuego, organizador=self.otro,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=formato, max_participantes=2,
            estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
        )
        inscripcion = InscripcionTorneo.objects.create(
            torneo=torneo, usuario=self.usuario, perfil_videojuego=perfil,
            nick_historico='Historico', rango_declarado_al_inscribirse=self.bronce,
        )
        self.client.force_login(self.usuario)
        self.client.post(
            reverse('editar-perfil-videojuego', args=[self.videojuego.pk]),
            {'nick_en_juego': 'Actual', 'rango_declarado': self.plata.pk},
        )
        inscripcion.refresh_from_db()
        self.assertEqual(inscripcion.nick_historico, 'Historico')
        self.assertEqual(inscripcion.rango_declarado_al_inscribirse, self.bronce)
