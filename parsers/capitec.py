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
    # Also support comma-separated format
    AMOUNT_PATTERN_COMMA = re.compile(r"-?[\d,]+\.\d{2}")

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
        """Extract transactions from Capitec statement.

        Format: Date | Description | Reference | Money in | Money out | Fees | Balance
        - Money in is credits (positive values or blank)
        - Money out is debits (negative values or positive values)
        - Fees are additional charges (positive values or blank)
        - Balance is the running balance
        """
        rows = []
        previous_balance = None

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip header rows
                if "Date" in line and "Description" in line:
                    continue
                if "Money in" in line or "Money out" in line:
                    continue
                if "Transaction history" in line:
                    continue

                # Capitec date format: DD/MM/YYYY
                if not re.match(r"\d{2}/\d{2}/\d{4}", line):
                    continue

                # Try both amount patterns (space-separated and comma-separated)
                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 2:
                    amounts = self.AMOUNT_PATTERN_COMMA.findall(line)

                if len(amounts) < 2:
                    continue

                date = line[:10]

                # Get description - everything between date and first amount
                first_amt_match = re.search(r"[-\d,\s]+\.\d{2}", line[11:])
                if first_amt_match:
                    description = line[11:11+first_amt_match.start()].strip()
                else:
                    description = line[11:].strip()

                # Parse amounts
                debit = 0.0
                credit = 0.0
                balance = clean_amount(amounts[-1])

                # Format: Description [Reference] Money_in Money_out [Fees] Balance
                # If 4+ amounts: likely Money_in, Money_out, Fees, Balance
                if len(amounts) >= 4:
                    money_in = clean_amount(amounts[-4])
                    money_out = clean_amount(amounts[-3])
                    fees = clean_amount(amounts[-2])

                    if money_in > 0:
                        credit = money_in
                    if money_out > 0:
                        debit += abs(money_out)
                    if fees > 0:
                        debit += fees

                # If 3 amounts: could be Money_in/Money_out, Fees, Balance
                elif len(amounts) == 3:
                    first_amt = clean_amount(amounts[0])
                    second_amt = clean_amount(amounts[1])

                    # Check if first amount is negative (money out)
                    if first_amt < 0:
                        debit = abs(first_amt)
                        # Second amount might be fees
                        if second_amt > 0 and second_amt < abs(first_amt):
                            debit += second_amt
                    else:
                        # First amount might be money in
                        if first_amt > 0:
                            credit = first_amt
                        # Or it could be money out + fees + balance
                        else:
                            # Use balance change to determine
                            if previous_balance is not None:
                                diff = balance - previous_balance
                                if diff < 0:
                                    debit = abs(diff)
                                else:
                                    credit = diff

                # If 2 amounts: transaction amount and balance
                elif len(amounts) == 2:
                    txn_amount = clean_amount(amounts[0])

                    # Determine debit/credit from amount sign or balance change
                    if txn_amount < 0:
                        debit = abs(txn_amount)
                    elif txn_amount > 0:
                        credit = txn_amount
                    elif previous_balance is not None:
                        diff = balance - previous_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff

                rows.append(create_transaction_row(date, description, debit, credit, balance))
                previous_balance = balance

        return pd.DataFrame(rows)
