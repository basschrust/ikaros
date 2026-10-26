from datetime import date

import pandas as pd


class BiznesRadarProvider:
    def get_contracts(self) -> pd.DataFrame:
        raise NotImplementedError

    def get_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        raise NotImplementedError
