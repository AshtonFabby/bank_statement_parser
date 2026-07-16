"""Bank Statement Parser API.

A FastAPI application for parsing South African bank statements.
Supports multiple banks with auto-detection.
"""

import asyncio
import contextlib
import gc
import io
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote

import anyio
import certifi
import httpx
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pypdf import PdfReader, PdfWriter

from parsers import SUPPORTED_BANKS, detect_bank, get_parser_by_id
from services import (
    calculate_activity_volume,
    calculate_coverage,
    calculate_revenue,
    calculate_summary,
    verify_and_correct,
)

app = FastAPI(
    title="Bank Statement Parser API",
    description="Parse bank statements from major South African banks. Supports single or multiple file uploads or URLs.",
    version="2.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Resource limits — the API runs on a 1GB instance, so every ingress path is
# size-capped and heavy work is serialized through a single semaphore slot.
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(30 * 1024 * 1024)))
MAX_FILES_PER_REQUEST = int(os.getenv("MAX_FILES_PER_REQUEST", "20"))
MAX_JSON_UPLOAD_BYTES = int(os.getenv("MAX_JSON_UPLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(100 * 1024 * 1024)))

# Limits concurrent heavy requests (parse/report generation). Waiting requests
# queue on the event loop at near-zero memory cost instead of each holding
# PDFs and DataFrames simultaneously.
_HEAVY_SEMAPHORE = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT_HEAVY", "1")))


@app.middleware("http")
async def _reject_oversized_bodies(request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={
                "detail": f"Request body too large (max {MAX_BODY_BYTES // (1024 * 1024)}MB)."
            },
        )
    return await call_next(request)


def _upload_size(file: UploadFile) -> Optional[int]:
    """Best-effort size of an UploadFile without reading it into memory."""
    if file.size is not None:
        return file.size
    try:
        file.file.seek(0, os.SEEK_END)
        size = file.file.tell()
        file.file.seek(0)
        return size
    except (OSError, AttributeError):
        return None


async def _read_json_upload(file: UploadFile) -> str:
    """Read a JSON upload with a size cap."""
    size = _upload_size(file)
    if size is not None and size > MAX_JSON_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"JSON upload too large (max {MAX_JSON_UPLOAD_BYTES // (1024 * 1024)}MB).",
        )
    return (await file.read()).decode()


@app.get("/")
def read_root():
    """Get API information and supported banks."""
    return {
        "message": "Bank Statement Parser API",
        "version": "2.2.0",
        "supported_banks": SUPPORTED_BANKS,
        "endpoints": {
            "/parse": "POST - Upload one or more PDFs or provide links (returns ZIP with combined Excel + PDF)",
            "/parse/json": "POST - Upload one or more PDFs or provide links (returns JSON with combined results)",
        },
    }


@app.get("/banks")
def list_banks():
    """List all supported banks."""
    return {"supported_banks": SUPPORTED_BANKS}


_PASS_PATTERN = re.compile(r"_pass=([^_.]+)", re.IGNORECASE)


def _error_result(
    filename: str, error: str, status_code: int = 400, bank_name: Optional[str] = None
) -> dict:
    return {
        "filename": filename,
        "bank_name": bank_name,
        "summary": None,
        "df": None,
        "error": error,
        "status_code": status_code,
    }


def _normalize_pdf_file(pdf_path: str, filename: str) -> str:
    """Unwrap Java-serialized wrappers and decrypt password-protected PDFs
    in place on the temp file. Returns the cleaned filename.

    Some bank portals serve statements as Java serialized objects where the
    PDF byte stream is stored inside a byte[] array; the serialization header
    is stripped. A `_pass=<password>` segment in the filename triggers
    decryption via pypdf.
    """
    with open(pdf_path, "rb") as f:
        header = f.read(2)
    if header == b"\xac\xed":
        contents = Path(pdf_path).read_bytes()
        pdf_start = contents.find(b"%PDF")
        if pdf_start > 0:
            Path(pdf_path).write_bytes(contents[pdf_start:])
        del contents

    match = _PASS_PATTERN.search(filename)
    if not match:
        return filename

    password = match.group(1)
    clean_filename = filename[: match.start()] + filename[match.end() :]

    reader = PdfReader(io.BytesIO(Path(pdf_path).read_bytes()))
    if reader.is_encrypted:
        reader.decrypt(password)

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    with open(pdf_path, "wb") as f:
        writer.write(f)
    return clean_filename


def _parse_pdf_path_sync(pdf_path: str, filename: str) -> dict:
    """Parse a PDF from disk and return a structured result.

    Runs in a worker thread — must not raise HTTPException; errors are
    reported via the result dict's `error`/`status_code` fields.
    """
    try:
        filename = _normalize_pdf_file(pdf_path, filename)
    except Exception as e:
        return _error_result(filename, f"Failed to read PDF: {str(e)}")

    bank_id = detect_bank(pdf_path)
    if bank_id == "INVALID_PDF":
        return _error_result(
            filename,
            "Could not be opened. The file may be corrupted or is not a valid PDF.",
        )
    if not bank_id:
        return _error_result(filename, "Bank not recognised.")

    with open(pdf_path, "rb") as pdf_handle:
        parser = get_parser_by_id(bank_id, pdf_handle)
        if not parser:
            return _error_result(filename, "Bank not recognised.")

        try:
            account_info, df = parser.parse()
        except Exception as e:
            return _error_result(
                filename,
                f"Failed to parse statement: {str(e)}",
                status_code=500,
                bank_name=parser.BANK_NAME,
            )
        finally:
            del parser

    if df.empty:
        return _error_result(
            filename, "No transactions detected.", bank_name=account_info.bank
        )

    summary = calculate_summary(df)

    return {
        "bank_name": account_info.bank,
        "bank_id": bank_id,
        "account_number": account_info.account_number,
        "summary": summary,
        "df": df,
        "filename": filename,
        "error": None,
    }


async def _parse_pdf_file(
    pdf_path: str, filename: str, raise_on_error: bool = True
) -> dict:
    """Run the blocking parse in a worker thread so the event loop stays free."""
    result = await anyio.to_thread.run_sync(_parse_pdf_path_sync, pdf_path, filename)
    if result["error"] and raise_on_error:
        raise HTTPException(
            status_code=result.get("status_code", 400), detail=result["error"]
        )
    return result


async def process_single_file(file: UploadFile, raise_on_error: bool = True) -> dict:
    """Process a single PDF upload and return parsed data.

    The upload is copied to a temp file and parsed from disk so the PDF bytes
    never need to be held in memory.
    """
    if not file.filename.lower().endswith(".pdf"):
        error_msg = "Only PDF files are accepted."
        if raise_on_error:
            raise HTTPException(status_code=400, detail=error_msg)
        return _error_result(file.filename, error_msg)

    size = _upload_size(file)
    if size is not None and size > MAX_PDF_BYTES:
        error_msg = f"File too large (max {MAX_PDF_BYTES // (1024 * 1024)}MB)."
        if raise_on_error:
            raise HTTPException(status_code=413, detail=error_msg)
        return _error_result(file.filename, error_msg, status_code=413)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
            await file.seek(0)
            await anyio.to_thread.run_sync(shutil.copyfileobj, file.file, tmp)
        return await _parse_pdf_file(tmp_path, file.filename, raise_on_error)
    finally:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def _sanitize_for_json(obj):
    """Recursively replace NaN/Infinity with None so the result is JSON-safe."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def _standardize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize a transaction DataFrame so Excel and PDF use identical clean data.

    Takes ownership of `df` and may mutate it — callers must pass a frame they
    no longer need (all current call sites pass freshly-built frames).

    - Coerces Debit/Credit/Balance to numeric (invalid → 0.0)
    - Parses Date with dayfirst=True, drops rows with unparseable dates
    - Re-formats Date back to DD/MM/YYYY strings for consistent downstream use
    """
    for col in ("Debit", "Credit", "Balance"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if "Date" in df.columns:
        parsed = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
        df = df[parsed.notna()].copy()
        parsed = parsed[parsed.notna()]
        df["_parsed_date"] = parsed
        df["Date"] = parsed.dt.strftime("%d/%m/%Y")
        df = df.sort_values("_parsed_date", kind="stable").reset_index(drop=True)
        df = df.drop(columns=["_parsed_date"])

    return df


def _verify_grouped_results(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, list]:
    """Verify and correct transactions, grouping by Source column if present.

    Returns a tuple of (corrected_df, verification_results) where
    verification_results is a list of VerificationResult objects
    suitable for passing to generate_summary_pdf().
    """
    verification_results: list = []
    corrected_dfs: list[pd.DataFrame] = []

    if "Source" in df.columns and df["Source"].nunique() > 1:
        for source_name, group in df.groupby("Source", sort=False):
            corrected_df, vr = verify_and_correct(
                group.reset_index(drop=True),
                filename=str(source_name),
                bank_name=str(source_name),
                bank_id="",
                account_number=None,
            )
            verification_results.append(vr)
            corrected_df["Source"] = source_name
            corrected_dfs.append(corrected_df)
        combined = pd.concat(corrected_dfs, ignore_index=True)
    else:
        source = df["Source"].iloc[0] if "Source" in df.columns else "combined"
        corrected_df, vr = verify_and_correct(
            df.reset_index(drop=True),
            filename=str(source),
            bank_name=str(source),
            bank_id="",
            account_number=None,
        )
        verification_results.append(vr)
        combined = corrected_df

    return combined, verification_results


def _deduplicate_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate transactions that arise when a bank statement and transaction
    history overlap in date range.  Two rows are considered duplicates when they share
    the same Date, Description, Debit, and Credit values (Balance is intentionally
    excluded because it can differ between document types for the same transaction).
    The first occurrence is kept so bank-statement data takes precedence over
    transaction-history data when files are ordered that way.
    """
    return df.drop_duplicates(
        subset=["Date", "Description", "Debit", "Credit"], keep="first"
    ).reset_index(drop=True)


async def process_single_url(url: str, raise_on_error: bool = True) -> dict:
    """Download a PDF from a URL (streamed to disk, size-capped) and parse it."""
    filename = unquote(url.split("/")[-1].split("?")[0])
    if not filename.lower().endswith(".pdf"):
        filename = filename + ".pdf"

    def _too_large(detail: str) -> dict:
        if raise_on_error:
            raise HTTPException(status_code=413, detail=detail)
        return _error_result(filename, detail, status_code=413)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".pdf", delete=False
        ) as tmp:
            tmp_path = tmp.name
            async with httpx.AsyncClient(
                timeout=120.0, verify=certifi.where()
            ) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        error_msg = (
                            f"Failed to download file: HTTP {response.status_code}"
                        )
                        if raise_on_error:
                            raise HTTPException(status_code=400, detail=error_msg)
                        return _error_result(filename, error_msg)

                    content_length = response.headers.get("content-length")
                    if content_length and content_length.isdigit():
                        if int(content_length) > MAX_PDF_BYTES:
                            return _too_large(
                                f"File too large (max {MAX_PDF_BYTES // (1024 * 1024)}MB)."
                            )

                    received = 0
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > MAX_PDF_BYTES:
                            return _too_large(
                                f"File too large (max {MAX_PDF_BYTES // (1024 * 1024)}MB)."
                            )
                        tmp.write(chunk)

        return await _parse_pdf_file(tmp_path, filename, raise_on_error)
    except httpx.RequestError as e:
        error_msg = f"Failed to download file: {str(e)}"
        if raise_on_error:
            raise HTTPException(status_code=400, detail=error_msg)
        return _error_result(filename, error_msg)
    finally:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def _parse_links_field(links: Optional[str]) -> List[str]:
    """Parse and validate the `links` form field into a list of URLs."""
    if not links:
        return []
    try:
        url_list = json.loads(links)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400, detail="'links' must be a valid JSON array of URLs."
        )
    if not isinstance(url_list, list):
        raise HTTPException(
            status_code=400, detail="'links' must be a valid JSON array of URLs."
        )
    return url_list


def _check_file_count(total_count: int) -> None:
    if total_count > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f"Too many files (max {MAX_FILES_PER_REQUEST} per request).",
        )


def _build_parse_zip_sync(results: list, errors: list, timestamp: str) -> io.BytesIO:
    """Assemble the combined Excel + summary PDF ZIP. Runs in a worker thread."""
    from services import generate_summary_pdf  # lazy: pulls in reportlab

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        all_dfs = []
        verification_results = []
        for result in results:
            corrected_df, vr = verify_and_correct(
                result["df"],
                filename=result["filename"],
                bank_name=result["bank_name"],
                bank_id=result.get("bank_id", ""),
                account_number=result.get("account_number"),
            )
            result["df"] = None
            verification_results.append(vr)
            corrected_df["Source"] = result["bank_name"]
            all_dfs.append(corrected_df)

            v_filename = Path(result["filename"]).stem + ".verification.json"
            zip_file.writestr(
                v_filename, json.dumps(vr.to_dict(), indent=2, default=str)
            )

        combined_df = _standardize_dataframe(
            _deduplicate_transactions(pd.concat(all_dfs, ignore_index=True))
        )
        del all_dfs

        combined_excel_buffer = io.BytesIO()
        combined_df.to_excel(combined_excel_buffer, index=False, engine="xlsxwriter")
        zip_file.writestr(
            f"combined_parsed_{timestamp}.xlsx", combined_excel_buffer.getvalue()
        )
        del combined_excel_buffer

        combined_summary = calculate_summary(combined_df)
        combined_coverage = calculate_coverage(combined_df)
        combined_activity = calculate_activity_volume(combined_df)
        combined_revenue = calculate_revenue(combined_df)
        combined_pdf_buffer = generate_summary_pdf(
            combined_df,
            combined_summary,
            combined_coverage,
            combined_activity,
            combined_revenue,
            verification_results=verification_results,
        )
        zip_file.writestr(
            f"combined_summary_{timestamp}.pdf", combined_pdf_buffer.getvalue()
        )
        del combined_pdf_buffer

        if errors:
            error_report = "Files that could not be parsed:\n\n"
            for e in errors:
                error_report += f"- {e['filename']}: {e['error']}\n"
            zip_file.writestr("parsing_errors.txt", error_report)

    zip_buffer.seek(0)
    return zip_buffer


@app.post("/parse")
async def parse_statement(
    files: Optional[List[UploadFile]] = File(None),
    links: Optional[str] = Form(None),
):
    """Parse one or more bank statement PDFs and return combined Excel + summary PDF in a ZIP file.

    Accepts either:
    - `files`: one or more PDF file uploads (multipart form)
    - `links`: JSON array of PDF URLs (form field), e.g. `["https://...","https://..."]`
    """
    if not files and not links:
        raise HTTPException(
            status_code=400, detail="Provide either 'files' or 'links'."
        )

    url_list = _parse_links_field(links)
    total_count = len(files or []) + len(url_list)
    _check_file_count(total_count)
    raise_on_error = total_count == 1

    async with _HEAVY_SEMAPHORE:
        try:
            all_results = []
            for f in (files or []):
                all_results.append(
                    await process_single_file(f, raise_on_error=raise_on_error)
                )
            for u in url_list:
                all_results.append(
                    await process_single_url(u, raise_on_error=raise_on_error)
                )

            results = [r for r in all_results if not r["error"]]
            errors = [r for r in all_results if r["error"]]

            if not results:
                error_details = "; ".join(
                    [f"{e['filename']}: {e['error']}" for e in errors]
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"No transactions could be extracted from any file. Errors: {error_details}",
                )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_buffer = await anyio.to_thread.run_sync(
                _build_parse_zip_sync, results, errors, timestamp
            )
            del all_results, results
        finally:
            gc.collect()

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=combined_statements_{timestamp}.zip"
        },
    )


@app.post("/parse/json")
async def parse_statement_json(
    files: Optional[List[UploadFile]] = File(None),
    links: Optional[str] = Form(None),
):
    """Parse one or more bank statement PDFs and return JSON response.

    Accepts either:
    - `files`: one or more PDF file uploads (multipart form)
    - `links`: JSON array of PDF URLs (form field), e.g. `["https://...","https://..."]`
    """
    if not files and not links:
        raise HTTPException(
            status_code=400, detail="Provide either 'files' or 'links'."
        )

    url_list = _parse_links_field(links)
    total_count = len(files or []) + len(url_list)
    _check_file_count(total_count)
    raise_on_error = total_count == 1

    async with _HEAVY_SEMAPHORE:
        try:
            all_results = []
            for f in (files or []):
                all_results.append(
                    await process_single_file(f, raise_on_error=raise_on_error)
                )
            for u in url_list:
                all_results.append(
                    await process_single_url(u, raise_on_error=raise_on_error)
                )

            response = await anyio.to_thread.run_sync(
                _build_parse_json_sync, all_results, total_count
            )
            del all_results
        finally:
            gc.collect()

    return JSONResponse(content=response)


def _build_parse_json_sync(all_results: list, total_count: int) -> dict:
    """Verify, combine, and serialize parse results. Runs in a worker thread."""
    all_dfs = []
    errors = []
    verification_results = []
    for result in all_results:
        if result["error"]:
            errors.append({"filename": result["filename"], "error": result["error"]})
            verification_results.append({
                "filename": result["filename"],
                "error": result["error"],
            })
        else:
            corrected_df, vr = verify_and_correct(
                result["df"],
                filename=result["filename"],
                bank_name=result["bank_name"],
                bank_id=result.get("bank_id", ""),
                account_number=result.get("account_number"),
            )
            result["df"] = None
            verification_results.append(vr.to_dict())
            corrected_df["Source"] = result["bank_name"]
            all_dfs.append(corrected_df)

    if not all_dfs:
        error_details = "; ".join([f"{e['filename']}: {e['error']}" for e in errors])
        raise HTTPException(
            status_code=400,
            detail=f"No transactions could be extracted from any file. Errors: {error_details}",
        )

    successful_files = len(all_dfs)
    combined_df = _standardize_dataframe(
        _deduplicate_transactions(pd.concat(all_dfs, ignore_index=True))
    )
    del all_dfs
    combined_summary = calculate_summary(combined_df)
    coverage = calculate_coverage(combined_df)
    activity = calculate_activity_volume(combined_df)
    revenue = calculate_revenue(combined_df)

    response = {
        "summary": combined_summary.to_dict(),
        "coverage": coverage.to_dict(),
        "activity_volume": activity.to_dict(),
        "revenue": revenue.to_dict(),
        "transactions": combined_df.to_dict(orient="records"),
        "verification": verification_results,
        "document_count": total_count,
        "successful_files": successful_files,
    }

    if errors:
        response["parsing_errors"] = errors

    return _sanitize_for_json(response)


def _build_report_from_transactions(transactions: list) -> tuple:
    """Build combined DataFrame and analysis from pre-parsed transaction records."""
    combined_df = pd.DataFrame(transactions)
    combined_df = _standardize_dataframe(_deduplicate_transactions(combined_df))
    summary = calculate_summary(combined_df)
    coverage = calculate_coverage(combined_df)
    activity = calculate_activity_volume(combined_df)
    revenue = calculate_revenue(combined_df)
    return combined_df, summary, coverage, activity, revenue


def _build_analysis_from_records(records: list):
    """Standardize, verify, and analyze transaction records. Runs in a worker thread.

    Returns (combined_df, summary, coverage, activity, revenue, verification_results).
    """
    combined_df, summary, coverage, activity, revenue = _build_report_from_transactions(
        records
    )
    corrected_df, verification_results = _verify_grouped_results(combined_df)
    if corrected_df is not None:
        combined_df = corrected_df
        summary = calculate_summary(combined_df)
        coverage = calculate_coverage(combined_df)
        activity = calculate_activity_volume(combined_df)
        revenue = calculate_revenue(combined_df)
    return combined_df, summary, coverage, activity, revenue, verification_results


def _build_report_zip_sync(records: list, timestamp: str) -> io.BytesIO:
    """Assemble the /report ZIP (Excel + summary PDF). Runs in a worker thread."""
    from services import generate_summary_pdf  # lazy: pulls in reportlab

    combined_df, summary, coverage, activity, revenue, verification_results = (
        _build_analysis_from_records(records)
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        excel_buffer = io.BytesIO()
        combined_df.to_excel(excel_buffer, index=False, engine="xlsxwriter")
        zip_file.writestr(f"combined_parsed_{timestamp}.xlsx", excel_buffer.getvalue())
        del excel_buffer

        pdf_buffer = generate_summary_pdf(
            combined_df, summary, coverage, activity, revenue,
            verification_results=verification_results,
        )
        zip_file.writestr(f"combined_summary_{timestamp}.pdf", pdf_buffer.getvalue())
        del pdf_buffer

    zip_buffer.seek(0)
    return zip_buffer


@app.post("/report")
async def generate_report(
    transactions: Optional[str] = Form(None),
    transactions_file: Optional[UploadFile] = File(None),
):
    """Generate ZIP (Excel + summary PDF) from pre-parsed transaction JSON.

    Accepts:
    - `transactions`: JSON array of transaction records (form field, <=1 MB)
    - `transactions_file`: Same data as a file upload (no size limit)
    At least one must be provided. File upload takes precedence.
    """
    raw = None
    if transactions_file:
        raw = await _read_json_upload(transactions_file)
    elif transactions:
        raw = transactions

    if not raw:
        raise HTTPException(
            status_code=400, detail="'transactions' or 'transactions_file' must be provided."
        )

    try:
        records = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400, detail="'transactions' must be a valid JSON array."
        )

    if not records:
        raise HTTPException(status_code=400, detail="No transactions provided.")

    del raw
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    async with _HEAVY_SEMAPHORE:
        try:
            zip_buffer = await anyio.to_thread.run_sync(
                _build_report_zip_sync, records, timestamp
            )
        finally:
            gc.collect()

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=combined_statements_{timestamp}.zip"
        },
    )


def _build_full_prevet_sync(
    form_data: dict, records: Optional[list], parsed_results: Optional[list]
) -> io.BytesIO:
    """Build the merged PreVet + bank analysis PDF. Runs in a worker thread."""
    from services import generate_prevet_pdf, generate_summary_pdf  # lazy: reportlab

    # ── 1. Generate PreVet PDF locally ────────────────────────────────────
    prevet_buffer = generate_prevet_pdf(form_data)

    # ── 2. Generate Bank Analysis PDF ─────────────────────────────────────
    bank_pdf_buffer: Optional[io.BytesIO] = None

    if records:
        # Fast path: use pre-parsed transactions
        combined_df, summary, coverage, activity, revenue, verification_results = (
            _build_analysis_from_records(records)
        )
        bank_pdf_buffer = generate_summary_pdf(
            combined_df, summary, coverage, activity, revenue,
            verification_results=verification_results,
        )
        del combined_df

    elif parsed_results:
        results = [r for r in parsed_results if not r["error"]]
        if results:
            all_dfs = []
            verification_results = []
            for result in results:
                corrected_df, vr = verify_and_correct(
                    result["df"],
                    filename=result["filename"],
                    bank_name=result["bank_name"],
                    bank_id=result.get("bank_id", ""),
                    account_number=result.get("account_number"),
                )
                result["df"] = None
                verification_results.append(vr)
                corrected_df["Source"] = result["bank_name"]
                all_dfs.append(corrected_df)

            combined_df = _standardize_dataframe(
                _deduplicate_transactions(
                    pd.concat(all_dfs, ignore_index=True)
                )
            )
            del all_dfs
            combined_summary = calculate_summary(combined_df)
            combined_coverage = calculate_coverage(combined_df)
            combined_activity = calculate_activity_volume(combined_df)
            combined_revenue = calculate_revenue(combined_df)
            bank_pdf_buffer = generate_summary_pdf(
                combined_df,
                combined_summary,
                combined_coverage,
                combined_activity,
                combined_revenue,
                verification_results=verification_results,
            )
            del combined_df

    # ── 3. Merge PDFs ─────────────────────────────────────────────────────
    writer = PdfWriter()

    prevet_buffer.seek(0)
    prevet_reader = PdfReader(prevet_buffer)
    for page in prevet_reader.pages:
        writer.add_page(page)

    if bank_pdf_buffer is not None:
        bank_pdf_buffer.seek(0)
        bank_reader = PdfReader(bank_pdf_buffer)
        for page in bank_reader.pages:
            writer.add_page(page)

    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    output_buffer.seek(0)
    return output_buffer


@app.post("/generate-full-prevet")
async def generate_full_prevet(
    prevet_data: Optional[str] = Form(None),
    prevet_data_file: Optional[UploadFile] = File(None),
    links: Optional[str] = Form(None),
    transactions: Optional[str] = Form(None),
    transactions_file: Optional[UploadFile] = File(None),
):
    """Generate a combined PDF with the PreVet form on top and bank analysis below.

    Accepts:
    - `prevet_data` / `prevet_data_file`: JSON string of prevet form fields
    - `links`: JSON array of bank statement PDF URLs (optional, slow – downloads & parses)
    - `transactions` / `transactions_file`: JSON array of pre-parsed transaction records (optional, fast)
    File upload variants bypass the 1 MB multipart field limit.
    If both `transactions` and `links` are provided, `transactions` takes precedence.
    """
    # Resolve prevet_data from file or form field
    raw_prevet = None
    if prevet_data_file:
        raw_prevet = await _read_json_upload(prevet_data_file)
    elif prevet_data:
        raw_prevet = prevet_data

    if not raw_prevet:
        raise HTTPException(
            status_code=400, detail="'prevet_data' or 'prevet_data_file' must be provided."
        )

    try:
        form_data = json.loads(raw_prevet)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="'prevet_data' must be valid JSON.")

    # Resolve transactions from file or form field
    raw_transactions = None
    if transactions_file:
        raw_transactions = await _read_json_upload(transactions_file)
    elif transactions:
        raw_transactions = transactions

    records = None
    if raw_transactions:
        try:
            records = json.loads(raw_transactions)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, detail="'transactions' must be valid JSON."
            )
        del raw_transactions

    async with _HEAVY_SEMAPHORE:
        try:
            parsed_results = None
            if not records and links:
                # Slow path: download & parse PDFs (kept for backwards compatibility)
                url_list = _parse_links_field(links)
                _check_file_count(len(url_list))
                parsed_results = []
                for u in url_list:
                    parsed_results.append(
                        await process_single_url(u, raise_on_error=False)
                    )

            output_buffer = await anyio.to_thread.run_sync(
                _build_full_prevet_sync, form_data, records, parsed_results
            )
            del records, parsed_results
        finally:
            gc.collect()

    return StreamingResponse(
        output_buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="full-prevet.pdf"'
        },
    )
