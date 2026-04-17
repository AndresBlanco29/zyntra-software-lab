from django import forms
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.utils.translation import gettext_lazy as _


class CustomerPasswordResetForm(PasswordResetForm):
	email = forms.EmailField(
		label=_('Email address'),
		max_length=254,
		widget=forms.EmailInput(attrs={
			'class': 'form-control form-control-lg',
			'placeholder': _('Enter your registered email'),
			'autocomplete': 'email',
		}),
	)


class CustomerSetPasswordForm(SetPasswordForm):
	new_password1 = forms.CharField(
		label=_('New password'),
		strip=False,
		widget=forms.PasswordInput(attrs={
			'class': 'form-control form-control-lg',
			'placeholder': _('Enter your new password'),
			'autocomplete': 'new-password',
		}),
	)
	new_password2 = forms.CharField(
		label=_('Confirm new password'),
		strip=False,
		widget=forms.PasswordInput(attrs={
			'class': 'form-control form-control-lg',
			'placeholder': _('Repeat your new password'),
			'autocomplete': 'new-password',
		}),
	)