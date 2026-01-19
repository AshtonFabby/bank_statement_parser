"""HBZ Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, clean_amount, create_transaction_row


class HBZParser(BaseBankParser):
    """Parser for HBZ Bank statements."""

    BANK_NAME = "HBZ Bank"
    BANK_ID = "hbz_bank"
    DETECTION_KEYWORDS = ["hbz bank"]

    # Date format: "Jan 02, 2024" (MMM DD, YYYY)
    DATE_PATTERN = re.compile(
        r"^((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4})",
        re.IGNORECASE
    )
    AMOUNT_PATTERN = re.compile(r"[\d,]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from HBZ Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account 04-01-01-20311-901-273881"
        acc_match = re.search(
            r"Account\s+(\d{2}-\d{2}-\d{2}-\d{5}-\d{3}-\d{6})",
            first_page, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Look for "Type Current Account"
        type_match = re.search(
            r"Type\s+(Current Account|Savings Account)",
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
        """Extract transactions from HBZ Bank statement."""
        rows = []
        current_balance = 0.0

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                if "Date" in line and "Particulars" in line:
                    continue
                if "Previous Balance" in line:
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        current_balance = clean_amount(amounts[-1])
                    continue
                if "Balance (CR)" in line:
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        current_balance = clean_amount(amounts[-1])
                    continue

                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_raw = date_match.group(1)
                # Parse "Jan 02, 2024" to "02/01/2024"
                date_raw = date_raw.replace(",", "")
                date_parts = date_raw.split()
                month = MONTH_MAP.get(date_parts[0].lower(), "01")
                day = date_parts[1].zfill(2)
                year = date_parts[2]
                date_str = f"{day}/{month}/{year}"

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                cleaned_amounts = [clean_amount(amt) for amt in amounts]

                rest_of_line = line[date_match.end():].strip()
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                debit = 0.0
                credit = 0.0

                # HBZ has Debit and Credit columns
                if len(cleaned_amounts) >= 2:
                    debit = cleaned_amounts[0]
                    credit = cleaned_amounts[1] if len(cleaned_amounts) > 1 else 0.0
                elif len(cleaned_amounts) == 1:
                    amt = cleaned_amounts[0]
                    # Determine based on position in line
                    amt_pos = line.rfind(amounts[0])
                    if amt_pos > len(line) * 0.6:
                        credit = amt
                    else:
                        debit = amt

                current_balance = current_balance - debit + credit

                rows.append(create_transaction_row(date_str, description, debit, credit, current_balance))

        return pd.DataFrame(rows)
