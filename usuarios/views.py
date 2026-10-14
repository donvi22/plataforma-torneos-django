from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db.models import Case, DateTimeField, F, IntegerField, Prefetch, Q, When
from django.db.models.functions import Coalesce
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .account_services import AccountLifecycleError, cambiar_nickname as cambiar_nickname_service, eliminar_cuenta
from .forms import (
    CambiarNicknameForm,
    ConfirmarEliminacionForm,
    EditarPerfilForm,
    InicioSesionForm,
    RegistroUsuarioForm,
)
from .models import HistorialKarma, HistorialXP, Usuario
from .services import progreso_nivel


def inicio(request):
    from arbitraje.models import ArbitroTorneo
    from partidas.models import Partida, ParticipantePartida
    from torneos.models import InscripcionTorneo, Torneo
    from torneos.public_views import _catalogo_queryset, _confirmadas_count

    catalogo = _catalogo_queryset().exclude(estado__in=(Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO))
    destacados = list(catalogo.order_by('-fecha_publicacion', '-pk')[:3])
    contexto = {'destacados': destacados, 'mis_torneos': [], 'proximas_partidas': [], 'puede_crear': False}
    if request.user.is_authenticated:
        usuario = request.user
        contexto['puede_crear'] = usuario.puede_operar
        contexto['mis_torneos'] = list(Torneo.objects.filter(pk__in=InscripcionTorneo.objects.filter(
            usuario=usuario, estado=InscripcionTorneo.Estado.CONFIRMADA,
        ).values('torneo_id')).select_related(
            'videojuego',
        ).annotate(
            confirmados=_confirmadas_count(),
            prioridad_terminal=Case(
                When(estado__in=(Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO), then=1),
                default=0, output_field=IntegerField(),
            ),
            prioridad_atencion=Case(
                When(estado=Torneo.Estado.EN_CURSO, then=0),
                default=1, output_field=IntegerField(),
            ),
            fecha_resumen=Case(
                When(estado__in=(Torneo.Estado.INSCRIPCIONES_ABIERTAS, Torneo.Estado.PRORROGA), then=Coalesce('fecha_cierre_inscripciones', 'fecha_inicio_prevista')),
                When(estado__in=(Torneo.Estado.PROXIMAMENTE, Torneo.Estado.BORRADOR), then=Coalesce('fecha_apertura_inscripciones', 'fecha_inicio_prevista')),
                When(estado__in=(Torneo.Estado.PREPARADO, Torneo.Estado.INSCRIPCIONES_CERRADAS), then='fecha_inicio_prevista'),
                default=None, output_field=DateTimeField(),
            ),
        ).order_by('prioridad_terminal', 'prioridad_atencion', F('fecha_resumen').asc(nulls_last=True), '-fecha_creacion', '-pk')[:3])
        partidas = Partida.objects.filter(
            Q(participantes__inscripcion__usuario=usuario, participantes__inscripcion__estado=InscripcionTorneo.Estado.CONFIRMADA)
            | Q(arbitro_asignado__usuario=usuario, arbitro_asignado__estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA, arbitro_asignado__activo_en_torneo=True),
            estado__in=(Partida.Estado.PENDIENTE, Partida.Estado.PROGRAMADA, Partida.Estado.CHECK_IN, Partida.Estado.LISTA_PARA_COMENZAR, Partida.Estado.EN_CURSO),
            torneo__estado__in=(Torneo.Estado.PREPARADO, Torneo.Estado.EN_CURSO),
        ).filter(
            Q(torneo_id__in=catalogo.values('pk')) | Q(torneo__tipo=Torneo.Tipo.PRIVADO),
        ).select_related('torneo__videojuego', 'arbitro_asignado').prefetch_related(Prefetch(
            'participantes', queryset=ParticipantePartida.objects.select_related('inscripcion__usuario').order_by('posicion'),
        )).defer('codigo_lobby', 'contrasena_lobby', 'nombre_lobby', 'instrucciones_lobby').distinct().order_by(
            F('fecha_hora_programada').asc(nulls_last=True), 'torneo_id', 'numero_ronda', 'numero_orden',
        )[:3]
        for partida in partidas:
            participantes = list(partida.participantes.all())
            es_jugador = any(p.inscripcion.usuario_id == usuario.pk and p.inscripcion.estado == InscripcionTorneo.Estado.CONFIRMADA for p in participantes)
            contexto['proximas_partidas'].append({
                'partida': partida,
                'rol': 'Jugador' if es_jugador else 'Árbitro',
                'rival': next((p.inscripcion for p in participantes if p.inscripcion.usuario_id != usuario.pk), None) if es_jugador else None,
                'participantes': [p.inscripcion for p in participantes] if not es_jugador else [],
            })
    return render(request, 'usuarios/inicio.html', contexto)


def registro(request):
    form = RegistroUsuarioForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        usuario = form.save()
        login(request, usuario)
        messages.success(request, 'Tu cuenta se ha creado correctamente.')
        destino = request.GET.get('next', '')
        if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(destino)
        return redirect('perfil', pk=usuario.pk)
    return render(request, 'usuarios/registro.html', {'form': form})


class InicioSesionView(LoginView):
    template_name = 'usuarios/login.html'
    authentication_form = InicioSesionForm
    redirect_authenticated_user = True


@login_required
def cerrar_sesion(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    from django.contrib.auth import logout
    logout(request)
    messages.info(request, 'Has cerrado la sesión.')
    return redirect('inicio')


def buscar_usuarios(request):
    consulta = request.GET.get('q', '').strip()[:150]
    page_obj = None
    if consulta:
        from videojuegos.models import PerfilVideojuegoUsuario
        usuarios = Usuario.objects.filter(
            username__icontains=consulta,
            is_active=True,
            estado_cuenta=Usuario.EstadoCuenta.ACTIVA,
        ).prefetch_related(Prefetch(
            'perfiles_videojuego',
            queryset=PerfilVideojuegoUsuario.objects.filter(
                videojuego__activo=True,
            ).select_related('videojuego'),
        )).order_by('username')
        page_obj = Paginator(usuarios, 20).get_page(request.GET.get('page'))
    return render(request, 'usuarios/buscar.html', {'consulta': consulta, 'page_obj': page_obj})


def perfil(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk)
    if usuario.estado_cuenta == Usuario.EstadoCuenta.ELIMINADA:
        return render(request, 'usuarios/perfil_no_disponible.html', status=404)
    from videojuegos.models import PerfilVideojuegoUsuario
    perfiles_videojuego = PerfilVideojuegoUsuario.objects.filter(
        usuario=usuario,
        videojuego__activo=True,
    ).select_related('videojuego', 'rango_declarado').prefetch_related('videojuego__plataformas')
    return render(request, 'usuarios/perfil.html', {
        'perfil': usuario,
        'es_propietario': request.user.is_authenticated and request.user.pk == usuario.pk,
        'perfiles_videojuego': perfiles_videojuego,
		'progreso_xp': progreso_nivel(usuario.xp_total),
    })


@login_required
def perfil_propio(request):
    return redirect('perfil', pk=request.user.pk)


@login_required
def editar_perfil(request):
    form = EditarPerfilForm(request.POST or None, request.FILES or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Tu perfil se ha actualizado.')
        return redirect('perfil', pk=request.user.pk)
    return render(request, 'usuarios/editar_perfil.html', {'form': form})


@login_required
def cambiar_nickname(request):
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])
    form = CambiarNicknameForm(
        request.POST or None,
        usuario=request.user,
        initial={'username': request.user.username},
    )
    if request.method == 'POST' and form.is_valid():
        try:
            cambiar_nickname_service(request.user, form.cleaned_data['username'])
        except AccountLifecycleError as error:
            form.add_error('username', str(error))
        else:
            messages.success(request, 'Tu nickname se ha actualizado.')
            return redirect('editar-perfil')
    return render(request, 'usuarios/cambiar_nickname.html', {'form': form})


@login_required
def eliminar_cuenta_web(request):
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])
    form = ConfirmarEliminacionForm(request.POST or None, usuario=request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            eliminar_cuenta(
                request.user,
                form.cleaned_data['password'],
                form.cleaned_data['confirmacion'],
            )
        except AccountLifecycleError as error:
            form.add_error(None, str(error))
        else:
            logout(request)
            messages.success(request, 'La cuenta se cerró y tus datos personales se anonimizaron.')
            return redirect('inicio')
    return render(request, 'usuarios/eliminar_cuenta.html', {'form': form})


@login_required
def historial_xp(request):
    movimientos = HistorialXP.objects.filter(usuario=request.user).select_related(
        'torneo', 'inscripcion',
    ).order_by('-fecha', '-pk')
    return render(request, 'usuarios/historial_xp.html', {'movimientos': movimientos})


@login_required
def historial_karma(request):
    movimientos = HistorialKarma.objects.filter(usuario=request.user).select_related(
        'torneo', 'partida',
    ).order_by('-fecha', '-pk')
    return render(request, 'usuarios/historial_karma.html', {'movimientos': movimientos})
