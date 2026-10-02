from django.urls import path

from .views import (
    InicioSesionView,
    cerrar_sesion,
    editar_perfil,
    historial_karma,
    inicio,
	 historial_xp,
    perfil,
    perfil_propio,
    registro,
)

urlpatterns = [
    path('', inicio, name='inicio'),
    path('registro/', registro, name='registro'),
    path('login/', InicioSesionView.as_view(), name='login'),
    path('logout/', cerrar_sesion, name='logout'),
    path('perfil/', perfil_propio, name='perfil-propio'),
	path('perfil/xp/', historial_xp, name='historial-xp'),
    path('perfil/karma/', historial_karma, name='historial-karma'),
    path('perfil/<int:pk>/', perfil, name='perfil'),
    path('perfil/editar/', editar_perfil, name='editar-perfil'),
]