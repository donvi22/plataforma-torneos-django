from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.validators import UnicodeUsernameValidator

from .models import Usuario


class RegistroUsuarioForm(UserCreationForm):
    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if Usuario.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Este nickname ya existe.')
        return username

    class Meta:
        model = Usuario
        fields = ('username', 'email', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if Usuario.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Este correo electrónico ya existe.')
        return email

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.email = self.cleaned_data['email']
        usuario.first_name = None
        usuario.last_name = None
        usuario.rol_global = Usuario.RolGlobal.PLAYER
        usuario.estado_cuenta = Usuario.EstadoCuenta.ACTIVA
        usuario.is_active = True
        usuario.is_staff = False
        usuario.is_superuser = False
        if commit:
            usuario.save()
        return usuario


class InicioSesionForm(AuthenticationForm):
    username = forms.CharField(label='Nickname')
    error_messages = {
        'invalid_login': 'Las credenciales no son correctas.',
        'inactive': 'Las credenciales no son correctas.',
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.puede_operar:
            raise forms.ValidationError(
                self.error_messages['inactive'], code='inactive',
            )


class CambiarNicknameForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        validators=[UnicodeUsernameValidator()],
        label='Nuevo nickname',
    )

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if not username:
            raise forms.ValidationError('El nickname no puede estar vacío.')
        if Usuario.objects.filter(username__iexact=username).exclude(pk=self.usuario.pk).exists():
            raise forms.ValidationError('Este nickname ya existe.')
        return username


class ConfirmarEliminacionForm(forms.Form):
    password = forms.CharField(label='Contraseña actual', widget=forms.PasswordInput)
    confirmacion = forms.CharField(label='Escribe ELIMINAR para confirmar', max_length=20)

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.usuario.check_password(password):
            raise forms.ValidationError('La contraseña actual no es correcta.')
        return password

    def clean_confirmacion(self):
        confirmacion = self.cleaned_data['confirmacion'].strip()
        if confirmacion != 'ELIMINAR':
            raise forms.ValidationError('Escribe ELIMINAR exactamente para confirmar.')
        return confirmacion


class EditarPerfilForm(forms.ModelForm):
    MAX_AVATAR_BYTES = 2 * 1024 * 1024
    avatar = forms.FileField(required=False, label='Avatar')

    class Meta:
        model = Usuario
        fields = (
            'avatar',
            'disponible_para_arbitrar',
            'mostrar_ultima_conexion',
            'mostrar_estado_online',
        )

    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')
        if not avatar:
            return avatar
        if avatar.size > self.MAX_AVATAR_BYTES:
            raise forms.ValidationError('El avatar no puede superar los 2 MB.')
        try:
            imagen = Image.open(avatar)
            imagen.verify()
        except (UnidentifiedImageError, OSError):
            raise forms.ValidationError('El archivo debe ser una imagen válida.')
        avatar.seek(0)
        extension = (imagen.format or '').lower()
        extensiones = {'jpeg': '.jpg', 'png': '.png', 'webp': '.webp', 'gif': '.gif'}
        if extension not in extensiones:
            raise forms.ValidationError('Utiliza una imagen PNG, JPEG, GIF o WebP.')
        avatar.name = f'{uuid4().hex}{extensiones[extension]}'
        return avatar