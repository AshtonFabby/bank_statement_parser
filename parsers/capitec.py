"""Capitec Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import PATTERNS, clean_amount, create_transaction_row


class CapitecParser(BaseBankParser):
    """Parser for Capitec Bank statements."""

    BANK_NAME = "Capitec"
    BANK_ID = "capitec"
    DETECTION_KEYWORDS = ["capitec"]

    # Capitec-specific amount pattern: -1 234.56 or 1 234.56
    AMOUNT_PATTERN = re.compile(r"-?\d{1,3}(?: \d{3})*\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Capitec statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        lines = first_page.split("\n")
        for i, line in enumerate(lines):
            line_lower = line.lower().strip()

            if "account number" in line_lower:
                match = re.search(r"\d{8,12}", line)
                if match:
                    account_number = match.group()
                elif i + 1 < len(lines):
                    match = re.search(r"\d{8,12}", lines[i + 1])
                    if match:
                        account_number = match.group()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Capitec statement."""
        rows = []
        previous_balance = None

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Capitec date format: DD/MM/YYYY
                if not re.match(r"\d{2}/\d{2}/\d{4}", line):
                    continue

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 2:
                    continue

                txn_amount = clean_amount(amounts[0])
                balance = clean_amount(amounts[-1])

                date = line[:10]
                description = line[11:].rsplit(amounts[0], 1)[0].strip()

                # Determine debit/credit from balance change
                if previous_balance is None:
                    debit = txn_amount if txn_amount < 0 else 0.0
                    credit = txn_amount if txn_amount > 0 else 0.0
                else:
                    diff = balance - previous_balance
                    if diff < 0:
                        debit = abs(diff)
                        credit = 0.0
                    else:
                        credit = diff
                        debit = 0.0

                rows.append(create_transaction_row(date, description, debit, credit, balance))
                previous_balance = balance

        return pd.DataFrame(rows)
