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
    # Numeric amount optionally followed by 'Cr' (credit txn or credit-balance marker)
    _AMOUNT_WITH_OPT_CR = re.compile(r"([\d,]+\.\d{2})(Cr)?", re.IGNORECASE)

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

    def _extract_statement_period(self, text: str) -> tuple:
        """Extract start/end month+year from statement period text.

        Returns (start_month, start_year, end_month, end_year) integers,
        or (None, None, None, None) if not found.
        """
        period_match = re.search(
            r"Statement\s+Period\s*[:\s]*"
            r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{4})"
            r"\s+to\s+"
            r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{4})",
            text, re.IGNORECASE
        )
        if period_match:
            start_month = int(MONTH_MAP.get(period_match.group(2).lower()[:3], "01"))
            start_year = int(period_match.group(3))
            end_month = int(MONTH_MAP.get(period_match.group(5).lower()[:3], "01"))
            end_year = int(period_match.group(6))
            return start_month, start_year, end_month, end_year
        return None, None, None, None

    def _assign_year(
        self,
        month_num: int,
        start_month,
        start_year,
        end_month,
        end_year,
        fallback_year,
    ) -> str:
        """Return the correct 4-digit year string for a transaction month.

        For statements that cross a year boundary (e.g. Dec 2025 to Jan 2026)
        we use start_year for months >= start_month and end_year otherwise.
        """
        if start_year is not None and end_year is not None:
            if start_year == end_year:
                return str(start_year)
            # Cross-year statement: months in the start_year range stay there
            if month_num >= start_month:
                return str(start_year)
            return str(end_year)
        return fallback_year or str(datetime.now().year)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from FNB statement.

        Handles both formats:
        1. Transaction History: DD MMM YYYY with CR/DR amounts
        2. Bank Statement: DD MMM with plain amounts
        """
        rows = []
        previous_balance = None
        current_year = None
        start_month = start_year = end_month = end_year = None

        for page_text in self._iterate_pages():
            # Extract year from statement period if not yet found (for old format)
            if not current_year:
                current_year = extract_year_from_text(page_text)

            # Extract the full statement period (start + end dates with years)
            # so we can assign the correct year to cross-year statements.
            if start_year is None:
                s_mo, s_yr, e_mo, e_yr = self._extract_statement_period(page_text)
                if s_yr is not None:
                    start_month, start_year, end_month, end_year = s_mo, s_yr, e_mo, e_yr

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
                        balance = self._clean_amount(amounts[0])
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
                month_abbr = date_match.group(2).lower()
                month = MONTH_MAP.get(month_abbr, "01")

                if has_year:
                    year = date_match.group(3)
                else:
                    month_num = int(month)
                    year = self._assign_year(
                        month_num,
                        start_month, start_year,
                        end_month, end_year,
                        current_year,
                    )

                date_str = f"{day}/{month}/{year}"

                # Get the rest of the line after the date
                rest_of_line = line[date_match.end():].strip()

                # Determine format:
                # - Transaction History: BOTH the amount AND balance carry a
                #   CR/DR suffix (e.g. "284.77 CR 0.00 CR") -> >=2 CR/DR tokens
                # - Bank Statement: only credit amounts carry a 'Cr' suffix;
                #   the running balance is a plain number -> <=1 CR/DR token
                #   Exception: when account flips to credit both amount and
                #   balance may have 'Cr' -> treated as Transaction History.
                cr_dr_matches = self.AMOUNT_PATTERN_CR_DR.findall(rest_of_line)
                has_cr_dr = len(cr_dr_matches) >= 2

                if has_cr_dr:
                    # Transaction History format with CR/DR
                    row = self._parse_transaction_history_line(rest_of_line, date_str)
                    if row:
                        rows.append(row)
                        previous_balance = row["Balance"]
                else:
                    # Bank Statement format with plain amounts (and optional Cr)
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
            # Strip trailing service fee decimal (e.g. "0.00" or "4.62") and
            # any sign character that precedes the transaction amount column.
            description = re.sub(r'\s+\d[\d,]*\.\d+\s*[-+]?\s*$', '', description).strip()
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

        FNB bank statement column layout:
          Description  Amount[Cr]  Balance[Cr]  [Accrued_Bank_Charges]

        Key rules:
        - A 'Cr' suffix on the TRANSACTION AMOUNT token = credit (money in).
        - A 'Cr' suffix on the BALANCE token = account has a positive/credit
          balance. This does NOT make the transaction a credit.
        - The optional 'Accrued Bank Charges' column is a small fixed fee
          (always < R30) appended at the end for fee-bearing transactions.

        We identify each column positionally: the last token is the bank charge
        (if small), the second-to-last is the balance, and everything before
        that is the transaction amount(s).
        """
        # Scan all numeric tokens in order, recording whether each has 'Cr' suffix
        found = []  # list of (float_value, has_cr, start_pos_in_rest_of_line)
        for m in self._AMOUNT_WITH_OPT_CR.finditer(rest_of_line):
            raw = m.group(1)
            has_cr = bool(m.group(2))
            val = self._clean_amount(raw)
            # Require at least 3 digits (e.g. "3.68" -> digits "368") to avoid
            # picking up version numbers or short reference codes
            if len(raw.replace(",", "").replace(".", "")) >= 3:
                found.append((val, has_cr, m.start()))

        if len(found) < 2:
            return None

        # Column layout: Amount[Cr] Balance[Cr] [Accrued_Bank_Charges]
        # When 3+ numeric tokens are present the last is always the optional
        # bank-charge column; the second-to-last is the running balance.
        if len(found) >= 3:
            balance = found[-2][0]
            tx_entries = found[:-2]
        else:
            balance = found[-1][0]
            tx_entries = found[:-1]

        if not tx_entries:
            return None

        # Description: everything before the first numeric token
        description = rest_of_line[: found[0][2]].strip() or None

        # Debit / credit determination:
        # Use the 'Cr' flag of the TRANSACTION AMOUNT token only.
        # When the account has a positive balance the running balance also carries
        # 'Cr' -- that must not be confused with an incoming credit transaction.
        tx_val, tx_has_cr, _ = tx_entries[-1]
        if tx_has_cr:
            debit, credit = 0.0, tx_val
        else:
            debit, credit = tx_val, 0.0

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
