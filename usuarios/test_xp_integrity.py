"""XP integrity; concurrent cases require a real PostgreSQL test database."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from torneos.models import ClasificacionTorneo, FormatoCompetitivo, InscripcionTorneo, Torneo
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import HistorialXP, Usuario
from .services import XPError, conceder_xp_torneo, nivel_para_xp


class XPFixture:
    def setUp(self):
        super().setUp()
        self.players = [Usuario.objects.create_user(
            username=f'xp_integrity_{i}', email=f'xp_integrity_{i}@example.invalid',
            xp_total=90, nivel=1,
        ) for i in range(2)]
        self.game = Videojuego.objects.create(nombre='XP integrity', genero='Competitivo')
        self.format = FormatoCompetitivo.objects.create(nombre='XP integrity', min_participantes=2, max_participantes=128)
        self.profiles = {p.pk: PerfilVideojuegoUsuario.objects.create(
            usuario=p, videojuego=self.game, nick_en_juego=p.username,
        ) for p in self.players}

    def tournament(self, reverse=False):
        tournament = Torneo.objects.create(
            nombre='XP integrity', videojuego=self.game, organizador=self.players[0],
            formato_competitivo=self.format, max_participantes=2,
            tipo=Torneo.Tipo.PUBLICO, estado=Torneo.Estado.FINALIZADO,
        )
        for index, player in enumerate(reversed(self.players) if reverse else self.players):
            registration = InscripcionTorneo.objects.create(
                torneo=tournament, usuario=player, perfil_videojuego=self.profiles[player.pk],
                nick_historico=player.username,
            )
            ClasificacionTorneo.objects.create(
                torneo=tournament, inscripcion=registration, posicion=index + 1,
                es_campeon=index == 0,
            )
        return tournament

    def assert_chain(self, player, initial=90):
        balance = initial
        for entry in HistorialXP.objects.filter(usuario=player).order_by('xp_anterior'):
            self.assertEqual(entry.xp_anterior, balance)
            self.assertEqual(entry.nivel_anterior, nivel_para_xp(balance))
            balance += entry.xp_concedida
            self.assertEqual(entry.xp_posterior, balance)
            self.assertEqual(entry.nivel_posterior, nivel_para_xp(balance))
        player.refresh_from_db()
        self.assertEqual(player.xp_total, balance)
        self.assertEqual(player.nivel, nivel_para_xp(balance))


class XPIntegrityTests(XPFixture, TestCase):
    def test_normal_award_preserves_amounts_and_level_thresholds(self):
        tournament = self.tournament()
        entries = conceder_xp_torneo(tournament)
        self.assertEqual([e.xp_concedida for e in entries], [110, 77])
        for player in self.players:
            self.assert_chain(player)

    def test_distinct_awards_append_a_consistent_chain_from_existing_balance(self):
        Usuario.objects.filter(pk=self.players[0].pk).update(xp_total=350, nivel=2)
        conceder_xp_torneo(self.tournament())
        conceder_xp_torneo(self.tournament(reverse=True))
        self.assert_chain(self.players[0], initial=350)
        self.assert_chain(self.players[1])
        self.assertEqual(self.players[0].nivel, 3)

    def test_repeated_award_returns_same_entries_without_increment(self):
        tournament = self.tournament()
        first = conceder_xp_torneo(tournament)
        second = conceder_xp_torneo(tournament)
        self.assertEqual([e.pk for e in first], [e.pk for e in second])
        self.assertEqual(HistorialXP.objects.count(), 2)
        for player in self.players:
            self.assert_chain(player)

    def test_database_uniqueness_still_rejects_duplicate_reward(self):
        entry = conceder_xp_torneo(self.tournament())[0]
        values = {f.attname: getattr(entry, f.attname) for f in HistorialXP._meta.fields if not f.primary_key and f.name != 'fecha'}
        with self.assertRaises(IntegrityError), transaction.atomic():
            HistorialXP.objects.create(**values)
        self.assertEqual(HistorialXP.objects.count(), 2)

    def test_late_history_failure_rolls_back_all_participants(self):
        tournament = self.tournament()
        create = HistorialXP.objects.create
        def fail_second(**values):
            if values['usuario'].pk == self.players[1].pk:
                raise IntegrityError('synthetic history failure')
            return create(**values)
        with patch.object(HistorialXP.objects, 'create', side_effect=fail_second):
            with self.assertRaises(XPError):
                conceder_xp_torneo(tournament)
        self.assertFalse(HistorialXP.objects.exists())
        for player in self.players:
            self.assert_chain(player)

    def test_balance_write_failure_rolls_back_history_and_previous_participant(self):
        tournament = self.tournament()
        save = Usuario.save
        def fail_second(player, *args, **kwargs):
            if player.pk == self.players[1].pk and 'xp_total' in kwargs.get('update_fields', ()):
                raise RuntimeError('synthetic balance failure')
            return save(player, *args, **kwargs)
        with patch.object(Usuario, 'save', autospec=True, side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'synthetic balance failure'):
                conceder_xp_torneo(tournament)
        self.assertFalse(HistorialXP.objects.exists())
        for player in self.players:
            self.assert_chain(player)

    def test_history_linked_to_wrong_user_is_rejected_without_new_awards(self):
        tournament = self.tournament()
        entries = conceder_xp_torneo(tournament)
        HistorialXP.objects.filter(pk=entries[0].pk).update(usuario=self.players[1])
        balances = list(Usuario.objects.order_by('pk').values_list('xp_total', 'nivel'))
        with self.assertRaises(XPError):
            conceder_xp_torneo(tournament)
        self.assertEqual(balances, list(Usuario.objects.order_by('pk').values_list('xp_total', 'nivel')))
        self.assertEqual(HistorialXP.objects.count(), 2)


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks and independent connections')
class XPPostgreSQLConcurrencyTests(XPFixture, TransactionTestCase):
    def concurrent_awards(self, tournament_ids):
        barrier = Barrier(2, timeout=10)
        def worker(tournament_id):
            close_old_connections()
            db = connections['default']
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '15s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    backend_pid = cursor.fetchone()[0]
                reached_lock = False
                def synchronize(execute, sql, params, many, context):
                    nonlocal reached_lock
                    if not reached_lock and 'FOR NO KEY UPDATE' in sql and 'usuarios_usuario' in sql:
                        reached_lock = True
                        self.assertIn('ORDER BY', sql)
                        barrier.wait()
                    return execute(sql, params, many, context)
                with db.execute_wrapper(synchronize):
                    result = conceder_xp_torneo(Torneo(pk=tournament_id))
                self.assertTrue(reached_lock, 'The worker must acquire real database row locks')
                return backend_pid, [entry.pk for entry in result]
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker, pk) for pk in tournament_ids]
            results = [future.result(timeout=25) for future in futures]
        self.assertEqual(len({pid for pid, _ in results}), 2)
        return results

    def test_simultaneous_distinct_tournaments_preserve_both_awards(self):
        first, second = self.tournament(), self.tournament()
        self.concurrent_awards([first.pk, second.pk])
        self.assertEqual(HistorialXP.objects.count(), 4)
        for player in self.players:
            self.assert_chain(player)
        self.assertEqual(self.players[0].xp_total, 310)
        self.assertEqual(self.players[1].xp_total, 244)

    def test_simultaneous_duplicate_award_is_idempotent(self):
        tournament = self.tournament()
        results = self.concurrent_awards([tournament.pk, tournament.pk])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(HistorialXP.objects.count(), 2)
        for player in self.players:
            self.assert_chain(player)

    def test_reversed_participant_orders_do_not_deadlock(self):
        first, second = self.tournament(), self.tournament(reverse=True)
        self.concurrent_awards([first.pk, second.pk])
        self.assertEqual(HistorialXP.objects.count(), 4)
        for player in self.players:
            self.assert_chain(player)
            self.assertEqual(player.xp_total, 277)
