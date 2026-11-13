"""Arbitration/scheduling integrity; real multiprocess tests require PostgreSQL."""
import multiprocessing
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from notificaciones.models import Notificacion
from partidas.models import CheckInPartida, HistorialProgramacionPartida, Partida, ParticipantePartida, ResultadoPartida
from partidas.scheduling import CheckInError, confirmar_checkin, detectar_ausencias, preparar_partida, reprogramar_partida
from partidas.services import ResultadoError, validar_resultado
from torneos.models import FormatoCompetitivo, HistorialEstadoTorneo, InscripcionTorneo, Torneo
from torneos.services import InscripcionError, inscribir_usuario, procesar_calendario
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, Videojuego

from .models import ArbitroTorneo, HistorialAsignacionArbitro
from .services import (ArbitrajeError, aceptar_invitacion, asignar_arbitro, cambiar_disponibilidad,
                       invitar_arbitro, reasignar_arbitro, rechazar_invitacion, solicitar_salida)


class ArbitrationFixture:
    def user(self, name):
        return Usuario.objects.create_user(username=name, email=f'{name}@example.invalid',
                                           disponible_para_arbitrar=True, karma_total=150)

    def setUp(self):
        super().setUp()
        self.organizer = self.user('arb_organizer')
        self.organizer.is_staff = self.organizer.is_superuser = True
        self.organizer.rol_global = 'ADMIN'
        self.organizer.save(update_fields=('is_staff', 'is_superuser', 'rol_global'))
        self.game = Videojuego.objects.create(nombre='Arbitration integrity', genero='Competitivo')
        format = FormatoCompetitivo.objects.create(nombre='Arbitration integrity', min_participantes=2, max_participantes=128)
        self.tournament = Torneo.objects.create(
            nombre='Arbitration integrity', videojuego=self.game, organizador=self.organizer,
            formato_competitivo=format, max_participantes=16, tipo=Torneo.Tipo.PUBLICO,
            estado=Torneo.Estado.PREPARADO, duracion_checkin_min=10,
        )
        self.referees = [ArbitroTorneo.objects.create(
            torneo=self.tournament, usuario=self.user(f'arb_ref_{i}'), estado_invitacion='ACEPTADA', activo_en_torneo=True,
        ) for i in range(2)]
        self.players = [self.user(f'arb_player_{i}') for i in range(2)]
        self.registrations = []
        for player in self.players:
            profile = PerfilVideojuegoUsuario.objects.create(usuario=player, videojuego=self.game, nick_en_juego=player.username)
            self.registrations.append(InscripcionTorneo.objects.create(
                torneo=self.tournament, usuario=player, perfil_videojuego=profile, nick_historico=player.username,
            ))
        self.match = self.create_match(1)
        self.now = timezone.now()

    def create_match(self, order):
        match = Partida.objects.create(torneo=self.tournament, numero_ronda=1, numero_orden=order)
        for position, registration in enumerate(self.registrations, 1):
            ParticipantePartida.objects.create(partida=match, inscripcion=registration, posicion=position)
        return match

    def pending(self, name='arb_pending'):
        return ArbitroTorneo.objects.create(torneo=self.tournament, usuario=self.user(name))

    def official_window(self):
        Torneo.objects.filter(pk=self.tournament.pk).update(tipo=Torneo.Tipo.OFICIAL)
        self.tournament.refresh_from_db()
        Partida.objects.filter(pk=self.match.pk).update(fecha_hora_programada=self.now + timedelta(minutes=10))
        preparar_partida(self.match, ahora=self.now)

    def assign(self):
        return asignar_arbitro(self.match, arbitro=self.referees[0])

    def assert_assignment_chain(self, count):
        history = list(self.match.historial_asignaciones_arbitro.order_by('pk'))
        self.assertEqual(len(history), count)
        for before, after in zip(history, history[1:]):
            self.assertEqual(before.arbitro_nuevo_id, after.arbitro_anterior_id)
        self.match.refresh_from_db()
        self.assertEqual(self.match.arbitro_asignado_id, history[-1].arbitro_nuevo_id)
        self.assertEqual(Notificacion.objects.filter(partida=self.match, tipo='ASIGNACION_PARTIDA').count(), count)


class ArbitrationIntegrityTests(ArbitrationFixture, TestCase):
    def test_invitation_uniqueness_is_enforced_by_database(self):
        invitation = self.pending()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArbitroTorneo.objects.create(torneo=self.tournament, usuario=invitation.usuario)

    def test_pending_capacity_and_duplicate_invitation_are_business_errors(self):
        Torneo.objects.filter(pk=self.tournament.pk).update(max_participantes=8)
        users = [self.user(f'limit{i}') for i in range(3)]
        for user in users[:2]:
            invitar_arbitro(self.tournament, user, self.organizer)
        for user in (users[0], users[2]):
            with self.assertRaises(ArbitrajeError):
                invitar_arbitro(self.tournament, user, self.organizer)
        self.assertEqual(self.tournament.arbitros.filter(estado_invitacion='PENDIENTE').count(), 2)

    def test_reinvitation_reuses_row_but_creates_new_event(self):
        user = self.user('reinvited')
        first = invitar_arbitro(self.tournament, user, self.organizer)
        rechazar_invitacion(first, user)
        second = invitar_arbitro(self.tournament, user, self.organizer)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Notificacion.objects.filter(destinatario=user, tipo='INVITACION_ARBITRAL').count(), 2)

    def test_invitation_failure_rolls_back_everything(self):
        user = self.user('failed_invite')
        with patch('notificaciones.services.crear_notificacion', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                invitar_arbitro(self.tournament, user, self.organizer)
        self.assertFalse(self.tournament.arbitros.filter(usuario=user).exists())

    def test_acceptance_and_rejection_notification_failure_roll_back(self):
        self.referees[0].delete()
        invitation = self.pending()
        for respond in (aceptar_invitacion, rechazar_invitacion):
            with self.subTest(respond=respond.__name__):
                with patch('notificaciones.services.crear_notificacion', side_effect=RuntimeError('synthetic')):
                    with self.assertRaises(RuntimeError):
                        respond(invitation, invitation.usuario)
                invitation.refresh_from_db()
                self.assertEqual(invitation.estado_invitacion, 'PENDIENTE')
                self.assertFalse(invitation.activo_en_torneo)

    def test_current_tournament_and_account_permissions_are_checked(self):
        user = self.user('stale_invite')
        Torneo.objects.filter(pk=self.tournament.pk).update(estado=Torneo.Estado.CANCELADO)
        with self.assertRaises(ArbitrajeError):
            invitar_arbitro(self.tournament, user, self.organizer)
        Torneo.objects.filter(pk=self.tournament.pk).update(estado=Torneo.Estado.PREPARADO)
        Usuario.objects.filter(pk=self.organizer.pk).update(estado_cuenta=Usuario.EstadoCuenta.SUSPENDIDA)
        with self.assertRaises(ArbitrajeError):
            invitar_arbitro(self.tournament, user, self.organizer)
        with self.assertRaises(ArbitrajeError):
            asignar_arbitro(self.match, actor=self.organizer)

    def test_stale_invitee_and_availability_karma_are_rechecked(self):
        self.referees[0].delete()
        invitation = self.pending()
        Usuario.objects.filter(pk=invitation.usuario_id).update(estado_cuenta=Usuario.EstadoCuenta.SUSPENDIDA)
        with self.assertRaises(ArbitrajeError):
            aceptar_invitacion(invitation, invitation.usuario)
        user = self.referees[1].usuario
        Usuario.objects.filter(pk=user.pk).update(karma_total=149, disponible_para_arbitrar=False)
        with self.assertRaises(ArbitrajeError):
            cambiar_disponibilidad(user, True)
        user.refresh_from_db()
        self.assertFalse(user.disponible_para_arbitrar)

    def test_manual_assignment_requires_authorized_actor(self):
        with self.assertRaises(ArbitrajeError):
            asignar_arbitro(self.match, actor=self.players[0], arbitro=self.referees[0])
        self.assertFalse(HistorialAsignacionArbitro.objects.exists())

    def test_assignment_notification_failure_rolls_back_history_and_match(self):
        with patch('notificaciones.services.crear_notificacion', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                self.assign()
        self.match.refresh_from_db()
        self.assertIsNone(self.match.arbitro_asignado_id)
        self.assertFalse(HistorialAsignacionArbitro.objects.exists())

    def test_reassignment_failure_rolls_back_checkins_and_history(self):
        self.assign()
        self.official_window()
        checkin = confirmar_checkin(self.match, self.referees[0].usuario, CheckInPartida.Tipo.ARBITRO, ahora=self.now)
        with patch('notificaciones.services.crear_notificacion', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                reasignar_arbitro(self.match, self.referees[1], self.organizer, 'Conflict')
        checkin.refresh_from_db()
        self.assertTrue(checkin.confirmado)
        self.assert_assignment_chain(1)

    def test_referee_round_trip_does_not_resurrect_old_checkin(self):
        self.assign()
        self.official_window()
        confirmar_checkin(self.match, self.referees[0].usuario, CheckInPartida.Tipo.ARBITRO, ahora=self.now)
        for target in (self.referees[1], self.referees[0]):
            reasignar_arbitro(self.match, target, self.organizer, 'Conflict')
            self.assertFalse(self.match.checkins.filter(tipo='ARBITRO', confirmado=True).exists())
        confirmar_checkin(self.match, self.referees[0].usuario, CheckInPartida.Tipo.ARBITRO, ahora=self.now)
        self.assert_assignment_chain(3)

    def test_departure_is_idempotent_and_invalidates_current_referee_checkin(self):
        self.assign()
        self.official_window()
        confirmar_checkin(self.match, self.referees[0].usuario, CheckInPartida.Tipo.ARBITRO, ahora=self.now)
        first = solicitar_salida(self.referees[0], 'Conflict')
        second = solicitar_salida(self.referees[0], 'Retry')
        self.assertEqual(first.fecha_salida, second.fecha_salida)
        self.assertEqual(second.motivo_salida, 'Conflict')
        self.assertFalse(self.match.checkins.filter(tipo='ARBITRO', confirmado=True).exists())
        with self.assertRaises(CheckInError):
            confirmar_checkin(self.match, self.referees[0].usuario, CheckInPartida.Tipo.ARBITRO, ahora=self.now)

    def test_opening_failure_rolls_back_all_notifications_and_state(self):
        from notificaciones.services import crear_notificacion
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('synthetic second notification failure')
            return crear_notificacion(*args, **kwargs)
        with patch('notificaciones.services.crear_notificacion', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                preparar_partida(self.match, ahora=self.now)
        self.match.refresh_from_db()
        self.assertEqual(self.match.estado, Partida.Estado.PENDIENTE)
        self.assertIsNone(self.match.fecha_hora_apertura_checkin)
        self.assertFalse(Notificacion.objects.filter(partida=self.match).exists())

    def test_validation_and_incident_states_cannot_be_reopened(self):
        for state in (Partida.Estado.PENDIENTE_VALIDACION, Partida.Estado.INCIDENCIA):
            Partida.objects.filter(pk=self.match.pk).update(estado=state)
            with self.assertRaises(CheckInError):
                preparar_partida(self.match, ahora=self.now)
            self.match.refresh_from_db()
            self.assertEqual(self.match.estado, state)

    def test_checkin_uses_current_account_and_expiration(self):
        self.official_window()
        with self.assertRaises(CheckInError):
            confirmar_checkin(self.match, self.players[0], 'PARTICIPANTE', ahora=self.now + timedelta(minutes=11))
        Usuario.objects.filter(pk=self.players[0].pk).update(estado_cuenta=Usuario.EstadoCuenta.SUSPENDIDA)
        with self.assertRaises(CheckInError):
            confirmar_checkin(self.match, self.players[0], 'PARTICIPANTE', ahora=self.now)
        self.assertFalse(self.match.checkins.exists())

    def test_schedule_uses_current_actor_permissions(self):
        self.official_window()
        Usuario.objects.filter(pk=self.organizer.pk).update(estado_cuenta=Usuario.EstadoCuenta.SUSPENDIDA)
        with self.assertRaises(CheckInError):
            reprogramar_partida(self.match, self.now + timedelta(hours=1), self.organizer, 'Change', ahora=self.now)
        self.assertFalse(HistorialProgramacionPartida.objects.exists())

    def test_absence_detection_is_read_only_and_does_not_decide_result(self):
        self.official_window()
        confirmar_checkin(self.match, self.players[0], 'PARTICIPANTE', ahora=self.now)
        status = detectar_ausencias(self.match, ahora=self.now + timedelta(minutes=11))
        self.assertEqual(status, {'participantes_ausentes':[self.players[1].pk], 'arbitro_ausente':True})
        self.assertFalse(ResultadoPartida.objects.exists())
        self.match.refresh_from_db()
        self.assertEqual(self.match.estado, Partida.Estado.CHECK_IN)

    def test_cancel_notification_failure_rolls_back_transition(self):
        Torneo.objects.filter(pk=self.tournament.pk).update(
            estado=Torneo.Estado.PRORROGA, fecha_cierre_inscripciones=self.now - timedelta(minutes=20), duracion_prorroga_min=5,
        )
        with patch('notificaciones.services.crear_notificacion', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                procesar_calendario(ahora=self.now)
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.estado, Torneo.Estado.PRORROGA)
        self.assertFalse(HistorialEstadoTorneo.objects.filter(torneo=self.tournament).exists())


@skipUnless(connection.vendor == 'postgresql' and 'fork' in multiprocessing.get_all_start_methods(),
            'Requires isolated PostgreSQL and independent forked processes')
class ArbitrationConcurrencyTests(ArbitrationFixture, TransactionTestCase):
    def workers(self, jobs, boundary='torneos_torneo'):
        connections.close_all()
        context = multiprocessing.get_context('fork')
        barrier, queue = context.Barrier(2, timeout=15), context.Queue()
        def worker(job):
            db = connections['default']
            reached = False
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout='10s'")
                    cursor.execute("SET statement_timeout='20s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                def synchronize(execute, sql, params, many, context):
                    nonlocal reached
                    if not reached and 'FOR NO KEY UPDATE' in sql and boundary in sql:
                        reached = True
                        barrier.wait()
                    return execute(sql, params, many, context)
                with db.execute_wrapper(synchronize):
                    try:
                        value = job()
                        outcome = ('ok', getattr(value, 'pk', None))
                    except (ArbitrajeError, CheckInError, InscripcionError, ResultadoError) as error:
                        outcome = ('business', str(error))
                queue.put((pid, reached, outcome))
            except BaseException as error:
                queue.put((None, reached, ('error', type(error).__name__, str(error))))
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

    def test_pending_capacity(self):
        Torneo.objects.filter(pk=self.tournament.pk).update(max_participantes=8)
        self.pending('capacity_existing')
        users = [self.user(f'capacity_{i}') for i in range(2)]
        jobs = [lambda:invitar_arbitro(self.tournament, users[0], self.organizer),
                lambda:invitar_arbitro(self.tournament, users[1], self.organizer)]
        self.assertCountEqual(self.workers(jobs), ['ok', 'business'])
        self.assertEqual(self.tournament.arbitros.filter(estado_invitacion='PENDIENTE').count(), 2)

    def test_duplicate_invitation(self):
        user = self.user('duplicate_invite')
        job = lambda:invitar_arbitro(self.tournament, user, self.organizer)
        self.assertCountEqual(self.workers([job, job]), ['ok', 'business'])
        self.assertEqual(self.tournament.arbitros.filter(usuario=user).count(), 1)
        self.assertEqual(Notificacion.objects.filter(destinatario=user).count(), 1)

    def test_last_accepted_slot(self):
        self.referees[0].delete()
        invitations = [self.pending(f'last_slot_{i}') for i in range(2)]
        jobs = [lambda:aceptar_invitacion(invitations[0], invitations[0].usuario),
                lambda:aceptar_invitacion(invitations[1], invitations[1].usuario)]
        self.assertCountEqual(self.workers(jobs), ['ok', 'business'])
        self.assertEqual(self.tournament.arbitros.filter(estado_invitacion='ACEPTADA', activo_en_torneo=True).count(), 2)
        self.assertEqual(self.tournament.arbitros.filter(estado_invitacion='PENDIENTE').count(), 0)

    def test_accept_reject_same_invitation(self):
        self.referees[0].delete()
        invitation = self.pending()
        jobs = [lambda:aceptar_invitacion(invitation, invitation.usuario),
                lambda:rechazar_invitacion(invitation, invitation.usuario)]
        self.assertCountEqual(self.workers(jobs), ['ok', 'business'])
        invitation.refresh_from_db()
        self.assertEqual(invitation.activo_en_torneo, invitation.estado_invitacion == 'ACEPTADA')
        self.assertEqual(Notificacion.objects.filter(invitacion_arbitral=invitation).count(), 1)

    def test_acceptance_and_enrollment_are_mutually_exclusive(self):
        self.referees[0].delete()
        invitation = self.pending()
        PerfilVideojuegoUsuario.objects.create(usuario=invitation.usuario, videojuego=self.game, nick_en_juego='Invited')
        Torneo.objects.filter(pk=self.tournament.pk).update(
            estado=Torneo.Estado.INSCRIPCIONES_ABIERTAS, fecha_apertura_inscripciones=self.now-timedelta(minutes=5),
            fecha_cierre_inscripciones=self.now+timedelta(minutes=5),
        )
        jobs = [lambda:aceptar_invitacion(invitation, invitation.usuario),
                lambda:inscribir_usuario(self.tournament, invitation.usuario)]
        self.assertCountEqual(self.workers(jobs), ['ok', 'business'])
        invitation.refresh_from_db()
        enrolled = InscripcionTorneo.objects.filter(torneo=self.tournament, usuario=invitation.usuario, estado='CONFIRMADA').exists()
        self.assertNotEqual(invitation.activo_en_torneo, enrolled)

    def test_initial_assignment_is_idempotent(self):
        jobs = [lambda:asignar_arbitro(self.match, arbitro=self.referees[0]),
                lambda:asignar_arbitro(self.match, arbitro=self.referees[1])]
        self.assertEqual(self.workers(jobs), ['ok', 'ok'])
        self.assert_assignment_chain(1)

    def test_assignments_to_different_matches_balance_load(self):
        other = self.create_match(2)
        self.assertEqual(self.workers([lambda:asignar_arbitro(self.match), lambda:asignar_arbitro(other)]), ['ok', 'ok'])
        self.match.refresh_from_db(); other.refresh_from_db()
        self.assertNotEqual(self.match.arbitro_asignado_id, other.arbitro_asignado_id)
        self.assertEqual(HistorialAsignacionArbitro.objects.count(), 2)

    def test_identical_reassignments_commit_one_history(self):
        self.assign()
        job = lambda:reasignar_arbitro(self.match, self.referees[1], self.organizer, 'Conflict')
        self.assertCountEqual(self.workers([job, job]), ['ok', 'business'])
        self.assert_assignment_chain(2)

    def test_reassignment_and_old_referee_validation_share_order(self):
        self.assign()
        Torneo.objects.filter(pk=self.tournament.pk).update(tipo=Torneo.Tipo.OFICIAL)
        Partida.objects.filter(pk=self.match.pk).update(estado=Partida.Estado.EN_CURSO)
        jobs = [lambda:reasignar_arbitro(self.match, self.referees[1], self.organizer, 'Conflict'),
                lambda:validar_resultado(self.match, self.referees[0].usuario, '2-1', self.registrations[0])]
        self.assertIn('ok', self.workers(jobs))
        self.assert_assignment_chain(2)
        results = ResultadoPartida.objects.filter(partida=self.match)
        if results.exists():
            self.assertEqual(results.get().validado_por_id, self.referees[0].usuario_id)
            self.match.refresh_from_db()
            self.assertEqual(self.match.estado, Partida.Estado.FINALIZADA)
        else:
            self.assertEqual(self.match.estado, Partida.Estado.EN_CURSO)

    def test_departure_and_validation_share_order(self):
        self.assign()
        Torneo.objects.filter(pk=self.tournament.pk).update(tipo=Torneo.Tipo.OFICIAL)
        Partida.objects.filter(pk=self.match.pk).update(estado=Partida.Estado.EN_CURSO)
        jobs = [lambda:solicitar_salida(self.referees[0], 'Conflict'),
                lambda:validar_resultado(self.match, self.referees[0].usuario, '2-1', self.registrations[0])]
        self.assertIn('ok', self.workers(jobs))
        self.referees[0].refresh_from_db()
        self.assertFalse(self.referees[0].activo_en_torneo)
        self.assertLessEqual(ResultadoPartida.objects.filter(partida=self.match).count(), 1)

    def test_checkin_and_reassignment_invalidate_old_responsibility(self):
        self.assign(); self.official_window()
        jobs = [lambda:confirmar_checkin(self.match, self.referees[0].usuario, 'ARBITRO', ahora=self.now),
                lambda:reasignar_arbitro(self.match, self.referees[1], self.organizer, 'Conflict')]
        self.assertIn('ok', self.workers(jobs))
        self.assert_assignment_chain(2)
        self.assertFalse(self.match.checkins.filter(usuario=self.referees[0].usuario, confirmado=True).exists())

    def test_opening_is_idempotent(self):
        job = lambda:preparar_partida(self.match, ahora=self.now)
        self.assertEqual(self.workers([job, job]), ['ok', 'ok'])
        self.match.refresh_from_db()
        self.assertEqual(self.match.fecha_hora_apertura_checkin, self.now)
        self.assertEqual(Notificacion.objects.filter(partida=self.match, tipo='APERTURA_CHECKIN').count(), 3)

    def test_duplicate_checkin(self):
        preparar_partida(self.match, ahora=self.now)
        job = lambda:confirmar_checkin(self.match, self.players[0], 'PARTICIPANTE', ahora=self.now)
        self.assertCountEqual(self.workers([job, job]), ['ok', 'business'])
        self.assertEqual(self.match.checkins.count(), 1)

    def test_last_checkins_start_match_once(self):
        preparar_partida(self.match, ahora=self.now)
        confirmar_checkin(self.match, self.organizer, 'ARBITRO', ahora=self.now)
        jobs = [lambda:confirmar_checkin(self.match, self.players[0], 'PARTICIPANTE', ahora=self.now),
                lambda:confirmar_checkin(self.match, self.players[1], 'PARTICIPANTE', ahora=self.now)]
        self.assertEqual(self.workers(jobs), ['ok', 'ok'])
        self.match.refresh_from_db(); self.tournament.refresh_from_db()
        self.assertEqual(self.match.estado, Partida.Estado.EN_CURSO)
        self.assertEqual(self.match.checkins.filter(confirmado=True).count(), 3)
        self.assertEqual(self.tournament.estado, Torneo.Estado.EN_CURSO)
        self.assertEqual(HistorialEstadoTorneo.objects.filter(torneo=self.tournament, estado_nuevo='EN_CURSO').count(), 1)

    def test_reprogramming_and_checkin_do_not_keep_old_confirmation(self):
        self.official_window()
        jobs = [lambda:confirmar_checkin(self.match, self.players[0], 'PARTICIPANTE', ahora=self.now),
                lambda:reprogramar_partida(self.match, self.now+timedelta(hours=1), self.organizer, 'Change', ahora=self.now)]
        self.assertIn('ok', self.workers(jobs))
        self.match.refresh_from_db()
        self.assertEqual(self.match.estado, Partida.Estado.PROGRAMADA)
        self.assertFalse(self.match.checkins.filter(confirmado=True).exists())
        self.assertEqual(HistorialProgramacionPartida.objects.filter(partida=self.match).count(), 1)

    def test_simultaneous_reprogramming_keeps_a_contiguous_history(self):
        self.official_window()
        dates = [self.now+timedelta(hours=i) for i in (1,2)]
        jobs = [lambda:reprogramar_partida(self.match, dates[0], self.organizer, 'First', ahora=self.now),
                lambda:reprogramar_partida(self.match, dates[1], self.organizer, 'Second', ahora=self.now)]
        self.assertEqual(self.workers(jobs), ['ok', 'ok'])
        histories = list(self.match.historial_programacion.order_by('pk'))
        self.assertEqual(len(histories), 2)
        self.assertEqual(histories[0].fecha_nueva, histories[1].fecha_anterior)
        self.match.refresh_from_db()
        self.assertEqual(self.match.fecha_hora_programada, histories[1].fecha_nueva)

    def test_availability_and_assignment_coordinate_on_user_row(self):
        self.referees[1].delete()
        jobs = [lambda:asignar_arbitro(self.match),
                lambda:cambiar_disponibilidad(self.referees[0].usuario, False)]
        self.assertEqual(self.workers(jobs, boundary='usuarios_usuario'), ['ok', 'ok'])
        self.referees[0].usuario.refresh_from_db()
        self.assertFalse(self.referees[0].usuario.disponible_para_arbitrar)
        self.match.refresh_from_db()
        if self.match.arbitro_asignado_id:
            self.assert_assignment_chain(1)
        else:
            self.assertFalse(HistorialAsignacionArbitro.objects.exists())

    def test_calendar_cancellation_and_opening_do_not_reopen_tournament(self):
        Torneo.objects.filter(pk=self.tournament.pk).update(
            estado=Torneo.Estado.PRORROGA, fecha_cierre_inscripciones=self.now-timedelta(minutes=20), duracion_prorroga_min=5,
        )
        jobs = [lambda:procesar_calendario(ahora=self.now), lambda:preparar_partida(self.match, ahora=self.now)]
        self.assertIn('ok', self.workers(jobs))
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.estado, Torneo.Estado.CANCELADO)
        self.assertEqual(HistorialEstadoTorneo.objects.filter(torneo=self.tournament, estado_nuevo='CANCELADO').count(), 1)
        self.assertFalse(ResultadoPartida.objects.exists())

    def test_expired_checkin_and_official_no_show_are_consistent(self):
        self.official_window()
        expired = self.now + timedelta(minutes=11)
        jobs = [lambda:confirmar_checkin(self.match, self.players[0], 'PARTICIPANTE', ahora=expired),
                lambda:validar_resultado(self.match, self.organizer, '', self.registrations[1],
                                        tipo_resultado=ResultadoPartida.TipoResultado.INCOMPARECENCIA)]
        self.assertCountEqual(self.workers(jobs), ['ok', 'business'])
        self.match.refresh_from_db()
        self.assertEqual(self.match.estado, Partida.Estado.FINALIZADA)
        result = ResultadoPartida.objects.get(partida=self.match)
        self.assertEqual(result.ganador_id, self.registrations[1].pk)
        self.assertEqual(self.match.historial_resultados.count(), 1)
        self.assertFalse(self.match.checkins.exists())

    def test_absence_snapshot_and_reprogramming_do_not_mix_windows(self):
        self.official_window()
        expired = self.now + timedelta(minutes=11)
        def snapshot():
            status = detectar_ausencias(self.match, ahora=expired)
            assert status in (
                {'participantes_ausentes':[p.pk for p in self.players], 'arbitro_ausente':True},
                {'participantes_ausentes':[], 'arbitro_ausente':False},
            ), status
        jobs = [snapshot,
                lambda:reprogramar_partida(self.match, self.now+timedelta(hours=1), self.organizer, 'Change', ahora=expired)]
        self.assertEqual(self.workers(jobs), ['ok', 'ok'])
        self.match.refresh_from_db()
        self.assertEqual(self.match.estado, Partida.Estado.PROGRAMADA)
        self.assertFalse(ResultadoPartida.objects.exists())
