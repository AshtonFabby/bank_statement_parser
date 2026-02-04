"""Standard Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row, normalize_amount_string


class StandardBankParser(BaseBankParser):
    """Parser for Standard Bank statements."""

    BANK_NAME = "Standard Bank"
    BANK_ID = "standard_bank"
    DETECTION_KEYWORDS = ["standard bank"]

    # Date format: "17 Nov 22" (DD MMM YY) or "17 Jul 25"
    DATE_PATTERN = re.compile(
        r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})\b",
        re.IGNORECASE,
    )
    # Amount pattern - handles comma-separated amounts and negative values
    AMOUNT_PATTERN = re.compile(r"-?[\d,]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Standard Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account number: 10 13 368 746 3" pattern (with spaces)
        acc_match = re.search(
            r"Account\s*number[:\s]*([\d\s]{10,20})",
            first_page,
            re.IGNORECASE,
        )
        if acc_match:
            account_number = acc_match.group(1).strip().replace(" ", "")

        # Also try without spaces
        if not account_number:
            acc_match = re.search(
                r"Account\s*number[:\s]*(\d{10,})",
                first_page,
                re.IGNORECASE,
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Look for "Product name: BUS CURRENT" or "CURRENT ACC" pattern
        product_match = re.search(
            r"Product\s*name[:\s]*([A-Z\s]+?)(?:\n|$)",
            first_page,
            re.IGNORECASE,
        )
        if product_match:
            account_type = product_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean Standard Bank amount string to float."""
        return normalize_amount_string(amt)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Standard Bank statement.

        Format: Date | Description | Payments | Deposits | Balance
        - Payments are negative (debits)
        - Deposits are positive (credits)
        - Balance is the running balance
        """
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip header rows
                if not line:
                    continue
                if "Date" in line and "Description" in line:
                    continue
                if "Payments" in line and "Deposits" in line:
                    continue

                # Handle STATEMENT OPENING BALANCE
                if "STATEMENT OPENING BALANCE" in line.upper():
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Statement Opening Balance", 0.0, 0.0, balance
                        ))
                    continue

                # Match date at start of line
                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                # Parse date - convert 2-digit year to 4-digit
                day = date_match.group(1).zfill(2)
                month = MONTH_MAP.get(date_match.group(2).lower(), "01")
                year = f"20{date_match.group(3)}"
                date_str = f"{day}/{month}/{year}"

                # Find all amounts
                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Clean amounts and track if negative
                cleaned_amounts = []
                is_negative = []
                for amt in amounts:
                    val = self._clean_amount(amt)
                    cleaned_amounts.append(abs(val))
                    is_negative.append(val < 0 or amt.strip().startswith("-"))

                # Get description
                rest_of_line = line[date_match.end():].strip()
                first_amount_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amount_match:
                    description = rest_of_line[:first_amount_match.start()].strip()
                else:
                    description = rest_of_line

                # Parse amounts - Standard Bank has Payments, Deposits, Balance columns
                debit = 0.0
                credit = 0.0
                balance = cleaned_amounts[-1] if cleaned_amounts else 0.0

                # If 3 amounts: Payments, Deposits, Balance
                if len(cleaned_amounts) == 3:
                    payment = cleaned_amounts[0]
                    deposit = cleaned_amounts[1]

                    # Payments are shown as negative (debits)
                    if is_negative[0] or payment > 0:
                        debit = payment

                    # Deposits are positive (credits)
                    if deposit > 0 and not is_negative[1]:
                        credit = deposit

                # If 2 amounts: could be Payment+Balance or Deposit+Balance
                elif len(cleaned_amounts) == 2:
                    first_amount = cleaned_amounts[0]
                    first_neg = is_negative[0]

                    # Negative = payment (debit), Positive = deposit (credit)
                    if first_neg:
                        debit = first_amount
                    else:
                        # Determine from balance change
                        if rows:
                            prev_balance = rows[-1]["Balance"]
                            diff = balance - prev_balance
                            if diff < 0:
                                debit = first_amount
                            else:
                                credit = first_amount
                        else:
                            # First transaction - check description for hints
                            desc_lower = description.lower()
                            if any(w in desc_lower for w in ["deposit", "credit", "received"]):
                                credit = first_amount
                            else:
                                debit = first_amount

                elif len(cleaned_amounts) == 1 and rows:
                    # Only balance - calculate from balance change
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
