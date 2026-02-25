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

import httpx
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from parsers import SUPPORTED_BANKS, get_parser, get_parser_by_id
from services import (
    calculate_activity_volume,
    calculate_coverage,
    calculate_revenue,
    calculate_summary,
    generate_summary_pdf,
)

app = FastAPI(
    title="Bank Statement Parser API",
    description="Parse bank statements from major South African banks. Supports single or multiple file uploads or URLs.",
    version="2.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://bank-statement-parser-frontend.vercel.app",
        "https://superadmin.todayscapital.co.za/",
    ],
    allow_credentials=True,
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
    parser = get_parser(detection_buffer)
    if not parser:
        error_msg = f"Could not detect bank type for '{filename}'. Supported banks: {', '.join(SUPPORTED_BANKS)}"
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
        error_msg = f"Failed to parse '{filename}': {str(e)}"
        if raise_on_error:
            raise HTTPException(status_code=500, detail=error_msg)
        result["error"] = error_msg
        result["bank_name"] = parser.BANK_NAME
        return result

    if df.empty:
        error_msg = f"No transactions detected in '{filename}'"
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
        error_msg = f"Only PDF files are accepted. '{file.filename}' is not a PDF."
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


async def process_single_url(url: str, raise_on_error: bool = True) -> dict:
    """Download a PDF from a URL and return parsed data."""
    filename = unquote(url.split("/")[-1].split("?")[0])
    if not filename.lower().endswith(".pdf"):
        filename = filename + ".pdf"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
    except httpx.RequestError as e:
        error_msg = f"Failed to download '{url}': {str(e)}"
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
        error_msg = f"Failed to download '{url}': HTTP {response.status_code}"
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

        combined_df = pd.concat(all_dfs, ignore_index=True)

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

    combined_df = pd.concat(all_dfs, ignore_index=True)
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
