from django.core.management.base import BaseCommand, CommandError

from torneos.models import Torneo
from usuarios.services import XPError, conceder_xp_torneo


class Command(BaseCommand):
    help = 'Concede XP de forma controlada a un torneo finalizado concreto.'

    def add_arguments(self, parser):
        parser.add_argument('--torneo', type=int, required=True)

    def handle(self, *args, **options):
        try:
            torneo = Torneo.objects.get(pk=options['torneo'])
        except Torneo.DoesNotExist as error:
            raise CommandError('El torneo indicado no existe.') from error
        try:
            movimientos = conceder_xp_torneo(torneo)
        except XPError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(
            f'Recompensas verificadas para {torneo.nombre}: {len(movimientos)} participantes.',
        ))