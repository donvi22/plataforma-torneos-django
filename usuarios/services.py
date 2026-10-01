"""Progresion y concesion de XP por clasificaciones finales."""

from decimal import Decimal, ROUND_HALF_UP
from math import isqrt
from threading import RLock

from django.db import IntegrityError, transaction

from torneos.models import ClasificacionTorneo, Torneo

from .models import HistorialXP, Usuario


class XPError(Exception):
    """Error de coherencia o configuración al conceder XP."""


_xp_lock = RLock()
_BASE_XP = (
    (1, 100),
    (2, 70),
    (4, 50),
    (8, 20),
    (16, 10),
)


def nivel_para_xp(xp_total):
    """Calcula el nivel para el umbral acumulado 100 * (nivel - 1)^2."""
    if xp_total < 0:
        raise XPError('La XP total no puede ser negativa.')
    return isqrt(xp_total // 100) + 1


def umbral_nivel(nivel):
    if nivel < 1:
        raise XPError('El nivel debe ser al menos 1.')
    return 100 * (nivel - 1) ** 2


def progreso_nivel(xp_total):
    nivel = nivel_para_xp(xp_total)
    inicio = umbral_nivel(nivel)
    siguiente = umbral_nivel(nivel + 1)
    return {
        'nivel': nivel,
        'xp_en_nivel': xp_total - inicio,
        'xp_para_siguiente': siguiente - inicio,
        'xp_restante': siguiente - xp_total,
    }


def xp_base_por_posicion(posicion):
    for limite, recompensa in _BASE_XP:
        if posicion <= limite:
            return recompensa
    return 5


def _parametros_xp(torneo):
    if torneo.tipo == Torneo.Tipo.PRIVADO:
        raise XPError('Los torneos privados no conceden XP.')
    if torneo.tipo not in (Torneo.Tipo.PUBLICO, Torneo.Tipo.OFICIAL):
        raise XPError('El tipo de torneo no admite recompensas de XP.')
    dificultad = torneo.coeficiente_dificultad or Decimal('1.000')
    if dificultad <= 0:
        raise XPError('El coeficiente de dificultad debe ser positivo.')
    multiplicador = Decimal('1.00')
    if torneo.modo_xp == Torneo.ModoXP.ESPECIAL:
        if torneo.tipo != Torneo.Tipo.OFICIAL:
            raise XPError('El multiplicador especial solo es válido para torneos oficiales.')
        if torneo.multiplicador_xp_especial is None or torneo.multiplicador_xp_especial <= 0:
            raise XPError('El multiplicador especial oficial debe ser positivo.')
        multiplicador = torneo.multiplicador_xp_especial
    elif torneo.modo_xp != Torneo.ModoXP.AUTOMATICA:
        raise XPError('El modo de XP no es válido.')
    factor_tamano = Decimal('1.0') + (Decimal(torneo.max_participantes.bit_length() - 1) / Decimal('10'))
    return dificultad, multiplicador, factor_tamano


def calcular_xp_clasificacion(clasificacion):
    torneo = clasificacion.torneo
    dificultad, multiplicador, factor_tamano = _parametros_xp(torneo)
    recompensa = Decimal(xp_base_por_posicion(clasificacion.posicion)) * factor_tamano * dificultad * multiplicador
    return int(recompensa.quantize(Decimal('1'), rounding=ROUND_HALF_UP)), dificultad, multiplicador


def _clasificacion_coherente(torneo):
    clasificaciones = list(torneo.clasificaciones.select_related('inscripcion__usuario').order_by('pk'))
    if len(clasificaciones) != torneo.max_participantes:
        raise XPError('La clasificación final está incompleta.')
    if len({fila.inscripcion_id for fila in clasificaciones}) != len(clasificaciones):
        raise XPError('La clasificación contiene inscripciones duplicadas.')
    if any(fila.inscripcion.torneo_id != torneo.pk for fila in clasificaciones):
        raise XPError('La clasificación contiene una inscripción de otro torneo.')
    if sum(fila.es_campeon for fila in clasificaciones) != 1:
        raise XPError('La clasificación debe tener exactamente un campeón.')
    return clasificaciones


def conceder_xp_torneo(torneo):
    """Concede o completa de forma segura las recompensas de una clasificación final."""
    with _xp_lock, transaction.atomic():
        torneo = Torneo.objects.get(pk=torneo.pk)
        if torneo.estado != Torneo.Estado.FINALIZADO:
            raise XPError('Solo se concede XP a torneos finalizados.')
        clasificaciones = _clasificacion_coherente(torneo)
        if torneo.tipo == Torneo.Tipo.PRIVADO:
            raise XPError('Los torneos privados no conceden XP.')
        movimientos = []
        for clasificacion in clasificaciones:
            xp, dificultad, multiplicador = calcular_xp_clasificacion(clasificacion)
            existente = HistorialXP.objects.filter(inscripcion=clasificacion.inscripcion).first()
            if existente:
                if (
                    existente.torneo_id != torneo.pk
                    or existente.clasificacion_id != clasificacion.pk
                    or existente.xp_concedida != xp
                    or existente.posicion_final != clasificacion.posicion
                    or existente.tamano_torneo != torneo.max_participantes
                    or existente.coeficiente_dificultad != dificultad
                    or existente.multiplicador_especial != multiplicador
                ):
                    raise XPError('Existe un historial de XP incoherente que requiere revisión.')
                movimientos.append(existente)
                continue
            usuario = Usuario.objects.get(pk=clasificacion.inscripcion.usuario_id)
            xp_anterior = usuario.xp_total
            nivel_anterior = usuario.nivel
            xp_posterior = xp_anterior + xp
            nivel_posterior = nivel_para_xp(xp_posterior)
            try:
                movimiento = HistorialXP.objects.create(
                    usuario=usuario,
                    torneo=torneo,
                    inscripcion=clasificacion.inscripcion,
                    clasificacion=clasificacion,
                    xp_concedida=xp,
                    xp_anterior=xp_anterior,
                    xp_posterior=xp_posterior,
                    nivel_anterior=nivel_anterior,
                    nivel_posterior=nivel_posterior,
                    posicion_final=clasificacion.posicion,
                    tamano_torneo=torneo.max_participantes,
                    coeficiente_dificultad=dificultad,
                    multiplicador_especial=multiplicador,
                    motivo=f'Clasificación final: posición {clasificacion.posicion}.',
                )
            except IntegrityError as error:
                raise XPError('No se pudo registrar la recompensa de XP de forma segura.') from error
            usuario.xp_total = xp_posterior
            usuario.nivel = nivel_posterior
            usuario.save(update_fields=('xp_total', 'nivel'))
            movimientos.append(movimiento)
        return movimientos
