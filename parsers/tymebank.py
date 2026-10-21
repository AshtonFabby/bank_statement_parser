"""TymeBank statement parser."""

import re

import pandas as pd
import pdfplumber

from .base import AccountInfo, BaseBankParser, DeclaredTotals
from .utils import MONTH_MAP, create_transaction_row


class TymeBankParser(BaseBankParser):
    """Parser for TymeBank statements."""

    BANK_NAME = "TymeBank"
    BANK_ID = "tymebank"
    DETECTION_KEYWORDS = [("tymebank", 5), ("tyme bank", 5), ("gotyme.co.za", 15)]

    DATE_PATTERN = re.compile(
        r"^(\d{2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})",
        re.IGNORECASE
    )
    AMOUNT_PATTERN = re.compile(r"[\d\s]+\.\d{2}")

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from TymeBank statement."""
        full_text = self._extract_full_text()
        if "gotyme.co.za" in full_text.lower():
            header = self._extract_first_page_text()
            account = re.search(r"Account Number:\s*(\d+)", header)
            def total(label):
                match = re.search(label + r"\s+R([\d,]+(?:\.\d{2})?)", header, re.I)
                return float(match.group(1).replace(",", "")) if match else None
            debit, fees = total("Total Debit"), total(r"Total Fees\*?")
            return AccountInfo(
                bank=self.BANK_NAME,
                account_number=account.group(1) if account else None,
                account_type="Current account" if "Current account" in header else None,
                declared_totals=DeclaredTotals(
                    opening_balance=total("Opening balance"),
                    closing_balance=total("Closing balance"),
                    credit_total=total("Total Credit"),
                    debit_total=debit + fees if debit is not None and fees is not None else None,
                ),
            )
        account_number = None
        account_type = None

        # Look for "Account Num. 51111402774"
        acc_match = re.search(
            r"Account\s*Num\.?\s*(\d{11})", full_text, re.IGNORECASE
        )
        if acc_match:
            account_number = acc_match.group(1)

        # Also look for "EveryDay account 51111402774"
        if not account_number:
            acc_match = re.search(
                r"EveryDay\s+account\s+(\d{11})", full_text, re.IGNORECASE
            )
            if acc_match:
                account_number = acc_match.group(1)
                account_type = "EveryDay Account"

        # Get account type
        if not account_type:
            if "everyday" in full_text.lower():
                account_type = "EveryDay Account"
            elif "goalsave" in full_text.lower():
                account_type = "GoalSave Account"

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from TymeBank statement."""
        if "gotyme.co.za" in self._extract_first_page_text().lower():
            return self._extract_gotyme_transactions()
        rows = []

        for page_text in self._iterate_pages():
            for line in page_text.split("\n"):
                line = line.strip()

                if "Date" in line and "Description" in line:
                    continue
                if "Opening Balance" in line and not self.DATE_PATTERN.match(line):
                    continue

                date_match = self.DATE_PATTERN.match(line)
                if not date_match:
                    continue

                date_raw = date_match.group(1)
                date_parts = date_raw.split()
                day = date_parts[0]
                month = MONTH_MAP.get(date_parts[1].lower(), "01")
                year = date_parts[2]
                date_str = f"{day}/{month}/{year}"

                amounts = self.AMOUNT_PATTERN.findall(line)
                if len(amounts) < 1:
                    continue

                cleaned_amounts = [amt.replace(" ", "") for amt in amounts]

                rest_of_line = line[date_match.end():].strip()
                first_amt_match = self.AMOUNT_PATTERN.search(rest_of_line)
                if first_amt_match:
                    description = rest_of_line[:first_amt_match.start()].strip()
                else:
                    description = rest_of_line

                # Remove "-" placeholders
                description = re.sub(r"^\s*-\s*", "", description).strip()

                debit = 0.0
                credit = 0.0
                balance = float(cleaned_amounts[-1]) if cleaned_amounts else 0.0

                # Use balance change to determine debit/credit
                if len(rows) > 0:
                    prev_balance = rows[-1]["Balance"]
                    diff = balance - prev_balance
                    if diff < 0:
                        debit = abs(diff)
                    else:
                        credit = diff
                elif len(cleaned_amounts) >= 2:
                    # First transaction
                    if len(cleaned_amounts) >= 3:
                        debit = float(cleaned_amounts[-3])
                        credit = float(cleaned_amounts[-2])

                rows.append(create_transaction_row(date_str, description, debit, credit, balance))

        return pd.DataFrame(rows)

    def _extract_gotyme_transactions(self) -> pd.DataFrame:
        """Read printed columns, retaining integer amounts and wrapped details.

        The diagonal verification stamp overlaps rows in plain-text extraction.
        Exclude rotated glyphs and use the table's cell borders for row bounds.
        Never infer amounts from balances: that makes verification circular.
        """
        rows = []
        declared = self.extract_account_info().declared_totals
        if declared.opening_balance is not None:
            rows.append(create_transaction_row("", "Opening Balance", 0, 0, declared.opening_balance))
        with pdfplumber.open(self.pdf_file) as pdf:
            for page in pdf.pages:
                clean = page.filter(lambda obj: obj.get("object_type") != "char"
                                    or abs(obj.get("matrix", (1, 0))[1]) < 1e-6)
                words = clean.extract_words()
                header = next((w for w in words if w["text"] == "Date"), None)
                if header is None:
                    continue
                edges = [e for e in page.edges if e["orientation"] == "h"
                         and e["top"] > header["bottom"]
                         and e["x0"] <= header["x0"] < e["x1"]]
                if not edges:
                    raise ValueError("GoTyme transaction table borders not found")
                first_bottom = min(e["top"] for e in edges)
                segments = [e for e in page.edges if e["orientation"] == "h"
                            and abs(e["top"] - first_bottom) < 0.1]
                xs = sorted(set(round(e[k], 3) for e in segments for k in ("x0", "x1")))
                if len(xs) != 7:
                    raise ValueError("Unrecognized GoTyme transaction columns")
                top = header["bottom"]
                for bottom in sorted(set(e["top"] for e in edges)):
                    cells = [" ".join(w["text"] for w in words
                                      if top < (w["top"] + w["bottom"]) / 2 < bottom
                                      and left <= (w["x0"] + w["x1"]) / 2 < right)
                             for left, right in zip(xs, xs[1:])]
                    top = bottom
                    if not self.DATE_PATTERN.fullmatch(cells[0]):
                        continue
                    day, month, year = cells[0].split()
                    def amount(value):
                        return 0.0 if value == "-" else float(value.replace(",", ""))
                    fee, credit, debit, balance = map(amount, cells[2:])
                    rows.append(create_transaction_row(
                        f"{day}/{MONTH_MAP[month.lower()]}/{year}", cells[1], debit + fee, credit, balance))
                page.flush_cache()
        self._reset_file()
        return pd.DataFrame(rows)
