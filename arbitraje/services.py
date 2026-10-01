"""Servicios de arbitraje.

Las operaciones que consumen plazas usan un RLock y una transaccion atomica.
En SQLite esto serializa llamadas del mismo proceso y aprovecha el bloqueo de
escritura del motor, pero no equivale a bloqueos de fila entre varios procesos.
Para produccion con concurrencia multiproceso se recomienda PostgreSQL con
select_for_update.
"""

from contextlib import contextmanager
from threading import RLock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from torneos.models import InscripcionTorneo, Torneo
from torneos.services import _es_administrador_autorizado

from .models import ArbitroTorneo, HistorialAsignacionArbitro


class ArbitrajeError(Exception):
    """Error de negocio en una operacion arbitral."""


_arbitraje_lock = RLock()


@contextmanager
def _bloqueo_arbitraje():
    with _arbitraje_lock:
        with transaction.atomic():
            yield


def plazas_arbitrales_necesarias(max_participantes):
    if max_participantes <= 8:
        return 1
    if max_participantes <= 16:
        return 2
    if max_participantes <= 32:
        return 3
    if max_participantes <= 48:
        return 4
    if max_participantes <= 64:
        return 5
    if max_participantes <= 96:
        return 6
    if max_participantes <= 128:
        return 8
    raise ArbitrajeError('El tamaño del torneo no tiene una configuración arbitral válida.')


def _autorizado_torneo(torneo, usuario):
    return bool(
        usuario
        and usuario.is_active
        and (torneo.organizador_id == usuario.pk or _es_administrador_autorizado(usuario))
    )


def _requisitos_publicos(torneo, usuario):
    if not usuario.is_active:
        raise ArbitrajeError('La cuenta del árbitro no está activa.')
    if torneo.tipo == Torneo.Tipo.PUBLICO:
        if not usuario.disponible_para_arbitrar:
            raise ArbitrajeError('El usuario no está disponible para arbitrar.')
        if usuario.karma_total < 150:
            raise ArbitrajeError('El árbitro necesita al menos 150 puntos de Karma.')


def _invitacion_existente(torneo, usuario):
    return ArbitroTorneo.objects.filter(torneo=torneo, usuario=usuario).first()


def _tiene_participacion_pendiente(torneo, usuario):
    return InscripcionTorneo.objects.filter(
        torneo=torneo,
        usuario=usuario,
        participaciones_partida__partida__estado__in=(
            'PENDIENTE',
            'PROGRAMADA',
            'CHECK_IN',
            'LISTA_PARA_COMENZAR',
            'EN_CURSO',
            'PENDIENTE_VALIDACION',
            'INCIDENCIA',
        ),
    ).exists()


def invitar_arbitro(torneo, usuario, actor):
    if torneo.tipo == Torneo.Tipo.PRIVADO:
        raise ArbitrajeError('Los torneos privados no utilizan arbitraje formal.')
    if torneo.tipo == Torneo.Tipo.OFICIAL and not _es_administrador_autorizado(actor):
        raise ArbitrajeError('Solo un administrador puede invitar árbitros oficiales.')
    if not _autorizado_torneo(torneo, actor):
        raise ArbitrajeError('Solo el organizador o un administrador puede invitar árbitros.')
    _requisitos_publicos(torneo, usuario)
    if InscripcionTorneo.objects.filter(
        torneo=torneo,
        usuario=usuario,
        estado=InscripcionTorneo.Estado.CONFIRMADA,
    ).exists():
        raise ArbitrajeError('Un participante confirmado no puede ser invitado como árbitro.')
    if _tiene_participacion_pendiente(torneo, usuario):
        raise ArbitrajeError('El usuario tiene una participación competitiva pendiente.')

    with _bloqueo_arbitraje():
        existente = _invitacion_existente(torneo, usuario)
        if existente and existente.estado_invitacion in (
            ArbitroTorneo.EstadoInvitacion.PENDIENTE,
            ArbitroTorneo.EstadoInvitacion.ACEPTADA,
        ):
            raise ArbitrajeError('Ya existe una invitación activa para este usuario.')
        if torneo.tipo == Torneo.Tipo.PUBLICO:
            limite_pendientes = plazas_arbitrales_necesarias(torneo.max_participantes) * 2
            pendientes = ArbitroTorneo.objects.filter(
                torneo=torneo,
                estado_invitacion=ArbitroTorneo.EstadoInvitacion.PENDIENTE,
            ).count()
            if pendientes >= limite_pendientes:
                raise ArbitrajeError('Se alcanzó el límite de invitaciones arbitrales pendientes.')
        ahora = timezone.now()
        if existente:
            existente.estado_invitacion = ArbitroTorneo.EstadoInvitacion.PENDIENTE
            existente.fecha_invitacion = ahora
            existente.fecha_respuesta = None
            existente.activo_en_torneo = False
            existente.fecha_salida = None
            existente.motivo_salida = ''
            existente.save()
            return existente
        try:
            invitacion = ArbitroTorneo.objects.create(
                torneo=torneo,
                usuario=usuario,
                fecha_invitacion=ahora,
            )
            try:
                from notificaciones.models import Notificacion
                from notificaciones.services import crear_notificacion
                crear_notificacion(usuario, Notificacion.Tipo.INVITACION_ARBITRAL, 'Invitación para arbitrar', f'Has sido invitado a arbitrar {torneo.nombre}.', es_critica=True, clave_evento=f'invitacion:{invitacion.pk}:pendiente', torneo=torneo, invitacion_arbitral=invitacion)
            except Exception:
                pass
            return invitacion
        except IntegrityError as error:
            raise ArbitrajeError('Ya existe una invitación para este usuario y torneo.') from error


def aceptar_invitacion(invitacion, usuario):
    with _bloqueo_arbitraje():
        invitacion = ArbitroTorneo.objects.select_related('torneo', 'usuario').get(pk=invitacion.pk)
        if invitacion.usuario_id != usuario.pk:
            raise ArbitrajeError('Solo el usuario invitado puede aceptar la invitación.')
        if invitacion.estado_invitacion != ArbitroTorneo.EstadoInvitacion.PENDIENTE:
            raise ArbitrajeError('La invitación ya no está pendiente.')
        _requisitos_publicos(invitacion.torneo, usuario)
        if InscripcionTorneo.objects.filter(
            torneo=invitacion.torneo,
            usuario=usuario,
            estado=InscripcionTorneo.Estado.CONFIRMADA,
        ).exists():
            raise ArbitrajeError('Un participante confirmado no puede aceptar ser árbitro.')
        if _tiene_participacion_pendiente(invitacion.torneo, usuario):
            raise ArbitrajeError('El usuario tiene una participación competitiva pendiente.')
        if invitacion.torneo.tipo == Torneo.Tipo.PUBLICO:
            limite = plazas_arbitrales_necesarias(invitacion.torneo.max_participantes)
            aceptados = ArbitroTorneo.objects.filter(
                torneo=invitacion.torneo,
                estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA,
                activo_en_torneo=True,
            ).exclude(pk=invitacion.pk).count()
            if aceptados >= limite:
                raise ArbitrajeError('No quedan plazas arbitrales disponibles.')
        invitacion.estado_invitacion = ArbitroTorneo.EstadoInvitacion.ACEPTADA
        invitacion.fecha_respuesta = timezone.now()
        invitacion.activo_en_torneo = True
        invitacion.save()
        try:
            from notificaciones.models import Notificacion
            from notificaciones.services import crear_notificacion
            crear_notificacion(invitacion.torneo.organizador, Notificacion.Tipo.RESPUESTA_INVITACION, 'Respuesta de árbitro', f'{usuario.username} ha aceptado la invitación arbitral.', es_critica=True, clave_evento=f'invitacion:{invitacion.pk}:aceptada', torneo=invitacion.torneo, invitacion_arbitral=invitacion)
        except Exception:
            pass
        if invitacion.torneo.tipo == Torneo.Tipo.PUBLICO:
            ArbitroTorneo.objects.filter(
                torneo=invitacion.torneo,
                estado_invitacion=ArbitroTorneo.EstadoInvitacion.PENDIENTE,
            ).update(
                estado_invitacion=ArbitroTorneo.EstadoInvitacion.CANCELADA,
                fecha_respuesta=timezone.now(),
            ) if ArbitroTorneo.objects.filter(
                torneo=invitacion.torneo,
                estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA,
                activo_en_torneo=True,
            ).count() >= plazas_arbitrales_necesarias(invitacion.torneo.max_participantes) else None
        return invitacion


def rechazar_invitacion(invitacion, usuario):
    with _bloqueo_arbitraje():
        invitacion = ArbitroTorneo.objects.get(pk=invitacion.pk)
        if invitacion.usuario_id != usuario.pk:
            raise ArbitrajeError('Solo el usuario invitado puede rechazar la invitación.')
        if invitacion.estado_invitacion != ArbitroTorneo.EstadoInvitacion.PENDIENTE:
            raise ArbitrajeError('La invitación ya no está pendiente.')
        invitacion.estado_invitacion = ArbitroTorneo.EstadoInvitacion.RECHAZADA
        invitacion.fecha_respuesta = timezone.now()
        invitacion.activo_en_torneo = False
        invitacion.save()
        try:
            from notificaciones.models import Notificacion
            from notificaciones.services import crear_notificacion
            crear_notificacion(invitacion.torneo.organizador, Notificacion.Tipo.RESPUESTA_INVITACION, 'Respuesta de árbitro', f'{usuario.username} ha rechazado la invitación arbitral.', es_critica=True, clave_evento=f'invitacion:{invitacion.pk}:rechazada', torneo=invitacion.torneo, invitacion_arbitral=invitacion)
        except Exception:
            pass
        return invitacion


def _arbitro_valido(partida, arbitro):
    return bool(
        arbitro
        and partida.torneo.tipo != Torneo.Tipo.PRIVADO
        and arbitro.torneo_id == partida.torneo_id
        and arbitro.estado_invitacion == ArbitroTorneo.EstadoInvitacion.ACEPTADA
        and arbitro.activo_en_torneo
    )


def asignar_arbitro(partida, actor=None, arbitro=None):
    from partidas.models import Partida

    with _bloqueo_arbitraje():
        partida = Partida.objects.select_related('torneo', 'arbitro_asignado').get(pk=partida.pk)
        if partida.arbitro_asignado and _arbitro_valido(partida, partida.arbitro_asignado):
            return partida.arbitro_asignado
        if arbitro and actor and arbitro.usuario_id == actor.pk:
            raise ArbitrajeError('Un árbitro no puede autoasignarse la partida.')
        if arbitro:
            arbitro = ArbitroTorneo.objects.get(pk=arbitro.pk)
        disponibles = list(ArbitroTorneo.objects.filter(
            torneo=partida.torneo,
            estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA,
            activo_en_torneo=True,
        ))
        if arbitro:
            if arbitro not in disponibles:
                raise ArbitrajeError('El árbitro seleccionado no está activo en este torneo.')
            candidatos = [arbitro]
        else:
            if not disponibles:
                return None
            if actor:
                disponibles = [candidato for candidato in disponibles if candidato.usuario_id != actor.pk]
                if not disponibles:
                    return None
            cargas = {
                candidato.pk: candidato.partidas_asignadas.exclude(
                    estado=Partida.Estado.FINALIZADA,
                ).exclude(
                    estado=Partida.Estado.CANCELADA,
                ).count()
                for candidato in disponibles
            }
            carga_minima = min(cargas.values())
            candidatos = [candidato for candidato in disponibles if cargas[candidato.pk] == carga_minima]
            import random
            arbitro = random.choice(candidatos)
        if not _arbitro_valido(partida, arbitro):
            raise ArbitrajeError('El árbitro seleccionado no es válido.')
        anterior = partida.arbitro_asignado
        partida.arbitro_asignado = arbitro
        try:
            partida.full_clean()
            partida.save(update_fields=('arbitro_asignado',))
        except ValidationError as error:
            raise ArbitrajeError('; '.join(error.messages)) from error
        HistorialAsignacionArbitro.objects.create(
            partida=partida,
            arbitro_anterior=anterior,
            arbitro_nuevo=arbitro,
            actor=actor,
            motivo='Asignación automática.' if actor is None else 'Asignación autorizada.',
        )
        try:
            from notificaciones.models import Notificacion
            from notificaciones.services import crear_notificacion
            crear_notificacion(arbitro.usuario, Notificacion.Tipo.ASIGNACION_PARTIDA, 'Partida asignada', f'Has sido asignado a {partida}.', es_critica=True, clave_evento=f'asignacion:{partida.pk}:{arbitro.pk}:{partida.fecha_actualizacion if hasattr(partida, "fecha_actualizacion") else partida.pk}', torneo=partida.torneo, partida=partida)
        except Exception:
            pass
        return arbitro


def solicitar_reasignacion(partida, arbitro, motivo):
    from partidas.models import Partida
    partida = Partida.objects.get(pk=partida.pk)
    if partida.arbitro_asignado_id != arbitro.pk:
        raise ArbitrajeError('El árbitro no está asignado a esta partida.')
    if not motivo or not motivo.strip():
        raise ArbitrajeError('El motivo de reasignación es obligatorio.')
    return {'partida': partida, 'arbitro': arbitro, 'motivo': motivo}


def reasignar_arbitro(partida, nuevo_arbitro, actor, motivo):
    from partidas.models import Partida

    if not motivo or not motivo.strip():
        raise ArbitrajeError('El motivo de reasignación es obligatorio.')
    with _bloqueo_arbitraje():
        partida = Partida.objects.select_related('torneo', 'arbitro_asignado').get(pk=partida.pk)
        if not _autorizado_torneo(partida.torneo, actor):
            raise ArbitrajeError('Solo el organizador o un administrador puede reasignar partidas.')
        nuevo_arbitro = ArbitroTorneo.objects.get(pk=nuevo_arbitro.pk)
        if not _arbitro_valido(partida, nuevo_arbitro):
            raise ArbitrajeError('El nuevo árbitro no está activo en este torneo.')
        anterior = partida.arbitro_asignado
        if anterior and anterior.pk == nuevo_arbitro.pk:
            raise ArbitrajeError('La partida ya está asignada a ese árbitro.')
        partida.arbitro_asignado = nuevo_arbitro
        partida.save(update_fields=('arbitro_asignado',))
        HistorialAsignacionArbitro.objects.create(
            partida=partida,
            arbitro_anterior=anterior,
            arbitro_nuevo=nuevo_arbitro,
            actor=actor,
            motivo=motivo,
        )
        return partida


def solicitar_salida(arbitro, motivo):
    if not motivo or not motivo.strip():
        raise ArbitrajeError('El motivo de salida es obligatorio.')
    arbitro.activo_en_torneo = False
    arbitro.fecha_salida = timezone.now()
    arbitro.motivo_salida = motivo
    arbitro.save(update_fields=('activo_en_torneo', 'fecha_salida', 'motivo_salida'))
    return arbitro
