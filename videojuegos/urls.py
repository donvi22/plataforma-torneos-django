from django.urls import path

from .views import catalogo, crear_perfil, editar_perfil_videojuego, ficha

urlpatterns = [
    path('', catalogo, name='catalogo-videojuegos'),
    path('<int:pk>/', ficha, name='ficha-videojuego'),
    path('<int:videojuego_pk>/perfil/crear/', crear_perfil, name='crear-perfil-videojuego'),
    path('<int:videojuego_pk>/perfil/editar/', editar_perfil_videojuego, name='editar-perfil-videojuego'),
]