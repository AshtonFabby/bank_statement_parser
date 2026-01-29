"""PDF report generation service."""

import io
from datetime import datetime

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .summary import Summary, CoverageMetrics, ActivityVolume, RevenueMetrics


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
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )
    elements = []
    styles = getSampleStyleSheet()

    # Title
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=18,
        spaceAfter=20,
        alignment=1,
    )
    elements.append(Paragraph("Bank Statement Summary", title_style))
    elements.append(Spacer(1, 12))

    # Date
    date_style = ParagraphStyle("DateStyle", parent=styles["Normal"], alignment=1)
    elements.append(
        Paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            date_style,
        )
    )
    elements.append(Spacer(1, 20))

    # Financial Summary Section
    elements.append(Paragraph("Financial Summary", styles["Heading2"]))
    elements.append(Spacer(1, 10))

    summary_data = [
        ["Metric", "Value"],
        ["Total Debits", f"R{summary.total_debits:,.2f}"],
        ["Total Credits", f"R{summary.total_credits:,.2f}"],
        ["Net Movement", f"R{summary.net_movement:,.2f}"],
        ["Ending Balance", f"R{summary.ending_balance:,.2f}"],
        ["Transaction Count", str(summary.transaction_count)],
    ]

    summary_table = Table(summary_data, colWidths=[2.5 * inch, 2.5 * inch])
    summary_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ecf0f1")),
            ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#bdc3c7")),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 10),
            ("TOPPADDING", (0, 1), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
        ])
    )
    elements.append(summary_table)
    elements.append(Spacer(1, 30))

    # Coverage, Data Integrity, Structure Section
    if coverage:
        elements.append(Paragraph("Coverage, Data Integrity, Structure", styles["Heading2"]))
        elements.append(Spacer(1, 10))

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

        coverage_table = Table(coverage_data, colWidths=[2.5 * inch, 2.5 * inch])
        coverage_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ecf0f1")),
                ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#bdc3c7")),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("TOPPADDING", (0, 1), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
            ])
        )
        elements.append(coverage_table)
        elements.append(Spacer(1, 30))

    # Activity Volume Section
    if activity:
        elements.append(Paragraph("Activity Volume", styles["Heading2"]))
        elements.append(Spacer(1, 10))

        activity_data = [
            ["Metric", "Value"],
            ["Number of Credit Transactions", str(activity.credit_transaction_count)],
            ["Number of Debit Transactions", str(activity.debit_transaction_count)],
            ["Average Credits per Month", f"{activity.avg_credits_per_month:.2f}"],
            ["Average Debits per Month", f"{activity.avg_debits_per_month:.2f}"],
        ]

        activity_table = Table(activity_data, colWidths=[2.5 * inch, 2.5 * inch])
        activity_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ecf0f1")),
                ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#bdc3c7")),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("TOPPADDING", (0, 1), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
            ])
        )
        elements.append(activity_table)
        elements.append(Spacer(1, 30))

    # Revenue / Turnover Section
    if revenue:
        elements.append(Paragraph("Revenue / Turnover (Credits)", styles["Heading2"]))
        elements.append(Spacer(1, 10))

        revenue_data = [
            ["Metric", "Value"],
            ["Total Credits (Gross Inflow)", f"R{revenue.total_credits:,.2f}"],
            ["Average Monthly Credits", f"R{revenue.avg_monthly_credits:,.2f}"],
            ["Lowest Month Credits", f"R{revenue.lowest_month_credits:,.2f}"],
            ["Highest Month Credits", f"R{revenue.highest_month_credits:,.2f}"],
            ["Revenue Volatility %", f"{revenue.revenue_volatility_pct:.2f}%" if revenue.revenue_volatility_pct is not None else "N/A"],
            ["Top 5 Credit Concentration %", f"{revenue.top_5_credit_concentration_pct:.2f}%" if revenue.top_5_credit_concentration_pct is not None else "N/A"],
            ["Largest Single Credit", f"R{revenue.largest_single_credit:,.2f}"],
        ]

        revenue_table = Table(revenue_data, colWidths=[2.5 * inch, 2.5 * inch])
        revenue_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ecf0f1")),
                ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#bdc3c7")),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("TOPPADDING", (0, 1), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
            ])
        )
        elements.append(revenue_table)
        elements.append(Spacer(1, 30))

    # Transactions Section
    elements.append(Paragraph("Transactions", styles["Heading2"]))
    elements.append(Spacer(1, 10))

    if not df.empty:
        txn_data = [["Date", "Description", "Debit", "Credit", "Balance"]]
        for _, row in df.iterrows():
            description = str(row["Description"])
            if len(description) > 30:
                description = description[:30] + "..."

            txn_data.append([
                row["Date"],
                description,
                f"R{row['Debit']:,.2f}" if row["Debit"] > 0 else "-",
                f"R{row['Credit']:,.2f}" if row["Credit"] > 0 else "-",
                f"R{row['Balance']:,.2f}",
            ])

        txn_table = Table(
            txn_data,
            colWidths=[0.8 * inch, 2.2 * inch, 1 * inch, 1 * inch, 1 * inch],
        )
        txn_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("ALIGN", (1, 1), (1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f8f9fa")],
                ),
            ])
        )
        elements.append(txn_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer
