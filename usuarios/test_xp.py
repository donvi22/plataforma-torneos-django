from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from partidas.models import Partida
from partidas.services import generar_bracket, validar_resultado
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from torneos.services import cerrar_torneo, generar_clasificacion_final
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import HistorialXP, Usuario
from .services import XPError, calcular_xp_clasificacion, conceder_xp_torneo, nivel_para_xp, progreso_nivel, umbral_nivel, xp_base_por_posicion


class XPServicesTests(TestCase):
    def setUp(self):
        self.organizador = Usuario.objects.create_user(username='org_xp', email='org_xp@example.com', password='ClaveSegura123!')
        self.admin = Usuario.objects.create_superuser(username='admin_xp', email='admin_xp@example.com', password='ClaveSegura123!')
        self.juego = Videojuego.objects.create(nombre='Juego XP', genero='Competitivo')
        self.formato = FormatoCompetitivo.objects.create(nombre='Formato XP', min_participantes=2, max_participantes=128)

    def crear_finalizado(self, cantidad=4, tipo=Torneo.Tipo.PUBLICO, especial=None, dificultad=None):
        torneo = Torneo.objects.create(
            nombre=f'Torneo XP {cantidad} {tipo}', videojuego=self.juego, organizador=self.organizador,
            tipo=tipo, formato_competitivo=self.formato, max_participantes=cantidad,
            estado=Torneo.Estado.INSCRIPCIONES_CERRADAS, fecha_publicacion=timezone.now(),
            modo_xp=Torneo.ModoXP.ESPECIAL if especial is not None else Torneo.ModoXP.AUTOMATICA,
            multiplicador_xp_especial=especial, coeficiente_dificultad=dificultad,
        )
        for indice in range(cantidad):
            usuario = Usuario.objects.create_user(username=f'xp_{cantidad}_{tipo}_{indice}', email=f'xp_{cantidad}_{tipo}_{indice}@example.com', password='ClaveSegura123!')
            perfil = PerfilVideojuegoUsuario.objects.create(usuario=usuario, videojuego=self.juego, nick_en_juego=usuario.username)
            InscripcionTorneo.objects.create(torneo=torneo, usuario=usuario, perfil_videojuego=perfil, nick_historico=usuario.username)
        generar_bracket(torneo)
        torneo.estado = Torneo.Estado.EN_CURSO
        torneo.save(update_fields=('estado',))
        for ronda in range(1, cantidad.bit_length()):
            for partida in torneo.partidas.filter(numero_ronda=ronda).order_by('numero_orden'):
                partida.estado = Partida.Estado.EN_CURSO
                partida.save(update_fields=('estado',))
                validar_resultado(partida, self.organizador, '2-1', partida.participantes.get(posicion=1).inscripcion)
        torneo.refresh_from_db()
        if torneo.estado != Torneo.Estado.FINALIZADO:
            actor = self.admin if tipo == Torneo.Tipo.OFICIAL else self.organizador
            cerrar_torneo(torneo, actor, automatico=tipo == Torneo.Tipo.PUBLICO)
        torneo.refresh_from_db()
        return torneo

    def test_bases_y_niveles_siguen_la_curva_documentada(self):
        self.assertEqual([xp_base_por_posicion(posicion) for posicion in (1, 2, 3, 4, 5, 8, 9, 16, 17)], [100, 70, 50, 50, 20, 20, 10, 10, 5])
        self.assertEqual([umbral_nivel(nivel) for nivel in range(1, 6)], [0, 100, 400, 900, 1600])
        self.assertEqual([nivel_para_xp(xp) for xp in (0, 99, 100, 399, 400, 899, 900, 1600)], [1, 1, 2, 2, 3, 3, 4, 5])
        self.assertEqual(progreso_nivel(150), {'nivel': 2, 'xp_en_nivel': 50, 'xp_para_siguiente': 300, 'xp_restante': 250})

    def test_recompensas_compartidas_factor_tamano_dificultad_y_especial(self):
        torneo = self.crear_finalizado(4)
        clasificaciones = list(torneo.clasificaciones.order_by('posicion'))
        terceros = [fila for fila in clasificaciones if fila.posicion == 3]
        self.assertEqual(calcular_xp_clasificacion(terceros[0])[0], calcular_xp_clasificacion(terceros[1])[0])
        self.assertEqual(calcular_xp_clasificacion(clasificaciones[0])[0], 120)
        oficial = self.crear_finalizado(2, Torneo.Tipo.OFICIAL, especial=Decimal('1.50'), dificultad=Decimal('1.200'))
        campeon = oficial.clasificaciones.get(es_campeon=True)
        self.assertEqual(calcular_xp_clasificacion(campeon)[0], 198)

    def test_privado_y_configuracion_especial_invalida_no_conceden_xp(self):
        privado = self.crear_finalizado(2, Torneo.Tipo.PRIVADO)
        with self.assertRaises(XPError):
            conceder_xp_torneo(privado)
        publico = self.crear_finalizado(2)
        publico.modo_xp = Torneo.ModoXP.ESPECIAL
        publico.multiplicador_xp_especial = Decimal('2.00')
        publico.save(update_fields=('modo_xp', 'multiplicador_xp_especial'))
        with self.assertRaises(XPError):
            calcular_xp_clasificacion(publico.clasificaciones.get(es_campeon=True))

    def test_historial_actualiza_xp_nivel_y_es_idempotente(self):
        torneo = self.crear_finalizado(2)
        movimientos = list(HistorialXP.objects.filter(torneo=torneo).select_related('usuario'))
        self.assertEqual(len(movimientos), 2)
        for movimiento in movimientos:
            movimiento.usuario.refresh_from_db()
            self.assertEqual(movimiento.usuario.xp_total, movimiento.xp_posterior)
            self.assertEqual(movimiento.usuario.nivel, movimiento.nivel_posterior)
            self.assertEqual(movimiento.xp_anterior, 0)
        antes = [(movimiento.usuario_id, movimiento.xp_posterior) for movimiento in movimientos]
        conceder_xp_torneo(torneo)
        self.assertEqual(HistorialXP.objects.filter(torneo=torneo).count(), 2)
        self.assertEqual(antes, [(movimiento.usuario_id, movimiento.xp_posterior) for movimiento in HistorialXP.objects.filter(torneo=torneo)])

    def test_concesion_parcial_completa_solo_filas_faltantes(self):
        torneo = self.crear_finalizado(2, Torneo.Tipo.PRIVADO)
        torneo.tipo = Torneo.Tipo.PUBLICO
        torneo.save(update_fields=('tipo',))
        clasificacion = torneo.clasificaciones.order_by('pk').first()
        usuario = clasificacion.inscripcion.usuario
        xp, dificultad, multiplicador = calcular_xp_clasificacion(clasificacion)
        HistorialXP.objects.create(
            usuario=usuario, torneo=torneo, inscripcion=clasificacion.inscripcion, clasificacion=clasificacion,
            xp_concedida=xp, xp_anterior=0, xp_posterior=xp, nivel_anterior=1,
            nivel_posterior=nivel_para_xp(xp), posicion_final=clasificacion.posicion,
            tamano_torneo=torneo.max_participantes, coeficiente_dificultad=dificultad,
            multiplicador_especial=multiplicador, motivo='Recuperación comprobada.',
        )
        usuario.xp_total = xp
        usuario.nivel = nivel_para_xp(xp)
        usuario.save(update_fields=('xp_total', 'nivel'))
        conceder_xp_torneo(torneo)
        self.assertEqual(HistorialXP.objects.filter(torneo=torneo).count(), 2)

    def test_comando_recompensa_torneo_finalizado_y_perfil_privado(self):
        torneo = self.crear_finalizado(2, Torneo.Tipo.PRIVADO)
        torneo.tipo = Torneo.Tipo.PUBLICO
        torneo.save(update_fields=('tipo',))
        call_command('conceder_xp_torneo', torneo=torneo.pk)
        campeon = torneo.clasificaciones.get(es_campeon=True).inscripcion.usuario
        self.client.force_login(campeon)
        respuesta = self.client.get(reverse('perfil', args=[campeon.pk]))
        self.assertContains(respuesta, 'Progresión')
        self.assertContains(respuesta, 'Ver historial de XP')
        self.assertContains(self.client.get(reverse('historial-xp')), torneo.nombre)
        self.client.force_login(self.organizador)
        self.assertNotContains(self.client.get(reverse('perfil', args=[campeon.pk])), 'Ver historial de XP')
