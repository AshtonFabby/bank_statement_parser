"""Bidvest Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, parse_date_yyyy_mm_dd, normalize_amount_string


class BidvestParser(BaseBankParser):
    """Parser for Bidvest Bank statements."""

    BANK_NAME = "Bidvest Bank"
    BANK_ID = "bidvest"
    DETECTION_KEYWORDS = [
        ("bidvest", 5),
        ("branch code:462005", 10),
        ("craft hardware", 5),
        ("vilbes investments", 5),
    ]

    # Date pattern YYYY/MM/DD
    DATE_PATTERN = re.compile(r"^(\d{4}/\d{2}/\d{2})")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from Bidvest Bank statement."""
        first_page = self._extract_first_page_text()
        account_number = None
        account_type = None

        # Look for "Account No: 03081729401" pattern
        acc_match = re.search(
            r"Account\s*No[:\s]*(\d{10,12})", first_page, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Also try "Account Number" pattern
        if not account_number:
            acc_match = re.search(
                r"Account\s*Number[:\s]*(\d{10,12})", first_page, re.IGNORECASE
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Look for "Account Statement: Business Account"
        type_match = re.search(
            r"Account\s*(?:Statement|Type)[:\s]*([A-Za-z\s]+?)(?:\s{2,}|Account|Date|\n)",
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
        """Clean Bidvest amount string to float."""
        return normalize_amount_string(amt)

    def _detect_format(self, text: str) -> str:
        """Detect which Bidvest format is being used."""
        # Transaction History format has explicit header
        if "Transaction History" in text:
            return "transaction_history"
        # Account Statement format (with summary on first page)
        if "Balance Brought" in text or "Closing Balance" in text:
            return "account_statement"
        return "account_statement"

    def _extract_amounts_from_end(self, text: str, format_type: str):
        """Extract amounts from the end of a line.
        
        Returns a list of (amount_string, is_negative) tuples from the end of the line.
        Only matches amounts preceded by whitespace to avoid matching reference numbers.
        """
        # Pattern: whitespace, optional minus, optional spaces, digits with spaces/commas, decimal
        # Must be followed by end of string or another amount
        amount_pattern = re.compile(r'\s(-?\s*[\d\s,]+\.\d{2})(?=\s|$)')
        matches = list(amount_pattern.finditer(text))
        
        result = []
        for m in matches:
            amt_str = m.group(1).strip()
            is_negative = amt_str.startswith('-') or '- ' in m.group()
            result.append((amt_str, is_negative, m.start(), m.end()))
        
        return result

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from Bidvest Bank statement.

        Handles two formats:
        1. Account Statement: YYYY/MM/DD | Effective Date | Description | Amount | Balance
           - Uses spaces as thousand separators (e.g., "7 992.27")
           - 2 amounts: Amount and Balance
        2. Transaction History: Transaction Date | Effective Date | Description | Fees | Amount | Balance
           - Uses commas as thousand separators (e.g., "5,551.20")
           - 3 amounts: Fees, Amount (with sign), and Balance
        """
        rows = []
        first_page = self._extract_first_page_text()
        format_type = self._detect_format(first_page)

        for page_text in self._iterate_pages():
            lines = page_text.split("\n")
            i = 0
            while i < len(lines):
                line = lines[i].strip()

                # Skip headers and special lines
                if not line:
                    i += 1
                    continue
                if "Transaction" in line and "Date" in line:
                    i += 1
                    continue
                if "Effective Date" in line and "Description" in line:
                    i += 1
                    continue
                if "Balance Brought Forward" in line or "BROUGHT FORWARD" in line:
                    # Extract amounts from end
                    amounts = self._extract_amounts_from_end(line, format_type)
                    if amounts:
                        balance = self._clean_amount(amounts[-1][0])
                        rows.append(create_transaction_row(
                            "", "Balance Brought Forward", 0.0, 0.0, balance
                        ))
                    i += 1
                    continue

                # Skip summary/header lines
                if "NEDLINK" in line and "Reference" in line:
                    i += 1
                    continue
                if "Fees" in line and "Amount" in line and "Balance" in line:
                    i += 1
                    continue
                if "NETCASH" in line:
                    i += 1
                    continue
                
                # Skip orphan lines (TYMEBANK numbers, etc.)
                if re.match(r'^\d+$', line):
                    i += 1
                    continue

                # Match date at start of line
                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    i += 1
                    continue

                date_str = parse_date_yyyy_mm_dd(date_match.group(1))

                # Get rest of line
                rest_of_line = line[date_match.end():].strip()

                # Check for second date (Effective Date)
                second_date_match = re.match(r"^\d{4}/\d{2}/\d{2}\s*", rest_of_line)
                if second_date_match:
                    rest_of_line = rest_of_line[second_date_match.end():].strip()

                # Extract amounts from the end of the line
                amounts = self._extract_amounts_from_end(rest_of_line, format_type)
                
                if len(amounts) < 2:
                    i += 1
                    continue
                
                # Get balance (last amount)
                balance_str, _, balance_start, _ = amounts[-1]
                balance = self._clean_amount(balance_str)
                
                debit = 0.0
                credit = 0.0
                
                if format_type == "transaction_history":
                    # Transaction History format: Fees, Amount, Balance
                    if len(amounts) >= 3:
                        fees_str, _, fees_start, fees_end = amounts[-3]
                        trans_str, trans_is_negative, trans_start, trans_end = amounts[-2]
                        
                        fees = self._clean_amount(fees_str)
                        trans_amount = self._clean_amount(trans_str)
                        
                        # Get description (everything before fees)
                        description = rest_of_line[:fees_start].strip()
                        
                        if trans_is_negative or trans_amount < 0:
                            debit = abs(trans_amount) + fees
                        else:
                            credit = trans_amount
                            if fees > 0:
                                debit = fees
                    else:
                        i += 1
                        continue
                else:
                    # Account Statement format: Amount, Balance
                    trans_str, trans_is_negative, trans_start, trans_end = amounts[-2]
                    trans_amount = self._clean_amount(trans_str)
                    
                    # Get description (everything before transaction amount)
                    description = rest_of_line[:trans_start].strip()
                    
                    if trans_is_negative:
                        debit = abs(trans_amount)
                    else:
                        credit = trans_amount
                
                # Clean up description
                if description.endswith('-'):
                    description = description[:-1].strip()
                
                rows.append(create_transaction_row(date_str, description, debit, credit, balance))
                i += 1

        return pd.DataFrame(rows)
