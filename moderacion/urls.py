from django.urls import path

from . import views


urlpatterns = [
	path('', views.centro, name='centro-moderacion'),
	path('mis-denuncias/', views.mis_denuncias, name='mis-denuncias'),
	path('denunciar/torneos/<int:pk>/', views.denunciar_torneo, name='denunciar-torneo'),
	path('denunciar/partidas/<int:pk>/', views.denunciar_partida, name='denunciar-partida'),
	path('denuncias/<int:pk>/', views.detalle_denuncia, name='detalle-denuncia'),
	path('denuncias/<int:pk>/revision/', views.tomar_en_revision_web, name='tomar-denuncia-en-revision'),
	path('denuncias/<int:pk>/resolver/', views.resolver_denuncia_web, name='resolver-denuncia'),
]