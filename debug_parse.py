#!/usr/bin/env python3
"""Debug script to test parsing of bank statements."""

import contextlib
import io
import sys
from pathlib import Path

# Add the parent directory to path to import parsers
sys.path.insert(0, str(Path(__file__).parent))

# This script prints ✓/❌ status markers, which a Windows console's default
# cp1252 encoding cannot represent — without this every run dies on the first
# result line with UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from parsers import get_parser, get_parser_by_id, SUPPORTED_BANKS


def test_parse_file(file_path: str):
    """Test parsing a single PDF file."""
    print(f"\n{'='*60}")
    print(f"Testing: {file_path}")
    print(f"{'='*60}")
    
    # Read the file
    with open(file_path, 'rb') as f:
        contents = f.read()
    
    print(f"File size: {len(contents)} bytes")
    
    # Test detection
    detection_buffer = io.BytesIO(contents)
    parser = get_parser(detection_buffer)
    
    if not parser:
        print(f"❌ Failed: Could not detect bank type")
        print(f"   Supported banks: {', '.join(SUPPORTED_BANKS)}")
        return False
    
    print(f"✓ Detected bank: {parser.BANK_NAME} (ID: {parser.BANK_ID})")
    
    # Create fresh buffer for parsing
    parsing_buffer = io.BytesIO(contents)
    parser = get_parser_by_id(parser.BANK_ID, parsing_buffer)
    
    try:
        account_info, df = parser.parse()
        
        print(f"✓ Parsed successfully")
        print(f"  - Account info: {account_info}")
        print(f"  - Transactions: {len(df)} rows")
        
        if df.empty:
            print(f"❌ Warning: No transactions found!")
            return False
        
        print(f"\nFirst 5 transactions:")
        print(df.head().to_string())
        return True
        
    except Exception as e:
        print(f"❌ Failed to parse: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_multiple_files(file_paths: list):
    """Test parsing multiple files in sequence."""
    print(f"\n{'#'*60}")
    print(f"Testing {len(file_paths)} files")
    print(f"{'#'*60}")
    
    results = []
    for file_path in file_paths:
        success = test_parse_file(file_path)
        results.append((file_path, success))
    
    print(f"\n{'#'*60}")
    print(f"Summary:")
    print(f"{'#'*60}")
    for file_path, success in results:
        status = "✓ PASS" if success else "❌ FAIL"
        print(f"{status}: {file_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_parse.py <pdf_file1> [pdf_file2] ...")
        print("\nExample:")
        print("  python debug_parse.py '1. Jan.pdf'")
        print("  python debug_parse.py '1. Jan.pdf' '2. Feb.pdf' '3. Mar.pdf'")
        sys.exit(1)
    
    file_paths = sys.argv[1:]
    
    # Check files exist
    for path in file_paths:
        if not Path(path).exists():
            print(f"Error: File not found: {path}")
            sys.exit(1)
    
    if len(file_paths) == 1:
        test_parse_file(file_paths[0])
    else:
        test_multiple_files(file_paths)
