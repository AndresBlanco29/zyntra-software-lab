"""Commercial follow-up PDFs for quotes and sales orders (WhatsApp / customer tracking).

Not warehouse picking tickets. Uses DEMO-aware branding from ``pdf_branding``.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.core.datetime_formats import format_local_datetime
from config.core.demo_branding import get_active_brand_name
from config.core.pdf_branding import (
    BRAND_BORDER,
    BRAND_MUTED_TEXT,
    BRAND_PRIMARY,
    BRAND_SOFT_BLUE,
    BRAND_SURFACE,
    BRAND_TEXT,
    NumberedPdfCanvas,
    build_pdf_brand_banner,
    build_pdf_storage_image,
    get_pdf_company_contact_lines,
)
from config.integrations.quickbooks.sync import resolve_customer_company_name


DECIMAL_ZERO = Decimal('0.00')


def _money(value):
    amount = value if value is not None else DECIMAL_ZERO
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount or '0'))
    return f'${amount.quantize(Decimal("0.01")):,.2f}'


def _line_name(item):
    presentacion = getattr(item, 'presentacion', None)
    producto = getattr(presentacion, 'producto', None) if presentacion is not None else None
    name = getattr(producto, 'nombre', None) or str(presentacion or '-')
    if getattr(item, 'es_regalo', False):
        return f'{name} ({_("FREE")})'
    return name


def _pack_label(item):
    presentacion = getattr(item, 'presentacion', None)
    if presentacion is None:
        return '-'
    return getattr(presentacion, 'nombre', None) or '-'


def build_quote_line_rows(cotizacion):
    rows = []
    total = DECIMAL_ZERO
    items = cotizacion.items.select_related('presentacion__producto').order_by('id')
    for item in items:
        qty = int(item.cantidad or 0)
        unit = item.precio_unitario_neto if hasattr(item, 'precio_unitario_neto') else item.precio
        if getattr(item, 'es_regalo', False):
            unit = DECIMAL_ZERO
            line_total = DECIMAL_ZERO
        else:
            line_total = getattr(item, 'subtotal', None)
            if line_total is None:
                line_total = (Decimal(str(unit or 0)) * Decimal(qty)).quantize(Decimal('0.01'))
            total += Decimal(str(line_total or 0))
        discount = getattr(item, 'descuento_linea_total', None)
        if discount is None:
            discount = getattr(item, 'descuento_monto', DECIMAL_ZERO) or DECIMAL_ZERO
            if getattr(item, 'descuento_aplicado', False):
                discount = Decimal(str(discount)) * Decimal(qty)
            else:
                discount = DECIMAL_ZERO
        product = getattr(getattr(item, 'presentacion', None), 'producto', None)
        rows.append({
            'name': _line_name(item),
            'pack': _pack_label(item),
            'qty': qty,
            'unit_price': unit,
            'discount': discount,
            'line_total': line_total,
            'is_gift': bool(getattr(item, 'es_regalo', False)),
            'image': getattr(product, 'imagen', None) if product is not None else None,
        })
    if cotizacion.total is not None:
        total = Decimal(str(cotizacion.total))
    return rows, total


def build_order_line_rows(pedido):
    rows = []
    total = DECIMAL_ZERO
    items = pedido.items.select_related('presentacion__producto').order_by('id')
    for item in items:
        qty = int(getattr(item, 'cantidad_solicitada', None) or item.cantidad or 0)
        unit = item.precio_unitario_neto if hasattr(item, 'precio_unitario_neto') else item.precio
        if getattr(item, 'es_regalo', False):
            unit = DECIMAL_ZERO
            line_total = DECIMAL_ZERO
        else:
            line_total = getattr(item, 'subtotal', None)
            if line_total is None:
                line_total = (Decimal(str(unit or 0)) * Decimal(qty)).quantize(Decimal('0.01'))
            total += Decimal(str(line_total or 0))
        discount = getattr(item, 'descuento_linea_total', None)
        if discount is None:
            discount = DECIMAL_ZERO
        rows.append({
            'name': _line_name(item),
            'pack': _pack_label(item),
            'qty': qty,
            'unit_price': unit,
            'discount': discount,
            'line_total': line_total,
            'is_gift': bool(getattr(item, 'es_regalo', False)),
        })
    if pedido.total is not None:
        total = Decimal(str(pedido.total))
    return rows, total


def build_commercial_document_pdf_response(
    *,
    document_title,
    document_ref,
    filename,
    customer,
    sales_rep='',
    status_label='',
    notes='',
    lines,
    total,
    document_date=None,
    include_product_photos=False,
):
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=28,
        rightMargin=28,
        topMargin=24,
        bottomMargin=28,
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        'CommercialBody',
        parent=styles['BodyText'],
        fontSize=8,
        leading=10,
        textColor=BRAND_TEXT,
    )
    muted = ParagraphStyle(
        'CommercialMuted',
        parent=body,
        fontSize=7.5,
        textColor=BRAND_MUTED_TEXT,
    )
    header_cell = ParagraphStyle(
        'CommercialHeaderCell',
        parent=body,
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9,
        textColor=colors.white,
        alignment=TA_CENTER,
    )
    center = ParagraphStyle('CommercialCenter', parent=body, alignment=TA_CENTER)
    right = ParagraphStyle('CommercialRight', parent=body, alignment=TA_RIGHT)

    page_width, _page_height = letter
    content_width = page_width - document.leftMargin - document.rightMargin
    company_name = get_active_brand_name()
    contact_lines = get_pdf_company_contact_lines()
    customer_name = resolve_customer_company_name(customer) if customer is not None else '-'
    dated = document_date or format_local_datetime(timezone.now())

    content = [
        build_pdf_brand_banner(
            styles=styles,
            title=document_title,
            subtitle=f'{company_name} · {document_ref}',
            document_date=dated,
            total_width=content_width,
        ),
        Spacer(1, 10),
    ]

    meta = Table(
        [
            [
                Paragraph(f'<b>{escape(_("Customer"))}</b><br/>{escape(customer_name)}', body),
                Paragraph(
                    f'<b>{escape(_("Status"))}</b><br/>{escape(status_label or "-")}<br/>'
                    f'<b>{escape(_("Sales rep"))}</b><br/>{escape(sales_rep or "-")}',
                    body,
                ),
                Paragraph(
                    f'<b>{escape(_("Contact"))}</b><br/>' + '<br/>'.join(escape(line) for line in contact_lines[:3]),
                    muted,
                ),
            ]
        ],
        colWidths=[content_width * 0.38, content_width * 0.28, content_width * 0.34],
    )
    meta.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
        ('BACKGROUND', (0, 0), (-1, -1), BRAND_SURFACE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    content.extend([meta, Spacer(1, 10)])

    if notes:
        content.extend([
            Paragraph(f'<b>{escape(_("Notes"))}</b>', body),
            Paragraph(escape(notes), muted),
            Spacer(1, 8),
        ])

    if include_product_photos:
        table_rows = [[
            Paragraph(escape(_('Photo')), header_cell),
            Paragraph(escape(_('Product')), header_cell),
            Paragraph(escape(_('Presentation')), header_cell),
            Paragraph(escape(_('Qty')), header_cell),
            Paragraph(escape(_('Unit price')), header_cell),
            Paragraph(escape(_('Line total')), header_cell),
        ]]
        for row in lines:
            photo_cell = build_pdf_storage_image(
                row.get('image'),
                max_width=44,
                max_height=44,
            )
            if photo_cell is not None:
                photo_cell.hAlign = 'CENTER'
            else:
                photo_cell = Paragraph('—', center)
            table_rows.append([
                photo_cell,
                Paragraph(escape(row['name']), body),
                Paragraph(escape(str(row['pack'])), body),
                Paragraph(str(row['qty']), center),
                Paragraph(_money(row['unit_price']), center),
                Paragraph(_money(row['line_total']), right),
            ])
        if len(table_rows) == 1:
            table_rows.append([
                Paragraph('—', center),
                Paragraph('—', body),
                Paragraph('—', body),
                Paragraph('0', center),
                Paragraph(_money(0), center),
                Paragraph(_money(0), right),
            ])
        col_widths = [
            content_width * 0.12,
            content_width * 0.30,
            content_width * 0.20,
            content_width * 0.10,
            content_width * 0.14,
            content_width * 0.14,
        ]
    else:
        table_rows = [[
            Paragraph(escape(_('Product')), header_cell),
            Paragraph(escape(_('Pack')), header_cell),
            Paragraph(escape(_('Qty')), header_cell),
            Paragraph(escape(_('Unit price')), header_cell),
            Paragraph(escape(_('Discount')), header_cell),
            Paragraph(escape(_('Line total')), header_cell),
        ]]
        for row in lines:
            table_rows.append([
                Paragraph(escape(row['name']), body),
                Paragraph(escape(str(row['pack'])), body),
                Paragraph(str(row['qty']), center),
                Paragraph(_money(row['unit_price']), center),
                Paragraph(_money(row['discount']) if row['discount'] else '—', center),
                Paragraph(_money(row['line_total']), right),
            ])
        if len(table_rows) == 1:
            table_rows.append([
                Paragraph('—', body),
                Paragraph('—', body),
                Paragraph('0', center),
                Paragraph(_money(0), center),
                Paragraph('—', center),
                Paragraph(_money(0), right),
            ])
        col_widths = [
            content_width * 0.34,
            content_width * 0.16,
            content_width * 0.08,
            content_width * 0.14,
            content_width * 0.14,
            content_width * 0.14,
        ]
    items_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BRAND_SURFACE]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    content.extend([items_table, Spacer(1, 12)])

    totals = Table(
        [[
            Paragraph(escape(_('Total')), ParagraphStyle('TotalLabel', parent=body, fontName='Helvetica-Bold')),
            Paragraph(_money(total), ParagraphStyle('TotalValue', parent=right, fontName='Helvetica-Bold', fontSize=11)),
        ]],
        colWidths=[content_width * 0.72, content_width * 0.28],
    )
    totals.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BRAND_SOFT_BLUE),
        ('BOX', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    content.append(totals)
    content.append(Spacer(1, 10))
    content.append(
        Paragraph(
            escape(_('Commercial summary for follow-up. Not a warehouse picking ticket.')),
            muted,
        )
    )

    document.build(
        content,
        canvasmaker=lambda *args, **kwargs: NumberedPdfCanvas(
            *args,
            page_label_template=_('Page %(current)s of %(total)s'),
            **kwargs,
        ),
    )
    pdf = buffer.getvalue()
    buffer.close()
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def quote_pdf_response(cotizacion):
    lines, total = build_quote_line_rows(cotizacion)
    sales_rep = ''
    if cotizacion.vendedor_id:
        sales_rep = cotizacion.vendedor.get_full_name() or cotizacion.vendedor.username
    notes = (cotizacion.nota_cliente or cotizacion.nota_backoffice or '').strip()
    return build_commercial_document_pdf_response(
        document_title=_('Quote'),
        document_ref=f'#{cotizacion.id}',
        filename=f'Quote-{cotizacion.id}.pdf',
        customer=cotizacion.cliente,
        sales_rep=sales_rep,
        status_label=cotizacion.get_estado_display(),
        notes=notes,
        lines=lines,
        total=total,
        document_date=format_local_datetime(cotizacion.fecha) if cotizacion.fecha else None,
        include_product_photos=True,
    )


def order_pdf_response(pedido):
    lines, total = build_order_line_rows(pedido)
    sales_rep = ''
    if pedido.vendedor_id:
        sales_rep = pedido.vendedor.get_full_name() or pedido.vendedor.username
    notes = (pedido.nota_cliente or '').strip()
    ref = pedido.numero_display
    return build_commercial_document_pdf_response(
        document_title=_('Sales Order'),
        document_ref=f'#{ref}',
        filename=f'Order-{ref}.pdf',
        customer=pedido.cliente,
        sales_rep=sales_rep,
        status_label=pedido.get_estado_display(),
        notes=notes,
        lines=lines,
        total=total,
        document_date=format_local_datetime(pedido.creada_en) if getattr(pedido, 'creada_en', None) else None,
    )
