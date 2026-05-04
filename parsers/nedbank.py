"""Nedbank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, normalize_amount_string


class NedbankParser(BaseBankParser):
    """Parser for Nedbank statements."""

    BANK_NAME = "Nedbank"
    BANK_ID = "nedbank"
    DETECTION_KEYWORDS = ["nedbank", "statement enquiry", "nedbank.co.za", "transaction listing", "enc *", "profile number"]

    # Date patterns
    DATE_PATTERN = re.compile(r"(\d{2}/\d{2}/\d{4})")
    DATE_PATTERN_YMD = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")  # Start of line
    # Amount patterns - comma-separated (standard) and space-separated (enquiry)
    AMOUNT_PATTERN = re.compile(r"(?<!\d)-?\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)")
    AMOUNT_PATTERN_SPACES = re.compile(r"(?<!\d)-?\d{1,3}(?: \d{3})+\.\d{2}(?!\d)")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Nedbank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        lines = first_page.split("\n")

        # Look for account number in the Account summary table
        for line in lines:
            line_lower = line.lower()
            if "current account" in line_lower:
                acc_match = re.search(r"(\d{10,13})", line)
                if acc_match:
                    account_number = acc_match.group(1)
                    account_type = "Current Account"
                    break
            elif "savings account" in line_lower:
                acc_match = re.search(r"(\d{10,13})", line)
                if acc_match:
                    account_number = acc_match.group(1)
                    account_type = "Savings Account"
                    break
            elif "business account" in line_lower:
                acc_match = re.search(r"(\d{10,13})", line)
                if acc_match:
                    account_number = acc_match.group(1)
                    account_type = "Business Account"
                    break

        # Try "Account number:" pattern
        if not account_number:
            acc_match = re.search(
                r"Account\s*number[:\s]*(\d{10,13})",
                first_page, re.IGNORECASE
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Try Statement Enquiry format: "Account number: 1313080209"
        if not account_number:
            acc_match = re.search(
                r"Account\s*(?:number|description)[:\s]*.*?(\d{10,13})",
                first_page, re.IGNORECASE
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Last fallback - any 10-13 digit number
        if not account_number:
            acc_match = re.search(r"\b(\d{10,13})\b", first_page)
            if acc_match:
                account_number = acc_match.group(1)

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean Nedbank amount string to float."""
        return normalize_amount_string(amt)

    def _detect_format(self, text: str) -> str:
        """Detect which Nedbank format is being used.

        Checks the first page text plus additional pages via _iterate_pages
        since cover pages may not contain the header markers.
        """
        combined = text.lower()
        if "statement enquiry" in combined:
            return "enquiry"
        if "tran list no" in combined or "tranlistno" in combined:
            return "business"
        if "bank charges for the period" in combined:
            return "charges"
        # Check additional pages for format markers (cover page may not have them)
        for page_text in self._iterate_pages():
            page_lower = page_text.lower()
            if "statement enquiry" in page_lower:
                return "enquiry"
            if "tran list no" in page_lower or "tranlistno" in page_lower:
                return "business"
            if "bank charges for the period" in page_lower:
                return "charges"
        self._reset_file()
        return "standard"

    def _find_amounts(self, line: str, format_type: str) -> list:
        """Find all amounts in a line, handling both comma and space separators.

        For enquiry format, space-separated amounts like '400 000.00' and
        '-8 942 165.72' are used. To avoid overlapping matches (e.g., '000.00'
        appearing inside '400 000.00'), we merge the results by position and
        remove any comma-pattern match that is a substring of a space-pattern match.
        """
        if format_type != "enquiry":
            return self.AMOUNT_PATTERN.findall(line)

        # For enquiry format, find both patterns and merge by position
        all_matches = list(self.AMOUNT_PATTERN_SPACES.finditer(line))
        comma_matches = list(self.AMOUNT_PATTERN.finditer(line))

        # Remove comma matches that are substrings of space matches
        space_spans = [(m.start(), m.end()) for m in all_matches]
        for cm in comma_matches:
            is_substring = any(cs <= cm.start() and ce >= cm.end() for cs, ce in space_spans)
            if not is_substring:
                all_matches.append(cm)

        # Sort by position
        all_matches.sort(key=lambda m: m.start())
        return [m.group(0) for m in all_matches]

    def _is_description_fragment(self, line: str, format_type: str = "standard") -> bool:
        """Check if a line is a description-only fragment (no date, no amounts)."""
        if not line:
            return False
        if self.DATE_PATTERN.search(line):
            return False
        if self.AMOUNT_PATTERN.search(line):
            return False
        if format_type == "enquiry" and self.AMOUNT_PATTERN_SPACES.search(line):
            return False
        skip_keywords = [
            "Tran list no", "Tranlistno", "Narrative Description",
            "NarrativeDescription",
            "Opening balance", "Openingbalance",
            "Balance carried forward", "Balancecarriedforward",
            "BROUGHT FORWARD", "Balancebroughtforward",
            "CARRIED FORWARD", "Balancecarriedforward",
            "Statement Enquiry", "Account description", "Account number",
            "Accountdescription",
            "VAT #", "Profile name", "User name", "Profile number", "User ID",
            "Date:", "Time:", "Notice", "ENC *", "PROVISIONAL STATEMENT",
            "Date Transactions", "Debit Credit", "Debits Credits",
            "Fees(R) Debits(R) Credits(R) Balance(R)",
            "Debits(R) Credits(R) Balance(R)",
        ]
        line_upper = line.upper()
        for kw in skip_keywords:
            if kw.upper() in line_upper:
                return False
        if re.match(r'^\d{3,6}$', line.strip()):
            return False
        return True

    def _merge_description_fragments(self, lines: list, format_type: str = "standard") -> list:
        """Merge description-only lines with adjacent transaction lines.

        PDF text extraction splits long descriptions across lines.
        Fragments before a bare transaction line are PREFIXES.
        Fragments right after a just-merged transaction are SUFFIXES.
        """
        result = []
        fragment_buffer = ""
        just_merged = False
        amt_pattern = self.AMOUNT_PATTERN_SPACES if format_type == "enquiry" else self.AMOUNT_PATTERN

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if self._is_description_fragment(stripped, format_type):
                if just_merged and result:
                    last = result[-1]
                    amt_match = amt_pattern.search(last) or self.AMOUNT_PATTERN.search(last)
                    if amt_match:
                        result[-1] = last[:amt_match.start()] + " " + stripped + last[amt_match.start():]
                    else:
                        result[-1] = last + " " + stripped
                else:
                    fragment_buffer = (fragment_buffer + " " + stripped).strip() if fragment_buffer else stripped
                continue

            just_merged = False

            if self.DATE_PATTERN.search(stripped):
                if fragment_buffer:
                    date_match = self.DATE_PATTERN.search(stripped)
                    insert_pos = date_match.end() if date_match else 0
                    enc_match = re.match(r'^(\d{3,6}\s+)', stripped)
                    if enc_match:
                        insert_pos = max(insert_pos, enc_match.end())
                    stripped = stripped[:insert_pos] + " " + fragment_buffer + stripped[insert_pos:]
                    fragment_buffer = ""
                    just_merged = True
                result.append(stripped)
            else:
                if fragment_buffer and result:
                    result[-1] = result[-1] + " " + fragment_buffer
                    fragment_buffer = ""
                result.append(stripped)

        if fragment_buffer and result:
            result[-1] = result[-1] + " " + fragment_buffer

        return result

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Nedbank statement.

        Handles multiple formats:
        1. Standard: Date | Description | Debit | Credit | Balance
        2. Business: Date | Description | Fees | Debits | Credits | Balance
        3. Statement Enquiry: Date | Transactions | Debit/Credit | Balance (space-separated)
        4. Bank charges: Tran list no | Date | Description | Debits (R) | Credits (R) | Balance (R)
        """
        rows = []
        first_page = self._extract_first_page_text()
        format_type = self._detect_format(first_page)

        # Collect all lines and merge description fragments before parsing
        all_lines = []
        for page_text in self._iterate_pages():
            all_lines.extend(page_text.split("\n"))
        all_lines = self._merge_description_fragments(all_lines, format_type)

        for line in all_lines:
            line = line.strip()

            # Skip header and special rows
            if not line:
                continue
            line_upper = line.upper()
            if "TRANLISTNO" in line_upper or "TRAN LIST NO" in line_upper:
                continue
            if "NARRATIVE DESCRIPTION" in line_upper or "NARRATIVEDESCRIPTION" in line_upper:
                continue
            if "Date" in line and ("Transactions" in line or "Description" in line):
                continue
            if "Debits" in line and "Credits" in line:
                continue
            if "FEES(R)" in line_upper and "DEBITS(R)" in line_upper:
                continue
            # Opening balance (with or without space)
            if "OPENINGBALANCE" in line_upper.replace(" ", ""):
                amounts = self._find_amounts(line, format_type)
                if amounts:
                    balance = self._clean_amount(amounts[-1])
                    rows.append(create_transaction_row(
                        "", "Opening Balance", 0.0, 0.0, balance
                    ))
                continue
            if "BALANCECARRIEDFORWARD" in line_upper.replace(" ", ""):
                continue
            if "BALANCEBROUGHTFORWARD" in line_upper.replace(" ", ""):
                continue
            if "BROUGHT FORWARD" in line_upper or "CARRIED FORWARD" in line_upper:
                continue
            if "PROVISIONAL STATEMENT" in line_upper:
                continue

            # Handle ATM CASH / BR CASH companion lines
            cash_pattern = re.search(r'(?:ATM CASH|BR CASH) R[\d,]+\.\d{2}\s+FEE', line)
            if cash_pattern and "TRANSACTION FEE" not in line:
                fee_match = re.search(r'FEE\s+(-?\d{1,3}(?:,\d{3})*\.\d{2})', line)
                if fee_match:
                    fee_val = self._clean_amount(fee_match.group(1))
                    fee_debit = abs(fee_val)
                    amounts = self._find_amounts(line, format_type)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        txn_date_match = re.search(r"^\d{3,6}\s+(\d{2}/\d{2}/\d{4})", line)
                        if txn_date_match:
                            date_str = txn_date_match.group(1)
                        else:
                            date_match = self.DATE_PATTERN.search(line)
                            date_str = date_match.group(1) if date_match else None
                        if date_str:
                            rows.append(create_transaction_row(
                                date_str, "ATM CASH FEE", fee_debit, 0.0, balance
                            ))
                continue

            # Find date in line
            date_str = None
            rest_start = 0

            txn_date_match = re.search(r"^\d{3,6}\s+(\d{2}/\d{2}/\d{4})", line)
            if txn_date_match:
                date_str = txn_date_match.group(1)
                rest_start = txn_date_match.end()
            else:
                date_match = self.DATE_PATTERN.match(line)
                if date_match:
                    date_str = date_match.group(1)
                    rest_start = date_match.end()
                else:
                    date_search = self.DATE_PATTERN.search(line)
                    if date_search:
                        date_str = date_search.group(1)
                        rest_start = date_search.end()
                    else:
                        continue

            # Find all amounts using format-appropriate pattern
            amounts = self._find_amounts(line, format_type)
            if len(amounts) < 1:
                continue

            # Clean amounts
            cleaned_amounts = []
            is_negative = []
            for amt in amounts:
                val = self._clean_amount(amt)
                cleaned_amounts.append(abs(val))
                is_negative.append(val < 0 or amt.strip().startswith("-"))

            # Get description
            rest_of_line = line[rest_start:].strip()
            first_amt_pattern = self.AMOUNT_PATTERN_SPACES if format_type == "enquiry" else self.AMOUNT_PATTERN
            first_amount_match = first_amt_pattern.search(rest_of_line)
            if not first_amount_match:
                first_amount_match = self.AMOUNT_PATTERN.search(rest_of_line)
            if first_amount_match:
                description = rest_of_line[:first_amount_match.start()].strip()
            else:
                description = rest_of_line
            description = re.sub(r'\s+', ' ', description)

            # Handle VAT lines
            is_vat_line = "VAT" in description and "= R" in line
            if is_vat_line:
                vat_amount_match = re.search(r'=\s*R\d{1,3}(?:,\d{3})*\.\d{2}', line)
                if vat_amount_match:
                    description = rest_of_line[:vat_amount_match.end()].strip()
                if len(cleaned_amounts) >= 2:
                    new_amounts = []
                    for amt in amounts:
                        amt_val = self._clean_amount(amt)
                        if f"R{amt}" not in description:
                            new_amounts.append(amt_val)
                    if len(new_amounts) >= 1:
                        cleaned_amounts = [abs(a) for a in new_amounts]
                        is_negative = [a < 0 for a in new_amounts]

            # Balance is always last
            balance = cleaned_amounts[-1] if cleaned_amounts else 0.0
            if is_negative and is_negative[-1]:
                balance = -balance

            debit = 0.0
            credit = 0.0

            # Parse amounts based on format
            if format_type in ["business", "charges"]:
                # Business format column order: Fees(R) Debits(R) Credits(R) Balance(R)
                # The Debits column ALREADY includes the fees amount.
                # The Fees column is just a sub-component breakdown.
                # So total debit = Debits (not Debits + Fees).
                if len(cleaned_amounts) >= 4:
                    fees = cleaned_amounts[-4]
                    debits = cleaned_amounts[-3]
                    credits = cleaned_amounts[-2]
                    debit = debits
                    credit = credits
                elif len(cleaned_amounts) == 3:
                    # Could be: Fees Debits Balance OR Debits Credits Balance
                    # Use balance progression to disambiguate
                    if rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        # Check if first amount is a small fee (typically < 500)
                        first_amt = cleaned_amounts[-3]
                        main_amt = cleaned_amounts[-2]
                        # If diff matches main_amt as debit (negative change)
                        if abs(diff - (-main_amt)) < 0.02:
                            # Fees + Debit + Balance
                            debit = main_amt
                            credit = 0.0
                        elif abs(diff - main_amt) < 0.02:
                            # Fees + Credit + Balance
                            debit = first_amt
                            credit = main_amt
                        elif abs(diff - (-first_amt - main_amt)) < 0.02:
                            # Both are debits (unusual but possible)
                            debit = first_amt + main_amt
                            credit = 0.0
                        elif abs(diff - (main_amt - first_amt)) < 0.02:
                            # Net credit after fee deduction
                            credit = main_amt
                            debit = first_amt
                        else:
                            # Fallback: treat as Debits Credits Balance
                            debit = cleaned_amounts[-3]
                            credit = cleaned_amounts[-2]
                    else:
                        debit = cleaned_amounts[-3]
                        credit = cleaned_amounts[-2]
                elif len(cleaned_amounts) == 2:
                    if rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff > 0:
                            credit = cleaned_amounts[0]
                        else:
                            debit = cleaned_amounts[0]
                    else:
                        debit = cleaned_amounts[0]
                elif len(cleaned_amounts) == 1 and rows:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

            elif format_type == "enquiry":
                # Enquiry format: signed amount + balance (space-separated)
                # or: debit credit balance (3 amounts when both present)
                # BROUGHT FORWARD / CARRIED FORWARD with 0.00 amounts already handled above
                if len(cleaned_amounts) == 3:
                    # 0.00 debit/credit indicator + debit/credit + balance
                    # or: debit + credit + balance
                    # Use is_negative to determine which
                    if is_negative[0] and cleaned_amounts[0] == 0:
                        # 0.00 is neutral, the second amount tells us
                        if is_negative[1]:
                            debit = cleaned_amounts[1]
                        else:
                            credit = cleaned_amounts[1]
                    elif is_negative[1] and cleaned_amounts[1] == 0:
                        if is_negative[0]:
                            debit = cleaned_amounts[0]
                        else:
                            credit = cleaned_amounts[0]
                    elif is_negative[0] and not is_negative[1]:
                        debit = cleaned_amounts[0]
                        credit = cleaned_amounts[1]
                    elif not is_negative[0] and is_negative[1]:
                        credit = cleaned_amounts[0]
                        debit = cleaned_amounts[1]
                    else:
                        # Both positive or both negative - use balance change
                        if rows:
                            prev_balance = rows[-1]["Balance"]
                            diff = balance - prev_balance
                            if diff < 0:
                                debit = cleaned_amounts[0] + cleaned_amounts[1]
                            else:
                                credit = cleaned_amounts[0] + cleaned_amounts[1]
                        else:
                            credit = cleaned_amounts[0]
                elif len(cleaned_amounts) == 2:
                    amt_val = cleaned_amounts[0]
                    if is_negative[0]:
                        debit = amt_val
                    else:
                        credit = amt_val
                elif len(cleaned_amounts) == 1:
                    if rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff

            else:
                # Standard format: Debit Credit Balance or single amount + balance
                if len(cleaned_amounts) == 3:
                    debit = cleaned_amounts[-3]
                    credit = cleaned_amounts[-2]
                elif len(cleaned_amounts) == 2:
                    amt_val = cleaned_amounts[0]
                    if rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff > 0:
                            credit = amt_val
                        else:
                            debit = amt_val
                    else:
                        desc_lower = description.lower()
                        if any(w in desc_lower for w in ["fee", "debit", "charge"]):
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

            # Validate enquiry amounts using balance progression.
            # Space-separated amounts like "736 825.00" can incorrectly
            # merge a reference number (736) with the actual amount (825.00).
            # If the parsed debit/credit doesn't match the expected balance,
            # try splitting space-separated amounts and use the alternative
            # that produces the correct balance change.
            if format_type == "enquiry" and rows:
                prev_balance = rows[-1]["Balance"]
                expected = round(prev_balance + credit - debit, 2)
                if abs(expected - balance) > 0.02:
                    for i in range(len(amounts) - 1):
                        amt_str = amounts[i]
                        if ' ' not in amt_str or amt_str.startswith('-'):
                            continue
                        parts = amt_str.split(' ')
                        for sp in range(1, len(parts)):
                            tail = ' '.join(parts[sp:])
                            try:
                                alt_val = abs(self._clean_amount(tail))
                            except (ValueError, IndexError):
                                continue
                            if alt_val == 0:
                                continue
                            alt_debit = alt_val if is_negative[i] else 0.0
                            alt_credit = alt_val if not is_negative[i] else 0.0
                            alt_expected = round(prev_balance + alt_credit - alt_debit, 2)
                            if abs(alt_expected - balance) < 0.02:
                                debit = alt_debit
                                credit = alt_credit
                                prefix = ' '.join(parts[:sp])
                                description = (description + ' ' + prefix).strip()
                                description = re.sub(r'\s+', ' ', description)
                                break
                        else:
                            continue
                        break

            rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
