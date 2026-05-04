# Fix Nedbank Parser Accuracy

## Problem
The Nedbank parser has very low accuracy across all 4 bank statement PDFs:
- File 1: 3.99% (27/677 passing)
- File 2: 5.21% (24/461 passing)
- File 3: 0.57% (2/350 passing)
- File 4: 36.96% (17/46 passing)

Three bugs identified, in order of impact:

---

## Fix 1: ATM CASH FEE companion lines (Critical - ~95% of failures)

**File**: `parsers/nedbank.py`  
**Lines**: 142-172 (the ATM CASH companion handler)

**Root cause**: The regex `FEE\s+(\d{1,3}(?:,\d{3})*\.\d{2})\*` requires:
1. A literal `*` suffix which doesn't exist (PDFs use `#` for VAT indicator)
2. Doesn't handle negative amounts (`-148.92`)
3. When the regex fails, the `continue` statement skips the entire line, losing the fee

**Each missed fee causes a balance offset that cascades to ALL subsequent transactions.** 81 total fee transactions are lost across all files.

**Fix**: Replace the ATM CASH companion handler block with:
```python
# Handle ATM CASH companion lines
# Format: "{enc} {date} ATM CASH R{withdrawal} FEE {fee} {balance} [#]"
# e.g. "690 03/04/2025 ATM CASH R14,510.00 FEE -148.92 -938,248.20 #"
# The R{withdrawal} is descriptive (not a transaction amount).
# The fee is the actual transaction amount (always a debit).
if "ATM CASH R" in line and "FEE" in line and "TRANSACTION FEE" not in line:
    fee_match = re.search(r'FEE\s+(-?\d{1,3}(?:,\d{3})*\.\d{2})', line)
    if fee_match:
        fee_val = self._clean_amount(fee_match.group(1))
        fee_debit = abs(fee_val)
        amounts = self.AMOUNT_PATTERN.findall(line)
        if amounts:
            balance = self._clean_amount(amounts[-1])
            txn_date_match = re.search(r"^\d{3,6}\s+(\d{2}/\d{2}/\d{4})", line)
            if txn_date_match:
                date_str = txn_date_match.group(1)
            else:
                date_match = self.DATE_PATTERN.search(line)
                date_str = date_match.group(1) if date_match else None
            if date_str:
                rows.append(create_transaction_row(
                    date_str, "ATM CASH FEE", fee_debit, 0.0, balance
                ))
    continue
```

Key changes:
- Removed `\*` requirement from regex
- Added `-?` to match negative fee amounts
- Use `abs(fee_val)` since fees are always debits
- Always skip the companion line with `continue` (regardless of fee extraction success)

---

## Fix 2: PROVISIONAL STATEMENT filter (Moderate - fixes 2-3 total failures)

**File**: `parsers/nedbank.py`  
**Lines**: In the skip conditions block (~line 122-141)

**Root cause**: Lines like `771 13/04/2026 PROVISIONAL STATEMENT 0.00 0.00` are parsed as transactions with Debit=0, Credit=0, Balance=0, causing massive balance jumps.

**Fix**: Add a skip condition after the existing skip checks (~line 141):
```python
if "PROVISIONAL STATEMENT" in line.upper():
    continue
```

---

## Fix 3: Multi-line descriptions (Quality - fixes empty descriptions)

**File**: `parsers/nedbank.py`  
**Method**: `extract_transactions`

**Root cause**: PDF text extraction splits long descriptions across lines. Lines without dates or amounts (description fragments) are skipped entirely, resulting in ~7-8 empty descriptions per file.

Patterns observed:
- **Prefix**: `CASHFOCUS SAVE CASH AND` appears before `689 01/04/2025 3,585.18 -279,156.36`
- **Suffix**: `CARRY` appears after the transaction line
- Combined: Full description should be "CASHFOCUS SAVE CASH AND CARRY"

**Fix**: Restructure `extract_transactions` to:

1. Collect all lines from all pages into a flat list
2. Pre-process: merge description fragments with adjacent transaction lines using a new `_merge_description_fragments` method
3. Parse the merged lines using the existing logic

Add a helper method `_is_description_fragment`:
```python
def _is_description_fragment(self, line: str) -> bool:
    """Check if a line is a description-only fragment (no date, no amounts, not special)."""
    if not line:
        return False
    if self.DATE_PATTERN.search(line):
        return False
    if self.AMOUNT_PATTERN.search(line):
        return False
    # Skip known special lines
    skip_keywords = [
        "Tran list no", "Narrative Description", "Date", "Debits", "Credits",
        "Opening balance", "Balance carried forward", "BROUGHT FORWARD",
        "CARRIED FORWARD", "Statement", "Account", "Profile", "User",
        "Notice", "VAT #", "ENC *", "PROVISIONAL STATEMENT",
    ]
    line_upper = line.upper()
    for kw in skip_keywords:
        if kw.upper() in line_upper:
            return False
    # Skip ENC number prefix lines (e.g., "689" standalone)
    if re.match(r'^\d{3,6}$', line.strip()):
        return False
    return True
```

Add `_merge_description_fragments` method:
```python
def _merge_description_fragments(self, lines: list) -> list:
    """Merge description-only lines with adjacent transaction lines."""
    result = []
    fragment_buffer = ""
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        if self._is_description_fragment(stripped):
            fragment_buffer = (fragment_buffer + " " + stripped).strip() if fragment_buffer else stripped
            continue
        
        if self.DATE_PATTERN.search(stripped):
            # Transaction line - check if it needs a prefix
            if fragment_buffer:
                # Insert fragment buffer after the date/ENC prefix
                date_match = self.DATE_PATTERN.search(stripped)
                enc_match = re.match(r'^\d{3,6}\s+', stripped)
                if enc_match and date_match:
                    insert_pos = date_match.end()
                elif date_match:
                    insert_pos = date_match.end()
                else:
                    insert_pos = 0
                stripped = stripped[:insert_pos] + " " + fragment_buffer + stripped[insert_pos:]
                fragment_buffer = ""
            result.append(stripped)
        else:
            # Non-transaction, non-fragment line (e.g., BROUGHT FORWARD)
            if fragment_buffer:
                # Append buffered fragment to the last transaction line
                if result:
                    result[-1] = result[-1] + " " + fragment_buffer
                fragment_buffer = ""
            result.append(stripped)
    
    # Handle remaining fragment (suffix of last transaction)
    if fragment_buffer and result:
        result[-1] = result[-1] + " " + fragment_buffer
    
    return result
```

Then modify `extract_transactions` to:
1. Collect all lines from all pages
2. Call `_merge_description_fragments` on the collected lines
3. Iterate over the merged lines instead of `page_text.split("\n")`

Change the loop from:
```python
for page_text in self._iterate_pages():
    for line in page_text.split("\n"):
```

To:
```python
all_lines = []
for page_text in self._iterate_pages():
    all_lines.extend(page_text.split("\n"))

all_lines = self._merge_description_fragments(all_lines)

for line in all_lines:
```

---

## Expected Outcome

After all fixes:
- ATM CASH FEEs will be correctly extracted, resolving ~95% of balance errors
- PROVISIONAL STATEMENT rows will be filtered out
- Multi-line descriptions will be preserved
- Expected accuracy: **95%+** across all files

## Testing

Run `python verify.py` to validate all 4 PDFs and check accuracy percentages.