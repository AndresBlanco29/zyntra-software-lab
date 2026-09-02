from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from config.core.demo_lab import (
    RESET_CONFIRMATION_PHRASE,
    demo_lab_enabled,
    mark_reset_rate_limited,
    reset_is_rate_limited,
    user_can_reset_demo,
)
from config.core.demo_showcase import DEMO_PASSWORD, seed_demo_showcase
from config.usuarios.permissions import get_redirect_url_for_user


@login_required
@require_http_methods(['GET', 'POST'])
def reset_demo(request):
    if not demo_lab_enabled():
        messages.error(request, _('Reset Demo is only available in the Software Lab environment.'))
        return redirect('home')
    if not user_can_reset_demo(request.user):
        messages.error(request, _('You do not have permission to reset this demo.'))
        return redirect(get_redirect_url_for_user(request.user))

    if request.method == 'GET':
        return render(
            request,
            'demo/reset_demo.html',
            {
                'confirmation_phrase': RESET_CONFIRMATION_PHRASE,
                'demo_password': DEMO_PASSWORD,
            },
        )

    confirmation = str(request.POST.get('confirmation') or '').strip().upper()
    understand = request.POST.get('understand') == 'yes'
    if not understand or confirmation != RESET_CONFIRMATION_PHRASE:
        messages.error(
            request,
            _('Type %(phrase)s and confirm that visitor data will be restored.')
            % {'phrase': RESET_CONFIRMATION_PHRASE},
        )
        return redirect('demo_reset')

    if reset_is_rate_limited(request.user):
        messages.error(request, _('Please wait a minute before resetting the demo again.'))
        return redirect('demo_reset')

    summary = seed_demo_showcase(reset=True)
    mark_reset_rate_limited(request.user)
    messages.success(
        request,
        _(
            'Demo restored. Login %(login)s · %(customers)s customers · %(orders)s orders · '
            '%(invoices)s invoices.'
        )
        % {
            'login': summary['demo_login'],
            'customers': summary['customers'],
            'orders': summary['orders'],
            'invoices': summary['invoices'],
        },
    )
    return redirect(get_redirect_url_for_user(request.user) or reverse('panel_admin'))
