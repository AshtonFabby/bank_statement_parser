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

    # Monthly Turnover Section
    elements.append(Paragraph("Monthly Turnover", styles["Heading2"]))
    elements.append(Spacer(1, 10))

    if not df.empty:
        dates = pd.to_datetime(df["Date"], dayfirst=True)
        df_monthly = df.copy()
        df_monthly["Month"] = dates.dt.to_period("M")
        monthly_group = df_monthly.groupby("Month").agg(
            Total_Credits=("Credit", "sum"),
            Credit_Count=("Credit", lambda x: (x > 0).sum()),
        ).reset_index()
        monthly_group = monthly_group.sort_values("Month")

        monthly_data = [["Month", "Total Turnover (Credits)", "Number of Credits"]]
        for _, row in monthly_group.iterrows():
            monthly_data.append([
                str(row["Month"]),
                f"R{row['Total_Credits']:,.2f}",
                str(int(row["Credit_Count"])),
            ])

        monthly_table = Table(monthly_data, colWidths=[1.5 * inch, 2 * inch, 2 * inch])
        monthly_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a6e8e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ecf0f1")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("TOPPADDING", (0, 1), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#d6eaf8")],
                ),
            ])
        )
        elements.append(monthly_table)
    elements.append(Spacer(1, 30))

    # Top 15 Credits Section
    elements.append(Paragraph("Top 15 Credits", styles["Heading2"]))
    elements.append(Spacer(1, 10))

    if not df.empty:
        # Get top 15 credits sorted by amount descending
        credits_df = df[df["Credit"] > 0].nlargest(15, "Credit")

        if not credits_df.empty:
            credit_data = [["Date", "Description", "Amount"]]
            for _, row in credits_df.iterrows():
                description = str(row["Description"])
                if len(description) > 40:
                    description = description[:40] + "..."
                credit_data.append([
                    row["Date"],
                    description,
                    f"R{row['Credit']:,.2f}",
                ])

            credit_table = Table(
                credit_data,
                colWidths=[1 * inch, 3.5 * inch, 1.5 * inch],
            )
            credit_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#27ae60")),
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
                        [colors.white, colors.HexColor("#e8f5e9")],
                    ),
                ])
            )
            elements.append(credit_table)
        else:
            elements.append(Paragraph("No credit transactions found.", styles["Normal"]))

    elements.append(Spacer(1, 30))

    # Top 15 Debits Section
    elements.append(Paragraph("Top 15 Debits", styles["Heading2"]))
    elements.append(Spacer(1, 10))

    if not df.empty:
        # Get top 15 debits sorted by amount descending
        debits_df = df[df["Debit"] > 0].nlargest(15, "Debit")

        if not debits_df.empty:
            debit_data = [["Date", "Description", "Amount"]]
            for _, row in debits_df.iterrows():
                description = str(row["Description"])
                if len(description) > 40:
                    description = description[:40] + "..."
                debit_data.append([
                    row["Date"],
                    description,
                    f"R{row['Debit']:,.2f}",
                ])

            debit_table = Table(
                debit_data,
                colWidths=[1 * inch, 3.5 * inch, 1.5 * inch],
            )
            debit_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c0392b")),
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
                        [colors.white, colors.HexColor("#ffebee")],
                    ),
                ])
            )
            elements.append(debit_table)
        else:
            elements.append(Paragraph("No debit transactions found.", styles["Normal"]))

    # Existing Facilities Detection Section
    elements.append(Spacer(1, 30))
    elements.append(Paragraph("Existing Facilities Detected", styles["Heading2"]))
    elements.append(Spacer(1, 10))

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
            count = len(matched)
            if count > 0:
                facility_summary.append([facility_name, str(count)])

        if facility_summary:
            facility_data = [["Facility", "Total Detections"]]
            facility_data.extend(facility_summary)

            facility_table = Table(
                facility_data,
                colWidths=[3 * inch, 2 * inch],
            )
            facility_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#8e44ad")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("ALIGN", (0, 1), (0, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ("TOPPADDING", (0, 1), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f5eef8")],
                    ),
                ])
            )
            elements.append(facility_table)
        else:
            elements.append(
                Paragraph("No existing facilities detected.", styles["Normal"])
            )

    # Service Penalties Section
    elements.append(Spacer(1, 30))
    elements.append(Paragraph("Service Penalties", styles["Heading2"]))
    elements.append(Spacer(1, 10))

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

            penalty_table = Table(
                penalty_data,
                colWidths=[3 * inch, 2 * inch],
            )
            penalty_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e74c3c")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("ALIGN", (0, 1), (0, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ("TOPPADDING", (0, 1), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#fdedec")],
                    ),
                ])
            )
            elements.append(penalty_table)
        else:
            elements.append(
                Paragraph("No service penalties detected.", styles["Normal"])
            )

    doc.build(elements)
    buffer.seek(0)
    return buffer
