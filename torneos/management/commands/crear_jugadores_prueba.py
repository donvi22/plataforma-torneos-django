import secrets
import string
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from torneos.models import InscripcionTorneo, Torneo
from torneos.services import EstadoInscripciones, InscripcionError, estado_inscripciones, inscribir_usuario
from usuarios.models import Usuario
from videojuegos.models import PerfilVideojuegoUsuario, RangoVideojuego


class Command(BaseCommand):
    help = 'Crea jugadores ficticios locales y opcionalmente los inscribe mediante el servicio real.'
    max_cantidad = 128

    def add_arguments(self, parser):
        parser.add_argument('--cantidad', type=int, required=True)
        parser.add_argument('--torneo', type=int)

    def handle(self, *args, **options):
        if not settings.DEBUG or not settings.DEV_LOCAL:
            raise CommandError('Este comando requiere DEBUG=True y TORNEO_DEV_LOCAL=1.')
        cantidad = options['cantidad']
        if cantidad < 1 or cantidad > self.max_cantidad:
            raise CommandError(f'La cantidad debe estar entre 1 y {self.max_cantidad}.')

        torneo = None
        if options.get('torneo'):
            try:
                torneo = Torneo.objects.select_related('videojuego').get(pk=options['torneo'])
            except Torneo.DoesNotExist as error:
                raise CommandError('El torneo indicado no existe.') from error
            libres = torneo.max_participantes - torneo.participantes_confirmados
            cantidad = min(cantidad, max(libres, 0))
            if cantidad == 0:
                self.stdout.write('El torneo ya está completo; no se crearon usuarios.')
                return
            if estado_inscripciones(torneo) not in (EstadoInscripciones.ABIERTAS, EstadoInscripciones.PRORROGA):
                raise CommandError('El torneo no tiene las inscripciones abiertas.')
            if torneo.nivel_minimo and torneo.nivel_minimo > 5:
                raise CommandError('El nivel mínimo del torneo supera el nivel de los jugadores de prueba.')
            if torneo.tipo != Torneo.Tipo.PRIVADO and torneo.rango_minimo_id:
                rango = torneo.rango_minimo
            else:
                rango = None

        creados = 0
        inscritos = 0
        for _ in range(cantidad):
            sufijo = uuid4().hex[:12]
            username = f'bot_prueba_{sufijo}'
            email = f'{username}@example.invalid'
            password = secrets.token_urlsafe(32)
            try:
                with transaction.atomic():
                    usuario = Usuario(username=username, email=email, nivel=5, karma_total=150)
                    usuario.set_password(password)
                    usuario.save()
                    creados += 1
                    if torneo:
                        perfil = PerfilVideojuegoUsuario.objects.create(
                            usuario=usuario,
                            videojuego=torneo.videojuego,
                            nick_en_juego=username,
                            rango_declarado=rango,
                        )
                        inscribir_usuario(torneo, usuario)
                        inscritos += 1
            except InscripcionError as error:
                self.stdout.write(self.style.WARNING(f'No se pudo inscribir un jugador de prueba: {error}'))
        self.stdout.write(self.style.SUCCESS(f'Usuarios creados: {creados}. Inscripciones confirmadas: {inscritos}.'))