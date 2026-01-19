"""ABSA Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import clean_amount, create_transaction_row


class ABSAParser(BaseBankParser):
    """Parser for ABSA statements."""

    BANK_NAME = "ABSA"
    BANK_ID = "absa"
    DETECTION_KEYWORDS = ["absa"]

    DATE_PATTERN = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})")
    AMOUNT_PATTERN = re.compile(r"[\d\s]+\.\d{2}")

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

        # Also try without dashes
        if not account_number:
            acc_match = re.search(
                r"Account\s*Number[:\s]*(\d{10,12})",
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

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from ABSA statement."""
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip header and special rows
                if "Date" in line and "Transaction" in line:
                    continue
                if "Bal Brought Forward" in line or "Balance Brought Forward" in line:
                    continue
                if "YOUR PRICING PLAN" in line.upper() or "INTEREST RATE" in line.upper():
                    continue

                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_str = date_match.group(1)
                rest_of_line = line[date_match.end():].strip()

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Clean amounts (remove spaces as thousands separators)
                cleaned_amounts = [amt.replace(" ", "") for amt in amounts]

                # Get description
                first_amount_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amount_match:
                    description = rest_of_line[:first_amount_match.start()].strip()
                else:
                    description = rest_of_line

                # Remove common charge types from description
                description = re.sub(
                    r"\s*(Headoffice|Settlement|Notifyme|Sms Notifications)\s*$",
                    "", description, flags=re.IGNORECASE
                ).strip()

                # Parse amounts
                debit = 0.0
                credit = 0.0
                balance = float(cleaned_amounts[-1]) if cleaned_amounts else 0.0

                if len(cleaned_amounts) >= 2:
                    non_balance = cleaned_amounts[:-1]

                    # Use balance change to determine debit/credit
                    if len(rows) > 0:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff
                    else:
                        if len(non_balance) == 1:
                            debit = float(non_balance[0])
                        elif len(non_balance) >= 2:
                            for i, amt in enumerate(non_balance):
                                amt_val = float(amt)
                                if i == len(non_balance) - 1:
                                    credit = amt_val
                                elif i == len(non_balance) - 2:
                                    debit = amt_val

                # Fallback to balance diff
                if len(cleaned_amounts) >= 2 and debit == 0 and credit == 0 and len(rows) > 0:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    elif diff > 0:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
