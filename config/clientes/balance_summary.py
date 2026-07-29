from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, IntegerField, When
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from config.facturacion.services import (
    resolve_invoice_payment_base_date,
    resolve_invoice_payment_due_date,
)


PAID_QB_PAYMENT_STATUSES = ('PAID', 'DEPOSITED')

AR_STATUS_ALL = 'all'
AR_STATUS_OUTSTANDING = 'outstanding'
AR_STATUS_OVERDUE = 'overdue'
AR_STATUS_DUE_SOON = 'due_soon'
AR_STATUS_NO_DEBT = 'no_debt'
AR_STATUS_CHOICES = (
    AR_STATUS_ALL,
    AR_STATUS_OUTSTANDING,
    AR_STATUS_OVERDUE,
    AR_STATUS_DUE_SOON,
    AR_STATUS_NO_DEBT,
)

DUE_SOON_WINDOWS = (0, 3, 7, 15, 30)
OVERDUE_BUCKETS = {
    '1_7': (1, 7),
    '8_15': (8, 15),
    '16_30': (16, 30),
    '31_60': (31, 60),
    '60_plus': (61, None),
}

SEVERITY_NONE = 'none'
SEVERITY_CURRENT = 'current'
SEVERITY_DUE_SOON = 'due_soon'
SEVERITY_OVERDUE_SOFT = 'overdue_soft'
SEVERITY_OVERDUE_HARD = 'overdue_hard'


def _quantize_money(value):
    amount = Decimal(str(value or '0.00'))
    return amount.quantize(Decimal('0.01'))


@dataclass(frozen=True)
class CustomerBalanceLine:
    amount: Decimal
    is_overdue: bool
    aging_days: int
    due_date: object = None
    invoice_date: object = None
    invoice_id: int | None = None
    invoice_number: str = ''
    source: str = 'invoice'
    terms_label: str = ''

    @property
    def aging_display(self):
        if not self.is_overdue:
            return '0'
        if self.aging_days <= 0:
            return str(_('Due today'))
        if self.aging_days == 1:
            return str(_('1 day past due'))
        return str(_('%(days)s days past due') % {'days': self.aging_days})

    @property
    def due_in_display(self):
        """Human readable countdown for invoices that are not yet due."""
        if self.is_overdue or self.due_date is None:
            return ''
        today = timezone.localdate()
        days = (self.due_date - today).days
        if days <= 0:
            return str(_('Due today'))
        if days == 1:
            return str(_('Due in 1 day'))
        return str(_('Due in %(days)s days') % {'days': days})

    @property
    def invoice_date_display(self):
        return self._format_date(self.invoice_date)

    @property
    def due_date_display(self):
        return self._format_date(self.due_date)

    @staticmethod
    def _format_date(value):
        if not value:
            return ''
        try:
            return date_format(value, format='SHORT_DATE', use_l10n=True)
        except (TypeError, ValueError):
            return str(value)

    @property
    def invoice_label(self):
        if self.invoice_number:
            return str(_('Invoice %(number)s') % {'number': self.invoice_number})
        return str(_('QuickBooks balance'))

    @property
    def balance_label(self):
        if self.source != 'invoice':
            return str(_('QuickBooks balance'))
        if self.is_overdue:
            return str(_('Due balance'))
        return str(_('Open'))

    @property
    def days_until_due(self):
        if self.is_overdue or self.due_date is None:
            return None
        return max((self.due_date - timezone.localdate()).days, 0)

    @property
    def status_label(self):
        if self.is_overdue:
            if self.aging_days <= 0:
                return str(_('Due today'))
            return str(_('Overdue'))
        if self.due_date is None:
            return str(_('Open'))
        days = self.days_until_due
        if days is not None and days <= 7:
            return str(_('Due soon'))
        return str(_('Not yet due'))

    @property
    def severity(self):
        if self.is_overdue:
            if self.aging_days > 30:
                return SEVERITY_OVERDUE_HARD
            return SEVERITY_OVERDUE_SOFT
        if self.due_date is None:
            return SEVERITY_CURRENT
        days = self.days_until_due
        if days is not None and days <= 7:
            return SEVERITY_DUE_SOON
        return SEVERITY_CURRENT


@dataclass(frozen=True)
class CustomerBalanceSummary:
    lines: tuple[CustomerBalanceLine, ...]
    overdue_balance: Decimal
    current_balance: Decimal
    total_open_balance: Decimal
    customer_credit: Decimal
    exceeds_credit_limit: bool
    credit_limit_excess: Decimal

    @property
    def has_balance(self):
        return self.total_open_balance > 0

    @property
    def has_credit(self):
        return self.customer_credit > 0

    @property
    def overdue_lines(self):
        return tuple(line for line in self.lines if line.is_overdue)

    @property
    def current_lines(self):
        return tuple(line for line in self.lines if not line.is_overdue)

    @property
    def overdue_count(self):
        return len(self.overdue_lines)

    @property
    def current_count(self):
        return len(self.current_lines)

    @property
    def max_aging_days(self):
        overdue = self.overdue_lines
        if not overdue:
            return 0
        return max(line.aging_days for line in overdue)

    @property
    def nearest_due_date(self):
        current = [line.due_date for line in self.current_lines if line.due_date]
        if not current:
            overdue = [line.due_date for line in self.overdue_lines if line.due_date]
            return min(overdue) if overdue else None
        return min(current)

    @property
    def severity(self):
        if not self.has_balance:
            return SEVERITY_NONE
        if self.max_aging_days > 30:
            return SEVERITY_OVERDUE_HARD
        if self.max_aging_days > 0:
            return SEVERITY_OVERDUE_SOFT
        for line in self.current_lines:
            if line.severity == SEVERITY_DUE_SOON:
                return SEVERITY_DUE_SOON
        return SEVERITY_CURRENT


@dataclass(frozen=True)
class CustomersReceivablesSummary:
    customers_with_balance: int
    total_outstanding: Decimal
    invoices_overdue: int
    invoices_due_this_week: int


@dataclass(frozen=True)
class ClienteListDisplayRow:
    cliente: object
    line: CustomerBalanceLine | None = None
    is_primary: bool = True
    hidden_line_count: int = 0


def _invoice_due_date(invoice):
    if invoice.qb_due_date:
        return invoice.qb_due_date
    due_date = resolve_invoice_payment_due_date(invoice)
    if due_date is not None:
        return due_date
    return resolve_invoice_payment_base_date(invoice)


def _invoice_document_date(invoice):
    if invoice.fecha_documento:
        return invoice.fecha_documento
    return timezone.localtime(invoice.creada_en).date()


def _build_line_from_invoice(invoice, *, today, terms_label=''):
    amount = _quantize_money(invoice.saldo_cliente)
    if amount <= 0:
        return None

    due_date = _invoice_due_date(invoice)
    invoice_date = _invoice_document_date(invoice)
    is_overdue = due_date < today
    aging_days = (today - due_date).days if is_overdue else 0

    return CustomerBalanceLine(
        amount=amount,
        is_overdue=is_overdue,
        aging_days=aging_days,
        due_date=due_date,
        invoice_date=invoice_date,
        invoice_id=invoice.pk,
        invoice_number=str(invoice.numero or f'#{invoice.pk}'),
        source='invoice',
        terms_label=terms_label,
    )


def _sort_balance_lines(lines):
    def sort_key(line):
        if line.is_overdue:
            return (0, -line.aging_days, line.due_date or timezone.localdate(), line.invoice_date or timezone.localdate())
        return (1, line.due_date or timezone.localdate(), line.invoice_date or timezone.localdate(), -(line.invoice_id or 0))

    return tuple(sorted(lines, key=sort_key))


def _qb_balance_remainder(*, cliente, invoice_total):
    stored_due = _quantize_money(cliente.due_balance)
    if stored_due <= 0:
        return Decimal('0.00')
    remainder = _quantize_money(stored_due - invoice_total)
    if remainder <= 0:
        return Decimal('0.00')
    qb_id = str(getattr(cliente, 'quickbooks_id', None) or '').strip()
    if not qb_id:
        return Decimal('0.00')
    if str(getattr(cliente, 'sync_status', '') or '').upper() != 'SYNCED':
        return Decimal('0.00')
    return remainder


def build_customer_balance_summary(cliente, *, invoices=None, today=None):
    today = today or timezone.localdate()
    customer_credit = _quantize_money(cliente.customer_credit_balance)

    if invoices is None:
        from config.facturacion.models import Invoice

        invoices = list(
            Invoice.objects.filter(
                cliente=cliente,
                estado='GENERADA',
                saldo_cliente__gt=0,
            )
            .exclude(qb_payment_status__in=PAID_QB_PAYMENT_STATUSES)
            .select_related('delivery')
            .order_by('creada_en')
        )

    terms_label = cliente.get_terminos_pago_label() if hasattr(cliente, 'get_terminos_pago_label') else ''

    raw_lines = []
    for invoice in invoices:
        line = _build_line_from_invoice(invoice, today=today, terms_label=terms_label)
        if line is not None:
            raw_lines.append(line)

    invoice_total = _quantize_money(sum((line.amount for line in raw_lines), Decimal('0.00')))
    qb_remainder = _qb_balance_remainder(cliente=cliente, invoice_total=invoice_total)

    if qb_remainder > 0:
        raw_lines.append(
            CustomerBalanceLine(
                amount=qb_remainder,
                is_overdue=True,
                aging_days=0,
                due_date=None,
                invoice_date=None,
                source='quickbooks',
            )
        )

    if not raw_lines and cliente.due_balance > 0:
        raw_lines.append(
            CustomerBalanceLine(
                amount=_quantize_money(cliente.due_balance),
                is_overdue=True,
                aging_days=0,
                due_date=None,
                invoice_date=None,
                source='quickbooks',
            )
        )

    lines = _sort_balance_lines(raw_lines)
    overdue_balance = _quantize_money(sum((line.amount for line in lines if line.is_overdue), Decimal('0.00')))
    current_balance = _quantize_money(sum((line.amount for line in lines if not line.is_overdue), Decimal('0.00')))
    total_open_balance = _quantize_money(overdue_balance + current_balance)

    credit_limit = cliente.credit_limit
    exceeds_credit_limit = False
    credit_limit_excess = Decimal('0.00')
    if credit_limit is not None and total_open_balance > _quantize_money(credit_limit):
        exceeds_credit_limit = True
        credit_limit_excess = _quantize_money(total_open_balance - credit_limit)

    return CustomerBalanceSummary(
        lines=lines,
        overdue_balance=overdue_balance,
        current_balance=current_balance,
        total_open_balance=total_open_balance,
        customer_credit=customer_credit,
        exceeds_credit_limit=exceeds_credit_limit,
        credit_limit_excess=credit_limit_excess,
    )


def attach_customer_balance_summaries(clientes):
    clientes = list(clientes)
    if not clientes:
        return clientes

    from config.facturacion.models import Invoice

    cliente_ids = [cliente.id for cliente in clientes]
    invoices_by_cliente = defaultdict(list)
    invoices = (
        Invoice.objects.filter(
            cliente_id__in=cliente_ids,
            estado='GENERADA',
            saldo_cliente__gt=0,
        )
        .exclude(qb_payment_status__in=PAID_QB_PAYMENT_STATUSES)
        .select_related('delivery')
        .order_by('creada_en')
    )
    for invoice in invoices:
        invoices_by_cliente[invoice.cliente_id].append(invoice)

    today = timezone.localdate()
    for cliente in clientes:
        cliente.balance_summary = build_customer_balance_summary(
            cliente,
            invoices=invoices_by_cliente.get(cliente.id, []),
            today=today,
        )
    return clientes


def expand_clientes_for_list_display(clientes, *, max_lines_per_customer=None):
    rows = []
    for cliente in clientes:
        summary = getattr(cliente, 'balance_summary', None)
        if summary is None:
            summary = build_customer_balance_summary(cliente)
            cliente.balance_summary = summary
        if summary.has_credit:
            rows.append(ClienteListDisplayRow(cliente=cliente, line=None, is_primary=True))
            continue
        if summary.lines:
            visible_lines = summary.lines
            hidden_line_count = 0
            if max_lines_per_customer is not None and len(visible_lines) > max_lines_per_customer:
                hidden_line_count = len(visible_lines) - max_lines_per_customer
                visible_lines = visible_lines[:max_lines_per_customer]
            for index, line in enumerate(visible_lines):
                rows.append(
                    ClienteListDisplayRow(
                        cliente=cliente,
                        line=line,
                        is_primary=index == 0,
                        hidden_line_count=hidden_line_count if index == 0 else 0,
                    )
                )
            continue
        rows.append(ClienteListDisplayRow(cliente=cliente, line=None, is_primary=True))
    return rows


def open_receivable_invoices_queryset(*, cliente_ids=None):
    from config.facturacion.models import Invoice

    qs = (
        Invoice.objects.filter(
            estado='GENERADA',
            saldo_cliente__gt=0,
        )
        .exclude(qb_payment_status__in=PAID_QB_PAYMENT_STATUSES)
        .select_related('cliente', 'delivery')
    )
    if cliente_ids is not None:
        qs = qs.filter(cliente_id__in=cliente_ids)
    return qs


def normalize_ar_filter_params(*, ar_status='', due_soon_window='', overdue_bucket=''):
    status = str(ar_status or AR_STATUS_ALL).strip().lower()
    if status not in AR_STATUS_CHOICES:
        status = AR_STATUS_ALL

    window = 7
    try:
        window = int(due_soon_window if due_soon_window not in (None, '') else 7)
    except (TypeError, ValueError):
        window = 7
    if window not in DUE_SOON_WINDOWS:
        window = 7

    bucket = str(overdue_bucket or '').strip().lower()
    if bucket and bucket not in OVERDUE_BUCKETS:
        bucket = ''

    return {
        'ar_status': status,
        'due_soon_window': window,
        'overdue_bucket': bucket,
    }


def _overdue_days_in_bucket(aging_days, bucket_key):
    if not bucket_key:
        return aging_days > 0
    low, high = OVERDUE_BUCKETS[bucket_key]
    if high is None:
        return aging_days >= low
    return low <= aging_days <= high


def _line_matches_ar_filters(line, *, ar_status, due_soon_window, overdue_bucket, today):
    if ar_status == AR_STATUS_OUTSTANDING:
        return True
    if ar_status == AR_STATUS_OVERDUE:
        if not line.is_overdue:
            return False
        return _overdue_days_in_bucket(line.aging_days, overdue_bucket)
    if ar_status == AR_STATUS_DUE_SOON:
        if line.is_overdue or line.due_date is None:
            return False
        days_until = (line.due_date - today).days
        if due_soon_window == 0:
            return days_until == 0
        return 0 <= days_until <= due_soon_window
    return True


def _cliente_matches_ar_filters(summary, *, ar_status, due_soon_window, overdue_bucket, today):
    if ar_status == AR_STATUS_ALL:
        return True
    if ar_status == AR_STATUS_NO_DEBT:
        return not summary.has_balance
    if not summary.has_balance:
        return False
    invoice_lines = [line for line in summary.lines if line.source == 'invoice']
    if not invoice_lines and ar_status in (AR_STATUS_OVERDUE, AR_STATUS_DUE_SOON):
        # QuickBooks-only remainder: treat as outstanding/overdue without due-date buckets.
        if ar_status == AR_STATUS_OVERDUE and not overdue_bucket:
            return summary.overdue_balance > 0
        return ar_status == AR_STATUS_OUTSTANDING
    if ar_status == AR_STATUS_OUTSTANDING:
        return True
    return any(
        _line_matches_ar_filters(
            line,
            ar_status=ar_status,
            due_soon_window=due_soon_window,
            overdue_bucket=overdue_bucket,
            today=today,
        )
        for line in invoice_lines
    )


def _priority_sort_key(summary):
    nearest = summary.nearest_due_date or timezone.localdate()
    return (
        -summary.max_aging_days,
        -summary.total_open_balance,
        nearest,
    )


def filter_clientes_queryset_by_receivables(
    queryset,
    *,
    ar_status=AR_STATUS_ALL,
    due_soon_window=7,
    overdue_bucket='',
    today=None,
):
    """Filter (and optionally priority-sort) customers by open receivables."""
    params = normalize_ar_filter_params(
        ar_status=ar_status,
        due_soon_window=due_soon_window,
        overdue_bucket=overdue_bucket,
    )
    ar_status = params['ar_status']
    due_soon_window = params['due_soon_window']
    overdue_bucket = params['overdue_bucket']
    today = today or timezone.localdate()

    if ar_status == AR_STATUS_ALL:
        return queryset

    cliente_ids = list(queryset.values_list('pk', flat=True))
    if not cliente_ids:
        return queryset.none()

    invoices = list(open_receivable_invoices_queryset(cliente_ids=cliente_ids))
    invoices_by_cliente = defaultdict(list)
    for invoice in invoices:
        invoices_by_cliente[invoice.cliente_id].append(invoice)

    # Lightweight cliente stubs for summary building (reuse ORM instances from queryset).
    clientes_by_id = queryset.in_bulk(cliente_ids)
    matching_ids = []
    priority_keys = {}

    for cliente_id in cliente_ids:
        cliente = clientes_by_id.get(cliente_id)
        if cliente is None:
            continue
        summary = build_customer_balance_summary(
            cliente,
            invoices=invoices_by_cliente.get(cliente_id, []),
            today=today,
        )
        if not _cliente_matches_ar_filters(
            summary,
            ar_status=ar_status,
            due_soon_window=due_soon_window,
            overdue_bucket=overdue_bucket,
            today=today,
        ):
            continue
        matching_ids.append(cliente_id)
        priority_keys[cliente_id] = _priority_sort_key(summary)

    if ar_status == AR_STATUS_NO_DEBT:
        return queryset.filter(pk__in=matching_ids)

    # Debt-oriented views: most overdue → highest balance → nearest due date.
    matching_ids.sort(key=lambda pk: priority_keys[pk])
    if not matching_ids:
        return queryset.none()

    preserved = Case(
        *[When(pk=pk, then=pos) for pos, pk in enumerate(matching_ids)],
        output_field=IntegerField(),
    )
    return queryset.filter(pk__in=matching_ids).order_by(preserved)


def build_customers_receivables_summary(queryset, *, today=None):
    """Aggregate AR metrics for the (already scoped) customer queryset."""
    today = today or timezone.localdate()
    cliente_ids = list(queryset.values_list('pk', flat=True))
    empty = CustomersReceivablesSummary(
        customers_with_balance=0,
        total_outstanding=Decimal('0.00'),
        invoices_overdue=0,
        invoices_due_this_week=0,
    )
    if not cliente_ids:
        return empty

    invoices = list(open_receivable_invoices_queryset(cliente_ids=cliente_ids))
    invoices_by_cliente = defaultdict(list)
    for invoice in invoices:
        invoices_by_cliente[invoice.cliente_id].append(invoice)

    clientes_by_id = queryset.model.objects.filter(pk__in=cliente_ids).in_bulk()
    customers_with_balance = 0
    total_outstanding = Decimal('0.00')
    invoices_overdue = 0
    invoices_due_this_week = 0
    week_end = today + timedelta(days=7)

    for cliente_id in cliente_ids:
        cliente = clientes_by_id.get(cliente_id)
        if cliente is None:
            continue
        summary = build_customer_balance_summary(
            cliente,
            invoices=invoices_by_cliente.get(cliente_id, []),
            today=today,
        )
        if summary.has_balance:
            customers_with_balance += 1
            total_outstanding = _quantize_money(total_outstanding + summary.total_open_balance)
        for line in summary.lines:
            if line.source != 'invoice':
                continue
            if line.is_overdue:
                invoices_overdue += 1
            elif line.due_date is not None and today <= line.due_date <= week_end:
                invoices_due_this_week += 1

    return CustomersReceivablesSummary(
        customers_with_balance=customers_with_balance,
        total_outstanding=_quantize_money(total_outstanding),
        invoices_overdue=invoices_overdue,
        invoices_due_this_week=invoices_due_this_week,
    )
