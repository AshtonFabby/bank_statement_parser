"""Bank Statement Parser API.

A FastAPI application for parsing South African bank statements.
Supports multiple banks with auto-detection.
"""

import asyncio
import io
import json
import math
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote

import certifi
import httpx
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pypdf import PdfReader, PdfWriter

from parsers import SUPPORTED_BANKS, detect_bank, get_parser, get_parser_by_id
from services import (
    calculate_activity_volume,
    calculate_coverage,
    calculate_revenue,
    calculate_summary,
    generate_prevet_pdf,
    generate_summary_pdf,
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


def _decrypt_pdf_if_needed(contents: bytes, filename: str) -> tuple[bytes, str]:
    """If the filename contains _pass=<password>, decrypt the PDF and strip the
    password segment from the filename. Returns (pdf_bytes, clean_filename)."""
    match = _PASS_PATTERN.search(filename)
    if not match:
        return contents, filename

    password = match.group(1)
    clean_filename = filename[: match.start()] + filename[match.end() :]

    reader = PdfReader(io.BytesIO(contents))
    if reader.is_encrypted:
        reader.decrypt(password)

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), clean_filename


async def _parse_pdf_bytes(
    contents: bytes, filename: str, raise_on_error: bool = True
) -> dict:
    """Parse raw PDF bytes and return structured result."""
    contents, filename = _decrypt_pdf_if_needed(contents, filename)
    result = {
        "filename": filename,
        "bank_name": None,
        "summary": None,
        "df": None,
        "error": None,
    }

    detection_buffer = io.BytesIO(contents)
    bank_id = detect_bank(detection_buffer)
    if bank_id == "INVALID_PDF":
        error_msg = "Could not be opened. The file may be corrupted or is not a valid PDF."
        if raise_on_error:
            raise HTTPException(status_code=400, detail=error_msg)
        result["error"] = error_msg
        return result
    parser = get_parser(detection_buffer)
    if not parser:
        error_msg = "Bank not recognised."
        if raise_on_error:
            raise HTTPException(status_code=400, detail=error_msg)
        result["error"] = error_msg
        return result

    parsing_buffer = io.BytesIO(contents)
    bank_id = parser.BANK_ID
    parser = get_parser_by_id(bank_id, parsing_buffer)

    try:
        account_info, df = parser.parse()
    except Exception as e:
        error_msg = f"Failed to parse statement: {str(e)}"
        if raise_on_error:
            raise HTTPException(status_code=500, detail=error_msg)
        result["error"] = error_msg
        result["bank_name"] = parser.BANK_NAME
        return result

    if df.empty:
        error_msg = "No transactions detected."
        if raise_on_error:
            raise HTTPException(status_code=400, detail=error_msg)
        result["error"] = error_msg
        result["bank_name"] = account_info.bank
        return result

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


async def process_single_file(file: UploadFile, raise_on_error: bool = True) -> dict:
    """Process a single PDF upload and return parsed data."""
    if not file.filename.lower().endswith(".pdf"):
        error_msg = "Only PDF files are accepted."
        if raise_on_error:
            raise HTTPException(status_code=400, detail=error_msg)
        return {
            "filename": file.filename,
            "bank_name": None,
            "summary": None,
            "df": None,
            "error": error_msg,
        }

    contents = await file.read()
    return await _parse_pdf_bytes(contents, file.filename, raise_on_error)


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

    - Coerces Debit/Credit/Balance to numeric (invalid → 0.0)
    - Parses Date with dayfirst=True, drops rows with unparseable dates
    - Re-formats Date back to DD/MM/YYYY strings for consistent downstream use
    """
    df = df.copy()
    for col in ("Debit", "Credit", "Balance"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if "Date" in df.columns:
        parsed = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
        df = df[parsed.notna()].copy()
        parsed = parsed[parsed.notna()]
        df["_parsed_date"] = parsed
        df["Date"] = parsed.dt.strftime("%d/%m/%Y")
        df = df.sort_values("_parsed_date").reset_index(drop=True)
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
    """Download a PDF from a URL and return parsed data."""
    filename = unquote(url.split("/")[-1].split("?")[0])
    if not filename.lower().endswith(".pdf"):
        filename = filename + ".pdf"

    try:
        async with httpx.AsyncClient(timeout=120.0, verify=certifi.where()) as client:
            response = await client.get(url)
    except httpx.RequestError as e:
        error_msg = f"Failed to download file: {str(e)}"
        if raise_on_error:
            raise HTTPException(status_code=400, detail=error_msg)
        return {
            "filename": filename,
            "bank_name": None,
            "summary": None,
            "df": None,
            "error": error_msg,
        }

    if response.status_code != 200:
        error_msg = f"Failed to download file: HTTP {response.status_code}"
        if raise_on_error:
            raise HTTPException(status_code=400, detail=error_msg)
        return {
            "filename": filename,
            "bank_name": None,
            "summary": None,
            "df": None,
            "error": error_msg,
        }

    return await _parse_pdf_bytes(response.content, filename, raise_on_error)


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

    url_list: List[str] = []
    if links:
        try:
            url_list = json.loads(links)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, detail="'links' must be a valid JSON array of URLs."
            )

    total_count = len(files or []) + len(url_list)
    raise_on_error = total_count == 1

    tasks = [
        *[process_single_file(f, raise_on_error=raise_on_error) for f in (files or [])],
        *[process_single_url(u, raise_on_error=raise_on_error) for u in url_list],
    ]
    all_results = await asyncio.gather(*tasks)

    results = [r for r in all_results if not r["error"]]
    errors = [r for r in all_results if r["error"]]

    if not results:
        error_details = "; ".join([f"{e['filename']}: {e['error']}" for e in errors])
        raise HTTPException(
            status_code=400,
            detail=f"No transactions could be extracted from any file. Errors: {error_details}",
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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
            verification_results.append(vr)
            result["df"] = corrected_df
            df_with_source = corrected_df.copy()
            df_with_source["Source"] = result["bank_name"]
            all_dfs.append(df_with_source)

            v_filename = Path(result["filename"]).stem + ".verification.json"
            zip_file.writestr(
                v_filename, json.dumps(vr.to_dict(), indent=2, default=str)
            )

        combined_df = _standardize_dataframe(
            _deduplicate_transactions(pd.concat(all_dfs, ignore_index=True))
        )

        combined_excel_buffer = io.BytesIO()
        combined_df.to_excel(combined_excel_buffer, index=False, engine="openpyxl")
        zip_file.writestr(
            f"combined_parsed_{timestamp}.xlsx", combined_excel_buffer.getvalue()
        )

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

        if errors:
            error_report = "Files that could not be parsed:\n\n"
            for e in errors:
                error_report += f"- {e['filename']}: {e['error']}\n"
            zip_file.writestr("parsing_errors.txt", error_report)

    zip_buffer.seek(0)

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

    url_list: List[str] = []
    if links:
        try:
            url_list = json.loads(links)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, detail="'links' must be a valid JSON array of URLs."
            )

    total_count = len(files or []) + len(url_list)
    raise_on_error = total_count == 1

    tasks = [
        *[process_single_file(f, raise_on_error=raise_on_error) for f in (files or [])],
        *[process_single_url(u, raise_on_error=raise_on_error) for u in url_list],
    ]
    all_results = await asyncio.gather(*tasks)

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
            result["df"] = corrected_df
            verification_results.append(vr.to_dict())
            df_with_source = corrected_df.copy()
            df_with_source["Source"] = result["bank_name"]
            all_dfs.append(df_with_source)

    if not all_dfs:
        error_details = "; ".join([f"{e['filename']}: {e['error']}" for e in errors])
        raise HTTPException(
            status_code=400,
            detail=f"No transactions could be extracted from any file. Errors: {error_details}",
        )

    combined_df = _standardize_dataframe(
        _deduplicate_transactions(pd.concat(all_dfs, ignore_index=True))
    )
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
        "successful_files": len(all_dfs),
    }

    if errors:
        response["parsing_errors"] = errors

    return JSONResponse(content=_sanitize_for_json(response))


def _build_report_from_transactions(transactions: list) -> tuple:
    """Build combined DataFrame and analysis from pre-parsed transaction records."""
    combined_df = pd.DataFrame(transactions)
    combined_df = _standardize_dataframe(_deduplicate_transactions(combined_df))
    summary = calculate_summary(combined_df)
    coverage = calculate_coverage(combined_df)
    activity = calculate_activity_volume(combined_df)
    revenue = calculate_revenue(combined_df)
    return combined_df, summary, coverage, activity, revenue


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
        raw = (await transactions_file.read()).decode()
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

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        excel_buffer = io.BytesIO()
        combined_df.to_excel(excel_buffer, index=False, engine="openpyxl")
        zip_file.writestr(f"combined_parsed_{timestamp}.xlsx", excel_buffer.getvalue())

        pdf_buffer = generate_summary_pdf(
            combined_df, summary, coverage, activity, revenue,
            verification_results=verification_results,
        )
        zip_file.writestr(f"combined_summary_{timestamp}.pdf", pdf_buffer.getvalue())

    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=combined_statements_{timestamp}.zip"
        },
    )


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
        raw_prevet = (await prevet_data_file.read()).decode()
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
        raw_transactions = (await transactions_file.read()).decode()
    elif transactions:
        raw_transactions = transactions

    # ── 1. Generate PreVet PDF locally ────────────────────────────────────
    prevet_buffer = generate_prevet_pdf(form_data)
    prevet_pdf_bytes = prevet_buffer.getvalue()

    # ── 2. Generate Bank Analysis PDF ─────────────────────────────────────
    bank_analysis_pdf_bytes: Optional[bytes] = None

    if raw_transactions:
        # Fast path: use pre-parsed transactions
        try:
            records = json.loads(raw_transactions)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, detail="'transactions' must be valid JSON."
            )
        if records:
            combined_df, summary, coverage, activity, revenue = (
                _build_report_from_transactions(records)
            )
            corrected_df, verification_results = _verify_grouped_results(combined_df)
            if corrected_df is not None:
                combined_df = corrected_df
                summary = calculate_summary(combined_df)
                coverage = calculate_coverage(combined_df)
                activity = calculate_activity_volume(combined_df)
                revenue = calculate_revenue(combined_df)
            bank_pdf_buffer = generate_summary_pdf(
                combined_df, summary, coverage, activity, revenue,
                verification_results=verification_results,
            )
            bank_analysis_pdf_bytes = bank_pdf_buffer.getvalue()

    elif links:
        # Slow path: download & parse PDFs (kept for backwards compatibility)
        try:
            url_list = json.loads(links)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, detail="'links' must be a valid JSON array of URLs."
            )

        if url_list:
            tasks = [process_single_url(u, raise_on_error=False) for u in url_list]
            all_results = await asyncio.gather(*tasks)
            results = [r for r in all_results if not r["error"]]

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
                    verification_results.append(vr)
                    result["df"] = corrected_df
                    df_with_source = corrected_df.copy()
                    df_with_source["Source"] = result["bank_name"]
                    all_dfs.append(df_with_source)

                combined_df = _standardize_dataframe(
                    _deduplicate_transactions(
                        pd.concat(all_dfs, ignore_index=True)
                    )
                )
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
                bank_analysis_pdf_bytes = bank_pdf_buffer.getvalue()

    # ── 3. Merge PDFs ─────────────────────────────────────────────────────
    writer = PdfWriter()

    prevet_reader = PdfReader(io.BytesIO(prevet_pdf_bytes))
    for page in prevet_reader.pages:
        writer.add_page(page)

    if bank_analysis_pdf_bytes:
        bank_reader = PdfReader(io.BytesIO(bank_analysis_pdf_bytes))
        for page in bank_reader.pages:
            writer.add_page(page)

    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    output_buffer.seek(0)

    return StreamingResponse(
        output_buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="full-prevet.pdf"'
        },
    )
