"""Bank Statement Parser API.

A FastAPI application for parsing South African bank statements.
Supports multiple banks with auto-detection.
"""

import io
import zipfile
from datetime import datetime
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from parsers import SUPPORTED_BANKS, get_parser
from services import calculate_summary, generate_summary_pdf

app = FastAPI(
    title="Bank Statement Parser API",
    description="Parse bank statements from major South African banks",
    version="2.0.0",
)


@app.get("/")
def read_root():
    """Get API information and supported banks."""
    return {
        "message": "Bank Statement Parser API",
        "version": "2.0.0",
        "supported_banks": SUPPORTED_BANKS,
        "endpoints": {
            "/parse": "POST - Upload PDF to parse (returns ZIP with Excel + PDF)",
            "/parse/json": "POST - Upload PDF to parse (returns JSON)",
        },
    }


@app.get("/banks")
def list_banks():
    """List all supported banks."""
    return {"supported_banks": SUPPORTED_BANKS}


@app.post("/parse")
async def parse_statement(file: UploadFile = File(...)):
    """Parse a bank statement PDF and return Excel + summary PDF in a ZIP file.

    Args:
        file: PDF file upload

    Returns:
        ZIP file containing parsed Excel and summary PDF
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    contents = await file.read()
    pdf_buffer = io.BytesIO(contents)

    # Get appropriate parser
    parser = get_parser(pdf_buffer)
    if not parser:
        raise HTTPException(
            status_code=400,
            detail=f"Could not detect bank type. Supported banks: {', '.join(SUPPORTED_BANKS)}",
        )

    try:
        account_info, df = parser.parse()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse PDF: {str(e)}")

    if df.empty:
        raise HTTPException(
            status_code=400,
            detail="No transactions detected in the PDF",
        )

    # Calculate summary
    summary = calculate_summary(df)

    # Generate Excel file
    excel_buffer = io.BytesIO()
    df.to_excel(excel_buffer, index=False, engine="openpyxl")
    excel_buffer.seek(0)
    excel_bytes = excel_buffer.getvalue()

    # Generate summary PDF
    summary_pdf_buffer = generate_summary_pdf(df, summary, account_info.to_dict())
    summary_pdf_bytes = summary_pdf_buffer.getvalue()

    # Create ZIP file
    zip_buffer = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bank_name = account_info.bank.lower().replace(" ", "_")

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr(f"{bank_name}_parsed_{timestamp}.xlsx", excel_bytes)
        zip_file.writestr(f"{bank_name}_summary_{timestamp}.pdf", summary_pdf_bytes)

    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={bank_name}_statement_{timestamp}.zip"
        },
    )


@app.post("/parse/json")
async def parse_statement_json(file: UploadFile = File(...)):
    """Parse a bank statement PDF and return JSON response.

    Args:
        file: PDF file upload

    Returns:
        JSON with account info, summary, and transactions
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    contents = await file.read()
    pdf_buffer = io.BytesIO(contents)

    # Get appropriate parser
    parser = get_parser(pdf_buffer)
    if not parser:
        raise HTTPException(
            status_code=400,
            detail=f"Could not detect bank type. Supported banks: {', '.join(SUPPORTED_BANKS)}",
        )

    try:
        account_info, df = parser.parse()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse PDF: {str(e)}")

    if df.empty:
        raise HTTPException(
            status_code=400,
            detail="No transactions detected in the PDF",
        )

    summary = calculate_summary(df)
    transactions = df.to_dict(orient="records")

    return {
        "account_info": account_info.to_dict(),
        "summary": summary.to_dict(),
        "transactions": transactions,
    }
