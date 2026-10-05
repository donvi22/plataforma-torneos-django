"""Acceso a los datos de lobby de una partida.

Política de visibilidad (MVP):
- Organizador y administración autorizada: siempre.
- Árbitro responsable aceptado y activo: siempre que esté asignado.
- Jugadores confirmados: solo cuando la partida ya tiene a sus dos participantes y,
  según el tipo de torneo:
  - OFICIAL: desde 30 minutos antes de la hora programada.
  - PUBLICO: mientras el check-in está abierto o la partida está en juego.
  - PRIVADO: con el torneo cerrado a inscripciones, preparado o en curso.
  Nunca una vez finalizada o cancelada la partida.
- Cualquier otra persona: nunca. Los participantes de rondas futuras ganan acceso
  solo al ser asignados a la partida, porque la regla se evalúa sobre los
  participantes actuales y no sobre permisos copiados.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from torneos.models import InscripcionTorneo, Torneo

from .models import Partida

LOBBY_ANTELACION_OFICIAL_MIN = 30
ESTADOS_PUBLICO_VISIBLE = (
    Partida.Estado.CHECK_IN,
    Partida.Estado.LISTA_PARA_COMENZAR,
    Partida.Estado.EN_CURSO,
    Partida.Estado.PENDIENTE_VALIDACION,
)
ESTADOS_PRIVADO_VISIBLE = (
    Torneo.Estado.INSCRIPCIONES_CERRADAS,
    Torneo.Estado.PREPARADO,
    Torneo.Estado.EN_CURSO,
)


def lobby_configurado(partida):
    return any((partida.nombre_lobby, partida.codigo_lobby, partida.contrasena_lobby, partida.instrucciones_lobby))


def puede_editar_lobby(partida, usuario):
    """Solo organizador o administración autorizada; el árbitro no edita en el MVP."""
    from torneos.services import _es_administrador_autorizado

    if not usuario or not getattr(usuario, 'is_authenticated', False) or not usuario.puede_operar:
        return False
    if partida.estado in (Partida.Estado.FINALIZADA, Partida.Estado.CANCELADA):
        return False
    return partida.torneo.organizador_id == usuario.pk or _es_administrador_autorizado(usuario)


def puede_ver_lobby(partida, usuario, ahora=None):
    from torneos.services import _es_administrador_autorizado

    if not usuario or not getattr(usuario, 'is_authenticated', False) or not usuario.puede_operar:
        return False
    torneo = partida.torneo
    if torneo.organizador_id == usuario.pk or _es_administrador_autorizado(usuario):
        return True
    arbitro = partida.arbitro_asignado
    if arbitro and arbitro.usuario_id == usuario.pk and arbitro.estado_invitacion == 'ACEPTADA' and arbitro.activo_en_torneo:
        return True
    if partida.estado in (Partida.Estado.FINALIZADA, Partida.Estado.CANCELADA):
        return False
    participantes = list(partida.participantes.select_related('inscripcion'))
    if len(participantes) != 2 or not any(
        p.inscripcion.usuario_id == usuario.pk and p.inscripcion.estado == InscripcionTorneo.Estado.CONFIRMADA
        for p in participantes
    ):
        return False
    ahora = ahora or timezone.now()
    if torneo.tipo == Torneo.Tipo.OFICIAL:
        return bool(
            partida.fecha_hora_programada
            and ahora >= partida.fecha_hora_programada - timedelta(minutes=LOBBY_ANTELACION_OFICIAL_MIN)
        )
    if torneo.tipo == Torneo.Tipo.PUBLICO:
        return partida.estado in ESTADOS_PUBLICO_VISIBLE
    return torneo.estado in ESTADOS_PRIVADO_VISIBLE


def actualizar_lobby(partida, actor, *, nombre, codigo, contrasena=None, instrucciones, quitar_contrasena=False):
    """Guarda los datos de lobby; una contraseña vacía conserva la actual y no se historiza."""
    if not puede_editar_lobby(partida, actor):
        raise ValidationError('No puedes editar la información de esta partida.')
    partida.nombre_lobby = nombre
    partida.codigo_lobby = codigo
    partida.instrucciones_lobby = instrucciones
    campos = ['nombre_lobby', 'codigo_lobby', 'instrucciones_lobby']
    if quitar_contrasena:
        partida.contrasena_lobby = ''
        campos.append('contrasena_lobby')
    elif contrasena:
        partida.contrasena_lobby = contrasena
        campos.append('contrasena_lobby')
    partida.full_clean(exclude=('siguiente_partida',))
    partida.save(update_fields=campos)
    return partida
