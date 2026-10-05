from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from torneos.models import Torneo

from .forms import LobbyPartidaForm, ProgramarPartidaForm
from .lobby import actualizar_lobby, puede_editar_lobby
from .models import Partida
from .scheduling import CheckInError, puede_programar_partidas, reprogramar_partida


def _solo_post(request):
    if request.method != 'POST':
        raise Http404


@login_required
def calendario_partidas(request, torneo_pk):
    torneo = get_object_or_404(Torneo.objects.select_related('videojuego'), pk=torneo_pk, tipo=Torneo.Tipo.OFICIAL)
    if not puede_programar_partidas(torneo, request.user):
        raise Http404
    partidas = list(
        torneo.partidas.select_related('arbitro_asignado__usuario').prefetch_related('participantes__inscripcion__usuario'),
    )
    programadas = sorted((p for p in partidas if p.fecha_hora_programada), key=lambda p: (p.fecha_hora_programada, p.pk))
    sin_programar = [p for p in partidas if not p.fecha_hora_programada]
    filas = []
    for partida in programadas + sin_programar:
        participantes = sorted(partida.participantes.all(), key=lambda p: p.posicion)
        editable = partida.estado not in (
            Partida.Estado.EN_CURSO, Partida.Estado.PENDIENTE_VALIDACION,
            Partida.Estado.FINALIZADA, Partida.Estado.CANCELADA, Partida.Estado.INCIDENCIA,
        )
        filas.append({
            'partida': partida,
            'inscripciones': [p.inscripcion for p in participantes],
            'editable': editable,
            'form': ProgramarPartidaForm(initial={'fecha_hora': partida.fecha_hora_programada}) if editable else None,
        })
    return render(request, 'partidas/calendario.html', {
        'torneo': torneo, 'filas': filas, 'ahora': timezone.now(),
        'sin_programar': len(sin_programar),
    })


@login_required
def programar_partida_web(request, pk):
    _solo_post(request)
    partida = get_object_or_404(Partida.objects.select_related('torneo'), pk=pk)
    if not puede_programar_partidas(partida.torneo, request.user):
        raise Http404
    form = ProgramarPartidaForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'La fecha y hora no son válidas.')
    else:
        try:
            reprogramar_partida(partida, form.cleaned_data['fecha_hora'], request.user, form.cleaned_data['motivo'])
        except CheckInError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, 'La programación de la partida se ha guardado.')
    return redirect('calendario-partidas', torneo_pk=partida.torneo_id)


@login_required
def editar_lobby_partida(request, pk):
    _solo_post(request)
    partida = get_object_or_404(Partida.objects.select_related('torneo', 'arbitro_asignado'), pk=pk)
    if not puede_editar_lobby(partida, request.user):
        raise Http404
    form = LobbyPartidaForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Los datos de la partida no son válidos.')
    else:
        datos = form.cleaned_data
        try:
            actualizar_lobby(
                partida, request.user, nombre=datos['nombre_lobby'], codigo=datos['codigo_lobby'],
                contrasena=datos['contrasena_lobby'], instrucciones=datos['instrucciones_lobby'],
                quitar_contrasena=datos['quitar_contrasena'],
            )
        except ValidationError:
            messages.error(request, 'No se pudo guardar la información de la partida.')
        else:
            messages.success(request, 'La información de la partida se ha guardado.')
    return redirect('detalle-partida', pk=partida.pk)
