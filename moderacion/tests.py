from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from partidas.models import Partida, ParticipantePartida
from notificaciones.models import Notificacion
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from usuarios.models import HistorialKarma, Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import Denuncia, HistorialDenuncia
from .services import (
	ModeracionError,
	crear_denuncia,
	resolver_denuncia,
	tomar_en_revision,
)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ModeracionTests(TestCase):
	def setUp(self):
		self.reporter = self.usuario('reporter')
		self.acusado = self.usuario('acusado')
		self.ajeno = self.usuario('ajeno')
		self.admin = Usuario.objects.create_superuser(
			username='moderador', email='moderador@example.com', password='test',
		)
		self.otro_admin = Usuario.objects.create_superuser(
			username='moderador2', email='moderador2@example.com', password='test',
		)
		self.videojuego = Videojuego.objects.create(nombre='Moderacion Game', genero='Competitivo')
		self.formato = FormatoCompetitivo.objects.create(
			nombre='Moderacion Format', min_participantes=2, max_participantes=128,
		)
		self.torneo = Torneo.objects.create(
			nombre='Moderacion Torneo', videojuego=self.videojuego,
			organizador=self.reporter, tipo=Torneo.Tipo.PUBLICO,
			formato_competitivo=self.formato, max_participantes=2,
			estado=Torneo.Estado.EN_CURSO, fecha_publicacion=timezone.now(),
		)
		self.inscripcion_reporter = self.inscribir(self.reporter, 'ReporterNick')
		self.inscripcion_acusado = self.inscribir(self.acusado, 'AcusadoNick')
		self.partida = Partida.objects.create(
			torneo=self.torneo, numero_ronda=1, numero_orden=1,
			estado=Partida.Estado.EN_CURSO,
		)
		ParticipantePartida.objects.create(
			partida=self.partida, inscripcion=self.inscripcion_reporter, posicion=1,
		)
		ParticipantePartida.objects.create(
			partida=self.partida, inscripcion=self.inscripcion_acusado, posicion=2,
		)

	def usuario(self, username):
		return Usuario.objects.create_user(
			username=username, email=f'{username}@example.com', password='test', karma_total=100,
		)

	def inscribir(self, usuario, nick):
		perfil = PerfilVideojuegoUsuario.objects.create(
			usuario=usuario, videojuego=self.videojuego, nick_en_juego=nick,
		)
		return InscripcionTorneo.objects.create(
			torneo=self.torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=nick,
		)

	def crear(self, *, partida=None, torneo=None, categoria=Denuncia.Categoria.CONDUCTA_INAPROPIADA,
	           acusado=None, descripcion='Describo el problema observado.'):
		return crear_denuncia(
			self.reporter, categoria, descripcion,
			torneo=torneo or (partida.torneo if partida else self.torneo),
			partida=partida,
			usuario_denunciado=acusado,
		)

	def tomar(self, denuncia, admin=None):
		return tomar_en_revision(denuncia, admin or self.admin)

	def test_creacion_valida_desde_torneo_y_partida_por_post(self):
		self.client.force_login(self.reporter)
		respuesta_torneo = self.client.post(reverse('denunciar-torneo', args=(self.torneo.pk,)), {
			'categoria': Denuncia.Categoria.PROBLEMA_ORGANIZACION,
			'descripcion': 'No se publicó el horario acordado.',
			'usuario_denunciado': '',
		})
		respuesta_partida = self.client.post(reverse('denunciar-partida', args=(self.partida.pk,)), {
			'categoria': Denuncia.Categoria.RESULTADO_INCORRECTO,
			'descripcion': 'Solicito revisar el resultado oficial.',
			'usuario_denunciado': str(self.acusado.pk),
		})
		self.assertEqual(respuesta_torneo.status_code, 302)
		self.assertEqual(respuesta_partida.status_code, 302)
		self.assertEqual(Denuncia.objects.filter(denunciante=self.reporter).count(), 2)
		self.assertEqual(HistorialDenuncia.objects.filter(denuncia__denunciante=self.reporter).count(), 2)
		self.assertEqual(Denuncia.objects.get(partida=self.partida).usuario_denunciado, self.acusado)
		self.partida.refresh_from_db()
		self.assertEqual(self.partida.estado, Partida.Estado.EN_CURSO)
		self.assertFalse(hasattr(self.partida, 'resultado_oficial'))

	def test_acceso_anonimo_descripcion_categoria_y_post(self):
		url = reverse('denunciar-partida', args=(self.partida.pk,))
		respuesta = self.client.get(url)
		self.assertRedirects(respuesta, f'{reverse("login")}?next={url}', fetch_redirect_response=False)
		self.client.force_login(self.reporter)
		self.assertEqual(self.client.get(url).status_code, 200)
		self.assertContains(self.client.get(url), 'no implica automáticamente una sanción')
		self.assertEqual(self.client.post(url, {
			'categoria': Denuncia.Categoria.TRAMPA, 'descripcion': '  ',
		}).status_code, 200)
		self.assertEqual(self.client.post(url, {
			'categoria': 'CATEGORIA_INVENTADA', 'descripcion': 'Descripción válida.',
		}).status_code, 200)
		self.assertFalse(Denuncia.objects.exists())

	def test_objetivo_no_relacionado_y_auto_denuncia_se_rechazan(self):
		with self.assertRaisesMessage(ModeracionError, 'No puedes denunciarte'):
			self.crear(partida=self.partida, acusado=self.reporter)
		with self.assertRaisesMessage(ModeracionError, 'no está relacionado'):
			self.crear(partida=self.partida, acusado=self.ajeno)
		self.assertFalse(Denuncia.objects.exists())

	def test_post_no_acepta_usuario_denunciado_arbitrario(self):
		self.client.force_login(self.reporter)
		respuesta = self.client.post(reverse('denunciar-partida', args=(self.partida.pk,)), {
			'categoria': Denuncia.Categoria.OTRO,
			'descripcion': 'El formulario no debe admitir un ID ajeno.',
			'usuario_denunciado': self.ajeno.pk,
		})
		self.assertEqual(respuesta.status_code, 200)
		self.assertFormError(respuesta.context['form'], 'usuario_denunciado', 'Select a valid choice. That choice is not one of the available choices.')
		self.assertFalse(Denuncia.objects.exists())

	def test_duplicado_abierto_equivalente_se_rechaza_pero_otra_categoria_se_permite(self):
		primera = self.crear(partida=self.partida, acusado=self.acusado)
		with self.assertRaisesMessage(ModeracionError, 'denuncia abierta equivalente'):
			self.crear(partida=self.partida, acusado=self.acusado)
		segunda = self.crear(
			partida=self.partida,
			acusado=self.acusado,
			categoria=Denuncia.Categoria.PROBLEMA_ARBITRAJE,
		)
		self.assertNotEqual(primera.pk, segunda.pk)

	def test_mis_denuncias_y_detalle_son_privados(self):
		propia = self.crear(partida=self.partida, acusado=self.acusado)
		ajena = crear_denuncia(
			self.ajeno, Denuncia.Categoria.OTRO, 'Caso de otra persona.',
			torneo=self.torneo,
		)
		self.client.force_login(self.reporter)
		self.assertContains(self.client.get(reverse('mis-denuncias')), Denuncia.Categoria.CONDUCTA_INAPROPIADA.label)
		self.assertEqual(self.client.get(reverse('detalle-denuncia', args=(ajena.pk,))).status_code, 404)
		self.client.force_login(self.acusado)
		self.assertEqual(self.client.get(reverse('detalle-denuncia', args=(propia.pk,))).status_code, 404)
		self.assertEqual(self.client.get(reverse('centro-moderacion')).status_code, 404)

	def test_centro_solo_admin_y_filtra_estado_categoria_torneo(self):
		denuncia = self.crear(partida=self.partida)
		self.assertEqual(self.client.get(reverse('centro-moderacion')).status_code, 302)
		self.client.force_login(self.reporter)
		self.assertEqual(self.client.get(reverse('centro-moderacion')).status_code, 404)
		self.client.force_login(self.admin)
		url = reverse('centro-moderacion')
		respuesta = self.client.get(url, {
			'estado': Denuncia.Estado.ABIERTA,
			'categoria': Denuncia.Categoria.CONDUCTA_INAPROPIADA,
			'torneo': self.torneo.pk,
		})
		self.assertContains(respuesta, denuncia.get_categoria_display())
		self.assertEqual(respuesta.context['page_obj'].paginator.count, 1)

	def test_revision_exclusiva_historial_y_doble_administrador(self):
		denuncia = self.crear(partida=self.partida)
		self.tomar(denuncia)
		denuncia.refresh_from_db()
		self.assertEqual(denuncia.estado, Denuncia.Estado.EN_REVISION)
		self.assertEqual(denuncia.asignada_a, self.admin)
		self.assertEqual(denuncia.historial.count(), 2)
		with self.assertRaisesMessage(ModeracionError, 'otro administrador'):
			tomar_en_revision(denuncia, self.otro_admin)
		self.assertEqual(tomar_en_revision(denuncia, self.admin)[1], False)
		evento = denuncia.historial.last()
		with self.assertRaises(ValidationError):
			evento.delete()
		resuelta, _ = resolver_denuncia(
			denuncia, self.admin, estado=Denuncia.Estado.DESESTIMADA,
			resolucion='No se confirma la alegación.',
		)
		with self.assertRaisesMessage(ModeracionError, 'Solo se pueden tomar denuncias abiertas.'):
			tomar_en_revision(resuelta, self.otro_admin)
		self.assertEqual(resuelta.estado, Denuncia.Estado.DESESTIMADA)

	def test_resolucion_sin_sancion_y_desestimacion_notifican_solo_al_denunciante(self):
		denuncia = self.crear(partida=self.partida, acusado=self.acusado)
		self.tomar(denuncia)
		resuelta, creada = resolver_denuncia(
			denuncia, self.admin, estado=Denuncia.Estado.RESUELTA,
			resolucion='Se confirma un problema operativo, sin sanción.',
		)
		self.assertTrue(creada)
		self.assertFalse(resuelta.hubo_sancion)
		self.assertFalse(HistorialKarma.objects.exists())
		aviso = resuelta.notificaciones.get(destinatario=self.reporter)
		self.assertTrue(aviso.es_critica)
		self.assertEqual(aviso.denuncia_id, resuelta.pk)
		self.assertFalse(Notificacion.objects.filter(destinatario=self.acusado).exists())
		self.client.force_login(self.reporter)
		self.assertContains(self.client.get(reverse('detalle-denuncia', args=(resuelta.pk,))), resuelta.resolucion)

		otra = crear_denuncia(
			self.ajeno, Denuncia.Categoria.OTRO, 'Alegación alternativa.', torneo=self.torneo,
		)
		tomar_en_revision(otra, self.admin)
		desestimada, _ = resolver_denuncia(
			otra, self.admin, estado=Denuncia.Estado.DESESTIMADA,
			resolucion='No se encontraron elementos que confirmen la alegación.',
		)
		self.assertFalse(desestimada.hubo_sancion)
		self.assertEqual(HistorialKarma.objects.count(), 0)

	def test_resolucion_con_amonestacion_cero_sin_movimiento_karma(self):
		denuncia = self.crear(partida=self.partida, acusado=self.acusado)
		self.tomar(denuncia)
		resolver_denuncia(
			denuncia, self.admin, estado=Denuncia.Estado.RESUELTA,
			resolucion='Se confirma la incidencia y se emite advertencia.',
			tipo_sancion=Denuncia.TipoSancion.ADVERTENCIA,
		)
		denuncia.refresh_from_db()
		self.assertTrue(denuncia.hubo_sancion)
		self.assertEqual(denuncia.karma_solicitado, 0)
		self.assertIsNone(denuncia.historial_karma_id)
		self.assertEqual(HistorialDenuncia.objects.filter(
			denuncia=denuncia, accion=HistorialDenuncia.Accion.SANCION_APLICADA,
		).count(), 1)
		self.assertEqual(self.acusado.karma_total, 100)

	def test_sanciones_cerradas_karma_idempotentes_y_suelo_cero(self):
		for indice, tipo_sancion, esperado in (
			(0, Denuncia.TipoSancion.LEVE, -5),
			(1, Denuncia.TipoSancion.MEDIA, -15),
			(2, Denuncia.TipoSancion.GRAVE, -30),
		):
			denuncia = crear_denuncia(
				self.reporter, Denuncia.Categoria.CONDUCTA_INAPROPIADA,
				f'Problema {indice}.', torneo=self.torneo, usuario_denunciado=self.acusado,
			)
			tomar_en_revision(denuncia, self.admin)
			resolver_denuncia(
				denuncia, self.admin, estado=Denuncia.Estado.RESUELTA,
				resolucion=f'Infracción confirmada {indice}.', tipo_sancion=tipo_sancion,
			)
			self.acusado.refresh_from_db()
			self.assertEqual(self.acusado.karma_total, 100 + sum(( -5, -15, -30)[:indice + 1]))
			denuncia.refresh_from_db()
			self.assertEqual(denuncia.historial_karma.cantidad_solicitada, esperado)
			self.assertEqual(denuncia.historial_karma.autorizado_por, self.admin)
			self.assertEqual(denuncia.historial_karma.clave_idempotencia, f'moderacion-denuncia:{denuncia.pk}:sancion')
			resumen = denuncia.resolucion
			cantidad_historial = HistorialDenuncia.objects.filter(denuncia=denuncia).count()
			resolver_denuncia(
				denuncia, self.admin, estado=Denuncia.Estado.RESUELTA,
				resolucion=resumen, tipo_sancion=tipo_sancion,
			)
			self.acusado.refresh_from_db()
			self.assertEqual(self.acusado.karma_total, 100 + sum((-5, -15, -30)[:indice + 1]))
			self.assertEqual(HistorialDenuncia.objects.filter(denuncia=denuncia).count(), cantidad_historial)

	def test_sancion_grave_respeta_suelo_cero_y_relaciona_historial(self):
		self.acusado.karma_total = 2
		self.acusado.save(update_fields=('karma_total',))
		denuncia = self.crear(partida=self.partida, acusado=self.acusado)
		tomar_en_revision(denuncia, self.admin)
		resolver_denuncia(
			denuncia, self.admin, estado=Denuncia.Estado.RESUELTA,
			resolucion='Infracción grave confirmada.', tipo_sancion=Denuncia.TipoSancion.GRAVE,
		)
		self.acusado.refresh_from_db()
		denuncia.refresh_from_db()
		self.assertEqual(self.acusado.karma_total, 0)
		self.assertEqual(denuncia.historial_karma.cantidad, -2)
		self.assertEqual(denuncia.historial_karma.cantidad_solicitada, -30)
		self.assertEqual(denuncia.historial_karma.partida_id, self.partida.pk)

	def test_resolucion_no_modifica_resultado_xp_estado_partida_ni_bracket(self):
		denuncia = self.crear(partida=self.partida, acusado=self.acusado)
		tomar_en_revision(denuncia, self.admin)
		karma_previa = self.acusado.karma_total
		xp_previa = self.acusado.xp_total
		resolver_denuncia(
			denuncia, self.admin, estado=Denuncia.Estado.RESUELTA,
			resolucion='Incidencia confirmada.', tipo_sancion=Denuncia.TipoSancion.LEVE,
		)
		self.partida.refresh_from_db()
		self.torneo.refresh_from_db()
		self.acusado.refresh_from_db()
		self.assertEqual(self.partida.estado, Partida.Estado.EN_CURSO)
		self.assertFalse(hasattr(self.partida, 'resultado_oficial'))
		self.assertEqual(self.partida.participantes.count(), 2)
		self.assertEqual(self.torneo.clasificaciones.count(), 0)
		self.assertEqual(self.acusado.xp_total, xp_previa)
		self.assertEqual(self.acusado.karma_total, karma_previa - 5)

	def test_admin_detalle_muestra_evidencia_y_notas_no_salen_al_reportero(self):
		denuncia = self.crear(partida=self.partida, acusado=self.acusado)
		tomar_en_revision(denuncia, self.admin)
		resolver_denuncia(
			denuncia, self.admin, estado=Denuncia.Estado.RESUELTA,
			resolucion='Resolución pública.', notas_internas='Nota confidencial.',
		)
		self.client.force_login(self.admin)
		respuesta_admin = self.client.get(reverse('detalle-denuncia', args=(denuncia.pk,)))
		self.assertContains(respuesta_admin, self.reporter.username)
		self.assertContains(respuesta_admin, self.acusado.username)
		self.assertContains(respuesta_admin, 'Nota confidencial.')
		self.client.force_login(self.reporter)
		respuesta_reporter = self.client.get(reverse('detalle-denuncia', args=(denuncia.pk,)))
		self.assertContains(respuesta_reporter, 'Resolución pública.')
		self.assertNotContains(respuesta_reporter, 'Nota confidencial.')
		self.assertNotContains(respuesta_reporter, self.acusado.email)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class NavegacionModeracionTests(TestCase):
	def test_mis_denuncias_solo_aparece_a_usuarios_autenticados(self):
		usuario = Usuario.objects.create_user(
			username='navegacion_moderacion',
			email='navegacion_moderacion@example.com',
			password='test',
		)
		url_denuncias = reverse('mis-denuncias')
		respuesta_anonima = self.client.get(reverse('inicio'))
		self.assertNotContains(respuesta_anonima, url_denuncias)
		self.client.force_login(usuario)
		respuesta_autenticada = self.client.get(reverse('inicio'))
		self.assertContains(respuesta_autenticada, url_denuncias)
		self.assertContains(respuesta_autenticada, 'Mis denuncias')
		self.assertNotContains(respuesta_autenticada, f'href="{reverse("centro-moderacion")}"')