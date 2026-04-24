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
        }


def _is_opening_balance_row(row: pd.Series) -> bool:
    desc = str(row.get("Description", "")).strip().lower()
    if not desc or desc == "nan":
        return False
    return any(kw in desc for kw in OPENING_BALANCE_KEYWORDS)


def verify_and_correct(
    df: pd.DataFrame,
    filename: str = "",
    bank_name: str = "",
    bank_id: str = "",
    account_number: Optional[str] = None,
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

        # Always write corrected values
        corrected.at[idx, "Debit"] = debit
        corrected.at[idx, "Credit"] = credit
        corrected.at[idx, "Balance"] = corrected_balance

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
                "previous_balance": previous_corrected,
                "corrected_balance": corrected_balance,
                "actual_balance": actual_balance,
                "difference": difference,
                "total_failure": is_total_failure,
            })

        previous_corrected = corrected_balance

    accuracy = round((pass_count / verified_count) * 100, 2) if verified_count > 0 else None

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