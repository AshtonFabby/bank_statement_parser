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
from fastapi.responses import StreamingResponse

from parsers import SUPPORTED_BANKS, get_parser
from services import calculate_summary, generate_summary_pdf

app = FastAPI(
    title="Bank Statement Parser API",
    description="Parse bank statements from major South African banks. Supports single or multiple file uploads.",
    version="2.1.0",
)


@app.get("/")
def read_root():
    """Get API information and supported banks."""
    return {
        "message": "Bank Statement Parser API",
        "version": "2.1.0",
        "supported_banks": SUPPORTED_BANKS,
        "endpoints": {
            "/parse": "POST - Upload one or more PDFs to parse (returns ZIP with Excel + PDF for each, plus combined files)",
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
    """Parse one or more bank statement PDFs and return Excel + summary PDF in a ZIP file.

    Args:
        files: One or more PDF file uploads

    Returns:
        ZIP file containing parsed Excel and summary PDF for each document,
        plus combined files when multiple documents are uploaded
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

        for i, result in enumerate(results):
            bank_name = result["bank_name"]
            summary = result["summary"]
            df = result["df"]
            bank_name_slug = bank_name.lower().replace(" ", "_")

            # Add source column for combined view
            df_with_source = df.copy()
            df_with_source["Source"] = bank_name

            all_dfs.append(df_with_source)

            # Generate Excel file for this document
            excel_buffer = io.BytesIO()
            df.to_excel(excel_buffer, index=False, engine="openpyxl")
            excel_bytes = excel_buffer.getvalue()

            # Generate summary PDF for this document
            summary_pdf_buffer = generate_summary_pdf(df, summary)
            summary_pdf_bytes = summary_pdf_buffer.getvalue()

            # Use index prefix for multiple files to avoid name collisions
            prefix = f"{i + 1}_" if len(results) > 1 else ""
            zip_file.writestr(f"{prefix}{bank_name_slug}_parsed_{timestamp}.xlsx", excel_bytes)
            zip_file.writestr(f"{prefix}{bank_name_slug}_summary_{timestamp}.pdf", summary_pdf_bytes)

        # If multiple files, create combined outputs
        if len(results) > 1:
            combined_df = pd.concat(all_dfs, ignore_index=True)

            # Combined Excel
            combined_excel_buffer = io.BytesIO()
            combined_df.to_excel(combined_excel_buffer, index=False, engine="openpyxl")
            zip_file.writestr(f"combined_parsed_{timestamp}.xlsx", combined_excel_buffer.getvalue())

            # Combined summary
            combined_summary = calculate_summary(combined_df)
            combined_pdf_buffer = generate_summary_pdf(combined_df, combined_summary)
            zip_file.writestr(f"combined_summary_{timestamp}.pdf", combined_pdf_buffer.getvalue())

    zip_buffer.seek(0)

    zip_name = "combined_statements" if len(results) > 1 else results[0]["bank_name"].lower().replace(" ", "_") + "_statement"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={zip_name}_{timestamp}.zip"
        },
    )


@app.post("/parse/json")
async def parse_statement_json(files: List[UploadFile] = File(...)):
    """Parse one or more bank statement PDFs and return JSON response.

    Args:
        files: One or more PDF file uploads

    Returns:
        JSON with summary and transactions.
        For single file: returns object directly.
        For multiple files: returns object with documents array and combined summary.
    """
    # Process all files
    results = []
    all_dfs = []

    for file in files:
        result = await process_single_file(file)
        df = result["df"]
        df_with_source = df.copy()
        df_with_source["Source"] = result["bank_name"]
        all_dfs.append(df_with_source)

        results.append({
            "summary": result["summary"].to_dict(),
            "transactions": df.to_dict(orient="records"),
        })

    # Single file - return simple response for backward compatibility
    if len(results) == 1:
        return results[0]

    # Multiple files - return combined response
    combined_df = pd.concat(all_dfs, ignore_index=True)
    combined_summary = calculate_summary(combined_df)

    return {
        "documents": results,
        "combined": {
            "summary": combined_summary.to_dict(),
            "transactions": combined_df.to_dict(orient="records"),
            "document_count": len(results),
        },
    }
