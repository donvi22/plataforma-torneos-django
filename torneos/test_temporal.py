from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from usuarios.models import Usuario
from videojuegos.models import Videojuego

from .models import FormatoCompetitivo, Torneo
from .services import EstadoInscripciones, estado_inscripciones, procesar_calendario


class TemporalInscripcionesTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(username='org', email='org@example.com', password='clave')
        self.videojuego = Videojuego.objects.create(nombre='Juego temporal', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(nombre='Formato temporal', min_participantes=2, max_participantes=128)

    def torneo(self, apertura, cierre, estado=Torneo.Estado.PROXIMAMENTE, prorroga=15):
        return Torneo.objects.create(
            nombre='Temporal', videojuego=self.videojuego, organizador=self.usuario,
            tipo=Torneo.Tipo.PUBLICO, formato_competitivo=self.formato, max_participantes=2,
            estado=estado, fecha_publicacion=apertura - timedelta(hours=1),
            fecha_apertura_inscripciones=apertura, fecha_cierre_inscripciones=cierre,
            duracion_prorroga_min=prorroga,
        )

    def test_antes_durante_y_despues_de_la_ventana(self):
        base = timezone.now()
        torneo = self.torneo(base + timedelta(minutes=5), base + timedelta(minutes=20))
        self.assertEqual(estado_inscripciones(torneo, base), EstadoInscripciones.PROXIMAMENTE)
        self.assertEqual(estado_inscripciones(torneo, base + timedelta(minutes=10)), EstadoInscripciones.ABIERTAS)
        self.assertEqual(estado_inscripciones(torneo, base + timedelta(minutes=21)), EstadoInscripciones.PRORROGA)
        self.assertEqual(estado_inscripciones(torneo, base + timedelta(minutes=36)), EstadoInscripciones.CERRADAS)

    def test_procesador_abre_y_no_reabre_una_ventana_totalmente_vencida(self):
        base = timezone.now()
        torneo = self.torneo(base - timedelta(minutes=40), base - timedelta(minutes=20))
        self.assertEqual(procesar_calendario(ahora=base), 1)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.CANCELADO)
        self.assertEqual(procesar_calendario(ahora=base + timedelta(minutes=1)), 0)
        self.assertEqual(torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.CANCELADO).count(), 1)

    def test_get_de_ficha_no_modifica_estado(self):
        base = timezone.now()
        torneo = self.torneo(base - timedelta(minutes=5), base + timedelta(minutes=10))
        from django.test import Client
        respuesta = Client().get(f'/torneos/{torneo.pk}/')
        self.assertEqual(respuesta.status_code, 200)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.PROXIMAMENTE)

    def test_ficha_muestra_abiertas_si_el_estado_persistido_aun_es_proximamente(self):
        base = timezone.now()
        torneo = self.torneo(base - timedelta(minutes=5), base + timedelta(minutes=10))
        respuesta = self.client.get(f'/torneos/{torneo.pk}/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Inscripciones abiertas')
        self.assertNotContains(respuesta, 'Las inscripciones todavía no han abierto')
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.PROXIMAMENTE)

    def test_oficial_proximamente_se_abre_por_fecha_sin_restricciones_publicas(self):
        base = timezone.now()
        torneo = Torneo.objects.create(
            nombre='Oficial temporal', videojuego=self.videojuego, organizador=self.usuario,
            tipo=Torneo.Tipo.OFICIAL, formato_competitivo=self.formato, max_participantes=2,
            estado=Torneo.Estado.PROXIMAMENTE,
            fecha_publicacion=base - timedelta(hours=2),
            fecha_apertura_inscripciones=base - timedelta(minutes=5),
            fecha_cierre_inscripciones=base + timedelta(hours=2),
        )
        self.assertEqual(estado_inscripciones(torneo, base), EstadoInscripciones.ABIERTAS)
        self.assertEqual(procesar_calendario(ahora=base), 1)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.INSCRIPCIONES_ABIERTAS)

    def test_comando_procesa_una_vez_y_es_idempotente(self):
        base = timezone.now()
        torneo = self.torneo(base - timedelta(minutes=1), base + timedelta(minutes=10))
        from io import StringIO
        salida = StringIO()
        call_command('procesar_calendario', stdout=salida)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.INSCRIPCIONES_ABIERTAS)
        self.assertIn('Torneos actualizados: 1', salida.getvalue())
        salida = StringIO()
        call_command('procesar_calendario', stdout=salida)
        self.assertIn('Torneos actualizados: 0', salida.getvalue())