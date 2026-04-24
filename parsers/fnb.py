"""FNB (First National Bank) statement parser."""

import logging
import re
from datetime import datetime

import pandas as pd

from .base import AccountInfo, BaseBankParser
from .utils import (
    MONTH_MAP,
    create_transaction_row,
    extract_year_from_text,
    normalize_amount_string,
)

logger = logging.getLogger(__name__)

# Optional OCR support – gracefully degrade when tesseract is not installed.
try:
    import pytesseract
    from PIL import Image

    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False


class FNBParser(BaseBankParser):
    """Parser for FNB statements."""

    BANK_NAME = "FNB"
    BANK_ID = "fnb"
    DETECTION_KEYWORDS = ["fnb", "first national bank"]

    # Transaction History format: DD MMM YYYY (e.g., "08 Jan 2026")
    # Supports both English and Afrikaans month names (full and short forms)
    DATE_PATTERN_WITH_YEAR = re.compile(
        r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
        r"Januarie|Februarie|Maart|April|Mei|Junie|Julie|Augustus|September|Oktober|November|Desember|"
        r"Jan|Feb|Mrt|Apr|Mei|Jun|Jul|Aug|Sep|Okt|Nov|Des)\s+(\d{4})\b",
        re.IGNORECASE
    )
    # Bank Statement format: DD MMM (e.g., "01 Dec")
    # Supports both English and Afrikaans month names (full and short forms)
    DATE_PATTERN_NO_YEAR = re.compile(
        r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
        r"Januarie|Februarie|Maart|April|Mei|Junie|Julie|Augustus|September|Oktober|November|Desember|"
        r"Jan|Feb|Mrt|Apr|Mei|Jun|Jul|Aug|Sep|Okt|Nov|Des)\b",
        re.IGNORECASE
    )
    # Transaction History ISO format: YYYY-MM-DD (e.g., "2026-04-13")
    DATE_PATTERN_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\b", re.MULTILINE)
    # Transaction History amounts: with CR/DR suffix (English) or KT/DT (Afrikaans)
    AMOUNT_PATTERN_CR_DR = re.compile(r"[\d,]+\.\d{2}\s*(?:CR|DR|KT|DT)", re.IGNORECASE)
    # Bank Statement amounts: plain numbers
    AMOUNT_PATTERN_PLAIN = re.compile(r"[\d,]+\.\d{2}")
    # Numeric amount optionally followed by 'Cr' or 'Kt' (credit txn or credit-balance marker)
    # Supports both English (Cr) and Afrikaans (Kt) credit indicators
    _AMOUNT_WITH_OPT_CR = re.compile(r"([\d,]+\.\d{2})(Cr|Kt)?", re.IGNORECASE)
    # Signed decimal amount with optional commas (for ISO Transaction History ZAR format)
    _ISO_AMOUNT = re.compile(r"-?[\d,]+\.\d{2}")
    # Lines to skip in ISO TH format (headers, footers, noise)
    # Supports both English and Afrikaans headers
    _ISO_SKIP_LINES = re.compile(
        r"(?:TRANSACTION HISTORY|Date Description|Page \d+ of \d+|"
        r"First National Bank|\(NCRCP|Date:|Reference:|Customer:|"
        r"Account Number:|Product Type:|"
        r"TRANSASIE GESKIEDENIS|Datum Beskrywing|Bladsy \d+van \d+|"
        r"Eerste Nasionale Bank|Datum:|Verwysing:|Klient:|"
        r"Rekeningnommer:|Produk Tipe:|Transaksies in RAND)",
        re.IGNORECASE,
    )

    def extract_account_info(self) -> AccountInfo:
        """Extract account info from FNB statement.
        
        Supports both English and Afrikaans statement formats.
        """
        full_text = self._extract_full_text()
        account_number = None
        account_type = None

        # Look for "Selected Account: 62388803027" pattern (new FNB format)
        selected_account_match = re.search(
            r"Selected\s*Account\s*[:\s]+(\d{10,12})", full_text, re.IGNORECASE
        )
        if selected_account_match:
            account_number = selected_account_match.group(1).strip()

        # Look for "Gold Business Account : 62765962941" pattern (English)
        # or "Platinum Business Account : 62116722548" pattern
        # Account type may be preceded by text like "Customer VAT Registration Number Not Provided"
        # Also handles Afrikaans "Nie Verskaf" (Not Provided) prefix
        if not account_number:
            # Process line by line to avoid matching across lines
            for line in full_text.split('\n'):
                account_match = re.search(
                    r"(.*?(?:Gold|Platinum|Silver|Business)\s+Account)\s*:\s*(\d{10,12})", 
                    line, re.IGNORECASE
                )
                if account_match:
                    raw_type = account_match.group(1).strip()
                    # Clean up - remove common prefixes (English and Afrikaans)
                    clean_type = re.sub(r'^(?:Customer VAT Registration Number Not Provided\s+|Nie\s+Verskaf\s+|\d+\s+)', '', raw_type, flags=re.IGNORECASE).strip()
                    account_type = clean_type
                    account_number = account_match.group(2).strip()
                    break

        # Look for Afrikaans account patterns: "Nie Verskaf Platinum Business Account : 62116722548"
        # "Nie Verskaf" means "Not Provided" in Afrikaans - we want to strip it
        # Use [^\n]* to match only on the same line, not across lines
        if not account_number:
            afrikaans_match = re.search(
                r"(Nie\s+Verskaf\s+)?([^\n]*?Account)\s*[:\s]+(\d{10,12})",
                full_text, re.IGNORECASE
            )
            if afrikaans_match:
                raw_type = afrikaans_match.group(2).strip()
                # Clean up the account type - remove "Nie Verskaf" prefix if present
                clean_type = re.sub(r'^Nie\s+Verskaf\s+', '', raw_type, flags=re.IGNORECASE).strip()
                account_type = clean_type
                account_number = afrikaans_match.group(3).strip()

        # Look for Nickname field to use as account type
        if not account_type:
            nickname_match = re.search(
                r"Nickname\s*[:\s]+([\w\s]+?)(?:\n|Selected)", full_text, re.IGNORECASE
            )
            if nickname_match:
                account_type = nickname_match.group(1).strip()

        # Fallback: look for Account Number field (English)
        if not account_number:
            acc_num_match = re.search(
                r"Account\s*Number[:\s]*(\d{10,12})", full_text, re.IGNORECASE
            )
            if acc_num_match:
                account_number = acc_num_match.group(1).strip()

        # Fallback: look for Rekeningnommer field (Afrikaans)
        if not account_number:
            acc_num_match = re.search(
                r"Rekeningnommer[:\s]*(\d{10,12})", full_text, re.IGNORECASE
            )
            if acc_num_match:
                account_number = acc_num_match.group(1).strip()

        return AccountInfo(
            bank=self.BANK_NAME,
            account_number=account_number,
            account_type=account_type,
        )

    def _clean_amount(self, amt: str) -> float:
        """Clean FNB amount string to float."""
        return normalize_amount_string(amt)

    def _extract_statement_period(self, text: str) -> tuple:
        """Extract start/end month+year from statement period text.

        Supports both English and Afrikaans formats.
        Returns (start_month, start_year, end_month, end_year) integers,
        or (None, None, None, None) if not found.
        """
        # English format: "Statement Period : 31 July 2025 to 31 August 2025"
        period_match = re.search(
            r"Statement\s+Period\s*[:\s]*"
            r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{4})"
            r"\s+to\s+"
            r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{4})",
            text, re.IGNORECASE
        )
        if period_match:
            start_month = int(MONTH_MAP.get(period_match.group(2).lower()[:3], "01"))
            start_year = int(period_match.group(3))
            end_month = int(MONTH_MAP.get(period_match.group(5).lower()[:3], "01"))
            end_year = int(period_match.group(6))
            return start_month, start_year, end_month, end_year

        # Afrikaans format: "Staat Periode : 31 Julie 2025 tot 30 Augustus 2025"
        afrikaans_period_match = re.search(
            r"Staat\s+Periode\s*[:\s]*"
            r"(\d{1,2})\s+(Januarie|Februarie|Maart|April|Mei|Junie|Julie|Augustus|"
            r"September|Oktober|November|Desember)\s+(\d{4})"
            r"\s+tot\s+"
            r"(\d{1,2})\s+(Januarie|Februarie|Maart|April|Mei|Junie|Julie|Augustus|"
            r"September|Oktober|November|Desember)\s+(\d{4})",
            text, re.IGNORECASE
        )
        if afrikaans_period_match:
            start_month = int(MONTH_MAP.get(afrikaans_period_match.group(2).lower(), "01"))
            start_year = int(afrikaans_period_match.group(3))
            end_month = int(MONTH_MAP.get(afrikaans_period_match.group(5).lower(), "01"))
            end_year = int(afrikaans_period_match.group(6))
            return start_month, start_year, end_month, end_year

        return None, None, None, None

    def _assign_year(
        self,
        month_num: int,
        start_month,
        start_year,
        end_month,
        end_year,
        fallback_year,
    ) -> str:
        """Return the correct 4-digit year string for a transaction month.

        For statements that cross a year boundary (e.g. Dec 2025 to Jan 2026)
        we use start_year for months >= start_month and end_year otherwise.
        """
        if start_year is not None and end_year is not None:
            if start_year == end_year:
                return str(start_year)
            # Cross-year statement: months in the start_year range stay there
            if month_num >= start_month:
                return str(start_year)
            return str(end_year)
        return fallback_year or str(datetime.now().year)

    # ------------------------------------------------------------------
    # OCR helpers for image-based descriptions
    # ------------------------------------------------------------------

    # Leading OCR noise: stray chars before the '#' that starts every FNB
    # fee description.  Handles "l#", "j#", "lf", "[H", "[e", "ié" etc.
    _OCR_LEADING_NOISE = re.compile(r"^.*?(?=#)")
    # Trailing pipe/dot artefacts from 1-bit image edges.
    _OCR_TRAILING_NOISE = re.compile(r"[\s|.]+$")

    @staticmethod
    def _ocr_image_description(page, line_top: float) -> str | None:
        """Try to OCR a description image at the given y-position.

        FNB renders certain fee descriptions (e.g. #Excess Item Fee) as
        bitmap images instead of text.  When we detect a transaction line
        with no text description we look for an image overlapping the
        description column at that y-position and OCR it.

        Returns the OCR'd text or None.
        """
        if not _OCR_AVAILABLE:
            return None

        from PIL import ImageOps  # deferred – only needed here

        # Find images in the description column area that overlap this line
        for img in page.images:
            # Description column: x0 roughly 30–300, small height (< 35px)
            if img["x0"] < 30 or img["x0"] > 300:
                continue
            if img["srcsize"][1] > 35:
                continue
            # Must overlap vertically with the transaction line
            if abs(img["top"] - line_top) > 6:
                continue

            try:
                # Pad horizontally only – vertical padding bleeds into
                # neighbouring rows and confuses tesseract.
                bbox = (
                    max(0, img["x0"] - 5),
                    img["top"],
                    min(float(page.width), img["x1"] + 5),
                    img["bottom"],
                )
                cropped = page.crop(bbox)
                pil_img = cropped.to_image(resolution=800).original
                # Convert to grayscale and add white border to help tesseract
                gray = pil_img.convert("L")
                bordered = ImageOps.expand(gray, border=30, fill=255)
                raw = pytesseract.image_to_string(
                    bordered, config="--psm 7"
                ).strip()
                if raw:
                    return FNBParser._clean_ocr_text(raw)
            except Exception:
                logger.debug("OCR failed for image at top=%.1f", img["top"])
                continue

        return None

    @classmethod
    def _clean_ocr_text(cls, raw: str) -> str:
        """Remove common OCR artefacts from the raw tesseract output."""
        # Strip everything before the first '#' (all FNB fee descriptions
        # start with '#').  Fall back to the generic noise stripper when
        # no '#' is present.
        if "#" in raw:
            text = cls._OCR_LEADING_NOISE.sub("", raw)
        else:
            # Tesseract sometimes reads '#' as 'lf' or 'if' — re-add '#'
            text = re.sub(r"^[^a-zA-Z]+", "", raw)
            text = re.sub(r"^(?:lf|if)", "#", text)
        text = cls._OCR_TRAILING_NOISE.sub("", text)
        # Common OCR mis-reads on these FNB fee images
        text = re.sub(r"\bltem\b", "Item", text)
        text = re.sub(r"\blfem\b", "Item", text, flags=re.IGNORECASE)
        text = re.sub(r"\bENB\b", "FNB", text)
        return text.strip()

    @staticmethod
    def _find_line_top(
        page_words: list[dict],
        rest_of_line: str,
    ) -> float | None:
        """Find the y-position (top) of a transaction line on the page.

        For description-less lines the ``rest_of_line`` is just amounts
        (e.g. ``"310.00 926,470.39"``).  We locate these amount tokens
        among the page words to pin-point the exact y-position, which is
        more reliable than matching by date when the same date appears
        on many lines.
        """
        # Extract the first amount token from rest_of_line to search for
        amount_match = re.search(r"[\d,]+\.\d{2}", rest_of_line)
        if not amount_match:
            return None
        target = amount_match.group()

        # FNB Amount column: x0 roughly 430–530.  The Accrued Bank
        # Charges column sits further right (x0 > 550), so we exclude it
        # to avoid false matches on small values like "4.00".
        for w in page_words:
            if w["text"] == target and 300 < w["x0"] < 550:
                return round(w["top"], 1)
        return None

    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from FNB statement.

        Handles three formats:
        1. Transaction History (CR/DR): DD MMM YYYY with CR/DR amounts
        2. Bank Statement: DD MMM with plain amounts
        3. Transaction History (ISO): YYYY-MM-DD with ZAR amounts
        """
        rows = []
        previous_balance = None
        current_year = None
        start_month = start_year = end_month = end_year = None
        iso_mode = False
        iso_desc_fragments: list[str] = []

        for page_text, page in self._iterate_pages_with_objects():
            # Detect ISO Transaction History format early
            if not iso_mode:
                if self.DATE_PATTERN_ISO.search(page_text) and "ZAR" in page_text:
                    iso_mode = True

            # Extract year from statement period if not yet found (for old format)
            if not current_year:
                current_year = extract_year_from_text(page_text)

            # Extract the full statement period (start + end dates with years)
            # so we can assign the correct year to cross-year statements.
            if start_year is None:
                s_mo, s_yr, e_mo, e_yr = self._extract_statement_period(page_text)
                if s_yr is not None:
                    start_month, start_year, end_month, end_year = s_mo, s_yr, e_mo, e_yr

            # Pre-extract words once per page (used by _find_line_top)
            page_words = page.extract_words()

            for line in page_text.split("\n"):
                line = line.strip()

                if not line:
                    continue
                if self._ISO_SKIP_LINES.search(line):
                    continue
                if "Date" in line and "Description" in line:
                    continue
                if "Balance" in line and "Amount" in line:
                    continue
                if "Service Fee" in line and "Closing Balance" in line:
                    continue

                # Handle Opening/Statement Balance (English and Afrikaans)
                line_lower = line.lower()
                if ("opening balance" in line_lower or "statement balance" in line_lower or
                    "openingsaldo" in line_lower):
                    amounts = self.AMOUNT_PATTERN_PLAIN.findall(line)
                    if amounts:
                        balance = self._clean_amount(amounts[0])
                        # Check for Dr/Cr/Dt/Kt suffix right after the first amount.
                        # FNB format: "Opening Balance 2,843.13Dr" or "... 5,000.00Cr"
                        # Afrikaans: "Openingsaldo 373,625.17Dt" or "... 384,020.98Kt"
                        after_amount = line[line.find(amounts[0]) + len(amounts[0]):].lstrip()
                        after_lower = after_amount.lower()
                        # Cr/Kt = credit balance (positive), Dr/Dt = debit balance (negative)
                        is_cr_balance = after_lower.startswith("cr") or after_lower.startswith("kt")
                        if not is_cr_balance:
                            balance = -balance
                        rows.append(create_transaction_row(
                            "", "Opening Balance", 0.0, 0.0, balance
                        ))
                        previous_balance = balance
                    continue

                # Try ISO Transaction History format first if detected
                iso_match = self.DATE_PATTERN_ISO.match(line)
                if iso_mode and iso_match and "ZAR" in line:
                    iso_desc_fragments_str = " ".join(iso_desc_fragments).strip()
                    iso_desc_fragments.clear()
                    row = self._parse_iso_transaction_line(
                        line, iso_match, iso_desc_fragments_str
                    )
                    if row:
                        rows.append(row)
                        previous_balance = row["Balance"]
                    continue

                # Collect description fragments for ISO mode (non-date lines
                # that precede a date line belong to the next transaction)
                if iso_mode and not self.DATE_PATTERN_WITH_YEAR.match(line) \
                        and not self.DATE_PATTERN_NO_YEAR.match(line):
                    iso_desc_fragments.append(line)
                    continue

                # Try matching with year first (Transaction History format)
                date_match = self.DATE_PATTERN_WITH_YEAR.match(line)
                has_year = True

                if not date_match:
                    # Try matching without year (Bank Statement format)
                    date_match = self.DATE_PATTERN_NO_YEAR.match(line)
                    has_year = False

                if not date_match:
                    continue

                # Parse date
                day = date_match.group(1)
                month_abbr = date_match.group(2).lower()
                month = MONTH_MAP.get(month_abbr, "01")

                if has_year:
                    year = date_match.group(3)
                else:
                    month_num = int(month)
                    year = self._assign_year(
                        month_num,
                        start_month, start_year,
                        end_month, end_year,
                        current_year,
                    )

                date_str = f"{day}/{month}/{year}"

                # Get the rest of the line after the date
                rest_of_line = line[date_match.end():].strip()

                # Determine format:
                # - Transaction History: BOTH the amount AND balance carry a
                #   CR/DR suffix (e.g. "284.77 CR 0.00 CR") -> >=2 CR/DR tokens
                # - Bank Statement: only credit amounts carry a 'Cr' suffix;
                #   the running balance is a plain number -> <=1 CR/DR token
                #   Exception: when account flips to credit both amount and
                #   balance may have 'Cr' -> treated as Transaction History.
                cr_dr_matches = self.AMOUNT_PATTERN_CR_DR.findall(rest_of_line)
                has_cr_dr = len(cr_dr_matches) >= 2

                if has_cr_dr:
                    # Transaction History format with CR/DR
                    row = self._parse_transaction_history_line(rest_of_line, date_str)
                    if row:
                        rows.append(row)
                        previous_balance = row["Balance"]
                else:
                    # Bank Statement format with plain amounts (and optional Cr)
                    row = self._parse_bank_statement_line(rest_of_line, date_str, previous_balance)
                    if row:
                        # If description is missing, attempt OCR on any
                        # image rendered in the description column.
                        if not row.get("Description"):
                            line_top = self._find_line_top(
                                page_words, rest_of_line,
                            )
                            if line_top is not None:
                                ocr_desc = self._ocr_image_description(page, line_top)
                                if ocr_desc:
                                    row["Description"] = ocr_desc
                        rows.append(row)
                        previous_balance = row["Balance"]

        # ISO Transaction History lists rows in reverse chronological order
        # (newest first). Reverse them so the DataFrame is chronological.
        if iso_mode and rows:
            rows.reverse()

        return pd.DataFrame(rows)

    def _parse_iso_transaction_line(
        self, line: str, date_match: re.Match, prefix_description: str
    ) -> dict | None:
        """Parse a line from ISO Transaction History format (YYYY-MM-DD ZAR).

        Format: YYYY-MM-DD [Description] ZAR [-]Amount ServiceFee Balance
        Example: 2026-04-11 material mbk01 ZAR -3000.00 8.00 924.91
        Example: 2026-04-13 ZAR 80.00 0.00 1004.91

        Negative amounts are debits, positive amounts are credits.
        Service fees are additional debits that reduce the running balance.
        Multi-line descriptions are passed via prefix_description.
        """
        year = date_match.group(1)
        month = date_match.group(2)
        day = date_match.group(3)
        date_str = f"{day}/{month}/{year}"

        # Everything after the date
        rest = line[date_match.end():].strip()

        # Split at "ZAR" to separate description from amounts
        zar_split = rest.split("ZAR", 1)
        if len(zar_split) < 2:
            return None

        inline_desc = zar_split[0].strip()
        amount_part = zar_split[1].strip()

        # Combine prefix (multi-line) description with inline description
        parts = []
        if prefix_description:
            parts.append(prefix_description)
        if inline_desc:
            parts.append(inline_desc)
        description = " ".join(parts) or None

        # Extract all signed decimal amounts after ZAR
        # Format: [-]Amount ServiceFee Balance
        amounts = self._ISO_AMOUNT.findall(amount_part)
        if len(amounts) < 2:
            return None

        amount_val = self._clean_amount(amounts[0])
        balance = self._clean_amount(amounts[-1])

        # Service fee: present when there are 3+ amounts (between Amount and Balance)
        service_fee = 0.0
        if len(amounts) >= 3:
            service_fee = self._clean_amount(amounts[1])

        # Determine debit/credit from the sign of the raw amount string
        raw_amount_str = amounts[0].strip()
        if raw_amount_str.startswith("-"):
            # Debit: do NOT add service fee to debit (balance column excludes it)
            debit = abs(amount_val)
            credit = 0.0
        else:
            # Credit: service fee is a separate debit
            credit = amount_val
            debit = service_fee

        return create_transaction_row(date_str, description, debit, credit, balance)

    def _parse_transaction_history_line(self, rest_of_line: str, date_str: str) -> dict:
        """Parse a line from Transaction History format (with CR/DR or KT/DT).

        Supports both English (CR/DR) and Afrikaans (KT/DT - Krediet/Debet) formats.

        Format: Description Service_Fee Amount Balance
        Example: CITIBANK IQVIA1004848 0.00 284.77 CR 0.00 CR
        Afrikaans Example: GRANIET 15.00 -3,240.00 DR -382,371.31 DR
        """
        # Find all amounts with CR/DR suffix
        amounts = self.AMOUNT_PATTERN_CR_DR.findall(rest_of_line)

        # Need at least 2 amounts: Amount and Balance
        if len(amounts) < 2:
            return None

        # Extract description (everything before the first amount)
        first_amount_match = self.AMOUNT_PATTERN_CR_DR.search(rest_of_line)
        if first_amount_match:
            description = rest_of_line[:first_amount_match.start()].strip()
            # Strip trailing service fee decimal (e.g. "0.00" or "4.62") and
            # any sign character that precedes the transaction amount column.
            description = re.sub(r'\s+\d[\d,]*\.\d+\s*[-+]?\s*$', '', description).strip()
        else:
            return None

        # The last amount is the Balance
        balance_str = amounts[-1]
        balance_val, balance_is_credit = self._parse_amount_cr_dr(balance_str)
        balance = balance_val if balance_is_credit else -balance_val

        # The second-to-last amount is the transaction Amount
        amount_str = amounts[-2]
        amount_val, amount_is_credit = self._parse_amount_cr_dr(amount_str)

        # CR means credit (money in), DR means debit (money out)
        if amount_is_credit:
            credit = amount_val
            debit = 0.0
        else:
            debit = amount_val
            credit = 0.0

        return create_transaction_row(date_str, description, debit, credit, balance)

    def _parse_bank_statement_line(self, rest_of_line: str, date_str: str, previous_balance: float) -> dict:
        """Parse a line from Bank Statement format (plain amounts).

        Supports both English and Afrikaans formats.

        FNB bank statement column layout:
          Description  Amount[Cr/Kt]  Balance[Cr/Kt]  [Accrued_Bank_Charges]

        Key rules:
        - A 'Cr' (English) or 'Kt' (Afrikaans) suffix on the TRANSACTION AMOUNT
          token = credit (money in).
        - A 'Cr'/'Kt' suffix on the BALANCE token = account has a positive/credit
          balance. This does NOT make the transaction a credit.
        - The optional 'Accrued Bank Charges' column is a small fixed fee
          (always < R30) appended at the end for fee-bearing transactions.

        We identify each column positionally: the last token is the bank charge
        (if small), the second-to-last is the balance, and everything before
        that is the transaction amount(s).
        """
        # Scan all numeric tokens in order, recording whether each has 'Cr' suffix
        found = []  # list of (float_value, has_cr, start_pos_in_rest_of_line)
        for m in self._AMOUNT_WITH_OPT_CR.finditer(rest_of_line):
            raw = m.group(1)
            has_cr = bool(m.group(2))
            val = self._clean_amount(raw)
            # Require at least 3 digits (e.g. "3.68" -> digits "368") to avoid
            # picking up version numbers or short reference codes
            if len(raw.replace(",", "").replace(".", "")) >= 3:
                found.append((val, has_cr, m.start()))

        if len(found) < 2:
            return None

        # Column layout: Amount[Cr] Balance[Cr] [Accrued_Bank_Charges]
        # When 3+ numeric tokens are present the last is always the optional
        # bank-charge column; the second-to-last is the running balance.
        if len(found) >= 3:
            balance = found[-2][0]
            balance_has_cr = found[-2][1]
            tx_entries = found[:-2]
        else:
            balance = found[-1][0]
            balance_has_cr = found[-1][1]
            tx_entries = found[:-1]

        if not tx_entries:
            return None

        # FNB Bank Statement uses Dr/Cr (English) or Dt/Kt (Afrikaans) sign convention on the balance:
        #   Cr/Kt suffix = credit balance (positive, money in your account)
        #   No Cr/Kt suffix = debit balance (negative, money you owe / overdraft)
        # Convert Dr/Dt balances to negative so that balance progression math
        # works correctly: previous_balance + credit - debit = new_balance.
        if not balance_has_cr:
            balance = -balance

        # Description: everything before the first numeric token
        description = rest_of_line[: found[0][2]].strip() or None

        # Debit / credit determination:
        # Use the 'Cr'/'Kt' flag of the TRANSACTION AMOUNT token only.
        # When the account has a positive balance the running balance also carries
        # 'Cr'/'Kt' -- that must not be confused with an incoming credit transaction.
        tx_val, tx_has_cr, _ = tx_entries[-1]
        if tx_has_cr:
            debit, credit = 0.0, tx_val
        else:
            debit, credit = tx_val, 0.0

        return create_transaction_row(date_str, description, debit, credit, balance)

    def _parse_amount_cr_dr(self, amount_str: str) -> tuple[float, bool]:
        """Parse amount with CR/DR/KT/DT suffix.

        Supports both English (CR/DR) and Afrikaans (KT/DT) formats.

        Args:
            amount_str: Amount string with CR/DR/KT/DT suffix (e.g., "284.77 CR", "1,178.06 DR", "5,000.00 Kt")

        Returns:
            Tuple of (absolute value, is_credit)
        """
        amount_str = amount_str.strip().upper()
        # Check for credit indicators: CR (English) or KT (Afrikaans - Krediet)
        is_credit = "CR" in amount_str or "KT" in amount_str

        # Remove CR/DR/KT/DT and clean the amount
        clean_str = (amount_str.replace("CR", "").replace("DR", "")
                     .replace("KT", "").replace("DT", "")
                     .replace(",", "").strip())

        try:
            value = abs(float(clean_str))
            return value, is_credit
        except ValueError:
            return 0.0, False
