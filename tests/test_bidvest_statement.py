"""Bidvest exports with image branding and space-grouped amounts."""

import io

import pymupdf
import pytest

from parsers import detect_bank
from parsers.bidvest import BidvestParser
from services.verification import verify_and_correct


@pytest.mark.parametrize("label", ["Branch Code:462005", "Branch Code: 462005", "Branch Code : 462005"])
def test_image_branded_statement_detection(label):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), "Business Account Interim Statement\nTransaction Effective Date\n" + label)
        assert detect_bank(pdf.tobytes()) == "bidvest"


def test_numeric_reference_and_negative_balances_reconcile():
    parser = BidvestParser(io.BytesIO())
    parser._page_texts_cache = [
        "Account Statement: Business Account Account No: 12345678901 Date: 2026/08/31\n"
        "Balance Brought Foward: - 14.97 Overdraft Limit: 0.00\n"
        "Closing Balance: 159 935.03 Prevailing Bank Rate: 10.50\n",
        "Transaction Effective\nTransaction Description Reference Amount Balance\n"
        "2026/08/01 2026/08/01 Balance Brought Forward 0.00 - 14.97\n"
        "2026/08/04 2026/08/03 Immediate Deposit 12345678901 160 000.00 159 985.03\n"
        "Page 2 of 3\n",
        "2026/08/31 2026/08/31 Administration Fee - 50.00 159 935.03\n"
        "VAT Number: 4680190875\n",
    ]
    account, rows = parser.parse()
    assert account.account_number == "12345678901"
    assert account.account_type == "Business Account"
    assert len(rows) == 3
    assert rows.iloc[1]["Description"] == "Immediate Deposit 12345678901"
    assert rows.iloc[1]["Credit"] == 160000
    assert rows.iloc[1]["Date"] == "04/08/2026"
    _, result = verify_and_correct(rows, declared_totals=account.declared_totals)
    assert result.accuracy_percentage == 100
    assert result.corrections == 0
    assert result.unverified_transactions == 0
    assert result.declared_totals_match is True


def test_interim_account_type():
    parser = BidvestParser(io.BytesIO())
    parser._page_texts_cache = [
        "Business Account Interim Statement Account No: 12345678901 Date: 2026/10/21"
    ]
    account = parser.extract_account_info()
    assert account.account_type == "Business Account"
    assert account.declared_totals is None


def test_history_keeps_separate_fees():
    parser = BidvestParser(io.BytesIO())
    parser._page_texts_cache = [
        "Transaction History\n"
        "2026/08/01 Balance Brought Forward 0.00 2,000.00\n"
        "2026/08/02 2026/08/02 Payment 12345678901 5.00 -1,000.00 995.00"
    ]
    rows = parser.extract_transactions()
    assert rows.iloc[1]["Description"] == "Payment 12345678901"
    assert rows.iloc[1]["Debit"] == 1005
    assert rows.iloc[1]["Balance"] == 995
