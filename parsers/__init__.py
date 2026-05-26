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
from .hellopaisa import HelloPaisaParser

# Registry of all available parsers (order matters for detection)
# More specific keywords should come first; generic ones (e.g. "fnb") last
# to avoid false positives from transaction references.
PARSER_REGISTRY: list[Type[BaseBankParser]] = [
    HelloPaisaParser,
    TymeBankParser,
    AfricanBankParser,
    HBZParser,
    DiscoveryParser,
    InvestecParser,
    BidvestParser,
    ABSAParser,
    NedbankParser,
    StandardBankParser,
    CapitecParser,
    FNBParser,
]

# Map of bank IDs to parser classes
PARSER_MAP: dict[str, Type[BaseBankParser]] = {
    parser.BANK_ID: parser for parser in PARSER_REGISTRY
}

# List of supported bank names
SUPPORTED_BANKS: list[str] = [parser.BANK_NAME for parser in PARSER_REGISTRY]


def _unwrap_java_serialized_pdf(pdf_file: io.BytesIO) -> io.BytesIO:
    """If the buffer is a Java-serialized byte[] wrapping a PDF, extract the PDF.

    Returns a new BytesIO with the raw PDF content, or the original buffer
    unchanged.
    """
    pdf_file.seek(0)
    header = pdf_file.read(2)
    pdf_file.seek(0)
    if header != b"\xac\xed":
        return pdf_file
    content = pdf_file.read()
    pdf_offset = content.find(b"%PDF")
    if pdf_offset > 0:
        return io.BytesIO(content[pdf_offset:])
    pdf_file.seek(0)
    return pdf_file


def detect_bank(pdf_file: io.BytesIO) -> Optional[str]:
    """Detect which bank the statement is from.

    Uses a scoring system based on keyword frequency rather than first-match.
    A bank's own statement will mention its name many times (headers, footers,
    branding), while a transaction referencing another bank will only mention
    it once or twice. The parser with the highest score wins.

    Args:
        pdf_file: PDF file buffer

    Returns:
        Bank ID string or None if not detected
    """
    import pdfplumber
    from pdfplumber.utils.exceptions import PdfminerException

    pdf_file = _unwrap_java_serialized_pdf(pdf_file)

    pdf_file.seek(0)
    try:
        pdf_context = pdfplumber.open(pdf_file)
    except PdfminerException:
        return "INVALID_PDF"
    with pdf_context as pdf:
        if pdf.pages:
            first_page_text = pdf.pages[0].extract_text() or ""
            # Limit to first 20 lines so transaction references to other banks
            # don't inflate scores above the actual issuing bank's branding.
            # Nedbank statements have branding on line 13 (nedbank.co.za) and
            # ABSA Transaction History has "ABSA" on line 6.
            # Also include the last 5 lines (footer) because some FNB formats
            # only carry branding in the page footer.
            _lines = first_page_text.split("\n")
            header_text = "\n".join(_lines[:20] + _lines[-5:])

            best_parser = None
            best_score = 0

            for parser_class in PARSER_REGISTRY:
                score = parser_class.detection_score(header_text)
                if score > best_score:
                    best_score = score
                    best_parser = parser_class

            if best_parser is not None:
                pdf_file.seek(0)
                return best_parser.BANK_ID

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
    pdf_file = _unwrap_java_serialized_pdf(pdf_file)
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
    pdf_file = _unwrap_java_serialized_pdf(pdf_file)
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
    "HelloPaisaParser",
    # Registry and utilities
    "PARSER_REGISTRY",
    "PARSER_MAP",
    "SUPPORTED_BANKS",
    "detect_bank",
    "get_parser",
    "get_parser_by_id",
]
