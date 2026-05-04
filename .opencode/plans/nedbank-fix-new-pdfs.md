# Fix Nedbank Parser - New Bank Statements

## Summary of Issues Found

The 8 new Nedbank bank statements use a **different format** (business format with fees column) compared to the original 4 (enquiry format). The parser needs several fixes:

---

### Issue 1: Format Detection - "Tranlistno" not matching (Critical)

**Files affected**: 1-7 (business format)
**Problem**: `_detect_format` checks for `"Tran list no"` (with spaces) but the PDF text extracts as `"Tranlistno"` (no spaces).
**Fix**: Add `"Tranlistno"` and `"Tran list no"` to detection check.

---

### Issue 2: Business format amount parsing (Critical)

**Files affected**: 1-7 (business format)
**Problem**: These statements have `Fees(R) Debits(R) Credits(R) Balance(R)` columns. Lines have different amount patterns:
- Fees-only: `NEDLNK DP 00142270 1867 35,312.31 -2,384,641.55` → fees=0, debit=35,312.31, balance
- With fees: `NEDLNK DP 00142270 1865 97,233.66 -2,263,770.22` → 1865 is description ref, fees=0(?), debit=97,233.66, balance
- Credit only: `Confuel 550,000.00 -1,585,236.20` → credit=550,000.00, balance
- Fee+debit: `NetBank Business subscription 256.91* -2,573,401.29` → fees=256.91, balance
- Fee+credit: (some lines have both fees and a credit amount)
- 3 amounts with fee: `SERVICE FEE 28/07 - 26/08 1,203.80* -1,521,145.75` → fees, balance
- 3 amounts debit: `TRIRIDGEFREIGHTANDLO 21.50 921.12 -3,626,850.86` → fees=21.50, debit=921.12, balance

The current parser treats all these as "standard" format and misclassifies fees as debits/credits.

**Fix**: When format is "business", properly handle varying numbers of amounts:
- 2 amounts with positive balance change → credit + balance
- 2 amounts with negative balance change → debit + balance  
- 2 amounts where amount is small and balance change doesn't match → fees + balance (fee, no debit/credit)
- 3 amounts → fees + debit + balance (if all positive except balance) OR fees + credit + balance

---

### Issue 3: "Balancebroughtforward" / "Balancecarriedforward" not filtered (Critical)

**Files affected**: 1-7 (business format)
**Problem**: PDF text extracts these as `Balancebroughtforward` and `Balancecarriedforward` (no spaces). The current skip checks look for `"BROUGHT FORWARD"` and `"CARRIED FORWARD"` (with spaces), so these don't match.
**Fix**: Add collapsed variants to skip checks in both `extract_transactions` and `_is_description_fragment`.

---

### Issue 4: "BR CASH R" not handled (Moderate)

**Files affected**: File 7
**Problem**: Line `27/02/2026 BR CASH R260,610.00 FEE 6,569.64* -6,409,007.37` has the same structure as `ATM CASH R...FEE...` but uses "BR" prefix instead of "ATM". The ATM CASH handler only checks for `"ATM CASH R"`.
**Fix**: Extend the handler to also match `"BR CASH R"` and `"CASH *"` patterns. Also handle `CASH *` lines like `27/02/2026 CASH * 260,610.00 -6,402,437.73` which are the actual withdrawal transactions.

---

### Issue 5: Space-separated amounts (Critical)

**Files affected**: File 8 (TH.pdf - enquiry format)
**Problem**: Amounts use spaces as thousands separators: `400 000.00`, `-8 942 165.72`, `107 594.21`. The `AMOUNT_PATTERN` regex only matches comma-separated amounts.
**Fix**: Add a second amount pattern for space-separated amounts and use it when the enquiry format is detected (or auto-detect). Pattern: `-?\d{1,3}(?: \d{3})+\.\d{2}` for space-separated amounts.

Need to handle the BROUGHT FORWARD/CARRIED FORWARD with space-separated amounts: `BROUGHT FORWARD 0.00 0.00 -8 282 161.74`

---

### Issue 6: "Opening balance" format differences (Minor)

**Files affected**: 1-7
**Problem**: Opening balance appears as `26/08/2025 Openingbalance -2,361,003.88` (collapsed text) and as `26/08/2025 Opening balance -2,361,003.88` (with space). Both need to be handled.
**Fix**: The current `"Opening balance" in line.lower()` check should also match `"Openingbalance"`.

---

## Implementation Order

1. Fix space-separated amount support (Issue 5) - critical for TH.pdf
2. Fix format detection for "Tranlistno" (Issue 1) 
3. Fix "Balancebroughtforward" / "Balancecarriedforward" skipping (Issue 3)
4. Fix business format amount parsing (Issue 2)
5. Add "BR CASH R" / "CASH *" handler (Issue 4)
6. Fix "Openingbalance" matching (Issue 6)