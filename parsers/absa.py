"""ABSA Bank statement parser."""

import re

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import create_transaction_row, normalize_amount_string, parse_date_dd_mmm_yyyy


class ABSAParser(BaseBankParser):
    """Parser for ABSA statements."""

    BANK_NAME = "ABSA"
    BANK_ID = "absa"
    DETECTION_KEYWORDS = [
        ("absa", 1),
        ("cheque account statement", 10),
        ("transaction history", 3),
        ("business bank esp", 10),
        ("bio case", 10),
    ]

    # Date patterns - ABSA uses multiple formats
    DATE_PATTERN_DMY = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})")  # DD/MM/YYYY or D/M/YYYY
    DATE_PATTERN_YMD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")  # YYYY-MM-DD (Transaction History)
    DATE_PATTERN_YYMMDD = re.compile(r"^\d{4,}\s+(\d{2})(\d{2})(\d{2})\s+")  # YYMMDD (Business Integrator)
    # Amount pattern for cheque statements - space as thousands separator (e.g. "11 236.59-")
    AMOUNT_PATTERN = re.compile(r"-?[\d\s,]+\.\d{2}-?")
    # Amount pattern for Transaction History - comma/space as thousands separator (e.g. "-6,000.00" or "-R4 482.32")
    # Also handles R prefix: -R115.00, R5 300.00 (space as thousands sep)
    # Pattern: optional minus, optional R, digits with optional comma/space thousands separators, decimal
    TH_AMOUNT_PATTERN = re.compile(r"-?R?\s*(?:\d{1,3}(?:[,\s]\d{3})*|\d{4,})\.\d{2}")
    # Footer markers that signal end of transaction section on a page
    FOOTER_MARKERS = ["SERVICE FEE:", "MNTHLY ACCT FEE", "CHARGE: A =", "* = VAT", "INTEREST RATE"]
    # Lines to skip in Transaction History format
    TH_SKIP_PATTERN = re.compile(r"^\(|^Page\s|^Balance Carried")
    # BizStart format patterns - uses "D Mon YYYY" dates and comma-decimal amounts
    BIZSTART_DATE_PATTERN = re.compile(
        r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\s*(.*)",
        re.IGNORECASE,
    )
    BIZSTART_AMOUNT_PATTERN = re.compile(r"\d{1,3}(?:\s\d{3})*,\d{2}-?")

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

        # Try BizStart format: "Account number 91 1595 8244"
        if not account_number:
            acc_match = re.search(
                r"Account\s*number\s+(\d{2}\s+\d{4}\s+\d{4})",
                first_page,
                re.IGNORECASE,
            )
            if acc_match:
                account_number = acc_match.group(1)

        # Transaction History format: account number on separate line after PO BOX
        # Format: "PO BOX 1088 4064712281" or "NELSPRUIT 4064712281"
        if not account_number:
            # Look for 10-digit number following PO BOX line or on its own line
            th_match = re.search(
                r"PO\s+BOX\s+\d+\s+(\d{10})",
                first_page,
                re.IGNORECASE,
            )
            if th_match:
                account_number = th_match.group(1)

        # Transaction History alternative: account number after branch/city
        if not account_number:
            th_match2 = re.search(
                r"(?:SQUARE BRANCH|BRANCH)\s*\n?\s*(\d{10})\s*\n",
                first_page,
                re.IGNORECASE,
            )
            if th_match2:
                account_number = th_match2.group(1)

        # Get account type
        if "bizstart" in first_page.lower():
            account_type = "BizStart"
        elif "cheque account" in first_page.lower():
            account_type = "Cheque Account"
        elif "savings account" in first_page.lower():
            account_type = "Savings Account"
        elif "transaction history" in first_page.lower():
            account_type = "Current Account"

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
        # Transaction History format uses YYYY-MM-DD dates - check first as it's more specific
        if re.search(r"\d{4}-\d{2}-\d{2}", text) and "Transaction History" in text:
            return "transaction_history"
        # Cheque account statement format - explicit header check
        if "Cheque account statement" in text:
            return "cheque_statement"
        # Business Integrator format uses YYMMDD dates (e.g., 250901) with entry numbers
        # Pattern: entry number (4+ digits) + space + YYMMDD date + space
        # Must also contain "BIO CASE" to avoid false positives
        if re.search(r"^\d{4}\s+\d{6}\s+", text, re.MULTILINE) and "BIO CASE" in text:
            return "business_integrator"
        # BizStart format uses "D Mon YYYY" dates and comma-decimal amounts
        if re.search(
            r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}",
            text,
            re.IGNORECASE,
        ):
            return "bizstart"
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
                        # Clean trailing R-prefixed fragments
                        description = re.sub(r'\s*-?R\d+\s*$', '', description).strip()
                        rows.append(create_transaction_row(date_str, description, 0.0, 0.0, balance))
                    continue

                # Get raw amount strings to preserve sign information
                # Last amount is balance, second-to-last is transaction amount
                amount_raw = amounts[-2]
                balance_raw = amounts[-1]

                amount_val = normalize_amount_string(amount_raw)
                balance = normalize_amount_string(balance_raw)

                # Extract description (text before first amount in cleaned line)
                first_match = self.TH_AMOUNT_PATTERN.search(rest_cleaned)
                description = rest_cleaned[:first_match.start()].strip() if first_match else rest_cleaned
                # Also clean any inline charge remnants from description
                description = inline_charge.sub("", description).strip()
                # Clean trailing R-prefixed fragments that look like partial amounts (e.g., "-R4", "R5")
                description = re.sub(r'\s*-?R\d+\s*$', '', description).strip()

                # Determine debit/credit from the raw amount string
                # In ABSA Transaction History: negative amounts are debits, positive are credits
                debit = 0.0
                credit = 0.0
                if amount_raw.strip().startswith("-"):
                    # Negative amount = debit (money out)
                    debit = abs(amount_val)
                else:
                    # Positive amount = credit (money in)
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
                    # Only if it looks like a continuation (not a header/footer)
                    if rows and not line.startswith("(") and not line.startswith("Page "):
                        # Skip lines that are clearly headers/footers/metadata
                        skip_prefixes = (
                            "eStamp", "General Enquiries", "Return address", "Private Bag",
                            "Cheque Account Number", "Account Type", "Statement no",
                            "Client VAT", "Our Privacy Notice", "Authorised Financial",
                            "Registration Number", "Tax Invoice", "Absa Bank Limited",
                            "Your transactions", "CSP", "NCWORKS", "Dep No", "Contact:",
                            "Absa Bank Transfer", "Card No.", "Effective", "Telkom",
                            "Tracker", "Superspar", "Arrie Nel", "Mugg And Bean",
                            "Walk Of", "Nelspruit", "PO BOX", "1200", "013753", "008140",
                            "008568", "0085", "08600"
                        )
                        if not any(line.startswith(prefix) for prefix in skip_prefixes):
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

    def _extract_transactions_business_integrator(self) -> pd.DataFrame:
        """Extract transactions from ABSA Business Integrator format.

        Format: EntryNo YYMMDD Description... Site Amount Balance
        Dates are in YYMMDD format (e.g., 250901 = 1 Sep 2025).
        Descriptions may span multiple lines.
        Example:
          1927 250901 POS PURCHASE (EFFEC 29082025) Kauai Florida SETTLEMENT -125.00 1859290.57
          Road DURBA
        """
        rows: list[dict] = []
        pending_transaction: dict | None = None

        # Entry number + YYMMDD date pattern
        entry_date_pattern = re.compile(r"^(\d{4,})\s+(\d{2})(\d{2})(\d{2})\s+(.*)")
        # Standard amount pattern (e.g., -125.00, 1859290.57)
        amount_pattern = re.compile(r"-?[\d,]+\.\d{2}")

        for page_text in self._iterate_pages():
            lines = page_text.split("\n")

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Skip header lines
                if "BIO CASE" in line:
                    continue
                if "Account 4113770534" in line or "Branch BUSINESS BANK ESP" in line:
                    continue
                if "Start Date" in line and "End Date" in line:
                    continue
                if re.match(r"^Entry\s+Event\s*$", line, re.IGNORECASE):
                    continue
                if "No Date Description Site Amount Balance" in line or \
                   "No Date Description" in line:
                    continue
                if re.match(r"^\d{4}-\d{2}-\d{2}$", line):  # Date line like 2025-10-06
                    continue
                if re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),", line):  # Timestamp line
                    continue

                # Check for transaction line with entry number + date
                match = entry_date_pattern.match(line)
                if match:
                    # Save previous pending transaction if any
                    if pending_transaction is not None:
                        rows.append(pending_transaction)

                    entry_no = match.group(1)
                    year = "20" + match.group(2)  # Convert YY to 20YY
                    month = match.group(3)
                    day = match.group(4)
                    date_str = f"{day}/{month}/{year}"
                    rest = match.group(5)

                    # Handle Balance B/FORWARD
                    if "BALANCE B/FORWARD" in rest or "BALANCE B/F" in rest.upper():
                        amounts = amount_pattern.findall(rest)
                        if amounts:
                            balance = normalize_amount_string(amounts[-1])
                            rows.append(create_transaction_row(
                                "", "Balance Brought Forward", 0.0, 0.0, balance
                            ))
                        pending_transaction = None
                        continue

                    # Find all amounts in the line
                    amounts = amount_pattern.findall(rest)

                    if len(amounts) >= 2:
                        # Complete transaction on single line
                        amount_val = normalize_amount_string(amounts[-2])
                        balance = normalize_amount_string(amounts[-1])

                        # Extract description (text before first amount)
                        first_amt_match = amount_pattern.search(rest)
                        description = rest[:first_amt_match.start()].strip() if first_amt_match else rest

                        # Remove site indicators from end of description
                        description = re.sub(r"\s+(SETTLEMENT|HEADOFFICE|CF|RASC|BALLI|DURBA|CINGSTANG|UMHLA|CN)\s*$", "", description).strip()

                        debit = 0.0
                        credit = 0.0
                        if amount_val < 0:
                            debit = abs(amount_val)
                        else:
                            credit = amount_val

                        rows.append(create_transaction_row(
                            date_str, description, debit, credit, balance
                        ))
                        pending_transaction = None
                    else:
                        # Need continuation lines for amounts
                        description = rest
                        pending_transaction = {
                            "Date": date_str,
                            "Description": description,
                            "Debit": 0.0,
                            "Credit": 0.0,
                            "Balance": 0.0,
                        }
                    continue

                # Continuation line - append to pending transaction description
                if pending_transaction is not None:
                    # Check if this line has amounts (completing the transaction)
                    amounts = amount_pattern.findall(line)
                    if len(amounts) >= 2:
                        # Final amounts found
                        amount_val = normalize_amount_string(amounts[-2])
                        balance = normalize_amount_string(amounts[-1])

                        # Extract additional description (text before first amount)
                        first_amt_match = amount_pattern.search(line)
                        if first_amt_match:
                            extra_desc = line[:first_amt_match.start()].strip()
                            if extra_desc:
                                pending_transaction["Description"] += " " + extra_desc

                        pending_transaction["Balance"] = balance
                        if amount_val < 0:
                            pending_transaction["Debit"] = abs(amount_val)
                        else:
                            pending_transaction["Credit"] = amount_val

                        rows.append(pending_transaction)
                        pending_transaction = None
                    else:
                        # Just more description text
                        pending_transaction["Description"] += " " + line

            # End of page - save any pending transaction
            if pending_transaction is not None:
                # Try to complete with last known balance if possible
                rows.append(pending_transaction)
                pending_transaction = None

        return pd.DataFrame(rows)

    def _parse_bizstart_amount(self, amt_str: str) -> float:
        """Parse BizStart amount (comma decimal, space thousands) to float.

        Examples: '535 285,75' -> 535285.75, '5 000,00-' -> -5000.0
        """
        s = amt_str.strip()
        negative = s.endswith("-")
        clean = s.rstrip("-").replace(" ", "").replace(",", ".")
        try:
            value = float(clean)
            return -value if negative else value
        except ValueError:
            return 0.0

    def _extract_transactions_bizstart(self) -> pd.DataFrame:
        """Extract transactions from ABSA BizStart statement format.

        Format: D Mon YYYY | Description | Amount | Balance
        Amounts use space as thousands separator and comma as decimal separator.
        Service/Transaction/Administration/Notification fees appear on sub-lines.
        """
        rows: list[dict] = []
        pending_date: str | None = None
        pending_desc: str | None = None
        pending_amount: float | None = None
        # Main transaction row that trailing reference lines attach to.
        # Reference lines (e.g. "Merch Cap Mca-for Advance 0695" for a Merchant
        # Capital debit order, "Thecp ..." for a Capital Partners debit order)
        # appear on their own line after the transaction and identify the payee.
        last_txn_row: dict | None = None

        for page_text in self._iterate_pages():
            lines = page_text.split("\n")

            # Find the column header to know where transactions start on this page
            start_idx = 0
            for i, line in enumerate(lines):
                if "date" in line.lower() and "description" in line.lower():
                    start_idx = i + 1
                    break

            for line in lines[start_idx:]:
                line = line.strip()
                if not line:
                    continue

                # Skip footer lines
                if (
                    "Please turn over" in line
                    or line.startswith("Page ")
                    or "Absa Bank Limited" in line
                    or "DGP002SV" in line
                    or "Our Privacy Notice" in line
                    # End-of-statement disclaimer prose (would otherwise be
                    # appended to the preceding transaction as a "reference")
                    or "currency conversion" in line
                    or "through an international" in line
                    or "applies to any transactions" in line
                    or line == "merchant."
                    or line.startswith("Important If")
                    or "differ from your records" in line
                    or "expedite any adjustments" in line
                    or line.startswith("Authorised Financial")
                    or line.startswith("Registration number")
                ):
                    continue

                # Check for date line
                date_match = self.BIZSTART_DATE_PATTERN.match(line)
                if date_match:
                    # Flush an unresolved credit reversal ("Direct Debit Unpaid"
                    # whose balance is not printed because it is followed only by
                    # a pending unpaid-fee notice). Its balance is the running
                    # balance plus the reversed (credit) amount.
                    if (
                        pending_date is not None
                        and pending_amount is not None
                        and pending_amount > 0.0
                    ):
                        base = rows[-1]["Balance"] if rows else 0.0
                        rows.append(
                            create_transaction_row(
                                pending_date,
                                pending_desc,
                                0.0,
                                pending_amount,
                                round(base + pending_amount, 2),
                            )
                        )
                        last_txn_row = rows[-1]
                        pending_date = None
                        pending_desc = None
                        pending_amount = None

                    day = date_match.group(1)
                    month_name = date_match.group(2)
                    year = date_match.group(3)
                    rest = date_match.group(4)
                    date_str = parse_date_dd_mmm_yyyy(day, month_name, year)

                    # Handle Balance Brought Forward
                    if "Balance Brought Forward" in rest:
                        amounts = list(self.BIZSTART_AMOUNT_PATTERN.finditer(rest))
                        if amounts:
                            balance = self._parse_bizstart_amount(amounts[-1].group())
                            rows.append(
                                create_transaction_row(
                                    "", "Balance Brought Forward", 0.0, 0.0, balance
                                )
                            )
                        pending_date = None
                        last_txn_row = None
                        continue

                    # Find amounts in rest of line
                    amounts = list(self.BIZSTART_AMOUNT_PATTERN.finditer(rest))

                    # Extract description (text before first amount)
                    if amounts:
                        desc = rest[: amounts[0].start()].strip()
                    else:
                        desc = rest.strip()

                    if len(amounts) >= 2:
                        # Complete transaction: amount + balance on same line
                        amt_val = self._parse_bizstart_amount(amounts[-2].group())
                        balance = self._parse_bizstart_amount(amounts[-1].group())
                        debit = abs(amt_val) if amt_val < 0 else 0.0
                        credit = amt_val if amt_val > 0 else 0.0
                        rows.append(
                            create_transaction_row(
                                date_str, desc, debit, credit, balance
                            )
                        )
                        last_txn_row = rows[-1]
                        pending_date = None
                        pending_desc = None
                        pending_amount = None
                    else:
                        # Need fee sub-line for balance
                        pending_date = date_str
                        pending_desc = desc
                        pending_amount = (
                            self._parse_bizstart_amount(amounts[0].group())
                            if amounts
                            else 0.0
                        )
                    continue

                # Check for fee sub-line (contains "Fee" and has amounts)
                if "Fee" in line:
                    amounts = list(self.BIZSTART_AMOUNT_PATTERN.finditer(line))
                    if amounts and len(amounts) >= 2:
                        fee_val = self._parse_bizstart_amount(amounts[-2].group())
                        balance = self._parse_bizstart_amount(amounts[-1].group())
                        fee_desc = line[: amounts[0].start()].strip()

                        # A fee marked "( Pending )" has NOT yet been applied to
                        # the printed balance; it is charged later via a separate
                        # "Transaction Fee Recovered" entry. For a normal fee the
                        # printed balance already includes it.
                        is_pending_fee = "( Pending )" in fee_desc

                        if pending_date is not None:
                            if pending_amount is not None and pending_amount != 0.0:
                                # Main transaction + separate fee
                                if is_pending_fee:
                                    # Printed balance is the main transaction's
                                    # balance (fee not yet applied).
                                    balance_before_fee = balance
                                    fee_debit = 0.0
                                else:
                                    balance_before_fee = balance - fee_val
                                    fee_debit = abs(fee_val)
                                debit = (
                                    abs(pending_amount)
                                    if pending_amount < 0
                                    else 0.0
                                )
                                credit = (
                                    pending_amount if pending_amount > 0 else 0.0
                                )
                                rows.append(
                                    create_transaction_row(
                                        pending_date,
                                        pending_desc,
                                        debit,
                                        credit,
                                        balance_before_fee,
                                    )
                                )
                                # Add fee as separate row (zero impact if pending)
                                rows.append(
                                    create_transaction_row(
                                        pending_date,
                                        fee_desc,
                                        fee_debit,
                                        0.0,
                                        balance_before_fee if is_pending_fee else balance,
                                    )
                                )
                                # Reference lines describe the main transaction,
                                # not the fee row.
                                last_txn_row = rows[-2]
                            else:
                                # Fee-only transaction (e.g. "Proof Of Paymt Sms")
                                rows.append(
                                    create_transaction_row(
                                        pending_date,
                                        pending_desc or fee_desc,
                                        0.0 if is_pending_fee else abs(fee_val),
                                        0.0,
                                        balance,
                                    )
                                )
                                last_txn_row = rows[-1]
                        else:
                            # Orphan fee (cross-page continuation)
                            last_date = rows[-1]["Date"] if rows else ""
                            rows.append(
                                create_transaction_row(
                                    last_date,
                                    fee_desc,
                                    0.0 if is_pending_fee else abs(fee_val),
                                    0.0,
                                    balance,
                                )
                            )
                            last_txn_row = rows[-1]

                        pending_date = None
                        pending_desc = None
                        pending_amount = None
                        continue

                    # Pending fee notice without a balance (e.g.
                    # "Unpaid Fee ( Pending ) 150,00-"): no balance impact now, it
                    # is charged later via "Transaction Fee Recovered". Skip it so
                    # it neither creates a row nor pollutes the prior description.
                    if "( Pending )" in line:
                        continue

                    # Real fee with an amount but no printed balance (page-break
                    # wrap, e.g. "Transaction Fee Recovered 137,01-"). Apply it as
                    # a debit with the balance computed from the running balance.
                    if amounts and len(amounts) == 1:
                        fee_val = self._parse_bizstart_amount(amounts[0].group())
                        fee_desc = line[: amounts[0].start()].strip()
                        base = rows[-1]["Balance"] if rows else 0.0
                        # Flush a pending main transaction first, if any.
                        if pending_date is not None and pending_amount:
                            main_balance = round(base + pending_amount, 2)
                            rows.append(
                                create_transaction_row(
                                    pending_date,
                                    pending_desc,
                                    abs(pending_amount) if pending_amount < 0 else 0.0,
                                    pending_amount if pending_amount > 0 else 0.0,
                                    main_balance,
                                )
                            )
                            base = main_balance
                        fee_date = pending_date or (rows[-1]["Date"] if rows else "")
                        rows.append(
                            create_transaction_row(
                                fee_date,
                                fee_desc,
                                abs(fee_val),
                                0.0,
                                round(base - abs(fee_val), 2),
                            )
                        )
                        last_txn_row = rows[-1]
                        pending_date = None
                        pending_desc = None
                        pending_amount = None
                        continue

                # Continuation line: a reference that identifies the payee of the
                # preceding transaction (e.g. "Merch Cap Mca-for Advance 0695" ->
                # Merchant Capital, "Thecp ..." -> Capital Partners). Append it to
                # the main transaction's description so the facility is identifiable.
                # Only attach when not mid-transaction (pending resolved) and the
                # line carries actual reference text.
                if (
                    pending_date is None
                    and last_txn_row is not None
                    and re.search(r"[A-Za-z]", line)
                ):
                    existing = last_txn_row["Description"]
                    if line not in existing:
                        last_txn_row["Description"] = (
                            f"{existing} {line}".strip() if existing else line
                        )

        # Finalize any remaining pending transaction
        if pending_date is not None and pending_amount is not None and pending_amount != 0.0:
            last_balance = rows[-1]["Balance"] if rows else 0.0
            new_balance = last_balance + pending_amount
            debit = abs(pending_amount) if pending_amount < 0 else 0.0
            credit = pending_amount if pending_amount > 0 else 0.0
            rows.append(
                create_transaction_row(
                    pending_date, pending_desc, debit, credit, new_balance
                )
            )

        return pd.DataFrame(rows)

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from ABSA statement.

        Handles multiple formats:
        1. Business Integrator (YYMMDD): EntryNo YYMMDD Description Site Amount Balance
        2. Transaction History (YYYY-MM-DD): Date | Transaction Description | Amount | Balance
        3. BizStart (D Mon YYYY): Date | Description | Amount | Balance (comma-decimal)
        4. Cheque Statement (DD/MM/YYYY): Date | Transaction Description | Charge | Debit | Credit | Balance
        """
        first_page = self._extract_first_page_text()
        format_type = self._detect_format(first_page)

        if format_type == "business_integrator":
            return self._extract_transactions_business_integrator()
        if format_type == "transaction_history":
            return self._extract_transactions_th()
        if format_type == "bizstart":
            return self._extract_transactions_bizstart()
        return self._extract_transactions_cheque()
