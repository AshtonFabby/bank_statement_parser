"""Bank statement parsers package.

This package provides parsers for various South African bank statements.
"""

import io
import logging
from pathlib import Path
from typing import Optional, Type, Union

logger = logging.getLogger(__name__)

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


# A statement's own branding sits in its letterhead and footer, while other
# banks appear in the transaction body ("CAPITEC ...PosSettle"). This window
# is therefore the identity zone, and a win inside it is worth trusting.
_IDENTITY_ZONE_HEAD = 50
_IDENTITY_ZONE_TAIL = 10

# How decisive an identity-zone win must be before we stop looking. A bank
# brands its own letterhead heavily (median score 24 across the corpus),
# whereas a passing reference that happens to fall in the zone scores <= 5.
# Measured zero-error plateau on the 194-statement corpus is 6..12; 8 sits in
# the middle. Below this we defer to the wider page rather than trusting a
# thin signal — that is what stops a lone "ABSA" mention hijacking an
# Investec statement.
_IDENTITY_ZONE_MIN_SCORE = 8


def _score_text(text: str):
    """Score every parser against some text.

    Returns ``(best_parser, best_score, runner_up)`` where ``runner_up`` is
    ``(score, bank_id)`` for the next best, or None. The runner-up is what
    lets the caller judge how clear-cut the win was.
    """
    scored = []
    for parser_class in PARSER_REGISTRY:
        score = parser_class.detection_score(text)
        if score > 0:
            scored.append((score, parser_class))

    if not scored:
        return None, 0, None

    # max() keeps the first of equal scores, and PARSER_REGISTRY lists
    # specific banks before generic ones, so ties break as the registry intends.
    best_score, best_parser = max(scored, key=lambda s: s[0])
    others = [(s, c.BANK_ID) for s, c in scored if c is not best_parser]
    return best_parser, best_score, (max(others) if others else None)


def _page_text(pdf, indexes) -> str:
    return "\n".join((pdf[i].get_text() or "") for i in indexes)


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
            # Widen the search in stages, stopping as soon as the evidence is
            # good enough. Scoring the whole page unconditionally does not
            # work: statements that receive payments from another bank repeat
            # that bank's name in the transaction body (a Standard Bank
            # statement carrying six "CAPITEC ...PosSettle" lines scores
            # capitec 60 vs standard_bank 24), so the body must not outvote
            # the letterhead.
            page1 = pdf[0].get_text() or ""
            _lines = page1.split("\n")
            identity_zone = "\n".join(
                _lines[:_IDENTITY_ZONE_HEAD] + _lines[-_IDENTITY_ZONE_TAIL:]
            )

            # 1. Letterhead + footer, trusted only when clearly branded.
            best_parser, best_score, runner_up = _score_text(identity_zone)

            if best_parser is None or best_score < _IDENTITY_ZONE_MIN_SCORE:
                # 2. All of page 1 — catches layouts that brand below the
                #    letterhead (ABSA from line 67, Investec from line 79),
                #    which the zone above cannot see.
                wider = _score_text(page1)
                if wider[0] is not None:
                    best_parser, best_score, runner_up = wider

            if best_parser is None and len(pdf) > 1:
                # 3. Pages 1-3 — some exports carry no branding on page 1 at
                #    all and only identify themselves further in.
                best_parser, best_score, runner_up = _score_text(
                    _page_text(pdf, range(min(3, len(pdf))))
                )

            if best_parser is not None:
                # A narrow margin means the winner is not clearly branded and
                # the pick may be wrong. Misrouting is the costly failure —
                # the caller still gets a 200 with plausible-looking numbers —
                # so make it visible instead of silent.
                if runner_up and best_score - runner_up[0] <= 1:
                    logger.warning(
                        "Low-confidence bank detection: %s (score %d) barely beat "
                        "%s (score %d); the statement may be misrouted.",
                        best_parser.BANK_ID, best_score, runner_up[1], runner_up[0],
                    )
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
