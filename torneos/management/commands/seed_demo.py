from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from arbitraje.models import ArbitroTorneo
from arbitraje.services import aceptar_invitacion, asignar_arbitro, cambiar_disponibilidad, invitar_arbitro
from moderacion.models import Denuncia
from moderacion.services import crear_denuncia, resolver_denuncia, tomar_en_revision
from notificaciones.models import Notificacion
from notificaciones.services import marcar_leida
from partidas.lobby import actualizar_lobby
from partidas.models import CheckInPartida, Partida
from partidas.scheduling import confirmar_checkin, preparar_partida, reprogramar_partida
from partidas.services import declarar_resultado, preparar_bracket, validar_resultado
from torneos.models import FormatoCompetitivo, Torneo
from torneos.privados import acceder_con_codigo, inscribir_en_privado
from torneos.services import cancelar_inscripcion, cerrar_torneo, crear_torneo, inscribir_usuario, procesar_calendario, publicar_torneo
from usuarios.karma import ajustar_karma, conceder_karma_torneo, premiar_resultado_validado
from usuarios.models import Usuario
from usuarios.services import nivel_para_xp
from videojuegos.models import PerfilVideojuegoUsuario, Plataforma, RangoVideojuego, Videojuego


DEMO_PASSWORD = 'DEMO-RankedKingdom-2026!'
DEMO_GROUP = 'ranked-kingdom-demo-v1'
DEMO_TOURNAMENTS = (
    'Kingdom Invitational', 'Kingdom Masters', 'Vortex Circuit', 'Community Clash',
    'Zenith Scheduled Cup', 'Pulse Check-in Cup', 'NightFox Private Arena', 'Ranked Kingdom Open',
)
DEMO_ACCOUNTS = (
    ('admin', 'KingdomAdmin', 3600),
    ('organizador', 'NovaForge', 1600),
    ('organizador', 'ArcByte', 2500),
    ('jugador', 'NovaStrike', 0),
    ('jugador', 'Vortex', 100),
    ('jugador', 'NightFox', 400),
    ('jugador', 'RiftOne', 900),
    ('jugador', 'Pulse', 1600),
    ('jugador', 'ShadowCore', 2500),
    ('jugador', 'Zenith', 3600),
    ('jugador', 'EchoVector', 100),
    ('arbitro', 'FairSignal', 1600),
    ('arbitro', 'ClearOrbit', 2500),
    ('arbitro', 'TruePulse', 1600),
)


@contextmanager
def demo_clock(moment):
    class ScenarioDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    with patch('django.utils.timezone.datetime', ScenarioDateTime):
        yield


class Command(BaseCommand):
    help = 'Prepara datos DEMO aislados; requiere DEBUG=True y TORNEO_DEMO=1.'

    def handle(self, *args, **options):
        if not settings.DEBUG or not settings.DEMO_MODE:
            raise CommandError('Solo desarrollo: requiere DEBUG=True y TORNEO_DEMO=1 (db_demo.sqlite3).')
        self.now = timezone.now().replace(microsecond=0)
        with transaction.atomic():
            marker = Group.objects.filter(name=DEMO_GROUP).first()
            if marker:
                users = self._existing_users(marker)
                created = False
            else:
                if Torneo.objects.exists():
                    raise CommandError('La base demo contiene torneos ajenos; no se ejecutara su calendario.')
                usernames = [f'demo_{name}' for _, name, _ in DEMO_ACCOUNTS]
                emails = [f'{username.lower()}@demo.example.invalid' for username in usernames]
                if Usuario.objects.filter(username__in=usernames).exists() or Usuario.objects.filter(email__in=emails).exists():
                    raise CommandError('Colision con cuentas existentes: no se modifico ningun dato.')
                marker = Group.objects.create(name=DEMO_GROUP)
                users = {}
                for role, name, xp in DEMO_ACCOUNTS:
                    username = f'demo_{name}'
                    manager = Usuario.objects.create_superuser if role == 'admin' else Usuario.objects.create_user
                    user = manager(
                        username=username, email=f'{username.lower()}@demo.example.invalid',
                        password=DEMO_PASSWORD, xp_total=xp, nivel=nivel_para_xp(xp),
                        date_joined=self.now - timedelta(days=7),
                    )
                    user.groups.add(marker)
                    users[name] = user
                self._populate(users)
                created = True
        self.stdout.write(self.style.SUCCESS(f'DEMO: {len(users)} usuarios {"creados" if created else "encontrados"}.'))
        for role, name, _ in DEMO_ACCOUNTS:
            self.stdout.write(f'{role}: {users[name].username}')
        self.stdout.write(f'Contrasena inicial exclusivamente DEMO (no se restablece): {DEMO_PASSWORD}')
        tournaments = Torneo.objects.filter(
            organizador__in=users.values(), nombre__in=[f'DEMO - {name}' for name in DEMO_TOURNAMENTS],
        ).order_by('pk')
        for tournament in tournaments:
            self.stdout.write(f'{tournament.nombre}: {tournament.get_estado_display()} ({tournament.participantes_confirmados}/{tournament.max_participantes})')
            if tournament.tipo == Torneo.Tipo.PRIVADO:
                self.stdout.write(f'Invitacion privada DEMO: /torneos/privado/{tournament.codigo_acceso}/')
        self.stdout.write(
            f'Torneos: {tournaments.count()}; partidas: {Partida.objects.filter(torneo__in=tournaments).count()}; '
            f'arbitrajes: {ArbitroTorneo.objects.filter(torneo__in=tournaments).count()}; '
            f'denuncias: {Denuncia.objects.filter(torneo__in=tournaments).count()}; '
            f'notificaciones: {Notificacion.objects.filter(destinatario__in=users.values()).count()}.'
        )
        self.stdout.write('Las fechas no se desplazan al repetir el comando. Las ventanas temporales reales caducan.')

    def _existing_users(self, marker):
        users = {}
        for _, name, _ in DEMO_ACCOUNTS:
            user = marker.user_set.filter(username=f'demo_{name}', email=f'demo_{name.lower()}@demo.example.invalid').first()
            if user is None:
                raise CommandError('La demo existente esta incompleta; no se sobrescribiran datos.')
            users[name] = user
        for name in DEMO_TOURNAMENTS:
            if Torneo.objects.filter(organizador__groups=marker, nombre=f'DEMO - {name}').count() != 1:
                raise CommandError('Los torneos demo estan incompletos o son ambiguos; no se sobrescribiran automaticamente.')
        return users

    def _populate(self, users):
        self.users = users
        self.admin = users['KingdomAdmin']
        self.players = [users[name] for role, name, _ in DEMO_ACCOUNTS if role == 'jugador']
        with demo_clock(self.now - timedelta(days=7)):
            for role, name, _ in DEMO_ACCOUNTS:
                if role in ('organizador', 'arbitro') or name == 'Zenith':
                    user = users[name]
                    ajustar_karma(user, 80 if role == 'arbitro' else 60, 'Saldo inicial ficticio DEMO autorizado.', self.admin, f'seed-demo:v1:karma:{user.username}')
                    user.refresh_from_db()
                    if role == 'arbitro' or name == 'Zenith':
                        cambiar_disponibilidad(user, True)
            self._catalog()
        self.format, _ = FormatoCompetitivo.objects.get_or_create(
            nombre='DEMO - Eliminacion individual', defaults={'min_participantes': 2, 'max_participantes': 8},
        )
        if not self.format.es_tamano_valido(8):
            raise CommandError('El formato reservado DEMO existente no admite ocho participantes.')
        for name, kind, owner, age, size in (
            ('Kingdom Invitational', Torneo.Tipo.OFICIAL, self.admin, 4, 4),
            ('Kingdom Masters', Torneo.Tipo.PUBLICO, users['NovaForge'], 3, 8),
        ):
            start = self.now - timedelta(days=age)
            tournament = self._tournament(name, kind, owner, start, size, size)
            self._referee(tournament, users['ClearOrbit'])
            self._finish(tournament, start + timedelta(hours=1))
        circuit = self._tournament('Vortex Circuit', Torneo.Tipo.PUBLICO, users['ArcByte'], self.now - timedelta(minutes=70), 8, 8)
        self._referee(circuit, users['FairSignal'])
        for index, match in enumerate(circuit.partidas.filter(numero_ronda=1).order_by('numero_orden')[:3]):
            self._play(match, self.now - timedelta(minutes=23 - index), complete=index < 2)
        self._play(circuit.partidas.get(numero_ronda=1, numero_orden=4), self.now - timedelta(minutes=20), declare=False)
        self._tournament('Community Clash', Torneo.Tipo.PUBLICO, users['NovaForge'], self.now - timedelta(minutes=55), 8, 8)
        scheduled = self._tournament('Zenith Scheduled Cup', Torneo.Tipo.OFICIAL, self.admin, self.now - timedelta(minutes=50), 8, 8)
        self._referee(scheduled, users['ClearOrbit'])
        with demo_clock(self.now):
            for index, match in enumerate(scheduled.partidas.filter(numero_ronda=1).order_by('numero_orden')):
                date = self.now + timedelta(days=1, minutes=index * 30)
                reprogramar_partida(match, date, self.admin)
                if index == 0:
                    reprogramar_partida(match, date + timedelta(hours=2), self.admin, motivo='Ajuste de agenda DEMO.')
        checkin = self._tournament('Pulse Check-in Cup', Torneo.Tipo.OFICIAL, self.admin, self.now - timedelta(minutes=50), 4, 4)
        self._referee(checkin, users['FairSignal'])
        with demo_clock(self.now):
            for index, match in enumerate(checkin.partidas.filter(numero_ronda=1).order_by('numero_orden')):
                reprogramar_partida(match, self.now + timedelta(minutes=30 + index * 90), self.admin)
                if index == 0:
                    participant = match.participantes.select_related('inscripcion__usuario').order_by('posicion').first()
                    confirmar_checkin(match, participant.inscripcion.usuario, CheckInPartida.Tipo.PARTICIPANTE)
                    actualizar_lobby(match, self.admin, nombre='Sala DEMO Pulse', codigo='DEMO-PULSE', contrasena='DEMO-LOBBY', instrucciones='Sala ficticia para la presentacion. No usar credenciales reales.')
        self._tournament('NightFox Private Arena', Torneo.Tipo.PRIVADO, users['ArcByte'], self.now, 8, 4)
        opened = self._tournament('Ranked Kingdom Open', Torneo.Tipo.PUBLICO, users['NovaForge'], self.now - timedelta(minutes=5), 8, 3)
        with demo_clock(self.now):
            cancelled = inscribir_usuario(opened, self.players[-1])
            cancelar_inscripcion(cancelled, 'Cambio de disponibilidad DEMO.')
            invitar_arbitro(opened, users['TruePulse'], opened.organizador)
            self._moderation(circuit)
            for user in (self.players[0], users['ClearOrbit']):
                notice = user.notificaciones.order_by('pk').first()
                if notice:
                    marcar_leida(user, notice)

    def _catalog(self):
        self.games = list(Videojuego.objects.filter(activo=True).order_by('nombre')[:3])
        for name, genre, platforms, ranks in (
            ('Valorant', 'Tactico individual', ('PC',), ('Hierro', 'Plata', 'Diamante')),
            ('Rocket League', 'Deportes 1 contra 1', ('PC', 'PlayStation', 'Xbox'), ('Bronce', 'Oro', 'Campeon')),
            ('League of Legends', 'Duelo individual', ('PC',), ('Hierro', 'Oro', 'Diamante')),
        ):
            if len(self.games) >= 3:
                break
            game, created = Videojuego.objects.get_or_create(nombre=name, defaults={'genero': genre, 'instrucciones_competicion': 'DEMO: formato individual. Seguir las reglas especificas del torneo.'})
            if created:
                for platform in platforms:
                    game.plataformas.add(Plataforma.objects.get_or_create(nombre=platform)[0])
                for position, rank in enumerate(ranks, 1):
                    RangoVideojuego.objects.create(videojuego=game, nombre=rank, posicion=position)
            if game.activo and game not in self.games:
                self.games.append(game)
        if not self.games:
            raise CommandError('No hay videojuegos activos compatibles para la demo.')
        for index, user in enumerate(self.users.values()):
            for game_index, game in enumerate(self.games):
                ranks = list(game.rangos.order_by('posicion'))
                profile = PerfilVideojuegoUsuario(
                    usuario=user, videojuego=game, nick_en_juego=f'{user.username.removeprefix("demo_")}_{game_index + 1}',
                    rango_declarado=ranks[(index + game_index) % len(ranks)] if ranks else None,
                )
                profile.full_clean()
                profile.save()

    def _tournament(self, name, kind, owner, start, size, enrolled):
        close = start + timedelta(minutes=45)
        game = self.games[DEMO_TOURNAMENTS.index(name) % len(self.games)]
        with demo_clock(start):
            owner.refresh_from_db()
            tournament = crear_torneo(
                owner, nombre=f'DEMO - {name}', tipo=kind, videojuego=game, formato_competitivo=self.format,
                max_participantes=size, estado=Torneo.Estado.BORRADOR,
                fecha_apertura_inscripciones=start if kind != Torneo.Tipo.PRIVADO else None,
                fecha_cierre_inscripciones=close if kind != Torneo.Tipo.PRIVADO else None,
                duracion_prorroga_min=15 if kind == Torneo.Tipo.PUBLICO else None,
                descanso_entre_partidas_min=5, duracion_checkin_min=60,
                reglas='Escenario ficticio DEMO. Competicion individual, eliminacion directa y resultado al mejor de tres.',
            )
            publicar_torneo(tournament, owner)
            if kind == Torneo.Tipo.OFICIAL:
                procesar_calendario(ahora=start)
            for player in self.players[:enrolled]:
                if kind == Torneo.Tipo.PRIVADO:
                    acceder_con_codigo(tournament.codigo_acceso, player)
                    inscribir_en_privado(tournament, player)
                else:
                    inscribir_usuario(tournament, player)
        if enrolled == size:
            with demo_clock(close):
                if kind == Torneo.Tipo.PUBLICO:
                    procesar_calendario(ahora=close)
                else:
                    preparar_bracket(tournament, owner)
        tournament.refresh_from_db()
        return tournament

    def _referee(self, tournament, user):
        with demo_clock(tournament.fecha_cierre_inscripciones):
            invitation = invitar_arbitro(tournament, user, tournament.organizador)
            invitation = aceptar_invitacion(invitation, user)
            for match in tournament.partidas.order_by('numero_ronda', 'numero_orden'):
                asignar_arbitro(match, actor=tournament.organizador, arbitro=invitation)

    def _play(self, match, moment, complete=True, declare=True):
        with demo_clock(moment - timedelta(minutes=1)):
            if match.torneo.tipo == Torneo.Tipo.OFICIAL:
                reprogramar_partida(match, moment, self.admin)
        with demo_clock(moment):
            match.refresh_from_db()
            if match.estado in (Partida.Estado.PENDIENTE, Partida.Estado.PROGRAMADA):
                preparar_partida(match)
            participants = list(match.participantes.select_related('inscripcion__usuario').order_by('posicion'))
            for participant in participants:
                confirmar_checkin(match, participant.inscripcion.usuario, CheckInPartida.Tipo.PARTICIPANTE)
            match.refresh_from_db()
            responsible = match.arbitro_asignado.usuario if match.arbitro_asignado else match.torneo.organizador
            confirmar_checkin(match, responsible, CheckInPartida.Tipo.ARBITRO)
            if not declare:
                return
            winner = participants[0].inscripcion
            with demo_clock(moment + timedelta(seconds=30)):
                declarar_resultado(match, participants[0].inscripcion.usuario, '2-0', winner)
            if complete:
                with demo_clock(moment + timedelta(seconds=60)):
                    declarar_resultado(match, participants[1].inscripcion.usuario, '2-0', winner)
                with demo_clock(moment + timedelta(seconds=90)):
                    result = validar_resultado(match, responsible, '2-0', ganador=winner, motivo='Resultado validado en escenario DEMO.')
                    premiar_resultado_validado(result.pk)

    def _finish(self, tournament, moment):
        for match in tournament.partidas.order_by('numero_ronda', 'numero_orden'):
            self._play(match, moment)
            moment += timedelta(minutes=10)
        tournament.refresh_from_db()
        with demo_clock(moment):
            if tournament.estado != Torneo.Estado.FINALIZADO:
                cerrar_torneo(tournament, actor=tournament.organizador)
            conceder_karma_torneo(tournament)

    def _moderation(self, tournament):
        crear_denuncia(self.players[0], Denuncia.Categoria.RESULTADO_INCORRECTO, 'Resultado incorrecto: solicitud neutra de revision DEMO.', torneo=tournament)
        resolved = crear_denuncia(self.players[1], Denuncia.Categoria.CONDUCTA_INAPROPIADA, 'Conducta antideportiva: incidencia ficticia sin contenido ofensivo.', torneo=tournament, usuario_denunciado=self.players[2])
        resolved, _ = tomar_en_revision(resolved, self.admin)
        resolver_denuncia(resolved, self.admin, estado=Denuncia.Estado.RESUELTA, resolucion='Se confirma la incidencia DEMO y se aplica una medida leve.', tipo_sancion=Denuncia.TipoSancion.LEVE)