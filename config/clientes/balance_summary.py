from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from config.facturacion.services import (
    resolve_invoice_payment_base_date,
    resolve_invoice_payment_due_date,
)


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
    def invoice_date_display(self):
        if not self.invoice_date:
            return ''
        try:
            return date_format(self.invoice_date, format='SHORT_DATE', use_l10n=True)
        except (TypeError, ValueError):
            return str(self.invoice_date)

    @property
    def balance_label(self):
        if self.source != 'invoice':
            return str(_('QuickBooks balance'))
        if self.is_overdue:
            return str(_('Due balance'))
        return str(_('Open'))


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


def _build_line_from_invoice(invoice, *, today):
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
            .select_related('delivery')
            .order_by('creada_en')
        )

    raw_lines = []
    for invoice in invoices:
        line = _build_line_from_invoice(invoice, today=today)
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
