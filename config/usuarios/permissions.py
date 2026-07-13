from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


PERMISSION_SECTIONS = (
    {
        'key': 'admin_access',
        'title': _('Administration'),
        'description': _('Access to administrative areas and supervisory workflows.'),
        'permissions': (
            {
                'code': 'admin.dashboard.view',
                'label': _('Admin dashboard'),
                'description': _('View the main administrative dashboard.'),
            },
            {
                'code': 'admin.users.view',
                'label': _('Internal users'),
                'description': _('View internal users and their assigned permissions.'),
            },
            {
                'code': 'admin.users.manage',
                'label': _('Manage internal users'),
                'description': _('Create, edit, activate or deactivate internal users and their permissions.'),
            },
            {
                'code': 'admin.customer_requests.view',
                'label': _('Customer requests'),
                'description': _('View customer approvals and application details.'),
            },
            {
                'code': 'admin.customer_requests.manage',
                'label': _('Manage customer requests'),
                'description': _('Approve or reject customer applications.'),
            },
            {
                'code': 'admin.customers.assign',
                'label': _('Assign customers to vendors'),
                'description': _('Assign one, several or all customers to specific vendors.'),
            },
            {
                'code': 'admin.products.view',
                'label': _('Products and brands'),
                'description': _('View products, categories and brands.'),
            },
            {
                'code': 'admin.products.manage',
                'label': _('Manage products and brands'),
                'description': _('Create, edit, activate or deactivate products, categories and brands.'),
            },
            {
                'code': 'admin.content.view',
                'label': _('Home content'),
                'description': _('View home content and testimonials.'),
            },
            {
                'code': 'admin.content.manage',
                'label': _('Manage home content'),
                'description': _('Edit home content and testimonials.'),
            },
        ),
    },
    {
        'key': 'backoffice_access',
        'title': _('BackOffice'),
        'description': _('Quotes and sales order processing for internal operations.'),
        'permissions': (
            {
                'code': 'backoffice.dashboard.view',
                'label': _('BackOffice dashboard'),
                'description': _('View the operational dashboard.'),
            },
            {
                'code': 'backoffice.quotes.view',
                'label': _('Quotes'),
                'description': _('View quotes and quote details.'),
            },
            {
                'code': 'backoffice.quotes.manage',
                'label': _('Manage quotes'),
                'description': _('Edit quotes, send them to customers and update follow-up actions.'),
            },
            {
                'code': 'backoffice.orders.view',
                'label': _('Sales orders'),
                'description': _('View sales orders, order details and picking tickets.'),
            },
            {
                'code': 'backoffice.orders.manage',
                'label': _('Manage sales orders'),
                'description': _('Update order status, edit line items and prepare picking workflows.'),
            },
            {
                'code': 'backoffice.reports.view',
                'label': _('Reports dashboard'),
                'description': _('View daily closing, revenue trends, driver reconciliation and commercial performance metrics.'),
            },
            {
                'code': 'backoffice.customers.assign',
                'label': _('Assign customers to vendors'),
                'description': _('Assign one, several or all customers to specific vendors.'),
            },
        ),
    },
    {
        'key': 'sales_access',
        'title': _('Sales'),
        'description': _('Customer management and vendor order-taking tools.'),
        'permissions': (
            {
                'code': 'vendor.customers.view',
                'label': _('Customers'),
                'description': _('View the assigned customer list and customer records.'),
            },
            {
                'code': 'vendor.customers.manage',
                'label': _('Manage customers'),
                'description': _('Create customers and update customer information.'),
            },
            {
                'code': 'vendor.orders.view',
                'label': _('Order taking'),
                'description': _('View the vendor order-taking flow and order summary.'),
            },
            {
                'code': 'vendor.orders.manage',
                'label': _('Manage order taking'),
                'description': _('Build and submit orders on behalf of customers.'),
            },
        ),
    },
    {
        'key': 'selector_access',
        'title': _('Picking verification'),
        'description': _('Assigned picking tickets verification and quantity adjustment workflow.'),
        'permissions': (
            {
                'code': 'selector.picking.view',
                'label': _('Assigned picking tickets'),
                'description': _('View only the picking tickets assigned to the selector.'),
            },
            {
                'code': 'selector.picking.manage',
                'label': _('Verify and adjust picking tickets'),
                'description': _('Update real picked quantities, register notes and finalize verification.'),
            },
        ),
    },
    {
        'key': 'driver_access',
        'title': _('Delivery'),
        'description': _('Assigned deliveries, route guidance, customer signature and payment capture workflow.'),
        'permissions': (
            {
                'code': 'driver.delivery.view',
                'label': _('Assigned deliveries'),
                'description': _('View the invoices and delivery assignments assigned to the driver.'),
            },
            {
                'code': 'driver.delivery.manage',
                'label': _('Manage deliveries'),
                'description': _('Start routes, register signatures, capture payment and complete deliveries.'),
            },
        ),
    },
)


PERMISSION_DEFINITIONS = {
    permission['code']: permission
    for section in PERMISSION_SECTIONS
    for permission in section['permissions']
}

VIEW_PERMISSION_CODES = tuple(
    code for code in PERMISSION_DEFINITIONS if code.endswith('.view')
)

MANAGE_TO_VIEW = {
    code: f"{code[:-7]}.view"
    for code in PERMISSION_DEFINITIONS
    if code.endswith('.manage')
}

DEFAULT_ROLE_PERMISSIONS = {
    'vendedor': {
        'vendor.customers.view',
        'vendor.customers.manage',
        'vendor.orders.view',
        'vendor.orders.manage',
    },
    'backoffice': {
        'backoffice.dashboard.view',
        'backoffice.quotes.view',
        'backoffice.quotes.manage',
        'backoffice.orders.view',
        'backoffice.orders.manage',
        'backoffice.reports.view',
        'backoffice.customers.assign',
        'vendor.customers.view',
        'vendor.customers.manage',
    },
    'seleccionador': {
        'selector.picking.view',
        'selector.picking.manage',
    },
    'driver': {
        'driver.delivery.view',
        'driver.delivery.manage',
    },
}


def _expand_manage_permissions(permission_codes):
    expanded = set(permission_codes)
    for manage_code, view_code in MANAGE_TO_VIEW.items():
        if manage_code in expanded:
            expanded.add(view_code)
    return expanded


def all_permission_codes():
    return tuple(PERMISSION_DEFINITIONS.keys())


def get_default_permissions_for_role(role):
    return _expand_manage_permissions(DEFAULT_ROLE_PERMISSIONS.get((role or '').strip().lower(), set()))


def normalize_permission_overrides(raw_overrides):
    overrides = {}
    if not isinstance(raw_overrides, dict):
        return overrides

    for code, value in raw_overrides.items():
        if code not in PERMISSION_DEFINITIONS:
            continue
        if isinstance(value, bool):
            overrides[code] = value

    for manage_code, view_code in MANAGE_TO_VIEW.items():
        if overrides.get(manage_code) is True:
            overrides[view_code] = True

    return overrides


def build_permission_overrides_for_role(role, selected_codes):
    selected = _expand_manage_permissions({code for code in selected_codes if code in PERMISSION_DEFINITIONS})
    defaults = get_default_permissions_for_role(role)
    overrides = {}
    for code in PERMISSION_DEFINITIONS:
        desired = code in selected
        default = code in defaults
        if desired != default:
            overrides[code] = desired
    return normalize_permission_overrides(overrides)


def get_effective_permissions(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return set()
    if getattr(user, 'is_superuser', False) or getattr(user, 'role', '') == 'admin':
        return set(all_permission_codes())

    effective = get_default_permissions_for_role(getattr(user, 'role', ''))
    overrides = normalize_permission_overrides(getattr(user, 'permission_overrides', None))
    for code, value in overrides.items():
        if value:
            effective.add(code)
        else:
            effective.discard(code)
    return _expand_manage_permissions(effective)


def user_has_permission(user, permission_code):
    if permission_code not in PERMISSION_DEFINITIONS:
        return False
    return permission_code in get_effective_permissions(user)


def build_permission_sections(role=None, overrides=None):
    mock_user = type('PermissionUser', (), {
        'is_authenticated': True,
        'is_superuser': False,
        'role': role or '',
        'permission_overrides': overrides or {},
    })()
    effective = get_effective_permissions(mock_user)

    sections = []
    for section in PERMISSION_SECTIONS:
        section_permissions = []
        for permission in section['permissions']:
            section_permissions.append({
                **permission,
                'checked': permission['code'] in effective,
            })
        sections.append({
            'key': section['key'],
            'title': section['title'],
            'description': section['description'],
            'permissions': section_permissions,
        })
    return sections


def get_permission_summary_labels(user):
    labels = []
    effective = get_effective_permissions(user)
    for section in PERMISSION_SECTIONS:
        if any(permission['code'] in effective for permission in section['permissions']):
            labels.append(section['title'])
    return labels


def get_redirect_url_for_user(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return reverse('login')

    candidates = (
        ('admin.dashboard.view', 'panel_admin'),
        ('admin.users.view', 'lista_usuarios_internos'),
        ('backoffice.dashboard.view', 'backoffice_dashboard'),
        ('backoffice.reports.view', 'reportes_dashboard'),
        ('driver.delivery.view', 'driver_delivery_list'),
        ('selector.picking.view', 'selector_picking_list'),
        ('admin.products.view', 'lista_productos'),
        ('admin.customer_requests.view', 'clientes_pendientes'),
        ('admin.content.view', 'contenido_home'),
        ('vendor.customers.view', 'vendedores_clientes'),
        ('vendor.orders.view', 'tomar_pedido'),
    )
    for permission_code, route_name in candidates:
        if user_has_permission(user, permission_code):
            return reverse(route_name)

    if getattr(user, 'role', '') == 'cliente':
        return reverse('catalogo')
    return reverse('login')


def _forbidden_response(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': False, 'message': str(_('You do not have permission to perform this action.'))}, status=403)
    messages.error(request, _('You do not have permission to access this section.'))
    return redirect(get_redirect_url_for_user(request.user))


def internal_permission_required(*permission_codes, require_all=False):
    valid_codes = tuple(code for code in permission_codes if code in PERMISSION_DEFINITIONS)

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            user = getattr(request, 'user', None)
            if not user or not user.is_authenticated:
                return redirect('login')

            results = [user_has_permission(user, code) for code in valid_codes]
            has_access = all(results) if require_all else any(results)
            if has_access:
                return view_func(request, *args, **kwargs)
            return _forbidden_response(request)

        return wrapped

    return decorator