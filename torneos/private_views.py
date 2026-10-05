from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import AccesoTorneoPrivado, InscripcionTorneo, Torneo
from .privados import (
    acceder_con_codigo,
    inscribir_en_privado,
    regenerar_codigo_privado,
    revocar_acceso_privado,
    tiene_acceso_privado,
)
from .services import InscripcionError


def _solo_post(request):
    return None if request.method == 'POST' else HttpResponseNotAllowed(['POST'])


def _torneo_privado_propio(request, pk):
    torneo = get_object_or_404(Torneo.objects.select_related('videojuego'), pk=pk, tipo=Torneo.Tipo.PRIVADO)
    if torneo.organizador_id != request.user.pk:
        raise Http404
    return torneo


def acceso_privado(request, codigo):
    # La redirección al login no depende de la validez del código.
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    torneo = acceder_con_codigo(codigo, request.user)
    if torneo is None:
        return render(request, 'torneos/privado_no_disponible.html', status=404)
    if torneo.organizador_id != request.user.pk:
        messages.success(request, 'Tienes acceso a este torneo privado.')
    return redirect('ficha-torneo', pk=torneo.pk)


@login_required
def inscribir_privado(request, pk):
    if (respuesta := _solo_post(request)):
        return respuesta
    torneo = get_object_or_404(Torneo, pk=pk, tipo=Torneo.Tipo.PRIVADO)
    if not tiene_acceso_privado(torneo, request.user):
        raise Http404
    try:
        inscribir_en_privado(torneo, request.user)
    except InscripcionError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Te has inscrito correctamente en el torneo.')
    return redirect('ficha-torneo', pk=torneo.pk)


@login_required
def gestionar_privado(request, pk):
    torneo = _torneo_privado_propio(request, pk)
    return render(request, 'torneos/privado_gestionar.html', {
        'torneo': torneo,
        'enlace': request.build_absolute_uri(reverse('acceso-privado', args=[torneo.codigo_acceso])),
        'accesos': torneo.accesos_privados.select_related('usuario'),
        'inscritos': torneo.inscripciones.filter(
            estado=InscripcionTorneo.Estado.CONFIRMADA,
        ).select_related('usuario'),
        'usuarios_inscritos': set(torneo.inscripciones.filter(
            estado=InscripcionTorneo.Estado.CONFIRMADA,
        ).values_list('usuario_id', flat=True)),
        'invitaciones_abiertas': torneo.estado not in (Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO),
    })


@login_required
def regenerar_privado(request, pk):
    if (respuesta := _solo_post(request)):
        return respuesta
    torneo = _torneo_privado_propio(request, pk)
    try:
        regenerar_codigo_privado(torneo, request.user)
    except InscripcionError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Se ha generado un nuevo enlace de invitación; el anterior ya no concede accesos nuevos.')
    return redirect('gestionar-privado', pk=torneo.pk)


@login_required
def revocar_acceso(request, pk, acceso_pk):
    if (respuesta := _solo_post(request)):
        return respuesta
    torneo = _torneo_privado_propio(request, pk)
    acceso = get_object_or_404(AccesoTorneoPrivado, pk=acceso_pk, torneo=torneo)
    try:
        revocar_acceso_privado(acceso, request.user)
    except InscripcionError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'El acceso se ha revocado.')
    return redirect('gestionar-privado', pk=torneo.pk)


@login_required
def mis_torneos_privados(request):
    usuario = request.user
    torneos = Torneo.objects.filter(tipo=Torneo.Tipo.PRIVADO).filter(
        Q(organizador=usuario)
        | (
            ~Q(estado=Torneo.Estado.BORRADOR)
            & (
                Q(accesos_privados__usuario=usuario, accesos_privados__estado=AccesoTorneoPrivado.Estado.ACTIVO)
                | Q(inscripciones__usuario=usuario)
            )
        )
    ).select_related('videojuego', 'organizador').distinct().order_by('-fecha_creacion')
    return render(request, 'torneos/mis_privados.html', {'torneos': torneos})
