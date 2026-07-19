import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "bank_statements"


def find_statement(filename: str):
    """Locate a corpus statement by name, or return None.

    Accepts either a bare filename or a ``Bank/file.pdf`` relative path, and
    falls back to a recursive search so the corpus can be reorganised into
    per-bank folders without silently skipping every test.
    """
    direct = CORPUS / filename
    if direct.exists():
        return direct
    matches = sorted(CORPUS.glob("**/" + Path(filename).name))
    return matches[0] if matches else None


def load_statement(filename: str) -> io.BytesIO:
    """Return a statement from the local corpus, or skip.

    ``bank_statements/`` is gitignored (real customer statements), so these
    tests are opportunistic: they pin real parser behaviour when the corpus is
    present and skip cleanly on a fresh clone or in CI.
    """
    path = find_statement(filename)
    if path is None:
        pytest.skip(f"corpus statement not available: {filename}")
    return io.BytesIO(path.read_bytes())


@pytest.fixture(scope="session")
def fnb_jan():
    """Parsed FNB January statement: (AccountInfo, DataFrame)."""
    from parsers import get_parser

    parser = get_parser(load_statement("1._Jan.pdf"))
    return parser.parse()
