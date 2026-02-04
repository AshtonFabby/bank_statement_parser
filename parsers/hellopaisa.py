"""HelloPaisa statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, normalize_amount_string


class HelloPaisaParser(BaseBankParser):
    """Parser for HelloPaisa statements."""

    BANK_NAME = "HelloPaisa"
    BANK_ID = "hellopaisa"
    DETECTION_KEYWORDS = ["hellopaisa", "hello paisa"]

    # Date format is YYYYMMDD (e.g., 20250801)
    DATE_PATTERN = re.compile(r"^(\d{8})\s+(\d{8})\s+")
    # Also handle single date format
    DATE_PATTERN_SINGLE = re.compile(r"^(\d{8})\s+")
    # Amount pattern
    AMOUNT_PATTERN = re.compile(r"[\d,]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from HelloPaisa statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account: 78600083370"
        acc_match = re.search(
            r"Account[:\s]+(\d{10,})", first_page, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Look for "Hello Paisa Transactional Account"
        type_match = re.search(
            r"(Hello\s*Paisa\s+\w+\s+Account)", first_page, re.IGNORECASE
        )
        if type_match:
            account_type = type_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _parse_date_yyyymmdd(self, date_str: str) -> str:
        """Convert YYYYMMDD to DD/MM/YYYY."""
        if len(date_str) != 8:
            return ""
        year = date_str[:4]
        month = date_str[4:6]
        day = date_str[6:8]
        return f"{day}/{month}/{year}"

    def _clean_amount(self, amt: str) -> float:
        """Clean HelloPaisa amount string to float."""
        return normalize_amount_string(amt)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from HelloPaisa statement.

        Format: Transaction Date | Effective Date | Description | Fee | Debit | Credit | Balance
        """
        rows = []

        for page_text in self._iterate_pages():
            lines = page_text.split("\n")

            for line in lines:
                line = line.strip()

                # Skip headers and empty lines
                if not line:
                    continue
                if "Transaction" in line and "Date" in line:
                    continue
                if "Effective Date" in line:
                    continue
                if "Fee" in line and "Debit" in line and "Credit" in line:
                    continue

                # Handle BALANCE BROUGHT FORWARD (no dates)
                if "BALANCE BROUGHT FORWARD" in line:
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "BALANCE BROUGHT FORWARD", 0.0, 0.0, balance
                        ))
                    continue

                # Try matching two dates first: YYYYMMDD YYYYMMDD
                date_match = self.DATE_PATTERN.match(line)
                if date_match:
                    trans_date = date_match.group(1)
                    date_str = self._parse_date_yyyymmdd(trans_date)
                    rest_of_line = line[date_match.end():].strip()
                else:
                    # Try single date
                    date_match = self.DATE_PATTERN_SINGLE.match(line)
                    if not date_match:
                        continue
                    trans_date = date_match.group(1)
                    date_str = self._parse_date_yyyymmdd(trans_date)
                    rest_of_line = line[date_match.end():].strip()

                if not date_str:
                    continue

                # Find all amounts in the line
                amounts = self.AMOUNT_PATTERN.findall(rest_of_line)
                if len(amounts) < 1:
                    continue

                # Clean amounts
                cleaned_amounts = [self._clean_amount(amt) for amt in amounts]

                # Extract description (text before first amount)
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                # Format: Description | Fee | Debit | Credit | Balance
                # We expect up to 4 amounts: Fee, Debit, Credit, Balance
                fee = 0.0
                debit = 0.0
                credit = 0.0
                balance = 0.0

                if len(cleaned_amounts) >= 4:
                    # Full format: Fee, Debit, Credit, Balance
                    fee = cleaned_amounts[-4]
                    debit = cleaned_amounts[-3]
                    credit = cleaned_amounts[-2]
                    balance = cleaned_amounts[-1]
                elif len(cleaned_amounts) == 3:
                    # Might be missing fee column: Debit, Credit, Balance
                    debit = cleaned_amounts[-3]
                    credit = cleaned_amounts[-2]
                    balance = cleaned_amounts[-1]
                elif len(cleaned_amounts) == 2:
                    # Just amount and balance
                    balance = cleaned_amounts[-1]
                    if rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = cleaned_amounts[0]
                        else:
                            credit = cleaned_amounts[0]
                    else:
                        # First transaction - check description for hints
                        desc_lower = description.lower()
                        if any(w in desc_lower for w in ["deposit", "credit", "received", "transfer in"]):
                            credit = cleaned_amounts[0]
                        else:
                            debit = cleaned_amounts[0]
                elif len(cleaned_amounts) == 1:
                    balance = cleaned_amounts[0]
                    # Calculate from balance change
                    if rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff

                # Add fee to debit if present
                if fee > 0:
                    debit += fee

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
