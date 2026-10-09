"""Tests for cross-document transaction de-duplication.

Two opposing requirements meet here: a statement and a transaction history
covering the same dates must not double-count, but a transaction that
genuinely happened twice on one day must not be collapsed into one.
"""

import pandas as pd

from main import _account_source, _build_parse_json_sync, _deduplicate_transactions

COLS = ["Date", "Description", "Debit", "Credit", "Balance", "Source"]


def test_standard_bank_padded_history_number_groups_with_monthly_statement():
    assert _account_source("Standard Bank", "0000012345678901") == _account_source(
        "Standard Bank", "12345678901"
    )
    assert _account_source("Standard Bank", "12345678902") != _account_source(
        "Standard Bank", "12345678901"
    )
    assert _account_source("FNB", "00123456789") == "FNB (00123456789)"


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


def test_upload_overlap_matches_ledger_despite_reference_wording():
    df = _df([
        ["22/09/2026", "CREDIT TRANSFER CUSTOMER", 0, 2000, 5000, "Standard Bank (123)"],
        ["22/09/2026", "CUSTOMER - CREDIT TRANSFER CUSTOMER", 0, 2000, 5000, "Standard Bank (123)"],
        ["22/09/2026", "CREDIT TRANSFER CUSTOMER", 0, 2000, 5000, "Standard Bank (456)"],
    ])
    df["_Document"] = [0, 1, 2]
    out = _deduplicate_transactions(df)
    assert len(out) == 2
    assert out.Source.tolist() == ["Standard Bank (123)", "Standard Bank (456)"]
    assert out.Description.iloc[0] == "CREDIT TRANSFER CUSTOMER"
    assert "_Document" not in out.columns


def test_upload_repetitions_are_preserved_within_each_document():
    df = _df([
        ["22/09/2026", "Pending", 0, 0, 5000, "FNB (123)"],
        ["22/09/2026", "Pending", 0, 0, 5000, "FNB (123)"],
        ["22/09/2026", "Pending", 0, 0, 5000, "FNB (123)"],
    ])
    df["_Document"] = [0, 0, 1]
    assert len(_deduplicate_transactions(df)) == 2


def test_parse_json_keeps_document_identity_separate_from_account_identity():
    results = []
    for index, (number, description) in enumerate([
        ("12345678901", "CREDIT TRANSFER CUSTOMER"),
        ("0000012345678901", "CUSTOMER - CREDIT TRANSFER CUSTOMER"),
        ("12345678902", "CREDIT TRANSFER CUSTOMER"),
    ]):
        results.append({
            "filename": f"statement-{index}.pdf", "error": None,
            "bank_name": "Standard Bank", "bank_id": "standard_bank",
            "account_number": number,
            "df": _df([["22/09/2026", description, 0.0, 2000.0, 5000.0, "ignored"]]),
        })
    response = _build_parse_json_sync(results, 3)
    assert response["successful_files"] == 3
    assert response["duplicates_removed"] == 1
    assert response["coverage"]["accounts_detected"] == 2
    assert len(response["transactions"]) == 2
    assert all("_Document" not in row for row in response["transactions"])
