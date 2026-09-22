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
