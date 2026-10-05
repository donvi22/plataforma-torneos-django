from django import template
from django.urls import reverse
from django.utils.html import format_html

register = template.Library()


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
