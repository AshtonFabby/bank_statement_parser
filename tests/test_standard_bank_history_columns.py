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


@pytest.mark.parametrize("explicit_year_band", [False, True])
def test_right_aligned_history_with_separate_year_and_space_thousands(explicit_year_band):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(600, 800))

    def row(y, date, description, credit="", debit="", fee="", balance=""):
        pdf.drawString(25, y, date)
        pdf.drawString(85, y, description)
        for x, value in zip((345, 420, 485, 560), (credit, debit, fee, balance)):
            pdf.drawRightString(x, y, value)

    def header():
        pdf.setFont("Helvetica", 7)
        row(700, "Date", "Description", "In (R)", "Out (R)", "Bank fees (R)", "Balance (R)")

    header()
    pdf.drawString(25, 780, "www.standardbank.co.za Transactions")
    pdf.drawString(25, 760, "Account: Current Acc 12-345-678-9")
    pdf.drawString(25, 740, "Transaction date range: 31 December 2025 - 2 January 2026")
    row(680, "2025", "")
    long_reference = "EXAMPLE CUSTOMER - electronic banking payment reference"
    row(660, "31 Dec", long_reference, "+ 30 680.29", balance="-70 677.22")
    row(640, "31 Dec", long_reference, debit="- 1 020.00", fee="9.30", balance="-71 697.22")
    pdf.showPage()
    header()
    if explicit_year_band:
        row(680, "2026", "")
    row(660, "1 Jan", "DEPOSIT", "+ 100 000.00", balance="28 302.78")
    row(640, "2 Jan", "POSTED FEE", debit="- 9.30", balance="28 293.48")
    pdf.drawString(25, 40, "# These fees are zero rated")
    pdf.save()
    buffer.seek(0)

    account, df = get_parser(buffer).parse()
    assert account.account_number == "123456789"
    assert df.Date.tolist() == ["31/12/2025", "31/12/2025", "01/01/2026", "02/01/2026"]
    assert df.Description.tolist() == [long_reference, long_reference, "DEPOSIT", "POSTED FEE"]
    assert df.Credit.tolist() == [30680.29, 0, 100000, 0]
    assert df.Debit.tolist() == [0, 1020, 0, 9.3]
    assert df.Balance.tolist() == [-70677.22, -71697.22, 28302.78, 28293.48]
    assert (df.Balance.diff() - df.Credit + df.Debit).iloc[1:].abs().max() < .01
    assert df.attrs["extraction_audit"] == dict(
        source_transactions=4, extracted_transactions=4, validated_transactions=4,
    )
