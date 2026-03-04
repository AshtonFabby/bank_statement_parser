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
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable

from .summary import ActivityVolume, CoverageMetrics, RevenueMetrics, Summary


def _format_date(val) -> str:
    """Format a date value for display in the PDF.

    Handles datetime objects, Timestamp, NaT, and string dates.
    Returns dd/mm/yyyy format or empty string for missing dates.
    """
    if pd.isna(val):
        return ""
    if isinstance(val, (pd.Timestamp, datetime)):
        return val.strftime("%d/%m/%Y")
    return str(val)

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

LOGO_URL = (
    "https://res.cloudinary.com/dbmvi2vd7/image/upload/v1738309402/getfunds-logo.png"
)
CORNER_RADIUS = 6

# Module-level logo cache (raw bytes)
_logo_bytes: bytes | None = None


class RoundedTable(Flowable):
    """Wraps a ReportLab Table and clips it to a rounded rectangle."""

    def __init__(
        self, table: Table, radius: int = CORNER_RADIUS, border_color=BORDER_COLOR
    ):
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
    return TableStyle(
        [
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
        ]
    )


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
        elements.append(
            Paragraph("COVERAGE, DATA INTEGRITY & STRUCTURE", section_style)
        )

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

        RISK_COLORS = {
            "LOW": colors.HexColor("#22c55e"),
            "MODERATE": colors.HexColor("#f59e0b"),
            "HIGH": colors.HexColor("#f97316"),
            "SEVERE": colors.HexColor("#ef4444"),
        }

        def _conc_risk(largest_pct: float, top3_pct: float):
            if largest_pct > 50:
                level = "SEVERE"
            elif largest_pct > 30:
                level = "HIGH"
            elif largest_pct > 15:
                level = "MODERATE"
            else:
                level = "LOW"
            flag = "*" if top3_pct > 60 else ""
            return level, f"{level}{flag} ({largest_pct:.0f}%)"

        monthly_data = [
            ["Month", "Total Turnover (Credits)", "Credits", "Concentration Risk"]
        ]
        risk_levels_for_rows = []
        top5_detail_rows = []  # flat rows for the compressed detail table

        for _, row in monthly_group.iterrows():
            month = row["Month"]
            total = float(row["Total_Credits"])

            month_credits_df = df_monthly[
                (df_monthly["Month"] == month) & (df_monthly["Credit"] > 0)
            ].nlargest(5, "Credit")

            largest = (
                float(month_credits_df["Credit"].iloc[0])
                if len(month_credits_df) > 0
                else 0.0
            )
            top3_sum = float(month_credits_df["Credit"].iloc[:3].sum())

            largest_pct = (largest / total * 100) if total > 0 else 0.0
            top3_pct = (top3_sum / total * 100) if total > 0 else 0.0

            level, label = _conc_risk(largest_pct, top3_pct)

            monthly_data.append(
                [
                    str(month),
                    f"R {total:,.2f}",
                    str(int(row["Credit_Count"])),
                    label,
                ]
            )
            risk_levels_for_rows.append(level)

            # Collect top 5 for detail table (month label only on first row)
            first = True
            for _, txn in month_credits_df.iterrows():
                desc = str(txn["Description"])
                if len(desc) > 33:
                    desc = desc[:33] + "..."
                pct = (float(txn["Credit"]) / total * 100) if total > 0 else 0.0
                top5_detail_rows.append(
                    [
                        str(month) if first else "",
                        desc,
                        f"R {txn['Credit']:,.2f}",
                        f"{pct:.1f}%",
                    ]
                )
                first = False

        # Monthly table style with per-row risk colouring
        monthly_commands = [
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
            ("TEXTCOLOR", (0, 1), (0, -1), colors.HexColor("#4a5568")),
        ]
        for i, level in enumerate(risk_levels_for_rows, start=1):
            c = RISK_COLORS.get(level, TEXT_DARK)
            monthly_commands.append(("TEXTCOLOR", (3, i), (3, i), c))
            monthly_commands.append(("FONTNAME", (3, i), (3, i), "Helvetica-Bold"))

        monthly_table = Table(
            monthly_data,
            colWidths=[1.4 * inch, 2.1 * inch, 0.7 * inch, 1.8 * inch],
        )
        monthly_table.setStyle(TableStyle(monthly_commands))
        elements.append(RoundedTable(monthly_table))

        # ── Top 5 credits per month (concentration risk detail) ───────────
        if top5_detail_rows:
            elements.append(Spacer(1, 4))
            small_label_style = ParagraphStyle(
                "SmallLabel",
                parent=styles["Normal"],
                fontSize=7.5,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor("#718096"),
                spaceBefore=0,
                spaceAfter=3,
            )
            elements.append(
                Paragraph(
                    "Top 5 credits per month — used for concentration risk calculation ",
                    small_label_style,
                )
            )

            detail_data = [["Month", "Description", "Amount", "% Rev"]]
            detail_data.extend(top5_detail_rows)

            detail_table = Table(
                detail_data,
                colWidths=[0.85 * inch, 3.0 * inch, 1.1 * inch, 0.75 * inch],
            )
            detail_commands = [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), GREEN_SECONDARY),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 7.5),
                ("TOPPADDING", (0, 0), (-1, 0), 5),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 7.5),
                ("TEXTCOLOR", (0, 1), (-1, -1), TEXT_DARK),
                ("TOPPADDING", (0, 1), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 1), (0, -1), colors.HexColor("#4a5568")),
            ]
            detail_table.setStyle(TableStyle(detail_commands))
            elements.append(RoundedTable(detail_table))

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
                credit_data.append(
                    [
                        _format_date(row["Date"]),
                        description,
                        f"R {row['Credit']:,.2f}",
                    ]
                )

            credit_table = Table(
                credit_data,
                colWidths=[1.1 * inch, 3.8 * inch, 1.1 * inch],
            )
            credit_table.setStyle(_green_table_style())
            elements.append(RoundedTable(credit_table))
        else:
            elements.append(
                Paragraph("No credit transactions found.", styles["Normal"])
            )

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
                debit_data.append(
                    [
                        _format_date(row["Date"]),
                        description,
                        f"R {row['Debit']:,.2f}",
                    ]
                )

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
        "Retail Capital": ["Retail Capital"],
        "Pollen Finance": ["Pollen Finance", "pm8+PolFin"],
        "Cashflow Capital": ["pm8@cfc"],
        "Business Fuel": ["Bfuel"],
        "Growise": ["ingrowis"],
        "GapAccess": ["Batchdep"],
        "Lulalend": ["lulalend"],
        "Fundrr": ["Fundrr"],
        "Bridgement": ["Rebridgem"],
        "Cash/Capital Connect": ["cconnect"],
        "Bizflex": ["Bizflex"],
        "Ikhokha": ["Ikhoka advance"],
        "Yoco": ["Yoco Advance"],
        "Vodalend": ["INLLVODACO"],
        "Merchant Capital": ["Merch Cap MCA"],
        "SWYPE": ["SWYPE FS"],
        "The Capital Partners": ["THECP"],
        "Tymebank": ["Tymebank"],
        "Genfin": ["Genfin"],
        "Flow48": ["Elend 48"],
    }

    if not df.empty:
        facility_summary = []
        for facility_name, keywords in facility_keywords.items():
            desc_lower = df["Description"].fillna("").astype(str).str.lower()
            matched = df[
                desc_lower.apply(
                    lambda d: any(kw.lower() in d for kw in keywords)
                )
            ]
            if len(matched) > 0:
                last_date = pd.to_datetime(matched["Date"], dayfirst=True).max()
                last_date_str = last_date.strftime("%Y-%m-%d") if pd.notna(last_date) else "N/A"
                facility_summary.append([facility_name, str(len(matched)), last_date_str])

        if facility_summary:
            facility_data = [["Facility", "Total Detections", "Last Payment Date"]]
            facility_data.extend(facility_summary)

            facility_table = Table(facility_data, colWidths=[3.5 * inch, 1.5 * inch, 1.5 * inch])
            facility_table.setStyle(_green_table_style())
            elements.append(RoundedTable(facility_table))
        else:
            elements.append(
                Paragraph("No existing facilities detected.", styles["Normal"])
            )

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
            elements.append(
                Paragraph("No service penalties detected.", styles["Normal"])
            )

    doc.build(elements)
    buffer.seek(0)
    return buffer
