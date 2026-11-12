"""Result/branch integrity. Multiprocess tests require real PostgreSQL."""
import multiprocessing
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from notificaciones.models import Notificacion
from torneos.models import FormatoCompetitivo, InscripcionTorneo, Torneo
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import DeclaracionResultado, HistorialResultadoPartida, Partida, ResultadoPartida
from .scheduling import CheckInError, reprogramar_partida
from .services import ResultadoError, declarar_resultado, generar_bracket, validar_resultado


class ResultFixture:
    def setUp(self):
        super().setUp()
        self.organizer = Usuario.objects.create_user(username='result_org', email='result_org@example.invalid')
        self.outsider = Usuario.objects.create_user(username='result_out', email='result_out@example.invalid')
        game = Videojuego.objects.create(nombre='Result integrity', genero='Competitivo')
        format = FormatoCompetitivo.objects.create(nombre='Result integrity', min_participantes=2, max_participantes=128)
        self.tournament = Torneo.objects.create(
            nombre='Result integrity', videojuego=game, organizador=self.organizer,
            formato_competitivo=format, max_participantes=4, tipo=Torneo.Tipo.PRIVADO,
            estado=Torneo.Estado.INSCRIPCIONES_CERRADAS,
        )
        for i in range(4):
            player = Usuario.objects.create_user(username=f'result_player_{i}', email=f'result_player_{i}@example.invalid')
            profile = PerfilVideojuegoUsuario.objects.create(usuario=player, videojuego=game, nick_en_juego=player.username)
            InscripcionTorneo.objects.create(torneo=self.tournament, usuario=player, perfil_videojuego=profile, nick_historico=player.username)
        generar_bracket(self.tournament)
        Torneo.objects.filter(pk=self.tournament.pk).update(estado=Torneo.Estado.EN_CURSO)
        self.tournament.refresh_from_db()
        Partida.objects.filter(torneo=self.tournament, numero_ronda=1).update(estado=Partida.Estado.EN_CURSO)
        self.first, self.second = list(self.tournament.partidas.filter(numero_ronda=1).order_by('pk'))
        self.final = self.first.siguiente_partida
        self.winner = self.first.participantes.order_by('posicion').first().inscripcion
        self.rival = self.first.participantes.order_by('posicion').last().inscripcion

    def assert_official(self, match, winner):
        match.refresh_from_db()
        self.assertEqual(match.estado, Partida.Estado.FINALIZADA)
        self.assertIsNotNone(match.fecha_hora_fin_real)
        official = ResultadoPartida.objects.get(partida=match)
        self.assertEqual(official.ganador_id, winner.pk)
        self.assertEqual(HistorialResultadoPartida.objects.filter(partida=match).count(), 1)
        self.assertEqual(Notificacion.objects.filter(partida=match, tipo=Notificacion.Tipo.RESULTADO_OFICIAL).count(), 2)
        return official


class ResultIntegrityTests(ResultFixture, TestCase):
    def test_one_declaration_per_player_and_no_implicit_official_result(self):
        declarar_resultado(self.first, self.winner.usuario, '2-1', self.winner)
        with self.assertRaises(ResultadoError):
            declarar_resultado(self.first, self.winner.usuario, '1-2', self.rival)
        declarar_resultado(self.first, self.rival.usuario, '1-2', self.rival)
        self.assertEqual(DeclaracionResultado.objects.filter(partida=self.first).count(), 2)
        self.assertFalse(ResultadoPartida.objects.exists())
        self.assertFalse(self.final.participantes.exists())

    def test_database_rejects_duplicate_declaration_even_outside_service(self):
        values = dict(partida=self.first, usuario=self.winner.usuario, resultado_declarado='2-1', ganador_declarado=self.winner)
        DeclaracionResultado.objects.create(**values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DeclaracionResultado.objects.create(**values)
        self.assertEqual(DeclaracionResultado.objects.count(), 1)

    def test_repeated_validation_cannot_change_result_or_duplicate_side_effects(self):
        validar_resultado(self.first, self.organizer, '2-1', self.winner)
        official = self.assert_official(self.first, self.winner)
        with self.assertRaises(ResultadoError):
            validar_resultado(self.first, self.organizer, '1-2', self.rival)
        self.assertEqual(self.assert_official(self.first, self.winner).pk, official.pk)
        self.assertEqual(self.final.participantes.count(), 1)

    def test_current_account_eligibility_is_rechecked(self):
        Usuario.objects.filter(pk=self.winner.usuario_id).update(estado_cuenta=Usuario.EstadoCuenta.SUSPENDIDA)
        with self.assertRaises(ResultadoError):
            declarar_resultado(self.first, self.winner.usuario, '2-1', self.winner)
        Usuario.objects.filter(pk=self.organizer.pk).update(estado_cuenta=Usuario.EstadoCuenta.SUSPENDIDA)
        with self.assertRaises(ResultadoError):
            validar_resultado(self.first, self.organizer, '2-1', self.winner)
        self.assertFalse(ResultadoPartida.objects.exists())

    def test_outsider_still_cannot_declare_or_validate(self):
        with self.assertRaises(ResultadoError):
            declarar_resultado(self.first, self.outsider, '2-1', self.winner)
        with self.assertRaises(ResultadoError):
            validar_resultado(self.first, self.outsider, '2-1', self.winner)
        self.assertFalse(DeclaracionResultado.objects.exists())
        self.assertFalse(ResultadoPartida.objects.exists())

    def test_history_failure_rolls_back_result_state_progress_and_notifications(self):
        with patch('partidas.services._crear_historial', side_effect=RuntimeError('synthetic history failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic history failure'):
                validar_resultado(self.first, self.organizer, '2-1', self.winner)
        self.first.refresh_from_db()
        self.assertEqual(self.first.estado, Partida.Estado.EN_CURSO)
        self.assertFalse(ResultadoPartida.objects.exists())
        self.assertFalse(self.final.participantes.exists())
        self.assertFalse(Notificacion.objects.filter(tipo=Notificacion.Tipo.RESULTADO_OFICIAL).exists())

    def test_second_notification_failure_rolls_back_whole_validation(self):
        from notificaciones.services import crear_notificacion
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('synthetic notification failure')
            return crear_notificacion(*args, **kwargs)
        with patch('notificaciones.services.crear_notificacion', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'synthetic notification failure'):
                validar_resultado(self.first, self.organizer, '2-1', self.winner)
        self.first.refresh_from_db()
        self.assertEqual(self.first.estado, Partida.Estado.EN_CURSO)
        self.assertFalse(ResultadoPartida.objects.exists())
        self.assertFalse(HistorialResultadoPartida.objects.exists())
        self.assertFalse(self.final.participantes.exists())
        self.assertFalse(Notificacion.objects.filter(tipo=Notificacion.Tipo.RESULTADO_OFICIAL).exists())
        validar_resultado(self.first, self.organizer, '2-1', self.winner)
        self.assert_official(self.first, self.winner)


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL and independent worker processes')
@skipUnless('fork' in multiprocessing.get_all_start_methods(), 'Multiprocess test requires fork (Linux CI)')
class ResultPostgreSQLTests(ResultFixture, TransactionTestCase):
    def workers(self, jobs):
        # fork is explicit: each worker has its own Python locks and DB session.
        context = multiprocessing.get_context('fork')
        barrier = context.Barrier(2, timeout=10)
        queue = context.Queue()
        connections.close_all()
        def worker(job):
            db = connections['default']
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout='10s'")
                    cursor.execute("SET statement_timeout='20s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                reached_lock = False
                def synchronize(execute, sql, params, many, context):
                    nonlocal reached_lock
                    if not reached_lock and 'FOR NO KEY UPDATE' in sql and 'torneos_torneo' in sql:
                        reached_lock = True
                        barrier.wait()
                    return execute(sql, params, many, context)
                with db.execute_wrapper(synchronize):
                    try:
                        value = job()
                        outcome = ('ok', getattr(value, 'pk', None))
                    except (ResultadoError, CheckInError) as error:
                        outcome = ('business', str(error))
                queue.put((pid, reached_lock, outcome))
            except BaseException as error:
                queue.put((None, False, ('error', type(error).__name__, str(error))))
            finally:
                db.close()
        processes = [context.Process(target=worker, args=(job,)) for job in jobs]
        try:
            for process in processes:
                process.start()
            results = [queue.get(timeout=30) for _ in processes]
        finally:
            for process in processes:
                if process.pid is not None:
                    process.join(3)
                    if process.is_alive():
                        process.terminate()
                        process.join(3)
        self.assertEqual(len({r[0] for r in results}), 2, results)
        self.assertTrue(all(r[1] for r in results), results)
        self.assertFalse(any(r[2][0] == 'error' for r in results), results)
        return [r[2][0] for r in results]

    def test_same_player_simultaneous_declarations_only_one_is_accepted(self):
        job = lambda: declarar_resultado(self.first, self.winner.usuario, '2-1', self.winner)
        self.assertCountEqual(self.workers([job, job]), ['ok', 'business'])
        self.assertEqual(DeclaracionResultado.objects.filter(partida=self.first, usuario_id=self.winner.usuario_id).count(), 1)

    def test_different_players_can_both_declare(self):
        jobs = [lambda: declarar_resultado(self.first, self.winner.usuario, '2-1', self.winner),
                lambda: declarar_resultado(self.first, self.rival.usuario, '1-2', self.rival)]
        self.assertEqual(self.workers(jobs), ['ok', 'ok'])
        self.assertEqual(DeclaracionResultado.objects.count(), 2)
        self.assertFalse(ResultadoPartida.objects.exists())

    def test_contradictory_official_decisions_commit_only_once(self):
        jobs = [lambda: validar_resultado(self.first, self.organizer, '2-1', self.winner),
                lambda: validar_resultado(self.first, self.organizer, '1-2', self.rival)]
        self.assertCountEqual(self.workers(jobs), ['ok', 'business'])
        winner = ResultadoPartida.objects.get(partida=self.first).ganador
        self.assert_official(self.first, winner)
        self.assertEqual(self.final.participantes.count(), 1)
        self.assertEqual(self.final.participantes.get().inscripcion_id, winner.pk)

    def test_sibling_winners_confirm_both_rivals_exactly_once(self):
        other = self.second.participantes.first().inscripcion
        jobs = [lambda: validar_resultado(self.first, self.organizer, '2-1', self.winner),
                lambda: validar_resultado(self.second, self.organizer, '2-1', other)]
        self.assertEqual(self.workers(jobs), ['ok', 'ok'])
        self.final.refresh_from_db()
        self.assertIsNotNone(self.final.fecha_hora_rivales_confirmados)
        self.assertEqual(set(self.final.participantes.values_list('inscripcion_id', flat=True)), {self.winner.pk, other.pk})
        self.assertFalse(ResultadoPartida.objects.filter(partida=self.final).exists())
        self.assert_official(self.first, self.winner)
        self.assert_official(self.second, other)

    def test_empty_sibling_branches_resolve_parent_once(self):
        jobs = [lambda: validar_resultado(self.first, self.organizer, '', tipo_resultado=ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA),
                lambda: validar_resultado(self.second, self.organizer, '', tipo_resultado=ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA)]
        self.assertEqual(self.workers(jobs), ['ok', 'ok'])
        self.final.refresh_from_db()
        self.assertEqual(self.final.estado, Partida.Estado.FINALIZADA)
        result = ResultadoPartida.objects.get(partida=self.final)
        self.assertEqual(result.tipo_resultado, ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA)
        self.assertIsNone(result.ganador_id)
        self.assertEqual(HistorialResultadoPartida.objects.filter(partida=self.final).count(), 1)

    def test_declaration_cannot_reopen_concurrently_finalized_match(self):
        jobs = [lambda: declarar_resultado(self.first, self.winner.usuario, '2-1', self.winner),
                lambda: validar_resultado(self.first, self.organizer, '2-1', self.winner)]
        outcomes = self.workers(jobs)
        self.assertIn('ok', outcomes)
        self.assertLessEqual(DeclaracionResultado.objects.count(), 1)
        self.assert_official(self.first, self.winner)

    def test_reprogramming_cannot_reopen_concurrently_finalized_match(self):
        Torneo.objects.filter(pk=self.tournament.pk).update(tipo=Torneo.Tipo.OFICIAL)
        Partida.objects.filter(pk=self.first.pk).update(estado=Partida.Estado.CHECK_IN)
        jobs = [lambda: reprogramar_partida(self.first, timezone.now() + timedelta(hours=1), self.organizer),
                lambda: validar_resultado(self.first, self.organizer, '', self.winner, tipo_resultado=ResultadoPartida.TipoResultado.INCOMPARECENCIA)]
        self.assertIn('ok', self.workers(jobs))
        self.assert_official(self.first, self.winner)


class DeclarationMigrationTests(ResultFixture, TransactionTestCase):
    """Exercise the real schema migration with synthetic historical rows only."""
    before = ('partidas', '0005_lobby_y_checkins_invalidados')
    after = ('partidas', '0006_declaracion_unica_por_usuario')

    def setUp(self):
        super().setUp()
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        executor.migrate([self.before])

    def tearDown(self):
        from django.db.migrations.executor import MigrationExecutor
        try:
            # Only this test's synthetic declarations; restore the latest schema
            # even when the duplicate-preflight assertion raises.
            DeclaracionResultado.objects.filter(partida__torneo=self.tournament).delete()
            MigrationExecutor(connection).migrate(self.latest)
        finally:
            super().tearDown()

    def declaration(self):
        return DeclaracionResultado.objects.create(
            partida=self.first, usuario=self.winner.usuario,
            resultado_declarado='2-1', ganador_declarado=self.winner,
        )

    def test_migration_refuses_duplicates_without_deleting_or_picking_a_winner(self):
        from django.db.migrations.executor import MigrationExecutor
        from django.db.migrations.recorder import MigrationRecorder
        self.declaration()
        self.declaration()
        rows = list(DeclaracionResultado.objects.order_by('pk').values())
        with self.assertRaisesRegex(RuntimeError, 'declaraciones duplicadas'):
            MigrationExecutor(connection).migrate([self.after])
        self.assertEqual(rows, list(DeclaracionResultado.objects.order_by('pk').values()))
        self.assertFalse(MigrationRecorder(connection).migration_qs.filter(app='partidas', name=self.after[1]).exists())

    def test_migration_preserves_unique_historical_declaration_and_enforces_constraint(self):
        from django.db.migrations.executor import MigrationExecutor
        self.declaration()
        rows = list(DeclaracionResultado.objects.order_by('pk').values())
        MigrationExecutor(connection).migrate([self.after])
        self.assertEqual(rows, list(DeclaracionResultado.objects.order_by('pk').values()))
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.declaration()
