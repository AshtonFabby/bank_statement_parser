"""HBZ Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row, normalize_amount_string


class HBZParser(BaseBankParser):
    """Parser for HBZ Bank statements."""

    BANK_NAME = "HBZ Bank"
    BANK_ID = "hbz_bank"
    DETECTION_KEYWORDS = [("hbz bank", 10)]

    # Date format: "Jan 02, 2024" (MMM DD, YYYY)
    DATE_PATTERN = re.compile(
        r"^((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE
    )
    # Amount pattern
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

    def _clean_amount(self, amt: str) -> float:
        """Clean HBZ amount string to float."""
        return normalize_amount_string(amt)

    def _parse_date(self, date_raw: str) -> str:
        """Parse HBZ date format (MMM DD, YYYY or MMM DD YYYY) to DD/MM/YYYY."""
        date_raw = date_raw.replace(",", "").strip()
        parts = date_raw.split()
        if len(parts) >= 3:
            month = MONTH_MAP.get(parts[0].lower(), "01")
            day = parts[1].zfill(2)
            year = parts[2]
            return f"{day}/{month}/{year}"
        return ""

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from HBZ Bank statement.

        Format: Date | Particulars | Debit | Credit
        Balance lines appear as "Balance (CR) amount"
        """
        rows = []
        current_balance = 0.0

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip headers
                if not line:
                    continue
                if "Date" in line and "Particulars" in line:
                    continue
                if "Debit" in line and "Credit" in line:
                    continue

                # Handle Previous Balance
                if "Previous Balance" in line:
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        current_balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Previous Balance", 0.0, 0.0, current_balance
                        ))
                    continue

                # Handle Balance (CR) lines - these show running balance
                if "Balance (CR)" in line:
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        current_balance = self._clean_amount(amounts[-1])
                    continue

                # Match date at start of line
                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_str = self._parse_date(date_match.group(1))
                if not date_str:
                    continue

                # Find all amounts in line
                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                cleaned_amounts = [self._clean_amount(amt) for amt in amounts]

                # Get rest of line after date
                rest_of_line = line[date_match.end():].strip()

                # Extract description (text before first amount)
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                debit = 0.0
                credit = 0.0

                # HBZ has Debit and Credit columns
                if len(cleaned_amounts) >= 2:
                    # Two amounts: Debit, Credit
                    debit = cleaned_amounts[0]
                    credit = cleaned_amounts[1]
                elif len(cleaned_amounts) == 1:
                    # Only one amount - determine based on description and balance change
                    amt = cleaned_amounts[0]
                    desc_lower = description.lower()

                    # Check description for hints
                    if any(w in desc_lower for w in ["cash deposit", "eft", "credit", "received"]):
                        credit = amt
                    elif any(w in desc_lower for w in ["fee", "charge", "service", "vat", "telex"]):
                        debit = amt
                    else:
                        # Use balance change to determine
                        if rows:
                            prev_balance = current_balance
                            # Assume this transaction affects balance
                            # We'll adjust later when we see Balance (CR)
                            credit = amt

                # Update running balance
                current_balance = current_balance - debit + credit

                rows.append(create_transaction_row(
                    date_str, description, debit, credit, current_balance
                ))

        return pd.DataFrame(rows)
