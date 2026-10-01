from datetime import timedelta
from io import BytesIO

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from usuarios.models import Usuario
from videojuegos.models import RangoVideojuego, Videojuego

from .models import FormatoCompetitivo, Torneo
from .forms import TorneoForm


class GestionTorneosViewsTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            username='organizador', email='organizador@example.com', password='ClaveSegura123!',
            nivel=5, karma_total=150,
        )
        self.otro = Usuario.objects.create_user(
            username='otro', email='otro@example.com', password='ClaveSegura123!',
        )
        self.admin = Usuario.objects.create_superuser(
            username='admin', email='admin@example.com', password='ClaveSegura123!',
        )
        self.videojuego = Videojuego.objects.create(nombre='Arena Battle', genero='Competitivo')
        self.otro_juego = Videojuego.objects.create(nombre='Otro Juego', genero='Estrategia')
        self.bronce = RangoVideojuego.objects.create(videojuego=self.videojuego, nombre='Bronce', posicion=1)
        self.rango_otro = RangoVideojuego.objects.create(videojuego=self.otro_juego, nombre='Inicial', posicion=1)
        self.formato = FormatoCompetitivo.objects.create(
            nombre='Eliminacion directa', min_participantes=2, max_participantes=128,
        )

    def datos_privado(self, **extra):
        datos = {
            'nombre': 'Privado web', 'tipo': 'PRIVADO', 'videojuego': self.videojuego.pk,
            'formato_competitivo': self.formato.pk, 'max_participantes': 2,
            'reglas': 'Reglas privadas',
        }
        datos.update(extra)
        return datos

    def datos_publico(self, **extra):
        ahora = timezone.now()
        datos = {
            'nombre': 'Publico web', 'tipo': 'PUBLICO', 'videojuego': self.videojuego.pk,
            'formato_competitivo': self.formato.pk, 'max_participantes': 2,
            'fecha_apertura_inscripciones': (ahora + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
            'fecha_cierre_inscripciones': (ahora + timedelta(hours=2, minutes=30)).strftime('%Y-%m-%dT%H:%M'),
            'duracion_prorroga_min': 15, 'descanso_entre_partidas_min': 5,
        }
        datos.update(extra)
        return datos

    def imagen(self, nombre='banner.png', formato='PNG'):
        contenido = BytesIO()
        Image.new('RGB', (320, 120), color='navy').save(contenido, format=formato)
        return SimpleUploadedFile(nombre, contenido.getvalue(), content_type='image/png')

    def test_crear_privado_y_publico_guarda_borradores(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(reverse('crear-torneo'), self.datos_privado(), follow=True)
        self.assertEqual(respuesta.status_code, 200)
        privado = Torneo.objects.get(nombre='Privado web')
        self.assertEqual(privado.estado, Torneo.Estado.BORRADOR)
        respuesta = self.client.post(reverse('crear-torneo'), self.datos_publico(imagen_banner=self.imagen()), follow=True)
        self.assertEqual(respuesta.status_code, 200)
        publico = Torneo.objects.get(nombre='Publico web')
        self.assertEqual(publico.estado, Torneo.Estado.BORRADOR)
        self.assertIsNone(publico.fecha_publicacion)
        self.assertTrue(publico.imagen_banner.name)

    def test_rechaza_publico_por_nivel_karma_y_oficial_normal(self):
        self.usuario.nivel = 4
        self.usuario.save(update_fields=('nivel',))
        self.client.force_login(self.usuario)
        respuesta = self.client.post(reverse('crear-torneo'), self.datos_publico())
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(Torneo.objects.filter(nombre='Publico web').exists())
        self.usuario.nivel = 5
        self.usuario.karma_total = 100
        self.usuario.save(update_fields=('nivel', 'karma_total'))
        self.client.post(reverse('crear-torneo'), self.datos_publico(nombre='Publico karma'))
        self.assertFalse(Torneo.objects.filter(nombre='Publico karma').exists())
        datos = self.datos_privado(nombre='Oficial indebido', tipo='OFICIAL')
        self.client.post(reverse('crear-torneo'), datos)
        self.assertFalse(Torneo.objects.filter(nombre='Oficial indebido').exists())

    def test_ignora_campos_internos_y_rechaza_rango_de_otro_juego(self):
        self.client.force_login(self.usuario)
        datos = self.datos_privado(
            rango_minimo=self.rango_otro.pk,
            organizador=self.otro.pk,
            estado='PUBLICO', fecha_publicacion=timezone.now().isoformat(),
            fecha_inicio_real=timezone.now().isoformat(), nivel_minimo=99,
        )
        respuesta = self.client.post(reverse('crear-torneo'), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(Torneo.objects.filter(nombre='Privado web').exists())
        datos['rango_minimo'] = ''
        respuesta = self.client.post(reverse('crear-torneo'), datos)
        torneo = Torneo.objects.get(nombre='Privado web')
        self.assertEqual(torneo.organizador_id, self.usuario.pk)
        self.assertEqual(torneo.estado, Torneo.Estado.BORRADOR)
        self.assertIsNone(torneo.fecha_publicacion)

    def test_edita_solo_borrador_propio(self):
        self.client.force_login(self.usuario)
        self.client.post(reverse('crear-torneo'), self.datos_privado())
        torneo = Torneo.objects.get(nombre='Privado web')
        respuesta = self.client.post(reverse('editar-borrador-torneo', args=[torneo.pk]), self.datos_privado(nombre='Privado editado'))
        self.assertEqual(respuesta.status_code, 302)
        torneo.refresh_from_db()
        self.assertEqual(torneo.nombre, 'Privado editado')
        torneo.estado = Torneo.Estado.PROXIMAMENTE
        torneo.fecha_publicacion = timezone.now()
        torneo.save(update_fields=('estado', 'fecha_publicacion'))
        self.assertEqual(self.client.get(reverse('editar-borrador-torneo', args=[torneo.pk])).status_code, 404)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(reverse('editar-borrador-torneo', args=[torneo.pk])).status_code, 404)

    def test_publicar_publico_por_post_inmediato_programado_y_get_rechazado(self):
        self.client.force_login(self.usuario)
        datos = self.datos_publico()
        ahora = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
        datos['fecha_apertura_inscripciones'] = (ahora + timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M')
        datos['fecha_cierre_inscripciones'] = (ahora + timedelta(minutes=40)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('crear-torneo'), datos)
        torneo = Torneo.objects.get(nombre='Publico web')
        self.assertEqual(self.client.get(reverse('publicar-torneo', args=[torneo.pk])).status_code, 405)
        respuesta = self.client.post(reverse('publicar-torneo', args=[torneo.pk]))
        self.assertEqual(respuesta.status_code, 302)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.PROXIMAMENTE)
        self.assertIsNotNone(torneo.fecha_publicacion)

    def test_publico_con_apertura_mas_lejana_conserva_borrador(self):
        self.client.force_login(self.usuario)
        ahora = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
        datos = self.datos_publico(
            fecha_apertura_inscripciones=(ahora + timedelta(hours=25)).strftime('%Y-%m-%dT%H:%M'),
            fecha_cierre_inscripciones=(ahora + timedelta(hours=25, minutes=30)).strftime('%Y-%m-%dT%H:%M'),
        )
        self.client.post(reverse('crear-torneo'), datos)
        torneo = Torneo.objects.get(nombre='Publico web')
        self.client.post(reverse('publicar-torneo', args=[torneo.pk]))
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.BORRADOR)
        self.assertIsNone(torneo.fecha_publicacion)

    def test_mis_torneos_solo_muestra_los_propios(self):
        Torneo.objects.create(
            nombre='Ajeno', videojuego=self.videojuego, organizador=self.otro,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=self.formato, max_participantes=2,
        )
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('mis-torneos'))
        self.assertNotContains(respuesta, 'Ajeno')

    def test_formulario_muestra_rangos_y_tamanos_del_formato(self):
        formulario = TorneoForm(
            data={'videojuego': str(self.videojuego.pk), 'formato_competitivo': str(self.formato.pk)},
            usuario=self.usuario,
        )
        self.assertEqual(list(formulario.fields['rango_minimo'].queryset), [self.bronce])
        self.assertEqual(list(formulario.fields['max_participantes'].choices), [(2, 2), (4, 4), (8, 8), (16, 16), (32, 32), (64, 64), (128, 128)])

    def test_edicion_conserva_rangos_y_endpoint_solo_lee(self):
        torneo = Torneo.objects.create(
            nombre='Borrador rangos', videojuego=self.videojuego, organizador=self.usuario,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=self.formato,
            max_participantes=2, rango_minimo=self.bronce, rango_maximo=self.bronce,
        )
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('editar-borrador-torneo', args=[torneo.pk]))
        self.assertContains(respuesta, f'value="{self.bronce.pk}" selected')
        respuesta = self.client.get(reverse('rangos-por-videojuego', args=[self.videojuego.pk]))
        self.assertEqual(respuesta.json()['rangos'][0]['id'], self.bronce.pk)
        self.assertEqual(self.client.post(reverse('rangos-por-videojuego', args=[self.videojuego.pk])).status_code, 405)

    def test_error_de_otro_campo_conserva_videojuego_y_rangos(self):
        self.client.force_login(self.usuario)
        datos = self.datos_privado(nombre='', rango_minimo=self.bronce.pk, rango_maximo=self.bronce.pk)
        respuesta = self.client.post(reverse('crear-torneo'), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Arena Battle')
        self.assertContains(respuesta, f'value="{self.bronce.pk}"')

    def test_rechaza_tamanos_manipulados_y_fechas_incoherentes_en_servidor(self):
        formulario = TorneoForm(
            data=self.datos_publico(max_participantes='3'),
            usuario=self.usuario,
        )
        self.assertFalse(formulario.is_valid())
        self.assertIn('max_participantes', formulario.errors)
        ahora = timezone.now()
        formulario = TorneoForm(
            data=self.datos_publico(
                fecha_apertura_inscripciones=ahora.strftime('%Y-%m-%dT%H:%M'),
                fecha_cierre_inscripciones=(ahora + timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M'),
                fecha_inicio_prevista=(ahora + timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M'),
            ),
            usuario=self.usuario,
        )
        self.assertFalse(formulario.is_valid())
        self.assertIn('fecha_inicio_prevista', formulario.errors)
