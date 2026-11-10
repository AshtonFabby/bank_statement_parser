"""Description-column history exports with variable-precision balances."""

import io

import pytest
from reportlab.pdfgen import canvas

from parsers import get_parser
from conftest import load_statement


def history_pdf():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(600, 800))

    def header():
        pdf.setFont("Helvetica", 7)
        pdf.drawString(25, 780, "www.standardbank.co.za Transaction History Statement")
        pdf.drawString(25, 760, "Account number: 12 345 678 9")
        pdf.drawString(25, 745, "Product name: BUSINESS CURRENT ACCOUNT")
        pdf.drawString(25, 730, "Available balance: R -1050")
        row(700, "Date", "Description", "In (R)", "Out (R)", "Bank fees (R)", "Balance (R)")

    def row(y, *cells):
        for x, value in zip((25, 95, 295, 365, 435, 505), cells):
            pdf.drawString(x, y, value)

    header()
    row(680, "01 Jun 2026", "PAYMENT REF 123 R500.00", "", "-1,000.00", "-9.3", "-2000")
    row(668, "", "a long continuation with several words", "", "", "", "")
    row(650, "01 Jun 2026", "RECEIPT", "+1,000.00", "", "-20", "-1000.0")
    pdf.drawString(25, 40, "# These fees are zero rated")
    pdf.showPage()
    header()
    row(680, "10 Nov 2026", "SEPARATELY POSTED FEE", "", "-50.00", "", "-1050.00")
    pdf.drawString(25, 40, "# These fees are zero rated")
    pdf.save()
    buffer.seek(0)
    return buffer


def test_history_columns_keep_amounts_descriptions_and_posted_fees():
    parser = get_parser(history_pdf())
    account, df = parser.parse()
    assert account.account_number == "123456789"
    assert account.account_type == "BUSINESS CURRENT ACCOUNT"
    assert df.Date.tolist() == ["01/06/2026", "01/06/2026", "10/11/2026"]
    assert df.Debit.tolist() == [1000, 0, 50]
    assert df.Credit.tolist() == [0, 1000, 0]
    assert df.Balance.tolist() == [-2000, -1000, -1050]
    assert df.Description.tolist() == [
        "PAYMENT REF 123 R500.00 a long continuation with several words",
        "RECEIPT", "SEPARATELY POSTED FEE",
    ]
    assert df.attrs["extraction_audit"] == dict(
        source_transactions=3, extracted_transactions=3, validated_transactions=3,
    )


def test_june_november_history_statement():
    parser = get_parser(load_statement("1 Jun - 10 Nov.pdf"))
    _, df = parser.parse()
    assert len(df) == 568
    assert df.Date.iloc[0] == "01/06/2026"
    assert df.Date.iloc[-1] == "10/11/2026"
    assert df.Balance.iloc[-1] == pytest.approx(-233357.55)
    assert (df.Balance.diff() - df.Credit + df.Debit).iloc[1:].abs().max() < .01
