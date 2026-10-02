from .services import no_leidas

def contador_notificaciones(request):
	if not request.user.is_authenticated:
		return {'notificaciones_no_leidas': 0}
	return {'notificaciones_no_leidas': no_leidas(request.user)}