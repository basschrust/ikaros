from datetime import date
from typing import Protocol

import pandas as pd


class DerivativesProvider(Protocol):
    def get_contracts(self) -> pd.DataFrame:
        """Return available derivative contracts."""
        ...

    def get_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Return historical observations for a contract."""
        ...
