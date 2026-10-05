"""Acceso por invitación a torneos privados."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AccesoTorneoPrivado, InscripcionTorneo, Torneo
from .services import InscripcionError, inscribir_usuario

ESTADOS_NUEVO_ACCESO = (Torneo.Estado.INSCRIPCIONES_ABIERTAS, Torneo.Estado.PRORROGA)


def tiene_acceso_privado(torneo, usuario):
    return bool(
        usuario
        and usuario.is_authenticated
        and AccesoTorneoPrivado.objects.filter(
            torneo=torneo, usuario=usuario, estado=AccesoTorneoPrivado.Estado.ACTIVO,
        ).exists()
    )


def acceder_con_codigo(codigo, usuario):
    """Devuelve el torneo si el código sirve; None sin distinguir el motivo."""
    if not codigo or not usuario or not usuario.puede_operar:
        return None
    torneo = Torneo.objects.filter(tipo=Torneo.Tipo.PRIVADO, codigo_acceso=codigo).first()
    if torneo is None:
        return None
    if torneo.organizador_id == usuario.pk:
        return torneo
    acceso = AccesoTorneoPrivado.objects.filter(torneo=torneo, usuario=usuario).first()
    if acceso:
        return torneo if acceso.estado == AccesoTorneoPrivado.Estado.ACTIVO else None
    if torneo.estado not in ESTADOS_NUEVO_ACCESO:
        return None
    try:
        with transaction.atomic():
            AccesoTorneoPrivado.objects.create(torneo=torneo, usuario=usuario)
    except IntegrityError:
        pass
    return torneo


def inscribir_en_privado(torneo, usuario):
    """Aplica las reglas comunes de inscripción exigiendo acceso previo por invitación."""
    if torneo.tipo != Torneo.Tipo.PRIVADO or not tiene_acceso_privado(torneo, usuario):
        raise InscripcionError('No tienes acceso a este torneo privado.')
    return inscribir_usuario(torneo, usuario)


def regenerar_codigo_privado(torneo, actor):
    if torneo.tipo != Torneo.Tipo.PRIVADO or torneo.organizador_id != actor.pk:
        raise InscripcionError('Solo el organizador puede regenerar la invitación.')
    with transaction.atomic():
        torneo = Torneo.objects.get(pk=torneo.pk)
        if torneo.estado in (Torneo.Estado.FINALIZADO, Torneo.Estado.CANCELADO):
            raise InscripcionError('El torneo ya no admite nuevas invitaciones.')
        torneo.codigo_acceso = Torneo.generar_codigo_acceso()
        torneo.save(update_fields=('codigo_acceso', 'fecha_actualizacion'))
    return torneo


def revocar_acceso_privado(acceso, actor):
    with transaction.atomic():
        acceso = AccesoTorneoPrivado.objects.select_related('torneo').get(pk=acceso.pk)
        if acceso.torneo.organizador_id != actor.pk:
            raise InscripcionError('Solo el organizador puede revocar accesos.')
        if acceso.estado != AccesoTorneoPrivado.Estado.ACTIVO:
            raise InscripcionError('El acceso ya estaba revocado.')
        if InscripcionTorneo.objects.filter(
            torneo=acceso.torneo, usuario_id=acceso.usuario_id, estado=InscripcionTorneo.Estado.CONFIRMADA,
        ).exists():
            raise InscripcionError('El usuario ya participa en el torneo; no se puede revocar su acceso.')
        acceso.estado = AccesoTorneoPrivado.Estado.REVOCADO
        acceso.fecha_revocacion = timezone.now()
        acceso.save(update_fields=('estado', 'fecha_revocacion'))
    return acceso
