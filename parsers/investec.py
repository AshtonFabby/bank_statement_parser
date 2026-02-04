"""Investec Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row, normalize_amount_string


class InvestecParser(BaseBankParser):
    """Parser for Investec statements."""

    BANK_NAME = "Investec"
    BANK_ID = "investec"
    DETECTION_KEYWORDS = ["investec"]

    # Date pattern: DD MMM YYYY (e.g., "1 May 2025", "30 Apr 2025")
    DATE_PATTERN = re.compile(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
        re.IGNORECASE
    )
    # Amount pattern - standard comma-separated
    AMOUNT_PATTERN = re.compile(r"[\d,]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Investec statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account Number 10012968438"
        acc_match = re.search(
            r"Account\s*Number\s*(\d{10,12})", first_page, re.IGNORECASE
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

    def _parse_date(self, day: str, month: str, year: str) -> str:
        """Parse date components to DD/MM/YYYY format."""
        month_num = MONTH_MAP.get(month.lower(), "01")
        return f"{day.zfill(2)}/{month_num}/{year}"

    def _clean_amount(self, amt: str) -> float:
        """Clean amount string to float."""
        return normalize_amount_string(amt)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Investec statement.

        Format has two date columns (Action date, Trans date) followed by:
        Transaction Description | Debit | Credit | Balance

        Example: "1 May 2025 30 Apr 2025 ATM cash withdrawal fee 18.50 62,608.90"
        """
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip headers
                if not line:
                    continue
                if "Action date" in line or "Trans date" in line:
                    continue
                if "Transaction Description" in line:
                    continue

                # Handle "Balance brought forward"
                if "balance brought forward" in line.lower():
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Balance brought forward", 0.0, 0.0, balance
                        ))
                    continue

                # Find first date (Action date)
                date_match = self.DATE_PATTERN.search(line)
                if not date_match:
                    continue

                day1 = date_match.group(1)
                month1 = date_match.group(2)
                year1 = date_match.group(3)
                date_str = self._parse_date(day1, month1, year1)

                # Get rest of line after first date
                rest_of_line = line[date_match.end():].strip()

                # Check for second date (Trans date)
                second_date_match = self.DATE_PATTERN.match(rest_of_line)
                if second_date_match:
                    rest_of_line = rest_of_line[second_date_match.end():].strip()

                # Find all amounts
                amounts = self.AMOUNT_PATTERN.findall(rest_of_line)
                if len(amounts) < 1:
                    continue

                # Clean amounts
                cleaned_amounts = [self._clean_amount(a) for a in amounts]

                # Extract description (text before first amount)
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                # Parse amounts - Investec has Debit, Credit, Balance columns
                # But they can be blank, so we might have 1, 2, or 3 amounts
                debit = 0.0
                credit = 0.0
                balance = cleaned_amounts[-1] if cleaned_amounts else 0.0

                if len(cleaned_amounts) >= 3:
                    # Full format: Debit, Credit, Balance
                    debit = cleaned_amounts[-3]
                    credit = cleaned_amounts[-2]
                elif len(cleaned_amounts) == 2:
                    # Either Debit+Balance or Credit+Balance
                    # Determine from balance change
                    if rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = cleaned_amounts[0]
                        else:
                            credit = cleaned_amounts[0]
                    else:
                        # First transaction - assume amount is transaction value
                        # Check description for hints
                        desc_lower = description.lower()
                        if any(w in desc_lower for w in ["fee", "withdrawal", "payment", "purchase", "debit"]):
                            debit = cleaned_amounts[0]
                        else:
                            credit = cleaned_amounts[0]
                elif len(cleaned_amounts) == 1:
                    # Only balance - calculate from balance change
                    if rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff

                rows.append(create_transaction_row(
                    date_str, description, debit, credit, balance
                ))

        return pd.DataFrame(rows)
