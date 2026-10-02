from django.core.management.base import BaseCommand, CommandError

from torneos.models import Torneo
from usuarios.karma import KarmaError, conceder_karma_torneo


class Command(BaseCommand):
    help = 'Completa de forma idempotente el Karma pendiente de un torneo finalizado.'

    def add_arguments(self, parser):
        parser.add_argument('--torneo', type=int, required=True)

    def handle(self, *args, **options):
        try:
            torneo = Torneo.objects.get(pk=options['torneo'])
        except Torneo.DoesNotExist as error:
            raise CommandError('El torneo indicado no existe.') from error
        try:
            movimientos = conceder_karma_torneo(torneo)
        except KarmaError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(
            f'Movimientos de Karma creados para {torneo.nombre}: {len(movimientos)}.',
        ))