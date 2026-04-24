"""Shared utilities for bank statement parsing."""

import re
from datetime import datetime
from typing import Optional, List, Tuple

# Common month mapping (English and Afrikaans)
MONTH_MAP = {
    # English short forms
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    # English full names
    "january": "01", "february": "02", "march": "03", "april": "04",
    "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    # Afrikaans short forms (same as English for most)
    "jan": "01", "feb": "02", "mrt": "03", "apr": "04",
    "mei": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "okt": "10", "nov": "11", "des": "12",
    # Afrikaans full names
    "januarie": "01", "februarie": "02", "maart": "03", "april": "04",
    "mei": "05", "junie": "06", "julie": "07", "augustus": "08",
    "september": "09", "oktober": "10", "november": "11", "desember": "12",
}

# Common regex patterns
PATTERNS = {
    # Date patterns
    "date_dd_mm_yyyy": re.compile(r"(\d{2}/\d{2}/\d{4})"),
    "date_d_mm_yyyy": re.compile(r"(\d{1,2}/\d{1,2}/\d{4})"),
    "date_yyyy_mm_dd": re.compile(r"(\d{4}/\d{2}/\d{2})"),
    "date_yyyy_mm_dd_dash": re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    "date_dd_mmm_yy": re.compile(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})\b",
        re.IGNORECASE
    ),
    "date_dd_mmm_yyyy": re.compile(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
        re.IGNORECASE
    ),
    "date_dd_mmm": re.compile(
        r"(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
        re.IGNORECASE
    ),
    "date_mmm_dd_yyyy": re.compile(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE
    ),

    # Amount patterns - comprehensive versions
    "amount_standard": re.compile(r"-?[\d,]+\.\d{2}"),
    "amount_with_spaces": re.compile(r"-?\s*[\d\s]+\.\d{2}"),
    "amount_with_cr": re.compile(r"-?[\d,]+\.\d{2}(?:Cr)?"),
    "amount_with_r_prefix": re.compile(r"-?\s*R?\s*[\d\s,]+\.\d{2}-?"),
    # Handles: R 10,274.00, -R980.44, R 983.08-, - R2.75, etc.
    "amount_south_african": re.compile(r"-?\s*R?\s*[\d\s,]+\.\d{2}-?"),
}


def normalize_amount_string(amount_str: str) -> float:
    """Normalize any South African bank amount format to a float.

    Handles formats like:
    - "1,234.56"
    - "1 234.56" (space as thousands separator)
    - "-1,234.56"
    - "R 1,234.56"
    - "- R 1,234.56"
    - "R 1,234.56-" (trailing minus)
    - "+1,234.56"

    Args:
        amount_str: Raw amount string from PDF

    Returns:
        Float value, negative if debit
    """
    if not amount_str:
        return 0.0

    original = amount_str.strip()

    # Determine sign
    is_negative = False
    if original.startswith("-") or original.startswith("- "):
        is_negative = True
    elif original.endswith("-"):
        is_negative = True
    elif original.startswith("+"):
        is_negative = False

    # Clean the string
    clean = original
    clean = clean.replace("R", "").replace("r", "")
    clean = clean.replace("-", "").replace("+", "")
    clean = clean.replace(" ", "")  # Remove spaces (thousands separator)
    clean = clean.replace(",", "")  # Remove commas (thousands separator)
    clean = clean.strip()

    try:
        value = float(clean)
        return -value if is_negative else value
    except ValueError:
        return 0.0


def extract_amounts_from_line(line: str, min_decimals: int = 2) -> List[Tuple[str, float, int]]:
    """Extract all amount patterns from a line with their positions.

    Returns list of tuples: (raw_string, float_value, start_position)
    """
    # Pattern to match various amount formats
    # Handles: 1,234.56, 1 234.56, -1234.56, R 1,234.56, R1234.56-, etc.
    amount_pattern = re.compile(
        r'(-?\s*R?\s*[\d\s,]+\.\d{2})-?|'  # Standard with optional R and trailing minus
        r'([+-]?\d{1,3}(?:[ ,]\d{3})*\.\d{2})'  # Space or comma separated
    )

    results = []
    for match in amount_pattern.finditer(line):
        raw = match.group(0)
        value = normalize_amount_string(raw)
        if value != 0.0 or raw.strip() == "0.00":
            results.append((raw, value, match.start()))

    return results


def parse_date_dd_mm_yyyy(date_str: str) -> str:
    """Parse DD/MM/YYYY format, return as is."""
    return date_str


def parse_date_yyyy_mm_dd(date_str: str) -> str:
    """Convert YYYY/MM/DD to DD/MM/YYYY."""
    parts = date_str.split("/")
    return f"{parts[2]}/{parts[1]}/{parts[0]}"


def parse_date_dd_mmm_yy(day: str, month: str, year: str) -> str:
    """Convert DD MMM YY to DD/MM/YYYY."""
    month_num = MONTH_MAP.get(month.lower(), "01")
    return f"{day.zfill(2)}/{month_num}/20{year}"


def parse_date_dd_mmm_yyyy(day: str, month: str, year: str) -> str:
    """Convert DD MMM YYYY to DD/MM/YYYY."""
    month_num = MONTH_MAP.get(month.lower(), "01")
    return f"{day.zfill(2)}/{month_num}/{year}"


def parse_date_dd_mmm(day: str, month: str, year: str) -> str:
    """Convert DD MMM to DD/MM/YYYY with provided year."""
    month_num = MONTH_MAP.get(month.lower(), "01")
    return f"{day}/{month_num}/{year}"


def parse_date_mmm_dd_yyyy(month: str, day: str, year: str) -> str:
    """Convert MMM DD, YYYY to DD/MM/YYYY."""
    month_num = MONTH_MAP.get(month.lower(), "01")
    return f"{day.zfill(2)}/{month_num}/{year}"


def clean_amount(amount_str: str, remove_spaces: bool = True, remove_commas: bool = True) -> float:
    """Clean and convert amount string to float.

    Args:
        amount_str: Raw amount string
        remove_spaces: Remove spaces (used as thousands separator)
        remove_commas: Remove commas (used as thousands separator)

    Returns:
        Float value of the amount
    """
    clean = amount_str
    if remove_spaces:
        clean = clean.replace(" ", "")
    if remove_commas:
        clean = clean.replace(",", "")
    clean = clean.replace("R", "").replace("Cr", "").strip()

    # Handle negative with space (e.g., "- 302.11")
    clean = re.sub(r"-\s+", "-", clean)

    try:
        return float(clean)
    except ValueError:
        return 0.0


def parse_amount_with_cr(amount_str: str) -> tuple[float, bool]:
    """Parse amount that may have Cr suffix for credit.

    Args:
        amount_str: Amount string possibly ending with 'Cr'

    Returns:
        Tuple of (absolute value, is_credit)
    """
    amount_str = amount_str.strip()
    is_credit = amount_str.endswith("Cr")
    clean_amount_str = amount_str.replace("Cr", "").replace(",", "").strip()

    try:
        value = abs(float(clean_amount_str))
        return value, is_credit
    except ValueError:
        return 0.0, False


def extract_year_from_text(text: str) -> Optional[str]:
    """Extract year from statement text, looking for statement period context.

    Args:
        text: Full text to search

    Returns:
        4-digit year string or None
    """
    # Try statement period context first
    period_match = re.search(
        r"(?:statement\s*period|period)[:\s]*.*?(20\d{2})",
        text, re.IGNORECASE
    )
    if period_match:
        return period_match.group(1)

    # Try full month name with year
    date_year_match = re.search(
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})",
        text, re.IGNORECASE
    )
    if date_year_match:
        return date_year_match.group(1)

    # Last resort: any year starting with 20
    year_match = re.search(r"\b(20\d{2})\b", text)
    if year_match:
        return year_match.group(1)

    return datetime.now().strftime("%Y")


def determine_debit_credit_from_balance(
    current_balance: float,
    previous_balance: Optional[float],
    amount: Optional[float] = None
) -> tuple[float, float]:
    """Determine debit/credit amounts based on balance change.

    Args:
        current_balance: Current transaction balance
        previous_balance: Previous transaction balance (or None if first)
        amount: Known transaction amount (optional)

    Returns:
        Tuple of (debit, credit)
    """
    if previous_balance is None:
        if amount is not None:
            if amount < 0:
                return abs(amount), 0.0
            return 0.0, amount
        return 0.0, 0.0

    diff = current_balance - previous_balance
    if diff < 0:
        return abs(diff), 0.0
    return 0.0, diff


def create_transaction_row(
    date: str,
    description: str,
    debit: float,
    credit: float,
    balance: float
) -> dict:
    """Create a standardized transaction row dictionary.

    Args:
        date: Transaction date in DD/MM/YYYY format
        description: Transaction description
        debit: Debit amount
        credit: Credit amount
        balance: Running balance

    Returns:
        Dictionary with standard column names
    """
    return {
        "Date": date,
        "Description": description,
        "Debit": debit,
        "Credit": credit,
        "Balance": balance,
    }
