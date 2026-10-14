from datetime import timedelta
from io import BytesIO

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, RangoVideojuego, Videojuego

from .models import FormatoCompetitivo, InscripcionTorneo, Torneo
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

    def test_crear_formulario_seccionado_con_campos_unicos_y_edicion_sin_cambios(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('crear-torneo'))
        self.assertTemplateUsed(respuesta, 'torneos/crear.html')
        for titulo in ('Información básica', 'Competición', 'Fechas y calendario', 'Acceso y visibilidad', 'Revisión y creación', 'Tu torneo'):
            self.assertContains(respuesta, titulo)
        for campo in TorneoForm.Meta.fields:
            self.assertContains(respuesta, f'name="{campo}"', count=1)
        self.assertContains(respuesta, 'name="csrfmiddlewaretoken"')
        self.assertContains(respuesta, 'enctype="multipart/form-data"')
        self.assertContains(respuesta, 'data-create-tournament')
        torneo = Torneo.objects.create(
            nombre='Edición conservada', organizador=self.usuario, videojuego=self.videojuego,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=self.formato, max_participantes=2,
        )
        edicion = self.client.get(reverse('editar-borrador-torneo', args=[torneo.pk]))
        self.assertTemplateUsed(edicion, 'torneos/gestionar.html')
        self.assertNotContains(edicion, 'css/tournament-create.css')
        self.assertContains(edicion, 'Guardar borrador')

    def test_crear_tipos_visibles_son_los_del_formulario_y_oficial_solo_admin(self):
        self.client.force_login(self.otro)
        respuesta = self.client.get(reverse('crear-torneo'))
        self.assertEqual([tipo for tipo, _ in respuesta.context['form'].fields['tipo'].choices], [Torneo.Tipo.PRIVADO])
        self.assertNotContains(respuesta, 'data-type-description="OFICIAL"')
        self.client.force_login(self.usuario)
        publico = self.client.get(reverse('crear-torneo'))
        self.assertEqual({tipo for tipo, _ in publico.context['form'].fields['tipo'].choices}, {Torneo.Tipo.PRIVADO, Torneo.Tipo.PUBLICO})
        self.client.force_login(self.admin)
        oficial = self.client.get(reverse('crear-torneo'))
        self.assertContains(oficial, 'data-type-description="OFICIAL"')
        self.client.post(reverse('crear-torneo'), self.datos_privado(nombre='Oficial autorizado', tipo='OFICIAL'))
        self.assertTrue(Torneo.objects.filter(nombre='Oficial autorizado', tipo=Torneo.Tipo.OFICIAL, estado=Torneo.Estado.BORRADOR).exists())

    def test_crear_errores_de_fecha_visibles_y_valores_preservados(self):
        self.client.force_login(self.usuario)
        apertura = timezone.localtime(timezone.now()).replace(second=0, microsecond=0) + timedelta(hours=2)
        datos = self.datos_publico(
            nombre='Nombre conservado',
            fecha_apertura_inscripciones=apertura.strftime('%Y-%m-%dT%H:%M'),
            fecha_cierre_inscripciones=(apertura - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M'),
        )
        respuesta = self.client.post(reverse('crear-torneo'), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context['form'].errors)
        self.assertContains(respuesta, 'Revisa los datos del torneo')
        self.assertContains(respuesta, 'data-bound="true"')
        self.assertContains(respuesta, 'id="id_fecha_cierre_inscripciones_error"')
        self.assertContains(respuesta, 'value="Nombre conservado"')
        self.assertContains(respuesta, f'value="{datos["fecha_cierre_inscripciones"]}"')
        self.assertFalse(Torneo.objects.filter(nombre='Nombre conservado').exists())
        privado = self.client.post(reverse('crear-torneo'), self.datos_privado(rango_minimo=self.rango_otro.pk))
        self.assertIn('rango_minimo', privado.context['form'].errors)
        self.assertContains(privado, 'data-has-errors="true"')
        self.assertContains(privado, 'id="id_rango_minimo_error"')

    def test_crear_error_general_de_limite_se_muestra_sin_crear_otro(self):
        Torneo.objects.create(
            nombre='Privado existente', organizador=self.usuario, videojuego=self.videojuego,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=self.formato, max_participantes=2,
        )
        self.client.force_login(self.usuario)
        respuesta = self.client.post(reverse('crear-torneo'), self.datos_privado(nombre='Segundo privado'))
        self.assertTrue(respuesta.context['form'].non_field_errors())
        self.assertContains(respuesta, 'ya tiene un torneo privado activo')
        self.assertContains(respuesta, 'value="Segundo privado"')
        self.assertEqual(Torneo.objects.filter(organizador=self.usuario).count(), 1)

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
        ajeno = Torneo.objects.create(
            nombre='Ajeno', videojuego=self.videojuego, organizador=self.otro,
            tipo=Torneo.Tipo.PRIVADO, formato_competitivo=self.formato, max_participantes=2,
        )
        perfil = PerfilVideojuegoUsuario.objects.create(usuario=self.usuario, videojuego=self.videojuego, nick_en_juego='Inscrito')
        InscripcionTorneo.objects.create(torneo=ajeno, usuario=self.usuario, perfil_videojuego=perfil, nick_historico='Inscrito')
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('mis-torneos'))
        self.assertNotContains(respuesta, 'Ajeno')

    def crear_torneo_organizado(self, nombre, estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS, tipo=Torneo.Tipo.PUBLICO, **extra):
        return Torneo.objects.create(
            nombre=nombre, videojuego=self.videojuego, organizador=self.usuario,
            tipo=tipo, formato_competitivo=self.formato, max_participantes=8, estado=estado, **extra,
        )

    def test_mis_torneos_prioriza_atencion_y_conserva_historico(self):
        ahora = timezone.now()
        terminal = self.crear_torneo_organizado('Finalizado reciente', Torneo.Estado.FINALIZADO)
        cancelado = self.crear_torneo_organizado('Cancelado reciente', Torneo.Estado.CANCELADO)
        borrador = self.crear_torneo_organizado('Borrador', Torneo.Estado.BORRADOR)
        proximo = self.crear_torneo_organizado('Próximo', Torneo.Estado.PROXIMAMENTE, fecha_apertura_inscripciones=ahora + timedelta(days=1))
        lejano = self.crear_torneo_organizado('Cierre posterior', fecha_cierre_inscripciones=ahora + timedelta(hours=2))
        cercano = self.crear_torneo_organizado('Cierre próximo', fecha_cierre_inscripciones=ahora + timedelta(minutes=10))
        curso = self.crear_torneo_organizado('En curso', Torneo.Estado.EN_CURSO)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('mis-torneos'))
        filas = respuesta.context['torneos']
        self.assertEqual([torneo.pk for torneo in filas[:5]], [curso.pk, cercano.pk, lejano.pk, proximo.pk, borrador.pk])
        self.assertEqual({torneo.pk for torneo in filas[5:]}, {terminal.pk, cancelado.pk})

    def test_mis_torneos_filtros_tipos_y_vacio(self):
        self.client.force_login(self.usuario)
        url = reverse('mis-torneos')
        self.assertContains(self.client.get(url), 'Todavía no tienes torneos')
        publico = self.crear_torneo_organizado('Público')
        privado = self.crear_torneo_organizado('Privado', tipo=Torneo.Tipo.PRIVADO)
        oficial = self.crear_torneo_organizado('Oficial', Torneo.Estado.FINALIZADO, Torneo.Tipo.OFICIAL)
        cancelado = self.crear_torneo_organizado('Cancelado', Torneo.Estado.CANCELADO)
        self.assertEqual({t.pk for t in self.client.get(url, {'vista': 'activos'}).context['torneos']}, {publico.pk, privado.pk})
        self.assertEqual([t.pk for t in self.client.get(url, {'vista': 'finalizados'}).context['torneos']], [oficial.pk])
        todos = self.client.get(url, {'vista': 'inventado', 'organizador': self.otro.pk})
        self.assertEqual(todos.context['vista'], 'todos')
        self.assertEqual({t.pk for t in todos.context['torneos']}, {publico.pk, privado.pk, oficial.pk, cancelado.pk})

    def test_mis_torneos_cta_reutiliza_gestion_y_publicacion_post(self):
        borrador = self.crear_torneo_organizado('Editar', Torneo.Estado.BORRADOR, Torneo.Tipo.PRIVADO)
        privado = self.crear_torneo_organizado('Accesos', tipo=Torneo.Tipo.PRIVADO)
        publico = self.crear_torneo_organizado('Arbitraje')
        oficial = self.crear_torneo_organizado('Calendario', tipo=Torneo.Tipo.OFICIAL)
        final = self.crear_torneo_organizado('Histórico', Torneo.Estado.FINALIZADO)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('mis-torneos'))
        rutas = {t.pk: t.accion_url for t in respuesta.context['torneos']}
        self.assertEqual(rutas, {borrador.pk: 'editar-borrador-torneo', privado.pk: 'gestionar-privado', publico.pk: 'gestionar-arbitros-torneo', oficial.pk: 'calendario-partidas', final.pk: 'ficha-torneo'})
        self.assertContains(respuesta, f'<form method="post" action="{reverse("publicar-torneo", args=[borrador.pk])}">')
        self.assertContains(respuesta, 'name="csrfmiddlewaretoken"')
        self.assertTemplateUsed(respuesta, 'components/torneo_media.html')
        Usuario.objects.filter(pk=self.usuario.pk).update(estado_cuenta=Usuario.EstadoCuenta.SUSPENDIDA)
        restringida = self.client.get(reverse('mis-torneos'))
        self.assertTrue(all(t.accion_url == 'ficha-torneo' and not t.mostrar_publicar for t in restringida.context['torneos']))

    def test_mis_torneos_conteos_no_consultan_por_fila(self):
        torneo = self.crear_torneo_organizado('Conteo')
        perfil = PerfilVideojuegoUsuario.objects.create(usuario=self.otro, videojuego=self.videojuego, nick_en_juego='Participante')
        InscripcionTorneo.objects.create(torneo=torneo, usuario=self.otro, perfil_videojuego=perfil, nick_historico='Participante')
        otro_usuario = Usuario.objects.create_user(username='cancelado', email='cancelado@example.com', password='test')
        otro_perfil = PerfilVideojuegoUsuario.objects.create(usuario=otro_usuario, videojuego=self.videojuego, nick_en_juego='Cancelado')
        InscripcionTorneo.objects.create(torneo=torneo, usuario=otro_usuario, perfil_videojuego=otro_perfil, nick_historico='Cancelado', estado=InscripcionTorneo.Estado.CANCELADA)
        self.client.force_login(self.usuario)
        self.client.get(reverse('mis-torneos'))
        with CaptureQueriesContext(connection) as inicial:
            respuesta = self.client.get(reverse('mis-torneos'))
        self.assertEqual(respuesta.context['torneos'][0].confirmados, 1)
        for indice in range(5):
            self.crear_torneo_organizado(f'Adicional {indice}')
        with CaptureQueriesContext(connection) as poblado:
            self.client.get(reverse('mis-torneos'))
        self.assertEqual(len(poblado), len(inicial))

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
