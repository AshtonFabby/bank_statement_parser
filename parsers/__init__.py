"""Bank statement parsers package.

This package provides parsers for various South African bank statements.
"""

import io
from typing import Optional, Type

from .base import AccountInfo, BaseBankParser
from .capitec import CapitecParser
from .fnb import FNBParser
from .standard_bank import StandardBankParser
from .nedbank import NedbankParser
from .absa import ABSAParser
from .bidvest import BidvestParser
from .investec import InvestecParser
from .discovery import DiscoveryParser
from .hbz import HBZParser
from .african_bank import AfricanBankParser
from .tymebank import TymeBankParser

# Registry of all available parsers (order matters for detection)
PARSER_REGISTRY: list[Type[BaseBankParser]] = [
    TymeBankParser,
    AfricanBankParser,
    HBZParser,
    DiscoveryParser,
    InvestecParser,
    BidvestParser,
    ABSAParser,
    NedbankParser,
    StandardBankParser,
    FNBParser,
    CapitecParser,
]

# Map of bank IDs to parser classes
PARSER_MAP: dict[str, Type[BaseBankParser]] = {
    parser.BANK_ID: parser for parser in PARSER_REGISTRY
}

# List of supported bank names
SUPPORTED_BANKS: list[str] = [parser.BANK_NAME for parser in PARSER_REGISTRY]


def detect_bank(pdf_file: io.BytesIO) -> Optional[str]:
    """Detect which bank the statement is from.

    Args:
        pdf_file: PDF file buffer

    Returns:
        Bank ID string or None if not detected
    """
    import pdfplumber

    with pdfplumber.open(pdf_file) as pdf:
        if pdf.pages:
            first_page_text = pdf.pages[0].extract_text() or ""

            for parser_class in PARSER_REGISTRY:
                if parser_class.can_parse(first_page_text):
                    pdf_file.seek(0)
                    return parser_class.BANK_ID

    pdf_file.seek(0)
    return None


def get_parser(pdf_file: io.BytesIO) -> Optional[BaseBankParser]:
    """Get the appropriate parser for a PDF file.

    Args:
        pdf_file: PDF file buffer

    Returns:
        Parser instance or None if bank not detected
    """
    bank_id = detect_bank(pdf_file)
    if bank_id and bank_id in PARSER_MAP:
        return PARSER_MAP[bank_id](pdf_file)
    return None


def get_parser_by_id(bank_id: str, pdf_file: io.BytesIO) -> Optional[BaseBankParser]:
    """Get a specific parser by bank ID.

    Args:
        bank_id: Bank identifier
        pdf_file: PDF file buffer

    Returns:
        Parser instance or None if bank ID not found
    """
    if bank_id in PARSER_MAP:
        return PARSER_MAP[bank_id](pdf_file)
    return None


__all__ = [
    # Base classes
    "AccountInfo",
    "BaseBankParser",
    # Parser classes
    "CapitecParser",
    "FNBParser",
    "StandardBankParser",
    "NedbankParser",
    "ABSAParser",
    "BidvestParser",
    "InvestecParser",
    "DiscoveryParser",
    "HBZParser",
    "AfricanBankParser",
    "TymeBankParser",
    # Registry and utilities
    "PARSER_REGISTRY",
    "PARSER_MAP",
    "SUPPORTED_BANKS",
    "detect_bank",
    "get_parser",
    "get_parser_by_id",
]
