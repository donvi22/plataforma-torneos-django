from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import TorneoForm
from .models import Torneo
from .public_views import _es_administrador_autorizado
from .services import CierreTorneoError, InscripcionError, cerrar_torneo, crear_torneo, publicar_torneo
from .services import cancelar_inscripcion, inscribir_usuario
from .models import InscripcionTorneo
from partidas.services import PreparacionBracketError, preparar_bracket


def _puede_gestionar(torneo, usuario):
    return torneo.organizador_id == usuario.pk or _es_administrador_autorizado(usuario)


@login_required
def crear(request):
    if not request.user.is_active:
        raise Http404
    form = TorneoForm(request.POST or None, request.FILES or None, usuario=request.user)
    if request.method == 'POST' and form.is_valid():
        datos = {field: form.cleaned_data.get(field) for field in form.Meta.fields}
        try:
            torneo = crear_torneo(request.user, **datos)
        except (InscripcionError, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, 'El torneo se ha guardado como borrador.')
            return redirect('mis-torneos')
    return render(request, 'torneos/gestionar.html', {
        'form': form, 'modo': 'crear', 'formato_tamanos': form.formato_tamanos,
    })


@login_required
def editar_borrador(request, pk):
    torneo = get_object_or_404(Torneo, pk=pk)
    if torneo.estado != Torneo.Estado.BORRADOR or not _puede_gestionar(torneo, request.user):
        raise Http404
    form = TorneoForm(request.POST or None, request.FILES or None, instance=torneo, usuario=request.user)
    if request.method == 'POST' and form.is_valid():
        for field in form.Meta.fields:
            setattr(torneo, field, form.cleaned_data.get(field))
        torneo.organizador_id = torneo.organizador_id
        try:
            torneo.full_clean()
            torneo.save()
        except ValidationError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, 'El borrador se ha actualizado.')
            return redirect('mis-torneos')
    return render(request, 'torneos/gestionar.html', {
        'form': form, 'modo': 'editar', 'torneo': torneo,
        'formato_tamanos': form.formato_tamanos,
    })


@login_required
def publicar(request, pk):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    torneo = get_object_or_404(Torneo, pk=pk)
    if not _puede_gestionar(torneo, request.user):
        raise Http404
    try:
        publicar_torneo(torneo, request.user)
    except (InscripcionError, ValidationError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'El torneo se ha publicado.')
    return redirect('mis-torneos')


@login_required
def mis_torneos(request):
    torneos = Torneo.objects.filter(organizador=request.user).select_related('videojuego').order_by('-fecha_creacion')
    return render(request, 'torneos/mis_torneos.html', {'torneos': torneos})


@login_required
def rangos_por_videojuego(request, videojuego_pk):
    if request.method != 'GET':
        return JsonResponse({'detail': 'Método no permitido.'}, status=405)
    from videojuegos.models import RangoVideojuego, Videojuego
    if not Videojuego.objects.filter(pk=videojuego_pk, activo=True).exists():
        return JsonResponse({'rangos': []})
    rangos = RangoVideojuego.objects.filter(videojuego_id=videojuego_pk).order_by('posicion')
    return JsonResponse({'rangos': [
        {'id': rango.pk, 'nombre': rango.nombre, 'posicion': rango.posicion}
        for rango in rangos
    ]})


@login_required
def inscribir(request, pk):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    torneo = get_object_or_404(Torneo, pk=pk)
    if torneo.tipo == Torneo.Tipo.PRIVADO or torneo.estado == Torneo.Estado.BORRADOR:
        raise Http404
    try:
        inscribir_usuario(torneo, request.user)
    except InscripcionError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Te has inscrito correctamente en el torneo.')
    return redirect('ficha-torneo', pk=torneo.pk)


@login_required
def cancelar_inscripcion_web(request, pk):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    inscripcion = get_object_or_404(
        InscripcionTorneo.objects.select_related('torneo'),
        pk=pk,
        usuario=request.user,
    )
    torneo = inscripcion.torneo
    try:
        cancelar_inscripcion(inscripcion, motivo='Cancelación solicitada por el participante.')
    except InscripcionError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Tu inscripción se ha cancelado y la plaza ha quedado libre.')
    return redirect('ficha-torneo', pk=torneo.pk)


@login_required
def mis_participaciones(request):
    inscripciones = InscripcionTorneo.objects.filter(
        usuario=request.user,
    ).select_related('torneo__videojuego').order_by('-fecha_inscripcion')
    return render(request, 'torneos/mis_participaciones.html', {'inscripciones': inscripciones})


@login_required
def preparar_torneo(request, pk):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    torneo = get_object_or_404(Torneo, pk=pk)
    try:
        preparar_bracket(torneo, request.user)
    except PreparacionBracketError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'El bracket se ha preparado correctamente.')
    return redirect('ficha-torneo', pk=torneo.pk)


@login_required
def cerrar_torneo_web(request, pk):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    torneo = get_object_or_404(Torneo, pk=pk)
    try:
        cerrar_torneo(torneo, request.user)
    except CierreTorneoError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'El torneo se ha cerrado y su clasificación final está disponible.')
    return redirect('ficha-torneo', pk=torneo.pk)