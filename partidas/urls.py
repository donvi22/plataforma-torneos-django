from django.urls import path

from .views import (
    confirmar_checkin_partida,
    declarar_resultado_partida,
    detalle_partida,
    mis_partidas,
    validar_resultado_partida,
)

urlpatterns = [
    path('mias/', mis_partidas, name='mis-partidas'),
    path('<int:pk>/confirmar-checkin/', confirmar_checkin_partida, name='confirmar-checkin-partida'),
    path('<int:pk>/declarar-resultado/', declarar_resultado_partida, name='declarar-resultado-partida'),
    path('<int:pk>/validar-resultado/', validar_resultado_partida, name='validar-resultado-partida'),
    path('<int:pk>/', detalle_partida, name='detalle-partida'),
]