from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from arbitraje.services import ArbitrajeError, invitar_arbitro
from partidas.models import Partida
from usuarios.models import Usuario
from usuarios.services import XPError, conceder_xp_torneo
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import AccesoTorneoPrivado, FormatoCompetitivo, InscripcionTorneo, Torneo
from .privados import acceder_con_codigo, inscribir_en_privado, regenerar_codigo_privado
from .services import InscripcionError, crear_torneo, inscribir_usuario

CLAVE = 'ClaveSegura123!'


class TorneoPrivadoTestBase(TestCase):
    def setUp(self):
        self.organizador = self.usuario('organizador')
        self.juego = Videojuego.objects.create(nombre='Juego privado', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(nombre='Formato privado', min_participantes=2, max_participantes=128)
        self.torneo = self.crear(estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS)

    def usuario(self, nombre, perfil=False):
        usuario = Usuario.objects.create_user(username=nombre, email=f'{nombre}@example.com', password=CLAVE)
        if perfil:
            PerfilVideojuegoUsuario.objects.create(
                usuario=usuario, videojuego=getattr(self, 'juego', None) or Videojuego.objects.first(),
                nick_en_juego=f'nick{nombre}',
            )
        return usuario

    def crear(self, organizador=None, **extra):
        datos = dict(
            nombre=f'Privado {Torneo.objects.count()}', videojuego=self.juego,
            organizador=organizador or self.organizador, tipo=Torneo.Tipo.PRIVADO,
            formato_competitivo=self.formato, max_participantes=2,
        )
        datos.update(extra)
        return Torneo.objects.create(**datos)

    def ruta(self, torneo=None):
        return reverse('acceso-privado', args=[(torneo or self.torneo).codigo_acceso])

    def jugador(self, nombre):
        return self.usuario(nombre, perfil=True)


class CodigoPrivadoTests(TorneoPrivadoTestBase):
    def test_genera_codigo_no_trivial_y_unico(self):
        otro = self.crear()
        self.assertGreaterEqual(len(self.torneo.codigo_acceso), 20)
        self.assertNotEqual(self.torneo.codigo_acceso, otro.codigo_acceso)
        self.assertNotIn(str(self.torneo.pk), self.torneo.codigo_acceso.split('-'))

    def test_crear_torneo_privado_por_servicio_incluye_codigo(self):
        self.organizador.save()
        torneo = crear_torneo(
            self.usuario('creador'), nombre='Servicio', videojuego=self.juego, tipo=Torneo.Tipo.PRIVADO,
            formato_competitivo=self.formato, max_participantes=2,
        )
        self.assertTrue(torneo.codigo_acceso)

    def test_publico_no_recibe_codigo(self):
        publico = self.crear(tipo=Torneo.Tipo.PUBLICO, estado=Torneo.Estado.BORRADOR)
        self.assertEqual(publico.codigo_acceso, '')

    def test_edicion_no_regenera_codigo(self):
        codigo = self.torneo.codigo_acceso
        self.torneo.nombre = 'Editado'
        self.torneo.save()
        self.torneo.save(update_fields=('estado',))
        self.torneo.refresh_from_db()
        self.assertEqual(self.torneo.codigo_acceso, codigo)

    def test_codigo_no_aparece_en_ficha_de_invitado_ni_catalogo(self):
        invitado = self.jugador('invitado')
        self.client.force_login(invitado)
        self.client.get(self.ruta())
        self.assertNotContains(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])), self.torneo.codigo_acceso)
        catalogo = self.client.get(reverse('catalogo-torneos'))
        self.assertNotContains(catalogo, self.torneo.nombre)
        self.assertNotContains(catalogo, self.torneo.codigo_acceso)


class AccesoPrivadoTests(TorneoPrivadoTestBase):
    def test_codigo_invalido_es_neutral(self):
        self.client.force_login(self.jugador('invitado'))
        respuesta = self.client.get(reverse('acceso-privado', args=['inventado123']))
        self.assertEqual(respuesta.status_code, 404)
        self.assertNotContains(respuesta, self.torneo.nombre, status_code=404)
        self.assertEqual(AccesoTorneoPrivado.objects.count(), 0)

    def test_enlace_valido_concede_acceso_persistente(self):
        invitado = self.jugador('invitado')
        self.client.force_login(invitado)
        respuesta = self.client.get(self.ruta())
        self.assertRedirects(respuesta, reverse('ficha-torneo', args=[self.torneo.pk]))
        acceso = AccesoTorneoPrivado.objects.get(torneo=self.torneo, usuario=invitado)
        self.assertEqual(acceso.estado, AccesoTorneoPrivado.Estado.ACTIVO)
        self.client.get(self.ruta())
        self.assertEqual(AccesoTorneoPrivado.objects.count(), 1)
        self.client.logout()
        self.client.force_login(invitado)
        self.assertEqual(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])).status_code, 200)

    def test_usuario_sin_acceso_no_entra_por_id(self):
        self.client.force_login(self.jugador('curioso'))
        self.assertEqual(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('bracket-torneo', args=[self.torneo.pk])).status_code, 404)
        respuesta = self.client.post(reverse('inscribirse-privado', args=[self.torneo.pk]))
        self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(InscripcionTorneo.objects.exists())
        self.client.logout()
        self.assertEqual(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])).status_code, 404)

    def test_anonimo_vuelve_al_enlace_tras_login(self):
        ruta = self.ruta()
        respuesta = self.client.get(ruta)
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta.url, f'/login/?next={ruta}')
        self.jugador('invitado')
        respuesta = self.client.post(respuesta.url, {'username': 'invitado', 'password': CLAVE})
        self.assertRedirects(respuesta, ruta, fetch_redirect_response=False)

    def test_anonimo_con_codigo_invalido_tambien_va_al_login(self):
        respuesta = self.client.get(reverse('acceso-privado', args=['inventado123']))
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(respuesta.url.startswith('/login/'))

    def test_login_y_registro_rechazan_redireccion_externa(self):
        self.jugador('invitado')
        respuesta = self.client.post('/login/?next=https://evil.example/', {'username': 'invitado', 'password': CLAVE})
        self.assertNotIn('evil.example', respuesta.url)
        self.client.logout()
        respuesta = self.client.post(
            '/registro/?next=https://evil.example/',
            {'username': 'nuevo', 'email': 'nuevo@example.com', 'password1': 'OtraClave98765!', 'password2': 'OtraClave98765!'},
        )
        self.assertNotIn('evil.example', respuesta.url)

    def test_registro_vuelve_al_enlace_privado(self):
        ruta = self.ruta()
        respuesta = self.client.post(
            f'/registro/?next={ruta}',
            {'username': 'nuevo', 'email': 'nuevo@example.com', 'password1': 'OtraClave98765!', 'password2': 'OtraClave98765!'},
        )
        self.assertRedirects(respuesta, ruta, fetch_redirect_response=False)

    def test_organizador_abre_su_enlace_sin_crear_acceso(self):
        self.client.force_login(self.organizador)
        self.assertRedirects(self.client.get(self.ruta()), reverse('ficha-torneo', args=[self.torneo.pk]))
        self.assertEqual(AccesoTorneoPrivado.objects.count(), 0)

    def test_cuentas_no_operativas_no_obtienen_acceso(self):
        for estado in (Usuario.EstadoCuenta.SUSPENDIDA, Usuario.EstadoCuenta.BLOQUEADA, Usuario.EstadoCuenta.ELIMINADA):
            usuario = self.usuario(f'usr{estado}')
            Usuario.objects.filter(pk=usuario.pk).update(estado_cuenta=estado)
            usuario.refresh_from_db()
            self.assertIsNone(acceder_con_codigo(self.torneo.codigo_acceso, usuario))
        inactivo = self.usuario('inactivo')
        Usuario.objects.filter(pk=inactivo.pk).update(is_active=False)
        inactivo.refresh_from_db()
        self.assertIsNone(acceder_con_codigo(self.torneo.codigo_acceso, inactivo))
        self.assertEqual(AccesoTorneoPrivado.objects.count(), 0)

    def test_estados_cerrados_no_dan_accesos_nuevos_pero_conservan_los_previos(self):
        previo = self.jugador('previo')
        nuevo = self.jugador('nuevo')
        self.assertIsNotNone(acceder_con_codigo(self.torneo.codigo_acceso, previo))
        for estado in (Torneo.Estado.CANCELADO, Torneo.Estado.FINALIZADO, Torneo.Estado.BORRADOR):
            Torneo.objects.filter(pk=self.torneo.pk).update(estado=estado)
            self.assertIsNone(acceder_con_codigo(self.torneo.codigo_acceso, nuevo))
        Torneo.objects.filter(pk=self.torneo.pk).update(estado=Torneo.Estado.FINALIZADO)
        self.client.force_login(previo)
        self.assertEqual(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])).status_code, 200)
        self.assertTrue(AccesoTorneoPrivado.objects.filter(usuario=previo).exists())

    def test_mis_torneos_privados_solo_muestra_los_relevantes(self):
        ajeno = self.crear(nombre='Torneo ajeno', organizador=self.usuario('otroorg'), estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS)
        invitado = self.jugador('invitado')
        self.client.force_login(invitado)
        self.assertNotContains(self.client.get(reverse('mis-torneos-privados')), self.torneo.nombre)
        self.client.get(self.ruta())
        listado = self.client.get(reverse('mis-torneos-privados'))
        self.assertContains(listado, self.torneo.nombre)
        self.assertNotContains(listado, 'Torneo ajeno')
        self.client.force_login(self.organizador)
        self.assertContains(self.client.get(reverse('mis-torneos-privados')), self.torneo.nombre)
        self.assertNotContains(self.client.get(reverse('mis-torneos-privados')), ajeno.nombre)

    def test_privados_no_aparecen_en_busqueda_de_usuarios(self):
        self.assertContains(self.client.get(reverse('buscar-usuarios'), {'q': self.torneo.nombre}), 'No se encontraron')


class InscripcionPrivadaTests(TorneoPrivadoTestBase):
    def setUp(self):
        super().setUp()
        self.jug = self.jugador('jugador1')
        self.client.force_login(self.jug)
        self.client.get(self.ruta())

    def inscribir(self):
        return self.client.post(reverse('inscribirse-privado', args=[self.torneo.pk]), follow=True)

    def test_inscripcion_valida_y_ficha_privada(self):
        ficha = self.client.get(reverse('ficha-torneo', args=[self.torneo.pk]))
        self.assertContains(ficha, 'Torneo privado')
        self.assertContains(ficha, 'Plazas disponibles: 2')
        self.assertContains(ficha, 'Inscribirme')
        respuesta = self.inscribir()
        self.assertContains(respuesta, 'Te has inscrito correctamente')
        inscripcion = InscripcionTorneo.objects.get(torneo=self.torneo, usuario=self.jug)
        self.assertEqual(inscripcion.nick_historico, 'nickjugador1')
        self.assertContains(respuesta, 'Plazas disponibles: 1')

    def test_get_no_inscribe(self):
        self.assertEqual(self.client.get(reverse('inscribirse-privado', args=[self.torneo.pk])).status_code, 405)
        self.assertFalse(InscripcionTorneo.objects.exists())

    def test_organizador_no_se_inscribe(self):
        PerfilVideojuegoUsuario.objects.create(usuario=self.organizador, videojuego=self.juego, nick_en_juego='org')
        self.client.force_login(self.organizador)
        respuesta = self.client.post(reverse('inscribirse-privado', args=[self.torneo.pk]))
        self.assertEqual(respuesta.status_code, 404)
        with self.assertRaises(InscripcionError):
            inscribir_usuario(self.torneo, self.organizador)
        self.assertFalse(InscripcionTorneo.objects.exists())

    def test_sin_perfil_duplicada_y_completa(self):
        PerfilVideojuegoUsuario.objects.filter(usuario=self.jug).delete()
        self.assertContains(self.inscribir(), 'perfil')
        PerfilVideojuegoUsuario.objects.create(usuario=self.jug, videojuego=self.juego, nick_en_juego='nick')
        self.inscribir()
        self.assertContains(self.inscribir(), 'ya tiene una inscripción')
        self.assertEqual(InscripcionTorneo.objects.count(), 1)
        for nombre in ('b', 'c'):
            otro = self.jugador(nombre)
            self.client.force_login(otro)
            self.client.get(self.ruta())
            self.inscribir()
        self.assertEqual(InscripcionTorneo.objects.filter(torneo=self.torneo).count(), 2)
        self.assertFalse(InscripcionTorneo.objects.filter(usuario__username='c').exists())

    def test_cancelacion_y_reinscripcion_reutilizan_la_fila(self):
        self.inscribir()
        inscripcion = InscripcionTorneo.objects.get(usuario=self.jug)
        self.client.post(reverse('cancelar-inscripcion', args=[inscripcion.pk]))
        inscripcion.refresh_from_db()
        self.assertEqual(inscripcion.estado, InscripcionTorneo.Estado.CANCELADA)
        cancelada_en = inscripcion.fecha_cancelacion
        ficha = self.client.get(reverse('ficha-torneo', args=[self.torneo.pk]))
        self.assertContains(ficha, 'Volver a inscribirme')
        self.client.post(reverse('inscribirse-privado', args=[self.torneo.pk]))
        inscripcion.refresh_from_db()
        self.assertEqual(InscripcionTorneo.objects.filter(usuario=self.jug).count(), 1)
        self.assertEqual(inscripcion.estado, InscripcionTorneo.Estado.CONFIRMADA)
        self.assertEqual(inscripcion.reinscripciones, 1)
        self.assertEqual(inscripcion.fecha_cancelacion, cancelada_en)
        self.assertTrue(inscripcion.motivo_cancelacion)
        self.assertEqual(self.torneo.participantes_confirmados, 1)

    def test_reinscripcion_con_torneo_cerrado_se_rechaza(self):
        self.inscribir()
        inscripcion = InscripcionTorneo.objects.get(usuario=self.jug)
        self.client.post(reverse('cancelar-inscripcion', args=[inscripcion.pk]))
        Torneo.objects.filter(pk=self.torneo.pk).update(estado=Torneo.Estado.INSCRIPCIONES_CERRADAS)
        with self.assertRaises(InscripcionError):
            inscribir_en_privado(self.torneo, self.jug)

    def test_descalificado_no_se_reactiva(self):
        self.inscribir()
        InscripcionTorneo.objects.filter(usuario=self.jug).update(estado=InscripcionTorneo.Estado.DESCALIFICADA)
        with self.assertRaises(InscripcionError):
            inscribir_en_privado(self.torneo, self.jug)
        self.assertEqual(InscripcionTorneo.objects.get(usuario=self.jug).estado, InscripcionTorneo.Estado.DESCALIFICADA)

    def test_reinscripcion_publica_sigue_rechazada(self):
        publico = self.crear(
            tipo=Torneo.Tipo.PUBLICO, estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
            fecha_apertura_inscripciones=timezone.now() - timedelta(minutes=5),
            fecha_cierre_inscripciones=timezone.now() + timedelta(minutes=30),
        )
        inscripcion = inscribir_usuario(publico, self.jug)
        InscripcionTorneo.objects.filter(pk=inscripcion.pk).update(estado=InscripcionTorneo.Estado.CANCELADA)
        with self.assertRaises(InscripcionError):
            inscribir_usuario(publico, self.jug)

    def test_sin_xp_karma_ni_arbitraje(self):
        with self.assertRaises(XPError):
            conceder_xp_torneo(self.torneo)
        with self.assertRaises(ArbitrajeError):
            invitar_arbitro(self.torneo, self.jugador('arbitro'), self.organizador)
        self.assertEqual(self.torneo.accesos_privados.count(), 1)

    def test_flujo_completo_hasta_bracket(self):
        self.inscribir()
        otro = self.jugador('jugador2')
        self.client.force_login(otro)
        self.client.get(self.ruta())
        self.inscribir()
        self.client.force_login(self.organizador)
        self.client.post(reverse('preparar-torneo', args=[self.torneo.pk]))
        self.assertEqual(Partida.objects.filter(torneo=self.torneo).count(), 1)


class GestionPrivadaTests(TorneoPrivadoTestBase):
    def test_organizador_ve_enlace_y_listas(self):
        invitado = self.jugador('invitado')
        self.client.force_login(invitado)
        self.client.get(self.ruta())
        self.client.force_login(self.organizador)
        respuesta = self.client.get(reverse('gestionar-privado', args=[self.torneo.pk]))
        self.assertContains(respuesta, 'Enlace de invitación')
        self.assertContains(respuesta, self.torneo.codigo_acceso)
        self.assertContains(respuesta, 'data-copy-target')
        self.assertContains(respuesta, 'invitado')

    def test_otro_organizador_y_visitantes_no_gestionan(self):
        otro = self.usuario('otroorg')
        for usuario in (otro, self.jugador('invitado')):
            self.client.force_login(usuario)
            for nombre in ('gestionar-privado', 'regenerar-privado'):
                metodo = self.client.get if nombre == 'gestionar-privado' else self.client.post
                self.assertEqual(metodo(reverse(nombre, args=[self.torneo.pk])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('gestionar-privado', args=[self.torneo.pk])).status_code, 302)

    def test_regenerar_solo_por_post_y_cambia_codigo(self):
        antiguo = self.torneo.codigo_acceso
        self.client.force_login(self.organizador)
        self.assertEqual(self.client.get(reverse('regenerar-privado', args=[self.torneo.pk])).status_code, 405)
        self.torneo.refresh_from_db()
        self.assertEqual(self.torneo.codigo_acceso, antiguo)
        self.client.post(reverse('regenerar-privado', args=[self.torneo.pk]))
        self.torneo.refresh_from_db()
        self.assertNotEqual(self.torneo.codigo_acceso, antiguo)

    def test_regeneracion_invalida_enlace_antiguo_y_conserva_accesos_e_inscripciones(self):
        autorizado = self.jugador('autorizado')
        self.client.force_login(autorizado)
        self.client.get(self.ruta())
        self.client.post(reverse('inscribirse-privado', args=[self.torneo.pk]))
        antiguo = self.torneo.codigo_acceso
        regenerar_codigo_privado(self.torneo, self.organizador)
        self.torneo.refresh_from_db()
        nuevo_usuario = self.jugador('nuevo')
        self.client.force_login(nuevo_usuario)
        self.assertEqual(self.client.get(reverse('acceso-privado', args=[antiguo])).status_code, 404)
        self.assertFalse(AccesoTorneoPrivado.objects.filter(usuario=nuevo_usuario).exists())
        self.assertRedirects(self.client.get(self.ruta()), reverse('ficha-torneo', args=[self.torneo.pk]))
        self.client.force_login(autorizado)
        self.assertEqual(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])).status_code, 200)
        self.assertEqual(InscripcionTorneo.objects.filter(usuario=autorizado, estado=InscripcionTorneo.Estado.CONFIRMADA).count(), 1)

    def test_regenerar_requiere_ser_organizador_en_servicio(self):
        with self.assertRaises(InscripcionError):
            regenerar_codigo_privado(self.torneo, self.jugador('intruso'))

    def test_revocar_acceso_antes_de_inscribirse(self):
        invitado = self.jugador('invitado')
        self.client.force_login(invitado)
        self.client.get(self.ruta())
        acceso = AccesoTorneoPrivado.objects.get(usuario=invitado)
        self.client.force_login(self.organizador)
        self.assertEqual(self.client.get(reverse('revocar-acceso-privado', args=[self.torneo.pk, acceso.pk])).status_code, 405)
        self.client.post(reverse('revocar-acceso-privado', args=[self.torneo.pk, acceso.pk]))
        acceso.refresh_from_db()
        self.assertEqual(acceso.estado, AccesoTorneoPrivado.Estado.REVOCADO)
        self.client.force_login(invitado)
        self.assertEqual(self.client.get(reverse('ficha-torneo', args=[self.torneo.pk])).status_code, 404)
        self.assertEqual(self.client.get(self.ruta()).status_code, 404)
        self.assertEqual(self.client.post(reverse('inscribirse-privado', args=[self.torneo.pk])).status_code, 404)

    def test_no_se_revoca_acceso_de_participante_ni_de_otro_torneo(self):
        jug = self.jugador('jugador1')
        self.client.force_login(jug)
        self.client.get(self.ruta())
        self.client.post(reverse('inscribirse-privado', args=[self.torneo.pk]))
        acceso = AccesoTorneoPrivado.objects.get(usuario=jug)
        self.client.force_login(self.organizador)
        self.client.post(reverse('revocar-acceso-privado', args=[self.torneo.pk, acceso.pk]))
        acceso.refresh_from_db()
        self.assertEqual(acceso.estado, AccesoTorneoPrivado.Estado.ACTIVO)
        otro = self.crear(organizador=self.usuario('otroorg'))
        self.client.force_login(otro.organizador)
        self.assertEqual(self.client.post(reverse('revocar-acceso-privado', args=[otro.pk, acceso.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('revocar-acceso-privado', args=[self.torneo.pk, acceso.pk])).status_code, 404)

    def test_abrir_inscripciones_privadas_desde_borrador(self):
        borrador = self.crear(estado=Torneo.Estado.BORRADOR)
        invitado = self.jugador('invitado')
        self.assertIsNone(acceder_con_codigo(borrador.codigo_acceso, invitado))
        self.client.force_login(invitado)
        self.assertEqual(self.client.post(reverse('publicar-torneo', args=[borrador.pk])).status_code, 404)
        self.client.force_login(self.organizador)
        self.client.post(reverse('publicar-torneo', args=[borrador.pk]))
        borrador.refresh_from_db()
        self.assertEqual(borrador.estado, Torneo.Estado.INSCRIPCIONES_ABIERTAS)
        self.assertIsNone(borrador.fecha_publicacion)
        self.assertIsNotNone(acceder_con_codigo(borrador.codigo_acceso, invitado))
