"""FNB (First National Bank) statement parser."""

import re
from datetime import datetime

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import (
    MONTH_MAP,
    create_transaction_row,
    extract_year_from_text,
    normalize_amount_string,
)


class FNBParser(BaseBankParser):
    """Parser for FNB statements."""

    BANK_NAME = "FNB"
    BANK_ID = "fnb"
    DETECTION_KEYWORDS = ["fnb", "first national bank"]

    # Transaction History format: DD MMM YYYY (e.g., "08 Jan 2026")
    DATE_PATTERN_WITH_YEAR = re.compile(
        r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\b",
        re.IGNORECASE
    )
    # Bank Statement format: DD MMM (e.g., "01 Dec")
    DATE_PATTERN_NO_YEAR = re.compile(
        r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
        re.IGNORECASE
    )
    # Transaction History amounts: with CR/DR suffix
    AMOUNT_PATTERN_CR_DR = re.compile(r"[\d,]+\.\d{2}\s*(?:CR|DR)", re.IGNORECASE)
    # Bank Statement amounts: plain numbers
    AMOUNT_PATTERN_PLAIN = re.compile(r"[\d,]+\.\d{2}")
    # Amount pattern with Cr suffix (for some formats)
    AMOUNT_PATTERN_CR = re.compile(r"[\d,]+\.\d{2}Cr", re.IGNORECASE)

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from FNB statement."""
        full_text = self._extract_full_text()
        account_number = None
        account_type = None

        # Look for "Selected Account: 62388803027" pattern (new FNB format)
        selected_account_match = re.search(
            r"Selected\s*Account\s*[:\s]+(\d{10,12})", full_text, re.IGNORECASE
        )
        if selected_account_match:
            account_number = selected_account_match.group(1).strip()

        # Look for "Gold Business Account : 62765962941" pattern
        if not account_number:
            account_match = re.search(
                r"([\w\s]+Account)\s*[:\s]+(\d{10,12})", full_text
            )
            if account_match:
                account_type = account_match.group(1).strip()
                account_number = account_match.group(2).strip()

        # Look for Nickname field to use as account type
        if not account_type:
            nickname_match = re.search(
                r"Nickname\s*[:\s]+([\w\s]+?)(?:\n|Selected)", full_text, re.IGNORECASE
            )
            if nickname_match:
                account_type = nickname_match.group(1).strip()

        # Fallback: look for Account Number field
        if not account_number:
            acc_num_match = re.search(
                r"Account\s*Number[:\s]*(\d{10,12})", full_text, re.IGNORECASE
            )
            if acc_num_match:
                account_number = acc_num_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean FNB amount string to float."""
        return normalize_amount_string(amt)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from FNB statement.

        Handles both formats:
        1. Transaction History: DD MMM YYYY with CR/DR amounts
        2. Bank Statement: DD MMM with plain amounts
        """
        rows = []
        previous_balance = None
        current_year = None

        for page_text in self._iterate_pages():
            # Extract year from statement period if not yet found (for old format)
            if not current_year:
                current_year = extract_year_from_text(page_text)

            for line in page_text.split("\n"):
                line = line.strip()

                # Skip header rows
                if not line:
                    continue
                if "Date" in line and "Description" in line:
                    continue
                if "Balance" in line and "Amount" in line:
                    continue
                if "Service Fee" in line and "Closing Balance" in line:
                    continue

                # Handle Opening/Statement Balance
                if "opening balance" in line.lower() or "statement balance" in line.lower():
                    amounts = self.AMOUNT_PATTERN_PLAIN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Opening Balance", 0.0, 0.0, balance
                        ))
                        previous_balance = balance
                    continue

                # Try matching with year first (Transaction History format)
                date_match = self.DATE_PATTERN_WITH_YEAR.match(line)
                has_year = True

                if not date_match:
                    # Try matching without year (Bank Statement format)
                    date_match = self.DATE_PATTERN_NO_YEAR.match(line)
                    has_year = False

                if not date_match:
                    continue

                # Parse date
                day = date_match.group(1)
                month = MONTH_MAP.get(date_match.group(2).lower(), "01")

                if has_year:
                    year = date_match.group(3)
                else:
                    year = current_year or datetime.now().strftime("%Y")

                date_str = f"{day}/{month}/{year}"

                # Get the rest of the line after the date
                rest_of_line = line[date_match.end():].strip()

                # Try to determine which format based on CR/DR presence
                has_cr_dr = bool(self.AMOUNT_PATTERN_CR_DR.search(rest_of_line))

                if has_cr_dr:
                    # Transaction History format with CR/DR
                    row = self._parse_transaction_history_line(rest_of_line, date_str)
                    if row:
                        rows.append(row)
                        previous_balance = row["Balance"]
                else:
                    # Bank Statement format with plain amounts
                    row = self._parse_bank_statement_line(rest_of_line, date_str, previous_balance)
                    if row:
                        rows.append(row)
                        previous_balance = row["Balance"]

        return pd.DataFrame(rows)

    def _parse_transaction_history_line(self, rest_of_line: str, date_str: str) -> dict:
        """Parse a line from Transaction History format (with CR/DR).

        Format: Description Service_Fee Amount Balance
        Example: CITIBANK IQVIA1004848 0.00 284.77 CR 0.00 CR
        """
        # Find all amounts with CR/DR suffix
        amounts = self.AMOUNT_PATTERN_CR_DR.findall(rest_of_line)

        # Need at least 2 amounts: Amount and Balance
        if len(amounts) < 2:
            return None

        # Extract description (everything before the first amount)
        first_amount_match = self.AMOUNT_PATTERN_CR_DR.search(rest_of_line)
        if first_amount_match:
            description = rest_of_line[:first_amount_match.start()].strip()
        else:
            return None

        # The last amount is the Balance
        balance_str = amounts[-1]
        balance_val, balance_is_credit = self._parse_amount_cr_dr(balance_str)
        balance = balance_val if balance_is_credit else -balance_val

        # The second-to-last amount is the transaction Amount
        amount_str = amounts[-2]
        amount_val, amount_is_credit = self._parse_amount_cr_dr(amount_str)

        # CR means credit (money in), DR means debit (money out)
        if amount_is_credit:
            credit = amount_val
            debit = 0.0
        else:
            debit = amount_val
            credit = 0.0

        return create_transaction_row(date_str, description, debit, credit, balance)

    def _parse_bank_statement_line(self, rest_of_line: str, date_str: str, previous_balance: float) -> dict:
        """Parse a line from Bank Statement format (plain amounts).

        Format: Description Amount Balance Accrued_Bank_Charges
        Example: ADT Cash Deposit 00072011 A005 Thanda Mnyama 800.00Cr 391,101.63
        """
        # Check for Cr suffix amounts first
        cr_amounts = self.AMOUNT_PATTERN_CR.findall(rest_of_line)

        # Find all plain amounts
        amounts = self.AMOUNT_PATTERN_PLAIN.findall(rest_of_line)

        # Filter out very small amounts that might be part of reference numbers
        amounts = [a for a in amounts if '.' in a and len(a.replace(',', '').replace('.', '')) >= 3]

        if len(amounts) < 2:
            return None

        # Extract description (everything before the first amount)
        first_amount_match = re.search(r"[\d,]+\.?\d*Cr|[\d,]+\.\d{2}", rest_of_line)
        if first_amount_match:
            description = rest_of_line[:first_amount_match.start()].strip()
        else:
            return None

        # The last amount is usually Balance
        balance = self._clean_amount(amounts[-1])

        # Determine debit/credit
        debit = 0.0
        credit = 0.0

        # Check if the transaction amount has Cr suffix
        if cr_amounts:
            # Has Cr suffix = credit
            amount = self._clean_amount(cr_amounts[0].replace('Cr', '').replace('cr', ''))
            credit = amount
        elif len(amounts) >= 2:
            amount = self._clean_amount(amounts[-2])

            # Determine debit/credit from balance change
            if previous_balance is not None:
                diff = balance - previous_balance
                if abs(diff - amount) < 0.01:
                    # Positive change = credit
                    credit = amount
                elif abs(diff + amount) < 0.01:
                    # Negative change = debit
                    debit = amount
                else:
                    # Fallback: if balance increased, it's credit
                    if diff > 0:
                        credit = amount
                    else:
                        debit = amount
            else:
                # First transaction: check description for hints
                desc_lower = description.lower()
                if any(w in desc_lower for w in ["deposit", "credit", "payment received", "transfer in"]):
                    credit = amount
                elif any(w in desc_lower for w in ["fee", "charge", "withdrawal", "payment", "purchase"]):
                    debit = amount
                else:
                    # Default to debit
                    debit = amount

        return create_transaction_row(date_str, description, debit, credit, balance)

    def _parse_amount_cr_dr(self, amount_str: str) -> tuple[float, bool]:
        """Parse amount with CR/DR suffix.

        Args:
            amount_str: Amount string with CR or DR suffix (e.g., "284.77 CR", "1,178.06 DR")

        Returns:
            Tuple of (absolute value, is_credit)
        """
        amount_str = amount_str.strip().upper()
        is_credit = "CR" in amount_str

        # Remove CR/DR and clean the amount
        clean_str = amount_str.replace("CR", "").replace("DR", "").replace(",", "").strip()

        try:
            value = abs(float(clean_str))
            return value, is_credit
        except ValueError:
            return 0.0, False
