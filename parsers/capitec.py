"""Capitec Bank statement parser."""

import re

import pandas as pd
import pdfplumber

from .base import AccountInfo, BaseBankParser
from .utils import PATTERNS, create_transaction_row, normalize_amount_string


class CapitecParser(BaseBankParser):
    """Parser for Capitec Bank statements."""

    BANK_NAME = "Capitec"
    BANK_ID = "capitec"
    DETECTION_KEYWORDS = [("capitec", 1)]

    # Capitec-specific amount pattern: -1 234.56 or +1 234.56 or 1 234.56
    # Also handles fees with one decimal place like -6.0 or -6.00
    # (?<!\d) p      revents grabbing trailing digits from reference numbers
    # e.g. "4001787814 505.00" won't match as "814 505.00"
    AMOUNT_PATTERN = re.compile(r"(?<![a-zA-Z0-9])[+\-\u2212]?\s*R?\s*\d{1,3}(?:[ ,]\d{3})*\.\d{1,2}")
    # Also support comma-separated format
    AMOUNT_PATTERN_COMMA = re.compile(r"[+\-\u2212]?\s*R?\s*[\d,]+\.\d{1,2}")
    # Date patterns
    DATE_PATTERN_FULL = re.compile(r"^(\d{2}/\d{2}/\d{4})")  # DD/MM/YYYY
    DATE_PATTERN_SHORT = re.compile(r"^(\d{2}/\d{2}/\d{2})")  # DD/MM/YY
    # Business format: two dates at start (Post Date, Trans Date)
    BUSINESS_DATE_PATTERN = re.compile(r"^(\d{2}/\d{2}/\d{2})\s+(\d{2}/\d{2}/\d{2})\s+")
    # Single short date pattern for table cell matching
    SHORT_DATE_CELL = re.compile(r"^\d{2}/\d{2}/\d{2}$")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Capitec statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        lines = first_page.split("\n")
        for i, line in enumerate(lines):
            line_lower = line.lower().strip()

            # Look for "Account No.", "Account number", or bare "Account [number]"
            if "account type" not in line_lower and "account holder" not in line_lower:
                if "account no" in line_lower or "account number" in line_lower or "account" in line_lower:
                    match = re.search(r"\d{7,12}", line)
                    if match:
                        account_number = match.group()
                    elif i + 1 < len(lines):
                        match = re.search(r"\d{7,12}", lines[i + 1])
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

    def _parse_cell_amount(self, cell: str) -> float:
        """Parse an amount from a table cell, handling None and empty values."""
        if not cell or not cell.strip():
            return 0.0
        cell = cell.strip()
        # Find amount pattern in the cell
        match = self.AMOUNT_PATTERN.search(cell)
        if not match:
            match = self.AMOUNT_PATTERN_COMMA.search(cell)
        if match:
            return self._clean_amount(match.group())
        return 0.0

    def _find_table_column_indices(self, header_row: list) -> dict:
        """Identify column indices from the header row, handling merged/None columns.

        Returns dict with keys: post_date, trans_date, description, reference, fees, amount, balance
        """
        indices = {}
        clean = [
            (i, (c.strip().lower().replace("\n", " ") if c else ""))
            for i, c in enumerate(header_row)
        ]

        for i, val in clean:
            if "post" in val and "date" in val:
                indices["post_date"] = i
            elif "trans" in val and "date" in val:
                indices["trans_date"] = i
            elif "description" in val:
                indices["description"] = i
            elif "reference" in val:
                indices["reference"] = i
            elif val == "fees":
                indices["fees"] = i
            elif val == "amount":
                indices["amount"] = i
            elif val == "balance":
                indices["balance"] = i

        return indices

    def _parse_business_format_table(self, page, rows: list, in_transactions: bool = False) -> tuple[bool, bool]:
        """Parse business account format using pdfplumber table extraction.

        Uses structured table extraction for bordered tables.
        Dynamically detects column layout from header row.

        Skips tables that appear before Transaction History (e.g. Scheduled Payments).

        Args:
            page: pdfplumber page object.
            rows: List to append parsed transaction rows to.
            in_transactions: Whether we've already entered the Transaction History
                section on a prior page or table.

        Returns:
            Tuple of (found_data, in_transactions). found_data is True if table
            extraction succeeded and found data. in_transactions is the updated flag.
        """
        tables = page.extract_tables()
        if not tables:
            return False, in_transactions

        found_data = False
        for table in tables:
            # Detect column layout from header row
            col_map = {}
            for row_cells in table:
                if not row_cells:
                    continue
                combined = " ".join((c or "") for c in row_cells).lower()
                if "post" in combined and "date" in combined and "balance" in combined:
                    col_map = self._find_table_column_indices(row_cells)
                    break

            # If no header found, skip this table
            if not col_map:
                continue

            # If we haven't entered the transaction section yet, check the header
            # for transaction-history markers. Skip non-transaction tables.
            if not in_transactions:
                combined_header = " ".join((c or "") for c in col_map.keys()).lower()
                # Tables with Post Date / Trans Date are transaction tables
                if "post_date" in col_map and "trans_date" in col_map:
                    in_transactions = True
                elif "trans_date" in col_map:
                    in_transactions = True
                else:
                    continue

            # Get column indices with fallbacks
            desc_idx = col_map.get("description")
            ref_idx = col_map.get("reference")
            fees_idx = col_map.get("fees")
            amount_idx = col_map.get("amount")
            balance_idx = col_map.get("balance")
            post_date_idx = col_map.get("post_date", 0)

            for row_cells in table:
                if not row_cells or all(not c or not c.strip() for c in row_cells):
                    continue

                # Clean cells
                cells = [c.strip() if c else "" for c in row_cells]
                combined = " ".join(cells).lower()

                # Skip header rows
                if "post" in combined and "date" in combined:
                    continue
                if ("no limit" in combined or "overdraft" in combined) and "rate" in combined:
                    continue

                # Handle "Balance brought forward"
                if (
                    "balance brought forward" in combined
                    or "balance b/forward" in combined
                ):
                    if balance_idx is not None and balance_idx < len(cells):
                        balance = self._parse_cell_amount(cells[balance_idx])
                    else:
                        # Find balance in last non-empty cell
                        balance = 0.0
                        for c in reversed(cells):
                            if c:
                                balance = self._parse_cell_amount(c)
                                if balance != 0.0:
                                    break
                    if balance != 0.0:
                        rows.append(
                            create_transaction_row(
                                "", "Balance brought forward", 0.0, 0.0, balance
                            )
                        )
                        found_data = True
                    continue

                # Skip interest rate lines
                if "interest rate" in combined:
                    continue

                # Check if post date cell is a date (DD/MM/YY)
                post_date_cell = (
                    cells[post_date_idx] if post_date_idx < len(cells) else ""
                )
                if not post_date_cell or not self.SHORT_DATE_CELL.match(post_date_cell):
                    continue

                date_str = self._convert_short_year(post_date_cell)

                # Build description from description and reference columns
                description_parts = []
                if desc_idx is not None and desc_idx < len(cells) and cells[desc_idx]:
                    description_parts.append(cells[desc_idx])
                if ref_idx is not None and ref_idx < len(cells) and cells[ref_idx]:
                    # Replace newlines in multi-line references
                    description_parts.append(cells[ref_idx].replace("\n", " "))
                description = " ".join(description_parts).strip()

                # Parse fees, amount, balance
                fees = 0.0
                amount_val = 0.0
                amount_str = ""
                balance = 0.0

                if fees_idx is not None and fees_idx < len(cells):
                    fees = abs(self._parse_cell_amount(cells[fees_idx]))
                if amount_idx is not None and amount_idx < len(cells):
                    amount_str = cells[amount_idx]
                    amount_val = self._parse_cell_amount(cells[amount_idx])
                if balance_idx is not None and balance_idx < len(cells):
                    balance = self._parse_cell_amount(cells[balance_idx])

                # Determine debit/credit
                debit = 0.0
                credit = 0.0

                if amount_str.startswith("+"):
                    credit = abs(amount_val)
                elif amount_str.startswith("-") or amount_str.startswith("\u2212") or amount_val < 0:
                    debit = abs(amount_val)
                elif amount_val != 0.0 and rows:
                    # Determine from balance change
                    prev_balance = rows[-1]["Balance"]
                    if balance > prev_balance:
                        credit = abs(amount_val)
                    else:
                        debit = abs(amount_val)

                if fees > 0:
                    debit += fees

                # If no amount found but we have balance, calculate from previous
                if amount_val == 0.0 and balance != 0.0 and rows:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(
                    create_transaction_row(
                        date_str, description, debit, credit, balance
                    )
                )
                found_data = True

        return found_data, in_transactions

    def _parse_business_format(self, page_text: str, rows: list, in_transactions: bool = False) -> bool:
        """Parse business account format from extracted text.

        Format: Post Date | Trans Date | Description | Reference | Fees | Amount | Balance
        - Fees column (negative values like -6.0)
        - Amount has +/- prefix for credit/debit

        Skips content before the Transaction History section unless
        in_transactions is True.

        Args:
            page_text: Extracted text from a single PDF page.
            rows: List to append parsed transaction rows to.
            in_transactions: Whether we've already entered the Transaction History
                section on a prior page.

        Returns:
            Updated in_transactions flag.
        """
        for line in page_text.split("\n"):
            line = line.strip()

            # Always process Balance brought forward regardless of section
            if (
                "balance brought forward" in line.lower()
                or "balance b/forward" in line.lower()
            ):
                amounts = self.AMOUNT_PATTERN.findall(line)
                if not amounts:
                    amounts = self.AMOUNT_PATTERN_COMMA.findall(line)
                if amounts:
                    balance = self._clean_amount(amounts[-1])
                    if "\u2212" in line:
                        balance = -abs(balance)
                    rows.append(
                        create_transaction_row(
                            "", "Balance brought forward", 0.0, 0.0, balance
                        )
                    )
                continue

            # Skip all content before Transaction History section
            if not in_transactions:
                if "transaction history" in line.lower():
                    in_transactions = True
                elif re.search(r"post.*date.*balance", line.lower()):
                    in_transactions = True
                continue

            # Below: we are in the transaction section

            # Skip headers
            if not line or ("Post" in line and "Date" in line):
                continue
            if "Description" in line and "Reference" in line:
                continue
            if "Transaction history" in line:
                continue

            # Check for business format: DD/MM/YY DD/MM/YY ...
            business_match = self.BUSINESS_DATE_PATTERN.match(line)
            if not business_match:
                continue

            # Use Post Date as the transaction date
            date_str = self._convert_short_year(business_match.group(1))
            rest_of_line = line[business_match.end() :].strip()

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
                description = rest_of_line[: first_amt_match.start()].strip()
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

                if amount_str.startswith("+") or (
                    not amount_str.startswith("-") and not amount_str.startswith("\u2212") and amount_val > 0
                ):
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
                elif amount_str.startswith("-") or amount_str.startswith("\u2212"):
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

            rows.append(
                create_transaction_row(date_str, description, debit, credit, balance)
            )

        return in_transactions

    def _parse_standard_format(self, page_text: str, rows: list, in_transactions: bool = False) -> bool:
        """Parse standard personal account format.

        Format: Date | Description | Reference | Money in | Money out | Fees | Balance
        Also handles newer format with different column structures.

        Skips content before the Transaction History section (Scheduled Payments,
        Debit Orders, Card Subscriptions, Spending Summary, etc.) unless
        in_transactions is True.

        Args:
            page_text: Extracted text from a single PDF page.
            rows: List to append parsed transaction rows to.
            in_transactions: Whether we've already entered the Transaction History
                section on a prior page.

        Returns:
            Updated in_transactions flag (True once Transaction History is found).
        """
        for line in page_text.split("\n"):
            line = line.strip()

            # Always process Opening Balance regardless of section
            if "opening balance" in line.lower():
                amounts = self.AMOUNT_PATTERN.findall(line)
                if not amounts:
                    amounts = self.AMOUNT_PATTERN_COMMA.findall(line)
                if amounts:
                    balance = self._clean_amount(amounts[-1])
                    # Capitec business accounts use negative notation for credit
                    # balances. The opening balance line may have a Unicode minus
                    # sign separated from the number by 'R' (e.g. "− R 5 076 705.75"),
                    # which the regex won't capture as a signed number.
                    if "\u2212" in line:
                        balance = -abs(balance)
                    rows.append(
                        create_transaction_row("", "Opening Balance", 0.0, 0.0, balance)
                    )
                continue

            # Skip all content before Transaction History section
            # (Scheduled Payments, Debit Orders, Card Subscriptions, Spending Summary, etc.)
            if not in_transactions:
                if "transaction history" in line.lower():
                    in_transactions = True
                elif re.search(r"date\b.*description.*balance", line.lower()):
                    in_transactions = True
                continue

            # Below: we are in the transaction section

            # Skip header rows
            if "Date" in line and "Description" in line:
                continue
            if "Money in" in line or "Money out" in line:
                continue
            if "Transaction history" in line:
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
            first_amt_match = re.search(r"[+\-\u2212]?[\d,\s]+\.\d{2}", rest_of_line)
            if first_amt_match:
                description = rest_of_line[: first_amt_match.start()].strip()
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
                # Standard format: [Money In or Money Out], [Fee*], [Balance]
                # Fee can be negative (charge → debit) or positive (refund → credit)
                # e.g. [-75.00, -0.50, 28.48] = debit 75.50
                # e.g. [50.00, 0.50, 126.76]  = credit 50.50 (Correction refund)
                # e.g. [-1000.00, 14.00, 123.90] = debit 1000, credit 14 (Correction refund)
                first_amt = self._clean_amount(amounts[0])
                second_amt = self._clean_amount(amounts[1])

                if first_amt < 0:
                    debit = abs(first_amt)
                    if second_amt < 0:
                        debit += abs(second_amt)
                    elif second_amt > 0:
                        credit += second_amt
                elif first_amt > 0:
                    credit = first_amt
                    if second_amt < 0:
                        debit += abs(second_amt)
                    elif second_amt > 0:
                        credit += second_amt

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

            rows.append(
                create_transaction_row(date, description, debit, credit, balance)
            )

        return in_transactions

    def _detect_format(self) -> str:
        """Detect statement format (business vs standard)."""
        first_page = self._extract_first_page_text()
        first_page_lower = first_page.lower()

        # Check for standard format indicators first - "Money in"/"Money out"
        # columns indicate standard format even on business accounts
        if "money in" in first_page_lower and "money out" in first_page_lower:
            return "standard"

        # Business format indicators
        if "business account" in first_page_lower:
            return "business"
        if "post date" in first_page_lower and "trans" in first_page_lower:
            return "business"
        # Check for DD/MM/YY DD/MM/YY pattern (two short dates)
        if self.BUSINESS_DATE_PATTERN.search(first_page):
            return "business"

        # Also check table content for business format indicators
        try:
            with pdfplumber.open(self.pdf_file) as pdf:
                if pdf.pages:
                    tables = pdf.pages[0].extract_tables()
                    if tables:
                        for table in tables:
                            for row in table:
                                if row:
                                    combined = " ".join(c for c in row if c).lower()
                                    if (
                                        "money in" in combined
                                        and "money out" in combined
                                    ):
                                        self._reset_file()
                                        return "standard"
                                    if "post" in combined and "date" in combined:
                                        self._reset_file()
                                        return "business"
            self._reset_file()
        except Exception:
            self._reset_file()

        return "standard"

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Capitec statement.

        Handles two formats:
        1. Standard: Date | Description | Reference | Money in | Money out | Fees | Balance
        2. Business: Post Date | Trans Date | Description | Reference | Fees | Amount | Balance
        """
        rows = []
        statement_format = self._detect_format()

        if statement_format == "business":
            in_transactions = False
            with pdfplumber.open(self.pdf_file) as pdf:
                for page in pdf.pages:
                    found, in_transactions = self._parse_business_format_table(
                        page, rows, in_transactions
                    )
                    if not found:
                        text = page.extract_text()
                        if text:
                            in_transactions = self._parse_business_format(
                                text, rows, in_transactions
                            )
            self._reset_file()
        else:
            in_transactions = False
            for page_text in self._iterate_pages():
                in_transactions = self._parse_standard_format(
                    page_text, rows, in_transactions
                )

        # Fallback: if section-gating removed everything, re-parse without gating
        # to maintain backward compatibility with older statement formats.
        if not rows:
            rows = self._extract_transactions_fallback(statement_format)

        return pd.DataFrame(rows)

    def _extract_transactions_fallback(self, statement_format: str) -> list:
        """Fallback parser that processes every dated line (no section gating).

        Used when section-gating removes all rows, which can happen with
        older statement formats that don't have a Transaction History header.
        """
        fallback_rows = []

        if statement_format == "business":
            with pdfplumber.open(self.pdf_file) as pdf:
                for page in pdf.pages:
                    if not self._parse_business_format_table(
                        page, fallback_rows, in_transactions=True
                    )[0]:
                        text = page.extract_text()
                        if text:
                            self._parse_business_format(
                                text, fallback_rows, in_transactions=True
                            )
            self._reset_file()
        else:
            for page_text in self._iterate_pages():
                self._parse_standard_format(
                    page_text, fallback_rows, in_transactions=True
                )

        return fallback_rows
