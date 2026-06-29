from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

from config.auditoria.services import is_admin_user


def admin_only_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not is_admin_user(request.user):
            messages.error(request, _('You do not have permission to access this area.'))
            return redirect('home')
        return view_func(request, *args, **kwargs)

    return wrapped
