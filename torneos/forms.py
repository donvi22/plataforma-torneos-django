from datetime import timedelta
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from django import forms

from videojuegos.models import RangoVideojuego, Videojuego

from .models import FormatoCompetitivo, Torneo


class TorneoForm(forms.ModelForm):
    MAX_BANNER_BYTES = 5 * 1024 * 1024

    class Meta:
        model = Torneo
        fields = (
            'nombre', 'tipo', 'videojuego', 'formato_competitivo', 'max_participantes',
            'imagen_banner', 'reglas', 'nivel_minimo', 'rango_minimo', 'rango_maximo',
            'fecha_apertura_inscripciones', 'fecha_cierre_inscripciones',
            'duracion_prorroga_min', 'descanso_entre_partidas_min', 'fecha_inicio_prevista',
        )
        widgets = {
            'reglas': forms.Textarea(attrs={'rows': 5}),
            'fecha_apertura_inscripciones': forms.DateTimeInput(
                format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'},
            ),
            'fecha_cierre_inscripciones': forms.DateTimeInput(
                format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'},
            ),
            'fecha_inicio_prevista': forms.DateTimeInput(
                format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'},
            ),
        }

    input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        for nombre in (
            'fecha_apertura_inscripciones',
            'fecha_cierre_inscripciones',
            'fecha_inicio_prevista',
        ):
            self.fields[nombre].input_formats = self.input_formats
        self.fields['videojuego'].queryset = Videojuego.objects.filter(activo=True).order_by('nombre')
        self.fields['formato_competitivo'].queryset = FormatoCompetitivo.objects.filter(
            tipo_regla_participantes=FormatoCompetitivo.TipoReglaParticipantes.ELIMINACION_DIRECTA,
        ).order_by('nombre')
        self.fields['rango_minimo'].queryset = RangoVideojuego.objects.none()
        self.fields['rango_maximo'].queryset = RangoVideojuego.objects.none()
        videojuego_id = self.data.get('videojuego') or getattr(self.instance, 'videojuego_id', None)
        if str(videojuego_id).isdigit():
            rangos = RangoVideojuego.objects.filter(videojuego_id=videojuego_id).order_by('posicion')
            self.fields['rango_minimo'].queryset = rangos
            self.fields['rango_maximo'].queryset = rangos
        formato_id = self.data.get('formato_competitivo') or getattr(self.instance, 'formato_competitivo_id', None)
        tamanos = []
        if str(formato_id).isdigit():
            formato = FormatoCompetitivo.objects.filter(pk=formato_id).first()
            if formato:
                tamanos = formato.tamanos_participantes_validos
        self.fields['max_participantes'] = forms.TypedChoiceField(
            label='Número máximo de participantes',
            choices=[(tamano, tamano) for tamano in tamanos],
            coerce=int,
            required=True,
        )
        if self.instance.pk and self.instance.max_participantes in tamanos:
            self.initial['max_participantes'] = self.instance.max_participantes
        self.formato_tamanos = {
            str(formato.pk): formato.tamanos_participantes_validos
            for formato in self.fields['formato_competitivo'].queryset
        }
        self.fields['tipo'].choices = [
            choice for choice in Torneo.Tipo.choices
            if self._puede_crear_tipo(choice[0])
        ]

    def _puede_crear_tipo(self, tipo):
        if not self.usuario.is_active:
            return False
        if tipo == Torneo.Tipo.OFICIAL:
            from .services import _es_administrador_autorizado
            return _es_administrador_autorizado(self.usuario)
        if tipo == Torneo.Tipo.PUBLICO:
            return self.usuario.nivel >= 5 and self.usuario.karma_total >= 150
        return True

    def clean_imagen_banner(self):
        imagen = self.cleaned_data.get('imagen_banner')
        if not imagen:
            return imagen
        if imagen.size > self.MAX_BANNER_BYTES:
            raise forms.ValidationError('El banner no puede superar los 5 MB.')
        try:
            comprobacion = Image.open(imagen)
            comprobacion.verify()
        except (UnidentifiedImageError, OSError):
            raise forms.ValidationError('El archivo debe ser una imagen válida.')
        imagen.seek(0)
        extensiones = {'jpeg': '.jpg', 'png': '.png', 'webp': '.webp', 'gif': '.gif'}
        extension = (comprobacion.format or '').lower()
        if extension not in extensiones:
            raise forms.ValidationError('Utiliza una imagen PNG, JPEG, GIF o WebP.')
        imagen.name = f'{uuid4().hex}{extensiones[extension]}'
        return imagen

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get('tipo')
        if tipo and not self._puede_crear_tipo(tipo):
            raise forms.ValidationError('No tienes permisos para crear este tipo de torneo.')
        videojuego = cleaned.get('videojuego')
        for nombre in ('rango_minimo', 'rango_maximo'):
            rango = cleaned.get(nombre)
            if rango and videojuego and rango.videojuego_id != videojuego.pk:
                self.add_error(nombre, 'El rango debe pertenecer al videojuego seleccionado.')
        if tipo == Torneo.Tipo.PUBLICO:
            apertura = cleaned.get('fecha_apertura_inscripciones')
            cierre = cleaned.get('fecha_cierre_inscripciones')
            if apertura and cierre and (cierre - apertura).total_seconds() not in range(15 * 60, 60 * 60 + 1):
                self.add_error('fecha_cierre_inscripciones', 'La inscripción pública debe durar entre 15 y 60 minutos.')
        if tipo == Torneo.Tipo.PUBLICO:
            apertura = cleaned.get('fecha_apertura_inscripciones')
            cierre = cleaned.get('fecha_cierre_inscripciones')
            inicio = cleaned.get('fecha_inicio_prevista')
            prorroga = cleaned.get('duracion_prorroga_min') or 0
            if inicio and cierre and inicio < cierre + timedelta(minutes=prorroga):
                self.add_error('fecha_inicio_prevista', 'El inicio debe ser posterior al cierre y la prórroga.')
        return cleaned