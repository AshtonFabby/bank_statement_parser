"""PDF report generation service."""

import io
from datetime import datetime

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .summary import Summary


def generate_summary_pdf(
    df: pd.DataFrame,
    summary: Summary,
    account_info: dict,
) -> io.BytesIO:
    """Generate a summary PDF report.

    Args:
        df: Transaction DataFrame
        summary: Summary object with totals
        account_info: Account information dictionary

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

    # Account Information Section
    elements.append(Paragraph("Account Information", styles["Heading2"]))
    elements.append(Spacer(1, 10))

    account_data = [
        ["Field", "Value"],
        ["Bank", account_info.get("bank") or "N/A"],
        ["Account Number", account_info.get("account_number") or "N/A"],
    ]

    account_table = Table(account_data, colWidths=[2.5 * inch, 2.5 * inch])
    account_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a5276")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#d4e6f1")),
            ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#5dade2")),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 10),
            ("TOPPADDING", (0, 1), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
        ])
    )
    elements.append(account_table)
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
