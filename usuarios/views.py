from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

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
    return render(request, 'usuarios/inicio.html')


def registro(request):
    form = RegistroUsuarioForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        usuario = form.save()
        login(request, usuario)
        messages.success(request, 'Tu cuenta se ha creado correctamente.')
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
