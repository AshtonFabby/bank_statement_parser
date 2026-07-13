import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from debug_parse import test_parse_file

def run_benchmark():
    files = list(Path("bank_statements").glob("*.pdf"))
    if not files:
        print("No files found.")
        return
        
    print(f"Benchmarking {len(files)} files...")
    t0 = time.time()
    
    for f in files:
        # Temporarily suppress stdout to avoid massive logs
        import sys, os
        old_stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
        try:
            test_parse_file(str(f))
        finally:
            sys.stdout.close()
            sys.stdout = old_stdout
            
    t1 = time.time()
    print(f"Total time for {len(files)} files: {t1-t0:.2f} seconds")
    print(f"Average time per file: {(t1-t0)/len(files):.2f} seconds")

if __name__ == "__main__":
    run_benchmark()
