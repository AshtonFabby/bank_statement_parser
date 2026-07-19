"""Cross-bank regression tests over the format corpus.

Three properties, checked for every format family in ``corpus.py``:

1. **Detection** routes the file to the right parser. Misrouting is the worst
   failure mode in this system: the caller still gets a 200 and plausible
   numbers, with nothing to indicate the answer came from the wrong parser.
2. **Parsing** returns transactions without raising.
3. **The balance chain reconciles** — every row follows from the previous one.

Families with known defects are marked ``xfail``, so this suite is green today
and each fix announces itself as an ``xpass``.

Note these assert on *reconciliation*, never on "whatever the parser currently
returns" — a snapshot test would pin today's bugs in place as the expected
answer.

Deliberately **not** asserted: "no row carries both a debit and a credit".
That looks like a misalignment signature, and it is one for Al Baraka, but
Capitec legitimately puts an ATM deposit and its fee on a single row
(11101.61 + 1100.00 - 15.40 = 12186.21, which reconciles). The balance chain
already catches the real defect without the false positives.
"""

import pytest

from corpus import (
    CORPUS,
    CORPUS_GAPS,
    KNOWN_BROKEN,
    MISSING_ANCHOR,
    UNRELIABLE_SOURCE,
    all_entries,
)
from conftest import find_statement

ENTRIES = list(all_entries())
IDS = [f"{fam}::{path.split('/')[-1]}" for fam, _, _, path in ENTRIES]


def _parse(path):
    """Detect + parse a corpus file, skipping when it isn't present."""
    import io

    from parsers import detect_bank, get_parser_by_id

    located = find_statement(path)
    if located is None:
        pytest.skip(f"corpus statement not available: {path}")

    detected = detect_bank(str(located))
    with open(located, "rb") as fh:
        parser = get_parser_by_id(detected, fh) if detected else None
        if parser is None:
            return detected, None, None, 0
        account, df = parser.parse()
        unreadable = getattr(parser, "unreadable_descriptions", 0)
    return detected, account, df, unreadable


@pytest.fixture(scope="session")
def parsed_cache():
    return {}


def _get(parsed_cache, path):
    if path not in parsed_cache:
        parsed_cache[path] = _parse(path)
    return parsed_cache[path]


@pytest.mark.parametrize("family,bank_id,kind,path", ENTRIES, ids=IDS)
def test_detection_routes_to_correct_parser(parsed_cache, family, bank_id, kind, path):
    """A statement must reach its own bank's parser.

    Regression guard for two real failures: branding below the header window
    scoring zero, and a passing reference to another bank outscoring the
    issuer's own letterhead.
    """
    detected, _, _, _ = _get(parsed_cache, path)
    assert detected == bank_id, f"{path} detected as {detected!r}, expected {bank_id!r}"


@pytest.mark.parametrize("family,bank_id,kind,path", ENTRIES, ids=IDS)
def test_parses_transactions(parsed_cache, family, bank_id, kind, path):
    _, _, df, _ = _get(parsed_cache, path)
    assert df is not None, f"{path} produced no parser"
    assert not df.empty, f"{path} parsed zero transactions"


@pytest.mark.parametrize("family,bank_id,kind,path", ENTRIES, ids=IDS)
def test_balance_chain_reconciles(request, parsed_cache, family, bank_id, kind, path):
    """Every transaction's balance must follow from the one before it."""
    from services.verification import verify_and_correct

    if family in KNOWN_BROKEN:
        request.node.add_marker(pytest.mark.xfail(reason=KNOWN_BROKEN[family], strict=False))

    _, account, df, _ = _get(parsed_cache, path)
    assert df is not None and not df.empty

    _, result = verify_and_correct(
        df, path, account.bank, bank_id,
        declared_totals=getattr(account, "declared_totals", None),
    )

    if path in UNRELIABLE_SOURCE:
        # The document's own arithmetic is wrong. Assert that we still *say*
        # so — silently reconciling one of these would be the actual bug.
        assert result.accuracy_percentage < 100.0, (
            f"{path} is a Word-authored statement whose balances do not add up; "
            f"it must not report a clean parse"
        )
        return

    assert result.accuracy_percentage == pytest.approx(100.0), (
        f"{path}: {result.failing_transactions} of {result.verified_transactions} "
        f"rows do not reconcile ({result.accuracy_percentage}%)"
    )


@pytest.mark.parametrize("family,bank_id,kind,path", ENTRIES, ids=IDS)
def test_first_transaction_is_verified(request, parsed_cache, family, bank_id, kind, path):
    """Without an Opening Balance anchor the first transaction is adopted as
    the baseline and silently trusted, and accuracy still reports 100%."""
    from services.verification import verify_and_correct

    if family in KNOWN_BROKEN or family in MISSING_ANCHOR:
        request.node.add_marker(pytest.mark.xfail(
            reason=KNOWN_BROKEN.get(family, "format emits no Opening Balance row"),
            strict=False,
        ))

    _, account, df, _ = _get(parsed_cache, path)
    assert df is not None and not df.empty

    _, result = verify_and_correct(df, path, account.bank, bank_id)
    assert result.unverified_transactions == 0, (
        f"{path}: {result.unverified_transactions} transaction(s) never verified"
    )


def test_every_bank_has_statements_and_histories():
    """The corpus should cover both document kinds for every bank.

    Known gaps are listed in ``CORPUS_GAPS`` — they are collection gaps rather
    than defects, but they are asserted here so that acquiring the missing
    documents makes the test pass rather than going unnoticed.
    """
    by_bank = {}
    for family, (bank_id, kind, paths) in CORPUS.items():
        by_bank.setdefault(bank_id, {"BS": 0, "TH": 0})[kind] += len(paths)

    missing = {
        b: c for b, c in by_bank.items()
        if (c["BS"] == 0 or c["TH"] == 0) and b not in CORPUS_GAPS
    }
    assert not missing, f"banks lacking one document kind: {missing}"
