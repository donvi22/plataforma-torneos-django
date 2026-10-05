from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from html.parser import HTMLParser

from partidas.services import generar_bracket
from torneos.models import ClasificacionTorneo, FormatoCompetitivo, InscripcionTorneo, Torneo
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego


class _Anidadas(HTMLParser):
    def __init__(self):
        super().__init__()
        self.abiertas = 0
        self.anidadas = False

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.anidadas = self.anidadas or self.abiertas > 0
            self.abiertas += 1

    def handle_endtag(self, tag):
        if tag == 'a':
            self.abiertas -= 1


def _anclas_anidadas(html):
    """Devuelve '<a' si hay un enlace dentro de otro, y cadena vacía en caso contrario."""
    parser = _Anidadas()
    parser.feed(html)
    return '<a' if parser.anidadas else ''


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

    def test_participante_activo_muestra_nick_historico_y_username_actual(self):
        torneo = self._ejecutar()
        usuario = Usuario.objects.get(username='jugadorbracket0')
        Usuario.objects.filter(pk=usuario.pk).update(username='nuevonick')
        enlace = f'<a class="text-link" href="{reverse("perfil", args=[usuario.pk])}">@nuevonick</a>'
        for nombre in ('ficha-torneo', 'bracket-torneo'):
            html = self.client.get(reverse(nombre, args=[torneo.pk])).content.decode()
            self.assertIn('<span class="player-nick">Nick histórico 0</span>', html)
            self.assertIn(enlace, html)
            self.assertNotIn('>Nick histórico 0</a>', html)
            self.assertNotIn('jugadorbracket0', html)

    def test_cuenta_eliminada_mantiene_nick_historico_y_etiqueta(self):
        torneo = self._ejecutar()
        usuario = Usuario.objects.get(username='jugadorbracket0')
        Usuario.objects.filter(pk=usuario.pk).update(
            estado_cuenta=Usuario.EstadoCuenta.ELIMINADA, is_active=False, username=f'usuario_eliminado_{usuario.pk}',
        )
        for nombre in ('ficha-torneo', 'bracket-torneo'):
            html = self.client.get(reverse(nombre, args=[torneo.pk])).content.decode()
            self.assertIn('<span class="player-nick">Nick histórico 0</span><span class="player-account">Usuario eliminado</span>', html)
            self.assertNotIn(reverse('perfil', args=[usuario.pk]), html)
            self.assertNotIn('usuario_eliminado_', html)

    def test_clasificacion_usa_ambas_identidades_en_ficha_y_bracket(self):
        torneo = self._ejecutar()
        inscripciones = list(torneo.inscripciones.order_by('pk')[:2])
        ClasificacionTorneo.objects.create(torneo=torneo, inscripcion=inscripciones[0], posicion=1, es_campeon=True)
        ClasificacionTorneo.objects.create(torneo=torneo, inscripcion=inscripciones[1], posicion=2, ronda_eliminado=3)
        Usuario.objects.filter(pk=inscripciones[1].usuario_id).update(
            estado_cuenta=Usuario.EstadoCuenta.ELIMINADA, is_active=False, username='usuario_eliminado_5',
        )
        Torneo.objects.filter(pk=torneo.pk).update(estado=Torneo.Estado.FINALIZADO)
        for nombre in ('ficha-torneo', 'bracket-torneo'):
            html = self.client.get(reverse(nombre, args=[torneo.pk])).content.decode()
            self.assertIn('<strong>1.</strong> <span class="player-identity"><span class="player-nick">Nick histórico 0</span>', html)
            self.assertIn('>@jugadorbracket0</a>', html)
            self.assertIn('<strong>2.</strong> <span class="player-identity"><span class="player-nick">Nick histórico 1</span><span class="player-account">Usuario eliminado</span>', html)
            self.assertNotIn('usuario_eliminado_', html)

    def test_bracket_conserva_enlace_a_partida_sin_anclas_anidadas(self):
        torneo = self._ejecutar()
        partida = torneo.partidas.first()
        for nombre in ('bracket-torneo', 'ficha-torneo', 'detalle-partida'):
            args = [partida.pk] if nombre == 'detalle-partida' else [torneo.pk]
            html = self.client.get(reverse(nombre, args=args)).content.decode()
            self.assertNotIn('<a', _anclas_anidadas(html))
        html = self.client.get(reverse('bracket-torneo', args=[torneo.pk])).content.decode()
        self.assertIn(f'<a class="match-link" href="{reverse("detalle-partida", args=[partida.pk])}">Partida {partida.numero_orden}</a>', html)

    def test_organizador_eliminado_se_muestra_neutral(self):
        torneo = self._ejecutar()
        Usuario.objects.filter(pk=self.organizador.pk).update(
            estado_cuenta=Usuario.EstadoCuenta.ELIMINADA, is_active=False, username='usuario_eliminado_77',
        )
        html = self.client.get(reverse('ficha-torneo', args=[torneo.pk])).content.decode()
        self.assertIn('Usuario eliminado', html)
        self.assertNotIn('usuario_eliminado_77', html)

    def test_organizador_y_busqueda_siguen_usando_username(self):
        torneo = self._ejecutar()
        self.assertContains(self.client.get(reverse('ficha-torneo', args=[torneo.pk])), '>orgenlaces</a>')
        respuesta = self.client.get(reverse('buscar-usuarios'), {'q': 'jugadorbracket0'})
        self.assertContains(respuesta, '>jugadorbracket0</a>')
        self.assertNotContains(respuesta, 'Nick histórico')
        self.assertNotContains(respuesta, 'Nick0')

    def test_detalle_partida_usa_ambas_identidades(self):
        torneo = self._ejecutar()
        partida = torneo.partidas.first()
        participante = partida.participantes.get(posicion=1).inscripcion
        html = self.client.get(reverse('detalle-partida', args=[partida.pk])).content.decode()
        self.assertIn(f'<span class="player-nick">{participante.nick_historico}</span>', html)
        self.assertIn(f'href="{reverse("perfil", args=[participante.usuario_id])}">@{participante.usuario.username}</a>', html)

    def test_identidad_se_renderiza_en_dos_bloques_sin_br(self):
        torneo = self._ejecutar()
        html = self.client.get(reverse('ficha-torneo', args=[torneo.pk])).content.decode()
        self.assertRegex(
            html,
            r'<span class="player-identity"><span class="player-nick">Nick histórico 0</span>'
            r'<span class="player-account"><a [^>]*>@jugadorbracket0</a></span></span>',
        )
        self.assertNotIn('<br', html.split('player-identity')[1].split('</li>')[0])
        css = (settings.BASE_DIR / 'static' / 'css' / 'site.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'\.player-nick, \.player-account \{ display: block; \}')

    def test_cabecera_con_buscador(self):
        self.assertContains(self.client.get(reverse('catalogo-torneos')), reverse('buscar-usuarios'))
