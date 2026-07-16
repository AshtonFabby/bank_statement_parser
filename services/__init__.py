"""Services package for bank statement processing."""

from .summary import (
    calculate_summary,
    Summary,
    calculate_coverage,
    CoverageMetrics,
    calculate_activity_volume,
    ActivityVolume,
    calculate_revenue,
    RevenueMetrics,
)
from .verification import verify_transactions, verify_and_correct, VerificationResult

# The PDF generators pull in reportlab (~20-35MB RSS), which most requests
# never need. PEP 562 lazy loading defers the import to the first report.
_LAZY_IMPORTS = {
    "generate_summary_pdf": "pdf_generator",
    "generate_prevet_pdf": "prevet_pdf_generator",
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        import importlib

        module = importlib.import_module(f".{_LAZY_IMPORTS[name]}", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "calculate_summary",
    "Summary",
    "calculate_coverage",
    "CoverageMetrics",
    "calculate_activity_volume",
    "ActivityVolume",
    "calculate_revenue",
    "RevenueMetrics",
    "generate_summary_pdf",
    "generate_prevet_pdf",
    "verify_transactions",
    "verify_and_correct",
    "VerificationResult",
]
