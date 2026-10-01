from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core import management
from django.core.management import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import FormatoCompetitivo, InscripcionTorneo, Torneo
from .services import InscripcionError, inscribir_usuario


class OrganizerRestrictionTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(
            username='organizador', email='organizador@example.com', password='ClaveSegura123!',
            nivel=5, karma_total=150,
        )
        self.otro = Usuario.objects.create_user(
            username='otro', email='otro@example.com', password='ClaveSegura123!',
            nivel=5, karma_total=150,
        )
        self.game = Videojuego.objects.create(nombre='Juego organizador', genero='Competitivo')
        self.format = FormatoCompetitivo.objects.create(nombre='Formato organizador', min_participantes=2, max_participantes=128)
        self.perfil_org = PerfilVideojuegoUsuario.objects.create(usuario=self.organizador, videojuego=self.game, nick_en_juego='Org')
        self.perfil_otro = PerfilVideojuegoUsuario.objects.create(usuario=self.otro, videojuego=self.game, nick_en_juego='Otro')

    def crear(self, tipo):
        ahora = timezone.now()
        return Torneo.objects.create(
            nombre=f'Torneo {tipo}', videojuego=self.game, organizador=self.organizador,
            tipo=tipo, formato_competitivo=self.format, max_participantes=2,
            estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
            fecha_publicacion=timezone.now(),
            fecha_apertura_inscripciones=ahora, fecha_cierre_inscripciones=ahora,
        )

    def test_organizador_no_puede_inscribirse_en_cualquier_tipo(self):
        for tipo in Torneo.Tipo.values:
            with self.subTest(tipo=tipo):
                torneo = self.crear(tipo)
                with self.assertRaisesMessage(InscripcionError, 'No puedes inscribirte como participante'):
                    inscribir_usuario(torneo, self.organizador)

    def test_organizador_puede_participar_en_torneo_ajeno(self):
        torneo = Torneo.objects.create(
            nombre='Torneo ajeno', videojuego=self.game, organizador=self.otro,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=self.format,
            max_participantes=2, estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
        )
        inscripcion = inscribir_usuario(torneo, self.organizador)
        self.assertEqual(inscripcion.estado, InscripcionTorneo.Estado.CONFIRMADA)

    def test_ficha_no_muestra_inscripcion_al_organizador(self):
        torneo = self.crear(Torneo.Tipo.PUBLICO)
        self.client.force_login(self.organizador)
        respuesta = self.client.get(reverse('ficha-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'No puedes inscribirte como participante')
        self.assertNotContains(respuesta, '>Inscribirme<')


class CrearJugadoresPruebaCommandTests(TestCase):
    def setUp(self):
        self.org = Usuario.objects.create_user(username='orgcmd', email='orgcmd@example.com', password='ClaveSegura123!', nivel=5, karma_total=150)
        self.game = Videojuego.objects.create(nombre='Juego comando', genero='Competitivo')
        self.format = FormatoCompetitivo.objects.create(nombre='Formato comando', min_participantes=2, max_participantes=128)
        PerfilVideojuegoUsuario.objects.create(usuario=self.org, videojuego=self.game, nick_en_juego='orgcmd')
        now = timezone.now()
        self.torneo = Torneo.objects.create(
            nombre='Torneo comando', videojuego=self.game, organizador=self.org,
            tipo=Torneo.Tipo.PUBLICO, formato_competitivo=self.format, max_participantes=2,
            estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
            fecha_apertura_inscripciones=now - timedelta(minutes=1),
            fecha_cierre_inscripciones=now + timedelta(minutes=30),
        )

    @override_settings(DEBUG=True, DEV_LOCAL=False)
    def test_comando_rechaza_entorno_no_autorizado(self):
        with self.assertRaises(CommandError):
            management.call_command('crear_jugadores_prueba', cantidad=1)

    @override_settings(DEBUG=True, DEV_LOCAL=True)
    def test_comando_crea_jugador_normal_y_inscribe_por_servicio(self):
        salida = StringIO()
        management.call_command('crear_jugadores_prueba', cantidad=3, torneo=self.torneo.pk, stdout=salida)
        jugadores = Usuario.objects.filter(username__startswith='bot_prueba_')
        self.assertEqual(jugadores.count(), 2)
        self.assertTrue(all(not jugador.is_staff and not jugador.is_superuser for jugador in jugadores))
        self.assertEqual(InscripcionTorneo.objects.filter(torneo=self.torneo, estado=InscripcionTorneo.Estado.CONFIRMADA).count(), 2)
        self.assertIn('Usuarios creados: 2', salida.getvalue())

    @override_settings(DEBUG=True, DEV_LOCAL=True)
    def test_comando_no_crea_si_el_torneo_esta_completo(self):
        for suffix in ('a', 'b'):
            jugador = Usuario.objects.create_user(username=f'existente{suffix}', email=f'existente{suffix}@example.com', password='ClaveSegura123!')
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=jugador, videojuego=self.game, nick_en_juego=f'existente{suffix}')
            InscripcionTorneo.objects.create(torneo=self.torneo, usuario=jugador, perfil_videojuego=perfil, nick_historico=f'existente{suffix}')
        management.call_command('crear_jugadores_prueba', cantidad=3, torneo=self.torneo.pk)
        self.assertFalse(Usuario.objects.filter(username__startswith='bot_prueba_').exists())
