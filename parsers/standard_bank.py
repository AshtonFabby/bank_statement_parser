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
    # Date format for current account statements: YYYYMMDD (e.g. 20250804)
    DATE_PATTERN_8D = re.compile(r"\b(20\d{2})(\d{2})(\d{2})\b")
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

        # Current account statement format: "Account 0000220835705 NAME"
        if not account_number:
            acc_match = re.search(
                r"^Account\s+(\d{10,})\s+",
                first_page,
                re.IGNORECASE | re.MULTILINE,
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Look for "Product name: BUS CURRENT" or "CURRENT ACC" pattern
        if not account_type:
            product_match = re.search(
                r"Product\s*name[:\s]*([A-Z\s]+?)(?:\n|$)",
                first_page,
                re.IGNORECASE,
            )
            if product_match:
                account_type = product_match.group(1).strip()

        # Current account statement: "CURRENT ACCOUNT - STATEMENT DETAILS"
        if not account_type:
            type_match = re.search(
                r"(CURRENT ACCOUNT|SAVINGS ACCOUNT|CHEQUE ACCOUNT)",
                first_page,
                re.IGNORECASE,
            )
            if type_match:
                account_type = type_match.group(1).title()

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
    # Business statement (international format): comma thousands, period decimal (e.g. 4,140.00-)
    BIZ_INT_AMOUNT_PATTERN = re.compile(r"-?[\d,]+\.\d{2}-?")
    # Business statement (international) line ending: MM DD Balance
    BIZ_INT_END_PATTERN = re.compile(
        r"(\d{2})\s+(\d{2})\s+(-?[\d,]+\.\d{2})\s*$"
    )

    def _detect_format(self) -> str:
        """Detect which Standard Bank statement format this is."""
        first_page = self._extract_first_page_text()

        # Regular statement has "Payments" and "Deposits" column headers
        if re.search(r"Payments.*Deposits.*Balance", first_page, re.DOTALL | re.IGNORECASE):
            return "regular"

        # Check for transactional history format (In/Out columns, space-thousands)
        if re.search(r"In \(R\)|Out \(R\)|Bank fees \(R\)", first_page):
            return "transactional_history"
        # Transactional history uses space-thousands SA amounts (e.g. "+41 635,00")
        # Require at least one space-thousands group to avoid matching regular comma-thousands
        SA_SPACE_THOUSANDS = re.compile(r"[+-]?\d{1,3}(?: \d{3})+,\d{2}")
        if (re.search(r"standardbank\.co\.za", first_page, re.IGNORECASE)
                and SA_SPACE_THOUSANDS.search(first_page)):
            return "transactional_history"

        # Current account statement format: columns are Page/Details/Service Fee/Debit/Credit/Date/Balance
        # with YYYYMMDD dates and comma-thousands amounts
        if re.search(r"Details.*Service.*Fee.*Debit.*Credit.*Date.*Balance", first_page,
                      re.DOTALL | re.IGNORECASE):
            return "current_account"

        # Check for business statement format (Details/Service/Credits/Date/Balance columns)
        if re.search(r"Details.*Service.*Credits.*Date.*Balance", first_page,
                      re.DOTALL | re.IGNORECASE):
            # Distinguish number format: period-thousands/comma-decimal (e.g. 1.234,56)
            # vs comma-thousands/period-decimal (e.g. 1,234.56)
            if re.search(r"\d{1,3}(?:\.\d{3})+,\d{2}", first_page):
                return "business_statement"
            return "business_statement_int"

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

    def _extract_transactions_business_int(self) -> pd.DataFrame:
        """Extract transactions from Standard Bank business statement (international number format).

        Same column layout as business_statement but uses comma-thousands, period-decimal
        (e.g. 4,140.00-) instead of period-thousands, comma-decimal.
        Columns: Details | Service Fee | Credits/Debits | Date (MM DD) | Balance
        Trailing minus indicates debit amounts.
        """
        rows = []

        # Extract year from statement period
        first_page = self._extract_first_page_text()
        year = "2025"
        period_match = re.search(
            r"Statement\s+from\s+.*?(\d{4})\s+to\s+.*?(\d{4})",
            first_page, re.IGNORECASE,
        )
        if period_match:
            year = period_match.group(2)

        for page_text in self._iterate_pages():
            lines = page_text.split("\n")
            in_header = True

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Detect end of header block (column headers end with "Fee Debits")
                if in_header:
                    if "Fee" in line and ("Debits" in line or "Debit" in line):
                        in_header = False
                    continue

                # Detect footer
                if "These fees include VAT" in line or "fees include VAT" in line.lower():
                    break

                # Try to match transaction line (has MM DD Balance at end)
                end_match = self.BIZ_INT_END_PATTERN.search(line)
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
                balance = self._clean_amount(end_match.group(3))
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
                amts = list(self.BIZ_INT_AMOUNT_PATTERN.finditer(before_end))

                if not amts:
                    continue

                # Last amount before date is the transaction amount
                amt_str = amts[-1].group()
                is_debit = amt_str.endswith("-")
                txn_amt = self._clean_amount(amt_str)

                # Description is text before the first amount
                description = before_end[:amts[0].start()].strip()

                debit = 0.0
                credit = 0.0
                if is_debit or txn_amt < 0:
                    debit = abs(txn_amt)
                else:
                    credit = txn_amt

                rows.append(create_transaction_row(
                    date_str, description, debit, credit, balance
                ))

        return pd.DataFrame(rows)

    def _extract_transactions_current_account(self) -> pd.DataFrame:
        """Extract transactions from Standard Bank current account statement.

        Format: Page# | Description | Service Fee | Debit | Credit | Date(YYYYMMDD) | Balance
        - Transaction lines start with a page number digit
        - Reference/narration lines follow without a page number prefix
        - Amounts use comma-thousands, period-decimal (e.g. -480,569.42)
        - Dates are 8-digit YYYYMMDD
        """
        rows = []

        for page_text in self._iterate_pages():
            lines = page_text.split("\n")

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Skip page headers/footers
                if re.match(r"^Standard Bank", line, re.IGNORECASE):
                    continue
                if re.search(r"The Standard Bank of South Africa", line, re.IGNORECASE):
                    continue
                if re.search(r"Computer Generated Copy", line, re.IGNORECASE):
                    continue
                if re.search(r"CURRENT ACCOUNT - STATEMENT DETAILS", line, re.IGNORECASE):
                    continue
                if "Page" in line and "Details" in line and "Balance" in line:
                    continue

                # Transaction lines start with a page number (digit(s) then space)
                page_num_match = re.match(r"^(\d+)\s+", line)
                if not page_num_match:
                    # Reference/narration line - append to last transaction description
                    if rows and line:
                        rows[-1]["Description"] = (rows[-1]["Description"] + " " + line).strip()
                    continue

                # Strip leading page number
                body = line[page_num_match.end():]

                # Find date (YYYYMMDD)
                date_match = self.DATE_PATTERN_8D.search(body)
                if not date_match:
                    continue

                year = date_match.group(1)
                month = date_match.group(2)
                day = date_match.group(3)
                date_str = f"{day}/{month}/{year}"

                # Find all amounts
                amounts_raw = self.AMOUNT_PATTERN.findall(body)
                if len(amounts_raw) < 2:
                    continue

                cleaned = [self._clean_amount(a) for a in amounts_raw]
                balance = cleaned[-1]

                # Description is text before the first amount
                first_amt_match = self.AMOUNT_PATTERN.search(body)
                description = body[:first_amt_match.start()].strip()

                # Handle BALANCE BROUGHT FORWARD
                if "BALANCE BROUGHT FORWARD" in description.upper():
                    rows.append(create_transaction_row(
                        date_str, "Balance Brought Forward", 0.0, 0.0, balance
                    ))
                    continue

                # Column layout: Service Fee | Debit | Credit | (date) | Balance
                # With 4 amounts: [service_fee, debit, credit, balance]
                debit = 0.0
                credit = 0.0
                if len(cleaned) >= 4:
                    debit_val = cleaned[-3]
                    credit_val = cleaned[-2]
                    if debit_val < 0:
                        debit = abs(debit_val)
                    if credit_val > 0:
                        credit = credit_val
                elif len(cleaned) == 3:
                    # Missing one column — use sign to determine debit vs credit
                    txn_val = cleaned[-2]
                    if txn_val < 0:
                        debit = abs(txn_val)
                    else:
                        credit = txn_val
                elif len(cleaned) == 2 and rows:
                    # Only balance present; derive from change
                    diff = balance - rows[-1]["Balance"]
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Standard Bank statement.

        Supports four formats:
        1. Regular statement: Payments | Deposits | Balance (period decimal)
        2. Transactional history: In (R) | Out (R) | Bank fees (R) | Balance (R) (comma decimal, space thousands)
        3. Business statement: Details | Service Fee | Credits/Debits | Date | Balance (period thousands, comma decimal)
        4. Current account statement: Page | Details | Service Fee | Debit | Credit | Date(YYYYMMDD) | Balance
        """
        fmt = self._detect_format()
        if fmt == "transactional_history":
            return self._extract_transactions_history()
        elif fmt == "business_statement":
            return self._extract_transactions_business()
        elif fmt == "business_statement_int":
            return self._extract_transactions_business_int()
        elif fmt == "current_account":
            return self._extract_transactions_current_account()
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
