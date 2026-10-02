from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, When
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from partidas.models import Partida
from torneos.models import Torneo
from torneos.services import _es_administrador_autorizado

from .forms import DenunciaForm, ResolverDenunciaForm
from .models import Denuncia
from .services import (
	ModeracionError,
	crear_denuncia,
	resolver_denuncia,
	tomar_en_revision,
	validar_contexto_denuncia,
)


DENUNCIAS_POR_PAGINA = 20
ESTADOS_FINALES = (Denuncia.Estado.RESUELTA, Denuncia.Estado.DESESTIMADA)


def _denuncia_detallada():
	return Denuncia.objects.select_related(
		'denunciante', 'usuario_denunciado', 'torneo__videojuego', 'partida__torneo',
		'asignada_a', 'resuelta_por', 'historial_karma',
	).prefetch_related('historial__responsable')


@login_required
def denunciar_torneo(request, pk):
	torneo = get_object_or_404(Torneo.objects.select_related('videojuego', 'organizador'), pk=pk)
	try:
		validar_contexto_denuncia(request.user, torneo=torneo)
	except ModeracionError:
		raise Http404
	if request.method not in ('GET', 'POST'):
		return HttpResponseNotAllowed(['GET', 'POST'])
	form = DenunciaForm(
		request.POST or None, denunciante=request.user, torneo=torneo,
	)
	if request.method == 'POST' and form.is_valid():
		try:
			crear_denuncia(
				request.user,
				form.cleaned_data['categoria'],
				form.cleaned_data['descripcion'],
				torneo=torneo,
				usuario_denunciado=form.cleaned_data['usuario_denunciado'],
			)
		except ModeracionError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, 'La denuncia se ha enviado para revisión administrativa.')
			return redirect('mis-denuncias')
	return render(request, 'moderacion/crear_denuncia.html', {
		'form': form, 'torneo': torneo, 'partida': None,
	})


@login_required
def denunciar_partida(request, pk):
	partida = get_object_or_404(
		Partida.objects.select_related('torneo__videojuego', 'torneo__organizador'), pk=pk,
	)
	try:
		validar_contexto_denuncia(request.user, torneo=partida.torneo, partida=partida)
	except ModeracionError:
		raise Http404
	if request.method not in ('GET', 'POST'):
		return HttpResponseNotAllowed(['GET', 'POST'])
	form = DenunciaForm(
		request.POST or None,
		denunciante=request.user,
		torneo=partida.torneo,
		partida=partida,
	)
	if request.method == 'POST' and form.is_valid():
		try:
			crear_denuncia(
				request.user,
				form.cleaned_data['categoria'],
				form.cleaned_data['descripcion'],
				torneo=partida.torneo,
				partida=partida,
				usuario_denunciado=form.cleaned_data['usuario_denunciado'],
			)
		except ModeracionError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, 'La denuncia se ha enviado para revisión administrativa.')
			return redirect('mis-denuncias')
	return render(request, 'moderacion/crear_denuncia.html', {
		'form': form, 'torneo': partida.torneo, 'partida': partida,
	})


@login_required
def mis_denuncias(request):
	page_obj = Paginator(
		_denuncia_detallada().filter(denunciante=request.user).order_by('-fecha_creacion', '-pk'),
		DENUNCIAS_POR_PAGINA,
	).get_page(request.GET.get('page'))
	return render(request, 'moderacion/mis_denuncias.html', {'page_obj': page_obj})


@login_required
def centro(request):
	if not _es_administrador_autorizado(request.user):
		raise Http404
	queryset = _denuncia_detallada()
	estado = request.GET.get('estado', '')
	categoria = request.GET.get('categoria', '')
	torneo_id = request.GET.get('torneo', '').strip()
	if estado in Denuncia.Estado.values:
		queryset = queryset.filter(estado=estado)
	else:
		estado = ''
	if categoria in Denuncia.Categoria.values:
		queryset = queryset.filter(categoria=categoria)
	else:
		categoria = ''
	if torneo_id.isdigit():
		queryset = queryset.filter(torneo_id=int(torneo_id))
	else:
		torneo_id = ''
	page_obj = Paginator(queryset.annotate(
		prioridad=Case(
			When(estado=Denuncia.Estado.ABIERTA, then=0),
			When(estado=Denuncia.Estado.EN_REVISION, then=1),
			When(estado=Denuncia.Estado.RESUELTA, then=2),
			When(estado=Denuncia.Estado.DESESTIMADA, then=3),
			default=4,
			output_field=IntegerField(),
		),
	).order_by('prioridad', '-fecha_creacion', '-pk'), DENUNCIAS_POR_PAGINA).get_page(
		request.GET.get('page'),
	)
	return render(request, 'moderacion/centro.html', {
		'page_obj': page_obj,
		'estado_seleccionado': estado,
		'categoria_seleccionada': categoria,
		'torneo_seleccionado': torneo_id,
		'estados': Denuncia.Estado,
		'categorias': Denuncia.Categoria,
	})


@login_required
def detalle_denuncia(request, pk):
	denuncia = get_object_or_404(_denuncia_detallada(), pk=pk)
	if _es_administrador_autorizado(request.user):
		partida = denuncia.partida
		if partida:
			partida = Partida.objects.select_related(
				'torneo__videojuego', 'arbitro_asignado__usuario', 'resultado_oficial__validado_por',
				'resultado_oficial__ganador__usuario',
			).prefetch_related(
				'participantes__inscripcion__usuario',
				'declaraciones_resultado__usuario',
				'declaraciones_resultado__ganador_declarado__usuario',
				'historial_asignaciones_arbitro__arbitro_anterior__usuario',
				'historial_asignaciones_arbitro__arbitro_nuevo__usuario',
			).get(pk=partida.pk)
		form_resolucion = None
		if denuncia.estado == Denuncia.Estado.EN_REVISION and denuncia.asignada_a_id == request.user.pk:
			form_resolucion = ResolverDenunciaForm(denuncia=denuncia)
		return render(request, 'moderacion/detalle_admin.html', {
			'denuncia': denuncia,
			'partida': partida,
			'form_resolucion': form_resolucion,
			'historial': denuncia.historial.select_related('responsable').all(),
		})
	if denuncia.denunciante_id != request.user.pk:
		raise Http404
	return render(request, 'moderacion/detalle_usuario.html', {'denuncia': denuncia})


@login_required
def tomar_en_revision_web(request, pk):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	if not _es_administrador_autorizado(request.user):
		raise Http404
	denuncia = get_object_or_404(Denuncia, pk=pk)
	try:
		tomar_en_revision(denuncia, request.user)
	except ModeracionError as error:
		messages.error(request, str(error))
	else:
		messages.success(request, 'La denuncia quedó asignada a tu revisión.')
	return redirect('detalle-denuncia', pk=denuncia.pk)


@login_required
def resolver_denuncia_web(request, pk):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	if not _es_administrador_autorizado(request.user):
		raise Http404
	denuncia = get_object_or_404(Denuncia, pk=pk)
	form = ResolverDenunciaForm(request.POST, denuncia=denuncia)
	if form.is_valid():
		try:
			resolver_denuncia(
				denuncia,
				request.user,
				estado=form.cleaned_data['estado'],
				resolucion=form.cleaned_data['resolucion'],
				notas_internas=form.cleaned_data['notas_internas'],
				tipo_sancion=form.cleaned_data['tipo_sancion'],
			)
		except ModeracionError as error:
			form.add_error(None, str(error))
		else:
			messages.success(request, 'La resolución de la denuncia se ha guardado.')
			return redirect('detalle-denuncia', pk=denuncia.pk)
	return render(request, 'moderacion/detalle_admin.html', {
		'denuncia': denuncia,
		'partida': denuncia.partida,
		'form_resolucion': form,
		'historial': denuncia.historial.select_related('responsable').all(),
	})