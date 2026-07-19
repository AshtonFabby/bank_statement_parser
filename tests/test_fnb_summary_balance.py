"""Unit tests for the Statement Balances summary-box balance reader.

No PDF needed - these run everywhere, unlike the corpus-backed tests.
"""

import io

import pytest

from parsers.fnb import FNBParser


@pytest.fixture
def parser():
    return FNBParser(io.BytesIO(b""))


def _words(*pairs):
    """Build (x0, text) pairs into pdfplumber-shaped word dicts."""
    return [{"x0": x, "text": t} for x, t in pairs]


def test_reads_balance_not_the_adjacent_service_fee(parser):
    """pdfplumber yields these words in content-stream order, which
    interleaves the summary box's columns - the service fee (167.00Dr) is
    emitted *before* the balance it must not be confused with.
    """
    row = _words(
        (434.2, "Credit"), (457.0, "Rate**"), (558.2, "Tiered"),
        (226.6, "Service"), (254.2, "Fees"), (394.1, "167.00Dr"),
        (19.7, "Opening"), (51.1, "Balance"), (171.1, "171,855.49Cr"),
    )
    assert parser._parse_summary_balance(row) == pytest.approx(171855.49)


def test_debit_balance_is_negative(parser):
    row = _words((19.7, "Opening"), (51.1, "Balance"), (171.1, "1,234.56Dr"))
    assert parser._parse_summary_balance(row) == pytest.approx(-1234.56)


def test_unsuffixed_balance_is_treated_as_debit(parser):
    row = _words((19.7, "Opening"), (51.1, "Balance"), (171.1, "1,234.56"))
    assert parser._parse_summary_balance(row) == pytest.approx(-1234.56)


def test_afrikaans_credit_suffix(parser):
    row = _words((19.7, "Openingsaldo"), (171.1, "1,234.56Kt"))
    assert parser._parse_summary_balance(row) == pytest.approx(1234.56)


def test_header_row_without_amounts_yields_none(parser):
    """'Statement Balances' matches the opening-balance label check but
    carries no figure; it must not produce an anchor."""
    row = _words(
        (301.2, "Bank"), (322.3, "Charges"), (483.4, "Interest"),
        (513.8, "Rate"), (82.8, "Statement"), (122.9, "Balances"),
    )
    assert parser._parse_summary_balance(row) is None
