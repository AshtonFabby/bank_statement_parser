import fitz
import pdfplumber

file_path = r"bank_statements\1._Jan.pdf"

print("--- PyMuPDF ---")
with fitz.open(file_path) as doc:
    text = doc[0].get_text()
    for line in text.split('\n')[:15]:
        print(repr(line))

print("\n--- pdfplumber ---")
with pdfplumber.open(file_path) as doc:
    text = doc.pages[0].extract_text()
    for line in text.split('\n')[:15]:
        print(repr(line))
