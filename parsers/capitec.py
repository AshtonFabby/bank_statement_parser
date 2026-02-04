"""Capitec Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import PATTERNS, create_transaction_row, normalize_amount_string


class CapitecParser(BaseBankParser):
    """Parser for Capitec Bank statements."""

    BANK_NAME = "Capitec"
    BANK_ID = "capitec"
    DETECTION_KEYWORDS = ["capitec"]

    # Capitec-specific amount pattern: -1 234.56 or +1 234.56 or 1 234.56
    # Also handles fees with one decimal place like -6.0 or -6.00
    AMOUNT_PATTERN = re.compile(r"[+-]?\d{1,3}(?:[ ,]\d{3})*\.\d{1,2}")
    # Also support comma-separated format
    AMOUNT_PATTERN_COMMA = re.compile(r"[+-]?[\d,]+\.\d{1,2}")
    # Date patterns
    DATE_PATTERN_FULL = re.compile(r"^(\d{2}/\d{2}/\d{4})")  # DD/MM/YYYY
    DATE_PATTERN_SHORT = re.compile(r"^(\d{2}/\d{2}/\d{2})")  # DD/MM/YY
    # Business format: two dates at start (Post Date, Trans Date)
    BUSINESS_DATE_PATTERN = re.compile(r"^(\d{2}/\d{2}/\d{2})\s+(\d{2}/\d{2}/\d{2})\s+")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Capitec statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        lines = first_page.split("\n")
        for i, line in enumerate(lines):
            line_lower = line.lower().strip()

            # Look for "Account No." or "Account number"
            if "account no" in line_lower or "account number" in line_lower:
                match = re.search(r"\d{8,12}", line)
                if match:
                    account_number = match.group()
                elif i + 1 < len(lines):
                    match = re.search(r"\d{8,12}", lines[i + 1])
                    if match:
                        account_number = match.group()

            # Look for account type
            if "account type" in line_lower:
                # Extract text after "Account type"
                type_match = re.search(r"account\s*type\s+(.+)", line, re.IGNORECASE)
                if type_match:
                    account_type = type_match.group(1).strip()
                elif i + 1 < len(lines):
                    account_type = lines[i + 1].strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _convert_short_year(self, date_str: str) -> str:
        """Convert DD/MM/YY to DD/MM/YYYY."""
        parts = date_str.split("/")
        if len(parts) == 3 and len(parts[2]) == 2:
            year = int(parts[2])
            # Assume 20xx for years 00-99
            full_year = 2000 + year if year < 100 else year
            return f"{parts[0]}/{parts[1]}/{full_year}"
        return date_str

    def _clean_amount(self, amount_str: str) -> float:
        """Clean Capitec amount string to float."""
        return normalize_amount_string(amount_str)

    def _parse_business_format(self, page_text: str, rows: list) -> None:
        """Parse business account format.

        Format: Post Date | Trans Date | Description | Reference | Fees | Amount | Balance
        - Fees column (negative values like -6.0)
        - Amount has +/- prefix for credit/debit
        """
        for line in page_text.split("\n"):
            line = line.strip()

            # Skip headers
            if not line or "Post" in line and "Date" in line:
                continue
            if "Description" in line and "Reference" in line:
                continue
            if "Transaction history" in line:
                continue

            # Handle "Balance brought forward" - can have different formats
            if "balance brought forward" in line.lower() or "balance b/forward" in line.lower():
                amounts = self.AMOUNT_PATTERN.findall(line)
                if not amounts:
                    amounts = self.AMOUNT_PATTERN_COMMA.findall(line)
                if amounts:
                    balance = self._clean_amount(amounts[-1])
                    rows.append(create_transaction_row("", "Balance brought forward", 0.0, 0.0, balance))
                continue

            # Handle "Interest Rate" line - skip
            if "interest rate" in line.lower():
                continue

            # Check for business format: DD/MM/YY DD/MM/YY ...
            business_match = self.BUSINESS_DATE_PATTERN.match(line)
            if not business_match:
                continue

            # Use Post Date as the transaction date
            date_str = self._convert_short_year(business_match.group(1))
            rest_of_line = line[business_match.end():].strip()

            # Find all amounts in the line
            amounts = self.AMOUNT_PATTERN.findall(rest_of_line)
            if not amounts:
                amounts = self.AMOUNT_PATTERN_COMMA.findall(rest_of_line)
            if not amounts:
                continue

            # Extract description (text before first amount)
            first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
            if not first_amt_match:
                first_amt_match = self.AMOUNT_PATTERN_COMMA.search(rest_of_line)
            if first_amt_match:
                description = rest_of_line[:first_amt_match.start()].strip()
            else:
                description = rest_of_line

            # Parse amounts - Format: [Fees] Amount Balance
            # Balance is always last
            balance = self._clean_amount(amounts[-1])
            debit = 0.0
            credit = 0.0
            fees = 0.0

            if len(amounts) >= 3:
                # Fees, Amount, Balance
                fees_str = amounts[-3]
                amount_str = amounts[-2]

                fees = abs(self._clean_amount(fees_str))
                amount_val = self._clean_amount(amount_str)

                if amount_str.startswith("+") or (not amount_str.startswith("-") and amount_val > 0):
                    credit = abs(amount_val)
                else:
                    debit = abs(amount_val)

                if fees > 0:
                    debit += fees

            elif len(amounts) >= 2:
                # Amount, Balance
                amount_str = amounts[-2]
                amount_val = self._clean_amount(amount_str)

                if amount_str.startswith("+"):
                    credit = abs(amount_val)
                elif amount_str.startswith("-"):
                    debit = abs(amount_val)
                elif rows:
                    # Determine from balance change
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff
                else:
                    # First transaction - check if amount is positive or negative
                    if amount_val < 0:
                        debit = abs(amount_val)
                    else:
                        credit = amount_val

            elif len(amounts) == 1 and rows:
                # Just balance, calculate from previous
                prev_balance = rows[-1]["Balance"]
                diff = balance - prev_balance
                if diff < 0:
                    debit = abs(diff)
                else:
                    credit = diff

            rows.append(create_transaction_row(date_str, description, debit, credit, balance))

    def _parse_standard_format(self, page_text: str, rows: list) -> None:
        """Parse standard personal account format.

        Format: Date | Description | Reference | Money in | Money out | Fees | Balance
        Also handles newer format with different column structures.
        """
        for line in page_text.split("\n"):
            line = line.strip()

            # Skip header rows
            if "Date" in line and "Description" in line:
                continue
            if "Money in" in line or "Money out" in line:
                continue
            if "Transaction history" in line:
                continue

            # Handle Opening balance
            if "opening balance" in line.lower():
                amounts = self.AMOUNT_PATTERN.findall(line)
                if not amounts:
                    amounts = self.AMOUNT_PATTERN_COMMA.findall(line)
                if amounts:
                    balance = self._clean_amount(amounts[-1])
                    rows.append(create_transaction_row("", "Opening Balance", 0.0, 0.0, balance))
                continue

            # Standard format: DD/MM/YYYY
            date_match = self.DATE_PATTERN_FULL.match(line)
            if not date_match:
                continue

            # Find all amounts in line
            amounts = self.AMOUNT_PATTERN.findall(line)
            if len(amounts) < 2:
                amounts = self.AMOUNT_PATTERN_COMMA.findall(line)

            if len(amounts) < 2:
                continue

            date = line[:10]

            # Get description (text between date and first amount)
            rest_of_line = line[11:].strip()
            first_amt_match = re.search(r"[+-]?[\d,\s]+\.\d{2}", rest_of_line)
            if first_amt_match:
                description = rest_of_line[:first_amt_match.start()].strip()
            else:
                description = rest_of_line

            balance = self._clean_amount(amounts[-1])
            debit = 0.0
            credit = 0.0

            if len(amounts) >= 4:
                # Full format: Money in, Money out, Fees, Balance
                money_in = self._clean_amount(amounts[-4])
                money_out = self._clean_amount(amounts[-3])
                fees = self._clean_amount(amounts[-2])

                if money_in > 0:
                    credit = money_in
                if money_out > 0:
                    debit += abs(money_out)
                if fees > 0:
                    debit += fees

            elif len(amounts) == 3:
                # Could be: Amount, Fees, Balance or Money in, Money out, Balance
                first_amt = self._clean_amount(amounts[0])
                second_amt = self._clean_amount(amounts[1])

                if first_amt < 0:
                    debit = abs(first_amt)
                    if second_amt > 0 and second_amt < abs(first_amt):
                        debit += second_amt  # Fees
                elif first_amt > 0:
                    credit = first_amt
                    if second_amt < 0:
                        debit = abs(second_amt)

            elif len(amounts) == 2:
                txn_amount = self._clean_amount(amounts[0])

                if txn_amount < 0:
                    debit = abs(txn_amount)
                elif txn_amount > 0:
                    credit = txn_amount
                elif rows:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

            rows.append(create_transaction_row(date, description, debit, credit, balance))

    def _detect_format(self) -> str:
        """Detect statement format (business vs standard)."""
        first_page = self._extract_first_page_text()

        # Business format indicators
        if "Business Account" in first_page:
            return "business"
        if "Post Date" in first_page and "Trans" in first_page:
            return "business"
        # Check for DD/MM/YY DD/MM/YY pattern (two short dates)
        if self.BUSINESS_DATE_PATTERN.search(first_page):
            return "business"

        return "standard"

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Capitec statement.

        Handles two formats:
        1. Standard: Date | Description | Reference | Money in | Money out | Fees | Balance
        2. Business: Post Date | Trans Date | Description | Reference | Fees | Amount | Balance
        """
        rows = []
        statement_format = self._detect_format()

        for page_text in self._iterate_pages():
            if statement_format == "business":
                self._parse_business_format(page_text, rows)
            else:
                self._parse_standard_format(page_text, rows)

        return pd.DataFrame(rows)
