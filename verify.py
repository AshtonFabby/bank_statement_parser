#!/usr/bin/env python3
"""Verify the accuracy of parsed bank statement transactions.

For each PDF in bank_statement/, this script:
1. Auto-detects the bank and parses transactions
2. Verifies every transaction's balance progression using corrected baselines:
   corrected_balance = previous_corrected + credit - debit
3. Computes accuracy percentage (% of verified transactions that pass)
4. Saves per-file JSON results (failures only) to parsed/
5. Saves corrected CSV files to parsed/
"""

import io
import json
import re
import sys
from pathlib import Path
from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).parent))

from parsers import detect_bank, get_parser_by_id
from services.verification import verify_and_correct

BANK_STATEMENT_DIR = Path(__file__).parent / "bank_statements"
PARSED_DIR = Path(__file__).parent / "parsed"

_PASS_PATTERN = re.compile(r"_pass=([^_.]+)", re.IGNORECASE)


def _unwrap_java_serialized_pdf(contents: bytes) -> bytes:
    if contents[:2] == b"\xac\xed":
        pdf_start = contents.find(b"%PDF")
        if pdf_start > 0:
            return contents[pdf_start:]
    return contents


def _decrypt_pdf_if_needed(contents: bytes, filename: str) -> tuple[bytes, str]:
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


def process_file(pdf_path: Path) -> dict:
    contents = pdf_path.read_bytes()
    contents = _unwrap_java_serialized_pdf(contents)
    contents, clean_name = _decrypt_pdf_if_needed(contents, pdf_path.name)
    detection_buffer = io.BytesIO(contents)
    bank_id = detect_bank(detection_buffer)

    if bank_id == "INVALID_PDF":
        return {"error": "Could not open PDF. The file may be corrupted or not a valid PDF."}

    if bank_id is None:
        return {"error": "Bank not recognised."}

    parsing_buffer = io.BytesIO(contents)
    parser = get_parser_by_id(bank_id, parsing_buffer)

    if parser is None:
        return {"error": f"Could not create parser for bank_id: {bank_id}"}

    try:
        account_info, df = parser.parse()
        unreadable_descriptions = parser.unreadable_descriptions
    except Exception as e:
        return {"error": f"Failed to parse statement: {e}"}

    if df.empty:
        return {"error": "No transactions detected."}

    corrected_df, result = verify_and_correct(
        df,
        filename=pdf_path.name,
        bank_name=account_info.bank,
        bank_id=bank_id,
        account_number=account_info.account_number,
        declared_totals=account_info.declared_totals,
        unreadable_descriptions=unreadable_descriptions,
    )
    return result.to_dict(), corrected_df


def main():
    if not BANK_STATEMENT_DIR.exists():
        print(f"Error: bank_statement directory not found at {BANK_STATEMENT_DIR}")
        sys.exit(1)

    pdf_files = sorted(BANK_STATEMENT_DIR.glob("*.pdf"))
    if not pdf_files:
        print("No PDF files found in bank_statement/")
        sys.exit(0)

    PARSED_DIR.mkdir(exist_ok=True)

    print(f"Found {len(pdf_files)} PDF file(s) in bank_statement/")
    print("=" * 80)

    for pdf_path in pdf_files:
        print(f"\nProcessing: {pdf_path.name}")
        result = process_file(pdf_path)

        if isinstance(result, dict) and "error" in result and "filename" not in result:
            print(f"  Error: {result['error']}")
            output_path = PARSED_DIR / f"{pdf_path.stem}.verification.json"
            output_path.write_text(json.dumps(result, indent=2, default=str))
            continue

        vr_dict, corrected_df = result
        output_path = PARSED_DIR / f"{pdf_path.stem}.verification.json"
        output_path.write_text(json.dumps(vr_dict, indent=2, default=str))

        csv_path = PARSED_DIR / f"{pdf_path.stem}.corrected.csv"
        corrected_df.to_csv(csv_path, index=False)

        acc = vr_dict.get("accuracy_percentage")
        acc_display = f"{acc}%" if acc is not None else "N/A"
        print(f"  Bank: {vr_dict.get('bank_name', 'N/A')} ({vr_dict.get('bank_id', 'N/A')})")
        if vr_dict.get("account_number"):
            print(f"  Account: {vr_dict['account_number']}")
        print(f"  Total transactions: {vr_dict.get('total_transactions', 0)}")
        print(f"  Verified: {vr_dict.get('verified_transactions', 0)}")
        print(f"  Passing: {vr_dict.get('passing_transactions', 0)}")
        print(f"  Failing: {vr_dict.get('failing_transactions', 0)}")
        print(f"  Total failures: {vr_dict.get('total_failures', 0)}")
        print(f"  Accuracy: {acc_display}")
        print(f"  Verification: {output_path}")
        print(f"  Corrected CSV: {csv_path}")

        failures = vr_dict.get("failures", [])
        if failures:
            print(f"\n  Failed transactions:")
            for f in failures[:20]:
                tf_tag = " [TOTAL FAILURE]" if f.get("total_failure") else ""
                print(f"    Row {f['row']}: {f['description']}{tf_tag} | "
                      f"corrected={f['corrected_balance']}, actual={f['actual_balance']}, "
                      f"diff={f['difference']}")
            if len(failures) > 20:
                print(f"    ... and {len(failures) - 20} more")

    print("\n" + "=" * 80)
    print("Verification complete.")


if __name__ == "__main__":
    main()