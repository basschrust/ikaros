import logging

import pandas as pd
from openbb import obb
from database.duckdb_storage import Repository
from config.config import load_config

logger = logging.getLogger(__name__)

START_DATE = "2025-01-01"


def fetch_fx_history(
    symbol: str,
    start_date: str = START_DATE,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Fetch daily FX OHLC data from Yahoo Finance via OpenBB."""

    result = obb.currency.price.historical(
        # result = obb.yfinance.currency.price(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        interval="1d",
        provider="yfinance",
    )

    df = result.to_dataframe().reset_index()
    df.columns = [str(col).lower() for col in df.columns]

    if "date" not in df.columns:
        df = df.rename(columns={"index": "date"})

    required = ["date", "open", "high", "low", "close"]
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f"{symbol}: missing columns {sorted(missing)}")

    if "volume" not in df.columns:
        df["volume"] = pd.NA

    df["date"] = pd.to_datetime(df["date"]).dt.date

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["symbol"] = symbol

    return (
        df[["symbol", "date", "open", "high", "low", "close", "volume"]]
        .sort_values("date")
        .reset_index(drop=True)
    )


def fetch_all_fx_history(
    ccy_pairs: pd.DataFrame,
    start_date: str = START_DATE,
    end_date: str | None = None,
) -> pd.DataFrame:
    frames = []

    for pair in ccy_pairs.itertuples(index=False):
        try:
            df = fetch_fx_history(symbol=pair.symbol, start_date=start_date, end_date=end_date)
            df["currency_pair_id"] = pair.currency_pair_id
            frames.append(df)
            logger.info("Fetched %s: %d rows", pair.symbol, len(df))

        except Exception:
            logger.exception("Failed to fetch %s", pair.symbol)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    repository = Repository()

    config = load_config("currencies.yaml")
    start_date = config.get("history", {}).get("start_date")
    end_date = config.get("history", {}).get("end_date", None)

    if not start_date:
        raise ValueError("No start date provided in config file currencies.yaml")

    ccy_pairs_df = repository.get_currency_pairs()

    fx_df = fetch_all_fx_history(
        ccy_pairs=ccy_pairs_df,
        start_date=start_date,
        end_date=end_date,
    )

    print(fx_df.head())
    print(fx_df.tail())
    print(fx_df.isna().sum())
    print("Saving to DB...")

    repository.save_fx_rates(fx_rates_df=fx_df)

    print("Done!")
