"""African Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, parse_date_yyyy_mm_dd, normalize_amount_string


class AfricanBankParser(BaseBankParser):
    """Parser for African Bank statements."""

    BANK_NAME = "African Bank"
    BANK_ID = "african_bank"
    DETECTION_KEYWORDS = ["african bank", "africanbank"]

    # Date format: YYYY/MM/DD
    DATE_PATTERN = re.compile(r"^(\d{4}/\d{2}/\d{2})")
    # Amount pattern - handles negative amounts and various formats
    AMOUNT_PATTERN = re.compile(r"-?[\d,\s]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from African Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account Number 20008855885"
        acc_match = re.search(
            r"Account\s*Number\s*(\d{10,12})", first_page, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Look for "Account Type Savings Pocket"
        type_match = re.search(
            r"Account\s*Type\s+([A-Za-z\s]+?)(?:\n|Account)",
            first_page, re.IGNORECASE
        )
        if type_match:
            account_type = type_match.group(1).strip()

        # Also try "Account Name" pattern
        if not account_type:
            name_match = re.search(
                r"Account\s*Name\s+([A-Za-z\s]+?)(?:\n|$)",
                first_page, re.IGNORECASE
            )
            if name_match:
                account_type = name_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean African Bank amount string to float."""
        return normalize_amount_string(amt)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from African Bank statement.

        Format: TRANSACTION DATE | TRANSACTION DETAILS | BANK CHARGES | AMOUNT | BALANCE
        Example: 2025/04/01 Credit Interest 35.76 35.41
        """
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip header rows
                if not line:
                    continue
                if "TRANSACTION DATE" in line or "TRANSACTION DETAILS" in line:
                    continue
                if "BANK CHARGES" in line and "AMOUNT" in line and "BALANCE" in line:
                    continue

                # Handle Opening Balance line
                if "Opening Balance" in line and "TRANSACTION" not in line:
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Opening Balance", 0.0, 0.0, balance
                        ))
                    continue

                # Match date at start of line
                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_str = parse_date_yyyy_mm_dd(date_match.group(1))

                # Find all amounts in the line
                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Clean amounts and determine if negative
                cleaned_amounts = []
                is_negative = []
                for amt in amounts:
                    val = self._clean_amount(amt)
                    cleaned_amounts.append(abs(val))
                    is_negative.append(val < 0 or amt.strip().startswith("-"))

                # Get rest of line after date
                rest_of_line = line[date_match.end():].strip()

                # Extract description (text before first amount)
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                # African Bank format: Description [BANK_CHARGES] AMOUNT BALANCE
                # BANK_CHARGES is optional (can be blank or negative like -0.60)
                # AMOUNT is positive for credits, negative for debits
                # BALANCE is the running balance

                balance = cleaned_amounts[-1] if cleaned_amounts else 0.0

                debit = 0.0
                credit = 0.0

                # If we have at least 2 amounts, second to last is the transaction amount
                if len(cleaned_amounts) >= 2:
                    amt = cleaned_amounts[-2]
                    amt_is_neg = is_negative[-2]

                    if amt_is_neg:
                        debit = amt
                    else:
                        credit = amt

                    # If we have 3+ amounts, handle bank charges
                    if len(cleaned_amounts) >= 3:
                        charges = cleaned_amounts[-3]
                        charges_neg = is_negative[-3]
                        if charges > 0:
                            debit += charges

                # If only 1 amount (balance), calculate from balance change
                elif len(cleaned_amounts) == 1 and rows:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
