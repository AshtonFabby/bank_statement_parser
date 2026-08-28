"""Regression coverage for the multiline HBZweb statement export."""

import pytest

from conftest import find_statement


STATEMENT = "hPLUSWeb-Statement-Feb 01 2026-Jul 31 2026.pdf"


def test_hbzweb_statement_parses_and_reconciles():
    path = find_statement(STATEMENT)
    if path is None:
        pytest.skip(f"statement not available: {STATEMENT}")

    from parsers import detect_bank, get_parser_by_id
    from services.verification import verify_and_correct

    assert detect_bank(str(path)) == "hbz_bank"
    with open(path, "rb") as pdf_file:
        parser = get_parser_by_id("hbz_bank", pdf_file)
        account, transactions = parser.parse()

    assert account.account_number == "04-01-07-20311-901-265691"
    assert transactions.iloc[0]["Description"] == "Previous Balance"
    assert transactions.iloc[0]["Balance"] == pytest.approx(28800.99)
    assert transactions.iloc[-1]["Balance"] == pytest.approx(6349.82)

    _, result = verify_and_correct(
        transactions, str(path), account.bank, "hbz_bank"
    )
    assert result.accuracy_percentage == pytest.approx(100.0)
    assert result.unverified_transactions == 0
