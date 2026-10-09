"""Regression coverage for the sequential /parse/json -> /report integration."""
import copy
import io
import json
import zipfile

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfReader

import main
from services import pdf_generator


def document(filename, rows, account="123"):
    result = main._build_parse_json_sync([dict(
        filename=filename, bank_name="Standard Bank", bank_id="standard_bank",
        account_number=account, error=None,
        df=pd.DataFrame(rows, columns=["Date", "Description", "Debit", "Credit", "Balance"]),
    )], 1)
    return {key: result[key] for key in ("transactions", "verification")}


@pytest.fixture
def documents():
    # Upload the newer statement first. Date sorting must not turn the two
    # independently valid statements into one invalid same-day balance chain.
    return [
        document("newer.pdf", [
            ["02/01/2026", "Opening balance", 0, 0, 900],
            ["02/01/2026", "Fee", 100, 0, 800],
        ]),
        document("older.pdf", [
            ["01/01/2026", "Opening balance", 0, 0, 1000],
            ["02/01/2026", "Fee", 100, 0, 900],
        ]),
    ]


def test_report_preserves_statement_verification_and_amounts(documents):
    payload = {"documents": documents}
    before = copy.deepcopy(payload)
    frame, summary, coverage, _, _, verification = main._build_analysis_from_records(payload)
    assert [v["filename"] for v in verification] == ["newer.pdf", "older.pdf"]
    assert [v["bank_name"] for v in verification] == ["Standard Bank"] * 2
    assert all(v["accuracy_percentage"] == 100 for v in verification)
    assert all(v["failing_transactions"] == 0 for v in verification)
    assert summary.total_debits == 200
    assert coverage.accounts_detected == 1
    assert "_Document" not in frame.columns
    assert payload == before


def test_overlap_deduplicates_financials_but_not_original_verification():
    rows = [["01/01/2026", "Opening balance", 0, 0, 1000],
            ["02/01/2026", "Payment", 100, 0, 900]]
    first = document("statement.pdf", rows)
    second = document("history.pdf", [rows[0], ["02/01/2026", "Reference payment", 100, 0, 900]])
    other_account = document("other.pdf", rows, account="456")
    _, summary, coverage, _, _, verification = main._build_analysis_from_records(
        {"documents": [first, second, other_account]}
    )
    assert summary.total_debits == 200  # one per account, not one per document
    assert coverage.accounts_detected == 2
    assert len(verification) == 3
    assert sum(v["verified_transactions"] for v in verification) == 3


def test_real_failures_and_unverified_rows_are_not_hidden():
    parsed = document("history.pdf", [
        ["02/01/2026", "Initial entry", 100, 0, 900],
        ["02/01/2026", "Bad balance", 50, 0, 700],
    ])
    expected = parsed["verification"][0]
    assert expected["failing_transactions"] == 1
    assert expected["unverified_transactions"] == 1
    parsed["verification"][0]["declared_totals_match"] = False
    *_, verification = main._build_analysis_from_records({"documents": [parsed]})
    assert verification == parsed["verification"]


@pytest.mark.parametrize("payload", [None, {}, [], {"documents": []},
    {"documents": [{"transactions": [{}], "verification": []}]},
    {"documents": [{"transactions": [{}], "verification": [{"error": "Bad PDF"}]}]},
])
def test_invalid_document_payload_is_rejected(payload):
    with pytest.raises(HTTPException) as error:
        main._decode_report_input(json.dumps(payload))
    assert error.value.status_code == 400


def test_legacy_array_remains_supported(documents):
    rows = [{k: v for k, v in row.items() if not k.startswith("_Statement")}
            for row in documents[0]["transactions"]]
    records = main._decode_report_input(json.dumps(rows))
    *_, verification = main._build_analysis_from_records(records)
    assert verification[0].accuracy_percentage == 100


def test_old_dashboard_flat_array_preserves_original_checks(documents):
    records = [row for document in documents for row in document["transactions"]]
    frame, summary, _, _, _, verification = main._build_analysis_from_records(records)
    assert verification == [d["verification"][0] for d in documents]
    assert all(v["failing_transactions"] == 0 for v in verification)
    assert summary.total_debits == 200
    assert not any(c.startswith("_Statement") for c in frame.columns)


def test_old_dashboard_flat_array_deduplicates_overlapping_documents(documents):
    repeated = document("repeated.pdf", [
        ["01/01/2026", "Opening balance", 0, 0, 1000],
        ["02/01/2026", "Another reference", 100, 0, 900],
    ])
    records = [row for doc in documents + [repeated] for row in doc["transactions"]]
    _, summary, _, _, _, verification = main._build_analysis_from_records(records)
    assert summary.total_debits == 200
    assert len(verification) == 3


def test_old_dashboard_does_not_hide_real_balance_failures():
    parsed = document("bad-balance.pdf", [
        ["02/01/2026", "Initial entry", 100, 0, 900],
        ["02/01/2026", "Bad balance", 50, 0, 700],
    ])
    *_, verification = main._build_analysis_from_records(parsed["transactions"])
    assert verification == parsed["verification"]
    assert verification[0]["failing_transactions"] == 1
    assert verification[0]["unverified_transactions"] == 1


@pytest.mark.parametrize("damage", ["missing_row", "missing_metadata", "mixed_metadata"])
def test_partial_embedded_verification_is_rejected(documents, damage):
    records = copy.deepcopy([row for doc in documents for row in doc["transactions"]])
    if damage == "missing_row":
        records.pop()
    elif damage == "missing_metadata":
        records[0].pop("_StatementVerification")
    else:
        records[-1].pop("_StatementId")
    with pytest.raises(HTTPException) as error:
        main._build_analysis_from_records(records)
    assert error.value.status_code == 400


@pytest.mark.parametrize("flat", [False, True])
def test_report_endpoint_returns_original_verification(documents, monkeypatch, flat):
    monkeypatch.setattr(pdf_generator, "_logo_flowable", lambda **_: None)
    payload = [row for doc in documents for row in doc["transactions"]] if flat else {"documents": documents}
    with TestClient(main.app) as client:
        response = client.post("/report", files={"transactions_file": (
            "transactions.json", json.dumps(payload), "application/json",
        )})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        verification_name = next(n for n in archive.namelist() if n.startswith("verification_"))
        assert json.loads(archive.read(verification_name)) == [d["verification"][0] for d in documents]
        pdf_name = next(n for n in archive.namelist() if n.endswith(".pdf"))
        text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(archive.read(pdf_name))).pages)
        assert "newer.pdf" in text and "older.pdf" in text
        assert "Checked" in text and "Severe" in text


@pytest.mark.parametrize("flat", [False, True])
def test_full_prevet_endpoint_uses_document_payload(documents, monkeypatch, flat):
    monkeypatch.setattr(pdf_generator, "_logo_flowable", lambda **_: None)
    # Keep the PreVet cover minimal; exercise the actual merged bank report.
    monkeypatch.setattr("services.generate_prevet_pdf", lambda _: pdf_generator.generate_summary_pdf(
        pd.DataFrame(), main.calculate_summary(pd.DataFrame()),
    ))
    payload = [row for doc in documents for row in doc["transactions"]] if flat else {"documents": documents}
    with TestClient(main.app) as client:
        response = client.post("/generate-full-prevet", data={"prevet_data": "{}"}, files={
            "transactions_file": ("transactions.json", json.dumps(payload), "application/json"),
        })
    assert response.status_code == 200
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(response.content)).pages)
    assert "newer.pdf" in text and "older.pdf" in text
    assert "Severe" in text
