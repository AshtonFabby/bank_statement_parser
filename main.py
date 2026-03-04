"""Bank Statement Parser API.

A FastAPI application for parsing South African bank statements.
Supports multiple banks with auto-detection.
"""

import asyncio
import io
import json
import zipfile
from datetime import datetime
from typing import List, Optional
from urllib.parse import unquote

import certifi
import httpx
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pypdf import PdfReader, PdfWriter

from parsers import SUPPORTED_BANKS, detect_bank, get_parser, get_parser_by_id
from services import (
    calculate_activity_volume,
    calculate_coverage,
    calculate_revenue,
    calculate_summary,
    generate_prevet_pdf,
    generate_summary_pdf,
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


async def _parse_pdf_bytes(
    contents: bytes, filename: str, raise_on_error: bool = True
) -> dict:
    """Parse raw PDF bytes and return structured result."""
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
        for result in results:
            df_with_source = result["df"].copy()
            df_with_source["Source"] = result["bank_name"]
            all_dfs.append(df_with_source)

        combined_df = _deduplicate_transactions(pd.concat(all_dfs, ignore_index=True))

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
    for result in all_results:
        if result["error"]:
            errors.append({"filename": result["filename"], "error": result["error"]})
        else:
            df_with_source = result["df"].copy()
            df_with_source["Source"] = result["bank_name"]
            all_dfs.append(df_with_source)

    if not all_dfs:
        error_details = "; ".join([f"{e['filename']}: {e['error']}" for e in errors])
        raise HTTPException(
            status_code=400,
            detail=f"No transactions could be extracted from any file. Errors: {error_details}",
        )

    combined_df = _deduplicate_transactions(pd.concat(all_dfs, ignore_index=True))
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
        "document_count": total_count,
        "successful_files": len(all_dfs),
    }

    if errors:
        response["parsing_errors"] = errors

    return response


def _build_report_from_transactions(transactions: list) -> tuple:
    """Build combined DataFrame and analysis from pre-parsed transaction records."""
    combined_df = pd.DataFrame(transactions)
    # Ensure numeric columns
    for col in ("Debit", "Credit", "Balance"):
        if col in combined_df.columns:
            combined_df[col] = pd.to_numeric(combined_df[col], errors="coerce")
    if "Date" in combined_df.columns:
        combined_df["Date"] = pd.to_datetime(combined_df["Date"], errors="coerce")
    combined_df = _deduplicate_transactions(combined_df)
    summary = calculate_summary(combined_df)
    coverage = calculate_coverage(combined_df)
    activity = calculate_activity_volume(combined_df)
    revenue = calculate_revenue(combined_df)
    return combined_df, summary, coverage, activity, revenue


@app.post("/report")
async def generate_report(
    transactions: str = Form(...),
):
    """Generate ZIP (Excel + summary PDF) from pre-parsed transaction JSON.

    Accepts:
    - `transactions`: JSON array of transaction records (from /parse/json responses)
    """
    try:
        records = json.loads(transactions)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400, detail="'transactions' must be a valid JSON array."
        )

    if not records:
        raise HTTPException(status_code=400, detail="No transactions provided.")

    combined_df, summary, coverage, activity, revenue = _build_report_from_transactions(
        records
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        excel_buffer = io.BytesIO()
        combined_df.to_excel(excel_buffer, index=False, engine="openpyxl")
        zip_file.writestr(f"combined_parsed_{timestamp}.xlsx", excel_buffer.getvalue())

        pdf_buffer = generate_summary_pdf(
            combined_df, summary, coverage, activity, revenue
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
    prevet_data: str = Form(...),
    links: Optional[str] = Form(None),
    transactions: Optional[str] = Form(None),
):
    """Generate a combined PDF with the PreVet form on top and bank analysis below.

    Accepts:
    - `prevet_data`: JSON string of prevet form fields
    - `links`: JSON array of bank statement PDF URLs (optional, slow – downloads & parses)
    - `transactions`: JSON array of pre-parsed transaction records (optional, fast)
    If both `transactions` and `links` are provided, `transactions` takes precedence.
    """
    try:
        form_data = json.loads(prevet_data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="'prevet_data' must be valid JSON.")

    # ── 1. Generate PreVet PDF locally ────────────────────────────────────
    prevet_buffer = generate_prevet_pdf(form_data)
    prevet_pdf_bytes = prevet_buffer.getvalue()

    # ── 2. Generate Bank Analysis PDF ─────────────────────────────────────
    bank_analysis_pdf_bytes: Optional[bytes] = None

    if transactions:
        # Fast path: use pre-parsed transactions
        try:
            records = json.loads(transactions)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, detail="'transactions' must be valid JSON."
            )
        if records:
            combined_df, summary, coverage, activity, revenue = (
                _build_report_from_transactions(records)
            )
            bank_pdf_buffer = generate_summary_pdf(
                combined_df, summary, coverage, activity, revenue
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
                for result in results:
                    df_with_source = result["df"].copy()
                    df_with_source["Source"] = result["bank_name"]
                    all_dfs.append(df_with_source)

                combined_df = _deduplicate_transactions(
                    pd.concat(all_dfs, ignore_index=True)
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
