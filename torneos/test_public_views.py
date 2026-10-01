from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, RangoVideojuego, Videojuego

from .models import FormatoCompetitivo, InscripcionTorneo, PremioTorneo, Torneo


class TorneosPublicosViewsTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(
            username='organizador', email='organizador@example.com', password='ClaveSegura123!',
        )
        self.visitante = Usuario.objects.create_user(
            username='visitante', email='visitante@example.com', password='ClaveSegura123!',
        )
        self.videojuego = Videojuego.objects.create(nombre='Arena Battle', genero='Competitivo')
        self.inactivo = Videojuego.objects.create(nombre='Juego Inactivo', genero='Accion', activo=False)
        self.formato = FormatoCompetitivo.objects.create(
            nombre='Eliminacion directa', min_participantes=2, max_participantes=128,
        )
        self.rango = RangoVideojuego.objects.create(
            videojuego=self.videojuego, nombre='Bronce', posicion=1,
        )

    def crear_torneo(self, nombre='Copa visible', tipo=Torneo.Tipo.PUBLICO, estado=Torneo.Estado.PROXIMAMENTE, videojuego=None, publicado=True):
        ahora = timezone.now()
        return Torneo.objects.create(
            nombre=nombre,
            videojuego=videojuego or self.videojuego,
            organizador=self.organizador,
            tipo=tipo,
            formato_competitivo=self.formato,
            max_participantes=2,
            estado=estado,
            fecha_publicacion=ahora if publicado else None,
            fecha_apertura_inscripciones=ahora + timedelta(hours=2),
            fecha_cierre_inscripciones=ahora + timedelta(hours=3),
            fecha_inicio_prevista=ahora + timedelta(days=1),
        )

    def test_catalogo_muestra_publicados_publicos_y_oficiales(self):
        self.crear_torneo()
        self.crear_torneo(nombre='Copa oficial', tipo=Torneo.Tipo.OFICIAL)
        self.crear_torneo(nombre='Privado', tipo=Torneo.Tipo.PRIVADO)
        self.crear_torneo(nombre='Borrador', estado=Torneo.Estado.BORRADOR)
        self.crear_torneo(nombre='Juego apagado', videojuego=self.inactivo)
        respuesta = self.client.get(reverse('catalogo-torneos'))
        self.assertContains(respuesta, 'Copa visible')
        self.assertContains(respuesta, 'Copa oficial')
        self.assertNotContains(respuesta, 'Privado')
        self.assertNotContains(respuesta, 'Borrador')
        self.assertNotContains(respuesta, 'Juego apagado')

    def test_filtros_busqueda_tipo_estado_y_orden_seguro(self):
        self.crear_torneo(nombre='Alpha')
        self.crear_torneo(nombre='Beta', tipo=Torneo.Tipo.OFICIAL, estado=Torneo.Estado.EN_CURSO)
        respuesta = self.client.get(reverse('catalogo-torneos'), {'q': 'Alpha'})
        self.assertContains(respuesta, 'Alpha')
        self.assertNotContains(respuesta, 'Beta')
        respuesta = self.client.get(reverse('catalogo-torneos'), {'tipo': 'OFICIAL', 'estado': 'EN_CURSO'})
        self.assertContains(respuesta, 'Beta')
        self.assertNotContains(respuesta, 'Alpha')
        respuesta = self.client.get(reverse('catalogo-torneos'), {'orden': 'campo_arbitrario'})
        self.assertEqual(respuesta.status_code, 200)

    def test_paginacion(self):
        for indice in range(11):
            self.crear_torneo(nombre=f'Copa {indice}')
        respuesta = self.client.get(reverse('catalogo-torneos'))
        self.assertContains(respuesta, 'Página 1 de 2')
        respuesta = self.client.get(reverse('catalogo-torneos'), {'page': 2})
        self.assertContains(respuesta, 'Página 2 de 2')

    def test_ficha_privada_y_borrador_protegidos_y_organizador_accede(self):
        privado = self.crear_torneo(nombre='Privado', tipo=Torneo.Tipo.PRIVADO, publicado=False)
        borrador = self.crear_torneo(nombre='Borrador', estado=Torneo.Estado.BORRADOR, publicado=False)
        self.assertEqual(self.client.get(reverse('ficha-torneo', args=[privado.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('ficha-torneo', args=[borrador.pk])).status_code, 404)
        self.client.force_login(self.organizador)
        self.assertContains(self.client.get(reverse('ficha-torneo', args=[privado.pk])), 'Privado')
        self.assertContains(self.client.get(reverse('ficha-torneo', args=[borrador.pk])), 'Borrador')

    def test_participante_confirmado_puede_ver_privado(self):
        privado = self.crear_torneo(nombre='Privado', tipo=Torneo.Tipo.PRIVADO, publicado=False)
        perfil = PerfilVideojuegoUsuario.objects.create(
            usuario=self.visitante, videojuego=self.videojuego, nick_en_juego='Jugador', rango_declarado=self.rango,
        )
        InscripcionTorneo.objects.create(
            torneo=privado, usuario=self.visitante, perfil_videojuego=perfil,
            nick_historico='Nick histórico', rango_declarado_al_inscribirse=self.rango,
            estado=InscripcionTorneo.Estado.CONFIRMADA,
        )
        self.client.force_login(self.visitante)
        self.assertContains(self.client.get(reverse('ficha-torneo', args=[privado.pk])), 'Privado')

    def test_ficha_muestra_requisitos_premio_y_nick_historico_sin_datos_privados(self):
        torneo = self.crear_torneo(
            estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
            tipo=Torneo.Tipo.OFICIAL,
        )
        torneo.nivel_minimo = 3
        torneo.rango_minimo = self.rango
        torneo.reglas = 'Reglas públicas'
        torneo.codigo_acceso = 'SECRETO'
        torneo.save(update_fields=('nivel_minimo', 'rango_minimo', 'reglas', 'codigo_acceso'))
        PremioTorneo.objects.create(torneo=torneo, descripcion='Trofeo oficial')
        perfil = PerfilVideojuegoUsuario.objects.create(
            usuario=self.visitante, videojuego=self.videojuego, nick_en_juego='Jugador', rango_declarado=self.rango,
        )
        InscripcionTorneo.objects.create(
            torneo=torneo, usuario=self.visitante, perfil_videojuego=perfil,
            nick_historico='Nick histórico', rango_declarado_al_inscribirse=self.rango,
            estado=InscripcionTorneo.Estado.CONFIRMADA,
        )
        respuesta = self.client.get(reverse('ficha-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'Reglas públicas')
        self.assertContains(respuesta, 'Nick histórico')
        self.assertNotContains(respuesta, 'SECRETO')
        self.assertNotContains(respuesta, self.visitante.email)

    def test_conteo_excluye_canceladas_y_descalificadas(self):
        torneo = self.crear_torneo(estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS)
        for indice, estado in enumerate((InscripcionTorneo.Estado.CONFIRMADA, InscripcionTorneo.Estado.CANCELADA, InscripcionTorneo.Estado.DESCALIFICADA)):
            usuario = Usuario.objects.create_user(
                username=f'jugador{indice}', email=f'jugador{indice}@example.com', password='ClaveSegura123!',
            )
            perfil = PerfilVideojuegoUsuario.objects.create(
                usuario=usuario, videojuego=self.videojuego, nick_en_juego=f'Nick{indice}',
            )
            InscripcionTorneo.objects.create(
                torneo=torneo, usuario=usuario, perfil_videojuego=perfil,
                nick_historico=f'Nick histórico {indice}', estado=estado,
            )
        respuesta = self.client.get(reverse('ficha-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, '1/2 participantes confirmados')
        self.assertContains(respuesta, 'Nick histórico 0')
        self.assertNotContains(respuesta, 'Nick histórico 1')

    def test_plazo_vencido_se_presenta_sin_cambiar_estado(self):
        torneo = self.crear_torneo(estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS)
        torneo.fecha_apertura_inscripciones = timezone.now() - timedelta(hours=2)
        torneo.fecha_cierre_inscripciones = timezone.now() - timedelta(minutes=1)
        torneo.duracion_prorroga_min = None
        torneo.save(update_fields=('fecha_apertura_inscripciones', 'fecha_cierre_inscripciones', 'duracion_prorroga_min'))
        respuesta = self.client.get(reverse('ficha-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, 'Plazo de inscripción vencido')
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.INSCRIPCIONES_ABIERTAS)
