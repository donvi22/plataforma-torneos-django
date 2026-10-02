from django.urls import path

from . import views


urlpatterns = [
	path('', views.centro, name='centro-arbitraje'),
	path('disponibilidad/', views.cambiar_disponibilidad_web, name='cambiar-disponibilidad-arbitraje'),
	path('invitaciones/<int:pk>/<str:respuesta>/', views.responder_invitacion, name='responder-invitacion-arbitral'),
	path('torneos/<int:pk>/', views.gestionar_torneo, name='gestionar-arbitros-torneo'),
]