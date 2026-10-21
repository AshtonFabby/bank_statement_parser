"""Regression coverage for stamped GoTyme tables and personal Capitec summaries."""
import io

from reportlab.pdfgen import canvas

from parsers.tymebank import TymeBankParser
from parsers.capitec import CapitecParser
from services.verification import verify_and_correct


def test_gotyme_reads_printed_columns_under_stamp():
    stream = io.BytesIO()
    c = canvas.Canvas(stream, pagesize=(440, 630))
    c.setFont('Helvetica', 6)
    for y, text in [(610, 'www.gotyme.co.za'), (600, 'Current account'),
                    (590, 'Account Number: 53000000001'),
                    (580, 'Opening balance R3,091.95'), (570, 'Total Credit R1,000'),
                    (560, 'Total Debit R3,000'), (550, 'Total Fees* R0.50'),
                    (540, 'Closing balance R1,091.45')]:
        c.drawString(20, y, text)
    xs = [18, 70, 238, 280, 323, 365, 421]
    for x, text in zip(xs, ['Date', 'Details', 'Fees*(-)', 'Credits (+)', 'Debits (-)', 'Running Balance']):
        c.drawString(x + 2, 500, text)
    rows = [(['01 Apr 2026', 'Money added to EveryDay account', '-', '-', '3,000', '91.95'], 480, 470),
            (['02 Apr 2026', 'Payment received', '0.50', '1,000', '-', '1,091.45'], 450, 435)]
    for cells, y, bottom in rows:
        for x, text in zip(xs, cells):
            c.drawString(x + 2, y, text)
        for left, right in zip(xs, xs[1:]):
            c.line(left, bottom, right, bottom)
    c.drawString(72, 442, 'Wrapped reference 123456789')
    c.saveState(); c.translate(170, 470); c.rotate(15)
    c.drawString(0, 0, 'GoTyme Bank stamp 15 Oct 2026'); c.restoreState()
    c.save(); stream.seek(0)
    parser = TymeBankParser(stream)
    info, df = parser.parse()
    assert info.account_number == '53000000001'
    assert len(df) == 3
    assert df.iloc[1].Debit == 3000
    assert df.iloc[2].Credit == 1000
    assert df.iloc[2].Debit == 0.5
    assert df.iloc[2].Description == 'Payment received Wrapped reference 123456789'
    _, result = verify_and_correct(df, declared_totals=info.declared_totals)
    assert result.failing_transactions == 0
    assert result.unverified_transactions == 0
    assert result.declared_totals_match is True
    # A missing row must fail independent controls, even if balances look plausible.
    _, missing = verify_and_correct(df.drop(index=1), declared_totals=info.declared_totals)
    assert missing.declared_totals_match is False


def test_capitec_personal_account_and_independent_totals():
    parser = CapitecParser(io.BytesIO())
    parser._page_texts_cache = ['Main Account Statement\nAccount 1234567890 VAT Registration Number 4680173723\n'
        'From Date: 01/04/2026 Opening Balance: R620.25\n'
        'To Date: 15/10/2026 Closing Balance: R33.17\n'
        'Money In Summary R475 820.66 Money Out Summary -R476 407.74']
    info = parser.extract_account_info()
    assert info.account_number == '1234567890'
    assert info.declared_totals.credit_total == 475820.66
    assert info.declared_totals.debit_total == 476407.74
    assert info.declared_totals.opening_balance == 620.25
    assert info.declared_totals.closing_balance == 33.17
