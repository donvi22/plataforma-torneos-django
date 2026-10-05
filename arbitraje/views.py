from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, When
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from partidas.models import Partida
from torneos.models import Torneo
from torneos.services import _es_administrador_autorizado
from usuarios.models import Usuario

from .models import ArbitroTorneo, HistorialAsignacionArbitro
from .services import (
	ArbitrajeError,
	_autorizado_torneo,
	aceptar_invitacion,
	cambiar_disponibilidad,
	invitar_arbitro,
	rechazar_invitacion,
)


ESTADOS_PARTIDA_ACTIVOS = (
	Partida.Estado.PENDIENTE,
	Partida.Estado.PROGRAMADA,
	Partida.Estado.CHECK_IN,
	Partida.Estado.LISTA_PARA_COMENZAR,
	Partida.Estado.EN_CURSO,
	Partida.Estado.PENDIENTE_VALIDACION,
	Partida.Estado.INCIDENCIA,
)
ESTADOS_REQUIEREN_ACCION = (
	Partida.Estado.PENDIENTE_VALIDACION,
	Partida.Estado.INCIDENCIA,
)
ESTADOS_EN_CURSO = (
	Partida.Estado.EN_CURSO,
	Partida.Estado.CHECK_IN,
	Partida.Estado.LISTA_PARA_COMENZAR,
)
ESTADOS_PROXIMAS = (
	Partida.Estado.PENDIENTE,
	Partida.Estado.PROGRAMADA,
)
ELEMENTOS_POR_PAGINA = 20


def _arbitraje_en_validacion(partida):
	resultado = partida.resultado_oficial
	arbitraje = partida.arbitro_asignado
	historial = list(partida.historial_asignaciones_arbitro.all())
	for cambio in sorted(historial, key=lambda fila: (fila.fecha, fila.pk), reverse=True):
		if cambio.fecha > resultado.fecha_validacion:
			arbitraje = cambio.arbitro_anterior
	return arbitraje, historial


def _partidas_asignadas_paginadas(usuario, estados, pagina):
	page_obj = Paginator(Partida.objects.filter(
		arbitro_asignado__usuario=usuario,
		estado__in=estados,
	).select_related(
		'torneo__videojuego', 'torneo__organizador', 'arbitro_asignado',
	).prefetch_related(
		'participantes__inscripcion__usuario', 'checkins',
	).order_by('fecha_hora_programada', 'pk'), ELEMENTOS_POR_PAGINA).get_page(pagina)
	for partida in page_obj:
		partida.inscripciones_participantes = [p.inscripcion for p in partida.participantes.all()]
		partida.nicks_participantes = [
			participante.inscripcion.nick_historico
			for participante in partida.participantes.all()
		]
		partida.checkins_confirmados = sum(checkin.confirmado for checkin in partida.checkins.all())
		partida.checkins_totales = len(partida.nicks_participantes) + 1
	return page_obj


@login_required
def centro(request):
	usuario = request.user
	invitaciones = ArbitroTorneo.objects.filter(usuario=usuario).select_related(
		'torneo__videojuego', 'torneo__organizador',
	).order_by('-fecha_invitacion', '-pk')
	invitaciones_pendientes = Paginator(invitaciones.filter(
		estado_invitacion=ArbitroTorneo.EstadoInvitacion.PENDIENTE,
	), ELEMENTOS_POR_PAGINA).get_page(request.GET.get('invitaciones_page'))
	torneos_aceptados = Paginator(invitaciones.filter(
		estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA,
		activo_en_torneo=True,
	), ELEMENTOS_POR_PAGINA).get_page(request.GET.get('torneos_page'))
	invitaciones_resueltas = Paginator(invitaciones.filter(
		Q(estado_invitacion__in=(
			ArbitroTorneo.EstadoInvitacion.RECHAZADA,
			ArbitroTorneo.EstadoInvitacion.CANCELADA,
		))
		| Q(
			estado_invitacion=ArbitroTorneo.EstadoInvitacion.ACEPTADA,
			activo_en_torneo=False,
		),
	), ELEMENTOS_POR_PAGINA).get_page(request.GET.get('historial_page'))
	partidas_requieren_accion = _partidas_asignadas_paginadas(
		usuario, ESTADOS_REQUIEREN_ACCION, request.GET.get('accion_page'),
	)
	partidas_en_curso = _partidas_asignadas_paginadas(
		usuario, ESTADOS_EN_CURSO, request.GET.get('curso_page'),
	)
	partidas_proximas = _partidas_asignadas_paginadas(
		usuario, ESTADOS_PROXIMAS, request.GET.get('proximas_page'),
	)

	historial_candidatos = Partida.objects.filter(
		estado=Partida.Estado.FINALIZADA,
		resultado_oficial__isnull=False,
	).filter(
		Q(arbitro_asignado__usuario=usuario)
		| Q(historial_asignaciones_arbitro__arbitro_nuevo__usuario=usuario)
		| Q(historial_asignaciones_arbitro__arbitro_anterior__usuario=usuario),
	).select_related(
		'torneo__videojuego', 'resultado_oficial__ganador', 'arbitro_asignado',
	).prefetch_related(
		'participantes__inscripcion__usuario',
		'historial_asignaciones_arbitro__arbitro_anterior',
		'historial_asignaciones_arbitro__arbitro_nuevo',
	).order_by('-resultado_oficial__fecha_validacion', '-pk').distinct()[:30]
	historial_reciente = []
	for partida in historial_candidatos:
		arbitraje, cambios = _arbitraje_en_validacion(partida)
		if arbitraje and arbitraje.usuario_id == usuario.pk:
			historial_reciente.append({
				'partida': partida,
				'arbitraje': arbitraje,
				'reasignada': any(cambio.arbitro_anterior_id is not None for cambio in cambios),
				'inscripciones': [participante.inscripcion for participante in partida.participantes.all()],
				'participantes': [
					participante.inscripcion.nick_historico
					for participante in partida.participantes.all()
				],
			})
			if len(historial_reciente) == 10:
				break

	torneos_gestionables = Paginator(Torneo.objects.filter(
		organizador=usuario,
		tipo__in=(Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL),
	).only('pk', 'nombre', 'tipo', 'estado').order_by('-fecha_creacion'), ELEMENTOS_POR_PAGINA).get_page(
		request.GET.get('gestion_page'),
	)
	return render(request, 'arbitraje/centro.html', {
		'invitaciones_pendientes': invitaciones_pendientes,
		'invitaciones_resueltas': invitaciones_resueltas[:10],
		'torneos_aceptados': torneos_aceptados,
		'partidas_requieren_accion': partidas_requieren_accion,
		'partidas_en_curso': partidas_en_curso,
		'partidas_proximas': partidas_proximas,
		'historial_reciente': historial_reciente,
		'torneos_gestionables': torneos_gestionables,
		'puede_arbitrar_publico': usuario.is_active and usuario.karma_total >= 150,
	})


@login_required
def cambiar_disponibilidad_web(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	valor_disponibilidad = request.POST.get('disponible')
	if valor_disponibilidad not in ('0', '1'):
		messages.error(request, 'Indica si quieres activar o desactivar la disponibilidad.')
		return redirect('centro-arbitraje')
	disponible = valor_disponibilidad == '1'
	try:
		cambiar_disponibilidad(request.user, disponible)
	except ArbitrajeError as error:
		messages.error(request, str(error))
	else:
		mensaje = 'Disponibilidad activada para futuras asignaciones.' if disponible else (
			'Disponibilidad desactivada. Las asignaciones existentes se mantienen.'
		)
		messages.success(request, mensaje)
	return redirect('centro-arbitraje')


@login_required
def responder_invitacion(request, pk, respuesta):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	if respuesta not in ('aceptar', 'rechazar'):
		raise Http404
	invitacion = get_object_or_404(ArbitroTorneo, pk=pk, usuario=request.user)
	try:
		if respuesta == 'aceptar':
			aceptar_invitacion(invitacion, request.user)
			messages.success(request, 'Has aceptado la invitación arbitral.')
		else:
			rechazar_invitacion(invitacion, request.user)
			messages.success(request, 'Has rechazado la invitación arbitral.')
	except ArbitrajeError as error:
		messages.error(request, str(error))
	return redirect('centro-arbitraje')


@login_required
def gestionar_torneo(request, pk):
	torneo = get_object_or_404(Torneo.objects.select_related('videojuego', 'organizador'), pk=pk)
	if torneo.tipo == Torneo.Tipo.PRIVADO or not _autorizado_torneo(torneo, request.user):
		raise Http404
	if request.method == 'POST':
		if not torneo.tipo == Torneo.Tipo.PUBLICO and not _es_administrador_autorizado(request.user):
			raise Http404
		username = request.POST.get('username', '').strip()
		usuario = Usuario.objects.filter(username__iexact=username, is_active=True).first()
		if not usuario:
			messages.error(request, 'No se encontró una cuenta activa con ese nombre de usuario.')
		else:
			try:
				invitar_arbitro(torneo, usuario, request.user)
			except ArbitrajeError as error:
				messages.error(request, str(error))
			else:
				messages.success(request, f'Invitación enviada a {usuario.username}.')
		return redirect('gestionar-arbitros-torneo', pk=torneo.pk)
	if request.method != 'GET':
		return HttpResponseNotAllowed(['GET', 'POST'])
	arbitros_page = Paginator(ArbitroTorneo.objects.filter(torneo=torneo).select_related('usuario').annotate(
		partidas_activas=Count(
			'partidas_asignadas',
			filter=Q(partidas_asignadas__estado__in=ESTADOS_PARTIDA_ACTIVOS),
			distinct=True,
		),
	).order_by('estado_invitacion', 'usuario__username'), ELEMENTOS_POR_PAGINA).get_page(
		request.GET.get('page'),
	)
	return render(request, 'arbitraje/gestionar_torneo.html', {
		'torneo': torneo,
		'arbitros_page': arbitros_page,
		'puede_invitar': torneo.tipo == Torneo.Tipo.PUBLICO or _es_administrador_autorizado(request.user),
	})
