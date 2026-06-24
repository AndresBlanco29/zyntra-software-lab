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


def get_pdf_logo_path():
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


def build_pdf_brand_banner(*, styles, title, subtitle='', total_width=540):
    logo_cell = build_pdf_logo_image(max_width=94, max_height=52)
    if logo_cell:
        logo_cell.hAlign = 'LEFT'
    else:
        fallback_style = ParagraphStyle(
            'PdfBrandFallback',
            parent=styles['BodyText'],
            fontName='Helvetica-Bold',
            fontSize=13,
            textColor=colors.white,
            leading=16,
        )
        logo_cell = Paragraph(BRAND_NAME, fallback_style)

    title_html = (
        f'<font size="11" color="#FFD400"><b>{BRAND_NAME}</b></font>'
        f'<br/><font size="18" color="#FFFFFF"><b>{title}</b></font>'
    )
    if subtitle:
        title_html += f'<br/><font size="10" color="#DBEAFE">{subtitle}</font>'

    title_style = ParagraphStyle(
        'PdfBrandTitle',
        parent=styles['BodyText'],
        fontName='Helvetica',
        fontSize=11,
        leading=15,
        textColor=colors.white,
    )

    banner = Table(
        [[logo_cell, Paragraph(title_html, title_style)]],
        colWidths=[108, max(total_width - 108, 220)],
    )
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