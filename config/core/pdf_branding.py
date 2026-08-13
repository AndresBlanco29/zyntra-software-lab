from io import BytesIO
from pathlib import Path

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Paragraph, Table, TableStyle


BRAND_NAME = 'La Tortilla Grocery'
BRAND_PRIMARY = colors.HexColor('#0B3D91')
BRAND_SECONDARY = colors.HexColor('#0C64CF')
BRAND_ACCENT = colors.HexColor('#FFD400')
BRAND_SURFACE = colors.HexColor('#F8FAFC')
BRAND_SOFT_BLUE = colors.HexColor('#DBEAFE')
BRAND_BORDER = colors.HexColor('#CBD5E1')
BRAND_TEXT = colors.HexColor('#1E293B')
BRAND_MUTED_TEXT = colors.HexColor('#64748B')
BRAND_ACCENT_HEX = '#FFD400'
BRAND_SOFT_HEX = '#DBEAFE'


def _demo_mode_active():
    return bool(getattr(settings, 'DEMO_MODE', False))


def _apply_demo_brand_palette():
    """Swap PDF palette when DEMO_MODE / Zyntra is active."""
    global BRAND_NAME, BRAND_PRIMARY, BRAND_SECONDARY, BRAND_ACCENT
    global BRAND_SOFT_BLUE, BRAND_ACCENT_HEX, BRAND_SOFT_HEX
    if not _demo_mode_active():
        return
    from config.core.demo_branding import get_demo_brand_legal_name

    # Match home / panel aurora (void + violet), not Tortilla blue/teal/yellow.
    BRAND_NAME = get_demo_brand_legal_name()
    BRAND_PRIMARY = colors.HexColor('#0B1224')
    BRAND_SECONDARY = colors.HexColor('#312E81')
    BRAND_ACCENT = colors.HexColor('#A78BFA')
    BRAND_SOFT_BLUE = colors.HexColor('#EDE9FE')
    BRAND_ACCENT_HEX = '#A78BFA'
    BRAND_SOFT_HEX = '#E9D5FF'


_apply_demo_brand_palette()


def get_pdf_logo_path():
    """Return a raster logo path, or '' when none should be shown.

    In DEMO_MODE never fall back to Tortilla ``logo.png``.
    """
    if _demo_mode_active():
        # Prefer an optional Zyntra raster mark; SVG is not reliable in ReportLab.
        demo_candidates = (
            Path(settings.BASE_DIR) / 'static' / 'img' / 'zyntra-mark.png',
            Path(settings.BASE_DIR) / 'staticfiles' / 'img' / 'zyntra-mark.png',
        )
        for path in demo_candidates:
            if path.exists():
                return str(path)
        return ''

    candidate_paths = (
        Path(settings.BASE_DIR) / 'static' / 'img' / 'logo.png',
        Path(settings.BASE_DIR) / 'staticfiles' / 'img' / 'logo.png',
    )
    for path in candidate_paths:
        if path.exists():
            return str(path)
    return ''


def build_pdf_logo_image(*, max_width, max_height):
    logo_path = get_pdf_logo_path()
    if not logo_path:
        return None

    image_reader = ImageReader(logo_path)
    original_width, original_height = image_reader.getSize()
    if not original_width or not original_height:
        return None

    width_ratio = max_width / float(original_width)
    height_ratio = max_height / float(original_height)
    scale = min(width_ratio, height_ratio)

    return Image(
        logo_path,
        width=float(original_width) * scale,
        height=float(original_height) * scale,
    )


def build_pdf_storage_image(field_file, *, max_width=44, max_height=44):
    """Build a ReportLab Image from a Django File/ImageField via storage.

    Works with local MEDIA and remote backends (e.g. Cloudinary). Returns None
    when the file is missing or cannot be decoded.
    """
    if field_file is None:
        return None
    name = getattr(field_file, 'name', None) or ''
    if not str(name).strip():
        return None

    try:
        with field_file.open('rb') as handle:
            payload = handle.read()
    except Exception:
        try:
            storage = getattr(field_file, 'storage', None)
            if storage is None or not name:
                return None
            with storage.open(name, 'rb') as handle:
                payload = handle.read()
        except Exception:
            return None

    if not payload:
        return None

    try:
        image_buffer = BytesIO(payload)
        image_reader = ImageReader(image_buffer)
        original_width, original_height = image_reader.getSize()
        if not original_width or not original_height:
            return None

        width_ratio = float(max_width) / float(original_width)
        height_ratio = float(max_height) / float(original_height)
        scale = min(width_ratio, height_ratio)
        display_width = float(original_width) * scale
        display_height = float(original_height) * scale

        # Fresh buffer: ImageReader may have consumed the previous one.
        return Image(BytesIO(payload), width=display_width, height=display_height)
    except Exception:
        return None


def build_pdf_brand_text_mark(*, styles, font_size=12):
    """Text brand mark used when no raster logo is available (demo / missing asset)."""
    return Paragraph(
        BRAND_NAME,
        ParagraphStyle(
            'PdfBrandFallback',
            parent=styles['BodyText'],
            fontName='Helvetica-Bold',
            fontSize=font_size,
            textColor=colors.white,
            leading=font_size + 2,
        ),
    )


def get_pdf_company_contact_lines():
    """Company contact lines for PDF headers (demo always uses fictitious Zyntra data)."""
    if _demo_mode_active():
        from config.core.demo_branding import (
            DEMO_ADDRESS_LINE_1,
            DEMO_ADDRESS_LINE_2,
            DEMO_EMAIL,
            DEMO_PHONE_DISPLAY,
        )

        return [
            DEMO_ADDRESS_LINE_1,
            DEMO_ADDRESS_LINE_2,
            DEMO_EMAIL,
            DEMO_PHONE_DISPLAY,
        ]

    from config.core.models import HomeContenido

    contenido = HomeContenido.objects.filter(activo=True).order_by('-actualizado').first()
    if contenido:
        lines = [
            contenido.footer_contacto_direccion_linea_1,
            contenido.footer_contacto_direccion_linea_2,
            contenido.footer_contacto_email,
            contenido.footer_contacto_telefono,
        ]
    else:
        lines = [
            '1666 Roswell Rd Bldg 100',
            'Marietta, GA 30062-3639',
            'latortilla@gmail.com',
            '+1 (470) 967 2782',
        ]
    return [line.strip() for line in lines if line and str(line).strip()]


def build_pdf_brand_banner(*, styles, title, subtitle='', document_date='', total_width=540):
    from html import escape

    logo_cell = build_pdf_logo_image(max_width=94, max_height=52)
    if logo_cell:
        logo_cell.hAlign = 'LEFT'
    else:
        logo_cell = build_pdf_brand_text_mark(styles=styles, font_size=13)

    title_html = (
        f'<font size="11" color="{BRAND_ACCENT_HEX}"><b>{escape(BRAND_NAME)}</b></font>'
        f'<br/><font size="18" color="#FFFFFF"><b>{escape(str(title))}</b></font>'
    )
    if subtitle:
        title_html += f'<br/><font size="10" color="{BRAND_SOFT_HEX}">{escape(str(subtitle))}</font>'

    title_style = ParagraphStyle(
        'PdfBrandTitle',
        parent=styles['BodyText'],
        fontName='Helvetica',
        fontSize=11,
        leading=15,
        textColor=colors.white,
    )

    date_width = 0
    cells = [logo_cell, Paragraph(title_html, title_style)]
    col_widths = [108, max(total_width - 108, 220)]
    if document_date:
        date_width = 110
        col_widths = [108, max(total_width - 108 - date_width, 180), date_width]
        date_style = ParagraphStyle(
            'PdfBrandDate',
            parent=styles['BodyText'],
            fontName='Helvetica-Bold',
            fontSize=9,
            leading=11,
            textColor=colors.white,
            alignment=2,  # TA_RIGHT
        )
        cells.append(
            Paragraph(
                f'<font size="8" color="{BRAND_SOFT_HEX}">Date</font><br/>'
                f'<font size="11" color="#FFFFFF"><b>{escape(str(document_date))}</b></font>',
                date_style,
            )
        )

    banner = Table([cells], colWidths=col_widths)
    banner.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BRAND_PRIMARY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 14),
        ('RIGHTPADDING', (0, 0), (-1, -1), 14),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))
    return banner


class NumberedPdfCanvas(canvas.Canvas):
	def __init__(self, *args, page_label_template='Page %(current)s of %(total)s', **kwargs):
		canvas.Canvas.__init__(self, *args, **kwargs)
		self.page_label_template = page_label_template
		self._saved_page_states = []

	def showPage(self):
		self._saved_page_states.append(dict(self.__dict__))
		self._startPage()

	def save(self):
		page_count = len(self._saved_page_states)
		for page_state in self._saved_page_states:
			self.__dict__.update(page_state)
			self._draw_page_number(page_count)
			canvas.Canvas.showPage(self)
		canvas.Canvas.save(self)

	def _draw_page_number(self, page_count):
		self.setFont('Helvetica', 8)
		self.setFillColor(BRAND_MUTED_TEXT)
		self.drawRightString(
			self._pagesize[0] - 24,
			14,
			self.page_label_template % {'current': self._pageNumber, 'total': page_count},
		)
