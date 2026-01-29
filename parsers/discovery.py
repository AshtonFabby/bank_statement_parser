"""Discovery Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import MONTH_MAP, create_transaction_row


class DiscoveryParser(BaseBankParser):
    """Parser for Discovery Bank statements."""

    BANK_NAME = "Discovery Bank"
    BANK_ID = "discovery_bank"
    DETECTION_KEYWORDS = ["discovery"]

    # Transaction History format: YYYY-MM-DD
    DATE_PATTERN_YYYY_MM_DD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\b")
    # Bank Statement format: DD MMM YYYY
    DATE_PATTERN_DD_MMM_YYYY = re.compile(
        r"^(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})",
        re.IGNORECASE
    )
    AMOUNT_PATTERN = re.compile(r"-?\s*R?\s*[\d\s,]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Discovery Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Discovery Gold Transaction Account 14655573958"
        acc_match = re.search(
            r"(?:Discovery\s+)?(?:Gold|Purple|Orange)?\s*(?:Transaction\s+)?Account\s*(\d{11})",
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

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_discovery_amount(self, amt: str) -> tuple[float, bool]:
        """Clean Discovery amount and determine if negative."""
        clean = amt.replace("R", "").replace(" ", "").strip()
        is_neg = clean.startswith("-")
        clean = clean.replace("-", "")
        return float(clean), is_neg

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Discovery Bank statement.

        Handles two formats:
        1. Transaction History: YYYY-MM-DD with Debit/Credit/Balance columns
        2. Bank Statement: DD MMM YYYY with single amount column
        """
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                if "Date" in line and ("Description" in line or "Card no" in line):
                    continue
                if "Opening balance" in line or "Debit" in line and "Credit" in line:
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

                    date_raw = date_match.group(1)
                    date_parts = date_raw.split()
                    day = date_parts[0].zfill(2)
                    month = MONTH_MAP.get(date_parts[1].lower(), "01")
                    year = date_parts[2]
                    date_str = f"{day}/{month}/{year}"
                    rest_of_line = line[date_match.end():].strip()

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                # Clean up description
                description = re.sub(r"^\*{3}\d+\s*", "", description).strip()
                description = re.sub(
                    r"^(EFT|Fee|Debit order)\s*", "", description, flags=re.IGNORECASE
                ).strip()

                debit = 0.0
                credit = 0.0
                balance = 0.0

                # Transaction History format: Description Debit Credit Balance
                # - Debit and Credit are separate columns (one will be blank)
                # - Balance is always present
                if len(amounts) >= 3:
                    # Likely format: Debit, Credit, Balance
                    debit_amt, debit_neg = self._clean_discovery_amount(amounts[-3])
                    credit_amt, credit_neg = self._clean_discovery_amount(amounts[-2])
                    balance_amt, balance_neg = self._clean_discovery_amount(amounts[-1])

                    if debit_amt > 0 and not debit_neg:
                        debit = debit_amt
                    if credit_amt > 0 and not credit_neg:
                        credit = credit_amt

                    balance = balance_amt if not balance_neg else -balance_amt

                elif len(amounts) == 2:
                    # Could be Debit/Credit + Balance or Amount + Balance
                    first_amt, first_neg = self._clean_discovery_amount(amounts[0])
                    balance_amt, balance_neg = self._clean_discovery_amount(amounts[-1])

                    if first_neg:
                        debit = first_amt
                    else:
                        credit = first_amt

                    balance = balance_amt if not balance_neg else -balance_amt

                elif amounts:
                    # Only one amount, likely the transaction amount (old format)
                    last_amt, last_neg = self._clean_discovery_amount(amounts[-1])
                    if last_neg:
                        debit = last_amt
                    else:
                        credit = last_amt

                    # Calculate balance
                    if len(rows) > 0:
                        prev_balance = rows[-1]["Balance"]
                        balance = prev_balance - debit + credit
                    else:
                        balance = credit - debit

                rows.append(create_transaction_row(
                    date_str,
                    description if description else "Transaction",
                    debit, credit, balance
                ))

        return pd.DataFrame(rows)
