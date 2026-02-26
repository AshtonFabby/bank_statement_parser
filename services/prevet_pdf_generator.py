"""PreVet PDF generation service using ReportLab."""

import io
import urllib.request
from typing import Any, Dict

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable

# ── Design tokens (matching bank analysis style) ────────────────────────────
GREEN_PRIMARY = colors.HexColor("#00ce4c")
GREEN_SECONDARY = colors.HexColor("#00a83d")
HEADER_BG = colors.HexColor("#f0faf4")
BORDER_COLOR = colors.HexColor("#e8f0ec")
ROW_ALT = colors.HexColor("#fafcfb")
TEXT_DARK = colors.HexColor("#2d3748")
TEXT_HEADING = colors.HexColor("#1a1a2e")
LABEL_COLOR = colors.HexColor("#4a5568")
CARD_BG = colors.HexColor("#f8faf9")
INSIGHT_BORDER = colors.HexColor("#00ce4c")

LOGO_URL = "https://res.cloudinary.com/dbmvi2vd7/image/upload/v1738309402/getfunds-logo.png"
CORNER_RADIUS = 6

_logo_bytes: bytes | None = None


def _fetch_logo() -> io.BytesIO | None:
    global _logo_bytes
    if _logo_bytes is None:
        try:
            with urllib.request.urlopen(LOGO_URL, timeout=5) as resp:
                _logo_bytes = resp.read()
        except Exception:
            return None
    return io.BytesIO(_logo_bytes)


def _logo_flowable(target_height: float = 60) -> Image | None:
    data = _fetch_logo()
    if data is None:
        return None
    reader = ImageReader(data)
    nat_w, nat_h = reader.getSize()
    target_w = target_height * (nat_w / nat_h)
    data.seek(0)
    img = Image(data, width=target_w, height=target_height)
    img.hAlign = "CENTER"
    return img


class RoundedTable(Flowable):
    def __init__(self, table: Table, radius: int = CORNER_RADIUS, border_color=BORDER_COLOR):
        Flowable.__init__(self)
        self.table = table
        self.radius = radius
        self.border_color = border_color
        self._w = 0.0
        self._h = 0.0

    def wrap(self, availWidth, availHeight):
        self._w, self._h = self.table.wrap(availWidth, availHeight)
        self.width = self._w
        self.height = self._h
        return self._w, self._h

    def split(self, availWidth, availHeight):
        parts = self.table.split(availWidth, availHeight)
        return [RoundedTable(p, self.radius, self.border_color) for p in parts]

    def draw(self):
        c = self.canv
        c.saveState()
        path = c.beginPath()
        path.roundRect(0, 0, self._w, self._h, self.radius)
        c.clipPath(path, stroke=0, fill=0)
        self.table.drawOn(c, 0, 0)
        c.restoreState()
        c.saveState()
        c.setStrokeColor(self.border_color)
        c.setLineWidth(0.5)
        c.roundRect(0, 0, self._w, self._h, self.radius, stroke=1, fill=0)
        c.restoreState()


def _green_table_style() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), GREEN_SECONDARY),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("TEXTCOLOR", (0, 1), (-1, -1), TEXT_DARK),
        ("TOPPADDING", (0, 1), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 1), (0, -1), LABEL_COLOR),
    ])


def _val(data: Dict[str, Any], key: str) -> str:
    v = data.get(key, "")
    return str(v) if v else "N/A"


def _detail_card_table(items: list[tuple[str, str]], cols: int, available_width: float) -> RoundedTable:
    """Render a grid of label/value cards as a table."""
    col_w = available_width / cols
    rows = []
    for i in range(0, len(items), cols):
        chunk = items[i : i + cols]
        # pad to fill row
        while len(chunk) < cols:
            chunk.append(("", ""))
        label_row = [Paragraph(f'<font color="#00a83d" size="8"><b>{lbl.upper()}</b></font>', getSampleStyleSheet()["Normal"]) for lbl, _ in chunk]
        value_row = [Paragraph(f'<font color="#2d3748" size="11">{val}</font>', getSampleStyleSheet()["Normal"]) for _, val in chunk]
        rows.append(label_row)
        rows.append(value_row)

    col_widths = [col_w] * cols
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return RoundedTable(t)


def _insight_table(label: str, value: str, explanation: str, available_width: float) -> RoundedTable:
    """Render an insight item (label + badge + explanation) as a rounded table."""
    styles = getSampleStyleSheet()
    header = Paragraph(
        f'<font color="#4a5568" size="9"><b>{label.upper()}</b></font>'
        f'  <font color="#1a1a2e" size="9"><b>{value}</b></font>',
        styles["Normal"],
    )
    exp_label = Paragraph('<font color="#a0aec0" size="8"><b>EXPLANATION</b></font>', styles["Normal"])
    exp_text = Paragraph(f'<font color="#4a5568" size="9">{explanation or "N/A"}</font>', styles["Normal"])

    t = Table(
        [[header], [exp_label], [exp_text]],
        colWidths=[available_width],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LINEBEFORE", (0, 0), (0, -1), 3, GREEN_PRIMARY),
    ]))
    return RoundedTable(t)


def generate_prevet_pdf(form_data: Dict[str, Any]) -> io.BytesIO:
    """Generate a PreVet PDF from form_data dict using ReportLab."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    available_w = A4[0] - 1.5 * inch
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontSize=20,
        fontName="Helvetica-Bold",
        textColor=TEXT_HEADING,
        spaceAfter=4,
        alignment=1,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#a0aec0"),
        alignment=1,
        spaceAfter=24,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica-Bold",
        textColor=GREEN_PRIMARY,
        spaceBefore=20,
        spaceAfter=8,
    )

    # ── Header ────────────────────────────────────────────────────────────
    logo = _logo_flowable(target_height=60)
    if logo:
        elements.append(logo)
        elements.append(Spacer(1, 10))
    elements.append(Paragraph("Pre Vetting Form", title_style))
    elements.append(Paragraph(f"Business: {_val(form_data, 'business_name')}", subtitle_style))

    # ── Business Details ──────────────────────────────────────────────────
    elements.append(Paragraph("BUSINESS DETAILS", section_style))
    biz_items = [
        ("Business Name", _val(form_data, "business_name")),
        ("Province", _val(form_data, "province")),
        ("City", _val(form_data, "city")),
        ("Suburb", _val(form_data, "suburb")),
        ("Business Model", _val(form_data, "model")),
        ("Industry", _val(form_data, "industry")),
        ("Deal Type", _val(form_data, "funding_type")),
        ("Funding Type", _val(form_data, "option")),
        ("Amount Requested", _val(form_data, "amount")),
        ("Price Quoted", _val(form_data, "price_quoted")),
        ("GP%", _val(form_data, "gp")),
        ("Lead Gen", _val(form_data, "lead_gen")),
        ("Broker", _val(form_data, "broker")),
    ]
    elements.append(_detail_card_table(biz_items, cols=3, available_width=available_w))

    # ── Overdraft ─────────────────────────────────────────────────────────
    elements.append(Paragraph("OVERDRAFT", section_style))
    od_data = [
        ["", "Bank Account", "Amount"],
        ["Overdraft 1", _val(form_data, "od1_account"), _val(form_data, "od1_amount")],
        ["Overdraft 2", _val(form_data, "od2_account"), _val(form_data, "od2_amount")],
    ]
    od_table = Table(od_data, colWidths=[1.2 * inch, 2.9 * inch, 2.9 * inch])
    od_table.setStyle(_green_table_style())
    elements.append(RoundedTable(od_table))

    # ── MCA Section ───────────────────────────────────────────────────────
    elements.append(Paragraph("MCA SECTION", section_style))
    mca_items = [
        ("MCA Balance 1", _val(form_data, "current_mca_balance1")),
        ("MCA Advance 1", _val(form_data, "original_mca_advance1")),
        ("MCA Balance 2", _val(form_data, "current_mca_balance2")),
        ("MCA Advance 2", _val(form_data, "original_mca_advance2")),
    ]
    elements.append(_detail_card_table(mca_items, cols=4, available_width=available_w))

    # ── Funding Section ───────────────────────────────────────────────────
    elements.append(Paragraph("FUNDING SECTION", section_style))
    funding_items = [
        ("Price Tier Quoted", _val(form_data, "price_tier_quoted")),
        ("Term Required", _val(form_data, "term_required")),
        ("Funder/Broker Requested", _val(form_data, "funder_broker_requested")),
        ("Funding Required Date", _val(form_data, "funding_required_date")),
        ("Funding Type", _val(form_data, "funding_type")),
    ]
    elements.append(_detail_card_table(funding_items, cols=3, available_width=available_w))
    elements.append(Spacer(1, 8))
    fu_items = [("Funding Use", _val(form_data, "funding_use"))]
    elements.append(_detail_card_table(fu_items, cols=1, available_width=available_w))

    # ── Business Health & Ownership Insights ──────────────────────────────
    elements.append(Paragraph("BUSINESS HEALTH & OWNERSHIP INSIGHTS", section_style))
    health_items = [
        ("Length of Ownership", _val(form_data, "length_of_ownership")),
        ("Website URL", _val(form_data, "website")),
    ]
    elements.append(_detail_card_table(health_items, cols=2, available_width=available_w))
    elements.append(Spacer(1, 10))

    for label, value_key, explanation_key in [
        ("Rental Arrears", "rental_arrears", "rental_arrears_explanation"),
        ("Judgement", "judgement", "judgement_explanation"),
        ("Supplier Arrears", "supplier_arrears", "supplier_arrears_explanation"),
        ("Dips in Turnover", "dips_in_turnover", "dips_in_turnover_explanation"),
    ]:
        elements.append(
            _insight_table(
                label,
                _val(form_data, value_key),
                _val(form_data, explanation_key),
                available_w,
            )
        )
        elements.append(Spacer(1, 6))

    mot_items = [("Motivation", _val(form_data, "motivation"))]
    elements.append(_detail_card_table(mot_items, cols=1, available_width=available_w))

    doc.build(elements)
    buffer.seek(0)
    return buffer
