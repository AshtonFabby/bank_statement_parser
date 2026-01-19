"""Investec Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, clean_amount, create_transaction_row


class InvestecParser(BaseBankParser):
    """Parser for Investec statements."""

    BANK_NAME = "Investec"
    BANK_ID = "investec"
    DETECTION_KEYWORDS = ["investec"]

    DATE_PATTERN = re.compile(
        r"^(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})",
        re.IGNORECASE
    )
    AMOUNT_PATTERN = re.compile(r"[\d,]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Investec statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account Number 10012968438"
        acc_match = re.search(
            r"Account\s*Number\s*(\d{11})", first_page, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Look for "Private Bank Account Statement"
        if "private bank" in first_page.lower():
            account_type = "Private Bank Account"

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Investec statement."""
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                if "Action date" in line or "Trans date" in line:
                    continue
                if "Balance brought forward" in line:
                    continue

                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_raw = date_match.group(1)
                # Parse "1 May 2025" to "01/05/2025"
                date_parts = date_raw.split()
                day = date_parts[0].zfill(2)
                month = MONTH_MAP.get(date_parts[1].lower(), "01")
                year = date_parts[2]
                date_str = f"{day}/{month}/{year}"

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                cleaned_amounts = [amt.replace(",", "") for amt in amounts]

                rest_of_line = line[date_match.end():].strip()
                # Skip second date if present
                second_date = self.DATE_PATTERN.match(rest_of_line)
                if second_date:
                    rest_of_line = rest_of_line[second_date.end():].strip()

                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                debit = 0.0
                credit = 0.0
                balance = float(cleaned_amounts[-1]) if cleaned_amounts else 0.0

                # Determine debit/credit from balance change
                if len(cleaned_amounts) >= 2:
                    if len(rows) > 0:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff
                    else:
                        debit = float(cleaned_amounts[0])

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
