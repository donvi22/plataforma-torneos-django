from time import sleep

from django.core.management.base import BaseCommand

from partidas.models import Partida
from torneos.services import procesar_calendario


class Command(BaseCommand):
    help = 'Procesa una vez el calendario de torneos o lo repite en desarrollo con --loop.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='Repetir el procesamiento localmente.')
        parser.add_argument('--interval', type=int, default=30, help='Segundos entre ejecuciones con --loop.')

    def handle(self, *args, **options):
        intervalo = max(options['interval'], 1)
        while True:
            candidatas = list(Partida.objects.filter(
                estado__in=(Partida.Estado.PENDIENTE, Partida.Estado.PROGRAMADA),
            ).values_list('pk', flat=True))
            actualizadas = procesar_calendario()
            checkins_abiertos = Partida.objects.filter(
                pk__in=candidatas,
                estado=Partida.Estado.CHECK_IN,
            ).count()
            self.stdout.write(self.style.SUCCESS(
                f'Torneos actualizados: {actualizadas}. Check-ins abiertos: {checkins_abiertos}',
            ))
            if not options['loop']:
                break
            sleep(intervalo)
