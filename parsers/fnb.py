"""FNB (First National Bank) statement parser."""

import re
from datetime import datetime

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import (
    MONTH_MAP,
    create_transaction_row,
    extract_year_from_text,
    parse_amount_with_cr,
)


class FNBParser(BaseBankParser):
    """Parser for FNB statements."""

    BANK_NAME = "FNB"
    BANK_ID = "fnb"
    DETECTION_KEYWORDS = ["fnb", "first national bank"]

    DATE_PATTERN = re.compile(
        r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
        re.IGNORECASE
    )
    AMOUNT_PATTERN = re.compile(r"-?[\d,]+\.\d{2}(?:Cr)?")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from FNB statement."""
        full_text = self._extract_full_text()
        account_number = None
        account_type = None

        # Look for "Gold Business Account : 63169152360" pattern
        account_match = re.search(
            r"([\w\s]+Account)\s*[:\s]+(\d{10,12})", full_text
        )
        if account_match:
            account_type = account_match.group(1).strip()
            account_number = account_match.group(2).strip()

        # Fallback: look for Account Number field
        if not account_number:
            acc_num_match = re.search(
                r"Account\s*Number[:\s]*(\d{10,12})", full_text, re.IGNORECASE
            )
            if acc_num_match:
                account_number = acc_num_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from FNB statement."""
        rows = []
        previous_balance = None
        current_year = None

        for page_text in self._iterate_pages():
            # Extract year from statement period if not yet found
            if not current_year:
                current_year = extract_year_from_text(page_text)

            for line in page_text.split("\n"):
                line = line.strip()

                # Skip header rows
                if "Date" in line and "Description" in line:
                    continue
                if "Balance" in line and "Amount" in line:
                    continue

                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Parse date
                day = date_match.group(1)
                month = MONTH_MAP.get(date_match.group(2).lower(), "01")
                year = current_year or datetime.now().strftime("%Y")
                date_str = f"{day}/{month}/{year}"

                # Get description
                rest_of_line = line[date_match.end():].strip()
                first_amount_match = re.search(r"-?[\d,]+\.\d{2}(?:Cr)?", rest_of_line)
                if first_amount_match:
                    description = rest_of_line[:first_amount_match.start()].strip()
                else:
                    description = rest_of_line

                # Parse amounts
                if len(amounts) >= 2:
                    balance_val, balance_is_credit = parse_amount_with_cr(amounts[-1])
                    amount_val, amount_is_credit = parse_amount_with_cr(amounts[0])

                    balance = balance_val if balance_is_credit else -balance_val

                    if amount_is_credit:
                        credit = amount_val
                        debit = 0.0
                    else:
                        debit = amount_val
                        credit = 0.0

                elif len(amounts) == 1:
                    balance_val, balance_is_credit = parse_amount_with_cr(amounts[0])
                    balance = balance_val if balance_is_credit else -balance_val

                    if previous_balance is not None:
                        diff = balance - previous_balance
                        if diff < 0:
                            debit = abs(diff)
                            credit = 0.0
                        else:
                            credit = diff
                            debit = 0.0
                    else:
                        debit = 0.0
                        credit = 0.0
                else:
                    continue

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))
                previous_balance = balance

        return pd.DataFrame(rows)
