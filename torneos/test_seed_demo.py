from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import F
from django.test import TestCase, override_settings

from usuarios.models import Usuario
from torneos.models import Torneo
from partidas.models import Partida
from notificaciones.models import Notificacion
from arbitraje.models import ArbitroTorneo, HistorialAsignacionArbitro
from moderacion.models import Denuncia
from usuarios.models import HistorialXP, HistorialKarma
from usuarios.services import nivel_para_xp
from torneos.models import FormatoCompetitivo
from videojuegos.models import Plataforma, RangoVideojuego, Videojuego


@override_settings(DEBUG=True, DEMO_MODE=True, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SeedDemoTests(TestCase):
    def run_seed(self):
        output = StringIO()
        call_command('seed_demo', stdout=output)
        return output.getvalue()

    def test_requires_demo_mode_and_debug(self):
        for overrides in ({'DEMO_MODE': False}, {'DEBUG': False}):
            with self.settings(**overrides), self.assertRaises(CommandError):
                self.run_seed()
        self.assertFalse(Usuario.objects.exists())

    def test_idempotent_and_preserves_non_demo_user(self):
        outsider = Usuario.objects.create_user(username='real_player', email='real@example.invalid', password='unchanged')
        password = outsider.password
        game = Videojuego.objects.create(nombre='Archivo Arena', genero='Genero existente', instrucciones_competicion='Reglas existentes')
        platform = Plataforma.objects.create(nombre='Plataforma existente')
        game.plataformas.add(platform)
        rank = RangoVideojuego.objects.create(videojuego=game, nombre='Rango existente', posicion=1)
        with self.captureOnCommitCallbacks(execute=True):
            output = self.run_seed()
        models = [model for model in apps.get_models() if model._meta.app_label in ('usuarios', 'torneos', 'partidas', 'videojuegos', 'arbitraje', 'moderacion', 'notificaciones')] + [Group]
        counts = {model: model.objects.count() for model in models}
        self.assertIn('encontrados', self.run_seed())
        self.assertEqual({model: model.objects.count() for model in models}, counts)
        self.assertEqual(Torneo.objects.count(), 8)
        self.assertEqual(Torneo.objects.filter(estado=Torneo.Estado.FINALIZADO).count(), 2)
        self.assertTrue(HistorialXP.objects.exists())
        self.assertTrue(HistorialKarma.objects.exists())
        self.assertTrue(HistorialKarma.objects.filter(tipo=HistorialKarma.Tipo.ARBITRAJE_COMPLETADO).exists())
        self.assertTrue(HistorialKarma.objects.filter(tipo=HistorialKarma.Tipo.DECLARACION_VERAZ).exists())
        self.assertFalse(HistorialAsignacionArbitro.objects.filter(
            partida__resultado_oficial__isnull=False, fecha__gt=F('partida__resultado_oficial__fecha_validacion'),
        ).exists())
        self.assertTrue(Partida.objects.filter(estado=Partida.Estado.EN_CURSO).exists())
        self.assertTrue(Partida.objects.filter(estado=Partida.Estado.PENDIENTE_VALIDACION).exists())
        self.assertTrue(Partida.objects.filter(estado=Partida.Estado.CHECK_IN, checkins__confirmado=True).exists())
        self.assertTrue(Partida.objects.filter(estado=Partida.Estado.PROGRAMADA).exists())
        self.assertTrue(ArbitroTorneo.objects.filter(estado_invitacion=ArbitroTorneo.EstadoInvitacion.PENDIENTE).exists())
        self.assertTrue(Denuncia.objects.filter(hubo_sancion=True).exists())
        self.assertTrue(Notificacion.objects.filter(leida=True).exists())
        self.assertTrue(Notificacion.objects.filter(leida=False).exists())
        self.assertIn('DEMO-RankedKingdom-2026!', output)
        for user in Usuario.objects.exclude(pk=outsider.pk):
            self.assertEqual(user.nivel, nivel_para_xp(user.xp_total))
            self.assertTrue(user.check_password('DEMO-RankedKingdom-2026!'))
        self.assertFalse(ArbitroTorneo.objects.filter(usuario__username='demo_TruePulse', partidas_asignadas__isnull=False).exists())
        outsider.refresh_from_db()
        self.assertEqual(outsider.password, password)
        game.refresh_from_db()
        self.assertEqual(game.genero, 'Genero existente')
        self.assertEqual(game.instrucciones_competicion, 'Reglas existentes')
        self.assertEqual(list(game.plataformas.all()), [platform])
        self.assertEqual(list(game.rangos.all()), [rank])

    def test_collision_does_not_adopt_or_modify_user(self):
        user = Usuario.objects.create_user(username='demo_KingdomAdmin', email='owner@example.invalid', password='unchanged')
        with self.assertRaises(CommandError):
            self.run_seed()
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)
        self.assertEqual(Usuario.objects.count(), 1)
        self.assertFalse(Group.objects.exists())

    def test_domain_failure_rolls_back_seed(self):
        with patch('torneos.management.commands.seed_demo.Command._moderation', side_effect=CommandError('Fallo de prueba')):
            with self.assertRaises(CommandError):
                self.run_seed()
        for model in (Usuario, Group, Torneo, Videojuego, Partida, Notificacion, HistorialKarma, HistorialXP):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_foreign_tournament_is_not_processed_or_deleted(self):
        user = Usuario.objects.create_user(username='owner', email='owner@example.invalid')
        game = Videojuego.objects.create(nombre='Juego existente', genero='Competitivo')
        competitive_format = FormatoCompetitivo.objects.create(nombre='Formato existente')
        tournament = Torneo.objects.create(nombre='Ajeno', organizador=user, videojuego=game, formato_competitivo=competitive_format, tipo=Torneo.Tipo.PUBLICO, max_participantes=8)
        with self.assertRaises(CommandError):
            self.run_seed()
        tournament.refresh_from_db()
        self.assertEqual(tournament.estado, Torneo.Estado.BORRADOR)
        self.assertEqual(Usuario.objects.count(), 1)
        self.assertEqual(Torneo.objects.count(), 1)
        self.assertFalse(Group.objects.exists())