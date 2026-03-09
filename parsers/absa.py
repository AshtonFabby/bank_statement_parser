"""ABSA Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, normalize_amount_string


class ABSAParser(BaseBankParser):
    """Parser for ABSA statements."""

    BANK_NAME = "ABSA"
    BANK_ID = "absa"
    DETECTION_KEYWORDS = ["absa", "cheque account statement", "transaction history"]

    # Date patterns - ABSA uses multiple formats
    DATE_PATTERN_DMY = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})")  # DD/MM/YYYY or D/M/YYYY
    DATE_PATTERN_YMD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")  # YYYY-MM-DD (Transaction History)
    # Amount pattern for cheque statements - space as thousands separator (e.g. "11 236.59-")
    AMOUNT_PATTERN = re.compile(r"-?[\d\s,]+\.\d{2}-?")
    # Amount pattern for Transaction History - comma as thousands separator (e.g. "-6,000.00")
    TH_AMOUNT_PATTERN = re.compile(r"-?\d{1,3}(?:,\d{3})*\.\d{2}")
    # Footer markers that signal end of transaction section on a page
    FOOTER_MARKERS = ["SERVICE FEE:", "MNTHLY ACCT FEE", "CHARGE: A =", "* = VAT", "INTEREST RATE"]
    # Lines to skip in Transaction History format
    TH_SKIP_PATTERN = re.compile(r"^\(|^Page\s|^Balance Carried")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from ABSA statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Cheque Account Number: 40-9691-8651" pattern
        acc_match = re.search(
            r"(?:Cheque\s*)?Account\s*Number[:\s]*(\d{2}-\d{4}-\d{4})",
            first_page,
            re.IGNORECASE,
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Try without dashes (e.g., "4084967163")
        if not account_number:
            acc_match = re.search(
                r"(?:ABSA|Account)\s*[\n\s]*(\d{10,12})",
                first_page,
                re.IGNORECASE,
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Get account type
        if "cheque account" in first_page.lower():
            account_type = "Cheque Account"
        elif "savings account" in first_page.lower():
            account_type = "Savings Account"

        type_match = re.search(
            r"Account\s*Type[:\s]*([A-Za-z\s]+?)(?:\s{2,}|Issued|Statement|\n)",
            first_page,
            re.IGNORECASE,
        )
        if type_match:
            account_type = type_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean ABSA amount string to float."""
        return normalize_amount_string(amt)

    def _detect_format(self, text: str) -> str:
        """Detect which ABSA format is being used."""
        # Transaction History format uses YYYY-MM-DD dates
        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            return "transaction_history"
        # Cheque account statement format
        if "Cheque account statement" in text:
            return "cheque_statement"
        return "standard"

    def _extract_transactions_th(self) -> pd.DataFrame:
        """Extract transactions from ABSA Transaction History format.

        Format: YYYY-MM-DD | Description | Amount | Balance
        Amounts use commas as thousands separators (e.g. -6,000.00).
        Charge lines appear on separate lines in parentheses: ( 40,00 )
        or inline: PayShap Ext Debit ( 8,50 ) -2,000.00 -489,967.70
        """
        rows = []
        # Inline charge pattern to strip before parsing amounts
        inline_charge = re.compile(r"\(\s*[\d,]+\s*\)")

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()
                if not line:
                    continue

                # Skip non-transaction lines
                if self.TH_SKIP_PATTERN.match(line):
                    continue
                if "Date:" in line and "Transaction" in line:
                    continue

                # Handle Balance Brought Forward (no date prefix)
                if "Balance Brought Forward" in line:
                    amounts = self.TH_AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = normalize_amount_string(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Balance Brought Forward", 0.0, 0.0, balance
                        ))
                    continue

                # Must start with YYYY-MM-DD
                date_match = self.DATE_PATTERN_YMD.match(line)
                if not date_match:
                    continue

                year, month, day = date_match.group(1), date_match.group(2), date_match.group(3)
                date_str = f"{day}/{month}/{year}"
                rest_of_line = line[date_match.end():].strip()

                # Strip inline charges like ( 8,50 ) before finding amounts
                rest_cleaned = inline_charge.sub("", rest_of_line)

                # Find amounts using TH-specific pattern (comma thousands, dot decimal)
                amounts = self.TH_AMOUNT_PATTERN.findall(rest_cleaned)
                if len(amounts) < 2:
                    # Need at least amount + balance; skip charge-only/zero lines
                    if len(amounts) == 1:
                        # Single amount means only balance (amount is 0.00)
                        balance = normalize_amount_string(amounts[0])
                        # Extract description
                        first_match = self.TH_AMOUNT_PATTERN.search(rest_cleaned)
                        description = rest_cleaned[:first_match.start()].strip() if first_match else rest_cleaned
                        description = inline_charge.sub("", description).strip()
                        rows.append(create_transaction_row(date_str, description, 0.0, 0.0, balance))
                    continue

                # Last amount is balance, second-to-last is transaction amount
                amount_val = normalize_amount_string(amounts[-2])
                balance = normalize_amount_string(amounts[-1])

                # Extract description (text before first amount in cleaned line)
                first_match = self.TH_AMOUNT_PATTERN.search(rest_cleaned)
                description = rest_cleaned[:first_match.start()].strip() if first_match else rest_cleaned
                # Also clean any inline charge remnants from description
                description = inline_charge.sub("", description).strip()

                debit = 0.0
                credit = 0.0
                if amount_val < 0:
                    debit = abs(amount_val)
                elif amount_val > 0:
                    credit = amount_val

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)

    def _extract_transactions_cheque(self) -> pd.DataFrame:
        """Extract transactions from ABSA Cheque/Standard statement format.

        Format: DD/MM/YYYY | Description | Charge | Debit | Credit | Balance
        Amounts use spaces as thousands separators (e.g. 4 694 675.63-).
        """
        rows = []

        for page_text in self._iterate_pages():
            in_footer = False
            for line in page_text.split("\n"):
                line = line.strip()

                if not line:
                    continue

                # Detect footer section and stop parsing transactions on this page
                if any(marker in line for marker in self.FOOTER_MARKERS):
                    in_footer = True
                    continue
                if in_footer:
                    continue

                if "Date" in line and "Transaction" in line:
                    continue
                if "Bal Brought Forward" in line or "Balance Brought Forward" in line:
                    amounts = self.AMOUNT_PATTERN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[-1])
                        rows.append(create_transaction_row(
                            "", "Balance Brought Forward", 0.0, 0.0, balance
                        ))
                    continue
                if "YOUR PRICING PLAN" in line.upper():
                    continue

                # Try DD/MM/YYYY format
                date_match = self.DATE_PATTERN_DMY.match(line)
                if not date_match:
                    # Continuation line: append reference info to previous transaction
                    if rows and not line.startswith("(") and not line.startswith("Page "):
                        rows[-1]["Description"] += " " + line
                    continue
                date_str = date_match.group(1)
                parts = date_str.split("/")
                if len(parts) == 3:
                    date_str = f"{parts[0].zfill(2)}/{parts[1].zfill(2)}/{parts[2]}"
                rest_of_line = line[date_match.end():].strip()

                # Find all amounts
                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                # Clean amounts
                cleaned_amounts = []
                is_negative = []
                for amt in amounts:
                    val = self._clean_amount(amt)
                    cleaned_amounts.append(abs(val))
                    is_negative.append(val < 0 or amt.strip().startswith("-") or amt.strip().endswith("-"))

                # Extract description (text before first amount)
                first_amount_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amount_match:
                    description = rest_of_line[:first_amount_match.start()].strip()
                else:
                    description = rest_of_line

                # Clean description - remove charge type suffixes
                description = re.sub(
                    r"\s*(Headoffice|Settlement|Notifyme|Sms Notifications)\s*$",
                    "", description, flags=re.IGNORECASE
                ).strip()

                # Balance is always last
                balance = cleaned_amounts[-1] if cleaned_amounts else 0.0
                if is_negative and is_negative[-1]:
                    balance = -balance

                debit = 0.0
                credit = 0.0

                if len(cleaned_amounts) >= 2:
                    # Use balance change to determine debit/credit
                    if rows:
                        prev_balance = rows[-1]["Balance"]
                        diff = balance - prev_balance
                        if diff < 0:
                            debit = abs(diff)
                        else:
                            credit = diff
                    else:
                        # First transaction
                        amount = cleaned_amounts[-2]
                        if is_negative[-2]:
                            debit = amount
                        else:
                            desc_lower = description.lower()
                            if any(w in desc_lower for w in ["fee", "charge", "debit", "payment"]):
                                debit = amount
                            else:
                                credit = amount

                elif len(cleaned_amounts) == 1 and rows:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from ABSA statement.

        Handles multiple formats:
        1. Transaction History (YYYY-MM-DD): Date | Transaction Description | Amount | Balance
        2. Cheque Statement (DD/MM/YYYY): Date | Transaction Description | Charge | Debit | Credit | Balance
        """
        first_page = self._extract_first_page_text()
        format_type = self._detect_format(first_page)

        if format_type == "transaction_history":
            return self._extract_transactions_th()
        return self._extract_transactions_cheque()
