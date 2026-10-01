from collections import Counter

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from partidas.models import Partida, ResultadoPartida
from partidas.services import generar_bracket, validar_resultado
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import ClasificacionTorneo, FormatoCompetitivo, InscripcionTorneo, Torneo
from .services import CierreTorneoError, cerrar_torneo, generar_clasificacion_final


class CierreTorneoTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(username='org_cierre', email='org_cierre@example.com', password='ClaveSegura123!')
        self.ajeno = Usuario.objects.create_user(username='ajeno_cierre', email='ajeno_cierre@example.com', password='ClaveSegura123!')
        self.admin = Usuario.objects.create_superuser(username='admin_cierre', email='admin_cierre@example.com', password='ClaveSegura123!')
        self.juego = Videojuego.objects.create(nombre='Juego cierre', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(nombre='Formato cierre', min_participantes=2, max_participantes=128)

    def crear_torneo(self, cantidad, tipo=Torneo.Tipo.PRIVADO):
        torneo = Torneo.objects.create(
            nombre=f'Torneo cierre {cantidad} {tipo}', videojuego=self.juego, organizador=self.organizador,
            tipo=tipo, formato_competitivo=self.formato, max_participantes=cantidad,
            estado=Torneo.Estado.INSCRIPCIONES_CERRADAS,
            fecha_publicacion=timezone.now() if tipo != Torneo.Tipo.PRIVADO else None,
        )
        for indice in range(cantidad):
            usuario = Usuario.objects.create_user(username=f'cierre_{cantidad}_{tipo}_{indice}', email=f'cierre_{cantidad}_{tipo}_{indice}@example.com', password='ClaveSegura123!')
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=self.juego, nick_en_juego=f'Nick cierre {indice}')
            InscripcionTorneo.objects.create(torneo=torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=f'Nick cierre {indice}')
        generar_bracket(torneo)
        torneo.estado = Torneo.Estado.EN_CURSO
        torneo.save(update_fields=('estado',))
        return torneo

    def resolver_torneo(self, torneo):
        for ronda in range(1, torneo.max_participantes.bit_length()):
            for partida in torneo.partidas.filter(numero_ronda=ronda).order_by('numero_orden'):
                partida.refresh_from_db()
                partida.estado = Partida.Estado.EN_CURSO
                partida.save(update_fields=('estado',))
                ganador = partida.participantes.get(posicion=1).inscripcion
                validar_resultado(partida, self.organizador, '2-1', ganador)

    def test_clasificacion_compartida_para_tamanos_validos(self):
        esperadas = {
            2: Counter({1: 1, 2: 1}),
            4: Counter({1: 1, 2: 1, 3: 2}),
            8: Counter({1: 1, 2: 1, 3: 2, 5: 4}),
            16: Counter({1: 1, 2: 1, 3: 2, 5: 4, 9: 8}),
        }
        for cantidad, posiciones in esperadas.items():
            torneo = self.crear_torneo(cantidad)
            self.resolver_torneo(torneo)
            clasificacion = cerrar_torneo(torneo, self.organizador)
            self.assertEqual(Counter(fila.posicion for fila in clasificacion), posiciones)
            self.assertEqual(sum(fila.es_campeon for fila in clasificacion), 1)
            self.assertEqual(torneo.clasificaciones.count(), cantidad)

    def test_rechaza_cierre_pendiente_y_es_idempotente(self):
        torneo = self.crear_torneo(4)
        with self.assertRaises(CierreTorneoError):
            cerrar_torneo(torneo, self.organizador)
        self.assertEqual(torneo.clasificaciones.count(), 0)
        self.resolver_torneo(torneo)
        cerrar_torneo(torneo, self.organizador)
        torneo.refresh_from_db()
        fecha_fin = torneo.fecha_fin_real
        historial = torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.FINALIZADO).count()
        cerrar_torneo(torneo, self.organizador)
        torneo.refresh_from_db()
        self.assertEqual(torneo.fecha_fin_real, fecha_fin)
        self.assertEqual(torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.FINALIZADO).count(), historial)

    def test_rechaza_final_sin_campeon_y_no_crea_clasificacion(self):
        torneo = self.crear_torneo(2)
        final = torneo.partidas.get()
        validar_resultado(final, self.organizador, '', tipo_resultado=ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA)
        with self.assertRaises(CierreTorneoError):
            cerrar_torneo(torneo, self.organizador)
        self.assertEqual(ClasificacionTorneo.objects.filter(torneo=torneo).count(), 0)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.EN_CURSO)

    def test_publico_cierra_al_validar_la_final(self):
        torneo = self.crear_torneo(2, Torneo.Tipo.PUBLICO)
        final = torneo.partidas.get()
        final.estado = Partida.Estado.EN_CURSO
        final.save(update_fields=('estado',))
        ganador = final.participantes.get(posicion=1).inscripcion
        validar_resultado(final, self.organizador, '2-1', ganador)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.FINALIZADO)
        self.assertTrue(torneo.fecha_fin_real)
        self.assertEqual(torneo.clasificaciones.count(), 2)
        self.assertEqual(torneo.historial_estados.filter(estado_nuevo=Torneo.Estado.FINALIZADO).count(), 1)

    def test_oficial_exige_admin_y_privado_organizador(self):
        oficial = self.crear_torneo(2, Torneo.Tipo.OFICIAL)
        self.resolver_torneo(oficial)
        with self.assertRaises(CierreTorneoError):
            cerrar_torneo(oficial, self.organizador)
        cerrar_torneo(oficial, self.admin)
        privado = self.crear_torneo(2)
        self.resolver_torneo(privado)
        with self.assertRaises(CierreTorneoError):
            cerrar_torneo(privado, self.ajeno)

    def test_post_cierre_privado_requiere_autorizacion_y_no_acepta_get(self):
        torneo = self.crear_torneo(2)
        self.resolver_torneo(torneo)
        url = reverse('cerrar-torneo', args=[torneo.pk])
        self.client.force_login(self.ajeno)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.EN_CURSO)
        self.client.force_login(self.organizador)
        self.client.post(url)
        torneo.refresh_from_db()
        self.assertEqual(torneo.estado, Torneo.Estado.FINALIZADO)

    def test_clasificacion_parcial_no_se_sobrescribe(self):
        torneo = self.crear_torneo(2)
        self.resolver_torneo(torneo)
        participante = torneo.inscripciones.first()
        ClasificacionTorneo.objects.create(torneo=torneo, inscripcion=participante, posicion=1, es_campeon=True)
        with self.assertRaises(CierreTorneoError):
            generar_clasificacion_final(torneo)
