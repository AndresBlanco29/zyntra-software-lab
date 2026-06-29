from datetime import datetime

from django.core.paginator import Paginator
from django.db.models import Max, Min, Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _

from config.auditoria.decorators import admin_only_required
from config.auditoria.models import AuditLog
from config.usuarios.models import Usuario

INTERNAL_AUDIT_ROLES = ('admin', 'vendedor', 'backoffice', 'seleccionador', 'driver')


def _parse_filters(request):
    data = request.GET
    start_date = parse_date(data.get('start_date') or '')
    end_date = parse_date(data.get('end_date') or '')
    start_dt = None
    end_dt = None
    if start_date:
        start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    if end_date:
        end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

    user_id = data.get('user_id') or ''
    try:
        user_id = int(user_id) if user_id else None
    except (TypeError, ValueError):
        user_id = None

    raw_business_only = (data.get('business_only') or '0').strip().lower()
    if raw_business_only in {'1', 'true', 'yes', 'on'}:
        business_only = True
    else:
        business_only = False

    return {
        'q': (data.get('q') or '').strip(),
        'user_id': user_id,
        'action_category': (data.get('action_category') or '').strip(),
        'http_method': (data.get('http_method') or '').strip().upper(),
        'entity_type': (data.get('entity_type') or '').strip(),
        'business_only': business_only,
        'start_dt': start_dt,
        'end_dt': end_dt,
        'start_date': start_date,
        'end_date': end_date,
    }


def _apply_filters(queryset, filters):
    if filters['q']:
        term = filters['q']
        queryset = queryset.filter(
            Q(action_label__icontains=term)
            | Q(path__icontains=term)
            | Q(entity_label__icontains=term)
            | Q(actor_username__icontains=term)
            | Q(route_name__icontains=term)
            | Q(ip_address__icontains=term)
        )
    if filters['user_id']:
        selected_user = Usuario.objects.filter(id=filters['user_id']).only('id', 'username').first()
        if selected_user:
            queryset = queryset.filter(
                Q(actor_id=selected_user.id) | Q(actor_username=selected_user.username)
            )
        else:
            queryset = queryset.filter(actor_id=filters['user_id'])
    if filters['action_category']:
        queryset = queryset.filter(action_category=filters['action_category'])
    if filters['http_method']:
        queryset = queryset.filter(http_method=filters['http_method'])
    if filters['entity_type']:
        queryset = queryset.filter(entity_type__icontains=filters['entity_type'])
    if filters.get('business_only'):
        queryset = queryset.exclude(entity_type='')
    if filters['start_dt']:
        queryset = queryset.filter(created_at__gte=filters['start_dt'])
    if filters['end_dt']:
        queryset = queryset.filter(created_at__lte=filters['end_dt'])
    return queryset


@admin_only_required
def audit_log_list(request):
    filters = _parse_filters(request)
    queryset = AuditLog.objects.select_related('actor').all()
    queryset = _apply_filters(queryset, filters)

    paginator = Paginator(queryset, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    users = (
        Usuario.objects.filter(role__in=INTERNAL_AUDIT_ROLES)
        .order_by('role', 'first_name', 'last_name', 'username')
    )

    archive_bounds = AuditLog.objects.aggregate(
        earliest=Min('created_at'),
        latest=Max('created_at'),
    )

    entity_types = (
        AuditLog.objects.exclude(entity_type='')
        .values_list('entity_type', flat=True)
        .distinct()
        .order_by('entity_type')[:50]
    )

    context = {
        'page_obj': page_obj,
        'filters': filters,
        'users': users,
        'entity_types': entity_types,
        'category_choices': AuditLog.CATEGORY_CHOICES,
        'method_choices': (
            ('GET', 'GET'),
            ('POST', 'POST'),
            ('PUT', 'PUT'),
            ('PATCH', 'PATCH'),
            ('DELETE', 'DELETE'),
        ),
        'total_count': paginator.count,
        'archive_earliest': archive_bounds['earliest'],
        'archive_latest': archive_bounds['latest'],
    }
    return render(request, 'backoffice/audit_log_list.html', context)
