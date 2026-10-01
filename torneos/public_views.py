from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import ClasificacionTorneo, InscripcionTorneo, Torneo
from .services import EstadoInscripciones, _es_administrador_autorizado, estado_inscripciones


PUBLIC_STATES = (
    Torneo.Estado.PROXIMAMENTE,
    Torneo.Estado.INSCRIPCIONES_ABIERTAS,
    Torneo.Estado.PRORROGA,
    Torneo.Estado.INSCRIPCIONES_CERRADAS,
    Torneo.Estado.PREPARADO,
    Torneo.Estado.EN_CURSO,
    Torneo.Estado.FINALIZADO,
    Torneo.Estado.CANCELADO,
)
SORT_OPTIONS = {
    'recientes': '-fecha_publicacion',
    'apertura': 'fecha_apertura_inscripciones',
}


def _confirmadas_count():
    return Count(
        'inscripciones',
        filter=Q(inscripciones__estado=InscripcionTorneo.Estado.CONFIRMADA),
        distinct=True,
    )


def _catalogo_queryset():
    return Torneo.objects.filter(
        tipo__in=(Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL),
        fecha_publicacion__isnull=False,
        estado__in=PUBLIC_STATES,
        videojuego__activo=True,
    ).select_related(
        'videojuego', 'formato_competitivo', 'organizador', 'rango_minimo', 'rango_maximo',
    ).annotate(confirmados= _confirmadas_count())


def _estado_presentacion(torneo, ahora):
    estado_efectivo = estado_inscripciones(torneo, ahora)
    if estado_efectivo == EstadoInscripciones.PROXIMAMENTE:
        return 'Próximamente'
    if estado_efectivo == EstadoInscripciones.ABIERTAS:
        return 'Inscripciones abiertas'
    if estado_efectivo == EstadoInscripciones.COMPLETAS:
        return 'Inscripciones completas'
    if estado_efectivo == EstadoInscripciones.PRORROGA:
        return 'Prórroga activa'
    if estado_efectivo == EstadoInscripciones.CANCELADAS:
        return 'Cancelado' if torneo.estado == Torneo.Estado.CANCELADO else 'Finalizado'
    if torneo.estado == Torneo.Estado.INSCRIPCIONES_ABIERTAS:
        return 'Plazo de inscripción vencido'
    return torneo.get_estado_display()


def _puede_ver_privado(torneo, usuario):
    if not usuario.is_authenticated:
        return False
    if torneo.organizador_id == usuario.pk or _es_administrador_autorizado(usuario):
        return True
    return torneo.inscripciones.filter(
        usuario=usuario,
        estado=InscripcionTorneo.Estado.CONFIRMADA,
    ).exists()


def catalogo(request):
    queryset = _catalogo_queryset()
    busqueda = request.GET.get('q', '').strip()
    tipo = request.GET.get('tipo', '').strip().upper()
    estado = request.GET.get('estado', '').strip().upper()
    orden = request.GET.get('orden', 'recientes').strip().lower()
    if busqueda:
        queryset = queryset.filter(nombre__icontains=busqueda)
    if tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL):
        queryset = queryset.filter(tipo=tipo)
    else:
        tipo = ''
    if estado in PUBLIC_STATES:
        queryset = queryset.filter(estado=estado)
    else:
        estado = ''
    queryset = queryset.order_by(SORT_OPTIONS.get(orden, SORT_OPTIONS['recientes']))
    if orden not in SORT_OPTIONS:
        orden = 'recientes'
    page = Paginator(queryset, 9).get_page(request.GET.get('page'))
    ahora = timezone.now()
    for torneo in page.object_list:
        torneo.estado_presentacion = _estado_presentacion(torneo, ahora)
    return render(request, 'torneos/catalogo.html', {
        'page_obj': page,
        'busqueda': busqueda,
        'tipo_seleccionado': tipo,
        'estado_seleccionado': estado,
        'orden_seleccionada': orden,
        'tipos': Torneo.Tipo,
        'estados': [(value, label) for value, label in Torneo.Estado.choices if value in PUBLIC_STATES],
        'ahora': ahora,
    })


def ficha(request, pk):
    torneo = get_object_or_404(
        Torneo.objects.select_related(
            'videojuego', 'formato_competitivo', 'organizador', 'rango_minimo', 'rango_maximo',
        ).prefetch_related('premios', 'inscripciones', 'clasificaciones__inscripcion'),
        pk=pk,
    )
    if torneo.tipo == Torneo.Tipo.PRIVADO or torneo.estado == Torneo.Estado.BORRADOR:
        if not _puede_ver_privado(torneo, request.user):
            raise Http404
    elif not (
        torneo.tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL)
        and torneo.fecha_publicacion
        and torneo.estado in PUBLIC_STATES
        and torneo.videojuego.activo
    ):
        raise Http404
    participantes = [
        inscripcion for inscripcion in torneo.inscripciones.all()
        if inscripcion.estado == InscripcionTorneo.Estado.CONFIRMADA
    ]
    torneo.confirmados = len(participantes)
    puede_preparar = bool(
        not torneo.partidas.exists()
        and torneo.confirmados == torneo.max_participantes
        and request.user.is_authenticated
        and (
            (torneo.tipo == Torneo.Tipo.PRIVADO and torneo.organizador_id == request.user.pk)
            or (torneo.tipo == Torneo.Tipo.OFICIAL and _es_administrador_autorizado(request.user))
        )
    )
    inscripcion_usuario = None
    perfil_videojuego = None
    puede_inscribirse = False
    es_organizador = request.user.is_authenticated and torneo.organizador_id == request.user.pk
    puede_cerrar = bool(
        request.user.is_authenticated
        and torneo.estado == Torneo.Estado.EN_CURSO
        and (
            (torneo.tipo == Torneo.Tipo.PRIVADO and (es_organizador or _es_administrador_autorizado(request.user)))
            or (torneo.tipo == Torneo.Tipo.OFICIAL and _es_administrador_autorizado(request.user))
        )
    )
    if request.user.is_authenticated and torneo.tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL):
        inscripcion_usuario = torneo.inscripciones.filter(usuario=request.user).first()
        perfil_videojuego = request.user.perfiles_videojuego.filter(
            videojuego=torneo.videojuego,
        ).select_related('rango_declarado').first()
        puede_inscribirse = (
            estado_inscripciones(torneo, timezone.now()) in (EstadoInscripciones.ABIERTAS, EstadoInscripciones.PRORROGA)
            and not inscripcion_usuario
            and torneo.confirmados < torneo.max_participantes
            and bool(perfil_videojuego)
        )
    return render(request, 'torneos/ficha.html', {
        'torneo': torneo,
        'participantes': participantes,
        'estado_presentacion': _estado_presentacion(torneo, timezone.now()),
        'ahora': timezone.now(),
        'inscripcion_usuario': inscripcion_usuario,
        'perfil_videojuego': perfil_videojuego,
        'puede_inscribirse': puede_inscribirse,
        'es_organizador': es_organizador,
        'puede_cancelar_inscripcion': bool(
            inscripcion_usuario
            and inscripcion_usuario.estado == InscripcionTorneo.Estado.CONFIRMADA
            and estado_inscripciones(torneo, timezone.now()) in (EstadoInscripciones.ABIERTAS, EstadoInscripciones.PRORROGA)
        ),
        'puede_preparar': puede_preparar,
		'clasificacion': torneo.clasificaciones.select_related('inscripcion').all() if torneo.estado == Torneo.Estado.FINALIZADO else [],
		'puede_cerrar': puede_cerrar,
    })