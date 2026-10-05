"""Generacion transaccional del bracket de eliminacion directa.

SQLite no ofrece bloqueos de fila efectivos. El servicio usa un RLock por
proceso y una transaccion atomica; la restriccion unica del modelo protege
contra duplicados y, para despliegues con varios procesos, se recomienda un
motor con select_for_update para aislar completamente la generacion.
"""

import random
import re
from contextlib import contextmanager
from threading import RLock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from torneos.models import InscripcionTorneo, Torneo
from torneos.services import _es_administrador_autorizado, _registrar_transicion

from .models import (
    DeclaracionResultado,
    HistorialResultadoPartida,
    ParticipantePartida,
    Partida,
    ResultadoPartida,
)


class BracketError(Exception):
    """Error de negocio al generar o validar un bracket."""


class ResultadoError(Exception):
    """Error de negocio al declarar o validar un resultado."""


class PreparacionBracketError(Exception):
    """Error de autorización o estado al preparar manualmente un bracket."""


_bracket_lock = RLock()


def _validar_marcador_normal(resultado):
    resultado = (resultado or '').strip()
    if not re.fullmatch(r'\d+-\d+', resultado):
        raise ResultadoError('El marcador debe usar el formato puntos-puntos, por ejemplo 2-1.')
    return resultado


@contextmanager
def _bloqueo_de_bracket():
    with _bracket_lock:
        with transaction.atomic():
            yield


def _validar_bracket_existente(torneo, partidas):
    numero_participantes = torneo.max_participantes
    total_esperado = numero_participantes - 1
    if partidas.count() != total_esperado:
        raise BracketError(
            'Existe un bracket incompleto; requiere revisión manual y no será reconstruido.'
        )
    por_clave = {(partida.numero_ronda, partida.numero_orden): partida for partida in partidas}
    numero_rondas = numero_participantes.bit_length() - 1
    for ronda in range(1, numero_rondas + 1):
        partidas_en_ronda = numero_participantes // (2 ** ronda)
        for orden in range(1, partidas_en_ronda + 1):
            partida = por_clave.get((ronda, orden))
            if partida is None:
                raise BracketError('El bracket existente tiene rondas u órdenes incoherentes.')
            if ronda < numero_rondas:
                siguiente = por_clave.get((ronda + 1, (orden + 1) // 2))
                posicion = 1 if orden % 2 else 2
                if partida.siguiente_partida_id != getattr(siguiente, 'pk', None):
                    raise BracketError('El bracket existente tiene relaciones incoherentes.')
                if partida.posicion_en_siguiente_partida != posicion:
                    raise BracketError('El bracket existente tiene posiciones de avance incoherentes.')
            elif partida.siguiente_partida_id is not None:
                raise BracketError('La final no puede tener una siguiente partida.')
    return list(partidas)


def generar_bracket(torneo):
    """Genera y devuelve todas las partidas de un bracket completo."""
    with _bloqueo_de_bracket():
        torneo = Torneo.objects.get(pk=torneo.pk)
        if torneo.formato_competitivo.tipo_regla_participantes != torneo.formato_competitivo.TipoReglaParticipantes.ELIMINACION_DIRECTA:
            raise BracketError('El torneo no utiliza eliminación directa.')
        existentes = Partida.objects.filter(torneo=torneo).order_by('numero_ronda', 'numero_orden')
        if existentes.exists():
            return _validar_bracket_existente(torneo, existentes)
        if torneo.estado != Torneo.Estado.INSCRIPCIONES_CERRADAS:
            raise BracketError('Las inscripciones deben estar cerradas definitivamente.')
        confirmadas = list(
            InscripcionTorneo.objects.filter(
                torneo=torneo,
                estado=InscripcionTorneo.Estado.CONFIRMADA,
            ).order_by('pk')
        )
        if len(confirmadas) != torneo.max_participantes:
            raise BracketError('El torneo debe tener exactamente todas sus plazas confirmadas.')
        if not torneo.formato_competitivo.es_tamano_valido(len(confirmadas)):
            raise BracketError('El número de participantes no es válido para eliminación directa.')

        inscripciones = confirmadas[:]
        random.shuffle(inscripciones)
        numero_rondas = len(inscripciones).bit_length() - 1
        creadas = {}
        for ronda in range(1, numero_rondas + 1):
            cantidad = len(inscripciones) // (2 ** ronda)
            for orden in range(1, cantidad + 1):
                partida = Partida.objects.create(
                    torneo=torneo,
                    numero_ronda=ronda,
                    numero_orden=orden,
                    estado=Partida.Estado.PENDIENTE,
                )
                creadas[(ronda, orden)] = partida

        for ronda in range(1, numero_rondas):
            cantidad = len(inscripciones) // (2 ** ronda)
            for orden in range(1, cantidad + 1):
                partida = creadas[(ronda, orden)]
                siguiente = creadas[(ronda + 1, (orden + 1) // 2)]
                partida.siguiente_partida = siguiente
                partida.posicion_en_siguiente_partida = 1 if orden % 2 else 2
                partida.save(update_fields=('siguiente_partida', 'posicion_en_siguiente_partida'))

        for posicion, inscripcion in enumerate(inscripciones, start=1):
            orden = (posicion + 1) // 2
            posicion_en_partida = 1 if posicion % 2 else 2
            ParticipantePartida.objects.create(
                partida=creadas[(1, orden)],
                inscripcion=inscripcion,
                posicion=posicion_en_partida,
            )

        if torneo.estado != Torneo.Estado.PREPARADO:
            _registrar_transicion(
                torneo,
                Torneo.Estado.PREPARADO,
                motivo='Bracket de eliminación directa generado.',
            )
        return list(creadas.values())


def preparar_bracket(torneo, actor):
    """Prepara manualmente brackets privados u oficiales autorizados."""
    from torneos.services import _es_administrador_autorizado, _registrar_transicion

    if torneo.tipo == Torneo.Tipo.PUBLICO:
        raise PreparacionBracketError('Los torneos públicos se preparan mediante el calendario.')
    if torneo.tipo == Torneo.Tipo.OFICIAL and not _es_administrador_autorizado(actor):
        raise PreparacionBracketError('Solo un administrador autorizado puede preparar torneos oficiales.')
    if torneo.tipo == Torneo.Tipo.PRIVADO and torneo.organizador_id != actor.pk and not _es_administrador_autorizado(actor):
        raise PreparacionBracketError('Solo el organizador puede preparar este torneo privado.')
    if torneo.participantes_confirmados != torneo.max_participantes:
        raise PreparacionBracketError('El torneo no tiene todas sus plazas confirmadas.')
    with transaction.atomic():
        torneo = Torneo.objects.get(pk=torneo.pk)
        if torneo.estado == Torneo.Estado.BORRADOR:
            raise PreparacionBracketError('El torneo todavía es un borrador.')
        if torneo.estado != Torneo.Estado.INSCRIPCIONES_CERRADAS:
            _registrar_transicion(torneo, Torneo.Estado.INSCRIPCIONES_CERRADAS, actor=actor, motivo='Cierre autorizado para preparar el bracket.')
        return generar_bracket(torneo)


def _partida_bloqueada(partida_id):
    return Partida.objects.select_related('torneo').get(pk=partida_id)


def declarar_resultado(partida, usuario, resultado_declarado, ganador_declarado=None):
    """Registra una declaración sin tocar el resultado oficial ni el bracket."""
    if not usuario or not usuario.puede_operar:
        raise ResultadoError('La cuenta no está activa para declarar resultados.')
    partida = Partida.objects.get(pk=partida.pk)
    if partida.estado not in (Partida.Estado.EN_CURSO, Partida.Estado.PENDIENTE_VALIDACION):
        raise ResultadoError('La partida ya no admite declaraciones.')
    if hasattr(partida, 'resultado_oficial'):
        raise ResultadoError('La partida ya tiene un resultado oficial validado.')
    if partida.participantes.count() != 2:
        raise ResultadoError('La partida debe tener dos participantes.')
    resultado_declarado = _validar_marcador_normal(resultado_declarado)
    if DeclaracionResultado.objects.filter(partida=partida, usuario=usuario).exists():
        raise ResultadoError('El usuario ya presentó una declaración para esta partida.')
    declaracion = DeclaracionResultado(
        partida=partida,
        usuario=usuario,
        resultado_declarado=resultado_declarado,
        ganador_declarado=ganador_declarado,
    )
    try:
        declaracion.full_clean()
        declaracion.save()
    except ValidationError as error:
        raise ResultadoError('; '.join(error.messages)) from error
    if partida.estado == Partida.Estado.EN_CURSO:
        partida.estado = Partida.Estado.PENDIENTE_VALIDACION
        partida.save(update_fields=('estado',))
    return declaracion


def _usuario_puede_validar(partida, usuario):
    arbitro_asignado = getattr(partida, 'arbitro_asignado', None)
    return bool(
        usuario
        and usuario.puede_operar
        and (
            partida.torneo.organizador_id == usuario.pk
            or _es_administrador_autorizado(usuario)
            or (
                arbitro_asignado
                and arbitro_asignado.usuario_id == usuario.pk
                and arbitro_asignado.estado_invitacion == 'ACEPTADA'
                and arbitro_asignado.activo_en_torneo
            )
        )
    )


def _crear_historial(resultado, actor, motivo):
    HistorialResultadoPartida.objects.create(
        partida=resultado.partida,
        resultado_nuevo=resultado.resultado,
        ganador_nuevo=resultado.ganador,
        tipo_resultado_nuevo=resultado.tipo_resultado,
        usuario_responsable=actor,
        motivo=motivo,
    )


def _registrar_participante_siguiente(partida, ganador):
    if not partida.siguiente_partida_id:
        return
    siguiente = Partida.objects.get(pk=partida.siguiente_partida_id)
    existente = siguiente.participantes.filter(posicion=partida.posicion_en_siguiente_partida).first()
    if existente:
        if existente.inscripcion_id != ganador.pk:
            raise ResultadoError('La posición de avance ya está ocupada por otro participante.')
    else:
        ParticipantePartida.objects.create(
            partida=siguiente,
            inscripcion=ganador,
            posicion=partida.posicion_en_siguiente_partida,
        )
    if siguiente.participantes.count() == 2 and siguiente.fecha_hora_rivales_confirmados is None:
        siguiente.fecha_hora_rivales_confirmados = timezone.now()
        siguiente.save(update_fields=('fecha_hora_rivales_confirmados',))
    _resolver_rama(siguiente)


def _resolver_rama(partida):
    """Resuelve solo cuando todas las partidas alimentadoras tienen resultado."""
    if hasattr(partida, 'resultado_oficial'):
        return
    alimentadoras = list(partida.partidas_anteriores.all())
    if not alimentadoras or not all(hasattr(alimentadora, 'resultado_oficial') for alimentadora in alimentadoras):
        return
    participantes = list(partida.participantes.all())
    if len(participantes) == 2:
        return
    if len(participantes) == 1:
        validar_resultado(
            partida,
            None,
            resultado='',
            ganador=participantes[0].inscripcion,
            tipo_resultado=ResultadoPartida.TipoResultado.AVANCE_AUTOMATICO,
            motivo='La rama contraria quedó definitivamente vacía.',
            _resolucion_automatica=True,
        )
    elif len(participantes) == 0:
        validar_resultado(
            partida,
            None,
            resultado='',
            ganador=None,
            tipo_resultado=ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA,
            motivo='Ambas ramas quedaron definitivamente vacías.',
            _resolucion_automatica=True,
        )


def validar_resultado(
    partida,
    actor,
    resultado,
    ganador=None,
    tipo_resultado=ResultadoPartida.TipoResultado.NORMAL,
    motivo='',
    _resolucion_automatica=False,
):
    """Valida un resultado una sola vez y propaga su consecuencia."""
    with _bracket_lock:
        with transaction.atomic():
            partida = _partida_bloqueada(partida.pk)
            if not _resolucion_automatica and not _usuario_puede_validar(partida, actor):
                raise ResultadoError('El usuario no puede validar esta partida.')
            if hasattr(partida, 'resultado_oficial'):
                raise ResultadoError('La partida ya tiene un resultado oficial validado.')
            participantes = list(partida.participantes.all())
            if tipo_resultado in (
                ResultadoPartida.TipoResultado.NORMAL,
                ResultadoPartida.TipoResultado.INCOMPARECENCIA,
            ):
                if len(participantes) != 2 or ganador is None:
                    raise ResultadoError('Este resultado necesita dos participantes y un ganador.')
            if tipo_resultado == ResultadoPartida.TipoResultado.NORMAL:
                resultado = _validar_marcador_normal(resultado)
            if (
                tipo_resultado == ResultadoPartida.TipoResultado.NORMAL
                and partida.torneo.tipo in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL)
                and partida.estado not in (Partida.Estado.EN_CURSO, Partida.Estado.PENDIENTE_VALIDACION)
            ):
                raise ResultadoError('El resultado normal requiere una partida iniciada correctamente.')
            if tipo_resultado == ResultadoPartida.TipoResultado.DOBLE_INCOMPARECENCIA and ganador is not None:
                raise ResultadoError('La doble incomparecencia no tiene ganador.')
            if ganador is not None and not any(participante.inscripcion_id == ganador.pk for participante in participantes):
                raise ResultadoError('El ganador debe pertenecer a la partida.')
            if not _resolucion_automatica and tipo_resultado == ResultadoPartida.TipoResultado.AVANCE_AUTOMATICO:
                raise ResultadoError('El avance automático solo puede resolverlo el sistema.')
            resultado_oficial = ResultadoPartida(
                partida=partida,
                resultado=resultado,
                ganador=ganador,
                tipo_resultado=tipo_resultado,
                validado_por=actor,
                motivo=motivo,
            )
            try:
                resultado_oficial.full_clean()
                resultado_oficial.save()
            except ValidationError as error:
                raise ResultadoError('; '.join(error.messages)) from error
            partida.estado = Partida.Estado.FINALIZADA
            partida.fecha_hora_fin_real = timezone.now()
            partida.save(update_fields=('estado', 'fecha_hora_fin_real'))
            if ganador:
                _registrar_participante_siguiente(partida, ganador)
            elif partida.siguiente_partida_id:
                _resolver_rama(Partida.objects.get(pk=partida.siguiente_partida_id))
            _crear_historial(resultado_oficial, actor or partida.torneo.organizador, motivo or 'Resultado validado.')
            from usuarios.karma import premiar_resultado_validado
            transaction.on_commit(
                lambda resultado_id=resultado_oficial.pk: premiar_resultado_validado(resultado_id),
                robust=True,
            )
            try:
                from notificaciones.models import Notificacion
                from notificaciones.services import crear_notificacion
                for participante in participantes:
                    crear_notificacion(participante.inscripcion.usuario, Notificacion.Tipo.RESULTADO_OFICIAL, 'Resultado oficial', f'Se ha validado el resultado de {partida}.', es_critica=True, clave_evento=f'resultado:{partida.pk}', torneo=partida.torneo, partida=partida)
            except Exception:
                pass
            if partida.siguiente_partida_id is None and partida.torneo.tipo == Torneo.Tipo.PUBLICO:
                from torneos.services import CierreTorneoError, cerrar_torneo
                try:
                    cerrar_torneo(partida.torneo, actor=actor, automatico=True)
                except CierreTorneoError:
                    pass
            return resultado_oficial


def final_tiene_campeon(torneo):
    final = torneo.partidas.order_by('-numero_ronda', 'numero_orden').first()
    return bool(
        final
        and hasattr(final, 'resultado_oficial')
        and final.resultado_oficial.ganador_id
    )
