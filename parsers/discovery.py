"""Discovery Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row, normalize_amount_string


class DiscoveryParser(BaseBankParser):
    """Parser for Discovery Bank statements."""

    BANK_NAME = "Discovery Bank"
    BANK_ID = "discovery_bank"
    DETECTION_KEYWORDS = ["discovery"]

    # Transaction History format: YYYY-MM-DD
    DATE_PATTERN_YYYY_MM_DD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\b")
    # Bank Statement format: DD MMM YYYY (e.g., "2 Jul 2025")
    DATE_PATTERN_DD_MMM_YYYY = re.compile(
        r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
        re.IGNORECASE
    )
    # Amount pattern - handles R prefix, spaces, trailing minus
    # Matches: R 10,274.00, -R980.44, R 983.08-, - R2.75, R11 420.00
    AMOUNT_PATTERN = re.compile(r'-?\s*R?\s*[\d\s,]+\.\d{2}-?')

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Discovery Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account number: 14655573958" pattern
        acc_match = re.search(
            r"Account\s*number[:\s]*(\d{10,12})",
            first_page, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Try alternative pattern: "Discovery Gold Transaction Account 14655573958"
        if not account_number:
            acc_match = re.search(
                r"(?:Discovery\s+)?(?:Gold|Purple|Orange)?\s*(?:Transaction\s+)?Account\s*(\d{10,12})",
                first_page, re.IGNORECASE
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Get account type from title
        type_match = re.search(
            r"(Discovery\s+(?:Gold|Purple|Orange)\s+(?:Transaction\s+)?Account)",
            first_page, re.IGNORECASE
        )
        if type_match:
            account_type = type_match.group(1).strip()

        # Alternative: "Account type: Transaction Account"
        if not account_type:
            type_match = re.search(
                r"Account\s*type[:\s]*([A-Za-z\s]+?)(?:\n|Account|$)",
                first_page, re.IGNORECASE
            )
            if type_match:
                account_type = type_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_discovery_amount(self, amt: str) -> float:
        """Clean Discovery amount string to float."""
        return normalize_amount_string(amt)

    def _detect_format(self, text: str) -> str:
        """Detect which Discovery format is being used."""
        # Check for YYYY-MM-DD date format (Transaction History)
        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            return "transaction_history"
        # Check for "Date Card no. Type Details Amount" header
        if "Card no" in text and "Type" in text:
            return "bank_statement_v1"
        # Check for simple "Date Description Debit Credit Balance" format
        if re.search(r"Date\s+Description\s+Debit\s+Credit\s+Balance", text, re.IGNORECASE):
            return "transaction_history"
        return "bank_statement_v2"

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Discovery Bank statement.

        Handles multiple formats:
        1. Transaction History: YYYY-MM-DD with Debit/Credit/Balance columns
        2. Bank Statement v1: DD MMM YYYY with Card no., Type, Details, Amount
        3. Bank Statement v2: DD MMM YYYY with Description, Debit, Credit, Balance
        """
        rows = []
        first_page = self._extract_first_page_text()
        format_type = self._detect_format(first_page)

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                # Skip headers and special lines
                if not line:
                    continue
                if "Date" in line and ("Description" in line or "Card no" in line or "Details" in line):
                    continue
                if "Opening balance" in line.lower():
                    # Try to extract opening balance
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_discovery_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Opening Balance", 0.0, 0.0, balance
                        ))
                    continue
                if "Debit" in line and "Credit" in line and "Balance" in line:
                    continue

                # Try YYYY-MM-DD format first (Transaction History)
                date_match = self.DATE_PATTERN_YYYY_MM_DD.match(line)
                if date_match:
                    year = date_match.group(1)
                    month = date_match.group(2)
                    day = date_match.group(3)
                    date_str = f"{day}/{month}/{year}"
                    rest_of_line = line[date_match.end():].strip()
                else:
                    # Try DD MMM YYYY format (Bank Statement)
                    date_match = self.DATE_PATTERN_DD_MMM_YYYY.match(line)
                    if not date_match:
                        continue

                    day = date_match.group(1).zfill(2)
                    month = MONTH_MAP.get(date_match.group(2).lower(), "01")
                    year = date_match.group(3)
                    date_str = f"{day}/{month}/{year}"
                    rest_of_line = line[date_match.end():].strip()

                # Find all amounts in the line
                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Clean amounts
                cleaned_amounts = [self._clean_discovery_amount(a) for a in amounts]

                # Extract description (text before first amount in rest_of_line)
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                # Clean up description - remove card numbers, type prefixes
                description = re.sub(r"^\*{3}\d+\s*", "", description).strip()
                description = re.sub(r"^(EFT|Fee|Debit order)\s+", "", description, flags=re.IGNORECASE).strip()

                # Parse amounts based on column structure
                debit = 0.0
                credit = 0.0
                balance = 0.0

                if len(cleaned_amounts) >= 3:
                    # Format: Debit, Credit, Balance
                    # Get the last 3 amounts
                    potential_debit = cleaned_amounts[-3]
                    potential_credit = cleaned_amounts[-2]
                    potential_balance = cleaned_amounts[-1]

                    # In Discovery, negative values in debit column are debits
                    # Positive values in credit column are credits
                    if potential_debit < 0:
                        debit = abs(potential_debit)
                    elif potential_debit > 0:
                        # Might be credit shown in wrong column
                        credit = potential_debit

                    if potential_credit > 0 and potential_debit <= 0:
                        credit = potential_credit
                    elif potential_credit < 0:
                        debit += abs(potential_credit)

                    balance = potential_balance

                elif len(cleaned_amounts) == 2:
                    # Could be Amount + Balance or Debit/Credit + Balance
                    first_amt = cleaned_amounts[0]
                    balance = cleaned_amounts[-1]

                    if first_amt < 0:
                        debit = abs(first_amt)
                    else:
                        credit = first_amt

                elif len(cleaned_amounts) == 1:
                    # Only balance or amount
                    if rows:
                        # Calculate from balance change
                        prev_balance = rows[-1]["Balance"]
                        balance = cleaned_amounts[0]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff
                    else:
                        balance = cleaned_amounts[0]

                # Skip if we couldn't extract meaningful data
                if not description and debit == 0 and credit == 0:
                    continue

                rows.append(create_transaction_row(
                    date_str,
                    description if description else "Transaction",
                    debit, credit, balance
                ))

        return pd.DataFrame(rows)
