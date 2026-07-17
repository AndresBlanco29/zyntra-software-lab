import csv
from datetime import datetime, timedelta
from html import escape
from io import BytesIO, StringIO

from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.auditoria.decorators import admin_only_required
from config.auditoria.enrichment import normalize_changes
from config.auditoria.models import AuditLog
from config.core.datetime_formats import format_local_datetime
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
    business_only = raw_business_only in {'1', 'true', 'yes', 'on'}

    result = (data.get('result') or '').strip().lower()
    if result not in {'success', 'failed', ''}:
        result = ''

    return {
        'q': (data.get('q') or '').strip(),
        'user_id': user_id,
        'actor_role': (data.get('actor_role') or '').strip(),
        'action_category': (data.get('action_category') or '').strip(),
        'http_method': (data.get('http_method') or '').strip().upper(),
        'entity_type': (data.get('entity_type') or '').strip(),
        'module': (data.get('module') or '').strip(),
        'ip_address': (data.get('ip_address') or '').strip(),
        'result': result,
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
            | Q(entity_id__icontains=term)
            | Q(actor_username__icontains=term)
            | Q(actor_full_name__icontains=term)
            | Q(route_name__icontains=term)
            | Q(module__icontains=term)
            | Q(ip_address__icontains=term)
            | Q(browser__icontains=term)
            | Q(os_name__icontains=term)
        )
    if filters['user_id']:
        selected_user = Usuario.objects.filter(id=filters['user_id']).only('id', 'username').first()
        if selected_user:
            queryset = queryset.filter(
                Q(actor_id=selected_user.id) | Q(actor_username=selected_user.username)
            )
        else:
            queryset = queryset.filter(actor_id=filters['user_id'])
    if filters['actor_role']:
        queryset = queryset.filter(actor_role=filters['actor_role'])
    if filters['action_category']:
        queryset = queryset.filter(action_category=filters['action_category'])
    if filters['http_method']:
        queryset = queryset.filter(http_method=filters['http_method'])
    if filters['entity_type']:
        queryset = queryset.filter(entity_type__icontains=filters['entity_type'])
    if filters['module']:
        queryset = queryset.filter(module__icontains=filters['module'])
    if filters['ip_address']:
        queryset = queryset.filter(ip_address__icontains=filters['ip_address'])
    if filters['result'] == 'success':
        queryset = queryset.filter(success=True)
    elif filters['result'] == 'failed':
        queryset = queryset.filter(success=False)
    if filters.get('business_only'):
        queryset = queryset.exclude(entity_type='')
    if filters['start_dt']:
        queryset = queryset.filter(created_at__gte=filters['start_dt'])
    if filters['end_dt']:
        queryset = queryset.filter(created_at__lte=filters['end_dt'])
    return queryset


def _build_stats(queryset):
    now = timezone.now()
    last_7 = now - timedelta(days=7)
    base = queryset
    by_category = {
        row['action_category']: row['total']
        for row in base.values('action_category').annotate(total=Count('id'))
    }
    top_users = list(
        base.exclude(actor_username='')
        .values('actor_username', 'actor_full_name', 'actor_role')
        .annotate(total=Count('id'))
        .order_by('-total')[:8]
    )
    top_modifiers = list(
        base.filter(action_category=AuditLog.CATEGORY_UPDATE)
        .exclude(actor_username='')
        .values('actor_username', 'actor_full_name')
        .annotate(total=Count('id'))
        .order_by('-total')[:5]
    )
    by_module = list(
        base.exclude(module='')
        .values('module')
        .annotate(total=Count('id'))
        .order_by('-total')[:8]
    )
    by_day = list(
        base.filter(created_at__gte=last_7)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(total=Count('id'))
        .order_by('day')
    )
    # Avoid TruncHour — SQLite/test DBs often lack timezone tables.
    by_hour = []
    recent = list(
        base.filter(created_at__gte=now - timedelta(days=1))
        .values_list('created_at', flat=True)[:2000]
    )
    hour_buckets = {}
    for created_at in recent:
        local = timezone.localtime(created_at) if timezone.is_aware(created_at) else created_at
        key = local.replace(minute=0, second=0, microsecond=0)
        hour_buckets[key] = hour_buckets.get(key, 0) + 1
    by_hour = [{'hour': hour, 'total': total} for hour, total in sorted(hour_buckets.items())]
    return {
        'created_count': by_category.get(AuditLog.CATEGORY_CREATE, 0),
        'updated_count': by_category.get(AuditLog.CATEGORY_UPDATE, 0),
        'deleted_count': by_category.get(AuditLog.CATEGORY_DELETE, 0),
        'login_count': by_category.get(AuditLog.CATEGORY_LOGIN, 0),
        'failed_login_count': base.filter(action_category=AuditLog.CATEGORY_LOGIN, success=False).count(),
        'failed_count': base.filter(success=False).count(),
        'top_users': top_users,
        'top_modifiers': top_modifiers,
        'by_module': by_module,
        'by_day': by_day,
        'by_hour': by_hour,
    }


def _export_querystring(request):
    return request.GET.urlencode()


def _serialize_log(log, *, include_timeline=False):
    changes = list(log.changes or [])
    if not changes:
        changes = normalize_changes(metadata=log.metadata or {})
    payload = {
        'id': log.id,
        'when': format_local_datetime(log.created_at, seconds=True),
        'when_iso': log.created_at.isoformat(),
        'actor_display': log.actor_display,
        'actor_username': log.actor_username or '',
        'actor_full_name': log.actor_full_name or '',
        'actor_role': log.actor_role or '',
        'action_label': log.action_label,
        'action_category': log.action_category,
        'action_category_display': log.get_action_category_display(),
        'http_method': log.http_method,
        'path': log.path,
        'route_name': log.route_name,
        'module': log.module or '',
        'ip_address': log.ip_address or '',
        'user_agent': log.user_agent or '',
        'browser': log.browser or '',
        'os_name': log.os_name or '',
        'device': log.device or '',
        'location': log.location_display,
        'geo_city': log.geo_city or '',
        'geo_country': log.geo_country or '',
        'entity_type': log.entity_type or '',
        'entity_id': log.entity_id or '',
        'entity_label': log.entity_label or '',
        'status_code': log.status_code,
        'success': bool(log.success),
        'result_label': str(log.result_label),
        'duration_ms': log.duration_ms,
        'changes': changes,
        'metadata': log.metadata or {},
        'device_summary': log.device_summary,
    }
    if include_timeline and log.entity_type and log.entity_id:
        related = (
            AuditLog.objects.filter(entity_type=log.entity_type, entity_id=log.entity_id)
            .order_by('-created_at', '-id')[:25]
        )
        payload['timeline'] = [
            {
                'id': item.id,
                'when': format_local_datetime(item.created_at, seconds=True),
                'actor_display': item.actor_display,
                'action_label': item.action_label,
                'action_category': item.action_category,
                'success': item.success,
                'is_current': item.id == log.id,
            }
            for item in related
        ]
    else:
        payload['timeline'] = []
    return payload


@admin_only_required
def audit_log_list(request):
    filters = _parse_filters(request)
    queryset = AuditLog.objects.select_related('actor').all()
    queryset = _apply_filters(queryset, filters)

    paginator = Paginator(queryset, 40)
    page_obj = paginator.get_page(request.GET.get('page'))
    stats = _build_stats(queryset)

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
    modules = (
        AuditLog.objects.exclude(module='')
        .values_list('module', flat=True)
        .distinct()
        .order_by('module')[:50]
    )

    context = {
        'page_obj': page_obj,
        'filters': filters,
        'users': users,
        'entity_types': entity_types,
        'modules': modules,
        'role_choices': [(role, role.capitalize()) for role in INTERNAL_AUDIT_ROLES],
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
        'stats': stats,
        'export_querystring': _export_querystring(request),
        'logs_json': [_serialize_log(log) for log in page_obj],
    }
    return render(request, 'backoffice/audit_log_list.html', context)


@admin_only_required
def audit_log_detail_json(request, log_id):
    log = get_object_or_404(AuditLog.objects.select_related('actor'), pk=log_id)
    return JsonResponse(_serialize_log(log, include_timeline=True))


def _filtered_export_queryset(request):
    filters = _parse_filters(request)
    return _apply_filters(AuditLog.objects.select_related('actor').all(), filters)


@admin_only_required
def audit_log_export_csv(request):
    queryset = _filtered_export_queryset(request)[:5000]
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        _('When'), _('User'), _('Full name'), _('Role'), _('Action'), _('Category'),
        _('Module'), _('Method'), _('Path'), _('Entity'), _('Entity ID'), _('IP'),
        _('Browser'), _('OS'), _('Device'), _('Result'), _('Status'), _('Duration ms'), _('Changes'),
    ])
    for log in queryset:
        changes = normalize_changes(log.changes, log.metadata or {})
        change_text = ' | '.join(
            f"{c['field']}: {c['before']} → {c['after']}" for c in changes
        )
        writer.writerow([
			format_local_datetime(log.created_at, seconds=True),
            log.actor_username,
            log.actor_full_name or log.actor_display,
            log.actor_role,
            log.action_label,
            log.action_category,
            log.module,
            log.http_method,
            log.path,
            log.entity_label or log.entity_type,
            log.entity_id,
            log.ip_address or '',
            log.browser,
            log.os_name,
            log.device,
            'OK' if log.success else 'FAIL',
            log.status_code,
            log.duration_ms or '',
            change_text,
        ])
    response = HttpResponse(buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="audit-trail.csv"'
    return response


@admin_only_required
def audit_log_export_excel(request):
    queryset = _filtered_export_queryset(request)[:3000]
    parts = [
        '<html><head><meta charset="utf-8"></head><body>',
        f'<h1>{escape(str(_("Audit trail")))}</h1>',
        f'<p>{escape(format_local_datetime(timezone.now()))}</p>',
        '<table border="1" cellspacing="0" cellpadding="4">',
        '<tr>'
        f'<th>{escape(str(_("When")))}</th>'
        f'<th>{escape(str(_("User")))}</th>'
        f'<th>{escape(str(_("Action")))}</th>'
        f'<th>{escape(str(_("Module")))}</th>'
        f'<th>{escape(str(_("Entity")))}</th>'
        f'<th>{escape(str(_("IP")))}</th>'
        f'<th>{escape(str(_("Result")))}</th>'
        f'<th>{escape(str(_("Changes")))}</th>'
        '</tr>',
    ]
    for log in queryset:
        changes = normalize_changes(log.changes, log.metadata or {})
        change_text = ' | '.join(f"{c['field']}: {c['before']} → {c['after']}" for c in changes)
        parts.append(
            '<tr>'
            f'<td>{escape(format_local_datetime(log.created_at))}</td>'
            f'<td>{escape(str(log.actor_display))}</td>'
            f'<td>{escape(log.action_label)}</td>'
            f'<td>{escape(log.module)}</td>'
            f'<td>{escape(log.entity_label or log.entity_type or "")}</td>'
            f'<td>{escape(str(log.ip_address or ""))}</td>'
            f'<td>{"OK" if log.success else "FAIL"}</td>'
            f'<td>{escape(change_text)}</td>'
            '</tr>'
        )
    parts.append('</table></body></html>')
    response = HttpResponse(''.join(parts), content_type='application/vnd.ms-excel; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="audit-trail.xls"'
    return response


@admin_only_required
def audit_log_export_pdf(request):
    queryset = list(_filtered_export_queryset(request)[:400])
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), rightMargin=24, leftMargin=24, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    title = ParagraphStyle('AuditTitle', parent=styles['Heading1'], textColor=colors.HexColor('#0f172a'), fontSize=16)
    story = [
        Paragraph(str(_('Audit trail')), title),
        Paragraph(f"{_('Generated')}: {format_local_datetime(timezone.now())}", styles['Normal']),
        Spacer(1, 0.15 * inch),
    ]
    rows = [[_('When'), _('User'), _('Action'), _('Module'), _('Result'), _('IP')]]
    for log in queryset:
        rows.append([
			format_local_datetime(log.created_at, seconds=True),
            str(log.actor_display)[:28],
            str(log.action_label)[:42],
            str(log.module or '-')[:18],
            'OK' if log.success else 'FAIL',
            str(log.ip_address or '-'),
        ])
    table = Table(rows, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f172a')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
    ]))
    story.append(table)
    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="audit-trail.pdf"'
    return response
