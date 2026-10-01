from django.urls import path

from .public_views import catalogo, ficha
from .bracket_views import bracket
from .manage_views import (
    cancelar_inscripcion_web,
    crear,
    editar_borrador,
    inscribir,
    mis_participaciones,
    mis_torneos,
    publicar,
    preparar_torneo,
	 cerrar_torneo_web,
    rangos_por_videojuego,
)

urlpatterns = [
    path('', catalogo, name='catalogo-torneos'),
    path('crear/', crear, name='crear-torneo'),
    path('mios/', mis_torneos, name='mis-torneos'),
    path('participaciones/', mis_participaciones, name='mis-participaciones'),
    path('rangos/<int:videojuego_pk>/', rangos_por_videojuego, name='rangos-por-videojuego'),
    path('<int:pk>/inscribirse/', inscribir, name='inscribirse-torneo'),
    path('<int:pk>/bracket/', bracket, name='bracket-torneo'),
    path('<int:pk>/preparar/', preparar_torneo, name='preparar-torneo'),
	path('<int:pk>/cerrar/', cerrar_torneo_web, name='cerrar-torneo'),
    path('inscripciones/<int:pk>/cancelar/', cancelar_inscripcion_web, name='cancelar-inscripcion'),
    path('<int:pk>/editar/', editar_borrador, name='editar-borrador-torneo'),
    path('<int:pk>/publicar/', publicar, name='publicar-torneo'),
    path('<int:pk>/', ficha, name='ficha-torneo'),
]