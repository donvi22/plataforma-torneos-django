from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from django.test import override_settings

from usuarios.models import Usuario
from videojuegos.models import RangoVideojuego, Videojuego

from .models import FormatoCompetitivo, PremioTorneo, Torneo


class TorneoModelTests(TestCase):
	@override_settings(TIME_ZONE='Europe/Madrid')
	def test_fecha_local_de_apertura_se_interpreta_con_zona_configurada(self):
		from .forms import TorneoForm
		formato = FormatoCompetitivo.objects.create(nombre='Formato local', min_participantes=2, max_participantes=128)
		videojuego = Videojuego.objects.create(nombre='Juego local', genero='Competitivo')
		usuario = Usuario.objects.create_user(
			username='local', email='local@example.com', password='clave', nivel=5, karma_total=150,
		)
		form = TorneoForm(data={
			'nombre': 'Local', 'tipo': Torneo.Tipo.PUBLICO, 'videojuego': videojuego.pk,
			'formato_competitivo': formato.pk, 'max_participantes': 2,
			'fecha_apertura_inscripciones': '2026-09-20T13:48',
			'fecha_cierre_inscripciones': '2026-09-20T14:03',
		}, usuario=usuario)
		self.assertTrue(form.is_valid(), form.errors)
		self.assertEqual(form.cleaned_data['fecha_apertura_inscripciones'].tzinfo.key, 'Europe/Madrid')
	def setUp(self):
		self.usuario = Usuario.objects.create_user(
			username='organizador',
			email='organizador@example.com',
			password='clave-segura-123',
		)
		self.videojuego = Videojuego.objects.create(
			nombre='Arena Battle',
			genero='Competitivo',
		)
		self.rango_bronce = RangoVideojuego.objects.create(
			videojuego=self.videojuego,
			nombre='Bronce',
			posicion=1,
		)
		self.rango_plata = RangoVideojuego.objects.create(
			videojuego=self.videojuego,
			nombre='Plata',
			posicion=2,
		)
		self.formato = FormatoCompetitivo.objects.create(
			nombre='Eliminación directa',
			min_participantes=2,
			max_participantes=128,
		)

	def crear_torneo(self, **kwargs):
		datos = {
			'nombre': 'Torneo de prueba',
			'videojuego': self.videojuego,
			'organizador': self.usuario,
			'tipo': Torneo.Tipo.PRIVADO,
			'formato_competitivo': self.formato,
			'tipo_participante': Torneo.TipoParticipante.INDIVIDUAL,
			'tamano_equipo': 1,
			'max_participantes': 8,
		}
		datos.update(kwargs)
		return Torneo(**datos)

	def test_formato_acepta_solo_potencias_de_dos_en_su_rango(self):
		for numero in (2, 4, 8, 16, 32, 64, 128):
			self.assertTrue(self.formato.es_tamano_valido(numero))
		for numero in (1, 3, 5, 129):
			self.assertFalse(self.formato.es_tamano_valido(numero))

	def test_torneo_valido_relaciona_videojuego_organizador_y_formato(self):
		torneo = self.crear_torneo()
		torneo.full_clean()
		torneo.save()

		self.assertEqual(torneo.videojuego, self.videojuego)
		self.assertEqual(torneo.organizador, self.usuario)
		self.assertEqual(torneo.formato_competitivo, self.formato)
		self.assertEqual(torneo.duracion_checkin_min, 10)

	def test_rechaza_tamano_no_valido_para_el_formato(self):
		torneo = self.crear_torneo(max_participantes=7)

		with self.assertRaises(ValidationError):
			torneo.full_clean()

	def test_mvp_solo_admite_participantes_individuales(self):
		torneo = self.crear_torneo(
			tipo_participante=Torneo.TipoParticipante.EQUIPO,
			tamano_equipo=2,
		)

		with self.assertRaises(ValidationError):
			torneo.full_clean()

	def test_rechaza_videojuego_inactivo_al_crear(self):
		self.videojuego.activo = False
		self.videojuego.save(update_fields=('activo',))
		torneo = self.crear_torneo()

		with self.assertRaises(ValidationError):
			torneo.full_clean()

	def test_rechaza_rangos_de_otro_videojuego_y_orden_incorrecto(self):
		otro_videojuego = Videojuego.objects.create(nombre='Otro Juego', genero='Acción')
		rango_otro_juego = RangoVideojuego.objects.create(
			videojuego=otro_videojuego,
			nombre='Inicial',
			posicion=1,
		)
		torneo = self.crear_torneo(rango_minimo=rango_otro_juego)

		with self.assertRaises(ValidationError):
			torneo.full_clean()

		torneo = self.crear_torneo(
			rango_minimo=self.rango_plata,
			rango_maximo=self.rango_bronce,
		)
		with self.assertRaises(ValidationError):
			torneo.full_clean()

	def test_valida_la_duracion_y_las_reglas_temporales_publicas(self):
		inicio = timezone.now()
		torneo = self.crear_torneo(
			tipo=Torneo.Tipo.PUBLICO,
			estado=Torneo.Estado.PROXIMAMENTE,
			fecha_apertura_inscripciones=inicio,
			fecha_cierre_inscripciones=inicio + timedelta(minutes=30),
			duracion_prorroga_min=20,
			descanso_entre_partidas_min=5,
		)
		torneo.full_clean()

		torneo.fecha_cierre_inscripciones = inicio + timedelta(minutes=10)
		with self.assertRaises(ValidationError):
			torneo.full_clean()

		torneo.fecha_cierre_inscripciones = inicio + timedelta(minutes=30)
		torneo.duracion_prorroga_min = 31
		with self.assertRaises(ValidationError):
			torneo.full_clean()

		torneo.duracion_prorroga_min = 20
		torneo.descanso_entre_partidas_min = 11
		with self.assertRaises(ValidationError):
			torneo.full_clean()

	def test_publico_no_puede_usar_xp_especial(self):
		torneo = self.crear_torneo(
			tipo=Torneo.Tipo.PUBLICO,
			modo_xp=Torneo.ModoXP.ESPECIAL,
			fecha_apertura_inscripciones=timezone.now(),
			fecha_cierre_inscripciones=timezone.now() + timedelta(minutes=15),
		)

		with self.assertRaises(ValidationError):
			torneo.full_clean()

	def test_xp_especial_solo_es_valido_en_oficiales(self):
		torneo = self.crear_torneo(
			tipo=Torneo.Tipo.OFICIAL,
			modo_xp=Torneo.ModoXP.ESPECIAL,
			multiplicador_xp_especial='1.50',
		)
		torneo.full_clean()

		torneo.tipo = Torneo.Tipo.PRIVADO
		with self.assertRaises(ValidationError):
			torneo.full_clean()

	def test_publico_no_puede_programarse_con_mas_de_24_horas(self):
		inicio = timezone.now() + timedelta(hours=25)
		torneo = self.crear_torneo(
			tipo=Torneo.Tipo.PUBLICO,
			estado=Torneo.Estado.PROXIMAMENTE,
			fecha_apertura_inscripciones=inicio,
			fecha_cierre_inscripciones=inicio + timedelta(minutes=30),
		)

		with self.assertRaises(ValidationError):
			torneo.full_clean()

	def test_premio_solo_se_admite_en_torneo_oficial(self):
		torneo = self.crear_torneo()
		torneo.save()
		premio = PremioTorneo(torneo=torneo, descripcion='Premio')

		with self.assertRaises(ValidationError):
			premio.full_clean()
