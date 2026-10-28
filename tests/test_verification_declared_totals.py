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


def test_matching_amount_totals_prevent_false_swaps_from_bad_balances():
    df = _df([
        ["", "Opening Balance", 0, 0, 100],
        ["01/01/2026", "Payment", 50, 0, 150],
        ["02/01/2026", "Deposit", 0, 20, 130],
    ])
    corrected, result = verify_and_correct(
        df, declared_totals=DeclaredTotals(debit_total=50, credit_total=20),
    )
    assert corrected.Debit.tolist() == [0, 50, 0]
    assert corrected.Credit.tolist() == [0, 0, 20]
    assert result.corrections == 0
    assert result.failing_transactions == 2
    assert result.declared_totals_match is True


def test_unconfirmed_totals_still_allow_classification_correction():
    df = _df([
        ["", "Opening Balance", 0, 0, 100],
        ["01/01/2026", "Deposit", 50, 0, 150],
    ])
    corrected, result = verify_and_correct(
        df, declared_totals=DeclaredTotals(debit_total=0, credit_total=50),
    )
    assert corrected.Credit.tolist() == [0, 50]
    assert result.corrections == 1
    assert result.declared_totals_match is True


@pytest.mark.parametrize("source,validated,unreadable,expected", [
    (2, 2, 0, 100), (3, 2, 0, 66.67), (2, 1, 0, 50), (2, 2, 1, 50),
])
def test_extraction_score_is_independent_of_balance_errors(source, validated, unreadable, expected):
    df = _df([
        ["", "Opening Balance", 0, 0, 100],
        ["01/01/2026", "Payment", 50, 0, 150],
        ["02/01/2026", "Deposit", 0, 20, 130],
    ])
    df.attrs["extraction_audit"] = dict(
        source_transactions=source, extracted_transactions=2, validated_transactions=validated,
    )
    _, result = verify_and_correct(df, declared_totals=DeclaredTotals(debit_total=50, credit_total=20),
                                   unreadable_descriptions=unreadable)
    assert result.parsing_accuracy_percentage == expected
    assert result.parsing_checked == source
    assert result.parsing_failed == source - max(0, validated - unreadable)
    assert result.parsing_severe == max(0, source - 2)
    assert result.to_dict()["balance_accuracy_percentage"] == 0
    assert result.failing_transactions == 2


def test_totals_alone_do_not_establish_extraction_accuracy(clean, declared):
    _, result = verify_and_correct(clean, declared_totals=declared)
    assert result.parsing_accuracy_percentage is None


def test_stale_extraction_audit_is_not_accepted(clean):
    clean.attrs["extraction_audit"] = dict(
        source_transactions=3, extracted_transactions=3, validated_transactions=3,
    )
    _, result = verify_and_correct(clean)
    assert result.parsing_accuracy_percentage is None
