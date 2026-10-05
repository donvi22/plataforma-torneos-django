from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from partidas.services import generar_bracket
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego


class BuscarUsuariosTests(TestCase):
    def setUp(self):
        self.url = reverse('buscar-usuarios')
        self.ana = Usuario.objects.create_user(username='Anaprueba', email='secreto-ana@example.com', password='ClaveSegura123!')
        self.bob = Usuario.objects.create_user(username='bobprueba', email='bob@example.com', password='ClaveSegura123!')

    def test_busqueda_publica_parcial_e_insensible_a_mayusculas(self):
        respuesta = self.client.get(self.url, {'q': 'ANAPRU'})
        self.assertContains(respuesta, 'Anaprueba')
        self.assertContains(respuesta, reverse('perfil', args=[self.ana.pk]))
        self.assertNotContains(respuesta, 'bobprueba')

    def test_no_expone_email_ni_datos_privados(self):
        respuesta = self.client.get(self.url, {'q': 'prueba'})
        self.assertNotContains(respuesta, 'secreto-ana@example.com')
        self.assertNotContains(respuesta, 'bob@example.com')

    def test_consulta_vacia_no_es_error_ni_lista_usuarios(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'Anaprueba')
        self.assertNotContains(respuesta, 'No se encontraron')

    def test_sin_resultados(self):
        self.assertContains(self.client.get(self.url, {'q': 'zzzz'}), 'No se encontraron usuarios con ese nickname.')

    def test_excluye_eliminados_y_no_activos(self):
        Usuario.objects.filter(pk=self.bob.pk).update(estado_cuenta=Usuario.EstadoCuenta.ELIMINADA, is_active=False, username='usuario_eliminado_9')
        respuesta = self.client.get(self.url, {'q': 'eliminado'})
        self.assertNotContains(respuesta, 'usuario_eliminado_9')
        self.assertContains(respuesta, 'No se encontraron')

    def test_paginacion_de_20(self):
        for i in range(21):
            Usuario.objects.create_user(username=f'masivo{i:02d}', email=f'm{i}@example.com', password='ClaveSegura123!')
        pagina1 = self.client.get(self.url, {'q': 'masivo'})
        self.assertEqual(len(pagina1.context['page_obj']), 20)
        self.assertContains(pagina1, 'Siguiente')
        self.assertEqual(len(self.client.get(self.url, {'q': 'masivo', 'page': 2}).context['page_obj']), 1)

    def test_muestra_perfiles_de_videojuegos_activos(self):
        activo = Videojuego.objects.create(nombre='Juego visible', genero='X')
        oculto = Videojuego.objects.create(nombre='Juego oculto', genero='X', activo=False)
        PerfilVideojuegoUsuario.objects.create(usuario=self.ana, videojuego=activo, nick_en_juego='a')
        PerfilVideojuegoUsuario.objects.create(usuario=self.ana, videojuego=oculto, nick_en_juego='b')
        respuesta = self.client.get(self.url, {'q': 'Anaprueba'})
        self.assertContains(respuesta, 'Juego visible')
        self.assertNotContains(respuesta, 'Juego oculto')

    def test_perfil_eliminado_sigue_siendo_neutral(self):
        Usuario.objects.filter(pk=self.bob.pk).update(estado_cuenta=Usuario.EstadoCuenta.ELIMINADA, is_active=False)
        self.assertEqual(self.client.get(reverse('perfil', args=[self.bob.pk])).status_code, 404)


class EnlacesPerfilTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(username='orgenlaces', email='orgenlaces@example.com', password='ClaveSegura123!')
        self.game = Videojuego.objects.create(nombre='Juego enlaces', genero='Competitivo')
        self.format = FormatoCompetitivo.objects.create(nombre='Formato enlaces', min_participantes=2, max_participantes=128)

    def _ejecutar(self):
        torneo = Torneo.objects.create(
            nombre='Torneo enlaces', videojuego=self.game, organizador=self.organizador,
            tipo=Torneo.Tipo.PUBLICO, formato_competitivo=self.format, max_participantes=8,
            estado=Torneo.Estado.INSCRIPCIONES_CERRADAS, fecha_publicacion=timezone.now(),
        )
        for index in range(8):
            usuario = Usuario.objects.create_user(username=f'jugadorbracket{index}', email=f'jb{index}@example.com', password='ClaveSegura123!')
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=self.game, nick_en_juego=f'Nick{index}')
            InscripcionTorneo.objects.create(torneo=torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=f'Nick histórico {index}')
        generar_bracket(torneo)
        return torneo

    def test_participante_activo_enlaza_a_su_perfil_actual(self):
        torneo = self._ejecutar()
        usuario = Usuario.objects.get(username='jugadorbracket0')
        Usuario.objects.filter(pk=usuario.pk).update(username='nuevonick')
        enlace = f'href="{reverse("perfil", args=[usuario.pk])}">Nick histórico 0</a>'
        for nombre in ('ficha-torneo', 'bracket-torneo'):
            html = self.client.get(reverse(nombre, args=[torneo.pk])).content.decode()
            self.assertIn(enlace, html)
            self.assertNotIn('nuevonick', html)

    def test_cuenta_eliminada_mantiene_texto_historico_sin_enlace(self):
        torneo = self._ejecutar()
        usuario = Usuario.objects.get(username='jugadorbracket0')
        Usuario.objects.filter(pk=usuario.pk).update(
            estado_cuenta=Usuario.EstadoCuenta.ELIMINADA, is_active=False, username=f'usuario_eliminado_{usuario.pk}',
        )
        for nombre in ('ficha-torneo', 'bracket-torneo'):
            html = self.client.get(reverse(nombre, args=[torneo.pk])).content.decode()
            self.assertIn('Nick histórico 0', html)
            self.assertNotIn(reverse('perfil', args=[usuario.pk]), html)
            self.assertNotIn('usuario_eliminado_', html)

    def test_organizador_eliminado_se_muestra_neutral(self):
        torneo = self._ejecutar()
        Usuario.objects.filter(pk=self.organizador.pk).update(
            estado_cuenta=Usuario.EstadoCuenta.ELIMINADA, is_active=False, username='usuario_eliminado_77',
        )
        html = self.client.get(reverse('ficha-torneo', args=[torneo.pk])).content.decode()
        self.assertIn('Usuario eliminado', html)
        self.assertNotIn('usuario_eliminado_77', html)

    def test_detalle_partida_enlaza_participantes(self):
        torneo = self._ejecutar()
        partida = torneo.partidas.first()
        participante = partida.participantes.get(posicion=1).inscripcion
        html = self.client.get(reverse('detalle-partida', args=[partida.pk])).content.decode()
        self.assertIn(f'href="{reverse("perfil", args=[participante.usuario_id])}">{participante.nick_historico}</a>', html)

    def test_cabecera_con_buscador(self):
        self.assertContains(self.client.get(reverse('catalogo-torneos')), reverse('buscar-usuarios'))
