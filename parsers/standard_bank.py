"""Standard Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row, normalize_amount_string


class StandardBankParser(BaseBankParser):
    """Parser for Standard Bank statements."""

    BANK_NAME = "Standard Bank"
    BANK_ID = "standard_bank"
    DETECTION_KEYWORDS = [
        ("standard bank", 2),
        ("standardbank", 3),
        ("standard bank of south africa", 10),
        ("www.standardbank.co.za", 10),
        ("0860 123 000", 10),
    ]

    # Date format: "17 Nov 22" (DD MMM YY) or "17 Jul 25"
    DATE_PATTERN = re.compile(
        r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})\b",
        re.IGNORECASE,
    )
    # Date format for transactional history: "09 Feb 2026" (DD MMM YYYY)
    DATE_PATTERN_4Y = re.compile(
        r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})(?:\s|$)",
        re.IGNORECASE,
    )
    # Date format for transactional history without year on date line: "22 Oct"
    DATE_PATTERN_NO_YEAR = re.compile(
        r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
        re.IGNORECASE,
    )
    # Standalone year line (e.g. "2025" on its own line in transactional history)
    YEAR_LINE_PATTERN = re.compile(r"^\d{4}$")
    # Date format for current account statements: YYYYMMDD (e.g. 20250804)
    DATE_PATTERN_8D = re.compile(r"\b(20\d{2})(\d{2})(\d{2})\b")
    # Date format ISO: YYYY-MM-DD
    DATE_PATTERN_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\b")
    # Amount pattern - handles comma-separated amounts and negative values
    AMOUNT_PATTERN = re.compile(r"-?\s*R?\s*-?[\d,]+\.\d{2}(?=\s|$)")
    # SA format amounts: comma decimal, space thousands (e.g. -432 785,10 or +41 635,00)
    SA_AMOUNT_PATTERN = re.compile(r"(?<!\w)[+-]?\d{1,3}(?: \d{3})*,\d{2}(?=\s|$)")
    # SA format amounts: period decimal, space thousands (e.g. -7 936 635.59 or + 40 440.00)
    SA_AMOUNT_PATTERN_PERIOD = re.compile(r"(?<!\w)[+-]?\s?\d{1,3}(?: \d{3})*\.\d{2}(?=\s|$)")

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

        # Transactional history format with dashes: "Account: Current Acc 41-059-012-6"
        if not account_number:
            acc_match = re.search(
                r"Account:\s*(.+?)\s+([\d][\d\-]{8,})",
                first_page,
                re.IGNORECASE,
            )
            if acc_match:
                account_type = acc_match.group(1).strip()
                account_number = acc_match.group(2).strip().replace("-", "")

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
        r"(\d{2})\s+(\d{2})\s+(-?[\d,]+\.\d{2}-?)\s*$"
    )

    def _detect_format(self) -> str:
        """Detect which Standard Bank statement format this is."""
        first_page = self._extract_first_page_text()

        # Regular statement with Fees column
        if re.search(r"Fees.*Payments.*Deposits.*Balance", first_page, re.DOTALL | re.IGNORECASE):
            return "regular_with_fees"

        # Regular statement has "Payments" and "Deposits" column headers
        if re.search(r"Payments.*Deposits.*Balance", first_page, re.DOTALL | re.IGNORECASE):
            return "regular"

        # Check for transactional history format (In/Out columns, space-thousands)
        if re.search(r"In \(R\)|Out \(R\)|Bank fees \(R\)", first_page):
            return "transactional_history"

        # Current account statement format: columns are Page/Details/Service Fee/Debit/Credit/Date/Balance
        # with YYYYMMDD dates and comma-thousands amounts
        if re.search(r"Details.*Service.*Fee.*Debit.*Credit.*Date.*Balance", first_page,
                      re.DOTALL | re.IGNORECASE):
            return "current_account"

        # Check for business statement format (Details/Service/Credits/Date/Balance columns)
        # Also handles split headers where "Details Service Date Balance" is on one line
        # and "Debits Credits" on the next (Credits appears after Date in text).
        if re.search(
            r"Details.*Service.*Credits.*Date.*Balance|Details\s+Service\s+Date\s+Balance",
            first_page, re.DOTALL | re.IGNORECASE
        ):
            # Distinguish number format: period-thousands/comma-decimal (e.g. 1.234,56)
            # vs comma-thousands/period-decimal (e.g. 1,234.56)
            if re.search(r"\d{1,3}(?:\.\d{3})+,\d{2}", first_page):
                return "business_statement"
            return "business_statement_int"

        # Transactional history fallback: SA space-thousands amounts (e.g. "+41 635,00")
        # Must come after business format checks to avoid false positives on amounts
        # like "09 04 101,055.54-" where "4 101,05" superficially matches the pattern.
        SA_SPACE_THOUSANDS = re.compile(r"[+-]?\d{1,3}(?: \d{3})+,\d{2}")
        if (re.search(r"standardbank\.co\.za", first_page, re.IGNORECASE)
                and SA_SPACE_THOUSANDS.search(first_page)):
            return "transactional_history"

        return "regular"

    def _parse_sa_amount(self, amt_str: str) -> float:
        """Parse SA-format amount (comma decimal, space thousands) to float."""
        s = amt_str.strip()
        neg = s.startswith("-")
        s = s.lstrip("+-").strip()
        s = s.replace(" ", "").replace(",", ".")
        val = float(s)
        return -val if neg else val

    def _parse_sa_amount_period(self, amt_str: str) -> float:
        """Parse SA-format amount with period decimal and space thousands (e.g. + 40 440.00 or -7 936 635.59)."""
        s = amt_str.strip()
        neg = s.startswith("-")
        s = s.lstrip("+-").strip()
        s = s.replace(" ", "")
        val = float(s)
        return -val if neg else val

    def _extract_year_from_date_range(self) -> str | None:
        """Extract the start year from the 'Transaction date range' header."""
        first_page = self._extract_first_page_text()
        m = re.search(
            r"Transaction\s+date\s+range[:\s]*\d{1,2}\s+\w+\s+(\d{4})",
            first_page, re.IGNORECASE,
        )
        if m:
            return m.group(1)
        m = re.search(
            r"Transaction\s+date\s+range[:\s]*.*?(\d{4})",
            first_page, re.IGNORECASE,
        )
        if m:
            return m.group(1)
        return None

    MONTH_ORDER = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "may": 5, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    def _extract_transactions_history(self) -> pd.DataFrame:
        """Extract transactions from Standard Bank transactional history format.

        Format uses SA number conventions:
        - Comma as decimal separator, space as thousands (e.g. +41 635,00)
        - OR period as decimal separator, space as thousands (e.g. + 40 440.00)
        - +/- prefix for credits/debits
        - Columns: In (R), Out (R), Bank fees (R), Balance (R)

        Supports two date layouts:
        1. DD MMM YYYY on each line (e.g. "09 Feb 2026")
        2. Standalone year line (e.g. "2025") followed by DD MMM lines (e.g. "22 Oct")
        """
        rows = []
        current_year = self._extract_year_from_date_range()
        prev_month_num = 0

        # Detect whether amounts use comma or period as decimal separator
        # by scanning the first page for the respective patterns
        first_page = self._extract_first_page_text()
        if self.SA_AMOUNT_PATTERN.search(first_page):
            amt_pattern = self.SA_AMOUNT_PATTERN
            amt_parser = self._parse_sa_amount
        else:
            amt_pattern = self.SA_AMOUNT_PATTERN_PERIOD
            amt_parser = self._parse_sa_amount_period

        for page_text in self._iterate_pages():
            lines = page_text.split("\n")
            prev_desc = ""
            pending_suffix = False

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

                # Check for standalone year line (e.g. "2025" on its own line)
                if self.YEAR_LINE_PATTERN.match(line):
                    current_year = line.strip()
                    prev_month_num = 0
                    pending_suffix = False
                    continue

                # Try matching date at start of line (DD MMM YYYY)
                date_match = self.DATE_PATTERN_4Y.match(line)
                if date_match:
                    day = date_match.group(1)
                    month = MONTH_MAP.get(date_match.group(2).lower(), "01")
                    year = date_match.group(3)
                    date_str = f"{day}/{month}/{year}"
                    current_year = year
                    prev_month_num = self.MONTH_ORDER.get(date_match.group(2).lower(), 0)
                else:
                    # Try matching date without year (DD MMM)
                    date_match = self.DATE_PATTERN_NO_YEAR.match(line)
                    if date_match:
                        day = date_match.group(1)
                        month_str = date_match.group(2).lower()
                        month = MONTH_MAP.get(month_str, "01")
                        month_num = self.MONTH_ORDER.get(month_str, 0)
                        if current_year:
                            # Year rollover: Dec -> Jan means increment year
                            if prev_month_num == 12 and month_num == 1:
                                try:
                                    current_year = str(int(current_year) + 1)
                                except ValueError:
                                    pass
                            date_str = f"{day.zfill(2)}/{month}/{current_year}"
                            prev_month_num = month_num
                        else:
                            if pending_suffix and rows:
                                pending_suffix = False
                                words = line.split()
                                is_suffix = len(words) <= 3 and ":" not in line and " - " not in line
                                if is_suffix:
                                    rows[-1]["Description"] = (rows[-1]["Description"] + " " + line).strip()
                                else:
                                    prev_desc = line
                            else:
                                prev_desc = line
                            continue
                    else:
                        if pending_suffix and rows:
                            pending_suffix = False
                            words = line.split()
                            is_suffix = len(words) <= 3 and ":" not in line and " - " not in line
                            if is_suffix:
                                rows[-1]["Description"] = (rows[-1]["Description"] + " " + line).strip()
                            else:
                                prev_desc = line
                        else:
                            prev_desc = line
                        continue

                rest = line[date_match.end():].strip()

                # Find all amounts using the detected format
                amt_matches = list(amt_pattern.finditer(rest))
                if len(amt_matches) < 2:
                    prev_desc = line
                    continue

                # Description is text before the first amount
                description = rest[:amt_matches[0].start()].strip()

                # If no inline description, use the previous non-date line
                if not description and prev_desc:
                    description = prev_desc

                # Parse all amounts
                parsed = [amt_parser(m.group()) for m in amt_matches]
                balance = parsed[-1]

                # Transaction amounts: first non-balance amount is In(R)/Out(R) by sign.
                # Any additional non-balance amounts represent the Bank fees(R) column,
                # which is a sub-component breakdown of Out(R) — not an additional charge.
                # Do not add fees to the debit; the Out(R) amount already includes them.
                debit = 0.0
                credit = 0.0
                non_balance = parsed[:-1]
                if non_balance:
                    first = non_balance[0]
                    if first < 0:
                        debit = abs(first)
                    elif first > 0:
                        credit = first

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))
                prev_desc = ""
                pending_suffix = True

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

                # Detect end of header block. Some formats have "Fee Debits" on one line;
                # others split across lines with "Fee" alone as the last header row.
                if in_header:
                    if ("Fee" in line and ("Debits" in line or "Debit" in line)) or line.strip() == "Fee":
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
                if re.match(r"^\*\*\s*END OF REPORT\s*\*\*$", line, re.IGNORECASE):
                    continue
                if re.match(r"^DATE\s+\d{8}", line):
                    continue

                # Transaction lines start with a page number (digit(s) then space)
                page_num_match = re.match(r"^(\d+)\s+", line)
                if not page_num_match:
                    # Skip footer lines that appear as non-transaction lines
                    if re.search(r"\*\*\s*END OF REPORT\s*\*\*", line, re.IGNORECASE):
                        continue
                    if re.match(r"^DATE\s+\d{8}", line):
                        continue
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

                # BALANCE BROUGHT FORWARD carries the opening balance. Emit the
                # first one as the anchor: without it verification adopts the
                # first real transaction as its baseline and never checks it.
                # Later occurrences are per-page continuation markers repeating
                # the running balance, so they are skipped.
                if "BALANCE BROUGHT FORWARD" in description.upper():
                    if not rows:
                        rows.append(create_transaction_row(
                            "", "Opening Balance", 0.0, 0.0, balance
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
            df = self._extract_transactions_history()
        elif fmt == "business_statement":
            df = self._extract_transactions_business()
        elif fmt == "business_statement_int":
            df = self._extract_transactions_business_int()
        elif fmt == "current_account":
            df = self._extract_transactions_current_account()
        elif fmt == "regular_with_fees":
            df = self._extract_transactions_regular_with_fees()
        else:
            df = self._extract_transactions_regular()
        return self._normalise_chronological_order(df)

    @staticmethod
    def _normalise_chronological_order(df: pd.DataFrame) -> pd.DataFrame:
        """Return transactions oldest-first, reversing newest-first exports.

        Standard Bank emits the same export in both directions depending on
        how it was generated: of eight otherwise-identical files, four run
        newest-first. The amounts are extracted correctly either way, but the
        running balance only reconciles forwards in time, so a newest-first
        document scores near 0% and every downstream balance check is wrong.

        Reversing (not sorting) is deliberate. Within a single day the source
        lists transactions in reverse too, so a plain reverse restores the true
        sequence, whereas sorting by date would shuffle same-day rows into an
        arbitrary order and break the chain it was meant to fix.
        """
        if df is None or df.empty or "Date" not in df.columns:
            return df

        dates = pd.to_datetime(df["Date"], format="%d/%m/%Y", errors="coerce")
        known = dates.dropna()
        if len(known) < 2:
            return df

        # Compare adjacent pairs rather than just first vs last, so a single
        # out-of-order row cannot flip an otherwise ascending document.
        deltas = known.diff().dropna()
        descending = int((deltas < pd.Timedelta(0)).sum())
        ascending = int((deltas > pd.Timedelta(0)).sum())
        if descending <= ascending:
            return df

        reversed_df = df.iloc[::-1].reset_index(drop=True)

        # An Opening Balance anchor belongs at the top; reversing would strand
        # it at the bottom where verification cannot use it as the baseline.
        is_opening = (
            reversed_df["Description"].astype(str).str.strip().str.lower()
            == "opening balance"
        )
        if is_opening.any():
            reversed_df = pd.concat(
                [reversed_df[is_opening], reversed_df[~is_opening]]
            ).reset_index(drop=True)

        return reversed_df

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
                    # Description rollover
                    if rows and not self.AMOUNT_PATTERN.search(line):
                        line_lower = line.lower()
                        ignore_keywords = [
                            "page", "statement", "balance", "customer care",
                            "bank", "fees", "deposits", "payments", "address:",
                            "post date", "account number", "netbank", "absa",
                            "standard bank", "fnb", "richards bay"
                        ]
                        if not any(k in line_lower for k in ignore_keywords):
                            rows[-1]["Description"] = (rows[-1]["Description"] + " " + line).strip()
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
                # Use the actual signed value for balance (preserves negative for overdraft)
                # but cleaned_amounts stores abs values for debit/credit magnitudes.
                debit = 0.0
                credit = 0.0
                balance = self._clean_amount(amounts[-1]) if amounts else 0.0

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

    def _extract_transactions_regular_with_fees(self) -> pd.DataFrame:
        """Extract transactions from regular Standard Bank statements with a Fees column.

        Format: Date | Description | Fees | Payments | Deposits | Balance
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

                # Match ISO date or regular date at start of line
                date_match = self.DATE_PATTERN_ISO.match(line)
                is_iso = True
                if not date_match:
                    date_match = self.DATE_PATTERN.match(line)
                    is_iso = False

                if not date_match:
                    # Description rollover
                    if rows and not self.AMOUNT_PATTERN.search(line):
                        line_lower = line.lower()
                        ignore_keywords = [
                            "page", "statement", "balance", "customer care",
                            "bank", "fees", "deposits", "payments", "address:",
                            "post date", "account number", "netbank", "absa",
                            "standard bank", "fnb", "richards bay"
                        ]
                        if not any(k in line_lower for k in ignore_keywords):
                            rows[-1]["Description"] = (rows[-1]["Description"] + " " + line).strip()
                    continue

                # Parse date
                if is_iso:
                    year = date_match.group(1)
                    month = date_match.group(2)
                    day = date_match.group(3)
                else:
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

                debit = 0.0
                credit = 0.0
                balance = self._clean_amount(amounts[-1]) if amounts else 0.0

                if len(cleaned_amounts) == 4:
                    payment = cleaned_amounts[1]
                    deposit = cleaned_amounts[2]
                    debit = payment
                    credit = deposit
                elif len(cleaned_amounts) == 3:
                    amt_val = cleaned_amounts[1]
                    if is_negative[1]:
                        debit = amt_val
                    else:
                        credit = amt_val
                elif len(cleaned_amounts) == 2:
                    amt_val = cleaned_amounts[0]
                    if is_negative[0]:
                        debit = amt_val
                    else:
                        credit = amt_val
                elif len(cleaned_amounts) == 1 and rows:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
