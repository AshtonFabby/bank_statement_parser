"""Business-account metadata and independent closing-balance checks.

Synthetic examples of the password-protected monthly business statements;
no customer data is needed for these regressions.
"""

import io

import pytest

from parsers.standard_bank import StandardBankParser
from services.verification import verify_and_correct


def statement(summary="Balance available at date of statement 120.00"):
    parser = StandardBankParser(io.BytesIO())
    parser._page_texts_cache = [
        "BUSINESS CURRENT ACCOUNT Account Number 12 345 678 9\n"
        "Statement from 19 December 2025 to 17 January 2026\n"
        "Month-end Balance R999.00\n"
        "Details Service Credits Date Balance\n"
        "Fee Debits\n"
        "BALANCE BROUGHT FORWARD 12 19 100.00\n"
        "CREDIT TRANSFER 9001 50.00 12 19 150.00\n"
        "CUSTOMER REFERENCE\n"
        "IB PAYMENT TO 8.90 30.00- 01 17 120.00\n"
        "SUPPLIER REFERENCE\n"
        "## These fees include VAT at 15%.\n",
        "Account Summary\nDetails of Agreement\n" + summary + "\n",
    ]
    return parser


def test_business_product_is_preserved_when_number_already_matched():
    account = statement().extract_account_info()
    assert account.account_number == "123456789"
    assert account.account_type == "BUSINESS CURRENT ACCOUNT"


def test_mymobiz_product_and_summary_are_preserved():
    parser = statement("Balance at date of statement 120.00")
    parser._page_texts_cache[0] = parser._page_texts_cache[0].replace("BUSINESS", "MYMOBIZ")
    account = parser.extract_account_info()
    assert account.account_type == "MYMOBIZ CURRENT ACCOUNT"
    assert account.declared_totals.closing_balance == 120


@pytest.mark.parametrize("footer", [
    "Please verify all transactions reflected on this statement",
    "Please visit our website at www.standardbank.co.za",
    "The Standard Bank of South Africa Limited",
    "## These fees include VAT at 15%.",
])
def test_page_footer_excluded_but_next_page_reference_preserved(footer):
    parser = statement()
    parser._page_texts_cache = [
        parser._page_texts_cache[0].split("SUPPLIER REFERENCE")[0]
        + footer + "\nLegal information\n",
        "Details Service Date Balance\nDebits Credits\nFee\n"
        "BALANCE BROUGHT FORWARD 120.00\n"
        "SUPPLIER REFERENCE\n"
        "PAYMENT 10.00- 01 17 110.00\n"
        "BENEFICIARY\n" + footer + "\nLegal information\n",
    ]
    df = parser.extract_transactions()
    assert df.Description.tolist()[-2:] == [
        "IB PAYMENT TO SUPPLIER REFERENCE", "PAYMENT BENEFICIARY",
    ]
    assert df.Balance.tolist() == [100, 150, 120, 110]


@pytest.mark.parametrize("label,amount,expected", [
    ("available", "1,234.56", 1234.56),
    ("outstanding", "-1,234.56", -1234.56),
    ("outstanding", "1,234.56-", -1234.56),
    ("available", "0.00", 0.0),
])
def test_summary_closing_balance_preserves_sign(label, amount, expected):
    account = statement(
        f"Balance {label} at date of statement {amount}"
    ).extract_account_info()
    assert account.declared_totals.closing_balance == expected


def test_month_end_balance_is_not_used_as_statement_closing_balance():
    account = statement(summary="VAT Summary\nTotal Vat 10.00").extract_account_info()
    assert account.declared_totals is None


def test_complete_statement_reconciles_with_independent_summary():
    account, df = statement().parse()
    assert df.Date.tolist() == ["19/12/2025", "19/12/2025", "17/01/2026"]
    assert df.Description.tolist() == [
        "Balance Brought Forward", "CREDIT TRANSFER 9001 CUSTOMER REFERENCE",
        "IB PAYMENT TO SUPPLIER REFERENCE",
    ]
    # The service-fee annotation is not an additional debit on this row.
    assert df.Debit.tolist() == [0, 0, 30]
    _, result = verify_and_correct(df, declared_totals=account.declared_totals)
    assert result.accuracy_percentage == 100.0
    assert result.unverified_transactions == 0
    assert result.corrections == 0
    assert result.declared_totals_match is True


def test_missing_final_transaction_is_detected_despite_balanced_rows():
    account, df = statement().parse()
    _, result = verify_and_correct(df.iloc[:-1], declared_totals=account.declared_totals)
    assert result.accuracy_percentage == 100.0
    assert result.declared_totals_match is False
    assert result.declared_totals_mismatches == [{
        "field": "closing_balance", "declared": 120.0,
        "parsed": 150.0, "difference": 30.0,
    }]
