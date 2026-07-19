"""Transaction verification and correction service.

Verifies the accuracy of parsed bank statement transactions by checking
that each transaction's balance follows from the previous *corrected*
balance via:

    corrected_balance = previous_corrected_balance + credit - debit

A transaction "passes" if |corrected_balance - actual_balance| < TOLERANCE.

When a transaction fails and swapping debit↔credit would make it pass,
the swap is applied: the corrected DataFrame has the debit/credit columns
fixed, and the transaction counts as passing.

By using the corrected balance as the running baseline, one parsing error
causes exactly one failure — the cascade self-heals on the next transaction.

The VerificationResult only contains failing transactions (no passing
entries in failures list).
"""

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

TOLERANCE = 0.01

OPENING_BALANCE_KEYWORDS = [
    "opening balance",
    "balance brought forward",
    "balance brought forward",
    "previous balance",
    "statement opening balance",
]


@dataclass
class VerificationResult:
    filename: str = ""
    bank_name: str = ""
    bank_id: str = ""
    account_number: Optional[str] = None
    total_transactions: int = 0
    opening_balance: Optional[float] = None
    verified_transactions: int = 0
    passing_transactions: int = 0
    failing_transactions: int = 0
    total_failures: int = 0
    corrections: int = 0
    accuracy_percentage: Optional[float] = None
    tolerance: float = TOLERANCE
    failures: list = field(default_factory=list)
    error: Optional[str] = None
    # Rows the balance check could not assess because no baseline was
    # established yet (no opening balance row, and nothing parsed before
    # them). They are excluded from accuracy_percentage, so they must be
    # reported rather than silently inflating it.
    unverified_transactions: int = 0
    # None when the statement declares no control totals; otherwise whether
    # the parse matches every figure the bank declared about itself.
    declared_totals_match: Optional[bool] = None
    declared_totals_mismatches: list = field(default_factory=list)
    # Transactions whose description could not be read (e.g. rendered as an
    # image with no OCR available). The amounts are still correct; only the
    # description is lost.
    unreadable_descriptions: int = 0

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "bank_name": self.bank_name,
            "bank_id": self.bank_id,
            "account_number": self.account_number,
            "total_transactions": self.total_transactions,
            "opening_balance": self.opening_balance,
            "verified_transactions": self.verified_transactions,
            "passing_transactions": self.passing_transactions,
            "failing_transactions": self.failing_transactions,
            "total_failures": self.total_failures,
            "corrections": self.corrections,
            "accuracy_percentage": self.accuracy_percentage,
            "tolerance": self.tolerance,
            "failures": [
                f if isinstance(f, dict) else f.__dict__ for f in self.failures
            ],
            "error": self.error,
            "unverified_transactions": self.unverified_transactions,
            "declared_totals_match": self.declared_totals_match,
            "declared_totals_mismatches": self.declared_totals_mismatches,
            "unreadable_descriptions": self.unreadable_descriptions,
        }


def _is_opening_balance_row(row: pd.Series) -> bool:
    desc = str(row.get("Description", "")).strip().lower()
    if not desc or desc == "nan":
        return False
    return any(kw in desc for kw in OPENING_BALANCE_KEYWORDS)


def _check_declared_totals(df: pd.DataFrame, declared) -> list[dict]:
    """Compare the parse against the control totals the bank declared.

    This is the only check here that is independent of the transaction rows:
    the running-balance check asks whether the rows agree with each other,
    which a wholesale misread can satisfy. Comparing counts and sums against
    the statement's own turnover block instead proves the parse is complete.

    Runs on the corrected frame, so it also validates any debit/credit swap
    the verifier applied. Returns a list of mismatches (empty when the parse
    agrees with every figure the statement declares).
    """
    tx = df[~df.apply(_is_opening_balance_row, axis=1)]
    credits = tx["Credit"].fillna(0).astype(float)
    debits = tx["Debit"].fillna(0).astype(float)

    # Cast out of numpy scalars: these land in verification.json and the
    # /parse/json response, and json.dumps cannot serialize np.float64.
    actuals = {
        "credit_count": int((credits > 0).sum()),
        "credit_total": round(float(credits.sum()), 2),
        "debit_count": int((debits > 0).sum()),
        "debit_total": round(float(debits.sum()), 2),
        "closing_balance": (
            round(float(tx.iloc[-1]["Balance"]), 2) if len(tx) else None
        ),
    }

    mismatches = []
    for field_name, actual in actuals.items():
        expected = getattr(declared, field_name, None)
        if expected is None or actual is None:
            continue
        if abs(float(expected) - float(actual)) >= TOLERANCE:
            mismatches.append({
                "field": field_name,
                "declared": expected,
                "parsed": actual,
                "difference": round(float(actual) - float(expected), 2),
            })
    return mismatches


def verify_and_correct(
    df: pd.DataFrame,
    filename: str = "",
    bank_name: str = "",
    bank_id: str = "",
    account_number: Optional[str] = None,
    declared_totals=None,
    unreadable_descriptions: int = 0,
) -> tuple[pd.DataFrame, VerificationResult]:
    """Verify transaction balances and return a corrected DataFrame.

    Uses corrected_balance as the running baseline so that one parsing
    error does not cascade into many failures.

    When a transaction fails verification but would pass if debit and
    credit were swapped (e.g. a credit amount was classified as debit),
    the swap is applied automatically and the transaction counts as
    passing.

    Only transactions that fail even after swap correction are included
    in the result's ``failures`` list.

    When ``declared_totals`` is supplied (a parser's ``DeclaredTotals``, read
    off the statement's own turnover block), the finished parse is also
    cross-checked against it — an oracle independent of the rows themselves.

    Returns:
        Tuple of (corrected_df, VerificationResult).
        The corrected DataFrame has Debit, Credit, and Balance columns
        corrected where swaps were applied.
    """
    corrected = df.copy()
    failures: list[dict] = []
    opening_balance = None
    previous_corrected = None
    verified_count = 0
    pass_count = 0
    fail_count = 0
    total_failure_count = 0
    corrections = 0
    unverified_count = 0

    for idx, row in df.iterrows():
        debit = float(row.get("Debit", 0) or 0)
        credit = float(row.get("Credit", 0) or 0)
        actual_balance = float(row.get("Balance", 0) or 0)
        date = str(row.get("Date", ""))
        description = str(row.get("Description", ""))
        if description == "nan":
            description = ""

        if _is_opening_balance_row(row):
            opening_balance = actual_balance
            previous_corrected = actual_balance
            corrected.at[idx, "Balance"] = actual_balance
            continue

        if previous_corrected is None:
            # No baseline yet: this row becomes the anchor and is taken on
            # trust. Counted so the caller can see accuracy_percentage did
            # not cover it.
            unverified_count += 1
            previous_corrected = actual_balance
            corrected.at[idx, "Balance"] = actual_balance
            continue

        corrected_balance = round(previous_corrected + credit - debit, 2)
        difference = round(abs(corrected_balance - actual_balance), 2)
        passed = difference < TOLERANCE

        # If it fails, check whether swapping debit↔credit would pass.
        # This catches the common parser error where a credit amount is
        # classified as debit (or vice versa).
        if not passed:
            if debit > 0 and credit == 0:
                swapped_balance = round(previous_corrected + debit, 2)
                swapped_diff = round(abs(swapped_balance - actual_balance), 2)
                if swapped_diff < TOLERANCE:
                    credit = debit
                    debit = 0.0
                    corrected_balance = swapped_balance
                    passed = True
                    corrections += 1
            elif credit > 0 and debit == 0:
                swapped_balance = round(previous_corrected - credit, 2)
                swapped_diff = round(abs(swapped_balance - actual_balance), 2)
                if swapped_diff < TOLERANCE:
                    debit = credit
                    credit = 0.0
                    corrected_balance = swapped_balance
                    passed = True
                    corrections += 1

        # Always write corrected values for debit/credit (in case they were swapped)
        corrected.at[idx, "Debit"] = debit
        corrected.at[idx, "Credit"] = credit

        old_previous_corrected = previous_corrected

        if passed:
            corrected.at[idx, "Balance"] = corrected_balance
            previous_corrected = corrected_balance
        else:
            corrected.at[idx, "Balance"] = actual_balance
            previous_corrected = actual_balance

        verified_count += 1
        if passed:
            pass_count += 1
        else:
            fail_count += 1
            is_total_failure = difference > max(abs(debit), abs(credit), 1.0)
            if is_total_failure:
                total_failure_count += 1
            failures.append({
                "row": idx + 1,
                "date": date,
                "description": description,
                "debit": debit,
                "credit": credit,
                "previous_balance": old_previous_corrected,
                "corrected_balance": corrected_balance,
                "actual_balance": actual_balance,
                "difference": difference,
                "total_failure": is_total_failure,
            })

    accuracy = round((pass_count / verified_count) * 100, 2) if verified_count > 0 else None

    declared_mismatches: list = []
    declared_match = None
    if declared_totals is not None and len(df):
        declared_mismatches = _check_declared_totals(corrected, declared_totals)
        declared_match = not declared_mismatches

    result = VerificationResult(
        filename=filename,
        bank_name=bank_name,
        bank_id=bank_id,
        account_number=account_number,
        total_transactions=len(df),
        opening_balance=opening_balance,
        verified_transactions=verified_count,
        passing_transactions=pass_count,
        failing_transactions=fail_count,
        total_failures=total_failure_count,
        corrections=corrections,
        accuracy_percentage=accuracy,
        tolerance=TOLERANCE,
        failures=failures,
        unverified_transactions=unverified_count,
        declared_totals_match=declared_match,
        declared_totals_mismatches=declared_mismatches,
        unreadable_descriptions=unreadable_descriptions,
    )

    return corrected, result


def verify_transactions(
    df: pd.DataFrame, filename: str = "", bank_name: str = "",
    bank_id: str = "", account_number: Optional[str] = None,
) -> VerificationResult:
    """Convenience wrapper: verify and correct, returning only the result."""
    _, result = verify_and_correct(
        df, filename=filename, bank_name=bank_name,
        bank_id=bank_id, account_number=account_number,
    )
    return result