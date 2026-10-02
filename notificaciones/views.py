from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_POST

from .models import Notificacion
from .services import marcar_leida, marcar_todas_leidas, notificaciones_de_usuario

NOTIFICACIONES_POR_PAGINA = 20

def destino_notificacion(notificacion):
	try:
		if notificacion.partida_id:
			return reverse('detalle-partida', args=(notificacion.partida_id,))
		if notificacion.invitacion_arbitral_id:
			return reverse('ficha-torneo', args=(notificacion.invitacion_arbitral.torneo_id,))
		if notificacion.torneo_id:
			return reverse('ficha-torneo', args=(notificacion.torneo_id,))
	except NoReverseMatch:
		return None
	return None

@login_required
def centro(request):
	queryset = notificaciones_de_usuario(request.user).select_related(
		'torneo', 'partida__torneo', 'invitacion_arbitral__torneo',
	).order_by('-fecha_creacion', '-pk')
	page_obj = Paginator(queryset, NOTIFICACIONES_POR_PAGINA).get_page(request.GET.get('page'))
	return render(request, 'notificaciones/centro.html', {'page_obj': page_obj})

@login_required
@require_POST
def abrir(request, pk):
	notificacion = get_object_or_404(
		Notificacion.objects.select_related('torneo', 'partida', 'invitacion_arbitral'),
		pk=pk,
		destinatario=request.user,
	)
	destino = destino_notificacion(notificacion)
	marcar_leida(request.user, notificacion)
	return HttpResponseRedirect(destino or reverse('centro-notificaciones'))

@login_required
@require_POST
def marcar_como_leida(request, pk):
	notificacion = get_object_or_404(Notificacion, pk=pk, destinatario=request.user)
	marcar_leida(request.user, notificacion)
	return redirect('centro-notificaciones')

@login_required
@require_POST
def marcar_todas_como_leidas(request):
	marcar_todas_leidas(request.user)
	return redirect('centro-notificaciones')

# Create your views here.
