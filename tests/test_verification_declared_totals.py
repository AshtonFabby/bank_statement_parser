"""Tests for the declared-totals cross-check and the unverified-row counter.

These build DataFrames by hand, so they need no statement corpus.
"""

import pandas as pd
import pytest

from parsers.base import DeclaredTotals
from services.verification import verify_and_correct


def _df(rows):
    return pd.DataFrame(rows, columns=["Date", "Description", "Debit", "Credit", "Balance"])


@pytest.fixture
def clean():
    """Opening 100, +50 credit, -30 debit, closing 120."""
    return _df([
        ["", "Opening Balance", 0.0, 0.0, 100.0],
        ["01/01/2026", "Deposit", 0.0, 50.0, 150.0],
        ["02/01/2026", "Payment", 30.0, 0.0, 120.0],
    ])


@pytest.fixture
def declared():
    return DeclaredTotals(
        opening_balance=100.0, closing_balance=120.0,
        credit_count=1, credit_total=50.0,
        debit_count=1, debit_total=30.0,
    )


def test_matching_parse_reports_match(clean, declared):
    _, result = verify_and_correct(clean, declared_totals=declared)
    assert result.declared_totals_match is True
    assert result.declared_totals_mismatches == []


def test_no_declared_totals_leaves_check_undetermined(clean):
    """Banks that print no control totals must not be reported as mismatching."""
    _, result = verify_and_correct(clean)
    assert result.declared_totals_match is None
    assert result.declared_totals_mismatches == []


def test_dropped_last_row_is_caught_despite_consistent_chain(clean, declared):
    """The running-balance check cannot see this: every remaining row still
    follows from the one before it, so accuracy stays 100%."""
    truncated = clean.iloc[:-1].copy()
    _, result = verify_and_correct(truncated, declared_totals=declared)

    assert result.accuracy_percentage == 100.0
    assert result.failing_transactions == 0
    assert result.declared_totals_match is False

    fields = {m["field"] for m in result.declared_totals_mismatches}
    assert fields == {"debit_count", "debit_total", "closing_balance"}


def test_mismatch_reports_declared_and_parsed_values(clean, declared):
    _, result = verify_and_correct(clean.iloc[:-1].copy(), declared_totals=declared)
    by_field = {m["field"]: m for m in result.declared_totals_mismatches}

    assert by_field["debit_count"]["declared"] == 1
    assert by_field["debit_count"]["parsed"] == 0
    assert by_field["closing_balance"]["declared"] == 120.0
    assert by_field["closing_balance"]["parsed"] == 150.0


def test_opening_balance_row_excluded_from_declared_counts(clean, declared):
    """The synthetic anchor is not a transaction and must not be counted."""
    _, result = verify_and_correct(clean, declared_totals=declared)
    assert result.declared_totals_match is True


def test_mismatches_are_json_serializable(clean, declared):
    """to_dict() feeds verification.json and /parse/json; numpy scalars raise."""
    import json

    _, result = verify_and_correct(clean.iloc[:-1].copy(), declared_totals=declared)
    json.dumps(result.to_dict())


def test_missing_opening_row_is_counted_as_unverified(declared):
    """Without an anchor the first row is taken on trust. Accuracy reports
    100% on the rows it did check, so the skip has to surface separately."""
    no_anchor = _df([
        ["01/01/2026", "Deposit", 0.0, 50.0, 150.0],
        ["02/01/2026", "Payment", 30.0, 0.0, 120.0],
    ])
    _, result = verify_and_correct(no_anchor)

    assert result.unverified_transactions == 1
    assert result.verified_transactions == 1
    assert result.accuracy_percentage == 100.0
    assert result.opening_balance is None


def test_anchored_statement_has_no_unverified_rows(clean):
    _, result = verify_and_correct(clean)
    assert result.unverified_transactions == 0
    assert result.verified_transactions == 2
    assert result.opening_balance == 100.0
