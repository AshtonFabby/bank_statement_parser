"""Bank Statement Parser API.

A FastAPI application for parsing South African bank statements.
Supports multiple banks with auto-detection.
"""

import io
import zipfile
from datetime import datetime
from typing import List

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from parsers import SUPPORTED_BANKS, get_parser
from services import (
    calculate_summary,
    calculate_coverage,
    calculate_activity_volume,
    calculate_revenue,
    generate_summary_pdf,
)

app = FastAPI(
    title="Bank Statement Parser API",
    description="Parse bank statements from major South African banks. Supports single or multiple file uploads.",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    """Get API information and supported banks."""
    return {
        "message": "Bank Statement Parser API",
        "version": "2.1.0",
        "supported_banks": SUPPORTED_BANKS,
        "endpoints": {
            "/parse": "POST - Upload one or more PDFs to parse (returns ZIP with combined Excel + PDF)",
            "/parse/json": "POST - Upload one or more PDFs to parse (returns JSON with combined results)",
        },
    }


@app.get("/banks")
def list_banks():
    """List all supported banks."""
    return {"supported_banks": SUPPORTED_BANKS}


async def process_single_file(file: UploadFile) -> dict:
    """Process a single PDF file and return parsed data.

    Args:
        file: PDF file upload

    Returns:
        Dict with bank_name, summary, df, and filename

    Raises:
        HTTPException: If file is invalid or parsing fails
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail=f"Only PDF files are accepted. '{file.filename}' is not a PDF.",
        )

    contents = await file.read()
    pdf_buffer = io.BytesIO(contents)

    parser = get_parser(pdf_buffer)
    if not parser:
        raise HTTPException(
            status_code=400,
            detail=f"Could not detect bank type for '{file.filename}'. Supported banks: {', '.join(SUPPORTED_BANKS)}",
        )

    try:
        account_info, df = parser.parse()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse '{file.filename}': {str(e)}",
        )

    if df.empty:
        raise HTTPException(
            status_code=400,
            detail=f"No transactions detected in '{file.filename}'",
        )

    summary = calculate_summary(df)

    return {
        "bank_name": account_info.bank,
        "summary": summary,
        "df": df,
        "filename": file.filename,
    }


@app.post("/parse")
async def parse_statement(files: List[UploadFile] = File(...)):
    """Parse one or more bank statement PDFs and return combined Excel + summary PDF in a ZIP file.

    Args:
        files: One or more PDF file uploads

    Returns:
        ZIP file containing combined Excel and summary PDF from all uploaded documents
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_buffer = io.BytesIO()

    # Process all files
    results = []
    for file in files:
        result = await process_single_file(file)
        results.append(result)

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        all_dfs = []

        # Collect all dataframes with source information
        for result in results:
            df_with_source = result["df"].copy()
            df_with_source["Source"] = result["bank_name"]
            all_dfs.append(df_with_source)

        # Combine all dataframes
        combined_df = pd.concat(all_dfs, ignore_index=True)

        # Generate combined Excel
        combined_excel_buffer = io.BytesIO()
        combined_df.to_excel(combined_excel_buffer, index=False, engine="openpyxl")
        zip_file.writestr(f"combined_parsed_{timestamp}.xlsx", combined_excel_buffer.getvalue())

        # Generate combined summary PDF
        combined_summary = calculate_summary(combined_df)
        combined_coverage = calculate_coverage(combined_df)
        combined_activity = calculate_activity_volume(combined_df)
        combined_revenue = calculate_revenue(combined_df)
        combined_pdf_buffer = generate_summary_pdf(
            combined_df, combined_summary, combined_coverage, combined_activity, combined_revenue
        )
        zip_file.writestr(f"combined_summary_{timestamp}.pdf", combined_pdf_buffer.getvalue())

    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=combined_statements_{timestamp}.zip"
        },
    )


@app.post("/parse/json")
async def parse_statement_json(files: List[UploadFile] = File(...)):
    """Parse one or more bank statement PDFs and return JSON response.

    Args:
        files: One or more PDF file uploads

    Returns:
        JSON with combined summary and transactions from all files.
    """
    # Process all files
    all_dfs = []

    for file in files:
        result = await process_single_file(file)
        df_with_source = result["df"].copy()
        df_with_source["Source"] = result["bank_name"]
        all_dfs.append(df_with_source)

    # Combine all dataframes
    combined_df = pd.concat(all_dfs, ignore_index=True)
    combined_summary = calculate_summary(combined_df)
    coverage = calculate_coverage(combined_df)
    activity = calculate_activity_volume(combined_df)
    revenue = calculate_revenue(combined_df)

    return {
        "summary": combined_summary.to_dict(),
        "coverage": coverage.to_dict(),
        "activity_volume": activity.to_dict(),
        "revenue": revenue.to_dict(),
        "transactions": combined_df.to_dict(orient="records"),
        "document_count": len(files),
    }
