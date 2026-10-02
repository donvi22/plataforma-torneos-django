from django.urls import path

from . import views

urlpatterns = [
	path('', views.centro, name='centro-notificaciones'),
	path('marcar-todas/', views.marcar_todas_como_leidas, name='marcar-todas-notificaciones-leidas'),
	path('<int:pk>/abrir/', views.abrir, name='abrir-notificacion'),
	path('<int:pk>/leer/', views.marcar_como_leida, name='marcar-notificacion-leida'),
]