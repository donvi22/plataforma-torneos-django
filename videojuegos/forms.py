from django import forms

from .models import PerfilVideojuegoUsuario, RangoVideojuego


class PerfilVideojuegoForm(forms.ModelForm):
    class Meta:
        model = PerfilVideojuegoUsuario
        fields = ('nick_en_juego', 'rango_declarado')
        labels = {
            'nick_en_juego': 'Nickname dentro del videojuego',
            'rango_declarado': 'Rango declarado',
        }
        help_texts = {
            'rango_declarado': 'Este rango es autodeclarado y todavía no se verifica mediante una API externa.',
        }

    def __init__(self, *args, videojuego, **kwargs):
        super().__init__(*args, **kwargs)
        self.videojuego = videojuego
        self.fields['rango_declarado'].error_messages['invalid_choice'] = (
            'Selecciona un rango perteneciente a este videojuego.'
        )
        self.fields['rango_declarado'].queryset = RangoVideojuego.objects.filter(
            videojuego=videojuego,
        ).order_by('posicion')

    def clean_rango_declarado(self):
        rango = self.cleaned_data.get('rango_declarado')
        if rango and rango.videojuego_id != self.videojuego.pk:
            raise forms.ValidationError('Selecciona un rango perteneciente a este videojuego.')
        return rango

    def save(self, commit=True):
        perfil = super().save(commit=False)
        perfil.videojuego = self.videojuego
        if commit:
            perfil.save()
        return perfil