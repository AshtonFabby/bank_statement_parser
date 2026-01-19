"""TymeBank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row


class TymeBankParser(BaseBankParser):
    """Parser for TymeBank statements."""

    BANK_NAME = "TymeBank"
    BANK_ID = "tymebank"
    DETECTION_KEYWORDS = ["tymebank", "tyme bank"]

    DATE_PATTERN = re.compile(
        r"^(\d{2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})",
        re.IGNORECASE
    )
    AMOUNT_PATTERN = re.compile(r"[\d\s]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from TymeBank statement."""
        full_text = self._extract_full_text()
        account_number = None
        account_type = None

        # Look for "Account Num. 51111402774"
        acc_match = re.search(
            r"Account\s*Num\.?\s*(\d{11})", full_text, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Also look for "EveryDay account 51111402774"
        if not account_number:
            acc_match = re.search(
                r"EveryDay\s+account\s+(\d{11})", full_text, re.IGNORECASE
            )
            if acc_match:
                account_number = acc_match.group(1)
                account_type = "EveryDay Account"

        # Get account type
        if not account_type:
            if "everyday" in full_text.lower():
                account_type = "EveryDay Account"
            elif "goalsave" in full_text.lower():
                account_type = "GoalSave Account"

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from TymeBank statement."""
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                if "Date" in line and "Description" in line:
                    continue
                if "Opening Balance" in line and not self.DATE_PATTERN.match(line):
                    continue

                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_raw = date_match.group(1)
                date_parts = date_raw.split()
                day = date_parts[0]
                month = MONTH_MAP.get(date_parts[1].lower(), "01")
                year = date_parts[2]
                date_str = f"{day}/{month}/{year}"

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                cleaned_amounts = [amt.replace(" ", "") for amt in amounts]

                rest_of_line = line[date_match.end():].strip()
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                # Remove "-" placeholders
                description = re.sub(r"^\s*-\s*", "", description).strip()

                debit = 0.0
                credit = 0.0
                balance = float(cleaned_amounts[-1]) if cleaned_amounts else 0.0

                # Use balance change to determine debit/credit
                if len(rows) > 0:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff
                elif len(cleaned_amounts) >= 2:
                    # First transaction
                    if len(cleaned_amounts) >= 3:
                        debit = float(cleaned_amounts[-3])
                        credit = float(cleaned_amounts[-2])

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
