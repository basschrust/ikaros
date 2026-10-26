from openbb import obb
from openbb_core.provider.utils.errors import EmptyDataError

from datetime import date

import pandas as pd

from providers.interface import DerivativesProvider


def fetch_futures_history(
    provider: DerivativesProvider,
    symbol: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    return provider.get_history(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    )


def fetch_eod_futures_prices(
    *,
    symbol: str,
    start_date: str,
    provider: str = "yfinance",
    end_date: str | None = None,
) -> pd.DataFrame:
    try:
        result = obb.yfinance.derivatives.futures.historical(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            interval="1d",
        )

        df = result.to_dataframe().reset_index()
        df.columns = [column.lower() for column in df.columns]

        # Standardize the date column
        if "date" not in df.columns:
            df = df.rename(columns={"index": "date"})

        df["symbol"] = symbol

        # Keep a stable schema; provider may omit volume for some instruments
        # TODO: dividend, split_ratio as additional cols
        for column in ["open", "high", "low", "close", "volume", "dividend", "split_ratio"]:
            if column not in df.columns:
                df[column] = pd.NA

        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        return (
            df[
                [
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "dividend",
                    "split_ratio",
                ]
            ]
            .sort_values("date")
            .reset_index(drop=True)
        )

    except EmptyDataError:
        print(f"No data for this symbol: {symbol}")
        return pd.DataFrame()
