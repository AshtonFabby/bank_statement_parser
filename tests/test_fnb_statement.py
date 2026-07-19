"""Regression tests for the FNB Bank Statement (positional) format.

The expected values below are the bank's own declared control totals, read
off the statement's turnover block:

    Opening Balance          171,855.49Cr
    Closing Balance           21,565.02Cr
    No. Credit Transactions 36  388,987.60Cr
    No. Debit Transactions  51  539,278.07Dr

They are ground truth, not a snapshot of whatever the parser happened to
produce, so a failure here means the parser is wrong.
"""

import pytest

OPENING = 171855.49
CLOSING = 21565.02
N_CREDIT, SUM_CREDIT = 36, 388987.60
N_DEBIT, SUM_DEBIT = 51, 539278.07


def _transactions(df):
    """Real transactions, excluding the synthetic Opening Balance anchor."""
    return df[df["Description"].astype(str).str.strip() != "Opening Balance"]


def test_detects_fnb(fnb_jan):
    account, _ = fnb_jan
    assert account.account_number == "62858659637"


def test_opening_balance_row_is_emitted(fnb_jan):
    """The opening anchor lives in the page-1 summary box, whose figure sits
    outside the transaction table's Balance column band."""
    _, df = fnb_jan
    first = df.iloc[0]
    assert str(first["Description"]).strip() == "Opening Balance"
    assert first["Balance"] == pytest.approx(OPENING)


def test_transaction_count_matches_declared_totals(fnb_jan):
    _, df = fnb_jan
    assert len(_transactions(df)) == N_CREDIT + N_DEBIT


def test_credit_and_debit_totals_match_declared_totals(fnb_jan):
    _, df = fnb_jan
    tx = _transactions(df)
    credits = tx["Credit"].fillna(0).astype(float)
    debits = tx["Debit"].fillna(0).astype(float)

    assert (credits > 0).sum() == N_CREDIT
    assert (debits > 0).sum() == N_DEBIT
    assert credits.sum() == pytest.approx(SUM_CREDIT, abs=0.01)
    assert debits.sum() == pytest.approx(SUM_DEBIT, abs=0.01)


def test_balance_chain_reconciles_to_declared_closing(fnb_jan):
    """opening + credits - debits == closing, per the bank's own figures."""
    _, df = fnb_jan
    tx = _transactions(df)
    expected = (
        OPENING
        + tx["Credit"].fillna(0).astype(float).sum()
        - tx["Debit"].fillna(0).astype(float).sum()
    )
    assert expected == pytest.approx(CLOSING, abs=0.01)
    assert df.iloc[-1]["Balance"] == pytest.approx(CLOSING, abs=0.01)


def test_declared_totals_are_read_off_the_statement(fnb_jan):
    account, _ = fnb_jan
    declared = account.declared_totals

    assert declared is not None
    assert declared.opening_balance == pytest.approx(OPENING)
    assert declared.closing_balance == pytest.approx(CLOSING)
    assert declared.credit_count == N_CREDIT
    assert declared.credit_total == pytest.approx(SUM_CREDIT)
    assert declared.debit_count == N_DEBIT
    assert declared.debit_total == pytest.approx(SUM_DEBIT)


def test_every_transaction_is_verified(fnb_jan):
    """Without the opening anchor the first transaction is silently trusted
    rather than verified, and accuracy still reports 100%."""
    from services.verification import verify_and_correct

    account, df = fnb_jan
    _, result = verify_and_correct(df, "1._Jan.pdf", account.bank, "fnb")

    assert result.verified_transactions == N_CREDIT + N_DEBIT
    assert result.unverified_transactions == 0
    assert result.failing_transactions == 0
    assert result.opening_balance == pytest.approx(OPENING)
    assert result.accuracy_percentage == 100.0


def test_parse_agrees_with_declared_totals(fnb_jan):
    from services.verification import verify_and_correct

    account, df = fnb_jan
    _, result = verify_and_correct(
        df, "1._Jan.pdf", account.bank, "fnb",
        declared_totals=account.declared_totals,
    )

    assert result.declared_totals_mismatches == []
    assert result.declared_totals_match is True


def test_cross_year_dates_use_statement_period(fnb_jan):
    """The period spans 30 Dec 2025 - 30 Jan 2026, so the leading 31 Dec rows
    belong to the previous year."""
    _, df = fnb_jan
    assert str(_transactions(df).iloc[0]["Date"]) == "31/12/2025"
