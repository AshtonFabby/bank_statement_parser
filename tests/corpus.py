"""Test corpus manifest: representative statements per bank and format.

Selection is by **format family**, not by folder or filename. Two things make
the obvious groupings unreliable:

* The ``TH`` filename suffix is a download convention, not a format. Every
  Al Baraka statement contains the words "transaction history" somewhere, and
  Nedbank calls both its statements and its exports "Statement Enquiry".
* A folder is not a format. ``Capitec/`` holds 30 near-identical statements
  and exactly one client-care export, so sampling at random from the folder
  would almost certainly miss the format that behaves differently.

Families were derived by clustering all 194 corpus statements on their page-1
header signature, then confirmed against what each document calls itself.

``bank_statements/`` is gitignored, so every entry here may be absent on a
fresh clone; tests skip rather than fail (see ``conftest.find_statement``).
"""

# Families whose parse is known to be wrong. Tests assert xfail against these
# so the suite is green today and flips to xpass the moment a fix lands —
# the tests are the completion signal, not a separate checklist.
KNOWN_BROKEN = {}

# Individual files that do not reconcile because the *source document* is
# wrong, not because the parser misread it. These four ABSA statements were
# authored in Microsoft Word rather than by a banking system, and their
# arithmetic is internally inconsistent in equal-and-opposite pairs — the
# signature of hand-transcription (e.g. a balance of 5,125,569.56 where the
# preceding rows require 5,124,569.56).
#
# Every one of the 189 statements produced by real bank systems reconciles at
# >= 99.18%; four of the five Word-authored ones fail at 83-87%. The parser
# must keep reporting them as failures: "fixing" this would suppress exactly
# the signal a prevetting product exists to raise.
UNRELIABLE_SOURCE = {
    "ABSA/4. 1 Dec - 25Dec.pdf",
    "ABSA/4. 25 Dec - 31Dec.pdf",
    "ABSA/5. Jan.pdf",
    "ABSA/6. Feb.pdf",
}

# Banks with no example of a document kind anywhere in the corpus. These are
# collection gaps, not parser defects — the format cannot be regression-tested
# until an example exists.
CORPUS_GAPS = {
    "investec": "no transaction-history export; both Investec formats are statements",
}

# Formats whose documents genuinely do not print an opening balance, so the
# first transaction cannot be verified against anything. Deriving one from the
# first row would be circular — it would restate the row being checked — so
# these stay unverified by design rather than by defect.
MISSING_ANCHOR = {
    "StdBank/txn-export",
    "FNB/txn-history",
}

# family -> (bank_id, kind, [relative paths])
# kind is "BS" (bank statement) or "TH" (transaction history / export).
CORPUS = {
    "ABSA/statement": ("absa", "BS", [
        "ABSA/1. May - 2345.pdf",
        "ABSA/4. Aug - 2345.pdf",
        "ABSA/9. Jan - 2345.pdf",
    ]),
    # Mixes genuine bank exports (1. Sep, 2. Oct - JasperReports/iText) with
    # two Word-authored files whose arithmetic does not reconcile, so the
    # suite covers both the happy path and the tampering signal.
    "ABSA/bio-case": ("absa", "TH", [
        "ABSA/1. Sep.pdf",
        "ABSA/2. Oct.pdf",
        "ABSA/4. 25 Dec - 31Dec.pdf",
        "ABSA/6. Feb.pdf",
    ]),
    "Capitec/statement": ("capitec", "BS", [
        "Capitec/1. Jun.pdf",
        "Capitec/4. Aug-2152.pdf",
        "Capitec/9. Jan-2152.pdf",
    ]),
    "Capitec/client-care": ("capitec", "TH", [
        "Capitec/7. TH.pdf",
    ]),
    "FNB/positional": ("fnb", "BS", [
        "FNB/1. Nov.pdf",
        "FNB/4. Feb.pdf",
        "FNB/6._Jun.pdf",
    ]),
    "FNB/txn-history": ("fnb", "TH", [
        "FNB/7. TH.pdf",
    ]),
    "Investec/statement": ("investec", "BS", [
        "Investec/1. May.pdf",
        "Investec/4. Aug.pdf",
        "Investec/9. Jan.pdf",
    ]),
    "Investec/capital-interest": ("investec", "BS", [
        "Investec/1. Jun.pdf",
        "Investec/4. Sep.pdf",
        "Investec/6. Nov.pdf",
    ]),
    "Nedbank/statement-pdf": ("nedbank", "BS", [
        "Nedbank/1. 25 Apr - 24 May.pdf",
        "Nedbank/4. 25 Jul - 25 Aug.pdf",
        "Nedbank/9. 25 Dec - 24 Jan.pdf",
    ]),
    "Nedbank/stmt-enquiry": ("nedbank", "TH", [
        "Nedbank/1. 1 Apr - 30 Aug - Nedbank.pdf",
        "Nedbank/3. Jul.pdf",
        "Nedbank/9. Jan.pdf",
    ]),
    "StdBank/statement": ("standard_bank", "BS", [
        "Standardbank/1. 24 Apr - 23 May.pdf",
        "Standardbank/3. 24 Jun - 23 Jul.pdf",
        "Standardbank/9. 24 Dec - 23 Jan.pdf",
    ]),
    "StdBank/month-end": ("standard_bank", "BS", [
        "Standardbank/1. 10 Jun - 9 Jul.pdf",
        "Standardbank/4. 10 Sep - 9 Oct.pdf",
        "Standardbank/7. 10 Dec - 9 Jan.pdf",
    ]),
    "StdBank/3-month": ("standard_bank", "TH", [
        "Standardbank/1. 27 Feb - 26 Aug.pdf",
        "Standardbank/3. 19 Nov - 17 Feb.pdf",
        "Standardbank/TH.pdf",
    ]),
    # Deliberately mixes both date directions: this export is emitted
    # newest-first or oldest-first depending on how it was generated, and
    # only the parser's ordering normalisation keeps both reconciling.
    # "2. Dec" and "6. TH" arrive newest-first; "3. Jan" oldest-first.
    "StdBank/txn-export": ("standard_bank", "TH", [
        "Standardbank/2. Dec.pdf",
        "Standardbank/3. Jan.pdf",
        "Standardbank/6. TH.pdf",
    ]),
    "alBaraka/account-stmts": ("albaraka", "BS", [
        "alBaraka/11. Aug.pdf",
        "alBaraka/4. Apr 2025 - alBaraka.pdf",
        "alBaraka/TH - 8411.pdf",
    ]),
    "alBaraka/my-statements": ("albaraka", "TH", [
        "alBaraka/1. Oct.pdf",
        "alBaraka/3. Dec.pdf",
        "alBaraka/Oct 2024 - 1 Oct 2025 - 8411.pdf",
    ]),
    "alBaraka/tax-invoice": ("albaraka", "BS", [
        "alBaraka/1. Aug 2024 - Oct 2024 - alBaraka.pdf",
        "alBaraka/6. Mar.pdf",
        "alBaraka/9. Jun.pdf",
    ]),
}


def all_entries():
    """Yield ``(family, bank_id, kind, relative_path)`` for every corpus file."""
    for family, (bank_id, kind, paths) in sorted(CORPUS.items()):
        for path in paths:
            yield family, bank_id, kind, path
