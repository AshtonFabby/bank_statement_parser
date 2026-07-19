import pdfplumber
import time

def probe_speed(file_path):
    print(f"Testing {file_path} with pdfplumber")
    t0 = time.time()
    
    with pdfplumber.open(file_path) as doc:
        full_text = ""
        for page in doc.pages:
            full_text += page.extract_text()
            page.flush_cache()
            
    t1 = time.time()
    print(f"Extract text: {t1-t0:.3f}s")
    
    t0 = time.time()
    with pdfplumber.open(file_path) as doc:
        for page in doc.pages:
            words = page.extract_words()
            page.flush_cache()
    t1 = time.time()
    print(f"Extract words: {t1-t0:.3f}s")
    
    t0 = time.time()
    with pdfplumber.open(file_path) as doc:
        for page in doc.pages:
            tabs = page.extract_tables()
            page.flush_cache()
    t1 = time.time()
    print(f"Extract tables: {t1-t0:.3f}s")

if __name__ == "__main__":
    probe_speed(r"bank_statements\1._Jan.pdf")
