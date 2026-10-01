from io import BytesIO

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

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
