from django import forms
from django.db.models import Q

from arbitraje.models import ArbitroTorneo, HistorialAsignacionArbitro
from partidas.models import ParticipantePartida
from torneos.models import InscripcionTorneo
from usuarios.models import Usuario

from .models import Denuncia


class DenunciaForm(forms.Form):
	categoria = forms.ChoiceField(choices=Denuncia.Categoria.choices, label='Categoría')
	usuario_denunciado = forms.ModelChoiceField(
		queryset=Usuario.objects.none(), required=False, label='Usuario relacionado (opcional)',
	)
	descripcion = forms.CharField(
		widget=forms.Textarea(attrs={'rows': 6, 'maxlength': 10000}),
		min_length=1,
		max_length=10000,
		label='Descripción',
	)

	def __init__(self, *args, denunciante, torneo=None, partida=None, **kwargs):
		super().__init__(*args, **kwargs)
		usuario_ids = set()
		if torneo:
			usuario_ids.add(torneo.organizador_id)
			usuario_ids.update(InscripcionTorneo.objects.filter(
				torneo=torneo,
			).values_list('usuario_id', flat=True))
			usuario_ids.update(ArbitroTorneo.objects.filter(
				torneo=torneo,
			).values_list('usuario_id', flat=True))
		if partida:
			usuario_ids.update(ParticipantePartida.objects.filter(
				partida=partida,
			).values_list('inscripcion__usuario_id', flat=True))
			usuario_ids.update(HistorialAsignacionArbitro.objects.filter(
				partida=partida,
			).values_list('arbitro_anterior__usuario_id', flat=True))
			usuario_ids.update(HistorialAsignacionArbitro.objects.filter(
				partida=partida,
			).values_list('arbitro_nuevo__usuario_id', flat=True))
			if partida.arbitro_asignado_id:
				usuario_ids.add(partida.arbitro_asignado.usuario_id)
		self.fields['usuario_denunciado'].queryset = Usuario.objects.filter(
			pk__in=usuario_ids,
		).exclude(pk=denunciante.pk).order_by('username')


class ResolverDenunciaForm(forms.Form):
	estado = forms.ChoiceField(
		choices=(
			(Denuncia.Estado.RESUELTA, Denuncia.Estado.RESUELTA.label),
			(Denuncia.Estado.DESESTIMADA, Denuncia.Estado.DESESTIMADA.label),
		),
		label='Decisión',
	)
	resolucion = forms.CharField(
		widget=forms.Textarea(attrs={'rows': 4, 'maxlength': 4000}),
		min_length=1,
		max_length=4000,
		label='Resolución visible para el denunciante',
	)
	notas_internas = forms.CharField(
		required=False,
		widget=forms.Textarea(attrs={'rows': 3, 'maxlength': 4000}),
		max_length=4000,
		label='Notas internas (no visibles para usuarios)',
	)
	tipo_sancion = forms.ChoiceField(
		required=False,
		choices=(('', 'Sin sanción'), *Denuncia.TipoSancion.choices),
		label='Medida administrativa',
	)

	def __init__(self, *args, denuncia, **kwargs):
		super().__init__(*args, **kwargs)
		self.denuncia = denuncia

	def clean(self):
		limpio = super().clean()
		tipo_sancion = limpio.get('tipo_sancion')
		if limpio.get('estado') == Denuncia.Estado.DESESTIMADA and tipo_sancion:
			self.add_error('tipo_sancion', 'Una denuncia desestimada no puede aplicar sanciones.')
		if tipo_sancion and self.denuncia.usuario_denunciado_id is None:
			self.add_error('tipo_sancion', 'Para aplicar una medida debe existir un usuario relacionado.')
		return limpio