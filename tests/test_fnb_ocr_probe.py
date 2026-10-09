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
def reset_probe(monkeypatch):
    """The probe caches process-wide; isolate each test."""
    fnb._TESSERACT_AVAILABLE = None
    monkeypatch.setattr(fnb.pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
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


def test_finds_windows_installation_outside_path(monkeypatch, tmp_path):
    command = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    command.parent.mkdir()
    command.touch()
    monkeypatch.setattr(fnb.sys, "platform", "win32")
    monkeypatch.setattr(fnb.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    fnb._configure_tesseract_command()
    assert fnb.pytesseract.pytesseract.tesseract_cmd == str(command)


def test_explicit_ocr_command_takes_precedence(monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", "custom/tesseract.exe")
    fnb._configure_tesseract_command()
    assert fnb.pytesseract.pytesseract.tesseract_cmd == "custom/tesseract.exe"


def test_existing_ocr_command_is_preserved(monkeypatch):
    monkeypatch.setattr(fnb.pytesseract.pytesseract, "tesseract_cmd", "configured/tesseract")
    fnb._configure_tesseract_command()
    assert fnb.pytesseract.pytesseract.tesseract_cmd == "configured/tesseract"


def test_path_installation_is_preferred(monkeypatch):
    monkeypatch.setattr(fnb.sys, "platform", "win32")
    monkeypatch.setattr(fnb.shutil, "which", lambda name: "path/tesseract.exe")
    fnb._configure_tesseract_command()
    assert fnb.pytesseract.pytesseract.tesseract_cmd == "tesseract"
