from django import forms


class ProgramarPartidaForm(forms.Form):
    # Django interpreta el valor naive en la zona horaria activa (Europe/Madrid).
    fecha_hora = forms.DateTimeField(
        label='Fecha y hora',
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'],
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
    )
    motivo = forms.CharField(label='Motivo del cambio', required=False, max_length=300)


class LobbyPartidaForm(forms.Form):
    nombre_lobby = forms.CharField(label='Nombre o sala', required=False, max_length=150)
    codigo_lobby = forms.CharField(label='Código de sala', required=False, max_length=150)
    contrasena_lobby = forms.CharField(
        label='Contraseña', required=False, max_length=150,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
    )
    quitar_contrasena = forms.BooleanField(label='Quitar la contraseña actual', required=False)
    instrucciones_lobby = forms.CharField(
        label='Instrucciones', required=False, max_length=500,
        widget=forms.Textarea(attrs={'rows': 3, 'maxlength': 500}),
    )
