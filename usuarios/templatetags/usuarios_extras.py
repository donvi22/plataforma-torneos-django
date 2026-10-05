from django import template
from django.urls import reverse
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def identidad_competitiva(usuario, nick):
    """Nick competitivo del torneo como texto principal y @username de la plataforma como secundario."""
    if usuario is None or usuario.estado_cuenta == usuario.EstadoCuenta.ELIMINADA:
        cuenta = 'Usuario eliminado'
    elif not usuario.puede_operar:
        cuenta = f'@{usuario.username}'
    else:
        cuenta = format_html(
            '<a class="text-link" href="{}">@{}</a>', reverse('perfil', args=[usuario.pk]), usuario.username,
        )
    return format_html(
        '<span class="player-identity"><span class="player-nick">{}</span><span class="player-account">{}</span></span>',
        nick, cuenta,
    )


@register.simple_tag
def enlace_usuario(usuario, texto=None):
    """Enlaza al perfil actual solo si la cuenta está activa; el texto histórico no cambia."""
    if usuario is None or usuario.estado_cuenta == usuario.EstadoCuenta.ELIMINADA:
        return texto or 'Usuario eliminado'
    if not usuario.puede_operar:
        return texto or usuario.username
    return format_html(
        '<a class="text-link user-link" href="{}">{}</a>',
        reverse('perfil', args=[usuario.pk]),
        texto or usuario.username,
    )
