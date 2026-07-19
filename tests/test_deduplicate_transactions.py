"""Tests for cross-document transaction de-duplication.

Two opposing requirements meet here: a statement and a transaction history
covering the same dates must not double-count, but a transaction that
genuinely happened twice on one day must not be collapsed into one.
"""

import pandas as pd

from main import _deduplicate_transactions

COLS = ["Date", "Description", "Debit", "Credit", "Balance", "Source"]


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


def test_identical_repeats_within_one_document_are_kept():
    """Two real R1,392.57 collections on the same day - the balance column
    proves both happened."""
    df = _df([
        ["02/01/2026", "B2B Collection 7772", 1392.57, 0.0, 35248.99, "FNB"],
        ["02/01/2026", "B2B Collection 7772", 1392.57, 0.0, 33856.42, "FNB"],
    ])
    assert len(_deduplicate_transactions(df)) == 2


def test_overlap_between_two_documents_is_deduplicated():
    """Same transaction in a statement and a transaction history."""
    df = _df([
        ["02/01/2026", "Payment ABC", 100.0, 0.0, 900.0, "statement.pdf"],
        ["02/01/2026", "Payment ABC", 100.0, 0.0, 901.0, "history.pdf"],
    ])
    out = _deduplicate_transactions(df)
    assert len(out) == 1
    assert out.iloc[0]["Source"] == "statement.pdf"


def test_repeats_present_in_both_documents_survive_once_each():
    """Two real repeats overlapping across documents collapse 4 rows to 2,
    not to 1."""
    rows = [
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 35248.99, "statement.pdf"],
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 33856.42, "statement.pdf"],
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 35248.99, "history.pdf"],
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 33856.42, "history.pdf"],
    ]
    out = _deduplicate_transactions(_df(rows))
    assert len(out) == 2
    assert set(out["Source"]) == {"statement.pdf"}


def test_document_with_more_repeats_contributes_the_extra():
    """The history saw a third collection the statement missed; it survives."""
    rows = [
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 35248.99, "statement.pdf"],
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 33856.42, "statement.pdf"],
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 35248.99, "history.pdf"],
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 33856.42, "history.pdf"],
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 32463.85, "history.pdf"],
    ]
    out = _deduplicate_transactions(_df(rows))
    assert len(out) == 3


def test_distinct_transactions_are_untouched():
    df = _df([
        ["02/01/2026", "Payment ABC", 100.0, 0.0, 900.0, "FNB"],
        ["03/01/2026", "Payment XYZ", 50.0, 0.0, 850.0, "FNB"],
    ])
    assert len(_deduplicate_transactions(df)) == 2


def test_works_without_a_source_column():
    """A single document: every row is a real event, nothing is dropped."""
    df = _df([
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 35248.99, "FNB"],
        ["02/01/2026", "B2B Collection", 1392.57, 0.0, 33856.42, "FNB"],
    ]).drop(columns="Source")
    assert len(_deduplicate_transactions(df)) == 2


def test_does_not_mutate_the_input_frame():
    df = _df([
        ["02/01/2026", "Payment ABC", 100.0, 0.0, 900.0, "statement.pdf"],
        ["02/01/2026", "Payment ABC", 100.0, 0.0, 901.0, "history.pdf"],
    ])
    before = df.copy()
    _deduplicate_transactions(df)
    pd.testing.assert_frame_equal(df, before)
