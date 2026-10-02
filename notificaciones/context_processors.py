from .models import Notificacion
from .services import no_leidas

def contador_notificaciones(request):
	if not request.user.is_authenticated:
		return {'notificaciones_no_leidas': 0, 'ultimas_notificaciones': ()}
	ultimas_notificaciones = Notificacion.objects.filter(
		destinatario=request.user,
	).select_related(
		'torneo', 'partida__torneo', 'invitacion_arbitral__torneo',
	).order_by('-fecha_creacion', '-pk')[:5]
	return {
		'notificaciones_no_leidas': no_leidas(request.user),
		'ultimas_notificaciones': ultimas_notificaciones,
	}