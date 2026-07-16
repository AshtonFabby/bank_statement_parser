"""Bank statement parsers package.

This package provides parsers for various South African bank statements.
"""

import io
from pathlib import Path
from typing import Optional, Type, Union

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
from .albaraka import AlbarakaParser

# Registry of all available parsers (order matters for detection)
# More specific keywords should come first; generic ones (e.g. "fnb") last
# to avoid false positives from transaction references.
PARSER_REGISTRY: list[Type[BaseBankParser]] = [
    HelloPaisaParser,
    AlbarakaParser,
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


def detect_bank(pdf_file: Union[str, Path, bytes, bytearray, io.BytesIO]) -> Optional[str]:
    """Detect which bank the statement is from.

    Uses a scoring system based on keyword frequency rather than first-match.
    A bank's own statement will mention its name many times (headers, footers,
    branding), while a transaction referencing another bank will only mention
    it once or twice. The parser with the highest score wins.

    Args:
        pdf_file: Path to a PDF on disk, raw PDF bytes, or a file-like buffer

    Returns:
        Bank ID string or None if not detected
    """
    import fitz

    try:
        if isinstance(pdf_file, (str, Path)):
            # Zero-copy: fitz reads directly from disk (main.py has already
            # unwrapped any Java-serialized wrapper on the temp file).
            pdf_context = fitz.open(str(pdf_file))
        elif isinstance(pdf_file, (bytes, bytearray)):
            pdf_context = fitz.open(stream=pdf_file, filetype="pdf")
        else:
            pdf_file = _unwrap_java_serialized_pdf(pdf_file)
            pdf_file.seek(0)
            # fitz accepts file-like objects directly — no full read() copy.
            pdf_context = fitz.open(stream=pdf_file, filetype="pdf")
    except Exception:
        return "INVALID_PDF"
    with pdf_context as pdf:
        if len(pdf) > 0:
            first_page_text = pdf[0].get_text() or ""
            # Limit to first 20 lines so transaction references to other banks
            # don't inflate scores above the actual issuing bank's branding.
            # Nedbank statements have branding on line 13 (nedbank.co.za) and
            # ABSA Transaction History has "ABSA" on line 6.
            # Also include the last 5 lines (footer) because some FNB formats
            # only carry branding in the page footer.
            _lines = first_page_text.split("\n")
            header_text = "\n".join(_lines[:50] + _lines[-10:])

            best_parser = None
            best_score = 0

            for parser_class in PARSER_REGISTRY:
                score = parser_class.detection_score(header_text)
                if score > best_score:
                    best_score = score
                    best_parser = parser_class

            if best_parser is not None:
                if hasattr(pdf_file, "seek"):
                    pdf_file.seek(0)
                return best_parser.BANK_ID

    if hasattr(pdf_file, "seek"):
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
    "AlbarakaParser",
    # Registry and utilities
    "PARSER_REGISTRY",
    "PARSER_MAP",
    "SUPPORTED_BANKS",
    "detect_bank",
    "get_parser",
    "get_parser_by_id",
]
