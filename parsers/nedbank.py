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
    # Amount pattern - handles comma separators and optional thousands space separator
    # Negative lookbehind for digits prevents matching partial phone numbers
    AMOUNT_PATTERN = re.compile(r"(?<!\d)-?\d{1,3}(?:[,\s]\d{3})*\.\d{2}(?!\d)")

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
        """Detect which Nedbank format is being used."""
        # Statement Enquiry format
        if "Statement Enquiry" in text:
            return "enquiry"
        # Business statement with Tran list no
        if "Tran list no" in text:
            return "business"
        # Standard bank charges format
        if "Bank charges for the period" in text:
            return "charges"
        return "standard"

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Nedbank statement.

        Handles multiple formats:
        1. Standard: Date | Description | Debits | Credits | Balance
        2. Business: Tran list no | Date | Description | Fees | Debits | Credits | Balance
        3. Statement Enquiry: Date | Transactions | Debits | Credits | Balance
        4. Bank charges: Tran list no | Date | Description | Debits (R) | Credits (R) | Balance (R)
        """
        rows = []
        first_page = self._extract_first_page_text()
        format_type = self._detect_format(first_page)

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip header and special rows
                if not line:
                    continue
                if "Tran list no" in line or "Narrative Description" in line:
                    continue
                if "Date" in line and ("Transactions" in line or "Description" in line):
                    continue
                if "Debits" in line and "Credits" in line:
                    continue
                if "Opening balance" in line.lower():
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Opening Balance", 0.0, 0.0, balance
                        ))
                    continue
                if "Balance carried forward" in line or "balance carried forward" in line:
                    continue
                if "BROUGHT FORWARD" in line or "CARRIED FORWARD" in line:
                    continue

                # Find date in line - different formats have dates in different positions
                date_str = None
                rest_start = 0

                # Format with transaction/page number prefix: "162 01/11/2025" or "000821 30/08/2025"
                txn_date_match = re.search(r"^\d{3,6}\s+(\d{2}/\d{2}/\d{4})", line)
                if txn_date_match:
                    date_str = txn_date_match.group(1)
                    rest_start = txn_date_match.end()
                else:
                    # Try date at start of line
                    date_match = self.DATE_PATTERN.match(line)
                    if date_match:
                        date_str = date_match.group(1)
                        rest_start = date_match.end()
                    else:
                        # Try finding date anywhere in line
                        date_search = self.DATE_PATTERN.search(line)
                        if date_search:
                            date_str = date_search.group(1)
                            rest_start = date_search.end()
                        else:
                            continue

                # Find all amounts
                amounts = self.AMOUNT_PATTERN.findall(line)
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
                first_amount_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amount_match:
                    description = rest_of_line[:first_amount_match.start()].strip()
                else:
                    description = rest_of_line

                # Balance is always last
                balance = cleaned_amounts[-1] if cleaned_amounts else 0.0
                if is_negative and is_negative[-1]:
                    balance = -balance

                debit = 0.0
                credit = 0.0

                # Parse amounts based on format
                if format_type in ["business", "charges"]:
                    # Business format: Fees, Debits, Credits, Balance
                    if len(cleaned_amounts) >= 4:
                        fees = cleaned_amounts[-4]
                        debits = cleaned_amounts[-3]
                        credits = cleaned_amounts[-2]
                        debit = debits + fees
                        credit = credits
                    elif len(cleaned_amounts) == 3:
                        # Debits, Credits, Balance
                        debit = cleaned_amounts[-3]
                        credit = cleaned_amounts[-2]
                    elif len(cleaned_amounts) == 2:
                        # Amount, Balance
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
                    # Statement Enquiry format: single signed amount + balance
                    # e.g. "-400,000.00 1,945,663.81" (debit) or "500,000.00 2,131,611.92" (credit)
                    if len(cleaned_amounts) == 2:
                        amt_val = cleaned_amounts[0]
                        if is_negative[0]:
                            debit = amt_val
                        else:
                            credit = amt_val
                    elif len(cleaned_amounts) == 1:
                        # Only balance (e.g. BROUGHT FORWARD already handled above)
                        if rows:
                            prev_balance = rows[-1]["Balance"]
                            diff = balance - prev_balance
                            if diff < 0:
                                debit = abs(diff)
                            else:
                                credit = diff
                    elif len(cleaned_amounts) == 3:
                        # Possible extra amount in description text (e.g. "VAT 28/10-25/11 = R16.97")
                        # Last is balance, second-to-last is the transaction amount
                        amt_val = cleaned_amounts[-2]
                        if is_negative[-2]:
                            debit = amt_val
                        else:
                            credit = amt_val
                else:
                    # Standard format: Debits, Credits, Balance
                    if len(cleaned_amounts) == 3:
                        debit = cleaned_amounts[-3]
                        credit = cleaned_amounts[-2]
                    elif len(cleaned_amounts) == 2:
                        amt_val = cleaned_amounts[0]
                        # Use balance change to determine if debit or credit
                        if rows:
                            prev_balance = rows[-1]["Balance"]
                            diff = balance - prev_balance
                            if diff > 0:
                                credit = amt_val
                            else:
                                debit = amt_val
                        else:
                            # First transaction - check description for hints
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

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
