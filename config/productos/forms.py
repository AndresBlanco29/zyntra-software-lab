from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from config.clientes.models import TipoCliente

from .models import Presentacion, Producto, Promocion, PromocionEscala


def _make_aware_if_naive(value):
    if value and timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


class PromocionForm(forms.ModelForm):
    """
    Promotion header form.

    ``producto`` / ``presentacion`` are posted as plain ids picked through the
    AJAX search widget (see buscar_productos_promocion / producto_presentaciones_promocion),
    never rendered as a preloaded <select> - that is what keeps this page fast
    with thousands of products.
    """

    producto = forms.ModelChoiceField(
        queryset=Producto.objects.all(),
        widget=forms.HiddenInput(),
        error_messages={
            'required': _('Search and select a product.'),
            'invalid_choice': _('Select a valid product.'),
        },
    )
    presentacion = forms.ModelChoiceField(
        queryset=Presentacion.objects.all(),
        required=False,
        widget=forms.HiddenInput(),
    )
    tipos_cliente = forms.ModelMultipleChoiceField(
        queryset=TipoCliente.objects.filter(activo=True),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        label=_('Customer types'),
        help_text=_('Leave empty to apply to every customer type.'),
    )
    activa = forms.BooleanField(
        required=False,
        initial=True,
        label=_('Active'),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    class Meta:
        model = Promocion
        fields = ['nombre', 'descripcion', 'producto', 'presentacion', 'tipos_cliente', 'fecha_inicio', 'fecha_fin', 'activa']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 150}),
            'descripcion': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 255}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('fecha_inicio', 'fecha_fin'):
            self.fields[name].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d']
            self.fields[name].widget = forms.DateTimeInput(
                attrs={'type': 'datetime-local', 'class': 'form-control'},
                format='%Y-%m-%dT%H:%M',
            )

    def clean_fecha_inicio(self):
        return _make_aware_if_naive(self.cleaned_data.get('fecha_inicio'))

    def clean_fecha_fin(self):
        return _make_aware_if_naive(self.cleaned_data.get('fecha_fin'))

    def clean(self):
        cleaned = super().clean()
        producto = cleaned.get('producto')
        presentacion = cleaned.get('presentacion')
        if producto and presentacion and presentacion.producto_id != producto.id:
            self.add_error('presentacion', _('The presentation must belong to the selected product.'))
        fecha_inicio = cleaned.get('fecha_inicio')
        fecha_fin = cleaned.get('fecha_fin')
        if fecha_inicio and fecha_fin and fecha_fin < fecha_inicio:
            self.add_error('fecha_fin', _('End date cannot be earlier than start date.'))
        return cleaned


class PromocionEscalaForm(forms.ModelForm):
    class Meta:
        model = PromocionEscala
        fields = ['cantidad_minima', 'tipo_beneficio', 'valor_beneficio', 'unidades_gratis', 'orden']
        widgets = {
            'cantidad_minima': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'step': 1}),
            'tipo_beneficio': forms.Select(attrs={'class': 'form-select'}),
            'valor_beneficio': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'step': '0.01'}),
            'unidades_gratis': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'step': 1}),
            'orden': forms.HiddenInput(),
        }

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get('tipo_beneficio')
        if self.cleaned_data.get('DELETE'):
            return cleaned
        if tipo == PromocionEscala.TIPO_FREE_UNITS:
            if not cleaned.get('unidades_gratis'):
                self.add_error('unidades_gratis', _('Enter how many free units are granted.'))
            cleaned['valor_beneficio'] = None
        else:
            valor = cleaned.get('valor_beneficio')
            if not valor or valor <= 0:
                self.add_error('valor_beneficio', _('Benefit value must be greater than zero.'))
            elif tipo == PromocionEscala.TIPO_PERCENT and valor > 100:
                self.add_error('valor_beneficio', _('Percentage benefit cannot exceed 100.'))
            cleaned['unidades_gratis'] = None
        return cleaned


PromocionEscalaFormSet = forms.inlineformset_factory(
    Promocion,
    PromocionEscala,
    form=PromocionEscalaForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
