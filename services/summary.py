"""Summary calculation service."""

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class CoverageMetrics:
    """Coverage and data integrity metrics."""
    start_date: Optional[str]
    end_date: Optional[str]
    days_covered: int
    distinct_months: int
    transaction_count: int
    accounts_detected: int
    missing_date_gaps: int

    def to_dict(self) -> dict:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "days_covered": self.days_covered,
            "distinct_months": self.distinct_months,
            "transaction_count": self.transaction_count,
            "accounts_detected": self.accounts_detected,
            "missing_date_gaps": self.missing_date_gaps,
        }


@dataclass
class ActivityVolume:
    """Activity volume metrics."""
    credit_transaction_count: int
    debit_transaction_count: int
    avg_credits_per_month: float
    avg_debits_per_month: float

    def to_dict(self) -> dict:
        return {
            "credit_transaction_count": self.credit_transaction_count,
            "debit_transaction_count": self.debit_transaction_count,
            "avg_credits_per_month": round(self.avg_credits_per_month, 2),
            "avg_debits_per_month": round(self.avg_debits_per_month, 2),
        }


@dataclass
class RevenueMetrics:
    """Revenue / turnover (credits) metrics."""
    total_credits: float
    avg_monthly_credits: float
    lowest_month_credits: float
    highest_month_credits: float
    revenue_volatility_pct: Optional[float]
    top_5_credit_concentration_pct: Optional[float]
    largest_single_credit: float

    def to_dict(self) -> dict:
        return {
            "total_credits": round(self.total_credits, 2),
            "avg_monthly_credits": round(self.avg_monthly_credits, 2),
            "lowest_month_credits": round(self.lowest_month_credits, 2),
            "highest_month_credits": round(self.highest_month_credits, 2),
            "revenue_volatility_pct": round(self.revenue_volatility_pct, 2) if self.revenue_volatility_pct is not None else None,
            "top_5_credit_concentration_pct": round(self.top_5_credit_concentration_pct, 2) if self.top_5_credit_concentration_pct is not None else None,
            "largest_single_credit": round(self.largest_single_credit, 2),
        }


@dataclass
class Summary:
    """Financial summary data class."""
    total_debits: float
    total_credits: float
    net_movement: float
    ending_balance: float
    transaction_count: int

    def to_dict(self) -> dict:
        return {
            "total_debits": self.total_debits,
            "total_credits": self.total_credits,
            "net_movement": self.net_movement,
            "ending_balance": self.ending_balance,
            "transaction_count": self.transaction_count,
        }


def calculate_summary(df: pd.DataFrame) -> Summary:
    """Calculate financial summary from transaction DataFrame.

    Args:
        df: DataFrame with Debit, Credit, Balance columns

    Returns:
        Summary object with calculated totals
    """
    if df.empty:
        return Summary(
            total_debits=0.0,
            total_credits=0.0,
            net_movement=0.0,
            ending_balance=0.0,
            transaction_count=0,
        )

    total_debits = df["Debit"].sum()
    total_credits = df["Credit"].sum()
    net_movement = total_credits - total_debits
    ending_balance = df["Balance"].iloc[-1]

    return Summary(
        total_debits=total_debits,
        total_credits=total_credits,
        net_movement=net_movement,
        ending_balance=ending_balance,
        transaction_count=len(df),
    )


def calculate_coverage(df: pd.DataFrame) -> CoverageMetrics:
    """Calculate coverage and data integrity metrics from transaction DataFrame.

    Args:
        df: DataFrame with Date column (and optionally Source for account detection)

    Returns:
        CoverageMetrics object with coverage statistics
    """
    if df.empty:
        return CoverageMetrics(
            start_date=None,
            end_date=None,
            days_covered=0,
            distinct_months=0,
            transaction_count=0,
            accounts_detected=0,
            missing_date_gaps=0,
        )

    dates = pd.to_datetime(df["Date"], dayfirst=True)
    start_date = dates.min()
    end_date = dates.max()

    days_covered = (end_date - start_date).days + 1

    months = dates.dt.to_period("M")
    distinct_months = months.nunique()

    transaction_count = len(df)

    if "Source" in df.columns:
        accounts_detected = df["Source"].nunique()
    else:
        accounts_detected = 1

    unique_dates = dates.dt.date.nunique()
    missing_date_gaps = days_covered - unique_dates

    return CoverageMetrics(
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        days_covered=days_covered,
        distinct_months=distinct_months,
        transaction_count=transaction_count,
        accounts_detected=accounts_detected,
        missing_date_gaps=missing_date_gaps,
    )


def calculate_activity_volume(df: pd.DataFrame) -> ActivityVolume:
    """Calculate activity volume metrics from transaction DataFrame.

    Args:
        df: DataFrame with Credit, Debit, Date columns

    Returns:
        ActivityVolume object with transaction counts and averages
    """
    if df.empty:
        return ActivityVolume(
            credit_transaction_count=0,
            debit_transaction_count=0,
            avg_credits_per_month=0.0,
            avg_debits_per_month=0.0,
        )

    credit_count = (df["Credit"] > 0).sum()
    debit_count = (df["Debit"] > 0).sum()

    dates = pd.to_datetime(df["Date"], dayfirst=True)
    distinct_months = dates.dt.to_period("M").nunique()

    if distinct_months > 0:
        avg_credits_per_month = credit_count / distinct_months
        avg_debits_per_month = debit_count / distinct_months
    else:
        avg_credits_per_month = 0.0
        avg_debits_per_month = 0.0

    return ActivityVolume(
        credit_transaction_count=int(credit_count),
        debit_transaction_count=int(debit_count),
        avg_credits_per_month=avg_credits_per_month,
        avg_debits_per_month=avg_debits_per_month,
    )


def calculate_revenue(df: pd.DataFrame) -> RevenueMetrics:
    """Calculate revenue/turnover (credits) metrics from transaction DataFrame.

    Args:
        df: DataFrame with Credit, Date columns

    Returns:
        RevenueMetrics object with credit statistics
    """
    if df.empty:
        return RevenueMetrics(
            total_credits=0.0,
            avg_monthly_credits=0.0,
            lowest_month_credits=0.0,
            highest_month_credits=0.0,
            revenue_volatility_pct=None,
            top_5_credit_concentration_pct=None,
            largest_single_credit=0.0,
        )

    total_credits = df["Credit"].sum()
    largest_single_credit = df["Credit"].max()

    # Monthly aggregation for min/max/volatility
    dates = pd.to_datetime(df["Date"], dayfirst=True)
    df_with_month = df.copy()
    df_with_month["Month"] = dates.dt.to_period("M")
    monthly_credits = df_with_month.groupby("Month")["Credit"].sum()

    distinct_months = len(monthly_credits)

    if distinct_months > 0:
        avg_monthly_credits = total_credits / distinct_months
        lowest_month_credits = monthly_credits.min()
        highest_month_credits = monthly_credits.max()

        # Revenue volatility: (max - min) / avg
        if avg_monthly_credits > 0:
            revenue_volatility_pct = ((highest_month_credits - lowest_month_credits) / avg_monthly_credits) * 100
        else:
            revenue_volatility_pct = None
    else:
        avg_monthly_credits = 0.0
        lowest_month_credits = 0.0
        highest_month_credits = 0.0
        revenue_volatility_pct = None

    # Top 5 credit concentration
    if total_credits > 0:
        top_5_credits = df["Credit"].nlargest(5).sum()
        top_5_credit_concentration_pct = (top_5_credits / total_credits) * 100
    else:
        top_5_credit_concentration_pct = None

    return RevenueMetrics(
        total_credits=total_credits,
        avg_monthly_credits=avg_monthly_credits,
        lowest_month_credits=lowest_month_credits,
        highest_month_credits=highest_month_credits,
        revenue_volatility_pct=revenue_volatility_pct,
        top_5_credit_concentration_pct=top_5_credit_concentration_pct,
        largest_single_credit=largest_single_credit,
    )
