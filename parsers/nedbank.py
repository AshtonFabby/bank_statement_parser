"""Nedbank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import clean_amount, create_transaction_row


class NedbankParser(BaseBankParser):
    """Parser for Nedbank statements."""

    BANK_NAME = "Nedbank"
    BANK_ID = "nedbank"
    DETECTION_KEYWORDS = ["nedbank"]

    DATE_PATTERN = re.compile(r"(\d{2}/\d{2}/\d{4})")
    AMOUNT_PATTERN = re.compile(r"[\d,]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Nedbank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        lines = first_page.split("\n")

        # Look for account number in the Account summary table
        for line in lines:
            if "current account" in line.lower():
                acc_match = re.search(r"(\d{10})", line)
                if acc_match:
                    account_number = acc_match.group(1)
                    account_type = "Current Account"
                    break
            elif "savings account" in line.lower():
                acc_match = re.search(r"(\d{10})", line)
                if acc_match:
                    account_number = acc_match.group(1)
                    account_type = "Savings Account"
                    break
            elif "business account" in line.lower():
                acc_match = re.search(r"(\d{10})", line)
                if acc_match:
                    account_number = acc_match.group(1)
                    account_type = "Business Account"
                    break

        # Fallback: look for any 10-digit number near "Account number"
        if not account_number:
            for i, line in enumerate(lines):
                if "account number" in line.lower():
                    acc_match = re.search(r"(\d{10})", line)
                    if acc_match:
                        account_number = acc_match.group(1)
                        break
                    elif i + 1 < len(lines):
                        acc_match = re.search(r"(\d{10})", lines[i + 1])
                        if acc_match:
                            account_number = acc_match.group(1)
                            break

        # Last fallback
        if not account_number:
            acc_match = re.search(r"\b(\d{10})\b", first_page)
            if acc_match:
                account_number = acc_match.group(1)

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Nedbank statement.

        Format: Date | Transactions | Debits | Credits | Balance
        - Debits are positive values in the Debits column (money out)
        - Credits are positive values in the Credits column (money in)
        - Balance is the running balance
        """
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip header and special rows
                if "Tran list no" in line or "Narrative Description" in line:
                    continue
                if "Date" in line and ("Transactions" in line or "Description" in line):
                    continue
                if "Debits" in line and "Credits" in line:
                    continue
                if "Opening balance" in line or "Balance carried forward" in line:
                    continue
                if "BROUGHT FORWARD" in line or "CARRIED FORWARD" in line:
                    continue

                # Find date in line - Nedbank uses DD/MM/YYYY format
                txn_date_match = re.search(r"^\d{6}\s+(\d{2}/\d{2}/\d{4})", line)
                if txn_date_match:
                    date_str = txn_date_match.group(1)
                    rest_start = txn_date_match.end()
                else:
                    date_match = self.DATE_PATTERN.match(line)
                    if date_match:
                        date_str = date_match.group(1)
                        rest_start = date_match.end()
                    else:
                        date_search = re.search(r"\s(\d{2}/\d{2}/\d{4})\s", line)
                        if date_search:
                            date_str = date_search.group(1)
                            rest_start = date_search.end()
                        else:
                            continue

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Get description
                rest_of_line = line[rest_start:].strip()
                first_amount_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amount_match:
                    description = rest_of_line[:first_amount_match.start()].strip()
                else:
                    description = rest_of_line

                # Parse amounts - Nedbank has Debits, Credits, Balance columns
                # Balance is always last
                balance = clean_amount(amounts[-1])

                debit = 0.0
                credit = 0.0

                # If 3 amounts: likely Debits, Credits, Balance
                if len(amounts) == 3:
                    debit = clean_amount(amounts[0])
                    credit = clean_amount(amounts[1])

                # If 2 amounts: could be Debit+Balance or Credit+Balance
                elif len(amounts) == 2:
                    amt_val = clean_amount(amounts[0])

                    # Use balance change to determine if debit or credit
                    if len(rows) > 0:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff > 0:
                            credit = amt_val
                        else:
                            debit = amt_val
                    else:
                        # First transaction - check description for hints
                        if "fee" in description.lower() or "debit" in description.lower():
                            debit = amt_val
                        else:
                            # Default to checking if balance is positive or negative
                            if balance > 0:
                                credit = amt_val
                            else:
                                debit = amt_val

                # If only 1 amount (balance), calculate from balance change
                elif len(amounts) == 1 and len(rows) > 0:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)
