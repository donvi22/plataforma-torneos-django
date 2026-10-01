from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import PerfilVideojuegoForm
from .models import PerfilVideojuegoUsuario, Videojuego


def catalogo(request):
    videojuegos = Videojuego.objects.filter(activo=True).prefetch_related('plataformas')
    busqueda = request.GET.get('q', '').strip()
    plataforma_id = request.GET.get('plataforma', '').strip()
    if busqueda:
        videojuegos = videojuegos.filter(nombre__icontains=busqueda)
    if plataforma_id.isdigit():
        videojuegos = videojuegos.filter(plataformas__pk=int(plataforma_id))
    videojuegos = videojuegos.distinct()
    from .models import Plataforma
    plataformas = Plataforma.objects.filter(videojuegos__activo=True).distinct().order_by('nombre')
    return render(request, 'videojuegos/catalogo.html', {
        'videojuegos': videojuegos,
        'plataformas': plataformas,
        'busqueda': busqueda,
        'plataforma_id': plataforma_id,
    })


def ficha(request, pk):
    videojuego = get_object_or_404(
        Videojuego.objects.filter(activo=True).prefetch_related('plataformas', 'rangos'),
        pk=pk,
    )
    perfil = None
    if request.user.is_authenticated:
        perfil = PerfilVideojuegoUsuario.objects.filter(
            usuario=request.user,
            videojuego=videojuego,
        ).select_related('rango_declarado').first()
    return render(request, 'videojuegos/ficha.html', {
        'videojuego': videojuego,
        'perfil_jugador': perfil,
    })


@login_required
def crear_perfil(request, videojuego_pk):
    videojuego = get_object_or_404(Videojuego, pk=videojuego_pk, activo=True)
    existente = PerfilVideojuegoUsuario.objects.filter(
        usuario=request.user,
        videojuego=videojuego,
    ).first()
    if existente:
        messages.info(request, 'Ya tienes un perfil para este videojuego.')
        return redirect('editar-perfil-videojuego', videojuego_pk=videojuego.pk)
    form = PerfilVideojuegoForm(request.POST or None, videojuego=videojuego)
    if request.method == 'POST' and form.is_valid():
        perfil = form.save(commit=False)
        perfil.usuario = request.user
        perfil.save()
        messages.success(request, 'Tu perfil de jugador se ha creado.')
        return redirect('ficha-videojuego', pk=videojuego.pk)
    return render(request, 'videojuegos/perfil_form.html', {
        'form': form,
        'videojuego': videojuego,
        'modo_edicion': False,
    })


@login_required
def editar_perfil_videojuego(request, videojuego_pk):
    videojuego = get_object_or_404(Videojuego, pk=videojuego_pk, activo=True)
    perfil = get_object_or_404(
        PerfilVideojuegoUsuario,
        pk=request.resolver_match.kwargs.get('perfil_pk') or request.GET.get('perfil'),
        usuario=request.user,
        videojuego=videojuego,
    ) if request.resolver_match.kwargs.get('perfil_pk') or request.GET.get('perfil') else get_object_or_404(
        PerfilVideojuegoUsuario,
        usuario=request.user,
        videojuego=videojuego,
    )
    form = PerfilVideojuegoForm(request.POST or None, instance=perfil, videojuego=videojuego)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Tu perfil de jugador se ha actualizado.')
        return redirect('ficha-videojuego', pk=videojuego.pk)
    return render(request, 'videojuegos/perfil_form.html', {
        'form': form,
        'videojuego': videojuego,
        'modo_edicion': True,
    })
