"""Abstract base class for all analysis modules."""

from abc import ABC, abstractmethod
from typing import Literal

import pandas as pd

Status = Literal["bullish", "bearish", "neutral"]


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
        """

    @abstractmethod
    def get_status(self) -> Status:
        """Return the overall market bias after running analyse().

        Returns:
            "bullish", "bearish", or "neutral".
        """

    @abstractmethod
    def get_summary(self) -> str:
        """Return a one-line human-readable summary of the analysis."""
