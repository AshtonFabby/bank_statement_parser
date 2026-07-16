"""Base parser class defining the interface for all bank statement parsers."""

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import pdfplumber


@dataclass
class AccountInfo:
    """Data class for account information."""
    bank: str
    account_number: Optional[str] = None
    account_type: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "bank": self.bank,
            "account_number": self.account_number,
            "account_type": self.account_type,
        }


class BaseBankParser(ABC):
    """Abstract base class for bank statement parsers."""

    # Subclasses must define these
    BANK_NAME: str = ""
    BANK_ID: str = ""
    # Each keyword can be a string (weight 1) or a (keyword, weight) tuple.
    # Higher weights make a keyword more impactful for bank detection, which
    # helps distinguish a bank's own branding from transaction references to
    # other banks (e.g. "CAPITEC" appearing in a Nedbank statement).
    DETECTION_KEYWORDS: list[str | tuple[str, int]] = []

    def __init__(self, pdf_file: io.BytesIO):
        self.pdf_file = pdf_file
        self._page_texts_cache: Optional[list[str]] = None
        self._reset_file()

    def _reset_file(self) -> None:
        """Reset file pointer to beginning."""
        self.pdf_file.seek(0)

    def _get_page_texts(self) -> list[str]:
        """Extract text for every page in a single pdfplumber pass and cache it.

        Parsers call _iterate_pages/_extract_full_text/_extract_first_page_text
        multiple times per parse; caching the page texts means the PDF is only
        opened and text-extracted once instead of once per call.
        """
        if self._page_texts_cache is None:
            import pdfplumber
            texts = []
            with pdfplumber.open(self.pdf_file) as pdf:
                for page in pdf.pages:
                    texts.append(page.extract_text() or "")
                    page.flush_cache()
            self._reset_file()
            self._page_texts_cache = texts
        return self._page_texts_cache

    def _extract_full_text(self) -> str:
        """Extract all text from the PDF."""
        return "".join(
            text + "\n" for text in self._get_page_texts() if text
        )

    def _extract_first_page_text(self) -> str:
        """Extract text from the first page of the PDF."""
        texts = self._get_page_texts()
        return texts[0] if texts else ""

    def _iterate_pages(self):
        """Yield text content page by page."""
        yield from (text for text in self._get_page_texts() if text)

    def _iterate_pages_with_objects(self):
        """Generator to iterate through PDF pages, yielding (text, page) tuples.

        Useful when parsers need access to the underlying pdfplumber page
        object (e.g. for image extraction / OCR).
        """
        with pdfplumber.open(self.pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    yield text, page
                page.flush_cache()
        self._reset_file()

    @classmethod
    def can_parse(cls, text: str) -> bool:
        """Check if this parser can handle the given PDF text."""
        text_lower = text.lower()
        for keyword in cls.DETECTION_KEYWORDS:
            kw = keyword[0] if isinstance(keyword, tuple) else keyword
            if kw.lower() in text_lower:
                return True
        return False

    @classmethod
    def detection_score(cls, text: str) -> int:
        """Score how confident we are this parser matches the given text.

        Uses keyword frequency * weight to distinguish between a bank's own
        statement (where its name appears many times in headers, footers,
        branding) vs a mere transaction reference to another bank.

        Returns:
            Integer score (0 = no match, higher = more confident)
        """
        text_lower = text.lower()
        score = 0
        for keyword in cls.DETECTION_KEYWORDS:
            if isinstance(keyword, tuple):
                kw, weight = keyword
            else:
                kw, weight = keyword, 1
            score += text_lower.count(kw.lower()) * weight
        return score

    @abstractmethod
    def extract_account_info(self) -> AccountInfo:
        """Extract account information from the statement.

        Returns:
            AccountInfo object with bank details
        """
        pass

    @abstractmethod
    def extract_transactions(self) -> pd.DataFrame:
        """Extract transactions from the statement.

        Returns:
            DataFrame with columns: Date, Description, Debit, Credit, Balance
        """
        pass

    def parse(self) -> tuple[AccountInfo, pd.DataFrame]:
        """Parse the full statement.

        Returns:
            Tuple of (AccountInfo, DataFrame of transactions)
        """
        account_info = self.extract_account_info()
        transactions = self.extract_transactions()
        return account_info, transactions
