import pandas as pd
from openbb import obb
from openbb_core.provider.utils.errors import EmptyDataError


def fetch_eod_prices(
    *,
    symbol: str,
    start_date: str,
    provider: str = "yfinance",
    end_date: str | None = None,
) -> pd.DataFrame:
    try:
        result = obb.equity.price.historical(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            interval="1d",
            provider=provider,
        )
        df = result.to_dataframe().reset_index()
        df.columns = [str(column).lower() for column in df.columns]
        if "date" not in df.columns:
            df = df.rename(columns={"index": "date"})
        df["symbol"] = symbol
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
