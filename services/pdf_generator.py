"""PDF report generation service."""

import io
import urllib.request
from datetime import datetime

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import Flowable

from .summary import Summary, CoverageMetrics, ActivityVolume, RevenueMetrics

# ── Design tokens (matching prevet style) ──────────────────────────────────
GREEN_PRIMARY = colors.HexColor("#00ce4c")
GREEN_SECONDARY = colors.HexColor("#00a83d")
HEADER_BG = colors.HexColor("#f0faf4")
BORDER_COLOR = colors.HexColor("#e8f0ec")
ROW_ALT = colors.HexColor("#fafcfb")
TEXT_DARK = colors.HexColor("#2d3748")
TEXT_HEADING = colors.HexColor("#1a1a2e")
RED_HEADER_BG = colors.HexColor("#fff5f5")
RED_HEADER_TEXT = colors.HexColor("#c0392b")
RED_BORDER = colors.HexColor("#fad5d0")
RED_ROW_ALT = colors.HexColor("#fff8f8")

LOGO_URL = "https://res.cloudinary.com/dbmvi2vd7/image/upload/v1738309402/getfunds-logo.png"
CORNER_RADIUS = 6

# Module-level logo cache (raw bytes)
_logo_bytes: bytes | None = None


class RoundedTable(Flowable):
    """Wraps a ReportLab Table and clips it to a rounded rectangle."""

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
        # Clip all drawing (backgrounds + content) to the rounded rect
        c.saveState()
        path = c.beginPath()
        path.roundRect(0, 0, self._w, self._h, self.radius)
        c.clipPath(path, stroke=0, fill=0)
        self.table.drawOn(c, 0, 0)
        c.restoreState()
        # Draw the rounded border on top of the clipped content
        c.saveState()
        c.setStrokeColor(self.border_color)
        c.setLineWidth(0.5)
        c.roundRect(0, 0, self._w, self._h, self.radius, stroke=1, fill=0)
        c.restoreState()


def _fetch_logo() -> io.BytesIO | None:
    """Fetch the GetFunds logo, caching the raw bytes across calls."""
    global _logo_bytes
    if _logo_bytes is None:
        try:
            with urllib.request.urlopen(LOGO_URL, timeout=5) as resp:
                _logo_bytes = resp.read()
        except Exception:
            return None
    return io.BytesIO(_logo_bytes)


def _logo_flowable(target_height: float = 60) -> Image | None:
    """Return a centered logo Image scaled to target_height points."""
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


def _green_table_style(align_col_0_bold: bool = False) -> TableStyle:
    """Standard prevet-style green table. Border/rounding handled by RoundedTable."""
    commands = [
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
    ]
    if align_col_0_bold:
        commands.append(("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"))
        commands.append(("TEXTCOLOR", (0, 1), (0, -1), colors.HexColor("#4a5568")))
    return TableStyle(commands)


def _red_table_style() -> TableStyle:
    """Clean red-accented table for debits/penalties. Border/rounding handled by RoundedTable."""
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), RED_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), RED_HEADER_TEXT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, RED_BORDER),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("TEXTCOLOR", (0, 1), (-1, -1), TEXT_DARK),
        ("TOPPADDING", (0, 1), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, RED_ROW_ALT]),
    ])


def generate_summary_pdf(
    df: pd.DataFrame,
    summary: Summary,
    coverage: CoverageMetrics | None = None,
    activity: ActivityVolume | None = None,
    revenue: RevenueMetrics | None = None,
) -> io.BytesIO:
    """Generate a summary PDF report.

    Args:
        df: Transaction DataFrame
        summary: Summary object with totals
        coverage: CoverageMetrics object with coverage stats (optional)
        activity: ActivityVolume object with activity metrics (optional)
        revenue: RevenueMetrics object with revenue stats (optional)

    Returns:
        BytesIO buffer containing the PDF
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "CustomTitle",
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
        "SectionHeading",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica-Bold",
        textColor=GREEN_PRIMARY,
        spaceBefore=20,
        spaceAfter=8,
    )

    # ── Header: logo + title ──────────────────────────────────────────────
    logo = _logo_flowable(target_height=60)
    if logo:
        elements.append(logo)
        elements.append(Spacer(1, 10))

    elements.append(Paragraph("Bank Statement Summary", title_style))
    elements.append(
        Paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            subtitle_style,
        )
    )

    # ── Financial Summary ─────────────────────────────────────────────────
    elements.append(Paragraph("FINANCIAL SUMMARY", section_style))

    summary_data = [
        ["Metric", "Value"],
        ["Total Debits", f"R {summary.total_debits:,.2f}"],
        ["Total Credits", f"R {summary.total_credits:,.2f}"],
        ["Net Movement", f"R {summary.net_movement:,.2f}"],
        ["Ending Balance", f"R {summary.ending_balance:,.2f}"],
        ["Transaction Count", str(summary.transaction_count)],
    ]

    summary_table = Table(summary_data, colWidths=[3 * inch, 3 * inch])
    summary_table.setStyle(_green_table_style())
    elements.append(RoundedTable(summary_table))

    # ── Coverage ──────────────────────────────────────────────────────────
    if coverage:
        elements.append(Paragraph("COVERAGE, DATA INTEGRITY & STRUCTURE", section_style))

        coverage_data = [
            ["Metric", "Value"],
            ["Start Date", coverage.start_date or "N/A"],
            ["End Date", coverage.end_date or "N/A"],
            ["Days Covered (Calendar)", str(coverage.days_covered)],
            ["Distinct Months Covered", str(coverage.distinct_months)],
            ["Number of Transactions", str(coverage.transaction_count)],
            ["Number of Accounts Detected", str(coverage.accounts_detected)],
            ["Missing Date Gaps", str(coverage.missing_date_gaps)],
        ]

        coverage_table = Table(coverage_data, colWidths=[3 * inch, 3 * inch])
        coverage_table.setStyle(_green_table_style())
        elements.append(RoundedTable(coverage_table))

    # ── Activity Volume ───────────────────────────────────────────────────
    if activity:
        elements.append(Paragraph("ACTIVITY VOLUME", section_style))

        activity_data = [
            ["Metric", "Value"],
            ["Number of Credit Transactions", str(activity.credit_transaction_count)],
            ["Number of Debit Transactions", str(activity.debit_transaction_count)],
            ["Average Credits per Month", f"{activity.avg_credits_per_month:.2f}"],
            ["Average Debits per Month", f"{activity.avg_debits_per_month:.2f}"],
        ]

        activity_table = Table(activity_data, colWidths=[3 * inch, 3 * inch])
        activity_table.setStyle(_green_table_style())
        elements.append(RoundedTable(activity_table))

    # ── Revenue / Turnover ────────────────────────────────────────────────
    if revenue:
        elements.append(Paragraph("REVENUE / TURNOVER (CREDITS)", section_style))

        revenue_data = [
            ["Metric", "Value"],
            ["Total Credits (Gross Inflow)", f"R {revenue.total_credits:,.2f}"],
            ["Average Monthly Credits", f"R {revenue.avg_monthly_credits:,.2f}"],
            ["Lowest Month Credits", f"R {revenue.lowest_month_credits:,.2f}"],
            ["Highest Month Credits", f"R {revenue.highest_month_credits:,.2f}"],
            [
                "Revenue Volatility %",
                f"{revenue.revenue_volatility_pct:.2f}%"
                if revenue.revenue_volatility_pct is not None
                else "N/A",
            ],
            [
                "Top 5 Credit Concentration %",
                f"{revenue.top_5_credit_concentration_pct:.2f}%"
                if revenue.top_5_credit_concentration_pct is not None
                else "N/A",
            ],
            ["Largest Single Credit", f"R {revenue.largest_single_credit:,.2f}"],
        ]

        revenue_table = Table(revenue_data, colWidths=[3 * inch, 3 * inch])
        revenue_table.setStyle(_green_table_style())
        elements.append(RoundedTable(revenue_table))

    # ── Monthly Turnover ──────────────────────────────────────────────────
    elements.append(Paragraph("MONTHLY TURNOVER", section_style))

    if not df.empty:
        dates = pd.to_datetime(df["Date"], dayfirst=True)
        df_monthly = df.copy()
        df_monthly["Month"] = dates.dt.to_period("M")
        monthly_group = (
            df_monthly.groupby("Month")
            .agg(
                Total_Credits=("Credit", "sum"),
                Credit_Count=("Credit", lambda x: (x > 0).sum()),
            )
            .reset_index()
            .sort_values("Month")
        )

        monthly_data = [["Month", "Total Turnover (Credits)", "Number of Credits"]]
        for _, row in monthly_group.iterrows():
            monthly_data.append([
                str(row["Month"]),
                f"R {row['Total_Credits']:,.2f}",
                str(int(row["Credit_Count"])),
            ])

        monthly_table = Table(monthly_data, colWidths=[2 * inch, 2.5 * inch, 1.5 * inch])
        monthly_table.setStyle(_green_table_style(align_col_0_bold=True))
        elements.append(RoundedTable(monthly_table))

    # ── Top 15 Credits ────────────────────────────────────────────────────
    elements.append(Paragraph("TOP 15 CREDITS", section_style))

    if not df.empty:
        credits_df = df[df["Credit"] > 0].nlargest(15, "Credit")

        if not credits_df.empty:
            credit_data = [["Date", "Description", "Amount"]]
            for _, row in credits_df.iterrows():
                description = str(row["Description"])
                if len(description) > 45:
                    description = description[:45] + "..."
                credit_data.append([
                    row["Date"],
                    description,
                    f"R {row['Credit']:,.2f}",
                ])

            credit_table = Table(
                credit_data,
                colWidths=[1.1 * inch, 3.8 * inch, 1.1 * inch],
            )
            credit_table.setStyle(_green_table_style())
            elements.append(RoundedTable(credit_table))
        else:
            elements.append(Paragraph("No credit transactions found.", styles["Normal"]))

    # ── Top 15 Debits ─────────────────────────────────────────────────────
    elements.append(Paragraph("TOP 15 DEBITS", section_style))

    if not df.empty:
        debits_df = df[df["Debit"] > 0].nlargest(15, "Debit")

        if not debits_df.empty:
            debit_data = [["Date", "Description", "Amount"]]
            for _, row in debits_df.iterrows():
                description = str(row["Description"])
                if len(description) > 45:
                    description = description[:45] + "..."
                debit_data.append([
                    row["Date"],
                    description,
                    f"R {row['Debit']:,.2f}",
                ])

            debit_table = Table(
                debit_data,
                colWidths=[1.1 * inch, 3.8 * inch, 1.1 * inch],
            )
            debit_table.setStyle(_red_table_style())
            elements.append(RoundedTable(debit_table, border_color=RED_BORDER))
        else:
            elements.append(Paragraph("No debit transactions found.", styles["Normal"]))

    # ── Existing Facilities ───────────────────────────────────────────────
    elements.append(Paragraph("EXISTING FACILITIES DETECTED", section_style))

    facility_keywords = {
        "Retail Capital": "Retail Capital",
        "Pollen Finance": "pm8+PolFin",
        "Cashflow Capital": "pm8@cfc",
        "Business Fuel": "Bfuel",
        "Growise": "ingrowis",
        "GapAccess": "Batchdep",
        "Lulalend": "lulalend",
        "Fundrr": "Fundrr",
        "Bridgement": "Rebridgem",
        "Cash/Capital Connect": "cconnect",
        "Bizflex": "Bizflex",
        "Ikhokha": "Ikhoka advance",
        "Yoco": "Yoco Advance",
        "Vodalend": "INLLVODACO",
        "Merchant Capital": "Merch Cap MCA",
        "SWYPE": "SWYPE FS",
        "The Capital Partners": "THECP",
        "Tymebank": "Tymebank",
        "Genfin": "Genfin",
        "Flow48": "Elend 48",
    }

    if not df.empty:
        facility_summary = []
        for facility_name, keyword in facility_keywords.items():
            keyword_lower = keyword.lower()
            matched = df[
                df["Description"].astype(str).str.lower().str.contains(keyword_lower, na=False)
            ]
            if len(matched) > 0:
                facility_summary.append([facility_name, str(len(matched))])

        if facility_summary:
            facility_data = [["Facility", "Total Detections"]]
            facility_data.extend(facility_summary)

            facility_table = Table(facility_data, colWidths=[4 * inch, 2 * inch])
            facility_table.setStyle(_green_table_style())
            elements.append(RoundedTable(facility_table))
        else:
            elements.append(Paragraph("No existing facilities detected.", styles["Normal"]))

    # ── Service Penalties ─────────────────────────────────────────────────
    elements.append(Paragraph("SERVICE PENALTIES", section_style))

    penalty_keywords = ["Unpaid", "Excess item fees", "Honouring Fee"]

    if not df.empty:
        penalty_counts: dict[str, int] = {}
        for _, row in df.iterrows():
            desc_lower = str(row["Description"]).lower()
            for kw in penalty_keywords:
                if kw.lower() in desc_lower:
                    penalty_counts[kw] = penalty_counts.get(kw, 0) + 1
                    break

        if penalty_counts:
            penalty_data = [["Penalty Type", "Total Detections"]]
            for kw, count in penalty_counts.items():
                penalty_data.append([kw, str(count)])

            penalty_table = Table(penalty_data, colWidths=[4 * inch, 2 * inch])
            penalty_table.setStyle(_red_table_style())
            elements.append(RoundedTable(penalty_table, border_color=RED_BORDER))
        else:
            elements.append(Paragraph("No service penalties detected.", styles["Normal"]))

    doc.build(elements)
    buffer.seek(0)
    return buffer
