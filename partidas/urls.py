from django.urls import path

from .views import (
    confirmar_checkin_partida,
    declarar_resultado_partida,
    detalle_partida,
    mis_partidas,
    validar_resultado_partida,
)
from .programacion_views import calendario_partidas, editar_lobby_partida, programar_partida_web

urlpatterns = [
    path('torneo/<int:torneo_pk>/calendario/', calendario_partidas, name='calendario-partidas'),
    path('<int:pk>/programar/', programar_partida_web, name='programar-partida'),
    path('<int:pk>/lobby/', editar_lobby_partida, name='editar-lobby-partida'),
    path('mias/', mis_partidas, name='mis-partidas'),
    path('<int:pk>/confirmar-checkin/', confirmar_checkin_partida, name='confirmar-checkin-partida'),
    path('<int:pk>/declarar-resultado/', declarar_resultado_partida, name='declarar-resultado-partida'),
    path('<int:pk>/validar-resultado/', validar_resultado_partida, name='validar-resultado-partida'),
    path('<int:pk>/', detalle_partida, name='detalle-partida'),
]