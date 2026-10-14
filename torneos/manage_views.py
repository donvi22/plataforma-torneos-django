from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Case, Count, DateTimeField, F, IntegerField, Q, When
from django.db.models.functions import Coalesce
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import TorneoForm
from .models import Torneo
from .public_views import _confirmadas_count, _es_administrador_autorizado
from .services import CierreTorneoError, InscripcionError, cerrar_torneo, crear_torneo, publicar_torneo
from .services import cancelar_inscripcion, inscribir_usuario
from .models import InscripcionTorneo
from partidas.services import PreparacionBracketError, preparar_bracket


def _puede_gestionar(torneo, usuario):
    return bool(usuario.puede_operar and (torneo.organizador_id == usuario.pk or _es_administrador_autorizado(usuario)))


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
    vista = request.GET.get('vista', 'todos')
    if vista not in ('todos', 'activos', 'finalizados'):
        vista = 'todos'
    torneos = Torneo.objects.filter(organizador=request.user).select_related('videojuego').annotate(
        confirmados=_confirmadas_count(),
        prioridad_gestion=Case(
            When(estado=Torneo.Estado.EN_CURSO, then=0),
            When(estado__in=(Torneo.Estado.PREPARADO, Torneo.Estado.INSCRIPCIONES_ABIERTAS, Torneo.Estado.PRORROGA, Torneo.Estado.INSCRIPCIONES_CERRADAS), then=1),
            When(estado=Torneo.Estado.PROXIMAMENTE, then=2),
            When(estado__in=(Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO), then=4),
            default=3, output_field=IntegerField(),
        ),
        fecha_gestion=Case(
            When(estado__in=(Torneo.Estado.INSCRIPCIONES_ABIERTAS, Torneo.Estado.PRORROGA), then=Coalesce('fecha_cierre_inscripciones', 'fecha_inicio_prevista')),
            When(estado__in=(Torneo.Estado.PROXIMAMENTE, Torneo.Estado.BORRADOR), then=Coalesce('fecha_apertura_inscripciones', 'fecha_inicio_prevista')),
            When(estado=Torneo.Estado.EN_CURSO, then=Coalesce('fecha_inicio_real', 'fecha_inicio_prevista')),
            When(estado__in=(Torneo.Estado.PREPARADO, Torneo.Estado.INSCRIPCIONES_CERRADAS), then='fecha_inicio_prevista'),
            default=None, output_field=DateTimeField(),
        ),
        fecha_historial_gestion=Coalesce('fecha_fin_real', 'fecha_actualizacion', 'fecha_creacion'),
    )
    if vista == 'activos':
        torneos = torneos.exclude(estado__in=(Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO))
    elif vista == 'finalizados':
        torneos = torneos.filter(estado=Torneo.Estado.FINALIZADO)
    torneos = torneos.order_by('prioridad_gestion', F('fecha_gestion').asc(nulls_last=True), '-fecha_historial_gestion', '-fecha_creacion', '-pk')
    torneos = list(torneos)
    for torneo in torneos:
        autorizado = _puede_gestionar(torneo, request.user)
        terminal = torneo.estado in (Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO)
        torneo.accion_url = 'ficha-torneo'
        torneo.accion_texto = 'Ver torneo'
        torneo.accion_contexto = 'Consulta del torneo'
        if autorizado and not terminal:
            if torneo.estado == Torneo.Estado.BORRADOR:
                torneo.accion_url = 'editar-borrador-torneo'
                torneo.accion_contexto = 'Editar borrador'
            elif torneo.tipo == Torneo.Tipo.PRIVADO:
                torneo.accion_url = 'gestionar-privado'
                torneo.accion_contexto = 'Invitación y accesos'
            elif torneo.tipo == Torneo.Tipo.OFICIAL:
                torneo.accion_url = 'calendario-partidas'
                torneo.accion_contexto = 'Calendario de partidas'
            else:
                torneo.accion_url = 'gestionar-arbitros-torneo'
                torneo.accion_contexto = 'Árbitros y asignaciones'
            torneo.accion_texto = 'Gestionar'
        torneo.mostrar_accesos = autorizado and torneo.tipo == Torneo.Tipo.PRIVADO and torneo.accion_url != 'gestionar-privado'
        torneo.mostrar_publicar = autorizado and torneo.estado == Torneo.Estado.BORRADOR and (torneo.tipo != Torneo.Tipo.OFICIAL or _es_administrador_autorizado(request.user))
    return render(request, 'torneos/mis_torneos.html', {'torneos': torneos, 'vista': vista})


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
    activa = Q(estado=InscripcionTorneo.Estado.CONFIRMADA) & ~Q(torneo__estado__in=(Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO))
    vista = request.GET.get('vista', 'todos')
    if vista not in ('todos', 'activos', 'finalizados'):
        vista = 'todos'
    inscripciones = InscripcionTorneo.objects.filter(
        usuario=request.user,
    ).select_related('torneo__videojuego').annotate(
        confirmados=Count('torneo__inscripciones', filter=Q(torneo__inscripciones__estado=InscripcionTorneo.Estado.CONFIRMADA), distinct=True),
        prioridad=Case(
            When(activa & Q(torneo__estado=Torneo.Estado.EN_CURSO), then=0),
            When(activa, then=1), default=2, output_field=IntegerField(),
        ),
        fecha_atencion=Case(
            When(activa & Q(torneo__estado__in=(Torneo.Estado.INSCRIPCIONES_ABIERTAS, Torneo.Estado.PRORROGA)), then=Coalesce('torneo__fecha_cierre_inscripciones', 'torneo__fecha_inicio_prevista')),
            When(activa & Q(torneo__estado__in=(Torneo.Estado.PROXIMAMENTE, Torneo.Estado.BORRADOR)), then=Coalesce('torneo__fecha_apertura_inscripciones', 'torneo__fecha_inicio_prevista')),
            When(activa, then='torneo__fecha_inicio_prevista'), default=None, output_field=DateTimeField(),
        ),
        fecha_historial=Case(
            When(estado=InscripcionTorneo.Estado.CANCELADA, then=Coalesce('fecha_cancelacion', 'fecha_inscripcion')),
            When(estado=InscripcionTorneo.Estado.DESCALIFICADA, then=Coalesce('fecha_descalificacion', 'fecha_inscripcion')),
            default=Coalesce('torneo__fecha_fin_real', 'fecha_inscripcion'), output_field=DateTimeField(),
        ),
    )
    if vista == 'activos':
        inscripciones = inscripciones.filter(activa)
    elif vista == 'finalizados':
        inscripciones = inscripciones.filter(torneo__estado=Torneo.Estado.FINALIZADO)
    inscripciones = inscripciones.order_by('prioridad', F('fecha_atencion').asc(nulls_last=True), '-fecha_historial', '-pk')
    return render(request, 'torneos/mis_participaciones.html', {
        'inscripciones': inscripciones, 'vista': vista,
    })


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