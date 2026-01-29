"""Standard Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, clean_amount, create_transaction_row


class StandardBankParser(BaseBankParser):
    """Parser for Standard Bank statements."""

    BANK_NAME = "Standard Bank"
    BANK_ID = "standard_bank"
    DETECTION_KEYWORDS = ["standard bank"]

    # Date format: "17 Jul 25" (DD MMM YY)
    DATE_PATTERN = re.compile(
        r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})\b",
        re.IGNORECASE,
    )
    AMOUNT_PATTERN = re.compile(r"-?[\d,]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Standard Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account number: 10 13 368 746 3" pattern
        acc_match = re.search(
            r"Account\s*number[:\s]*([\d\s]{10,20})",
            first_page,
            re.IGNORECASE,
        )
        if acc_match:
            account_number = acc_match.group(1).strip()

        # Look for "Product name: BUS CURRENT" pattern
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
                if "Date" in line and "Description" in line:
                    continue
                if "Payments" in line and "Deposits" in line:
                    continue
                if "STATEMENT OPENING BALANCE" in line.upper():
                    continue

                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Parse date - convert 2-digit year to 4-digit
                day = date_match.group(1).zfill(2)
                month = MONTH_MAP.get(date_match.group(2).lower(), "01")
                year = f"20{date_match.group(3)}"
                date_str = f"{day}/{month}/{year}"

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
                balance = clean_amount(amounts[-1])

                # If 3 amounts: Payments, Deposits, Balance
                if len(amounts) == 3:
                    payment = clean_amount(amounts[0])
                    deposit = clean_amount(amounts[1])

                    # Payments are shown as negative (debits)
                    if payment < 0:
                        debit = abs(payment)
                    elif payment > 0:
                        # If positive, it's a deposit (unusual but handle it)
                        credit = payment

                    # Deposits are positive (credits)
                    if deposit > 0:
                        credit = deposit
                    elif deposit < 0:
                        # If negative, it's a payment (unusual but handle it)
                        debit = abs(deposit)

                # If 2 amounts: could be Payment+Balance or Deposit+Balance
                elif len(amounts) == 2:
                    first_amount = clean_amount(amounts[0])
                    # Negative = payment (debit), Positive = deposit (credit)
                    if first_amount < 0:
                        debit = abs(first_amount)
                    else:
                        credit = first_amount

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
