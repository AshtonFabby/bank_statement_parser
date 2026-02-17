"""Standard Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row, normalize_amount_string


class StandardBankParser(BaseBankParser):
    """Parser for Standard Bank statements."""

    BANK_NAME = "Standard Bank"
    BANK_ID = "standard_bank"
    DETECTION_KEYWORDS = ["standard bank", "standardbank"]

    # Date format: "17 Nov 22" (DD MMM YY) or "17 Jul 25"
    DATE_PATTERN = re.compile(
        r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})\b",
        re.IGNORECASE,
    )
    # Date format for transactional history: "09 Feb 2026" (DD MMM YYYY)
    DATE_PATTERN_4Y = re.compile(
        r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\b",
        re.IGNORECASE,
    )
    # Amount pattern - handles comma-separated amounts and negative values
    AMOUNT_PATTERN = re.compile(r"-?[\d,]+\.\d{2}")
    # SA format amounts: comma decimal, space thousands (e.g. -432 785,10 or +41 635,00)
    SA_AMOUNT_PATTERN = re.compile(r"[+-]?\d{1,3}(?: \d{3})*,\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Standard Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account number: 10 13 368 746 3" pattern (with spaces)
        acc_match = re.search(
            r"Account\s*number[:\s]*([\d\s]{10,20})",
            first_page,
            re.IGNORECASE,
        )
        if acc_match:
            account_number = acc_match.group(1).strip().replace(" ", "")

        # Also try without spaces
        if not account_number:
            acc_match = re.search(
                r"Account\s*number[:\s]*(\d{10,})",
                first_page,
                re.IGNORECASE,
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Transactional history format: "Account: Current Account 0000282855556000"
        if not account_number:
            acc_match = re.search(
                r"Account:\s*(.+?)\s+(\d{10,})",
                first_page,
                re.IGNORECASE,
            )
            if acc_match:
                account_type = acc_match.group(1).strip()
                account_number = acc_match.group(2).strip()

        # Business statement format: "BUSINESS CURRENT ACCOUNT Account Number 02 058 050 9"
        if not account_number:
            acc_match = re.search(
                r"(BUSINESS\s+\w+\s+ACCOUNT)\s+Account\s+Number\s+([\d\s]+)",
                first_page,
                re.IGNORECASE,
            )
            if acc_match:
                account_type = acc_match.group(1).strip()
                account_number = acc_match.group(2).strip().replace(" ", "")

        # Look for "Product name: BUS CURRENT" or "CURRENT ACC" pattern
        if not account_type:
            product_match = re.search(
                r"Product\s*name[:\s]*([A-Z\s]+?)(?:\n|$)",
                first_page,
                re.IGNORECASE,
            )
            if product_match:
                account_type = product_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean Standard Bank amount string to float."""
        return normalize_amount_string(amt)

    # Business statement amounts: period thousands, comma decimal (e.g. 295.981,28-)
    BIZ_AMOUNT_PATTERN = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}-?")
    # Business statement line ending: MM DD Balance
    BIZ_END_PATTERN = re.compile(
        r"(\d{2})\s+(\d{2})\s+(\d{1,3}(?:\.\d{3})*,\d{2}-?)\s*$"
    )

    def _detect_format(self) -> str:
        """Detect which Standard Bank statement format this is."""
        first_page = self._extract_first_page_text()

        # Check for transactional history format (In/Out columns, space-thousands)
        if re.search(r"In \(R\)|Out \(R\)|Bank fees \(R\)", first_page):
            return "transactional_history"
        if (re.search(r"standardbank\.co\.za", first_page, re.IGNORECASE)
                and self.SA_AMOUNT_PATTERN.search(first_page)):
            return "transactional_history"

        # Check for business statement format (period-thousands, comma-decimal)
        if re.search(r"Details.*Service.*Credits.*Date.*Balance", first_page,
                      re.DOTALL | re.IGNORECASE):
            return "business_statement"

        return "regular"

    def _parse_sa_amount(self, amt_str: str) -> float:
        """Parse SA-format amount (comma decimal, space thousands) to float."""
        s = amt_str.strip()
        neg = s.startswith("-")
        s = s.lstrip("+-").strip()
        s = s.replace(" ", "").replace(",", ".")
        val = float(s)
        return -val if neg else val

    def _extract_transactions_history(self) -> pd.DataFrame:
        """Extract transactions from Standard Bank transactional history format.

        Format uses SA number conventions:
        - Comma as decimal separator
        - Space as thousands separator
        - +/- prefix for credits/debits
        - Columns: In (R), Out (R), Bank fees (R), Balance (R)
        """
        rows = []

        for page_text in self._iterate_pages():
            lines = page_text.split("\n")
            prev_desc = ""

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Skip header lines
                if "Date" in line and "Reference" in line:
                    continue
                if "In (R)" in line or "Out (R)" in line:
                    continue
                if "Available balance" in line:
                    continue
                if "Account holder" in line or "Account:" in line:
                    continue
                if "Transaction date" in line:
                    continue
                if "Customer Care" in line or "Website:" in line:
                    continue

                # Try matching date at start of line (DD MMM YYYY)
                date_match = self.DATE_PATTERN_4Y.match(line)
                if not date_match:
                    # Not a date line - save as potential description for next transaction
                    prev_desc = line
                    continue

                day = date_match.group(1)
                month = MONTH_MAP.get(date_match.group(2).lower(), "01")
                year = date_match.group(3)
                date_str = f"{day}/{month}/{year}"

                rest = line[date_match.end():].strip()

                # Find all SA-format amounts in the line
                amt_matches = list(self.SA_AMOUNT_PATTERN.finditer(rest))
                if len(amt_matches) < 2:
                    # Need at least transaction amount + balance
                    prev_desc = line
                    continue

                # Description is text before the first amount
                description = rest[:amt_matches[0].start()].strip()

                # If no inline description, use the previous non-date line
                if not description and prev_desc:
                    description = prev_desc

                # Parse all amounts
                parsed = [self._parse_sa_amount(m.group()) for m in amt_matches]
                balance = parsed[-1]

                # Transaction amounts are all except the last (balance)
                debit = 0.0
                credit = 0.0
                for amt in parsed[:-1]:
                    if amt < 0:
                        debit += abs(amt)
                    elif amt > 0:
                        credit += amt

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))
                prev_desc = ""

        return pd.DataFrame(rows)

    def _parse_biz_amount(self, amt_str: str) -> float:
        """Parse business format amount (period thousands, comma decimal, trailing minus)."""
        s = amt_str.strip()
        neg = s.endswith("-")
        s = s.rstrip("-")
        s = s.replace(".", "").replace(",", ".")
        val = float(s)
        return -val if neg else val

    def _extract_transactions_business(self) -> pd.DataFrame:
        """Extract transactions from Standard Bank business statement format.

        Format uses period-thousands, comma-decimal (e.g. 1.234,56).
        Columns: Details | Service Fee | Credits/Debits | Date (MM DD) | Balance
        Trailing minus indicates negative amounts.
        """
        rows = []

        # Extract year from statement period
        first_page = self._extract_first_page_text()
        year = "2024"
        period_match = re.search(
            r"Statement\s+from\s+.*?(\d{4})\s+to\s+.*?(\d{4})",
            first_page, re.IGNORECASE,
        )
        if period_match:
            year = period_match.group(2)  # Use the "to" year

        for page_text in self._iterate_pages():
            lines = page_text.split("\n")
            in_header = True

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Detect end of header block (column headers end with "Fee Debits")
                if in_header:
                    if "Fee" in line and "Debits" in line:
                        in_header = False
                    continue

                # Detect footer
                if "These fees include VAT" in line:
                    break

                # Try to match transaction line (has MM DD Balance at end)
                end_match = self.BIZ_END_PATTERN.search(line)
                if not end_match:
                    # Handle BALANCE BROUGHT FORWARD without date (continuation pages)
                    if "BALANCE BROUGHT FORWARD" in line.upper():
                        continue

                    # Continuation description line - append to last transaction
                    if rows and line:
                        rows[-1]["Description"] += " " + line
                    continue

                month = end_match.group(1)
                day = end_match.group(2)
                balance = self._parse_biz_amount(end_match.group(3))
                date_str = f"{day}/{month}/{year}"

                # Get text before the date+balance
                before_end = line[:end_match.start()].strip()

                # Handle BALANCE BROUGHT FORWARD
                if "BALANCE BROUGHT FORWARD" in before_end.upper():
                    rows.append(create_transaction_row(
                        date_str, "Balance Brought Forward", 0.0, 0.0, balance
                    ))
                    continue

                # Find amounts in the text before date
                amts = list(self.BIZ_AMOUNT_PATTERN.finditer(before_end))

                if not amts:
                    continue

                # Last amount before date is the transaction amount
                txn_amt = self._parse_biz_amount(amts[-1].group())

                # Description is text before the first amount
                description = before_end[:amts[0].start()].strip()

                # Determine debit/credit (trailing minus = debit)
                debit = 0.0
                credit = 0.0
                if txn_amt < 0:
                    debit = abs(txn_amt)
                else:
                    credit = txn_amt

                rows.append(create_transaction_row(
                    date_str, description, debit, credit, balance
                ))

        return pd.DataFrame(rows)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Standard Bank statement.

        Supports three formats:
        1. Regular statement: Payments | Deposits | Balance (period decimal)
        2. Transactional history: In (R) | Out (R) | Bank fees (R) | Balance (R) (comma decimal, space thousands)
        3. Business statement: Details | Service Fee | Credits/Debits | Date | Balance (period thousands, comma decimal)
        """
        fmt = self._detect_format()
        if fmt == "transactional_history":
            return self._extract_transactions_history()
        elif fmt == "business_statement":
            return self._extract_transactions_business()
        return self._extract_transactions_regular()

    def _extract_transactions_regular(self) -> pd.DataFrame:
        """Extract transactions from regular Standard Bank statement format.

        Format: Date | Description | Payments | Deposits | Balance
        - Payments are negative (debits)
        - Deposits are positive (credits)
        - Balance is the running balance
        """
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip header rows
                if not line:
                    continue
                if "Date" in line and "Description" in line:
                    continue
                if "Payments" in line and "Deposits" in line:
                    continue

                # Handle STATEMENT OPENING BALANCE
                if "STATEMENT OPENING BALANCE" in line.upper():
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Statement Opening Balance", 0.0, 0.0, balance
                        ))
                    continue

                # Match date at start of line
                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                # Parse date - convert 2-digit year to 4-digit
                day = date_match.group(1).zfill(2)
                month = MONTH_MAP.get(date_match.group(2).lower(), "01")
                year = f"20{date_match.group(3)}"
                date_str = f"{day}/{month}/{year}"

                # Find all amounts
                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Clean amounts and track if negative
                cleaned_amounts = []
                is_negative = []
                for amt in amounts:
                    val = self._clean_amount(amt)
                    cleaned_amounts.append(abs(val))
                    is_negative.append(val < 0 or amt.strip().startswith("-"))

                # Get description
                rest_of_line = line[date_match.end():].strip()
                first_amount_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amount_match:
                    description = rest_of_line[:first_amount_match.start()].strip()
                else:
                    description = rest_of_line

                # Parse amounts - Standard Bank has Payments, Deposits, Balance columns
                debit = 0.0
                credit = 0.0
                balance = cleaned_amounts[-1] if cleaned_amounts else 0.0

                # If 3 amounts: Payments, Deposits, Balance
                if len(cleaned_amounts) == 3:
                    payment = cleaned_amounts[0]
                    deposit = cleaned_amounts[1]

                    # Payments are shown as negative (debits)
                    if is_negative[0] or payment > 0:
                        debit = payment

                    # Deposits are positive (credits)
                    if deposit > 0 and not is_negative[1]:
                        credit = deposit

                # If 2 amounts: could be Payment+Balance or Deposit+Balance
                elif len(cleaned_amounts) == 2:
                    first_amount = cleaned_amounts[0]
                    first_neg = is_negative[0]

                    # Negative = payment (debit), Positive = deposit (credit)
                    if first_neg:
                        debit = first_amount
                    else:
                        # Determine from balance change
                        if rows:
                            prev_balance = rows[-1]["Balance"]
                            diff = balance - prev_balance
                            if diff < 0:
                                debit = first_amount
                            else:
                                credit = first_amount
                        else:
                            # First transaction - check description for hints
                            desc_lower = description.lower()
                            if any(w in desc_lower for w in ["deposit", "credit", "received"]):
                                credit = first_amount
                            else:
                                debit = first_amount

                elif len(cleaned_amounts) == 1 and rows:
                    # Only balance - calculate from balance change
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
