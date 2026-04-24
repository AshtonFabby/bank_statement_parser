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
from .pdf_generator import generate_summary_pdf
from .prevet_pdf_generator import generate_prevet_pdf
from .verification import verify_transactions, verify_and_correct, VerificationResult

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
