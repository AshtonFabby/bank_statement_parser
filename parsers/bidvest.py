"""Bidvest Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, parse_date_yyyy_mm_dd


class BidvestParser(BaseBankParser):
    """Parser for Bidvest Bank statements."""

    BANK_NAME = "Bidvest Bank"
    BANK_ID = "bidvest"
    DETECTION_KEYWORDS = ["bidvest"]

    DATE_PATTERN = re.compile(r"^(\d{4}/\d{2}/\d{2})")
    AMOUNT_PATTERN = re.compile(r"-?\s*[\d\s]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Bidvest Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account No: 03081729401"
        acc_match = re.search(
            r"Account\s*No[:\s]*(\d{11})", first_page, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Look for "Account Statement: Business Account"
        type_match = re.search(
            r"Account\s*Statement[:\s]*([A-Za-z\s]+?)(?:\s{2,}|Account No|\n)",
            first_page, re.IGNORECASE
        )
        if type_match:
            account_type = type_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Bidvest Bank statement.

        Format: Transaction Date | Effective Date | Description | Reference | Fees | Amount | Balance
        - Fees are shown separately (can be 0.00 or positive)
        - Amount is negative for debits, positive for credits
        - Balance is the running balance
        """
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                if "Transaction" in line and "Date" in line:
                    continue
                if "Effective Date" in line or "Description" in line:
                    continue
                if "Balance Brought Forward" in line:
                    continue
                if "NEDLINK" in line and "Reference" in line:
                    continue

                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_str = parse_date_yyyy_mm_dd(date_match.group(1))

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Clean amounts and track negative signs
                cleaned_amounts = [
                    amt.replace(" ", "").replace("-", "").strip()
                    for amt in amounts
                ]
                is_negative = ["-" in amt for amt in amounts]

                rest_of_line = line[date_match.end():].strip()
                # Skip second date if present (Effective Date)
                second_date = re.match(r"^\d{4}/\d{2}/\d{2}\s*", rest_of_line)
                if second_date:
                    rest_of_line = rest_of_line[second_date.end():].strip()

                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                # Format: Description [Reference] Fees Amount Balance
                # Balance is always last
                balance = float(cleaned_amounts[-1]) if cleaned_amounts else 0.0

                debit = 0.0
                credit = 0.0

                # If we have 3+ amounts: likely Fees, Amount, Balance
                if len(cleaned_amounts) >= 3:
                    # Second to last is the Amount
                    amt_idx = -2
                    amt_val = float(cleaned_amounts[amt_idx])

                    if is_negative[amt_idx]:
                        debit = amt_val
                    else:
                        credit = amt_val

                    # Add fees to debit if present
                    fees_idx = -3
                    if fees_idx >= -len(cleaned_amounts):
                        fees_val = float(cleaned_amounts[fees_idx])
                        if fees_val > 0:
                            debit += fees_val

                # If we have 2 amounts: likely Amount, Balance
                elif len(cleaned_amounts) == 2:
                    amt_val = float(cleaned_amounts[0])
                    if is_negative[0]:
                        debit = amt_val
                    else:
                        credit = amt_val

                # If only 1 amount (balance), calculate from balance change
                elif len(cleaned_amounts) == 1 and len(rows) > 0:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
