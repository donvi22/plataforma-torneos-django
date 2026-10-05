"""Programacion y check-in manual de partidas.

El procesador temporal se invoca explicitamente. Para automatizarlo en un
servidor real sera necesario conectarlo a cron, un scheduler o un worker.
"""

from datetime import timedelta
from threading import RLock

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from torneos.models import Torneo

from .models import CheckInPartida, HistorialProgramacionPartida, Partida


class CheckInError(Exception):
    """Error de negocio en horarios o disponibilidad."""


_scheduling_lock = RLock()


def _requiere_checkin(partida):
    return partida.torneo.tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL)


def _responsable(partida):
    arbitro = partida.arbitro_asignado
    if arbitro:
        if arbitro.estado_invitacion == 'ACEPTADA' and arbitro.activo_en_torneo:
            return arbitro.usuario
        return None
    return partida.torneo.organizador


def _checkins_requeridos(partida):
    participantes = list(partida.participantes.select_related('inscripcion__usuario'))
    ids = [(p.inscripcion.usuario_id, CheckInPartida.Tipo.PARTICIPANTE) for p in participantes]
    responsable = _responsable(partida)
    if responsable and (partida.arbitro_asignado_id or partida.torneo.tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL)):
        ids.append((responsable.pk, CheckInPartida.Tipo.ARBITRO))
    return ids


def _checkins_completos(partida):
    requeridos = _checkins_requeridos(partida)
    confirmados = set(
        partida.checkins.filter(confirmado=True).values_list('usuario_id', 'tipo')
    )
    return bool(requeridos) and _responsable(partida) is not None and all(item in confirmados for item in requeridos)


def preparar_partida(partida, ahora=None):
    """Abre el check-in cuando la partida ya puede prepararse."""
    ahora = ahora or timezone.now()
    with _scheduling_lock, transaction.atomic():
        partida = Partida.objects.select_related('torneo', 'arbitro_asignado').get(pk=partida.pk)
        if not _requiere_checkin(partida):
            return partida
        if hasattr(partida, 'resultado_oficial'):
            return partida
        if partida.estado in (Partida.Estado.FINALIZADA, Partida.Estado.CANCELADA, Partida.Estado.EN_CURSO):
            raise CheckInError('La partida ya no puede abrir su check-in.')
        if partida.torneo.tipo == Torneo.Tipo.PUBLICO:
            if partida.numero_ronda > 1:
                if partida.participantes.count() != 2:
                    return partida
                if not partida.fecha_hora_rivales_confirmados:
                    return partida
                descanso = partida.torneo.descanso_entre_partidas_min or 5
                if ahora < partida.fecha_hora_rivales_confirmados + timedelta(minutes=descanso):
                    return partida
            elif partida.torneo.estado != Torneo.Estado.PREPARADO:
                raise CheckInError('El bracket público todavía no está preparado.')
        elif partida.torneo.tipo == Torneo.Tipo.OFICIAL:
            if not partida.fecha_hora_programada:
                raise CheckInError('La partida oficial necesita una hora programada.')
            if partida.participantes.count() != 2:
                return partida
            if ahora < partida.fecha_hora_programada - timedelta(minutes=partida.torneo.duracion_checkin_min):
                return partida
        partida.estado = Partida.Estado.CHECK_IN
        partida.fecha_hora_apertura_checkin = ahora
        partida.save(update_fields=('estado', 'fecha_hora_apertura_checkin'))
        try:
            from notificaciones.models import Notificacion
            from notificaciones.services import crear_notificacion
            for usuario_id, tipo in _checkins_requeridos(partida):
                usuario = partida.torneo.organizador.__class__.objects.get(pk=usuario_id)
                crear_notificacion(usuario, Notificacion.Tipo.APERTURA_CHECKIN, 'Check-in abierto', f'Ya puedes confirmar tu disponibilidad para {partida}.', es_critica=True, clave_evento=f'checkin:{partida.pk}:{partida.fecha_hora_apertura_checkin}', torneo=partida.torneo, partida=partida)
        except Exception:
            pass
        return partida


def confirmar_checkin(partida, usuario, tipo, ahora=None):
    ahora = ahora or timezone.now()
    with _scheduling_lock, transaction.atomic():
        partida = Partida.objects.select_related('torneo', 'arbitro_asignado').get(pk=partida.pk)
        if not usuario or not usuario.puede_operar:
            raise CheckInError('La cuenta no está activa para confirmar el check-in.')
        if not _requiere_checkin(partida):
            raise CheckInError('Los torneos privados no requieren check-in.')
        if partida.estado != Partida.Estado.CHECK_IN:
            raise CheckInError('El check-in no está abierto.')
        if partida.fecha_hora_apertura_checkin is None:
            raise CheckInError('La partida no tiene una ventana de check-in.')
        duracion = partida.torneo.duracion_checkin_min
        if ahora > partida.fecha_hora_apertura_checkin + timedelta(minutes=duracion):
            raise CheckInError('La ventana de check-in ya terminó.')
        if tipo not in CheckInPartida.Tipo.values:
            raise CheckInError('Tipo de check-in no válido.')
        if tipo == CheckInPartida.Tipo.PARTICIPANTE and not partida.participantes.filter(
            inscripcion__usuario=usuario,
        ).exists():
            raise CheckInError('El usuario no participa en esta partida.')
        if tipo == CheckInPartida.Tipo.ARBITRO:
            responsable = _responsable(partida)
            if responsable is None or responsable.pk != usuario.pk:
                raise CheckInError('El usuario no es el responsable de esta partida.')
        checkin, creado = CheckInPartida.objects.get_or_create(
            partida=partida,
            usuario=usuario,
            tipo=tipo,
        )
        if checkin.confirmado:
            raise CheckInError('El usuario ya confirmó su disponibilidad.')
        checkin.confirmado = True
        checkin.fecha_confirmacion = ahora
        checkin.save(update_fields=('confirmado', 'fecha_confirmacion'))
        if _checkins_completos(partida):
            partida.estado = Partida.Estado.LISTA_PARA_COMENZAR
            partida.save(update_fields=('estado',))
            try:
                iniciar_partida(partida, ahora=ahora)
            except CheckInError:
                pass
        return checkin


def iniciar_partida(partida, ahora=None):
    ahora = ahora or timezone.now()
    with _scheduling_lock, transaction.atomic():
        partida = Partida.objects.select_related('torneo').get(pk=partida.pk)
        if partida.estado != Partida.Estado.LISTA_PARA_COMENZAR:
            raise CheckInError('La partida no está lista para comenzar.')
        if partida.torneo.tipo == Torneo.Tipo.OFICIAL:
            if not partida.fecha_hora_programada or ahora < partida.fecha_hora_programada:
                raise CheckInError('La partida oficial todavía no ha llegado a su hora programada.')
        elif partida.torneo.tipo == Torneo.Tipo.PUBLICO and partida.numero_ronda > 1:
            descanso = partida.torneo.descanso_entre_partidas_min or 5
            if partida.fecha_hora_rivales_confirmados and ahora < partida.fecha_hora_rivales_confirmados + timedelta(minutes=descanso):
                raise CheckInError('Todavía no ha terminado el descanso entre partidas.')
        partida.estado = Partida.Estado.EN_CURSO
        partida.fecha_hora_inicio_real = ahora
        partida.save(update_fields=('estado', 'fecha_hora_inicio_real'))
        from torneos.services import registrar_inicio_partida
        registrar_inicio_partida(partida.torneo)
        return partida


def detectar_ausencias(partida, ahora=None):
    ahora = ahora or timezone.now()
    partida = Partida.objects.select_related('torneo').get(pk=partida.pk)
    if partida.estado != Partida.Estado.CHECK_IN or not partida.fecha_hora_apertura_checkin:
        return {'participantes_ausentes': [], 'arbitro_ausente': False}
    if ahora <= partida.fecha_hora_apertura_checkin + timedelta(minutes=partida.torneo.duracion_checkin_min):
        return {'participantes_ausentes': [], 'arbitro_ausente': False}
    confirmados = set(partida.checkins.filter(confirmado=True).values_list('usuario_id', 'tipo'))
    participantes_ausentes = [
        p.inscripcion.usuario_id
        for p in partida.participantes.select_related('inscripcion')
        if (p.inscripcion.usuario_id, CheckInPartida.Tipo.PARTICIPANTE) not in confirmados
    ]
    responsable = _responsable(partida)
    return {
        'participantes_ausentes': participantes_ausentes,
        'arbitro_ausente': (responsable.pk, CheckInPartida.Tipo.ARBITRO) not in confirmados,
    }


def procesar_checkins(ahora=None):
    ahora = ahora or timezone.now()
    procesadas = 0
    for partida in Partida.objects.select_related('torneo').filter(
        estado__in=(Partida.Estado.PENDIENTE, Partida.Estado.PROGRAMADA),
    ):
        anterior = partida.estado
        try:
            preparar_partida(partida, ahora=ahora)
        except CheckInError:
            continue
        partida.refresh_from_db()
        if partida.estado != anterior:
            procesadas += 1
    return procesadas


def puede_programar_partidas(torneo, usuario):
    """Administración global autorizada u organizador operativo de un torneo oficial."""
    from torneos.services import _es_administrador_autorizado

    return bool(
        torneo.tipo == Torneo.Tipo.OFICIAL
        and usuario
        and getattr(usuario, 'is_authenticated', False)
        and (_es_administrador_autorizado(usuario) or (usuario.puede_operar and torneo.organizador_id == usuario.pk))
    )


ESTADOS_NO_REPROGRAMABLES = (
    Partida.Estado.EN_CURSO,
    Partida.Estado.PENDIENTE_VALIDACION,
    Partida.Estado.FINALIZADA,
    Partida.Estado.CANCELADA,
    Partida.Estado.INCIDENCIA,
)
MAX_ANTELACION_PROGRAMACION = timedelta(days=365)


def _destinatarios_programacion(partida, actor):
    destinatarios = {p.inscripcion.usuario for p in partida.participantes.select_related('inscripcion__usuario')}
    arbitro = partida.arbitro_asignado
    if arbitro and arbitro.estado_invitacion == 'ACEPTADA' and arbitro.activo_en_torneo:
        destinatarios.add(arbitro.usuario)
    if partida.torneo.organizador_id != actor.pk:
        destinatarios.add(partida.torneo.organizador)
    return destinatarios


def reprogramar_partida(partida, nueva_fecha, actor, motivo='', ahora=None):
    """Programa o reprograma una partida oficial; es el único punto de escritura de la hora."""
    ahora = ahora or timezone.now()
    motivo = (motivo or '').strip()
    with _scheduling_lock, transaction.atomic():
        partida = Partida.objects.select_related('torneo__organizador', 'arbitro_asignado__usuario').get(pk=partida.pk)
        torneo = partida.torneo
        if torneo.tipo != Torneo.Tipo.OFICIAL or not puede_programar_partidas(torneo, actor):
            raise CheckInError('Solo la administración o el organizador oficial pueden programar partidas.')
        if partida.estado in ESTADOS_NO_REPROGRAMABLES or hasattr(partida, 'resultado_oficial'):
            raise CheckInError('Esta partida ya no puede reprogramarse.')
        if torneo.estado in (Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO):
            raise CheckInError('El torneo ya no admite cambios de programación.')
        if not nueva_fecha or timezone.is_naive(nueva_fecha):
            raise CheckInError('La fecha debe incluir zona horaria.')
        if nueva_fecha < ahora:
            raise CheckInError('La fecha debe ser futura.')
        if nueva_fecha > ahora + MAX_ANTELACION_PROGRAMACION:
            raise CheckInError('La fecha está demasiado lejos en el futuro.')
        if torneo.fecha_inicio_prevista and nueva_fecha < torneo.fecha_inicio_prevista:
            raise CheckInError('La partida no puede empezar antes del inicio previsto del torneo.')
        if torneo.fecha_fin_prevista and nueva_fecha > torneo.fecha_fin_prevista:
            raise CheckInError('La partida no puede programarse después del fin previsto del torneo.')
        anterior = partida.fecha_hora_programada
        if anterior == nueva_fecha:
            return partida
        if anterior and not motivo:
            raise CheckInError('El motivo de reprogramación es obligatorio.')
        # La confirmación de una ventana anterior no vale para la nueva hora.
        invalidados = partida.checkins.filter(confirmado=True).update(confirmado=False, fecha_confirmacion=None)
        partida.fecha_hora_programada = nueva_fecha
        partida.fecha_hora_apertura_checkin = None
        partida.estado = Partida.Estado.PROGRAMADA
        partida.save(update_fields=('fecha_hora_programada', 'fecha_hora_apertura_checkin', 'estado'))
        historial = HistorialProgramacionPartida.objects.create(
            partida=partida,
            fecha_anterior=anterior,
            fecha_nueva=nueva_fecha,
            actor=actor,
            motivo=motivo or 'Programación inicial.',
            checkins_invalidados=invalidados,
        )
        from notificaciones.models import Notificacion
        from notificaciones.services import crear_notificacion
        titulo = 'Partida reprogramada' if anterior else 'Partida programada'
        cuando = timezone.localtime(nueva_fecha).strftime('%d/%m/%Y %H:%M')
        for usuario in _destinatarios_programacion(partida, actor):
            crear_notificacion(
                usuario, Notificacion.Tipo.CAMBIO_HORARIO, titulo,
                f'La partida {partida} está programada para el {cuando}.',
                es_critica=True, clave_evento=f'horario:{historial.pk}', torneo=torneo, partida=partida,
            )
    try:
        preparar_partida(partida, ahora=ahora)
    except CheckInError:
        pass
    partida.refresh_from_db()
    return partida


programar_partida = reprogramar_partida
