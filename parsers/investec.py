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
    # Amount pattern with optional R prefix and optional minus sign.
    # The trailing negative lookahead prevents matching the "19.01" fragment
    # of an embedded date like "19.01.2026" (which appears inside some
    # transaction descriptions) as a spurious amount.
    AMOUNT_PATTERN = re.compile(r"-?R?[\d,]+\.\d{2}(?![.\d])")
    # Date pattern: DDMMMYYYY with no spaces (e.g., "1MAY2026")
    DATE_PATTERN_NOSPACE = re.compile(
        r"(\d{1,2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{4})",
        re.IGNORECASE
    )
    # Amount pattern with DR/CR suffix (e.g., "896.73DR", "582,725.04CR")
    AMOUNT_DRCR_PATTERN = re.compile(
        r"([\d,]+\.\d{2})(DR|CR)", re.IGNORECASE
    )
    # Business Online (transaction-history export) amount: "R 429.99" is a
    # credit, "- R 429.99" a debit (note the space after R). group(1) present
    # means the amount is negative (a debit / an overdrawn balance).
    TH_AMOUNT_PATTERN = re.compile(r"(-\s*)?R\s+([\d,]+\.\d{2})")

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
        # Call-deposit statement footer (otherwise merged into the last
        # transaction's description by the text fallback).
        "growyoursavings",
        "moneyfundlinked",
        "calluson",
        "investecspecialistbank",
        "ombudsmanforbanking",
        # Corporate-card statement footers / summary-section headers.
        "cardtransactions",
        "onlinepayments",
        "beforemarch2018",
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

        # Return the customer-facing "Account number", never an "Internal" or
        # "Electronic" alias printed alongside it. A statement identifies one
        # real account by several numbers (e.g. the call-deposit account prints
        # "Account number 1100597029500" and "Electronic account number
        # 50019941900"); grouping needs a single, deterministic value per
        # account. The optional prefix group is captured so aliases can be
        # detected on both spaced ("Electronic account number") and squished
        # ("Electronicaccountnumber") text — the old (?<!\S) guard silently
        # failed on the squished form, splitting one account across statements.
        fallback = None
        for m in re.finditer(
            r"(internal|electronic)?\s*account\s*number\s*:?\s*(\d{6,})",
            first_page, re.IGNORECASE,
        ):
            is_alias = m.group(1) is not None
            value = m.group(2)
            if is_alias:
                fallback = fallback or value
                continue
            account_number = value
            break

        if account_number is None:
            account_number = fallback

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

        # These statements print two balance columns side by side — Capital and
        # Interest ("Date Description Amount Balance Rate% Days Amount
        # Balance"). Interest rows carry the *interest* balance, so folding
        # them into the capital chain reads as a wild jump (1,663,031.75DR to
        # 14,712.15DR) and breaks every subsequent row.
        if any(
            marker in description.lower().replace(" ", "")
            for marker in ("interestadvised", "revisedinterest", "interestaccrued")
        ):
            return None

        parsed = [(self._clean_amount(m.group(1)), m.group(2).upper()) for m in dr_cr_matches]

        if len(parsed) == 1:
            amt, typ = parsed[0]
            debit = 0.0
            credit = 0.0
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

    def _detect_statement_format(self) -> str:
        """Classify which Investec layout this statement uses.

        Investec ships several distinct layouts under the same brand; each
        needs different line grouping:

        - ``corporate_card`` — Corporate Card Account Statement. Clean, one
          transaction per line (single amount + running balance), plus
          duplicate summary sections that must be excluded.
        - ``call_deposit`` — Call Deposit / Electronic account. DR/CR amounts,
          dual balance columns, heavy multi-line wrapping; all data is in the
          page text.
        - ``business`` — Business / transactional account (the default).
        """
        cached = getattr(self, "_statement_format", None)
        if cached is not None:
            return cached
        first = self._extract_first_page_text().lower()
        if "investec business online" in first:
            # Transaction-history export (any account type). Signed "- R x"
            # amounts, most-recent-first ordering, no opening balance.
            fmt = "th_export"
        elif "corporate card account statement" in first:
            fmt = "corporate_card"
        elif "call deposit" in first or "electronic account number" in first:
            fmt = "call_deposit"
        else:
            fmt = "business"
        self._statement_format = fmt
        return fmt

    def _looks_like_transaction(self, text: str) -> bool:
        """Whether a table cell / line plausibly holds a transaction.

        Used to reject spurious pdfplumber tables — on many pages pdfplumber
        detects a one-cell "table" containing only the rotated date watermark
        (e.g. ``"31 May 2026"``). Such a cell has a date but no amount, so it
        is rejected and the page falls back to text extraction.
        """
        low = text.lower().replace(" ", "")
        if any(m in low for m in ("openingbalance", "closingbalance", "balancebroughtforward")):
            return True
        has_date = self._search_date(text) is not None
        has_amount = bool(
            self.AMOUNT_PATTERN.search(text) or self.AMOUNT_DRCR_PATTERN.search(text)
        )
        return has_date and has_amount

    def _iter_corporate_card_lines(self):
        """Yield transaction lines from a Corporate Card statement.

        Each transaction is a self-contained single line
        (``PostedDate TransDate Description Amount Balance``), so no
        multi-line merging is needed. Yields only lines inside the
        "Transaction detail" region — from "Balance brought forward" to
        "Closing Balance" — which excludes the duplicate "Card transactions"
        and "Online payments" summary sections that would otherwise inject
        phantom rows.
        """
        in_section = False
        for text in self._get_page_texts():
            if not text:
                continue
            for raw in text.split("\n"):
                line = raw.strip()
                if not line:
                    continue
                low = line.lower().replace(" ", "")
                if "balancebroughtforward" in low:
                    in_section = True
                    yield line  # opening balance
                    continue
                if "posteddate" in low and "transdate" in low:
                    in_section = True  # per-page column header
                    continue
                if not in_section:
                    continue
                if "closingbalance" in low:
                    in_section = False
                    continue
                if self._is_skip_line(line):
                    continue
                # Real transaction rows start with a date (DD MMM YYYY); the
                # rotated "01 February 2026" watermark uses a full month name
                # and is not matched, so it is naturally excluded.
                if self._find_date_at_start(line):
                    yield line

    def _parse_card_line(self, line: str, rows: list[dict]) -> dict | None:
        """Parse one Corporate Card transaction line.

        These statements print a single amount + running balance per row and
        come in two renderings — spaced (``1 Jan 2026``) and squished
        (``1Jul2025``) — both with plain (no DR/CR) amounts. Debit vs credit
        is inferred from the running-balance movement (per the agreed
        approach): a falling balance is a debit, a rising balance a credit.
        """
        line = line.strip()
        if not line:
            return None

        low = line.lower().replace(" ", "")
        if "balancebroughtforward" in low or "openingbalance" in low:
            amounts = self.AMOUNT_PATTERN.findall(line)
            if amounts:
                return create_transaction_row(
                    "", "Opening Balance", 0.0, 0.0, self._clean_amount(amounts[-1])
                )
            return None
        if "closingbalance" in low:
            return None

        date_info = self._find_date_at_start(line)
        if not date_info:
            return None
        date_str = self._parse_date_from_match(date_info)
        rest = line[date_info[0].end():].strip()

        # Optional second (transaction) date — same or different rendering.
        second = self._find_date_at_start(rest)
        if second:
            rest = rest[second[0].end():].strip()

        amounts = list(self.AMOUNT_PATTERN.finditer(rest))
        if not amounts:
            return None
        cleaned = [self._clean_amount(m.group(0)) for m in amounts]

        inline_desc = rest[:amounts[0].start()].strip()
        suffix_desc = rest[amounts[-1].end():].strip()
        description = " ".join(p for p in (inline_desc, suffix_desc) if p)

        balance = cleaned[-1]
        debit = credit = 0.0
        prev = rows[-1]["Balance"] if rows else None

        if len(cleaned) >= 2:
            amount = cleaned[-2]
            if prev is not None and round(balance - prev, 2) < 0:
                debit = amount
            else:
                credit = amount
        elif prev is not None:
            diff = round(balance - prev, 2)
            if diff < 0:
                debit = -diff
            else:
                credit = diff

        return create_transaction_row(date_str, description, debit, credit, balance)

    # ------------------------------------------------------------------
    # Business Online transaction-history export ("13. TH.pdf")
    # ------------------------------------------------------------------

    _TH_SKIP_MARKERS = (
        "investecbusinessonline",
        "accounts/details",
        "currency:",
        "filter",
        "sort:",
        "numberofresults",
        "entrydate",  # card export column header
        "transactionvaluedate",
        "transactiondatevaluedate",
        "statementreference",
        "currentbalance",
        "cardaccount:",
        "callaccount:",
        "transactionalaccount:",
        "printedon",
        "entity:",
        "downloadedon",
        "contactus",
        # Footer legal boilerplate wraps across several lines; each fragment
        # needs its own marker or it merges into the next transaction.
        "investeccorporate",
        "aregisteredcreditprovider",
        "codeofbanking",
        "bankingpractice",
        "ombudsmanforbanking",
        "copiesofthecode",
        "authorisedfinancial",
        "overthecounter",
        "memberofthejse",
        "registrationnumber",
        "visitinvestec",
    )

    def _th_is_skip(self, line: str) -> bool:
        low = line.lower().replace(" ", "")
        return any(m in low for m in self._TH_SKIP_MARKERS)

    def _th_split(self, line: str):
        """Locate the financial fields on a Business Online line.

        Returns ``(dates, txn_match, balance_match)`` or None. The transaction
        amount and running balance are always the rightmost two ``R``-amounts;
        the entry/value dates are the slash-dates to the left of them.
        """
        amts = list(self.TH_AMOUNT_PATTERN.finditer(line))
        if len(amts) < 2:
            return None
        txn, balance = amts[-2], amts[-1]
        dates = [
            d for d in self.DATE_PATTERN_SLASH.finditer(line)
            if d.start() < txn.start()
        ]
        if not dates:
            return None
        return dates, txn, balance

    def _th_is_dated(self, line: str) -> bool:
        """Whether a line is a transaction row (starts with a date + has amounts)."""
        return bool(
            self.DATE_PATTERN_SLASH.match(line.strip())
            and self._th_split(line)
        )

    def _th_has_inline_desc(self, line: str) -> bool:
        split = self._th_split(line)
        if not split:
            return False
        dates, txn, _ = split
        return len(line[dates[-1].end():txn.start()].strip()) > 0

    def _iter_th_lines(self):
        """Merge the export into one string per transaction.

        Descriptions (and the card-holder column) wrap onto the lines above
        and below the dated line. Same prefix/suffix heuristic as
        ``_merge_multiline``: a dated line that already carries an inline
        description leaves trailing undated lines to the next transaction;
        otherwise it absorbs them as a suffix.
        """
        clean = []
        for text in self._get_page_texts():
            for raw in text.split("\n"):
                line = raw.strip()
                if line and not self._th_is_skip(line):
                    clean.append(line)

        merged = []
        i, n = 0, len(clean)
        while i < n:
            line = clean[i]
            if self._th_is_dated(line):
                combined = line
                if not self._th_has_inline_desc(line):
                    while i + 1 < n and not self._th_is_dated(clean[i + 1]):
                        i += 1
                        combined += " " + clean[i]
                merged.append(combined)
            else:
                prefix = line
                while i + 1 < n and not self._th_is_dated(clean[i + 1]):
                    i += 1
                    prefix += " " + clean[i]
                if i + 1 < n:
                    i += 1
                    dated = clean[i]
                    combined = prefix + " " + dated
                    if not self._th_has_inline_desc(dated):
                        while i + 1 < n and not self._th_is_dated(clean[i + 1]):
                            i += 1
                            combined += " " + clean[i]
                    merged.append(combined)
            i += 1
        return merged

    def _parse_th_line(self, line: str) -> dict | None:
        """Parse one merged Business Online transaction string into a row."""
        split = self._th_split(line)
        if not split:
            return None
        dates, txn, bal = split
        d0 = dates[0]
        date_str = f"{d0.group(1)}/{d0.group(2)}/{d0.group(3)}"

        prefix_desc = line[:d0.start()].strip()
        inline_desc = line[dates[-1].end():txn.start()].strip()
        suffix_desc = line[bal.end():].strip()
        description = re.sub(
            r"\s+", " ",
            " ".join(p for p in (prefix_desc, inline_desc, suffix_desc) if p),
        ).strip()

        amount = self._clean_amount(txn.group(2))
        balance = self._clean_amount(bal.group(2))
        if bal.group(1) is not None:
            balance = -balance

        is_debit = txn.group(1) is not None
        debit = amount if is_debit else 0.0
        credit = 0.0 if is_debit else amount
        return create_transaction_row(date_str, description, debit, credit, balance)

    def _extract_th_transactions(self) -> pd.DataFrame:
        """Extract a Business Online export, restored to chronological order.

        The export is sorted most-recent-first, so the rows are reversed to
        oldest-first before returning — the running-balance chain (and the
        verification oracle) both read top-to-bottom.
        """
        rows = [
            row for line in self._iter_th_lines()
            if (row := self._parse_th_line(line))
        ]
        rows.reverse()
        return pd.DataFrame(rows)

    def _iter_transaction_lines(self):
        """Yield transaction text lines from the PDF, routed by layout.

        Business statements prefer pdfplumber table extraction (which groups
        wrapped descriptions); corporate-card and call-deposit statements keep
        all data in the page text and are parsed directly, since spurious
        watermark "tables" would otherwise shadow the real transactions.
        """
        fmt = self._detect_statement_format()

        if fmt == "corporate_card":
            yield from self._iter_corporate_card_lines()
            return

        if fmt == "call_deposit":
            for text in self._get_page_texts():
                if not text:
                    continue
                for line in self._merge_multiline(text.split("\n")):
                    yield line
            return

        # business: table-preferred, ignoring spurious tables and falling
        # back to text per-page when a page yields no real transaction rows.
        with pdfplumber.open(self.pdf_file) as pdf:
            for page in pdf.pages:
                yielded_any = False
                for table in page.extract_tables() or []:
                    for row in table:
                        if not row or not row[0]:
                            continue
                        cell = row[0].replace("\n", " ").strip()
                        low = cell.lower().replace(" ", "")
                        if "transactiondate" in low or "transdate" in low:
                            continue
                        if self._looks_like_transaction(cell):
                            yielded_any = True
                            yield cell
                if not yielded_any:
                    text = page.extract_text()
                    if text:
                        for line in self._merge_multiline(text.split("\n")):
                            yield line

        self._reset_file()

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Investec statement."""
        fmt = self._detect_statement_format()
        if fmt == "th_export":
            return self._extract_th_transactions()

        rows = []
        parse_line = (
            self._parse_card_line
            if fmt == "corporate_card"
            else self._parse_transaction_line
        )
        for line in self._iter_transaction_lines():
            txn = parse_line(line, rows)
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
