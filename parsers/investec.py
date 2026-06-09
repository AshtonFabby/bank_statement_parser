"""Investec Bank statement parser."""

import re

import pandas as pd
import pdfplumber

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row, normalize_amount_string


class InvestecParser(BaseBankParser):
    """Parser for Investec statements."""

    BANK_NAME = "Investec"
    BANK_ID = "investec"
    DETECTION_KEYWORDS = [("investec", 5)]

    # Date pattern: DD MMM YYYY (e.g., "1 May 2025", "30 Apr 2025")
    DATE_PATTERN = re.compile(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
        re.IGNORECASE
    )
    # Date pattern: DD/MM/YYYY (e.g., "01/05/2025")
    DATE_PATTERN_SLASH = re.compile(
        r"(\d{2})/(\d{2})/(\d{4})"
    )
    # Amount pattern with optional R prefix and optional minus sign
    AMOUNT_PATTERN = re.compile(r"-?R?[\d,]+\.\d{2}")
    # Date pattern: DDMMMYYYY with no spaces (e.g., "1MAY2026")
    DATE_PATTERN_NOSPACE = re.compile(
        r"(\d{1,2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{4})",
        re.IGNORECASE
    )
    # Amount pattern with DR/CR suffix (e.g., "896.73DR", "582,725.04CR")
    AMOUNT_DRCR_PATTERN = re.compile(
        r"([\d,]+\.\d{2})(DR|CR)", re.IGNORECASE
    )

    # Lines to skip during text-based parsing
    _SKIP_MARKERS = [
        "monthlyaccountstatement",
        "shouldyouhaveanyqueries",
        "transactiondate", "transdate", "valuedate",
        "transactiondescription",
        "investeccorporate",
        "accountnumber:",
        "statementperiod:",
        "statementtype:",
        "ombudsman",
        "capitalinterest",
        "datedescriptionamountbalance",
        "shouldyoudisagree",
        "corporate&institutionalbanking",
        "aregisteredcreditprovider",
        "australiabotswanacanada",
        "ofpwcourexternalauditors",
    ]

    def _is_skip_line(self, line: str) -> bool:
        """Check if a line is a header, footer, or other non-transaction text."""
        stripped = line.strip()
        if not stripped:
            return True
        if stripped == ".":
            return True
        if re.match(r"^Pg\d+of\d+$", stripped, re.IGNORECASE):
            return True
        if re.match(r"^(Transaction|Trans)\s+date", stripped, re.IGNORECASE):
            return True
        if stripped.lower() in ("date", "description"):
            return True
        line_nosp = stripped.lower().replace(" ", "")
        for marker in self._SKIP_MARKERS:
            if marker in line_nosp:
                return True
        return False

    def _find_date_at_start(self, line: str):
        """Check if line starts with a date, returning (match, kind, day, month, year) or None."""
        line = line.strip()
        match = self.DATE_PATTERN_SLASH.match(line)
        if match:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return (match, "slash", day, month, year)
        match = self.DATE_PATTERN_NOSPACE.match(line)
        if match:
            day = int(match.group(1))
            month_str = match.group(2)
            year = match.group(3)
            month_num = int(MONTH_MAP.get(month_str.lower(), "01"))
            return (match, "nospace", day, month_num, int(year))
        match = self.DATE_PATTERN.match(line)
        if match:
            day, month_str, year = match.group(1), match.group(2), match.group(3)
            month_num = int(MONTH_MAP.get(month_str.lower(), "01"))
            return (match, "named", int(day), month_num, year)
        return None

    def _search_date(self, text: str):
        """Search for a date anywhere in text. Returns (match, kind, day, month, year) or None."""
        slash = self.DATE_PATTERN_SLASH.search(text)
        if slash:
            day, month, year = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
            return (slash, "slash", day, month, year)
        nospace = self.DATE_PATTERN_NOSPACE.search(text)
        if nospace:
            day = int(nospace.group(1))
            month_str = nospace.group(2)
            year = nospace.group(3)
            month_num = int(MONTH_MAP.get(month_str.lower(), "01"))
            return (nospace, "nospace", day, month_num, int(year))
        named = self.DATE_PATTERN.search(text)
        if named:
            day = int(named.group(1))
            month_str = named.group(2)
            year = named.group(3)
            month_num = int(MONTH_MAP.get(month_str.lower(), "01"))
            return (named, "named", day, month_num, year)
        return None

    def _merge_multiline(self, lines: list[str]) -> list[str]:
        """Join multi-line description fragments in text-based extraction.

        Fallback for pages without table extraction.  Uses the heuristic that
        if a dated line already has an inline description (text between the
        last date and the first amount), the following undated line is a
        *prefix* for the next transaction; otherwise it is a *suffix* for the
        current one.
        """
        clean = []
        for line in lines:
            if not self._is_skip_line(line):
                clean.append(line.strip())

        if not clean:
            return []

        merged = []
        i = 0
        while i < len(clean):
            line = clean[i]
            date_info = self._find_date_at_start(line)

            if date_info:
                combined = line
                # Determine if trailing undated lines are suffixes or prefixes
                if date_info[1] == "nospace":
                    # Nospace format: trailing lines are always suffixes
                    while i + 1 < len(clean) and not self._find_date_at_start(clean[i + 1]):
                        i += 1
                        combined += " " + clean[i]
                    merged.append(combined)
                elif self._line_has_inline_desc(line, date_info):
                    # Inline description present → trailing lines are prefixes
                    # for the *next* transaction, so stop merging here.
                    merged.append(combined)
                else:
                    # No inline description → trailing lines are suffixes
                    while i + 1 < len(clean) and not self._find_date_at_start(clean[i + 1]):
                        i += 1
                        combined += " " + clean[i]
                    merged.append(combined)
            else:
                # Undated line – collect prefix until the next dated line
                prefix = line
                while i + 1 < len(clean) and not self._find_date_at_start(clean[i + 1]):
                    i += 1
                    prefix += " " + clean[i]

                if i + 1 < len(clean):
                    i += 1
                    dated = clean[i]
                    combined = prefix + " " + dated
                    date_info = self._find_date_at_start(dated)
                    if date_info:
                        if date_info[1] == "nospace":
                            while i + 1 < len(clean) and not self._find_date_at_start(clean[i + 1]):
                                i += 1
                                combined += " " + clean[i]
                        elif not self._line_has_inline_desc(dated, date_info):
                            while i + 1 < len(clean) and not self._find_date_at_start(clean[i + 1]):
                                i += 1
                                combined += " " + clean[i]
                    merged.append(combined)

            i += 1

        return merged

    def _line_has_inline_desc(self, line: str, date_info: tuple) -> bool:
        """Check if a dated transaction line has description text between
        the last date and the first amount (i.e. is not just a stub line
        whose description wraps to neighbouring lines)."""
        _, _, _, _, _ = date_info
        match = date_info[0]
        rest = line[match.end():].strip()

        # Skip a possible second date
        d2 = self.DATE_PATTERN_SLASH.match(rest)
        if d2:
            rest = rest[d2.end():].strip()
        else:
            d2 = self.DATE_PATTERN.match(rest)
            if d2:
                rest = rest[d2.end():].strip()

        first_amt = self.AMOUNT_PATTERN.search(rest)
        if not first_amt:
            return False
        inline = rest[:first_amt.start()].strip()
        return len(inline) > 0

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Investec statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Prefer the customer-facing account number (not internal).
        # e.g. "Accountnumber 40005077141" rather than
        # "Internalaccountnumber 1300597865580".
        matches = re.finditer(
            r"(?<!\S)(?:Internal\s*)?[Aa]ccount\s*number\s*(\d+)",
            first_page, re.IGNORECASE
        )
        for m in matches:
            is_internal = "internal" in m.group(0).lower()
            value = m.group(1)
            if is_internal:
                continue
            if account_number is None or len(value) >= 10:
                account_number = value

        if account_number is None:
            acc_match = re.search(
                r"[Aa]ccount\s*number\s*(\d{6,})", first_page, re.IGNORECASE
            )
            if acc_match:
                account_number = acc_match.group(1)

        if "private bank" in first_page.lower():
            account_type = "Private Bank Account"

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean amount string to float."""
        return normalize_amount_string(amt)

    def _parse_date_from_match(self, date_info: tuple) -> str:
        """Convert a date match tuple to DD/MM/YYYY format."""
        _, kind, day, month, year = date_info
        return f"{day:02d}/{month:02d}/{year}"

    def _parse_transaction_line(self, line: str, rows: list[dict]) -> dict | None:
        """Parse a single merged transaction line into a row dict.

        Args:
            line: The full text of one transaction (may include prefix/suffix
                  description fragments).
            rows: Previously parsed rows (needed for debit/credit inference
                  from balance changes).

        Returns:
            A transaction row dict, or None if the line couldn't be parsed.
        """
        line = line.strip()
        if not line:
            return None

        # Opening / Closing balance
        line_nosp = line.lower().replace(" ", "")
        if "openingbalance" in line_nosp or "balancebroughtforward" in line_nosp:
            amounts = self.AMOUNT_PATTERN.findall(line)
            if amounts:
                balance = self._clean_amount(amounts[-1])
                return create_transaction_row(
                    "", "Opening Balance", 0.0, 0.0, balance
                )
            return None

        if "closingbalance" in line_nosp:
            return None

        # Find first date
        date_info = self._find_date_at_start(line)
        if not date_info:
            date_info = self._search_date(line)
        if not date_info:
            return None

        match = date_info[0]
        kind = date_info[1]
        date_str = self._parse_date_from_match(date_info)

        # Text before the first date → description prefix
        prefix_desc = line[:match.start()].strip()

        rest = line[match.end():].strip()

        # Optional second date (value date) - skip for nospace format
        second_info = None
        if kind != "nospace":
            slash2 = self.DATE_PATTERN_SLASH.match(rest)
            if slash2:
                second_info = (slash2, "slash",
                               int(slash2.group(1)), int(slash2.group(2)), int(slash2.group(3)))
                rest = rest[slash2.end():].strip()
            else:
                named2 = self.DATE_PATTERN.match(rest)
                if named2:
                    d2 = int(named2.group(1))
                    m2_str = named2.group(2)
                    y2 = named2.group(3)
                    m2_num = int(MONTH_MAP.get(m2_str.lower(), "01"))
                    second_info = (named2, "named", d2, m2_num, y2)
                    rest = rest[named2.end():].strip()

        if kind == "nospace":
            return self._parse_nospace_line(date_str, match, prefix_desc, rest, rows)

        # Find amounts
        amounts = self.AMOUNT_PATTERN.findall(rest)
        if not amounts:
            return None

        cleaned = [self._clean_amount(a) for a in amounts]

        # Description: text between (last date or start of rest) and first amount,
        # plus any text after the last amount
        first_amt = self.AMOUNT_PATTERN.search(rest)
        if first_amt:
            inline_desc = rest[:first_amt.start()].strip()
            last_amt = None
            for m in self.AMOUNT_PATTERN.finditer(rest):
                last_amt = m
            suffix_desc = rest[last_amt.end():].strip() if last_amt else ""
        else:
            inline_desc = rest
            suffix_desc = ""

        parts = []
        if prefix_desc:
            parts.append(prefix_desc)
        if inline_desc:
            parts.append(inline_desc)
        if suffix_desc:
            parts.append(suffix_desc)
        description = " ".join(parts)

        # Debit / Credit / Balance
        debit = 0.0
        credit = 0.0
        balance = cleaned[-1] if cleaned else 0.0

        if len(cleaned) >= 3:
            debit = cleaned[-3]
            credit = cleaned[-2]
        elif len(cleaned) == 2:
            if rows:
                prev = rows[-1]["Balance"]
                diff = balance - prev
                if diff < 0:
                    debit = cleaned[0]
                else:
                    credit = cleaned[0]
            else:
                desc_lower = description.lower()
                if any(w in desc_lower for w in ["fee", "withdrawal", "payment", "purchase", "debit"]):
                    debit = cleaned[0]
                else:
                    credit = cleaned[0]
        elif len(cleaned) == 1:
            if rows:
                prev = rows[-1]["Balance"]
                diff = balance - prev
                if diff < 0:
                    debit = abs(diff)
                else:
                    credit = diff

        return create_transaction_row(date_str, description, debit, credit, balance)

    def _parse_nospace_line(self, date_str: str, date_match, prefix_desc: str, rest: str, rows: list[dict]) -> dict | None:
        """Parse a transaction line in nospace format (DDMMMYYYY dates, DR/CR amounts)."""
        dr_cr_matches = list(self.AMOUNT_DRCR_PATTERN.finditer(rest))
        if not dr_cr_matches:
            return None

        first_amt_match = dr_cr_matches[0]
        inline_desc = rest[:first_amt_match.start()].strip()

        last_amt_match = dr_cr_matches[-1]
        suffix_desc = rest[last_amt_match.end():].strip()

        parts = []
        if prefix_desc:
            parts.append(prefix_desc)
        if inline_desc:
            parts.append(inline_desc)
        if suffix_desc:
            parts.append(suffix_desc)
        description = " ".join(parts)

        if "interestadvised" in description.lower().replace(" ", ""):
            return None

        parsed = [(self._clean_amount(m.group(1)), m.group(2).upper()) for m in dr_cr_matches]

        if len(parsed) == 1:
            amt, typ = parsed[0]
            if typ == "DR":
                debit = amt
                balance = -amt
            else:
                credit = amt
                balance = amt
            return create_transaction_row(date_str, description, debit, credit, balance)

        balance_amount, balance_type = parsed[-1]
        balance = balance_amount if balance_type == "CR" else -balance_amount

        non_balance = parsed[:-1]
        if len(non_balance) > 1 and "interest" in description.lower() and non_balance[0][1] == "CR" and non_balance[0][0] < 100:
            non_balance = non_balance[1:]

        debit = 0.0
        credit = 0.0
        for amt, typ in non_balance:
            if typ == "DR":
                debit += amt
            else:
                credit += amt

        return create_transaction_row(date_str, description, debit, credit, balance)

    def _iter_transaction_lines(self):
        """Yield transaction text lines from the PDF.

        Uses table extraction when available (which correctly groups
        multi-line description rows), falling back to text-based merging
        for pages without detectable tables.
        """
        with pdfplumber.open(self.pdf_file) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            if not row or not row[0]:
                                continue
                            cell = row[0].replace("\n", " ").strip()
                            # Skip table header rows
                            low = cell.lower().replace(" ", "")
                            if "transactiondate" in low or "transdate" in low:
                                continue
                            if cell:
                                yield cell
                else:
                    text = page.extract_text()
                    if text:
                        merged = self._merge_multiline(text.split("\n"))
                        for line in merged:
                            yield line

        self._reset_file()

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Investec statement."""
        rows = []
        for line in self._iter_transaction_lines():
            txn = self._parse_transaction_line(line, rows)
            if txn:
                rows.append(txn)
        if rows:
            self._correct_opening_balance_sign(rows)
        return pd.DataFrame(rows)

    def _correct_opening_balance_sign(self, rows: list[dict]) -> None:
        """Post-parse: fix opening balance sign if parsed wrong.

        Some statements show opening balance as ``-R48,187.57`` where the
        ``-`` is an overdraft indicator (not arithmetic), while others use it
        as a true negative sign.  We validate against the first real
        transaction — correcting its debit/credit if the opening sign flip
        requires it.
        """
        if len(rows) < 2:
            return
        opening = rows[0]
        if opening.get("Description", "").strip() != "Opening Balance":
            return
        first = rows[1]
        opening_parsed = opening["Balance"]
        first_balance = first["Balance"]
        total_amt = first["Debit"] + first["Credit"]

        expected = round(opening_parsed + first["Credit"] - first["Debit"], 2)
        if abs(expected - first_balance) < 0.01:
            return

        flipped = -opening_parsed
        if abs(round(flipped + total_amt - first_balance, 2)) < 0.01:
            opening["Balance"] = flipped
            first["Credit"] = total_amt
            first["Debit"] = 0.0
            return
        if abs(round(flipped - total_amt - first_balance, 2)) < 0.01:
            opening["Balance"] = flipped
            first["Debit"] = total_amt
            first["Credit"] = 0.0
