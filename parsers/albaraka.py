"""Al Baraka Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row, normalize_amount_string


class AlbarakaParser(BaseBankParser):
    """Parser for Al Baraka Bank statements.

    Handles three statement formats:
    1. "View Statement": Monthly statements (YYYYMMDD dates)
    2. "e-Stamp": Combined annual statements (YYYYMMDD dates)
    3. "Transaction History": Summary + transactions (DD MMM YYYY dates)

    All use the same 7-column transaction table layout:
    Transaction Date | Effective Date | Description & Reference | Fee | Debit | Credit | Balance
    """

    BANK_NAME = "Al Baraka Bank"
    BANK_ID = "albaraka"
    DETECTION_KEYWORDS = [
        ("al baraka", 5),
        ("albaraka", 5),
        ("ibanking.co.za", 10),
        ("albarakaonline", 10),
        ("0860 225 786", 10),
        ("effective date", 10),
        ("fee debit credit balance", 10),
        ("business banking", 3),
        ("bank branch number", 5),
        ("branch code", 5),
    ]

    # Transaction date format 1: 8 digits (YYYYMMDD) at start of line
    DATE_PATTERN_YMD8 = re.compile(r"^(\d{8})\b")
    # Transaction date format 2: DD MMM YYYY (e.g. "02 Jul 2025")
    DATE_PATTERN_DMY4 = re.compile(
        r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
        r"Januarie|Februarie|Maart|April|Mei|Junie|Julie|Augustus|September|Oktober|November|Desember|"
        r"Jan|Feb|Mrt|Apr|Mei|Jun|Jul|Aug|Sep|Okt|Nov|Des)\s+(\d{4})\b",
        re.IGNORECASE,
    )
    # Amount pattern: optional sign, comma-separated thousands, decimal
    AMOUNT_PATTERN = re.compile(r"[+-]?[\d,]+\.\d{2}")
    # Lines to skip (headers, footers)
    SKIP_PATTERNS = [
        re.compile(r"^Requested by ", re.IGNORECASE),
        re.compile(r"^Transaction History$", re.IGNORECASE),
        re.compile(r"^Transaction Date Effective Date", re.IGNORECASE),
        re.compile(r"^Transaction\s.*Description$", re.IGNORECASE),
        re.compile(r"^Effective Date\s", re.IGNORECASE),
        re.compile(r"^Date and Reference$", re.IGNORECASE),
        re.compile(r"^Date\s+and\s+Reference$", re.IGNORECASE),
        re.compile(r"^My Statements", re.IGNORECASE),
        re.compile(r"^STATEMENT OF ACCOUNT$", re.IGNORECASE),
        re.compile(r"^Statement of Account$", re.IGNORECASE),
        re.compile(r"^Tax Invoice$", re.IGNORECASE),
        re.compile(r"^Al Baraka Bank Statement", re.IGNORECASE),
        re.compile(r"^Albaraka Bank Limited", re.IGNORECASE),
        re.compile(r"^Statement Date", re.IGNORECASE),
        re.compile(r"^Branch Code", re.IGNORECASE),
        re.compile(r"^Voucher Number", re.IGNORECASE),
        re.compile(r"^To verify this statement", re.IGNORECASE),
        re.compile(r"^www\.", re.IGNORECASE),
        re.compile(r"^the .Statement Verification. option$"),
        re.compile(r"^Lost Card", re.IGNORECASE),
        re.compile(r"^Contact Centre", re.IGNORECASE),
        re.compile(r"^Contact E-mail", re.IGNORECASE),
        re.compile(r"^Reg\. No\.:", re.IGNORECASE),
        re.compile(r"^We subscribe ", re.IGNORECASE),
        re.compile(r"^Address: ", re.IGNORECASE),
        re.compile(r"^(Name|Trading as|Suburb|City|Province|Postal code)\s", re.IGNORECASE),
        re.compile(r"^Address\s", re.IGNORECASE),
        re.compile(r"^(Opening|Closing|Total credits|Total debits|Total fees)\s", re.IGNORECASE),
        re.compile(r"^Fees on this statement ", re.IGNORECASE),
        re.compile(r"^Account Effects Not Cleared$", re.IGNORECASE),
        re.compile(r"^\d+$"),  # Page numbers
    ]

    def _convert_date_ymd8(self, yyyymmdd: str) -> str:
        """Convert YYYYMMDD to DD/MM/YYYY."""
        return f"{yyyymmdd[6:8]}/{yyyymmdd[4:6]}/{yyyymmdd[0:4]}"

    def _convert_date_dmy4(self, day: str, month_str: str, year: str) -> str:
        """Convert DD MMM YYYY to DD/MM/YYYY."""
        month = MONTH_MAP.get(month_str.lower(), "01")
        return f"{day}/{month}/{year}"

    def _clean_amount(self, amt_str: str) -> float:
        """Clean and parse an amount string to float."""
        return normalize_amount_string(amt_str)

    def _should_skip_line(self, line: str) -> bool:
        """Check if a line should be skipped."""
        for pattern in self.SKIP_PATTERNS:
            if pattern.match(line):
                return True
        return False

    def _is_transaction_start(self, line: str) -> bool:
        """Check if a line starts a new transaction."""
        return bool(self.DATE_PATTERN_YMD8.match(line) or self.DATE_PATTERN_DMY4.match(line))

    def _detect_format(self) -> str:
        """Detect the statement date format by examining the first transaction.

        Returns 'ymd8' for YYYYMMDD dates or 'dmy4' for DD MMM YYYY dates.
        """
        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                stripped = line.strip()
                if self._should_skip_line(stripped):
                    continue
                if self.DATE_PATTERN_YMD8.match(stripped):
                    self._reset_file()
                    return "ymd8"
                if self.DATE_PATTERN_DMY4.match(stripped):
                    self._reset_file()
                    return "dmy4"
        self._reset_file()
        return "ymd8"

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Al Baraka statement."""
        full_text = self._extract_full_text()
        account_number = None
        account_type = None

        # Look for "Account:" without digits nearby (address contains "Account" word)
        acc_match = re.search(
            r"Account\s+(\d{10,13})",
            full_text, re.IGNORECASE,
        )
        if not acc_match:
            acc_match = re.search(
                r"Account[:\s]*(\d{10,13})",
                full_text, re.IGNORECASE,
            )
        if acc_match:
            account_number = acc_match.group(1)

        # Detect account type from branding
        if "business banking" in full_text.lower():
            account_type = "Business Banking"
        elif "personal" in full_text.lower():
            account_type = "Personal"

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    # Sections printed after the transaction table whose rows are not
    # transactions on the account. "Account Effects Not Cleared" lists card
    # authorisations that have not settled: they carry a zero balance and
    # dates outside the statement period, so treating them as transactions
    # both breaks the running balance and overstates money actually spent.
    SECTION_TERMINATORS = [
        re.compile(r"^Account Effects Not Cleared", re.IGNORECASE),
    ]

    def _is_section_terminator(self, line: str) -> bool:
        return any(pattern.match(line) for pattern in self.SECTION_TERMINATORS)

    def _merge_continuation_lines(self, lines: list[str]) -> list[str]:
        """Merge continuation lines (no date prefix) with the preceding transaction.

        Stops at the first post-transaction section: everything after it
        describes uncleared effects rather than account activity.
        """
        merged = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if self._is_section_terminator(stripped):
                break
            if self._should_skip_line(stripped):
                continue
            if self._is_transaction_start(stripped):
                merged.append(stripped)
            elif merged:
                merged[-1] = merged[-1] + " " + stripped
        return merged

    def _find_column_run(self, rest: str):
        """Locate the Fee/Debit/Credit/Balance block in a merged line.

        The four columns are printed consecutively, separated only by
        whitespace, and any continuation text follows the balance. Taking the
        *last four* amounts on the line therefore breaks whenever a wrapped
        description carries its own figure::

            ... GMORE 0.00 221.18 0.00 131,083.75
            DISTRIBUTION D DURBAN ZA (ZAR 221.18) [No Base 1]

        Merged, that yields five amounts, and the last four shift every column
        by one: the balance becomes 221.18 and the credit becomes the balance.

        Anchoring on the first run of four consecutive amounts instead ignores
        anything the continuation contributes. Returns the run's match objects,
        or None when the line has no complete column block.
        """
        matches = list(self.AMOUNT_PATTERN.finditer(rest))
        if len(matches) < 4:
            return None

        run = [matches[0]]
        for prev, current in zip(matches, matches[1:]):
            between = rest[prev.end():current.start()]
            if between.strip() == "":
                run.append(current)
            else:
                if len(run) >= 4:
                    return run
                run = [current]
        return run if len(run) >= 4 else None

    def _parse_merged_transaction(
        self, line: str, prev_balance: float | None, date_format: str = "ymd8"
    ) -> dict | None:
        """Parse a single merged transaction line.

        Handles both YYYYMMDD and DD MMM YYYY date formats.
        For DD MMM YYYY (dmy4) format, uses explicit Fee/Debit/Credit/Balance columns.
        For YYYYMMDD (ymd8) format, uses balance progression.
        """
        trans_date_str = None
        rest = None
        is_dmy4 = False

        # Try YYYYMMDD format first
        date_match = self.DATE_PATTERN_YMD8.match(line)
        if date_match:
            trans_date_str = self._convert_date_ymd8(date_match.group(1))
            rest = line[date_match.end():].strip()
        else:
            # Try DD MMM YYYY format
            date_match = self.DATE_PATTERN_DMY4.match(line)
            if date_match:
                trans_date_str = self._convert_date_dmy4(
                    date_match.group(1), date_match.group(2), date_match.group(3)
                )
                rest = line[date_match.end():].strip()
                is_dmy4 = True
            else:
                return None

        # Check for BALANCE BROUGHT [FORWARD]. The My Statements export
        # truncates the label to "BALANCE BROUGHT", so requiring "FORWARD"
        # dropped the opening balance and left the first transaction
        # unverified.
        if rest and re.match(r"BALANCE\s+BROUGHT(\s+FORWARD)?\b", rest, re.IGNORECASE):
            amounts = self.AMOUNT_PATTERN.findall(rest)
            if amounts:
                balance = self._clean_amount(amounts[-1])
                return create_transaction_row(
                    "", "Balance Brought Forward", 0.0, 0.0, balance
                )
            return None

        # Try to find a second date (Effective Date) — same format as first
        if rest:
            eff_match = self.DATE_PATTERN_YMD8.match(rest)
            if not eff_match:
                eff_match = self.DATE_PATTERN_DMY4.match(rest)
            if eff_match:
                rest = rest[eff_match.end():].strip()

        if not rest:
            return None

        # Parse ALL amounts from the rest of the line
        amounts = self.AMOUNT_PATTERN.findall(rest)
        if len(amounts) < 1:
            return None

        # For dmy4 format, amounts are: Fee, Debit, Credit, Balance (always 4 columns)
        if is_dmy4:
            column_run = self._find_column_run(rest)
            if column_run is not None:
                fee, col_debit, col_credit, balance = (
                    self._clean_amount(m.group(0)) for m in column_run[-4:]
                )
                debit = fee + col_debit
                credit = col_credit

                description = re.sub(r"\s+", " ", rest[:column_run[-4].start()].strip())
                return create_transaction_row(
                    trans_date_str, description, debit, credit, balance
                )

        # For ymd8 format (or dmy4 with fewer than 4 amounts), use balance progression
        balance = self._clean_amount(amounts[-1])
        non_balance_values = [self._clean_amount(a) for a in amounts[:-1]]

        # Extract description (text before the first amount)
        first_amt_match = self.AMOUNT_PATTERN.search(rest)
        if first_amt_match:
            description = rest[:first_amt_match.start()].strip()
        else:
            description = rest
        description = re.sub(r"\s+", " ", description)

        # Determine debit/credit from balance change
        if prev_balance is None:
            net_non_balance = sum(non_balance_values)
            if net_non_balance > 0:
                debit, credit = 0.0, net_non_balance
            elif net_non_balance < 0:
                debit, credit = abs(net_non_balance), 0.0
            else:
                debit, credit = 0.0, 0.0
        else:
            net_change = round(balance - prev_balance, 2)
            if net_change > 0:
                debit, credit = 0.0, net_change
            elif net_change < 0:
                debit, credit = abs(net_change), 0.0
            else:
                net_non_balance = round(sum(non_balance_values), 2)
                if net_non_balance > 0:
                    debit, credit = 0.0, net_non_balance
                elif net_non_balance < 0:
                    debit, credit = abs(net_non_balance), 0.0
                else:
                    debit, credit = 0.0, 0.0

        return create_transaction_row(trans_date_str, description, debit, credit, balance)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Al Baraka statement.

        Collects all lines from all pages, merges continuation lines
        (those without a leading date) with their parent transaction,
        then parses each merged transaction.
        """
        date_format = self._detect_format()
        rows = []
        prev_balance = None

        # Collect all lines from all pages
        all_lines = []
        for page_text in self._iterate_pages():
            all_lines.extend(page_text.split("\n"))

        # Merge continuation lines with their parent transactions
        merged_lines = self._merge_continuation_lines(all_lines)

        for merged_line in merged_lines:
            row = self._parse_merged_transaction(merged_line, prev_balance, date_format)
            if row is None:
                continue

            rows.append(row)

            # Track balance for balance-progression-based debit/credit
            prev_balance = row["Balance"]

        return pd.DataFrame(rows)
