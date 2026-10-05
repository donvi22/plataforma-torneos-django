from io import BytesIO
from datetime import timedelta

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Usuario


class UsuarioViewsTests(TestCase):
    def crear_imagen(self, nombre='avatar.png', formato='PNG'):
        contenido = BytesIO()
        Image.new('RGB', (32, 32), color='teal').save(contenido, format=formato)
        return SimpleUploadedFile(nombre, contenido.getvalue(), content_type='image/png')

    def crear_usuario(self, username='jugador', email='jugador@example.com', **extra):
        return Usuario.objects.create_user(
            username=username,
            email=email,
            password='ClaveSegura123!',
            **extra,
        )

    def test_inicio_para_visitante_y_usuario_autenticado(self):
        respuesta = self.client.get(reverse('inicio'))
        self.assertContains(respuesta, 'Crear cuenta')
        usuario = self.crear_usuario()
        self.client.force_login(usuario)
        respuesta = self.client.get(reverse('inicio'))
        self.assertContains(respuesta, usuario.username)
        self.assertContains(respuesta, reverse('perfil-propio'))

    def test_registro_normaliza_email_y_no_acepta_privilegios_manipulados(self):
        respuesta = self.client.post(reverse('registro'), {
            'username': 'nuevo',
            'email': ' NUEVO@EXAMPLE.COM ',
            'password1': 'ClaveSegura123!',
            'password2': 'ClaveSegura123!',
            'rol_global': Usuario.RolGlobal.ADMIN,
            'is_staff': 'on',
            'is_superuser': 'on',
            'nivel': 99,
            'karma_total': 999,
        })
        self.assertRedirects(respuesta, reverse('perfil', args=[Usuario.objects.get(username='nuevo').pk]))
        usuario = Usuario.objects.get(username='nuevo')
        self.assertEqual(usuario.email, 'nuevo@example.com')
        self.assertEqual(usuario.rol_global, Usuario.RolGlobal.PLAYER)
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        self.assertEqual(usuario.nivel, 1)
        self.assertEqual(usuario.karma_total, 100)

    def test_registro_rechaza_nickname_email_y_password_duplicados(self):
        self.crear_usuario()
        respuesta = self.client.post(reverse('registro'), {
            'username': 'jugador',
            'email': 'otro@example.com',
            'password1': 'ClaveSegura123!',
            'password2': 'ClaveSegura123!',
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'ya existe')
        respuesta = self.client.post(reverse('registro'), {
            'username': 'otro',
            'email': 'JUGADOR@EXAMPLE.COM',
            'password1': 'ClaveSegura123!',
            'password2': 'ClaveSegura123!',
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'ya existe')
        respuesta = self.client.post(reverse('registro'), {
            'username': 'tercero',
            'email': 'tercero@example.com',
            'password1': '123',
            'password2': '123',
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context['form'].errors['password2'])

    def test_login_acepta_nickname_rechaza_credenciales_y_cuenta_inactiva(self):
        usuario = self.crear_usuario()
        respuesta = self.client.post(reverse('login'), {
            'username': usuario.username,
            'password': 'ClaveSegura123!',
        })
        self.assertRedirects(respuesta, reverse('inicio'))
        self.client.logout()
        respuesta = self.client.post(reverse('login'), {'username': 'jugador', 'password': 'incorrecta'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Las credenciales no son correctas.')
        usuario.is_active = False
        usuario.save(update_fields=('is_active',))
        respuesta = self.client.post(reverse('login'), {'username': 'jugador', 'password': 'ClaveSegura123!'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse('_auth_user_id' in self.client.session)

    def test_logout_solo_acepta_post(self):
        usuario = self.crear_usuario()
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        respuesta = self.client.post(reverse('logout'))
        self.assertRedirects(respuesta, reverse('inicio'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_perfil_publico_oculta_email_y_respetas_preferencias(self):
        perfil = self.crear_usuario(mostrar_ultima_conexion=False, mostrar_estado_online=False)
        visitante = self.crear_usuario(username='visitante', email='visitante@example.com')
        self.client.force_login(visitante)
        respuesta = self.client.get(reverse('perfil', args=[perfil.pk]))
        self.assertContains(respuesta, perfil.username)
        self.assertNotContains(respuesta, perfil.email)
        self.assertNotContains(respuesta, 'Última conexión')
        self.assertNotContains(respuesta, 'Estado online')
        respuesta = self.client.get(reverse('perfil-propio'))
        self.assertContains(respuesta, perfil.email) if visitante.pk == perfil.pk else None

    def test_edicion_requiere_autenticacion_y_solo_permite_campos_explicitos(self):
        usuario = self.crear_usuario()
        otro = self.crear_usuario(username='otro', email='otro@example.com')
        respuesta = self.client.get(reverse('editar-perfil'))
        self.assertRedirects(respuesta, f'{reverse("login")}?next={reverse("editar-perfil")}')
        self.client.force_login(usuario)
        respuesta = self.client.post(reverse('editar-perfil'), {
            'avatar': self.crear_imagen(),
            'disponible_para_arbitrar': 'on',
            'mostrar_ultima_conexion': 'on',
            'mostrar_estado_online': 'on',
            'nivel': 99,
            'karma_total': 999,
            'rol_global': Usuario.RolGlobal.ADMIN,
            'email': 'atacante@example.com',
        })
        self.assertRedirects(respuesta, reverse('perfil', args=[usuario.pk]))
        usuario.refresh_from_db()
        self.assertTrue(usuario.disponible_para_arbitrar)
        self.assertEqual(usuario.nivel, 1)
        self.assertEqual(usuario.karma_total, 100)
        self.assertEqual(usuario.email, 'jugador@example.com')
        self.assertFalse(usuario.is_staff)
        respuesta = self.client.post(reverse('perfil', args=[otro.pk]), {
            'nivel': 99,
            'karma_total': 999,
        })
        self.assertEqual(respuesta.status_code, 200)
        otro.refresh_from_db()
        self.assertEqual(otro.nivel, 1)
        self.assertEqual(otro.karma_total, 100)

    def test_rechaza_archivo_no_imagen(self):
        usuario = self.crear_usuario()
        self.client.force_login(usuario)
        archivo = SimpleUploadedFile('archivo.txt', b'no soy una imagen', content_type='text/plain')
        respuesta = self.client.post(reverse('editar-perfil'), {'avatar': archivo})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'imagen válida')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AccountLifecycleViewsTests(TestCase):
    PASSWORD = 'ClaveSegura123!'

    def crear_usuario(self, username, **extra):
        return Usuario.objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password=self.PASSWORD,
            **extra,
        )

    def test_cambio_nickname_unico_actualiza_fecha_y_respeta_30_dias(self):
        usuario = self.crear_usuario('nick_actual')
        duplicado = self.crear_usuario('nick_ocupado')
        self.client.force_login(usuario)
        url = reverse('cambiar-nickname')

        respuesta = self.client.post(url, {'username': duplicado.username})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Este nickname ya existe.')
        usuario.refresh_from_db()
        self.assertEqual(usuario.username, 'nick_actual')

        respuesta = self.client.post(url, {'username': 'nick_nuevo'})
        self.assertRedirects(respuesta, reverse('editar-perfil'))
        usuario.refresh_from_db()
        primer_cambio = usuario.fecha_ultimo_cambio_nick
        self.assertEqual(usuario.username, 'nick_nuevo')
        self.assertIsNotNone(primer_cambio)

        respuesta = self.client.post(url, {'username': 'nick_demasiado_pronto'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'días')
        usuario.refresh_from_db()
        self.assertEqual(usuario.username, 'nick_nuevo')
        self.assertEqual(usuario.fecha_ultimo_cambio_nick, primer_cambio)

        usuario.fecha_ultimo_cambio_nick = timezone.now() - timedelta(days=31)
        usuario.save(update_fields=('fecha_ultimo_cambio_nick',))
        respuesta = self.client.post(url, {'username': 'nick_renovado'})
        self.assertRedirects(respuesta, reverse('editar-perfil'))
        usuario.refresh_from_db()
        self.assertEqual(usuario.username, 'nick_renovado')

    def test_cambio_nickname_no_reescribe_inscripcion_historica(self):
        from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
        from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

        usuario = self.crear_usuario('nick_con_historia')
        videojuego = Videojuego.objects.create(nombre='Nickname histórico', genero='Competitivo')
        formato = FormatoCompetitivo.objects.create(
            nombre='Nickname histórico', min_participantes=2, max_participantes=128,
        )
        torneo = Torneo.objects.create(
            nombre='Torneo histórico', videojuego=videojuego, organizador=usuario,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=formato, max_participantes=2,
        )
        perfil = PerfilVideojuegoUsuario.objects.create(
            usuario=usuario, videojuego=videojuego, nick_en_juego='NickSnapshot',
        )
        inscripcion = InscripcionTorneo.objects.create(
            torneo=torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico='NickSnapshot',
        )
        self.client.force_login(usuario)

        self.client.post(reverse('cambiar-nickname'), {'username': 'NickActualNuevo'})

        usuario.refresh_from_db()
        inscripcion.refresh_from_db()
        self.assertEqual(usuario.username, 'NickActualNuevo')
        self.assertEqual(inscripcion.nick_historico, 'NickSnapshot')

    def test_eliminacion_exige_password_y_confirmacion_explicita(self):
        usuario = self.crear_usuario('cuenta_confirmacion')
        self.client.force_login(usuario)
        url = reverse('eliminar-cuenta')
        self.assertEqual(self.client.post(url, {
            'password': 'incorrecta', 'confirmacion': 'ELIMINAR',
        }).status_code, 200)
        self.assertTrue(Usuario.objects.filter(pk=usuario.pk).exists())
        self.assertEqual(self.client.post(url, {
            'password': self.PASSWORD, 'confirmacion': 'no',
        }).status_code, 200)
        usuario.refresh_from_db()
        self.assertEqual(usuario.estado_cuenta, Usuario.EstadoCuenta.ACTIVA)

    def test_eliminacion_anonimiza_y_preserva_referencias_historicas_idempotentemente(self):
        usuario = self.crear_usuario('cuenta_a_eliminar', karma_total=107, xp_total=45, nivel=1)
        self.client.force_login(usuario)
        respuesta = self.client.post(reverse('eliminar-cuenta'), {
            'password': self.PASSWORD, 'confirmacion': 'ELIMINAR',
        })
        self.assertRedirects(respuesta, reverse('inicio'))
        usuario.refresh_from_db()
        self.assertEqual(usuario.estado_cuenta, Usuario.EstadoCuenta.ELIMINADA)
        self.assertFalse(usuario.is_active)
        self.assertEqual(usuario.username, f'usuario_eliminado_{usuario.pk}')
        self.assertTrue(usuario.email.endswith('@example.invalid'))
        self.assertFalse(usuario.has_usable_password())
        self.assertEqual((usuario.xp_total, usuario.karma_total), (45, 107))
        from notificaciones.models import Notificacion
        from notificaciones.services import crear_notificacion
        self.assertIsNone(crear_notificacion(
            usuario, Notificacion.Tipo.INTERVENCION_REQUERIDA,
            'Nuevo aviso', 'No debe recibirlo.', es_critica=True,
        ))
        self.assertEqual(self.client.post(reverse('eliminar-cuenta'), {
            'password': self.PASSWORD, 'confirmacion': 'ELIMINAR',
        }).status_code, 302)
        from .account_services import eliminar_cuenta
        usuario, cambio = eliminar_cuenta(usuario, 'contraseña ya inutilizable', 'ELIMINAR')
        self.assertFalse(cambio)
        self.assertEqual(usuario.username, f'usuario_eliminado_{usuario.pk}')

        login = self.client.post(reverse('login'), {
            'username': usuario.username, 'password': self.PASSWORD,
        })
        self.assertEqual(login.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        perfil = self.client.get(reverse('perfil', args=(usuario.pk,)))
        self.assertEqual(perfil.status_code, 404)
        self.assertNotContains(perfil, 'cuenta_a_eliminar', status_code=404)
        self.assertNotContains(perfil, 'cuenta_a_eliminar@example.com', status_code=404)

    def test_admin_no_puede_restaurar_identidad_eliminada_ni_borrarla(self):
        from django.contrib import admin
        from django.test import RequestFactory

        from .account_services import eliminar_cuenta

        usuario = self.crear_usuario('admin_no_restaurar')
        administrador = Usuario.objects.create_superuser(
            username='admin_lifecycle', email='admin_lifecycle@example.com', password='admin-pass',
        )
        eliminar_cuenta(usuario, self.PASSWORD, 'ELIMINAR')
        usuario.refresh_from_db()
        admin_request = RequestFactory().get('/admin/')
        admin_request.user = administrador
        admin_usuario = admin.site._registry[Usuario]
        readonly = set(admin_usuario.get_readonly_fields(admin_request, usuario))
        self.assertTrue({'username', 'email', 'estado_cuenta', 'fecha_eliminacion'} <= readonly)
        self.assertFalse(admin_usuario.has_delete_permission(admin_request, usuario))

        usuario.username = 'identidad_restaurada'
        usuario.email = 'restaurado@example.com'
        usuario.estado_cuenta = Usuario.EstadoCuenta.ACTIVA
        usuario.is_active = True
        admin_usuario.save_model(admin_request, usuario, None, True)

        usuario.refresh_from_db()
        self.assertEqual(usuario.estado_cuenta, Usuario.EstadoCuenta.ELIMINADA)
        self.assertEqual(usuario.username, f'usuario_eliminado_{usuario.pk}')
        self.assertNotEqual(usuario.email, 'restaurado@example.com')
        self.assertFalse(usuario.is_active)

    def test_eliminacion_preserva_torneo_partida_clasificacion_xp_karma_y_borra_avatar(self):
        from decimal import Decimal
        from pathlib import Path

        from partidas.models import Partida, ParticipantePartida, ResultadoPartida
        from torneos.models import ClasificacionTorneo, FormatoCompetitivo, InscripcionTorneo, Torneo
        from usuarios.karma import registrar_movimiento_karma
        from usuarios.models import HistorialKarma, HistorialXP
        from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

        usuario = self.crear_usuario('cuenta_historial', xp_total=45)
        organizador = self.crear_usuario('org_historial')
        videojuego = Videojuego.objects.create(nombre='Historial conservado', genero='Competitivo')
        formato = FormatoCompetitivo.objects.create(
            nombre='Historial conservado', min_participantes=2, max_participantes=128,
        )
        torneo = Torneo.objects.create(
            nombre='Historial conservado', videojuego=videojuego, organizador=organizador,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=formato, max_participantes=2,
            estado=Torneo.Estado.FINALIZADO,
        )
        perfil = PerfilVideojuegoUsuario.objects.create(
            usuario=usuario, videojuego=videojuego, nick_en_juego='Nick histórico vivo',
        )
        inscripcion = InscripcionTorneo.objects.create(
            torneo=torneo, usuario=usuario, perfil_videojuego=perfil,
            nick_historico='Snapshot del torneo',
        )
        clasificacion = ClasificacionTorneo.objects.create(
            torneo=torneo, inscripcion=inscripcion, posicion=1, es_campeon=True,
        )
        partida = Partida.objects.create(
            torneo=torneo, numero_ronda=1, numero_orden=1, estado=Partida.Estado.FINALIZADA,
        )
        ParticipantePartida.objects.create(partida=partida, inscripcion=inscripcion, posicion=1)
        ResultadoPartida.objects.create(
            partida=partida, resultado='2-0', ganador=inscripcion,
            tipo_resultado=ResultadoPartida.TipoResultado.INCOMPARECENCIA,
            validado_por=organizador,
        )
        HistorialXP.objects.create(
            usuario=usuario, torneo=torneo, inscripcion=inscripcion, clasificacion=clasificacion,
            xp_concedida=45, xp_anterior=0, xp_posterior=45,
            nivel_anterior=1, nivel_posterior=1, posicion_final=1, tamano_torneo=2,
            coeficiente_dificultad=Decimal('1.000'), multiplicador_especial=Decimal('1.00'),
            motivo='Snapshot XP histórico.',
        )
        registrar_movimiento_karma(
            usuario, 5, HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
            'Snapshot Karma histórico.', 'account-history:karma',
        )
        avatar = SimpleUploadedFile('avatar.png', b'avatar-bytes', content_type='image/png')
        usuario.avatar.save('avatar.png', avatar, save=True)
        avatar_path = Path(usuario.avatar.path)
        self.client.force_login(usuario)

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(reverse('eliminar-cuenta'), {
                'password': self.PASSWORD, 'confirmacion': 'ELIMINAR',
            })

        self.assertRedirects(respuesta, reverse('inicio'))
        usuario.refresh_from_db()
        inscripcion.refresh_from_db()
        perfil.refresh_from_db()
        self.assertEqual(inscripcion.nick_historico, 'Snapshot del torneo')
        self.assertEqual(perfil.nick_en_juego, f'usuario_eliminado_{usuario.pk}')
        self.assertTrue(ClasificacionTorneo.objects.filter(pk=clasificacion.pk).exists())
        self.assertTrue(ResultadoPartida.objects.filter(partida=partida).exists())
        self.assertEqual(HistorialXP.objects.get(usuario=usuario).xp_posterior, 45)
        self.assertEqual(HistorialKarma.objects.get(usuario=usuario).karma_despues, 105)
        self.assertFalse(avatar_path.exists())
        self.client.force_login(organizador)
        ficha = self.client.get(reverse('ficha-torneo', args=(torneo.pk,)))
        self.assertContains(ficha, 'Snapshot del torneo')
        self.assertNotContains(ficha, f'usuario_eliminado_{usuario.pk}')

    def test_suspendida_bloqueada_y_eliminada_no_crean_torneos_ni_inscripciones(self):
        from arbitraje.services import ArbitrajeError, invitar_arbitro
        from django.core.exceptions import ValidationError
        from moderacion.models import Denuncia
        from moderacion.services import ModeracionError, crear_denuncia
        from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
        from torneos.services import InscripcionError, crear_torneo, inscribir_usuario
        from usuarios.karma import KarmaError, registrar_movimiento_karma
        from usuarios.models import HistorialKarma
        from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

        videojuego = Videojuego.objects.create(nombre='Cuenta operativa', genero='Competitivo')
        formato = FormatoCompetitivo.objects.create(
            nombre='Cuenta operativa', min_participantes=2, max_participantes=128,
        )
        organizador = self.crear_usuario('org_operativo')
        torneo = Torneo.objects.create(
            nombre='Torneo operativo', videojuego=videojuego, organizador=organizador,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=formato, max_participantes=2,
            estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
        )
        torneo_publico = Torneo.objects.create(
            nombre='Torneo de arbitraje operativo', videojuego=videojuego,
            organizador=organizador, tipo=Torneo.Tipo.PUBLICO, formato_competitivo=formato,
            max_participantes=2, estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS,
            fecha_publicacion=timezone.now(),
        )
        usuario = self.crear_usuario('estado_suspendido')
        perfil = PerfilVideojuegoUsuario.objects.create(
            usuario=usuario, videojuego=videojuego, nick_en_juego='Suspendido',
        )
        usuario.estado_cuenta = Usuario.EstadoCuenta.SUSPENDIDA
        usuario.save(update_fields=('estado_cuenta',))

        for estado in (Usuario.EstadoCuenta.SUSPENDIDA, Usuario.EstadoCuenta.BLOQUEADA):
            usuario.estado_cuenta = estado
            usuario.save(update_fields=('estado_cuenta',))
            with self.assertRaises(InscripcionError):
                inscribir_usuario(torneo, usuario)
            with self.assertRaises(InscripcionError):
                crear_torneo(usuario, tipo=Torneo.Tipo.PRIVADO)
            with self.assertRaises(ArbitrajeError):
                invitar_arbitro(torneo_publico, usuario, organizador)
            with self.assertRaises(ModeracionError):
                crear_denuncia(
                    usuario, Denuncia.Categoria.OTRO, 'No debe entrar.', torneo=torneo_publico,
                )

        usuario.estado_cuenta = Usuario.EstadoCuenta.ELIMINADA
        usuario.is_active = False
        usuario.save(update_fields=('estado_cuenta', 'is_active'))
        with self.assertRaises(InscripcionError):
            inscribir_usuario(torneo, usuario)
        with self.assertRaises(InscripcionError):
            crear_torneo(usuario, tipo=Torneo.Tipo.PRIVADO)
        with self.assertRaises(ArbitrajeError):
            invitar_arbitro(torneo_publico, usuario, organizador)
        with self.assertRaises(ModeracionError):
            crear_denuncia(
                usuario, Denuncia.Categoria.OTRO, 'No debe entrar.', torneo=torneo_publico,
            )
        with self.assertRaises(KarmaError):
            registrar_movimiento_karma(
                usuario, 5, HistorialKarma.Tipo.PARTICIPACION_COMPLETADA,
                'No conceder a cuenta eliminada.', 'cuenta-eliminada:karma',
            )
        self.assertFalse(InscripcionTorneo.objects.filter(usuario=usuario).exists())

    def test_cuenta_eliminada_no_recibe_xp_futuro(self):
        from torneos.models import ClasificacionTorneo, FormatoCompetitivo, InscripcionTorneo, Torneo
        from usuarios.models import HistorialXP
        from usuarios.services import conceder_xp_torneo
        from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

        videojuego = Videojuego.objects.create(nombre='XP cuenta eliminada', genero='Competitivo')
        formato = FormatoCompetitivo.objects.create(
            nombre='XP cuenta eliminada', min_participantes=2, max_participantes=128,
        )
        organizador = self.crear_usuario('xp_eliminada_org')
        torneo = Torneo.objects.create(
            nombre='XP cuenta eliminada', videojuego=videojuego, organizador=organizador,
            tipo=Torneo.Tipo.PUBLICO, formato_competitivo=formato, max_participantes=2,
            estado=Torneo.Estado.FINALIZADO, fecha_publicacion=timezone.now(),
        )
        eliminada = self.crear_usuario('xp_eliminada')
        activa = self.crear_usuario('xp_activa')
        inscripciones = []
        for usuario in (eliminada, activa):
            perfil = PerfilVideojuegoUsuario.objects.create(
                usuario=usuario, videojuego=videojuego, nick_en_juego=usuario.username,
            )
            inscripciones.append(InscripcionTorneo.objects.create(
                torneo=torneo, usuario=usuario, perfil_videojuego=perfil,
                nick_historico=usuario.username,
            ))
        ClasificacionTorneo.objects.create(
            torneo=torneo, inscripcion=inscripciones[0], posicion=1, es_campeon=True,
        )
        ClasificacionTorneo.objects.create(
            torneo=torneo, inscripcion=inscripciones[1], posicion=2, ronda_eliminado=1,
        )
        from .account_services import eliminar_cuenta
        eliminar_cuenta(eliminada, self.PASSWORD, 'ELIMINAR')

        conceder_xp_torneo(torneo)

        eliminada.refresh_from_db()
        activa.refresh_from_db()
        self.assertEqual(eliminada.xp_total, 0)
        self.assertFalse(HistorialXP.objects.filter(usuario=eliminada).exists())
        self.assertGreater(activa.xp_total, 0)

    def test_cuentas_suspendidas_y_bloqueadas_no_inician_sesion(self):
        for estado in (Usuario.EstadoCuenta.SUSPENDIDA, Usuario.EstadoCuenta.BLOQUEADA):
            with self.subTest(estado=estado):
                usuario = self.crear_usuario(f'cuenta_{estado.lower()}')
                usuario.estado_cuenta = estado
                usuario.save(update_fields=('estado_cuenta',))
                respuesta = self.client.post(reverse('login'), {
                    'username': usuario.username, 'password': self.PASSWORD,
                })
                self.assertEqual(respuesta.status_code, 200)
                self.assertNotIn('_auth_user_id', self.client.session)
