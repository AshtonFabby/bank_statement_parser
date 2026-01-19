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
    DETECTION_KEYWORDS: list[str] = []

    def __init__(self, pdf_file: io.BytesIO):
        self.pdf_file = pdf_file

    def _reset_file(self) -> None:
        """Reset file pointer to beginning."""
        self.pdf_file.seek(0)

    def _extract_full_text(self) -> str:
        """Extract all text from PDF."""
        full_text = ""
        with pdfplumber.open(self.pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
        self._reset_file()
        return full_text

    def _extract_first_page_text(self) -> str:
        """Extract text from first page only."""
        with pdfplumber.open(self.pdf_file) as pdf:
            if pdf.pages:
                text = pdf.pages[0].extract_text() or ""
                self._reset_file()
                return text
        self._reset_file()
        return ""

    def _iterate_pages(self):
        """Generator to iterate through PDF pages."""
        with pdfplumber.open(self.pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    yield text
        self._reset_file()

    @classmethod
    def can_parse(cls, text: str) -> bool:
        """Check if this parser can handle the given PDF text."""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in cls.DETECTION_KEYWORDS)

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
