"""Capitec account-number extraction must ignore transaction references.

Capitec transaction rows wrap the bare word "ACCOUNT" onto a line of its own
next to a long reference number — a payment out to another bank
("...FNB CAPITEC ACCOUNT To 63201624665250655") or a SWIFT reference
("STP Inw Pmt I/W SWFA INTN: 45349838"). An unanchored search over page 1
picks those up as the account number.

That is not a cosmetic mislabel. ``account_number`` builds the ``Source``
column (``main._account_source``), which drives the reported account count,
the per-account balance-chain verification and dedup partitioning. One real
account read as three splits its running balance at the month boundaries and
reports accounts the client does not have.

These six statements are all the same Capitec account, 1054899320. Two of
them contain the reference patterns above.
"""

import pytest

from conftest import load_statement

ACCOUNT = "1054899320"

STATEMENTS = [
    "1._Feb-_9320.pdf",  # carries "INTN: 45349838" after a wrapped "ACCOUNT"
    "2._Mar-9320.pdf",
    "3._Apr-9320.pdf",  # carries "ACCOUNT To 63201624665250655"
    "4._May_-9320.pdf",
    "5._Jun-_9320.pdf",
    "6._Jul_-9320.pdf",  # newer Tax Invoice layout: "Account: ... 1054899320"
]


def _account_info(filename: str):
    from parsers.capitec import CapitecParser

    return CapitecParser(load_statement(filename)).extract_account_info()


@pytest.mark.parametrize("filename", STATEMENTS)
def test_account_number_is_the_header_number(filename):
    assert _account_info(filename).account_number == ACCOUNT


@pytest.mark.parametrize("filename", STATEMENTS)
def test_account_type_is_read(filename):
    assert _account_info(filename).account_type == "Capitec Business Account"


def test_statements_group_into_one_account():
    """The end-to-end property: six files, one Source, one account."""
    from main import _account_source

    sources = {
        _account_source(info.bank, info.account_number)
        for info in (_account_info(f) for f in STATEMENTS)
    }
    assert sources == {f"Capitec ({ACCOUNT})"}
