from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from partidas.models import CheckInPartida, Partida
from partidas.scheduling import CheckInError, confirmar_checkin
from usuarios.models import Usuario


class Command(BaseCommand):
    help = 'Confirma el check-in de un jugador ficticio local mediante el servicio real.'

    def add_arguments(self, parser):
        parser.add_argument('--partida', type=int, required=True)
        parser.add_argument('--usuario', required=True, help='Username del jugador ficticio.')

    def handle(self, *args, **options):
        if not settings.DEBUG or not settings.DEV_LOCAL:
            raise CommandError('Este comando requiere DEBUG=True y TORNEO_DEV_LOCAL=1.')
        try:
            partida = Partida.objects.get(pk=options['partida'])
            usuario = Usuario.objects.get(username=options['usuario'])
        except (Partida.DoesNotExist, Usuario.DoesNotExist) as error:
            raise CommandError('La partida o el usuario indicado no existe.') from error
        if not (
            usuario.username.startswith('bot_prueba_')
            and usuario.email.endswith('@example.invalid')
            and not usuario.is_staff
            and not usuario.is_superuser
        ):
            raise CommandError('Solo se pueden confirmar cuentas ficticias locales.')
        if not partida.participantes.filter(inscripcion__usuario=usuario).exists():
            raise CommandError('El usuario ficticio no participa en esta partida.')
        try:
            confirmar_checkin(partida, usuario, CheckInPartida.Tipo.PARTICIPANTE)
        except CheckInError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f'Check-in confirmado para {usuario.username} en la partida {partida.pk}.'))
