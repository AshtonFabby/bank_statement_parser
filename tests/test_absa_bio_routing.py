"""ABSA Business Integrator statements must reach the BIO extractor.

The BIO report ships in two renderings of the same data. Each one used to be
rejected by a different gate, and in both cases the extractor itself was fine:
forced past the gates, every statement reconciled at 100%.

* The **JasperReports/iText export** ("BIO CASE 32962001") was gated on
  ``^\\d{4}\\s+\\d{6}\\s+``. The entry number is a running sequence that widens
  over an account's life, so five-digit numbers stopped matching — ``\\d{4}``
  consumes "4988" of "49883 260301" and ``\\s`` then fails against "3". Every
  statement from a client whose sequence had rolled over parsed to zero rows.
* The **browser print-to-PDF** rendering carries no "BIO CASE" string at all,
  and its page title is "Statement Enquiry" — the exact phrase Nedbank claims
  at weight 10 for its own export. With no ABSA branding in the letterhead it
  lost detection outright and was parsed by the Nedbank parser.

These tests use synthetic header text rather than the gitignored corpus, so
they pin the routing on a fresh clone where the PDFs are absent.
"""

import pytest

from parsers.absa import ABSAParser
from parsers.nedbank import NedbankParser

# Letterhead of the browser-printed rendering: name first, number last, and a
# "Statement Enquiry" title shared with Nedbank's export.
BIO_PRINT_HEADER = """\
3/3/26, 9:43 AM about:blank
Statement Enquiry
Tue, Mar 3, 2026 at 09:42:57
Account PIGMENTS AND MASTERBATCHES JHB (PTY) LTD - 4096212091 Branch EASTGATE
AM
Start Date 260201 End Date 260228
Entry Number 00 To 49880
Event
Date Description Site Amount Balance
Number
00 260201 BALANCE B/FORWARD 0.00 -2,757,058.11
49317 260201 MIN SERVICE FEE HEADOFFICE -115.00 -2,757,173.11
"""

# Letterhead of the JasperReports export: number first, "BIO CASE" present.
BIO_CASE_HEADER = """\
BIO CASE 32962001
2026-04-01
Wed, 1 Apr, 2026 at 10:11:53 AM
Account 4096212091 - PIGMENTS AND MASTERBATCHES JHB (PTY) LTD
Branch EASTGATE
Start Date 20260301 End Date 20260331
Entry
Event
No Date Description Site Amount Balance
00 260301 BALANCE B/FORWARD 0.00 -4016384.95
49883 260301 MIN SERVICE FEE HEADOFFICE -115.00 -4016499.95
"""


def test_bio_print_outscores_nedbank():
    """Both layouts say "Statement Enquiry"; ABSA must still win its own."""
    absa = ABSAParser.detection_score(BIO_PRINT_HEADER)
    nedbank = NedbankParser.detection_score(BIO_PRINT_HEADER)

    assert absa > nedbank, (
        f"ABSA BIO print scored {absa} against Nedbank's {nedbank}; the "
        f"statement would be parsed by the wrong bank's parser"
    )


def test_nedbank_keyword_still_carries_its_own_export():
    """The fix must not have been made by weakening Nedbank's keyword."""
    assert ("statement enquiry", 10) in NedbankParser.DETECTION_KEYWORDS


@pytest.mark.parametrize("header", [BIO_PRINT_HEADER, BIO_CASE_HEADER])
def test_both_renderings_route_to_business_integrator(header):
    parser = ABSAParser.__new__(ABSAParser)  # no PDF needed for _detect_format
    assert parser._detect_format(header) == "business_integrator"


@pytest.mark.parametrize("entry_no", ["00", "1927", "49883", "512340"])
def test_entry_number_width_does_not_gate_detection(entry_no):
    """Entry numbers widen as an account's sequence advances."""
    parser = ABSAParser.__new__(ABSAParser)
    header = f"BIO CASE 32962001\n{entry_no} 260301 MIN SERVICE FEE HEADOFFICE -115.00 -4016499.95\n"

    assert parser._detect_format(header) == "business_integrator"


@pytest.mark.parametrize(
    "header", [BIO_PRINT_HEADER, BIO_CASE_HEADER], ids=["print", "bio-case"]
)
def test_account_number_read_from_either_header_ordering(header, monkeypatch):
    parser = ABSAParser.__new__(ABSAParser)
    monkeypatch.setattr(parser, "_extract_first_page_text", lambda: header)

    info = parser.extract_account_info()

    assert info.account_number == "4096212091"
    assert info.account_type == "Business Integrator"
