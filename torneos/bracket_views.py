from django.http import Http404
from django.shortcuts import get_object_or_404, render

from partidas.models import Partida

from .models import Torneo
from .public_views import PUBLIC_STATES, _puede_ver_privado


def _nombre_ronda(numero_ronda, total_rondas):
    distancia = total_rondas - numero_ronda
    return {
        0: 'Final',
        1: 'Semifinales',
        2: 'Cuartos de final',
        3: 'Octavos de final',
    }.get(distancia, f'Ronda de {2 ** distancia}')


def _slot(partida, posicion):
    participante = partida.participantes.filter(posicion=posicion).select_related('inscripcion').first()
    if participante:
        resultado = getattr(partida, 'resultado_oficial', None)
        return {
            'texto': participante.inscripcion.nick_historico,
            'participante': True,
            'ganador': bool(resultado and resultado.ganador_id == participante.inscripcion_id),
        }
    alimentadora = partida.partidas_anteriores.filter(posicion_en_siguiente_partida=posicion).first()
    if alimentadora and hasattr(alimentadora, 'resultado_oficial') and not alimentadora.resultado_oficial.ganador_id:
        return {'texto': 'Sin participante', 'participante': False, 'ganador': False}
    return {'texto': 'Por determinar', 'participante': False, 'ganador': False}


def _preparar_partida(partida):
    resultado = getattr(partida, 'resultado_oficial', None)
    return {
        'partida': partida,
        'slot_1': _slot(partida, 1),
        'slot_2': _slot(partida, 2),
        'resultado': resultado,
        'ganador': resultado.ganador.nick_historico if resultado and resultado.ganador_id else None,
        'tipo_resultado': resultado.get_tipo_resultado_display() if resultado else None,
    }


def bracket(request, pk):
    torneo = get_object_or_404(Torneo.objects.select_related('videojuego', 'organizador'), pk=pk)
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
    partidas = list(
        Partida.objects.filter(torneo=torneo)
        .select_related('siguiente_partida')
        .prefetch_related('participantes__inscripcion', 'partidas_anteriores__resultado_oficial', 'resultado_oficial__ganador')
        .order_by('numero_ronda', 'numero_orden')
    )
    if not partidas:
        return render(request, 'torneos/bracket_no_disponible.html', {'torneo': torneo})
    total_rondas = max(partida.numero_ronda for partida in partidas)
    rondas = [{
        'numero': numero,
        'nombre': _nombre_ronda(numero, total_rondas),
        'partidas': [_preparar_partida(p) for p in partidas if p.numero_ronda == numero],
    } for numero in range(1, total_rondas + 1)]
    final = next(p for p in partidas if p.numero_ronda == total_rondas)
    resultado_final = getattr(final, 'resultado_oficial', None)
    return render(request, 'torneos/bracket.html', {
        'torneo': torneo,
        'rondas': rondas,
        'campeon': resultado_final.ganador.nick_historico if resultado_final and resultado_final.ganador_id else None,
		'clasificacion': torneo.clasificaciones.select_related('inscripcion').all() if torneo.estado == Torneo.Estado.FINALIZADO else [],
    })