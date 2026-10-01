from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, RangoVideojuego, Videojuego

from .models import FormatoCompetitivo, InscripcionTorneo, Torneo
from .services import InscripcionError, cancelar_inscripcion, inscribir_usuario


class InscripcionServiceTests(TestCase):
	def setUp(self):
		self.organizador = Usuario.objects.create_user(
			username='organizador',
			email='organizador@example.com',
			password='clave-segura-123',
		)
		self.usuario = Usuario.objects.create_user(
			username='jugador',
			email='jugador@example.com',
			password='clave-segura-123',
		)
		self.videojuego = Videojuego.objects.create(nombre='Arena Battle', genero='Competitivo')
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
		self.perfil = PerfilVideojuegoUsuario.objects.create(
			usuario=self.usuario,
			videojuego=self.videojuego,
			nick_en_juego='NickOriginal',
			rango_declarado=self.rango_plata,
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
			'organizador': self.organizador,
			'tipo': Torneo.Tipo.PUBLICO,
			'formato_competitivo': self.formato,
			'max_participantes': 2,
			'estado': Torneo.Estado.INSCRIPCIONES_ABIERTAS,
			'fecha_apertura_inscripciones': timezone.now() - timedelta(minutes=5),
			'fecha_cierre_inscripciones': timezone.now() + timedelta(minutes=30),
		}
		datos.update(kwargs)
		return Torneo.objects.create(**datos)

	def test_inscripcion_correcta_guarda_las_instantaneas(self):
		torneo = self.crear_torneo()
		inscripcion = inscribir_usuario(torneo, self.usuario)

		self.assertEqual(inscripcion.estado, InscripcionTorneo.Estado.CONFIRMADA)
		self.assertEqual(inscripcion.nick_historico, 'NickOriginal')
		self.assertEqual(inscripcion.rango_declarado_al_inscribirse, self.rango_plata)
		self.assertEqual(torneo.participantes_confirmados, 1)
		self.assertEqual(torneo.plazas_disponibles, 1)

	def test_rechaza_inscripcion_duplicada(self):
		torneo = self.crear_torneo()
		inscribir_usuario(torneo, self.usuario)

		with self.assertRaises(InscripcionError):
			inscribir_usuario(torneo, self.usuario)

	def test_rechaza_torneo_lleno_y_excluye_canceladas(self):
		torneo = self.crear_torneo(max_participantes=2)
		otro_usuario = Usuario.objects.create_user(
			username='jugador2',
			email='jugador2@example.com',
			password='clave-segura-123',
		)
		PerfilVideojuegoUsuario.objects.create(
			usuario=otro_usuario,
			videojuego=self.videojuego,
			nick_en_juego='NickDos',
			rango_declarado=self.rango_bronce,
		)
		inscripcion = inscribir_usuario(torneo, self.usuario)
		segunda = inscribir_usuario(torneo, otro_usuario)

		self.assertEqual(torneo.participantes_confirmados, 2)
		with self.assertRaises(InscripcionError):
			inscribir_usuario(torneo, self.organizador)

		segunda.estado = InscripcionTorneo.Estado.DESCALIFICADA
		segunda.save(update_fields=('estado',))
		self.assertEqual(torneo.participantes_confirmados, 1)
		self.assertEqual(torneo.plazas_disponibles, 1)

		cancelar_inscripcion(inscripcion)
		self.assertEqual(torneo.participantes_confirmados, 0)

	def test_rechaza_inscripcion_cerrada(self):
		torneo = self.crear_torneo(estado=Torneo.Estado.INSCRIPCIONES_CERRADAS)

		with self.assertRaises(InscripcionError):
			inscribir_usuario(torneo, self.usuario)

	def test_valida_nivel_minimo(self):
		torneo = self.crear_torneo(nivel_minimo=2)

		with self.assertRaises(InscripcionError):
			inscribir_usuario(torneo, self.usuario)

		self.usuario.nivel = 2
		self.usuario.save(update_fields=('nivel',))
		inscribir_usuario(torneo, self.usuario)

	def test_valida_rango_minimo_y_maximo(self):
		torneo = self.crear_torneo(rango_minimo=self.rango_bronce, rango_maximo=self.rango_plata)
		inscribir_usuario(torneo, self.usuario)

		self.perfil.rango_declarado = None
		self.perfil.save(update_fields=('rango_declarado',))
		otro_torneo = self.crear_torneo(nombre='Torneo dos', rango_minimo=self.rango_bronce)
		with self.assertRaises(InscripcionError):
			inscribir_usuario(otro_torneo, self.usuario)

	def test_rechaza_perfil_de_otro_usuario_o_videojuego(self):
		torneo = self.crear_torneo()
		otro_usuario = Usuario.objects.create_user(
			username='jugador2',
			email='jugador2@example.com',
			password='clave-segura-123',
		)
		otro_perfil = PerfilVideojuegoUsuario.objects.create(
			usuario=otro_usuario,
			videojuego=self.videojuego,
			nick_en_juego='NickDos',
		)
		inscripcion = InscripcionTorneo(
			torneo=torneo,
			usuario=self.usuario,
			perfil_videojuego=otro_perfil,
			nick_historico='NickDos',
		)
		with self.assertRaises(ValidationError):
			inscripcion.full_clean()

		otro_videojuego = Videojuego.objects.create(nombre='Otro Juego', genero='Acción')
		perfil_otro_juego = PerfilVideojuegoUsuario.objects.create(
			usuario=self.usuario,
			videojuego=otro_videojuego,
			nick_en_juego='NickOtroJuego',
		)
		inscripcion.perfil_videojuego = perfil_otro_juego
		with self.assertRaises(ValidationError):
			inscripcion.full_clean()

	def test_conserva_historial_si_cambia_el_perfil(self):
		torneo = self.crear_torneo()
		inscripcion = inscribir_usuario(torneo, self.usuario)
		self.perfil.nick_en_juego = 'NickNuevo'
		self.perfil.rango_declarado = self.rango_bronce
		self.perfil.save()
		inscripcion.refresh_from_db()

		self.assertEqual(inscripcion.nick_historico, 'NickOriginal')
		self.assertEqual(inscripcion.rango_declarado_al_inscribirse, self.rango_plata)

	def test_cancelacion_registra_fecha_y_no_permite_cancelar_cerrado(self):
		torneo = self.crear_torneo()
		inscripcion = inscribir_usuario(torneo, self.usuario)
		cancelada = cancelar_inscripcion(inscripcion, motivo='Cambio de planes')

		self.assertEqual(cancelada.estado, InscripcionTorneo.Estado.CANCELADA)
		self.assertIsNotNone(cancelada.fecha_cancelacion)
		self.assertEqual(cancelada.motivo_cancelacion, 'Cambio de planes')

		torneo.estado = Torneo.Estado.INSCRIPCIONES_CERRADAS
		torneo.save(update_fields=('estado',))
		with self.assertRaises(InscripcionError):
			cancelar_inscripcion(cancelada)

	def test_torneo_privado_no_aplica_requisitos_competitivos(self):
		torneo = self.crear_torneo(
			nombre='Privado flexible',
			tipo=Torneo.Tipo.PRIVADO,
			nivel_minimo=100,
			rango_minimo=self.rango_plata,
		)
		inscribir_usuario(torneo, self.usuario)