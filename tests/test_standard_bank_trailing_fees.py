"""Column layout regressions, using synthetic statements without customer data."""

import io

import pytest
from reportlab.pdfgen import canvas

from parsers.standard_bank import StandardBankParser


def statement():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(600, 800))

    def text(x, y, value):
        pdf.drawString(x, y, value)

    def header():
        pdf.setFont("Helvetica", 8)
        text(20, 780, "STANDARD BANK 6 month statement")
        text(20, 760, "Account number: 22 123 456 7")
        text(420, 760, "27 EXAMPLE STREET")
        text(20, 745, "Product name: BUSINESS CURRENT ACCOUNT")
        text(420, 745, "EXAMPLE SUBURB")
        for x, label in zip((20, 95), ("Date", "Description")):
            text(x, 700, label)
        for x, label in zip((350, 425, 500, 580), ("Payments", "Deposits", "Bank fees", "Balance")):
            pdf.drawRightString(x, 700, label)

    def row(y, date, description, debit, credit, fee, balance):
        for x, value in zip((20, 95), (date, description)):
            text(x, y, value)
        for x, value in zip((350, 425, 500, 580), (debit, credit, fee, balance)):
            pdf.drawRightString(x, y, value)

    header()
    row(680, "", "STATEMENT OPENING BALANCE", "", "", "", "-1,000.00")
    row(660, "24 Mar 26", "SHOP", "- 100.00", "", "4.90", "-1,100.00")
    text(95, 650, "CHEQUE CARD PURCHASE")
    row(630, "24 Mar 26", "REF R79.65", "- 79.65", "", "", "-1,179.65")
    text(95, 620, "BANK FEE")
    text(20, 40, "The Standard Bank of South Africa Limited")
    pdf.showPage()
    header()
    row(680, "25 Mar 26", "DEPOSIT", "", "200.00", "4.90", "-979.65")
    row(660, "25 Mar 26", "SEPARATE FEE", "- 4.90", "", "", "-984.55")
    text(20, 640, "Please verify all transactions reflected on this statement")
    text(95, 620, "Statement Summary")
    text(95, 600, "Today's debits have not yet been paid")
    pdf.save()
    buffer.seek(0)
    return buffer


def test_trailing_fees_and_description_amounts_do_not_become_transactions():
    parser = StandardBankParser(statement())
    account, df = parser.parse()
    assert parser._detect_format() == "regular_trailing_fees"
    assert account.account_number == "221234567"
    assert account.account_type == "BUSINESS CURRENT ACCOUNT"
    assert len(df) == 5
    assert df.Debit.tolist() == [0, 100, 79.65, 0, 4.9]
    assert df.Credit.tolist() == [0, 0, 0, 200, 0]
    assert df.Description.tolist() == [
        "Statement Opening Balance", "SHOP CHEQUE CARD PURCHASE",
        "REF R79.65 BANK FEE", "DEPOSIT", "SEPARATE FEE",
    ]
    assert (df.Balance.diff() - df.Credit + df.Debit).iloc[1:].abs().max() < .01


@pytest.mark.parametrize("columns,expected", [
    ("Fees Payments Deposits Balance", "regular_with_fees"),
    ("Payments Deposits Balance", "regular"),
])
def test_existing_regular_layout_routing_is_preserved(columns, expected):
    parser = StandardBankParser(io.BytesIO())
    parser._page_texts_cache = ["Date Description " + columns]
    assert parser._detect_format() == expected


def test_six_month_statement_without_repeated_table_headings():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(600, 800))

    def header():
        pdf.setFont("Helvetica", 8)
        pdf.drawString(20, 780, "STANDARD BANK 6 month statement")
        pdf.drawString(20, 760, "Account number: 22 123 456 7")
        pdf.drawString(420, 760, "27 EXAMPLE STREET")
        pdf.drawString(20, 745, "Product name: BIZLAUNCH")
        pdf.drawString(420, 745, "EXAMPLE SUBURB")
        pdf.drawString(20, 720, "26 Oct 2026")

    def row(y, date, description, debit, credit, balance):
        pdf.drawString(20, y, date)
        pdf.drawString(95, y, description)
        for x, value in zip((380, 470, 580), (debit, credit, balance)):
            pdf.drawRightString(x, y, value)

    header()
    row(700, "Date", "Description", "Payments", "Deposits", "Balance")
    row(680, "", "STATEMENT OPENING BALANCE", "", "", "100.00")
    row(660, "24 Oct 26", "SHOP R10.00", "- 25.00", "", "125.00")
    pdf.drawString(95, 648, "CHEQUE CARD PURCHASE")
    pdf.drawString(20, 40, "The Standard Bank of South Africa Limited")
    pdf.showPage()
    header()
    # No table heading on continuation pages. Positive deposits must remain
    # credits even when a descending export's balance decreases on the page.
    row(680, "23 Oct 26", "RECEIPT", "", "50.00", "150.00")
    pdf.drawString(95, 668, "ELECTRONIC BANKING PAYMENT FR")
    pdf.drawString(20, 640, "Please verify all transactions")
    pdf.drawString(95, 620, "Statement Summary")
    pdf.drawString(95, 600, "Today's debits have not yet been paid")
    pdf.save()
    buffer.seek(0)

    account, df = StandardBankParser(buffer).parse()
    assert account.account_type == "BIZLAUNCH"
    assert account.account_number == "221234567"
    assert df.Description.tolist() == [
        "Statement Opening Balance", "RECEIPT ELECTRONIC BANKING PAYMENT FR",
        "SHOP R10.00 CHEQUE CARD PURCHASE",
    ]
    assert df.Date.tolist() == ["", "23/10/2026", "24/10/2026"]
    assert df.Debit.tolist() == [0, 0, 25]
    assert df.Credit.tolist() == [0, 50, 0]
    assert df.Balance.tolist() == [100, 150, 125]
    assert df.attrs["extraction_audit"] == dict(
        source_transactions=2, extracted_transactions=2, validated_transactions=2,
    )


def test_regular_statement_summary_is_an_independent_amount_check():
    parser = StandardBankParser(io.BytesIO())
    parser._page_texts_cache = [
        "Statement Summary\nPayments - R 1,250.00\nDeposits R 1,500.00\n"
        "Today's debits have not yet been paid"
    ]
    totals = parser._extract_regular_declared_totals()
    assert totals.debit_total == 1250
    assert totals.credit_total == 1500
    assert totals.closing_balance is None
