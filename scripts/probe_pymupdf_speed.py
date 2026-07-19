import fitz
import time

def probe_speed(file_path):
    print(f"Testing {file_path} with PyMuPDF")
    t0 = time.time()
    
    with fitz.open(file_path) as doc:
        full_text = ""
        for page in doc:
            full_text += page.get_text()
            
    t1 = time.time()
    print(f"Extract text: {t1-t0:.3f}s")
    
    t0 = time.time()
    with fitz.open(file_path) as doc:
        for page in doc:
            words = page.get_text("words")
    t1 = time.time()
    print(f"Extract words: {t1-t0:.3f}s")
    
    t0 = time.time()
    with fitz.open(file_path) as doc:
        for page in doc:
            tabs = page.find_tables()
            for tab in tabs.tables:
                tab.extract()
    t1 = time.time()
    print(f"Extract tables: {t1-t0:.3f}s")

if __name__ == "__main__":
    probe_speed(r"bank_statements\1._Jan.pdf")
