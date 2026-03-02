"""ABSA Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, normalize_amount_string


class ABSAParser(BaseBankParser):
    """Parser for ABSA statements."""

    BANK_NAME = "ABSA"
    BANK_ID = "absa"
    DETECTION_KEYWORDS = ["absa", "cheque account statement"]

    # Date patterns - ABSA uses multiple formats
    DATE_PATTERN_DMY = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})")  # DD/MM/YYYY or D/M/YYYY
    DATE_PATTERN_YMD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")  # YYYY-MM-DD (Transaction History)
    # Amount pattern - handles space as thousands separator and trailing minus (e.g. "11 236.59-")
    AMOUNT_PATTERN = re.compile(r"-?[\d\s,]+\.\d{2}-?")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from ABSA statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Cheque Account Number: 40-9691-8651" pattern
        acc_match = re.search(
            r"(?:Cheque\s*)?Account\s*Number[:\s]*(\d{2}-\d{4}-\d{4})",
            first_page,
            re.IGNORECASE,
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Try without dashes (e.g., "4084967163")
        if not account_number:
            acc_match = re.search(
                r"(?:ABSA|Account)\s*[\n\s]*(\d{10,12})",
                first_page,
                re.IGNORECASE,
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Get account type
        if "cheque account" in first_page.lower():
            account_type = "Cheque Account"
        elif "savings account" in first_page.lower():
            account_type = "Savings Account"

        type_match = re.search(
            r"Account\s*Type[:\s]*([A-Za-z\s]+?)(?:\s{2,}|Issued|Statement|\n)",
            first_page,
            re.IGNORECASE,
        )
        if type_match:
            account_type = type_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean ABSA amount string to float."""
        return normalize_amount_string(amt)

    def _detect_format(self, text: str) -> str:
        """Detect which ABSA format is being used."""
        # Transaction History format uses YYYY-MM-DD dates
        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            return "transaction_history"
        # Cheque account statement format
        if "Cheque account statement" in text:
            return "cheque_statement"
        return "standard"

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from ABSA statement.

        Handles multiple formats:
        1. Transaction History (YYYY-MM-DD): Date | Transaction Description | Amount | Balance
        2. Cheque Statement (DD/MM/YYYY): Date | Transaction Description | Charge | Debit | Credit | Balance
        3. Standard format (D/M/YYYY): Date | Description | Amount | Balance
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
                if "Date" in line and "Transaction" in line:
                    continue
                if "Bal Brought Forward" in line or "Balance Brought Forward" in line:
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Balance Brought Forward", 0.0, 0.0, balance
                        ))
                    continue
                if "YOUR PRICING PLAN" in line.upper() or "INTEREST RATE" in line.upper():
                    continue

                # Try YYYY-MM-DD format first (Transaction History)
                date_match = self.DATE_PATTERN_YMD.match(line)
                if date_match:
                    year = date_match.group(1)
                    month = date_match.group(2)
                    day = date_match.group(3)
                    date_str = f"{day}/{month}/{year}"
                    rest_of_line = line[date_match.end():].strip()
                else:
                    # Try DD/MM/YYYY format
                    date_match = self.DATE_PATTERN_DMY.match(line)
                    if not date_match:
                        continue
                    date_str = date_match.group(1)
                    # Normalize to DD/MM/YYYY if needed
                    parts = date_str.split("/")
                    if len(parts) == 3:
                        date_str = f"{parts[0].zfill(2)}/{parts[1].zfill(2)}/{parts[2]}"
                    rest_of_line = line[date_match.end():].strip()

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
                    is_negative.append(val < 0 or amt.strip().startswith("-") or amt.strip().endswith("-"))

                # Extract description (text before first amount)
                first_amount_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amount_match:
                    description = rest_of_line[:first_amount_match.start()].strip()
                else:
                    description = rest_of_line

                # Clean description - remove charge type suffixes
                description = re.sub(
                    r"\s*(Headoffice|Settlement|Notifyme|Sms Notifications)\s*$",
                    "", description, flags=re.IGNORECASE
                ).strip()

                # Balance is always last
                balance = cleaned_amounts[-1] if cleaned_amounts else 0.0
                if is_negative and is_negative[-1]:
                    balance = -balance

                debit = 0.0
                credit = 0.0

                # Parse amounts based on format
                if format_type == "transaction_history":
                    # Transaction History: Amount is second to last, Balance is last
                    if len(cleaned_amounts) >= 2:
                        amount = cleaned_amounts[-2]
                        if is_negative[-2]:
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
                                # First transaction - check if positive or negative
                                debit = amount
                    elif len(cleaned_amounts) == 1 and rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff
                else:
                    # Standard and Cheque formats
                    if len(cleaned_amounts) >= 2:
                        # Use balance change to determine debit/credit
                        if rows:
                            prev_balance = rows[-1]["Balance"]
                            diff = balance - prev_balance
                            if diff < 0:
                                debit = abs(diff)
                            else:
                                credit = diff
                        else:
                            # First transaction
                            amount = cleaned_amounts[-2]
                            if is_negative[-2]:
                                debit = amount
                            else:
                                # Check description for hints
                                desc_lower = description.lower()
                                if any(w in desc_lower for w in ["fee", "charge", "debit", "payment"]):
                                    debit = amount
                                else:
                                    credit = amount

                    elif len(cleaned_amounts) == 1 and rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
