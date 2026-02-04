"""Bidvest Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, parse_date_yyyy_mm_dd, normalize_amount_string


class BidvestParser(BaseBankParser):
    """Parser for Bidvest Bank statements."""

    BANK_NAME = "Bidvest Bank"
    BANK_ID = "bidvest"
    DETECTION_KEYWORDS = ["bidvest"]

    # Date pattern YYYY/MM/DD
    DATE_PATTERN = re.compile(r"^(\d{4}/\d{2}/\d{2})")
    # Amount pattern - handles spaces as thousands separator and negative amounts
    AMOUNT_PATTERN = re.compile(r"-?\s*[\d\s,]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Bidvest Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account No: 03081729401" pattern
        acc_match = re.search(
            r"Account\s*No[:\s]*(\d{10,12})", first_page, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Also try "Account Number" pattern
        if not account_number:
            acc_match = re.search(
                r"Account\s*Number[:\s]*(\d{10,12})", first_page, re.IGNORECASE
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Look for "Account Statement: Business Account"
        type_match = re.search(
            r"Account\s*(?:Statement|Type)[:\s]*([A-Za-z\s]+?)(?:\s{2,}|Account|Date|\n)",
            first_page, re.IGNORECASE
        )
        if type_match:
            account_type = type_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean Bidvest amount string to float."""
        return normalize_amount_string(amt)

    def _detect_format(self, text: str) -> str:
        """Detect which Bidvest format is being used."""
        # Transaction History format has explicit header
        if "Transaction History" in text:
            return "transaction_history"
        # Account Statement format (with summary on first page)
        if "Balance Brought" in text or "Closing Balance" in text:
            return "account_statement"
        return "account_statement"

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Bidvest Bank statement.

        Handles two formats:
        1. Account Statement: YYYY/MM/DD | Description | Reference | Amount | Balance
        2. Transaction History: Transaction Date | Effective Date | Description | Reference | Fees | Amount | Balance
        """
        rows = []
        first_page = self._extract_first_page_text()

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip headers and special lines
                if not line:
                    continue
                if "Transaction" in line and "Date" in line:
                    continue
                if "Effective Date" in line or "Description" in line and "Reference" in line:
                    continue
                if "Balance Brought Forward" in line or "BROUGHT FORWARD" in line:
                    # Try to extract balance
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Balance Brought Forward", 0.0, 0.0, balance
                        ))
                    continue

                # Skip summary/header lines
                if "NEDLINK" in line and "Reference" in line:
                    continue
                if "Fees" in line and "Amount" in line and "Balance" in line:
                    continue

                # Match date at start of line
                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_str = parse_date_yyyy_mm_dd(date_match.group(1))

                # Get rest of line
                rest_of_line = line[date_match.end():].strip()

                # Check for second date (Effective Date)
                second_date_match = re.match(r"^\d{4}/\d{2}/\d{2}\s*", rest_of_line)
                if second_date_match:
                    rest_of_line = rest_of_line[second_date_match.end():].strip()

                # Find all amounts
                amounts = self.AMOUNT_PATTERN.findall(rest_of_line)
                if len(amounts) < 1:
                    continue

                # Clean amounts and track if negative
                cleaned_amounts = []
                is_negative = []
                for amt in amounts:
                    val = self._clean_amount(amt)
                    cleaned_amounts.append(abs(val))
                    is_negative.append(val < 0 or "-" in amt)

                # Extract description (text before first amount)
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                # Balance is always last
                balance = cleaned_amounts[-1] if cleaned_amounts else 0.0
                # Handle negative balance
                if is_negative and is_negative[-1]:
                    balance = -balance

                debit = 0.0
                credit = 0.0

                # Determine debit/credit based on number of amounts
                if len(cleaned_amounts) >= 3:
                    # Format: Fees, Amount, Balance
                    fees = cleaned_amounts[-3]
                    amount = cleaned_amounts[-2]

                    if is_negative[-2]:
                        debit = amount
                    else:
                        credit = amount

                    if fees > 0:
                        debit += fees

                elif len(cleaned_amounts) == 2:
                    # Format: Amount, Balance
                    amount = cleaned_amounts[0]

                    if is_negative[0]:
                        debit = amount
                    else:
                        # Determine from balance change
                        if rows:
                            prev_balance = rows[-1]["Balance"]
                            diff = balance - prev_balance
                            if diff < 0:
                                debit = amount
                            else:
                                credit = amount
                        else:
                            credit = amount

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
