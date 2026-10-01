from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, RangoVideojuego, Videojuego
from partidas.models import Partida

from .models import FormatoCompetitivo, HistorialEstadoTorneo, InscripcionTorneo, Torneo
from .services import (
	InscripcionError,
	cancelar_inscripcion,
	crear_torneo,
	inscribir_usuario,
	procesar_calendario,
	publicar_torneo,
)


class TorneoLifecycleServiceTests(TestCase):
	def setUp(self):
		self.organizador = Usuario.objects.create_user(
			username='organizador',
			email='organizador@example.com',
			password='clave-segura-123',
			nivel=5,
			karma_total=150,
		)
		self.usuario = Usuario.objects.create_user(
			username='jugador',
			email='jugador@example.com',
			password='clave-segura-123',
		)
		self.videojuego = Videojuego.objects.create(nombre='Arena Battle', genero='Competitivo')
		self.rango = RangoVideojuego.objects.create(
			videojuego=self.videojuego,
			nombre='Bronce',
			posicion=1,
		)
		self.formato = FormatoCompetitivo.objects.create(
			nombre='Eliminación directa',
			min_participantes=2,
			max_participantes=128,
		)
		PerfilVideojuegoUsuario.objects.create(
			usuario=self.usuario,
			videojuego=self.videojuego,
			nick_en_juego='Jugador',
			rango_declarado=self.rango,
		)

	def datos_base(self, tipo):
		return {
			'nombre': f'Torneo {tipo}',
			'tipo': tipo,
			'videojuego': self.videojuego,
			'formato_competitivo': self.formato,
			'max_participantes': 2,
		}

	def crear_publico(self, **extra):
		datos = self.datos_base(Torneo.Tipo.PUBLICO)
		datos.update(extra)
		return crear_torneo(self.organizador, **datos)

	def publicar_con_fechas(self, torneo, apertura, cierre, ahora=None):
		torneo.fecha_apertura_inscripciones = apertura
		torneo.fecha_cierre_inscripciones = cierre
		torneo.save(update_fields=('fecha_apertura_inscripciones', 'fecha_cierre_inscripciones'))
		return publicar_torneo(torneo, self.organizador, ahora=ahora)

	def test_crea_privado_y_limita_un_activo(self):
		privado = crear_torneo(self.organizador, **self.datos_base(Torneo.Tipo.PRIVADO))
		self.assertEqual(privado.estado, Torneo.Estado.BORRADOR)

		with self.assertRaises(InscripcionError):
			crear_torneo(self.organizador, **self.datos_base(Torneo.Tipo.PRIVADO))

		privado.estado = Torneo.Estado.CANCELADO
		privado.save(update_fields=('estado',))
		crear_torneo(self.organizador, **self.datos_base(Torneo.Tipo.PRIVADO))

	def test_requisitos_y_limite_de_publicos(self):
		self.organizador.nivel = 4
		self.organizador.save(update_fields=('nivel',))
		with self.assertRaises(InscripcionError):
			crear_torneo(self.organizador, **self.datos_base(Torneo.Tipo.PUBLICO))

		self.organizador.nivel = 5
		self.organizador.karma_total = 149
		self.organizador.save(update_fields=('nivel', 'karma_total'))
		with self.assertRaises(InscripcionError):
			crear_torneo(self.organizador, **self.datos_base(Torneo.Tipo.PUBLICO))

		self.organizador.karma_total = 150
		self.organizador.save(update_fields=('karma_total',))
		crear_torneo(self.organizador, **self.datos_base(Torneo.Tipo.PUBLICO))
		crear_torneo(self.organizador, **dict(self.datos_base(Torneo.Tipo.PUBLICO), nombre='Torneo PUBLICO 2'))
		with self.assertRaises(InscripcionError):
			crear_torneo(self.organizador, **dict(self.datos_base(Torneo.Tipo.PUBLICO), nombre='Torneo PUBLICO 3'))

	def test_solo_administrador_autorizado_crea_oficial(self):
		with self.assertRaises(InscripcionError):
			crear_torneo(self.organizador, **self.datos_base(Torneo.Tipo.OFICIAL))

		admin = Usuario.objects.create_superuser(
			username='admin',
			email='admin@example.com',
			password='clave-segura-123',
		)
		ofical = crear_torneo(admin, **self.datos_base(Torneo.Tipo.OFICIAL))
		self.assertEqual(ofical.organizador, admin)

	def test_publicacion_inmediata_y_programada(self):
		ahora = timezone.now()
		inmediato = self.crear_publico(nombre='Inmediato')
		self.publicar_con_fechas(
			inmediato,
			ahora - timedelta(minutes=1),
			ahora + timedelta(minutes=29),
			ahora,
		)
		self.assertEqual(inmediato.estado, Torneo.Estado.INSCRIPCIONES_ABIERTAS)
		self.assertEqual(inmediato.fecha_publicacion, ahora)

		programado = self.crear_publico(nombre='Programado')
		apertura = ahora + timedelta(minutes=5)
		self.publicar_con_fechas(programado, apertura, apertura + timedelta(minutes=30), ahora)
		self.assertEqual(programado.estado, Torneo.Estado.PROXIMAMENTE)
		self.assertEqual(procesar_calendario(apertura), 1)
		programado.refresh_from_db()
		self.assertEqual(programado.estado, Torneo.Estado.INSCRIPCIONES_ABIERTAS)

	def test_rechaza_apertura_con_mas_de_24_horas(self):
		ahora = timezone.now()
		torneo = self.crear_publico()
		apertura = ahora + timedelta(hours=24, seconds=1)
		torneo.fecha_apertura_inscripciones = apertura
		torneo.fecha_cierre_inscripciones = apertura + timedelta(minutes=30)
		torneo.save(update_fields=('fecha_apertura_inscripciones', 'fecha_cierre_inscripciones'))

		with self.assertRaises(InscripcionError):
			publicar_torneo(torneo, self.organizador, ahora=ahora)

	def test_prorroga_cancela_o_cierra_y_no_duplica_historial(self):
		ahora = timezone.now()
		torneo = self.crear_publico(nombre='Con prorroga', duracion_prorroga_min=15)
		self.publicar_con_fechas(torneo, ahora - timedelta(minutes=30), ahora - timedelta(minutes=1), ahora)
		self.assertEqual(procesar_calendario(ahora), 1)
		torneo.refresh_from_db()
		self.assertEqual(torneo.estado, Torneo.Estado.PRORROGA)
		self.assertEqual(procesar_calendario(ahora), 0)

		fin = ahora + timedelta(minutes=15)
		self.assertEqual(procesar_calendario(fin), 1)
		torneo.refresh_from_db()
		self.assertEqual(torneo.estado, Torneo.Estado.CANCELADO)
		self.assertEqual(torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.PRORROGA).count(), 1)
		self.assertEqual(torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.CANCELADO).count(), 1)

	def test_ultimo_participante_cierra_prorroga_y_historial(self):
		ahora = timezone.now()
		torneo = self.crear_publico(nombre='Completo en prorroga', duracion_prorroga_min=15)
		self.publicar_con_fechas(torneo, ahora - timedelta(minutes=30), ahora - timedelta(minutes=1), ahora)
		procesar_calendario(ahora)
		segundo_usuario = Usuario.objects.create_user(
			username='jugador2',
			email='jugador2@example.com',
			password='clave-segura-123',
		)
		PerfilVideojuegoUsuario.objects.create(
			usuario=segundo_usuario,
			videojuego=self.videojuego,
			nick_en_juego='JugadorDos',
			rango_declarado=self.rango,
		)
		inscribir_usuario(torneo, self.usuario)
		inscribir_usuario(torneo, segundo_usuario)
		torneo.refresh_from_db()
		self.assertEqual(torneo.estado, Torneo.Estado.INSCRIPCIONES_CERRADAS)
		self.assertEqual(torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.INSCRIPCIONES_CERRADAS).count(), 1)

	def test_cierre_normal_completo_generar_bracket_y_no_anticipar(self):
		ahora = timezone.now()
		torneo = self.crear_publico(nombre='Completo al cierre')
		self.publicar_con_fechas(torneo, ahora - timedelta(minutes=5), ahora + timedelta(minutes=10), ahora)
		segundo = Usuario.objects.create_user(username='jugador_cierre', email='jugador_cierre@example.com', password='clave-segura-123')
		PerfilVideojuegoUsuario.objects.create(usuario=segundo, videojuego=self.videojuego, nick_en_juego='JugadorCierre', rango_declarado=self.rango)
		inscribir_usuario(torneo, self.usuario)
		inscribir_usuario(torneo, segundo)
		self.assertEqual(procesar_calendario(ahora), 0)
		self.assertEqual(Partida.objects.filter(torneo=torneo).count(), 0)
		self.assertEqual(procesar_calendario(ahora + timedelta(minutes=11)), 1)
		torneo.refresh_from_db()
		self.assertEqual(torneo.estado, Torneo.Estado.PREPARADO)
		self.assertEqual(Partida.objects.filter(torneo=torneo).count(), 1)
		self.assertEqual(torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.PREPARADO).count(), 1)
		self.assertEqual(procesar_calendario(ahora + timedelta(minutes=12)), 0)

	def test_cierre_tardio_completo_prepara_sin_duplicar(self):
		ahora = timezone.now()
		torneo = self.crear_publico(nombre='Cierre tardio')
		self.publicar_con_fechas(torneo, ahora - timedelta(minutes=5), ahora + timedelta(minutes=30), ahora)
		segundo = Usuario.objects.create_user(username='jugador_tardio', email='jugador_tardio@example.com', password='clave-segura-123')
		PerfilVideojuegoUsuario.objects.create(usuario=segundo, videojuego=self.videojuego, nick_en_juego='JugadorTardio', rango_declarado=self.rango)
		inscribir_usuario(torneo, self.usuario)
		inscribir_usuario(torneo, segundo)
		torneo.fecha_apertura_inscripciones = ahora - timedelta(hours=2)
		torneo.fecha_cierre_inscripciones = ahora - timedelta(hours=1)
		torneo.save(update_fields=('fecha_apertura_inscripciones', 'fecha_cierre_inscripciones'))
		self.assertEqual(procesar_calendario(ahora), 1)
		torneo.refresh_from_db()
		self.assertEqual(torneo.estado, Torneo.Estado.PREPARADO)
		self.assertEqual(Partida.objects.filter(torneo=torneo).count(), 1)

	def test_inscripcion_rechazada_despues_del_cierre_sin_procesar_calendario(self):
		ahora = timezone.now()
		torneo = self.crear_publico(nombre='Vencido')
		self.publicar_con_fechas(torneo, ahora - timedelta(minutes=30), ahora - timedelta(minutes=1), ahora)
		with self.assertRaises(InscripcionError):
			inscribir_usuario(torneo, self.usuario)

	def test_cancelar_inscripcion_no_reabre_ni_cambia_estado(self):
		ahora = timezone.now()
		torneo = self.crear_publico(nombre='Completo')
		self.publicar_con_fechas(torneo, ahora - timedelta(minutes=30), ahora + timedelta(minutes=30), ahora)
		inscripcion = inscribir_usuario(torneo, self.usuario)
		cancelar_inscripcion(inscripcion)
		self.assertEqual(torneo.participantes_confirmados, 0)