"""Summary calculation service."""

from dataclasses import dataclass

import pandas as pd


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
