"""Standard Bank emits the same export in both date directions.

The running balance only reconciles forwards in time, so a newest-first
document scored near 0% before normalisation. These tests cover both the
pure ordering logic (no PDF needed) and the real statements.
"""

import io

import pandas as pd
import pytest

from parsers.standard_bank import StandardBankParser
from conftest import load_statement

COLS = ["Date", "Description", "Debit", "Credit", "Balance"]

ASCENDING = [
    ["01/01/2026", "Opening deposit", 0.0, 100.0, 1100.0],
    ["02/01/2026", "Payment", 50.0, 0.0, 1050.0],
    ["03/01/2026", "Payment", 25.0, 0.0, 1025.0],
]


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


def _normalise(rows):
    return StandardBankParser._normalise_chronological_order(_df(rows))


def test_descending_is_reversed_to_chronological():
    out = _normalise(list(reversed(ASCENDING)))
    assert list(out["Date"]) == ["01/01/2026", "02/01/2026", "03/01/2026"]
    assert list(out["Balance"]) == [1100.0, 1050.0, 1025.0]


def test_ascending_is_left_untouched():
    """The guard against over-correcting: most exports are already correct."""
    out = _normalise(ASCENDING)
    assert list(out["Date"]) == ["01/01/2026", "02/01/2026", "03/01/2026"]


def test_same_day_sequence_is_preserved_not_sorted():
    """Sorting by date would shuffle same-day rows and break the chain that
    reversing exists to fix. Reversing restores the true order because the
    source lists same-day transactions in reverse too."""
    descending = [
        ["02/01/2026", "third", 10.0, 0.0, 970.0],
        ["02/01/2026", "second", 10.0, 0.0, 980.0],
        ["02/01/2026", "first", 10.0, 0.0, 990.0],
        ["01/01/2026", "start", 0.0, 0.0, 1000.0],
    ]
    out = _normalise(descending)
    assert list(out["Description"]) == ["start", "first", "second", "third"]
    assert list(out["Balance"]) == [1000.0, 990.0, 980.0, 970.0]


def test_single_out_of_order_row_does_not_flip_the_document():
    """Adjacent-pair counting, not first-vs-last: one stray row must not
    reverse an otherwise ascending statement."""
    mostly_ascending = [
        ["01/01/2026", "a", 0.0, 0.0, 1000.0],
        ["05/01/2026", "b", 0.0, 0.0, 1000.0],
        ["03/01/2026", "out of order", 0.0, 0.0, 1000.0],
        ["07/01/2026", "c", 0.0, 0.0, 1000.0],
        ["09/01/2026", "d", 0.0, 0.0, 1000.0],
    ]
    out = _normalise(mostly_ascending)
    assert list(out["Description"]) == ["a", "b", "out of order", "c", "d"]


def test_opening_balance_stays_first_after_reversal():
    """Reversing would otherwise strand the anchor at the bottom, where
    verification cannot use it as the baseline."""
    descending_with_anchor = [
        ["03/01/2026", "later", 25.0, 0.0, 1025.0],
        ["02/01/2026", "earlier", 50.0, 0.0, 1050.0],
        ["", "Opening Balance", 0.0, 0.0, 1100.0],
    ]
    out = _normalise(descending_with_anchor)
    assert out.iloc[0]["Description"] == "Opening Balance"
    assert list(out["Description"]) == ["Opening Balance", "earlier", "later"]


def test_undated_rows_do_not_crash():
    out = _normalise([["", "no date", 0.0, 0.0, 1000.0]])
    assert len(out) == 1


def _resolver(period_line, default="2000"):
    """Build a business-statement year resolver without needing a real PDF."""
    parser = StandardBankParser(io.BytesIO(b""))
    return parser._statement_year_resolver(period_line, default_year=default)


def test_year_resolver_crosses_dec_jan_boundary():
    """Dec->Jan statements carry only MM DD per row, so December must keep the
    start year and January take the end year — not one 'to' year for both.

    Regression for the real bug: a Dec/Jan statement stamped every row 2026,
    pushing December 2025 to December 2026."""
    resolve = _resolver("Statement from 05 December 2025 to 03 January 2026")
    assert resolve(12) == "2025"
    assert resolve(1) == "2026"


def test_year_resolver_same_year_period_uses_that_year():
    resolve = _resolver("Statement from 05 July 2025 to 04 August 2025")
    assert resolve(7) == "2025"
    assert resolve(8) == "2025"


def test_year_resolver_falls_back_when_no_period_line():
    resolve = _resolver("no recognisable period here", default="2024")
    assert resolve(6) == "2024"


@pytest.mark.parametrize("filename", ["2. Dec.pdf", "6. TH.pdf"])
def test_newest_first_statements_now_reconcile(filename):
    """These scored 0.0% and 1.4% before normalisation."""
    from parsers import get_parser
    from services.verification import verify_and_correct

    parser = get_parser(load_statement(filename))
    account, df = parser.parse()
    _, result = verify_and_correct(df, filename, account.bank, "standard_bank")

    assert result.accuracy_percentage == pytest.approx(100.0)


def test_incomplete_statement_is_flagged_not_silently_accepted():
    """'1. 17 Jun - 11 Dec.pdf' claims to span June to December but contains
    no September transactions at all — the balance jumps 17,240.11 -> 23,474.57
    across the gap. Verification must surface that discontinuity rather than
    report a clean parse, which is exactly what a prevetting reviewer needs.
    """
    from parsers import get_parser
    from services.verification import verify_and_correct

    filename = "1. 17 Jun - 11 Dec.pdf"
    parser = get_parser(load_statement(filename))
    account, df = parser.parse()
    _, result = verify_and_correct(df, filename, account.bank, "standard_bank")

    assert result.failing_transactions >= 1
    assert result.accuracy_percentage < 100.0
