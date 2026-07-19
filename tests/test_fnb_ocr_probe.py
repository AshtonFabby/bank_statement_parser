"""Tests for the tesseract availability probe.

FNB renders some fee descriptions as images. If the tesseract binary is
absent those descriptions are lost, so the probe must say so loudly and
exactly once - importing the pytesseract wrapper is not evidence the binary
is installed.
"""

import logging

import pytest

from parsers import fnb


@pytest.fixture(autouse=True)
def reset_probe():
    """The probe caches process-wide; isolate each test."""
    fnb._TESSERACT_AVAILABLE = None
    yield
    fnb._TESSERACT_AVAILABLE = None


def test_available_when_binary_probe_succeeds(monkeypatch):
    monkeypatch.setattr(fnb, "_OCR_IMPORTED", True)
    monkeypatch.setattr(fnb.pytesseract, "get_tesseract_version", lambda: "5.3.0")
    assert fnb._ocr_available() is True


def test_unavailable_when_binary_missing(monkeypatch):
    """pytesseract imports fine but shelling out to tesseract fails."""
    monkeypatch.setattr(fnb, "_OCR_IMPORTED", True)

    def boom():
        raise fnb.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(fnb.pytesseract, "get_tesseract_version", boom)
    assert fnb._ocr_available() is False


def test_unavailable_when_wrapper_not_importable(monkeypatch):
    monkeypatch.setattr(fnb, "_OCR_IMPORTED", False)
    assert fnb._ocr_available() is False


def test_missing_binary_warns_exactly_once(monkeypatch, caplog):
    """A warning per unreadable row would be hundreds of lines per document."""
    monkeypatch.setattr(fnb, "_OCR_IMPORTED", True)

    def boom():
        raise fnb.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(fnb.pytesseract, "get_tesseract_version", boom)

    with caplog.at_level(logging.WARNING, logger="parsers.fnb"):
        for _ in range(5):
            fnb._ocr_available()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "tesseract" in warnings[0].getMessage().lower()


def test_probe_runs_only_once(monkeypatch):
    """Each probe shells out to a subprocess; don't do it per row."""
    monkeypatch.setattr(fnb, "_OCR_IMPORTED", True)
    calls = []

    def counted():
        calls.append(1)
        return "5.3.0"

    monkeypatch.setattr(fnb.pytesseract, "get_tesseract_version", counted)
    for _ in range(5):
        fnb._ocr_available()

    assert len(calls) == 1
