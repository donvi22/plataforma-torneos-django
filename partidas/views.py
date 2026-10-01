from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from torneos.models import InscripcionTorneo
from torneos.public_views import PUBLIC_STATES, _puede_ver_privado

from .models import CheckInPartida, Partida, ResultadoPartida
from .scheduling import CheckInError, confirmar_checkin
from .services import ResultadoError, _usuario_puede_validar, declarar_resultado, validar_resultado


def _puede_ver_partida(partida, usuario):
    torneo = partida.torneo
    if torneo.tipo == torneo.Tipo.PRIVADO or torneo.estado == torneo.Estado.BORRADOR:
        return _puede_ver_privado(torneo, usuario)
    return (
        torneo.tipo in (torneo.Tipo.PUBLICO, torneo.Tipo.OFICIAL)
        and torneo.fecha_publicacion is not None
        and torneo.estado in PUBLIC_STATES
        and torneo.videojuego.activo
    )


def _preparar_participante(participante):
    return participante.inscripcion.nick_historico


def _tipo_checkin_autorizado(partida, usuario):
    if not usuario or not usuario.is_authenticated:
        return None
    if partida.participantes.filter(inscripcion__usuario=usuario).exists():
        return CheckInPartida.Tipo.PARTICIPANTE
    arbitro = partida.arbitro_asignado
    if arbitro and arbitro.usuario_id == usuario.pk and arbitro.estado_invitacion == 'ACEPTADA' and arbitro.activo_en_torneo:
        return CheckInPartida.Tipo.ARBITRO
    if not arbitro and partida.torneo.organizador_id == usuario.pk:
        return CheckInPartida.Tipo.ARBITRO
    return None


def _puede_declarar_resultado(partida, usuario):
    return bool(
        usuario
        and partida.estado in (Partida.Estado.EN_CURSO, Partida.Estado.PENDIENTE_VALIDACION)
        and not hasattr(partida, 'resultado_oficial')
        and partida.participantes.filter(inscripcion__usuario=usuario).exists()
    )


def _declaraciones_coinciden(declaraciones):
    return (
        len(declaraciones) == 2
        and declaraciones[0].resultado_declarado == declaraciones[1].resultado_declarado
        and declaraciones[0].ganador_declarado_id == declaraciones[1].ganador_declarado_id
    )


def _preparar_partida(partida, usuario=None):
    participantes = {p.posicion: _preparar_participante(p) for p in partida.participantes.all()}
    resultado = getattr(partida, 'resultado_oficial', None)
    ids_usuario = {p.inscripcion.usuario_id for p in partida.participantes.all()}
    arbitro = partida.arbitro_asignado
    es_participante = usuario and usuario.pk in ids_usuario
    es_arbitro = usuario and arbitro and arbitro.usuario_id == usuario.pk and arbitro.estado_invitacion == 'ACEPTADA' and arbitro.activo_en_torneo
    tipo_checkin = _tipo_checkin_autorizado(partida, usuario)
    checkin = None
    if tipo_checkin:
        checkin = partida.checkins.filter(usuario=usuario, tipo=tipo_checkin, confirmado=True).first()
    ahora = timezone.now()
    fin_checkin = None
    if partida.fecha_hora_apertura_checkin:
        fin_checkin = partida.fecha_hora_apertura_checkin + timedelta(minutes=partida.torneo.duracion_checkin_min)
    apertura_prevista = None
    if partida.numero_ronda > 1 and partida.fecha_hora_rivales_confirmados:
        apertura_prevista = partida.fecha_hora_rivales_confirmados + timedelta(
            minutes=partida.torneo.descanso_entre_partidas_min or 5,
        )
    ventana_abierta = bool(
        tipo_checkin
        and partida.estado == Partida.Estado.CHECK_IN
        and partida.fecha_hora_apertura_checkin
        and ahora <= fin_checkin
    )
    puede_revisar = bool(_usuario_puede_validar(partida, usuario))
    declaraciones = list(partida.declaraciones_resultado.select_related('usuario', 'ganador_declarado').order_by('fecha'))
    declaracion_usuario = next((declaracion for declaracion in declaraciones if usuario and declaracion.usuario_id == usuario.pk), None)
    return {
        'partida': partida,
        'participantes': participantes,
        'resultado': resultado,
        'ganador': resultado.ganador.nick_historico if resultado and resultado.ganador_id else None,
        'es_participante': bool(es_participante),
        'es_arbitro': bool(es_arbitro),
        'es_organizador': bool(usuario and partida.torneo.organizador_id == usuario.pk),
        'checkin': checkin,
        'puede_confirmar_checkin': ventana_abierta and checkin is None,
        'fin_checkin': fin_checkin,
        'checkin_vencido': bool(fin_checkin and ahora > fin_checkin),
        'apertura_prevista': apertura_prevista,
        'puede_declarar_resultado': _puede_declarar_resultado(partida, usuario) and declaracion_usuario is None,
        'puede_validar_resultado': puede_revisar and partida.estado in (Partida.Estado.EN_CURSO, Partida.Estado.PENDIENTE_VALIDACION) and not resultado,
        'declaracion_usuario': declaracion_usuario,
        'declaraciones_responsable': declaraciones if puede_revisar else [],
        'declaraciones_coinciden': _declaraciones_coinciden(declaraciones),
        'declaraciones_discrepan': len(declaraciones) == 2 and not _declaraciones_coinciden(declaraciones),
    }


def _queryset_partidas():
    return Partida.objects.select_related(
        'torneo__videojuego', 'torneo__organizador', 'arbitro_asignado__usuario',
    ).prefetch_related(
        'participantes__inscripcion', 'checkins', 'resultado_oficial__ganador',
        'declaraciones_resultado__usuario', 'declaraciones_resultado__ganador_declarado',
    )


@login_required
def mis_partidas(request):
    partidas = _queryset_partidas().filter(
        participantes__inscripcion__usuario=request.user,
        participantes__inscripcion__estado=InscripcionTorneo.Estado.CONFIRMADA,
    ).distinct().order_by('torneo_id', 'numero_ronda', 'numero_orden')
    return render(request, 'partidas/mis_partidas.html', {
        'partidas': [_preparar_partida(partida, request.user) for partida in partidas],
    })


def detalle_partida(request, pk):
    partida = get_object_or_404(_queryset_partidas(), pk=pk)
    if not _puede_ver_partida(partida, request.user):
        raise Http404
    return render(request, 'partidas/detalle.html', {
        'detalle': _preparar_partida(partida, request.user if request.user.is_authenticated else None),
    })


@login_required
def confirmar_checkin_partida(request, pk):
    if request.method != 'POST':
        raise Http404
    partida = get_object_or_404(_queryset_partidas(), pk=pk)
    if not _puede_ver_partida(partida, request.user):
        raise Http404
    tipo = _tipo_checkin_autorizado(partida, request.user)
    if not tipo:
        raise Http404
    try:
        confirmar_checkin(partida, request.user, tipo)
    except CheckInError:
        pass
    return redirect('detalle-partida', pk=partida.pk)


def _partida_y_participantes_para_accion(request, pk):
    partida = get_object_or_404(_queryset_partidas(), pk=pk)
    if not _puede_ver_partida(partida, request.user):
        raise Http404
    participantes = {
        str(participante.inscripcion_id): participante.inscripcion
        for participante in partida.participantes.all()
    }
    return partida, participantes


@login_required
def declarar_resultado_partida(request, pk):
    if request.method != 'POST':
        raise Http404
    partida, participantes = _partida_y_participantes_para_accion(request, pk)
    if not _puede_declarar_resultado(partida, request.user):
        raise Http404
    ganador = participantes.get(request.POST.get('ganador', ''))
    if ganador is None:
        messages.error(request, 'Selecciona un ganador válido de esta partida.')
    else:
        try:
            declarar_resultado(partida, request.user, request.POST.get('resultado', ''), ganador)
        except ResultadoError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, 'Resultado declarado y pendiente de revisión.')
    return redirect('detalle-partida', pk=partida.pk)


@login_required
def validar_resultado_partida(request, pk):
    if request.method != 'POST':
        raise Http404
    partida, participantes = _partida_y_participantes_para_accion(request, pk)
    if not _usuario_puede_validar(partida, request.user):
        raise Http404
    ganador = participantes.get(request.POST.get('ganador', ''))
    motivo = request.POST.get('motivo', '').strip()
    declaraciones = list(partida.declaraciones_resultado.all())
    if ganador is None:
        messages.error(request, 'Selecciona un ganador válido de esta partida.')
    elif not _declaraciones_coinciden(declaraciones) and not motivo:
        messages.error(request, 'Una revisión sin declaraciones coincidentes requiere un motivo explícito.')
    else:
        try:
            validar_resultado(
                partida, request.user, request.POST.get('resultado', '').strip(), ganador,
                tipo_resultado=ResultadoPartida.TipoResultado.NORMAL, motivo=motivo,
            )
        except ResultadoError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, 'Resultado oficial validado.')
    return redirect('detalle-partida', pk=partida.pk)
