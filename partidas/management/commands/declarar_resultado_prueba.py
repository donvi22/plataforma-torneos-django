from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from partidas.models import Partida
from partidas.services import ResultadoError, declarar_resultado
from usuarios.models import Usuario


class Command(BaseCommand):
    help = 'Declara un resultado de una cuenta ficticia local mediante el servicio real.'

    def add_arguments(self, parser):
        parser.add_argument('--partida', type=int, required=True)
        parser.add_argument('--usuario', required=True, help='Username del jugador ficticio.')
        parser.add_argument('--resultado', required=True, help='Marcador, por ejemplo 2-1.')
        parser.add_argument('--ganador', type=int, required=True, help='ID de la inscripción ganadora.')

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
            raise CommandError('Solo se pueden usar cuentas ficticias locales.')
        ganador = partida.participantes.filter(inscripcion_id=options['ganador']).select_related('inscripcion').first()
        if not ganador or not partida.participantes.filter(inscripcion__usuario=usuario).exists():
            raise CommandError('El usuario ficticio o el ganador no pertenecen a esta partida.')
        try:
            declarar_resultado(partida, usuario, options['resultado'], ganador.inscripcion)
        except ResultadoError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f'Resultado declarado por {usuario.username} en la partida {partida.pk}.'))