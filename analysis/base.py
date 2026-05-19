"""Abstract base class for all analysis modules."""

from abc import ABC, abstractmethod
from typing import Literal

import pandas as pd

Status = Literal["bullish", "bearish", "neutral"]
Strength = Literal["Strong", "Medium", "Weak"]

STRENGTH_COLORS: dict[str, str] = {
    "Strong": "#155724",
    "Medium": "#856404",
    "Weak": "#721c24",
}

STRENGTH_BG: dict[str, str] = {
    "Strong": "#d4edda",
    "Medium": "#fff3cd",
    "Weak": "#f8d7da",
}


class BaseAnalysis(ABC):
    """Contract that every analysis module must satisfy."""

    @abstractmethod
    def analyse(self, symbol: str, data: pd.DataFrame) -> dict:
        """Run the analysis and return a results dictionary.

        Args:
            symbol: The stock ticker being analysed.
            data: OHLCV DataFrame with columns Open, High, Low, Close, Volume.

        Returns:
            Dictionary of analysis results specific to the subclass.
            Must include 'strength' key with a Strength value.
        """

    @abstractmethod
    def get_status(self) -> Status:
        """Return the overall market bias after running analyse().

        Returns:
            "bullish", "bearish", or "neutral".
        """

    @abstractmethod
    def get_strength(self) -> Strength:
        """Return the signal confidence rating after running analyse().

        Returns:
            "Strong", "Medium", or "Weak".
        """

    @abstractmethod
    def get_summary(self) -> str:
        """Return a one-line human-readable summary of the analysis."""
