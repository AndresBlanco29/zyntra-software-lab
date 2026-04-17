from django import forms
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm


class CustomerPasswordResetForm(PasswordResetForm):
	email = forms.EmailField(
		label='Correo electronico',
		max_length=254,
		widget=forms.EmailInput(attrs={
			'class': 'form-control form-control-lg',
			'placeholder': 'Ingresa tu correo registrado',
			'autocomplete': 'email',
		}),
	)


class CustomerSetPasswordForm(SetPasswordForm):
	new_password1 = forms.CharField(
		label='Nueva contrasena',
		strip=False,
		widget=forms.PasswordInput(attrs={
			'class': 'form-control form-control-lg',
			'placeholder': 'Ingresa tu nueva contrasena',
			'autocomplete': 'new-password',
		}),
	)
	new_password2 = forms.CharField(
		label='Confirma tu nueva contrasena',
		strip=False,
		widget=forms.PasswordInput(attrs={
			'class': 'form-control form-control-lg',
			'placeholder': 'Repite tu nueva contrasena',
			'autocomplete': 'new-password',
		}),
	)