"""African Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, parse_date_yyyy_mm_dd


class AfricanBankParser(BaseBankParser):
    """Parser for African Bank statements."""

    BANK_NAME = "African Bank"
    BANK_ID = "african_bank"
    DETECTION_KEYWORDS = ["african bank", "africanbank"]

    DATE_PATTERN = re.compile(r"^(\d{4}/\d{2}/\d{2})")
    AMOUNT_PATTERN = re.compile(r"-?[\d]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from African Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account Number 20008855885"
        acc_match = re.search(
            r"Account\s*Number\s*(\d{11})", first_page, re.IGNORECASE
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

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from African Bank statement."""
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                if "TRANSACTION DATE" in line or "TRANSACTION DETAILS" in line:
                    continue
                if "Opening Balance" in line and "TRANSACTION" not in line:
                    continue

                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_str = parse_date_yyyy_mm_dd(date_match.group(1))

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                rest_of_line = line[date_match.end():].strip()
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                debit = 0.0
                credit = 0.0
                balance = float(amounts[-1]) if amounts else 0.0

                # Determine debit/credit from amount sign or balance change
                if len(amounts) >= 2:
                    amt = float(amounts[-2])
                    if amt < 0:
                        debit = abs(amt)
                    else:
                        credit = amt
                elif len(rows) > 0:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
